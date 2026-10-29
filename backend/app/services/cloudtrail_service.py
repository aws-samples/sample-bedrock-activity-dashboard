"""Service for retrieving Bedrock invocation events from CloudWatch Logs and CloudTrail."""

import gzip
import hashlib
import json
import logging
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from app.config import settings
from app.services.cache import TTLCache

logger = logging.getLogger(__name__)


def parse_regions(raw: str) -> list[str]:
    """Parse a comma-separated region list, trimming whitespace and blanks.

    Same convention as ``AgentCoreDiscoveryService.regions()`` — kept as a
    free function here (rather than importing that module) to avoid a
    cross-service dependency for a one-line helper.
    """
    return [r.strip() for r in raw.split(",") if r.strip()]


# ---------------------------------------------------------------------------
# Raw-ingestion cache (thread-safe, user-agnostic)
# ---------------------------------------------------------------------------
#
# get_activity_records performs the expensive CloudWatch + S3 (+ CloudTrail)
# ingestion. That work is identical for every caller of a given time window, so
# we cache it process-wide behind a short TTL.
#
# SECURITY: this cache stores ONLY the raw, unfiltered AWS records and is keyed
# SOLELY by a coarse (start, end) hour bucket — never by user identity, role, or
# any request filter. A single cache entry therefore safely serves all callers.
# The per-request pipeline (DEMO_MODE synthetic merge, cost application,
# FilterService.apply_filters, and the auth `_scope_to_user` identity override)
# always runs AFTER this cache returns, so each caller still only ever sees
# their own correctly-scoped and filtered subset. Nothing scoped or filtered is
# ever stored here.
_ACTIVITY_CACHE_TTL_SECONDS = 60
_activity_cache: TTLCache = TTLCache(_ACTIVITY_CACHE_TTL_SECONDS)


def clear_activity_cache() -> None:
    """Clear the raw-ingestion cache (used to isolate tests)."""
    _activity_cache.clear()


def _activity_cache_key(
    start_date: datetime, end_date: datetime, regions: tuple[str, ...]
) -> tuple[datetime, datetime, tuple[str, str, str]]:
    """Compute the coarse hour-bucket fetch window and cache key.

    Rounds ``start_date`` DOWN to the hour and ``end_date`` UP to the hour so
    that near-identical requests collapse onto the same cache entry. Returns the
    (rounded_start, rounded_end, key) triple. The key is derived from the
    rounded time window PLUS the sorted region set — it is intentionally
    user-agnostic (no identity/role/filter ever contributes to it), but MUST
    include regions: a request for just ``["us-west-2"]`` and one for
    ``["us-west-2", "us-east-1"]`` in the same hour bucket are genuinely
    different fetches and must not collide on the same cache entry.
    """
    start_floor = start_date.replace(minute=0, second=0, microsecond=0)
    end_ceil = end_date.replace(minute=0, second=0, microsecond=0)
    if end_date > end_ceil:
        end_ceil = end_ceil + timedelta(hours=1)
    region_key = ",".join(sorted(regions))
    key = (start_floor.isoformat(), end_ceil.isoformat(), region_key)
    return start_floor, end_ceil, key


# Source application patterns matched against the userAgent field
_SOURCE_APP_PATTERNS = [
    # IDE / developer tools
    ("kiro", "Kiro"),
    ("q-developer", "Q Developer"),
    ("vscode", "VS Code"),
    ("jetbrains", "JetBrains IDE"),
    ("cursor", "Cursor"),
    # AWS Console (browser)
    ("mozilla/", "AWS Console"),
    ("chrome/", "AWS Console"),
    ("safari/", "AWS Console"),
    # AWS SDKs
    ("boto3/", "Python SDK (Boto3)"),
    ("aws-sdk-java", "Java SDK"),
    ("aws-sdk-js", "JavaScript SDK"),
    ("aws-sdk-go", "Go SDK"),
    ("aws-sdk-dotnet", "DotNet SDK"),
    ("aws-cli/", "AWS CLI"),
    # AWS services
    ("resource-explorer", "AWS Resource Explorer"),
    ("lambda", "AWS Lambda"),
    ("ecs-agent", "ECS Agent"),
    ("sagemaker", "SageMaker"),
    ("bedrock-agent", "Bedrock Agent"),
    # CloudWatch Logs source (set by the parser, not userAgent)
    ("cloudwatch", "CloudWatch Logs"),
]

# CloudTrail event names for Bedrock invocations
_BEDROCK_EVENT_NAMES = ("InvokeModel", "InvokeModelWithResponseStream")
_BEDROCK_EVENT_SOURCE = "bedrock-runtime.amazonaws.com"

# Retry configuration
_MAX_RETRIES = 3
_BASE_BACKOFF_SECONDS = 1.0


# ---------------------------------------------------------------------------
# Usage-token extraction helpers (tolerant of multiple model-family shapes)
# ---------------------------------------------------------------------------
#
# Different Bedrock model families expose token counts under different keys in
# the invocation log ``usage`` block:
#   - Anthropic:      input_tokens / output_tokens,
#                     cache_read_input_tokens / cache_creation_input_tokens
#   - Converse API:   inputTokens / outputTokens,
#                     cacheReadInputTokens / cacheWriteInputTokens
#   - Nova / Titan /  inputTokenCount / outputTokenCount  (also snake variants
#     Meta Llama:     input_token_count / output_token_count)
# The helper below checks a list of well-known key names and returns the first
# present integer value, defaulting to ``default`` when none match. Only
# conservative, documented key variants are included.

# Key-name variants per token dimension, ordered most-specific-first.
_INPUT_TOKEN_KEYS = (
    "input_tokens",
    "inputTokens",
    "inputTokenCount",
    "input_token_count",
)
_OUTPUT_TOKEN_KEYS = (
    "output_tokens",
    "outputTokens",
    "outputTokenCount",
    "output_token_count",
)
_CACHE_READ_TOKEN_KEYS = (
    "cache_read_input_tokens",
    "cacheReadInputTokens",
    "cache_read_input_token_count",
)
_CACHE_WRITE_TOKEN_KEYS = (
    "cache_creation_input_tokens",
    "cacheWriteInputTokens",
    "cache_write_input_tokens",
    "cache_creation_input_token_count",
)


def _first_int(source: dict[str, Any], keys: tuple[str, ...], default: int = 0) -> int:
    """Return the first present key's value coerced to int, else ``default``.

    Tolerant of the differing ``usage`` shapes across Bedrock model families.
    Non-numeric or missing values fall through to the next key / default.
    """
    for key in keys:
        if key in source and source[key] is not None:
            try:
                return int(source[key])
            except (TypeError, ValueError):
                continue
    return default


# Cross-region inference prefixes Bedrock prepends to model IDs. Real
# CloudWatch/CloudTrail records keep these (e.g. ``us.amazon.nova-pro-v1:0``)
# while pricing (DEFAULT_PRICING) and demo/synthetic records use the bare form
# (``amazon.nova-pro-v1:0``). Stripping the prefix at ingestion keeps a single
# logical model from fragmenting across two rows/columns in every aggregation.
_REGION_PREFIXES = ("us.", "eu.", "ap.", "global.")


def _strip_region_prefix(model_id: str) -> str:
    """Strip a leading cross-region inference prefix (us./eu./ap./global.).

    Returns the bare model ID so it matches the non-prefixed form used by
    pricing and demo records. Non-prefixed IDs are returned unchanged.
    """
    if not model_id:
        return model_id
    for prefix in _REGION_PREFIXES:
        if model_id.startswith(prefix):
            return model_id[len(prefix):]
    return model_id


# ---------------------------------------------------------------------------
# Inference-profile ARN capture (Req 4.1)
# ---------------------------------------------------------------------------
#
# When a workload invokes Bedrock through an application inference profile, the
# raw ``modelId`` on the invocation record is the profile ARN, e.g.
#   arn:aws:bedrock:us-west-2:123:inference-profile/us.amazon.nova-pro-v1:0
#   arn:aws:bedrock:us-west-2:123:application-inference-profile/abcd1234
# The parser normalizes ``modelId`` to the bare model form (stripping the ARN
# path + region prefix) so pricing/aggregation keys don't fragment. That
# normalization discards the profile identity, which downstream enrichment
# (Req 4.2) needs to resolve Environment/Application/Team tags. This helper
# captures the full raw ARN — additively, BEFORE stripping — leaving the bare
# ``modelId`` untouched.
_INFERENCE_PROFILE_ARN_RE = re.compile(
    r"^arn:aws:bedrock:[^:]*:[^:]*:(?:application-)?inference-profile/.+",
)


def _extract_inference_profile_arn(raw_model_ref: Any) -> str | None:
    """Return the raw inference-profile ARN when ``raw_model_ref`` is one, else None.

    Matches both plain ``inference-profile/...`` and
    ``application-inference-profile/...`` Bedrock ARNs. Non-ARN or non-profile
    references (bare model IDs, cross-region-prefixed IDs) return ``None`` so
    downstream enrichment can rely on the field being non-null EXACTLY when a
    profile ARN was used (Property 14).
    """
    if not raw_model_ref or not isinstance(raw_model_ref, str):
        return None
    if _INFERENCE_PROFILE_ARN_RE.match(raw_model_ref):
        return raw_model_ref
    return None


def _stable_record_id(
    timestamp: str, user_identity: str, model_id: str, input_tokens: int, output_tokens: int
) -> str:
    """Derive a deterministic id from event content for dedup when requestId is absent.

    The same underlying invocation observed via both CloudWatch and S3 (neither
    carrying a ``requestId``) yields the same id, so it dedups correctly instead
    of double-counting. Uses a sha256 of the salient identifying fields.
    """
    basis = "|".join(
        [
            str(timestamp),
            str(user_identity),
            str(model_id),
            str(input_tokens),
            str(output_tokens),
        ]
    )
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


class CloudTrailError(Exception):
    """Raised when CloudTrail API calls fail after retries."""

    def __init__(self, error_code: str, message: str):
        self.error_code = error_code
        self.message = message
        super().__init__(f"{error_code}: {message}")


class CloudTrailService:
    """Queries Bedrock invocation logs from CloudWatch Logs (recent) + S3 (historical) + CloudTrail (fallback)."""

    # S3 bucket for Bedrock invocation logs (auto-detected from settings)
    _S3_PREFIX = "bedrock-logs"

    def __init__(self):
        self._session_kwargs: dict[str, Any] = {}
        if settings.aws_access_key_id:
            self._session_kwargs["aws_access_key_id"] = settings.aws_access_key_id
        if settings.aws_secret_access_key:
            self._session_kwargs["aws_secret_access_key"] = settings.aws_secret_access_key
        if settings.aws_session_token:
            self._session_kwargs["aws_session_token"] = settings.aws_session_token

        # Per-region client caches. CloudWatch Logs and CloudTrail are
        # regional APIs (a log group / lookup-events call only ever sees its
        # own region's data), so each configured region gets its own client,
        # built lazily via _cloudtrail_client_for/_logs_client_for. The
        # default (single, un-suffixed) attributes below are kept for
        # backward compatibility with any external caller still reaching
        # into `self.client`/`self.logs_client` directly — they're just the
        # cached client for `settings.aws_region`.
        self.__cloudtrail_clients: dict[str, Any] = {}
        self.__logs_clients: dict[str, Any] = {}
        self.client = self._cloudtrail_client_for(settings.aws_region)
        self.logs_client = self._logs_client_for(settings.aws_region)

        # S3 is NOT a per-region resource in this design: Bedrock invocation
        # logging for every configured region writes into ONE shared bucket
        # (bedrock-invocation-logs-<account>), distinguished only by a
        # per-region key PREFIX (bedrock-logs/<region>/...) — see
        # infrastructure/enable-bedrock-logging-all-regions.sh and Step 3.5 of
        # build-and-deploy-backend.sh. So a single S3 client, built against
        # any region (S3 bucket operations are effectively global/regionless
        # for a standard bucket), is sufficient; only the key prefix passed
        # to _query_s3_logs varies per region.
        session = boto3.Session(region_name=settings.aws_region, **self._session_kwargs)
        # max_pool_connections defaults to 10 in botocore, but
        # _fetch_s3_day_objects fans out up to _S3_FETCH_CONCURRENCY (64)
        # concurrent get_object calls against this SAME client — with only 2
        # regions running that fan-out in parallel (_fetch_activity_records'
        # region-level ThreadPoolExecutor), that's up to 128 concurrent
        # requests against a 10-connection pool. Measured impact: pool
        # exhaustion forces urllib3 to discard/recreate connections on every
        # request past the first 10, which serialized what should have been
        # a fully parallel GET phase and was the dominant remaining cost in
        # 30d/90d Overview latency after the listing-phase fix above. Sized
        # to 2x _S3_FETCH_CONCURRENCY as headroom for both regions' fan-outs
        # running concurrently without contending for the same 10 slots.
        self.s3_client = session.client(
            "s3", config=Config(max_pool_connections=self._S3_FETCH_CONCURRENCY * 2)
        )
        self.log_group = settings.bedrock_log_group
        # Derive S3 bucket and account ID
        try:
            sts = session.client("sts")
            self._account_id = sts.get_caller_identity()["Account"]
        except Exception:
            self._account_id = "unknown"
        # Prefer explicit CDK-injected values (BEDROCK_S3_LOG_BUCKET /
        # BEDROCK_S3_LOG_KEY_PREFIX env vars). They match whatever Step 3.5
        # of build-and-deploy-backend.sh actually configured and never race
        # the put-model-invocation-logging-configuration call the way the
        # boot-time auto-detect can. Auto-detect stays as a fallback so
        # out-of-band setups and older deploys keep working.
        #
        # NOTE: BEDROCK_S3_LOG_KEY_PREFIX is single-region
        # (bedrock-logs/<deploy-region>) — it's only used here as the
        # DEFAULT prefix for settings.aws_region. Additional configured
        # regions derive their own prefix from the same base
        # (bedrock-logs/<region>) via _s3_prefix_for(), since every region
        # shares the same bucket but writes under its own region subpath.
        self._s3_bucket = getattr(settings, "bedrock_s3_log_bucket", None) or ""
        self._default_s3_prefix = (
            getattr(settings, "bedrock_s3_log_key_prefix", None) or "bedrock-logs"
        )
        if self._s3_bucket:
            logger.info(
                "Using CDK-injected S3 log bucket: %s (default prefix: %s)",
                self._s3_bucket,
                self._default_s3_prefix,
            )
        else:
            try:
                bedrock_client = session.client(
                    "bedrock", config=Config(connect_timeout=5, read_timeout=5)
                )
                log_config = bedrock_client.get_model_invocation_logging_configuration()
                s3_config = log_config.get("loggingConfig", {}).get("s3Config", {})
                self._s3_bucket = s3_config.get("bucketName", "") or ""
                # Only override the prefix default when the API actually returns one,
                # so the "bedrock-logs" fallback still holds if s3Config is missing.
                api_prefix = s3_config.get("keyPrefix")
                if api_prefix:
                    self._default_s3_prefix = api_prefix
                if self._s3_bucket:
                    logger.info(
                        "Auto-detected S3 log bucket: %s (default prefix: %s)",
                        self._s3_bucket,
                        self._default_s3_prefix,
                    )
            except Exception as exc:
                logger.warning("Could not auto-detect S3 log bucket: %s", exc)
                # Match the naming pattern the deploy scripts create
                # (bedrock-invocation-logs-<account>). The previous fallback
                # included the region and never matched anything the scripts
                # provisioned.
                self._s3_bucket = f"bedrock-invocation-logs-{self._account_id}"

    def _cloudtrail_client_for(self, region: str) -> Any:
        """Return a cached ``cloudtrail`` client for ``region``, creating it lazily."""
        if region not in self.__cloudtrail_clients:
            session = boto3.Session(region_name=region, **self._session_kwargs)
            self.__cloudtrail_clients[region] = session.client("cloudtrail")
        return self.__cloudtrail_clients[region]

    def _logs_client_for(self, region: str) -> Any:
        """Return a cached ``logs`` client for ``region``, creating it lazily."""
        if region not in self.__logs_clients:
            session = boto3.Session(region_name=region, **self._session_kwargs)
            self.__logs_clients[region] = session.client(
                "logs",
                config=Config(read_timeout=55, connect_timeout=10, retries={"max_attempts": 2}),
            )
        return self.__logs_clients[region]

    def _s3_prefix_for(self, region: str) -> str:
        """The S3 key prefix for ``region``'s invocation logs within the shared bucket.

        ``settings.aws_region`` (the deploy region) uses the CDK-injected/
        auto-detected default prefix as-is. Any OTHER configured region
        derives its prefix from the same ``bedrock-logs`` base, since Bedrock
        logging in that region was set up with the matching
        ``bedrock-logs/<region>`` convention (see
        ``enable-bedrock-logging-all-regions.sh``).
        """
        if region == settings.aws_region:
            return self._default_s3_prefix
        base = self._default_s3_prefix.rsplit("/", 1)[0] if "/" in self._default_s3_prefix else "bedrock-logs"
        return f"{base}/{region}"

    def configured_regions(self) -> list[str]:
        """The list of regions to fan out ingestion across.

        Falls back to ``[settings.aws_region]`` when ``CLOUDTRAIL_REGIONS`` is
        unset, so behavior is unchanged for deploys that haven't opted into
        multi-region scanning.
        """
        regions = parse_regions(settings.cloudtrail_regions)
        return regions if regions else [settings.aws_region]

    def get_activity_records(
        self,
        start_date: datetime,
        end_date: datetime,
        regions: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Retrieve raw Bedrock activity records (list only; backward-compatible).

        Thin wrapper over :meth:`get_activity_records_with_meta` that drops the
        truncation and ingestion-error flags for callers that only need records.

        Args:
            regions: Regions to fan out ingestion across. Defaults to
                :meth:`configured_regions` (``CLOUDTRAIL_REGIONS``, falling
                back to ``settings.aws_region``) when omitted.
        """
        records, _, _ = self.get_activity_records_with_meta(start_date, end_date, regions)
        return records

    def get_activity_records_with_truncation(
        self,
        start_date: datetime,
        end_date: datetime,
        regions: list[str] | None = None,
    ) -> tuple[list[dict[str, Any]], bool]:
        """Retrieve raw Bedrock activity records plus a truncation indicator.

        Backward-compatible thin wrapper over :meth:`get_activity_records_with_meta`
        that drops the ingestion-error flag.

        Returns:
            (records, truncated) where ``truncated`` is True if any ingestion
            source hit its record cap (``_MAX_CW_RECORDS`` / ``_MAX_S3_RECORDS``).
        """
        records, truncated, _ = self.get_activity_records_with_meta(start_date, end_date, regions)
        return records, truncated

    def get_activity_records_with_meta(
        self,
        start_date: datetime,
        end_date: datetime,
        regions: list[str] | None = None,
    ) -> tuple[list[dict[str, Any]], bool, bool]:
        """Retrieve raw Bedrock activity records plus truncation + ingestion-error flags.

        Uses the process-wide, user-agnostic raw-ingestion cache (see the module
        docstring for ``_activity_cache``). On a hit, returns shallow copies of
        the cached records so per-request cost mutation can never corrupt the
        shared cache entry. On a miss, performs the hybrid ingestion for the
        coarse hour-bucket window (fanned out across ``regions`` in parallel)
        and caches the raw result.

        Args:
            regions: Regions to fan out ingestion across. Defaults to
                :meth:`configured_regions` when omitted — pass an explicit
                (possibly narrower) list to scope a single request to fewer
                regions than the deployment scans by default (the region
                filter's job).

        Returns:
            (records, truncated, ingestion_error) where:
              - ``truncated`` is True if any ingestion source hit its record cap.
              - ``ingestion_error`` is True when EVERY attempted ingestion source
                errored and none returned records (hard failure — the empty
                result reflects broken data sources, not genuinely no activity).
        """
        effective_regions = tuple(sorted(regions)) if regions else tuple(sorted(self.configured_regions()))
        start_floor, end_ceil, key = _activity_cache_key(start_date, end_date, effective_regions)

        cached = _activity_cache.get(key)
        if cached is not None:
            records, truncated, ingestion_error = cached
            # Return copies so downstream in-place cost mutation is isolated.
            return [dict(r) for r in records], truncated, ingestion_error

        records, truncated, ingestion_error = self._fetch_activity_records(
            start_floor, end_ceil, list(effective_regions)
        )
        _activity_cache.set(key, (records, truncated, ingestion_error))
        return [dict(r) for r in records], truncated, ingestion_error

    def _fetch_activity_records(
        self, start_date: datetime, end_date: datetime, regions: list[str]
    ) -> tuple[list[dict[str, Any]], bool, bool]:
        """Perform the hybrid ingestion (uncached), fanned out across ``regions``.

        Each region runs the full CloudWatch -> S3 -> CloudTrail pipeline
        independently and in PARALLEL (ThreadPoolExecutor — boto3 clients are
        thread-safe for this read-only usage), so total latency is roughly
        max(region latencies) rather than sum(region latencies). A single
        region's total failure never aborts the others: per-region results
        are merged and deduplicated (by record id, which already collapses
        the same invocation seen via multiple sources) after every region has
        finished or failed.

        Returns (deduped_records, truncated, ingestion_error) where
        ``ingestion_error`` is only True when EVERY region's pipeline reported
        a hard failure (see ``_fetch_region_activity_records``).
        """
        records: list[dict[str, Any]] = []
        truncated = False
        region_errors = 0

        if len(regions) == 1:
            # Skip the thread-pool overhead for the common single-region case.
            region_records, region_truncated, region_error = self._fetch_region_activity_records(
                regions[0], start_date, end_date
            )
            records.extend(region_records)
            truncated = truncated or region_truncated
            if region_error:
                region_errors += 1
        else:
            with ThreadPoolExecutor(max_workers=len(regions)) as executor:
                future_to_region = {
                    executor.submit(
                        self._fetch_region_activity_records, region, start_date, end_date
                    ): region
                    for region in regions
                }
                for future in as_completed(future_to_region):
                    region = future_to_region[future]
                    try:
                        region_records, region_truncated, region_error = future.result()
                    except Exception:
                        logger.exception("Unexpected error fetching region %s", region)
                        region_records, region_truncated, region_error = [], False, True
                    records.extend(region_records)
                    truncated = truncated or region_truncated
                    if region_error:
                        region_errors += 1

        # Hard failure: EVERY region's pipeline reported a hard failure (all of
        # ITS sources errored with no records). Distinct from "genuinely no
        # activity" (at least one region's source succeeded, even if empty).
        ingestion_error = len(regions) > 0 and region_errors == len(regions)
        if ingestion_error:
            logger.error(
                "All %d configured region(s) failed ingestion for window %s..%s; "
                "activity data is unavailable, not empty.",
                len(regions),
                start_date.isoformat(),
                end_date.isoformat(),
            )

        # Deduplicate by record ID across regions (a cross-region inference
        # profile can otherwise cause the same logical invocation to appear
        # once from the caller's region and again from wherever it inferred).
        seen_ids: set[str] = set()
        deduped: list[dict[str, Any]] = []
        for r in records:
            rid = r.get("id", "")
            if rid and rid in seen_ids:
                continue
            seen_ids.add(rid)
            deduped.append(r)

        return deduped, truncated, ingestion_error

    def _fetch_region_activity_records(
        self, region: str, start_date: datetime, end_date: datetime
    ) -> tuple[list[dict[str, Any]], bool, bool]:
        """Run the CloudWatch -> S3 -> CloudTrail pipeline for ONE region.

        Identical logic to the pre-multi-region ``_fetch_activity_records``,
        scoped to a single region's clients/log group/S3 prefix. Returns
        (records, truncated, ingestion_error) where ``ingestion_error`` here
        means every source THIS region attempted failed with no records —
        the caller (``_fetch_activity_records``) aggregates this per-region
        flag across all regions to decide the overall ingestion_error.
        """
        records: list[dict[str, Any]] = []
        truncated = False
        attempted = 0
        errored = 0

        # Phase 1: Recent data from CloudWatch Logs (last 48h) — always attempted
        recent_cutoff = end_date - timedelta(hours=48)
        if recent_cutoff < start_date:
            recent_cutoff = start_date

        attempted += 1
        try:
            cw_records, cw_truncated = self._query_cloudwatch_logs(region, recent_cutoff, end_date)
            records.extend(cw_records)
            truncated = truncated or cw_truncated
            logger.info("[%s] CloudWatch Logs: %d records (recent 48h)", region, len(cw_records))
        except Exception as exc:
            errored += 1
            logger.warning("[%s] CloudWatch Logs query failed: %s", region, exc)

        # Phase 2: Historical data from S3 (older than 48h)
        if start_date < recent_cutoff:
            attempted += 1
            try:
                s3_records, s3_truncated = self._query_s3_logs(region, start_date, recent_cutoff)
                records.extend(s3_records)
                truncated = truncated or s3_truncated
                logger.info("[%s] S3 logs: %d records (historical)", region, len(s3_records))
            except Exception as exc:
                errored += 1
                logger.warning("[%s] S3 logs query failed: %s", region, exc)

        # Phase 3: If no records from either source, try CloudTrail
        if not records:
            attempted += 1
            try:
                records = self._query_cloudtrail(region, start_date, end_date)
                logger.info("[%s] CloudTrail fallback: %d records", region, len(records))
            except Exception as exc:
                errored += 1
                logger.warning("[%s] CloudTrail fallback failed: %s", region, exc)

        ingestion_error = attempted > 0 and errored == attempted and not records
        return records, truncated, ingestion_error
    # -----------------------------------------------------------------------
    # CloudWatch Logs (primary)
    # -----------------------------------------------------------------------

    # Maximum records to fetch from CloudWatch to prevent timeouts
    _MAX_CW_RECORDS = 2000

    def _query_cloudwatch_logs(
        self, region: str, start_date: datetime, end_date: datetime
    ) -> tuple[list[dict[str, Any]], bool]:
        """Query CloudWatch Logs in ``region`` for Bedrock invocation log events.

        Queries recent events first (last 48h), then backfills with older events
        up to _MAX_CW_RECORDS total. This ensures recent invocations always appear
        even when there's a large volume of older data.

        Returns (records, truncated) where ``truncated`` is True if the
        ``_MAX_CW_RECORDS`` cap was reached (older history may be missing).
        """
        end_ms = int(end_date.timestamp() * 1000)
        start_ms = int(start_date.timestamp() * 1000)

        # Split: query recent 48h first, then older data
        recent_cutoff = int((end_date - timedelta(hours=48)).timestamp() * 1000)
        if recent_cutoff < start_ms:
            recent_cutoff = start_ms

        records: list[dict[str, Any]] = []

        # Phase 1: Recent events (last 48h) — always fetched
        if recent_cutoff < end_ms:
            records.extend(
                self._fetch_cw_page(region, recent_cutoff, end_ms, self._MAX_CW_RECORDS // 2)
            )

        # Phase 2: Older events — fill remaining capacity
        remaining = self._MAX_CW_RECORDS - len(records)
        if remaining > 0 and start_ms < recent_cutoff:
            older = self._fetch_cw_page(region, start_ms, recent_cutoff, remaining)
            records.extend(older)

        truncated = len(records) >= self._MAX_CW_RECORDS
        if truncated:
            logger.warning(
                "[%s] CloudWatch Logs record cap (%d) reached for window %s..%s; "
                "older history may be omitted.",
                region,
                self._MAX_CW_RECORDS,
                start_date.isoformat(),
                end_date.isoformat(),
            )

        return records, truncated

    def _fetch_cw_page(
        self, region: str, start_ms: int, end_ms: int, max_records: int
    ) -> list[dict[str, Any]]:
        """Fetch up to max_records from CloudWatch Logs in ``region`` for a time window."""
        records: list[dict[str, Any]] = []
        next_token: str | None = None
        logs_client = self._logs_client_for(region)

        while True:
            kwargs: dict[str, Any] = {
                "logGroupName": self.log_group,
                "startTime": start_ms,
                "endTime": end_ms,
                "limit": 100,
            }
            if next_token:
                kwargs["nextToken"] = next_token

            try:
                response = logs_client.filter_log_events(**kwargs)
            except ClientError as exc:
                error_code = exc.response.get("Error", {}).get("Code", "")
                if error_code == "ResourceNotFoundException":
                    logger.info("[%s] CloudWatch log group %s not found", region, self.log_group)
                    return []
                raise

            for event in response.get("events", []):
                parsed = self._parse_cloudwatch_event(event, region)
                if parsed is not None:
                    records.append(parsed)

            if len(records) >= max_records:
                break

            next_token = response.get("nextToken")
            if not next_token:
                break

        return records

    def _fetch_body_from_s3(self, s3_path: str) -> dict[str, Any] | None:
        """Fetch a Bedrock large-data overflow body from an ``s3://`` URI.

        Bedrock's ``largeDataDeliveryS3Config`` redirects any oversized input
        or output payload (over the inline size threshold, typically ~100 KB)
        to a separate object under a ``data/`` prefix, and the main log entry
        replaces ``inputBodyJson`` / ``outputBodyJson`` with an
        ``inputBodyS3Path`` / ``outputBodyS3Path`` field carrying the full URI.

        Returns the parsed JSON body when the object can be fetched and
        decoded, or ``None`` on any failure — network, permissions, missing
        object, corrupted gzip, invalid JSON. Failures are logged at WARNING
        and the caller falls back to an empty body, so tokens/timestamps still
        land in the record but the response text may be missing (which is
        strictly better than the whole record disappearing).
        """
        if not s3_path or not s3_path.startswith("s3://"):
            return None
        _, _, rest = s3_path.partition("s3://")
        bucket, _, key = rest.partition("/")
        if not bucket or not key:
            return None
        try:
            resp = self.s3_client.get_object(Bucket=bucket, Key=key)
            data = resp["Body"].read()
            if key.endswith(".gz"):
                data = gzip.decompress(data)
            return json.loads(data)
        except Exception as exc:
            logger.warning(
                "Could not fetch Bedrock large-data body from %s: %s",
                s3_path,
                type(exc).__name__,
            )
            return None

    def _parse_cloudwatch_event(
        self, event: dict[str, Any], region: str | None = None
    ) -> dict[str, Any] | None:
        """Parse a Bedrock invocation log event from CloudWatch Logs.

        Args:
            region: The caller region this log entry was fetched from (i.e.
                which region's log group/S3 prefix produced this record —
                NOT necessarily where the model actually ran, see
                ``inferenceRegion`` below). Defaults to
                ``settings.aws_region`` when omitted so any existing direct
                caller of this method (tests, `_parse_s3_log_entry`) keeps
                working without threading a region through explicitly.
        """
        region = region or settings.aws_region
        message = event.get("message", "")
        if not message.startswith("{"):
            return None

        try:
            log = json.loads(message)
        except (json.JSONDecodeError, TypeError):
            return None

        if log.get("schemaType") != "ModelInvocationLog":
            return None

        raw_model_id = log.get("modelId", "unknown")
        # Capture the inference-profile ARN (Req 4.1) from the RAW reference
        # BEFORE any normalization strips the profile identity. Non-null only
        # when the raw reference is an application inference-profile ARN.
        inference_profile_arn = _extract_inference_profile_arn(raw_model_id)
        model_id = raw_model_id
        # Normalize cross-region inference model IDs: first drop any inference
        # profile ARN path, then strip the cross-region prefix (us./eu./ap./
        # global.) so the stored/displayed id matches the bare form used by
        # pricing and demo records (prevents matrix/model fragmentation).
        if "/" in model_id:
            model_id = model_id.rsplit("/", 1)[-1]
        model_id = _strip_region_prefix(model_id)

        # Real requestId when present; a stable content-hash fallback is derived
        # below once the salient fields are parsed (see _stable_record_id).
        request_id = log.get("requestId")

        timestamp_str = log.get("timestamp", "")
        try:
            timestamp = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            timestamp = datetime.fromtimestamp(event.get("timestamp", 0) / 1000, tz=timezone.utc)

        identity = log.get("identity", {})
        user_identity = identity.get("arn", "unknown")
        user_name = self._extract_user_name(user_identity)
        # Prefer the caller-supplied requestMetadata identity (set via
        # Converse/InvokeModel `requestMetadata` — see AgentService; this
        # parameter does not exist on InvokeInlineAgent, so AgentCoreInvoker's
        # inline agent path never sets it) over the IAM ARN. In this
        # dashboard's architecture
        # every Bedrock call is made under ONE shared backend credential, so
        # `identity.arn` is always that shared credential — it can never
        # reflect which end user actually triggered the call. requestMetadata
        # is the only place in the log record where that real identity can
        # appear. Falls back to the IAM-ARN-derived identity when no
        # requestMetadata was attached (e.g. calls from other applications in
        # the account that don't set it).
        request_metadata = log.get("requestMetadata")
        if isinstance(request_metadata, dict):
            rm_identity = request_metadata.get("UserIdentity")
            if rm_identity:
                user_identity = rm_identity
                user_name = request_metadata.get("UserName") or self._extract_user_name(rm_identity)

        input_section = log.get("input", {})
        output_section = log.get("output", {})
        input_tokens = int(input_section.get("inputTokenCount", 0))
        output_tokens = int(output_section.get("outputTokenCount", 0))

        cache_read_tokens = 0
        cache_write_tokens = 0
        output_body = output_section.get("outputBodyJson", {})
        input_body = input_section.get("inputBodyJson", {})
        # Follow Bedrock large-data overflow references. When a payload exceeds
        # the inline threshold, the main log carries an *_BodyS3Path URI
        # instead of the inline *BodyJson dict, and the actual JSON lives in a
        # separate object under the largeDataDeliveryS3Config prefix. Fetch
        # and swap in the real body so response text is visible in the UI and
        # token/prompt extraction below has something to work with.
        if not output_body:
            fetched = self._fetch_body_from_s3(output_section.get("outputBodyS3Path", ""))
            if fetched is not None:
                output_body = fetched
        if not input_body:
            fetched = self._fetch_body_from_s3(input_section.get("inputBodyS3Path", ""))
            if fetched is not None:
                input_body = fetched
        if isinstance(output_body, dict):
            usage = output_body.get("usage", {})
            if isinstance(usage, dict) and usage:
                # Tolerant extraction across model families (Anthropic, Converse
                # API, Nova/Titan, Meta Llama). Fall back to the section-level
                # counts already parsed above when a dimension is absent.
                input_tokens = _first_int(usage, _INPUT_TOKEN_KEYS, input_tokens)
                output_tokens = _first_int(usage, _OUTPUT_TOKEN_KEYS, output_tokens)
                cache_read_tokens = _first_int(usage, _CACHE_READ_TOKEN_KEYS, 0)
                cache_write_tokens = _first_int(usage, _CACHE_WRITE_TOKEN_KEYS, 0)

        # Extract prompt text
        prompt_text = ""
        if isinstance(input_body, dict):
            messages = input_body.get("messages", [])
            if messages:
                user_messages = [m for m in messages if m.get("role") == "user"]
                last_user = user_messages[-1] if user_messages else messages[-1]
                content = last_user.get("content", "")
                if isinstance(content, list):
                    prompt_text = " ".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("text"))
                else:
                    prompt_text = str(content)
            else:
                prompt_text = str(input_body.get("prompt", ""))

        # Extract response text
        response_text = ""
        if isinstance(output_body, dict):
            converse_output = output_body.get("output", {})
            if isinstance(converse_output, dict):
                msg = converse_output.get("message", {})
                if isinstance(msg, dict):
                    content_blocks = msg.get("content", [])
                    if isinstance(content_blocks, list):
                        response_text = " ".join(b.get("text", "") for b in content_blocks if isinstance(b, dict))
            if not response_text:
                content_blocks = output_body.get("content", [])
                if isinstance(content_blocks, list):
                    response_text = " ".join(b.get("text", "") for b in content_blocks if isinstance(b, dict) and b.get("text"))
            if not response_text:
                response_text = str(output_body.get("completion", output_body.get("outputs", "")))

        timestamp_iso = timestamp.isoformat() if isinstance(timestamp, datetime) else str(timestamp)

        # Prefer the real requestId; otherwise derive a STABLE deterministic id
        # from the event content so the same invocation seen via both CloudWatch
        # and S3 dedups correctly (rather than getting a random uuid each time).
        record_id = request_id or _stable_record_id(
            timestamp_iso, user_identity, model_id, input_tokens, output_tokens
        )

        return {
            "id": record_id,
            "timestamp": timestamp_iso,
            "userIdentity": user_identity,
            "userName": user_name,
            "modelId": model_id,
            "inferenceProfileArn": inference_profile_arn,
            # `region`: the region this log entry was fetched FROM (which
            # region's log group/S3 prefix produced this record — the
            # dashboard's ingestion scope, used for the region filter).
            # `usedCrossRegionInference`: True when the raw model reference
            # carries a cross-region inference prefix (us./eu./ap./global.),
            # meaning Bedrock may have routed the actual inference to a
            # DIFFERENT specific region within that geography than `region`.
            # Bedrock's ModelInvocationLog schema does not expose which exact
            # region ultimately served the request in that case, so we
            # surface the (honest, best-effort) signal that cross-region
            # routing was in play rather than fabricating a specific region.
            "region": region,
            "usedCrossRegionInference": raw_model_id != model_id and "/" not in raw_model_id,
            "sourceApplication": "CloudWatch Logs",
            "sourceIp": "unknown",
            "inputTokens": input_tokens,
            "outputTokens": output_tokens,
            "cacheReadTokens": cache_read_tokens,
            "cacheWriteTokens": cache_write_tokens,
            "totalTokens": input_tokens + output_tokens + cache_read_tokens + cache_write_tokens,
            "costEstimate": None,
            "promptText": prompt_text,
            "responseText": response_text,
        }

    # -----------------------------------------------------------------------
    # CloudTrail (fallback)
    # -----------------------------------------------------------------------
    # S3 Logs (historical)
    # -----------------------------------------------------------------------

    _MAX_S3_RECORDS = 5000

    # Bounded concurrency for both S3 sub-phases of a date-range scan:
    #   1. Listing (``_list_s3_days``) — one list_objects_v2 (+ pagination) call
    #      per calendar day in the window.
    #   2. Fetching (``_fetch_s3_day_objects``) — one get_object call per log
    #      object within a day.
    # Measured against this account's bucket: a naive SEQUENTIAL day-by-day
    # list loop alone took ~7-8s for a 90-day window (regardless of how many
    # objects existed that day — each list_objects_v2 call still costs
    # ~70-250ms of round-trip latency), which is what made 30d/90d Overview
    # requests slow even after the GET-phase fix below. Fanning the listing
    # out the same way cuts that to well under 1s. boto3 S3 clients are
    # thread-safe for this read-only usage, matching the existing
    # region-level ThreadPoolExecutor fan-out pattern in
    # ``_fetch_activity_records`` (which parallelizes across regions, not
    # within one region's S3 scan — this constant is independent of that).
    # 16 was the initial value but under-parallelized the GET phase on a
    # window with ~700-900 objects (6-7s); 64 measured ~2.5x faster (2.6s)
    # with no further gain at 100 — GET-phase latency is dominated by S3
    # per-request round-trip time, not local CPU, so higher concurrency here
    # is safe and cheap (bounded worker count, not bounded by object count).
    _S3_FETCH_CONCURRENCY = 64

    def _query_s3_logs(
        self, region: str, start_date: datetime, end_date: datetime
    ) -> tuple[list[dict[str, Any]], bool]:
        """Query S3 for Bedrock invocation log files written from ``region``.

        S3 path pattern: {prefix}/AWSLogs/{account}/BedrockModelInvocationLogs/{region}/{year}/{month}/{day}/{hour}/
        Each file is a gzipped JSON log entry. The bucket is SHARED across all
        configured regions (Bedrock invocation logging for each region writes
        into the same account-wide bucket) — only the key prefix varies, via
        ``_s3_prefix_for(region)``.

        Two-phase, both fanned out concurrently (see ``_S3_FETCH_CONCURRENCY``):
          1. ``_list_s3_days`` lists every day in the window in parallel,
             returning (day, keys) pairs in the same newest-first day order
             the caller iterates below.
          2. All listed keys are flattened, in that same newest-first day
             order, into a SINGLE list, truncated to ``_MAX_S3_RECORDS``
             *before* any GET happens (so a dense window never fetches
             objects beyond the cap just to discard them), then fetched in
             ONE parallel batch via ``_fetch_s3_day_objects`` — rather than
             spinning up a new thread pool per day, which is wasteful when
             most days only have a handful of objects.

        Days are listed NEWEST->OLDEST so that when the ``_MAX_S3_RECORDS``
        cap truncates the flattened key list, the most recent history is
        what's kept.

        Returns (records, truncated) where ``truncated`` is True if the cap was
        reached (older history may be missing).
        """
        s3_prefix = self._s3_prefix_for(region)

        # Generate the day list, newest day first (matches prior behavior).
        current = end_date.replace(hour=0, minute=0, second=0, microsecond=0)
        start_day = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
        days: list[datetime] = []
        while current >= start_day:
            days.append(current)
            current -= timedelta(days=1)

        day_keys = self._list_s3_days(days, region, s3_prefix)

        # Flatten in newest-first order (day_keys is already newest-first;
        # within a day, listing order is fine since object-level ordering
        # doesn't matter for the cap — only which DAYS are newest does).
        all_keys: list[str] = [key for _day, keys in day_keys for key in keys]

        truncated = len(all_keys) > self._MAX_S3_RECORDS
        if truncated:
            all_keys = all_keys[: self._MAX_S3_RECORDS]
            logger.warning(
                "[%s] S3 logs record cap (%d) reached for window %s..%s; "
                "older history may be omitted (newest retained).",
                region,
                self._MAX_S3_RECORDS,
                start_date.isoformat(),
                end_date.isoformat(),
            )

        if not all_keys:
            return [], truncated

        records = self._fetch_s3_day_objects(all_keys, region, start_date, end_date)
        return records, truncated

    def _list_s3_days(
        self, days: list[datetime], region: str, s3_prefix: str
    ) -> list[tuple[datetime, list[str]]]:
        """List ``.json.gz`` keys for each day in ``days`` concurrently.

        Returns (day, keys) pairs in the SAME order as the input ``days`` list
        (executor.map preserves submission order regardless of completion
        order), so callers relying on newest-first day order are unaffected.
        """
        if not days:
            return []

        def list_one_day(day: datetime) -> list[str]:
            date_prefix = (
                f"{s3_prefix}/AWSLogs/{self._account_id}/BedrockModelInvocationLogs/"
                f"{region}/{day.strftime('%Y/%m/%d')}/"
            )
            keys: list[str] = []
            try:
                paginator = self.s3_client.get_paginator("list_objects_v2")
                for page in paginator.paginate(Bucket=self._s3_bucket, Prefix=date_prefix):
                    for obj in page.get("Contents", []):
                        key = obj["Key"]
                        if key.endswith(".json.gz"):
                            keys.append(key)
            except Exception as exc:
                logger.debug("[%s] S3 list failed for prefix %s: %s", region, date_prefix, exc)
            return keys

        max_workers = min(self._S3_FETCH_CONCURRENCY, len(days))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            keys_per_day = list(executor.map(list_one_day, days))

        return list(zip(days, keys_per_day))

    def _fetch_s3_day_objects(
        self, keys: list[str], region: str, start_date: datetime, end_date: datetime
    ) -> list[dict[str, Any]]:
        """GET + parse a batch of S3 log objects concurrently.

        Despite the (retained, pre-existing) method name, ``keys`` may span
        multiple days — ``_query_s3_logs`` flattens all listed keys across
        the whole window into one list before calling this, so a single
        thread pool is reused for the entire region's fetch rather than
        recreated per day. Uses a bounded thread pool
        (``_S3_FETCH_CONCURRENCY``) rather than one GET at a time — boto3 S3
        clients are thread-safe for this read-only usage, matching the
        existing region-level fan-out pattern. Preserves input key order
        (``executor.map`` yields results in submission order).
        """
        import gzip

        def fetch_one(key: str) -> dict[str, Any] | None:
            try:
                resp = self.s3_client.get_object(Bucket=self._s3_bucket, Key=key)
                body = gzip.decompress(resp["Body"].read())
                log = json.loads(body)

                parsed = self._parse_s3_log_entry(log, region)
                if parsed:
                    ts = parsed.get("timestamp", "")
                    if ts >= start_date.isoformat() and ts <= end_date.isoformat():
                        return parsed
            except Exception as exc:
                logger.debug("[%s] Failed to parse S3 log %s: %s", region, key, exc)
            return None

        max_workers = min(self._S3_FETCH_CONCURRENCY, len(keys))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            return [parsed for parsed in executor.map(fetch_one, keys) if parsed is not None]

    def _parse_s3_log_entry(
        self, log: dict[str, Any], region: str | None = None
    ) -> dict[str, Any] | None:
        """Parse a single S3 Bedrock invocation log entry.

        S3 log format is the same as CloudWatch Logs (ModelInvocationLog schema).
        Reuses _parse_cloudwatch_event with a wrapper.
        """
        if log.get("schemaType") != "ModelInvocationLog":
            return None

        # Wrap in the format _parse_cloudwatch_event expects
        event = {"message": json.dumps(log), "timestamp": 0}
        return self._parse_cloudwatch_event(event, region)

    # -----------------------------------------------------------------------
    # CloudTrail (fallback)
    # -----------------------------------------------------------------------

    def _query_cloudtrail(
        self, region: str, start_date: datetime, end_date: datetime
    ) -> list[dict[str, Any]]:
        """Fallback: query CloudTrail LookupEvents in ``region`` for Bedrock invocations."""
        lookup_attributes = [
            {
                "AttributeKey": "EventSource",
                "AttributeValue": _BEDROCK_EVENT_SOURCE,
            }
        ]

        records: list[dict[str, Any]] = []
        next_token: str | None = None

        while True:
            kwargs: dict[str, Any] = {
                "LookupAttributes": lookup_attributes,
                "StartTime": start_date,
                "EndTime": end_date,
                "MaxResults": 50,
            }
            if next_token:
                kwargs["NextToken"] = next_token

            response = self._call_with_retry(region, kwargs)

            for event in response.get("Events", []):
                parsed = self._parse_event(event, region)
                if parsed is not None:
                    records.append(parsed)

            next_token = response.get("NextToken")
            if not next_token:
                break

        return records

    def _call_with_retry(self, region: str, kwargs: dict[str, Any]) -> dict[str, Any]:
        """Call CloudTrail lookup_events in ``region`` with exponential backoff retry.

        Retries up to _MAX_RETRIES times on ClientError, then raises
        CloudTrailError with the error code and message.
        """
        last_error: ClientError | None = None
        client = self._cloudtrail_client_for(region)
        for attempt in range(_MAX_RETRIES):
            try:
                return client.lookup_events(**kwargs)
            except ClientError as exc:
                last_error = exc
                if attempt < _MAX_RETRIES - 1:
                    backoff = _BASE_BACKOFF_SECONDS * (2 ** attempt)
                    time.sleep(backoff)

        # All retries exhausted
        error_response = last_error.response.get("Error", {})  # type: ignore[union-attr]
        raise CloudTrailError(
            error_code=error_response.get("Code", "UnknownError"),
            message=error_response.get("Message", str(last_error)),
        )

    def _parse_event(
        self, event: dict[str, Any], region: str | None = None
    ) -> dict[str, Any] | None:
        """Parse a single CloudTrail event into an activity record dict.

        Returns None if the event is not a Bedrock invocation we care about.

        Args:
            region: The region this CloudTrail event was looked up from.
                Defaults to ``settings.aws_region`` when omitted.
        """
        region = region or settings.aws_region
        cloud_trail_event_raw = event.get("CloudTrailEvent", "{}")
        try:
            ct_event = json.loads(cloud_trail_event_raw)
        except (json.JSONDecodeError, TypeError):
            return None

        event_name = ct_event.get("eventName", "")
        if event_name not in _BEDROCK_EVENT_NAMES:
            return None

        # Extract user identity
        user_identity_info = ct_event.get("userIdentity", {})
        user_identity = (
            user_identity_info.get("arn", "")
            or user_identity_info.get("userName", "")
            or user_identity_info.get("principalId", "unknown")
        )
        user_name = self._extract_user_name(user_identity)

        # Extract model ID from requestParameters, applying the same
        # normalization as the CloudWatch path: drop any inference-profile ARN
        # path segment, then strip the cross-region prefix so this id collapses
        # onto the bare form used by pricing and demo records.
        request_params = ct_event.get("requestParameters", {}) or {}
        raw_model_id = request_params.get("modelId", "unknown")

        # Prefer requestMetadata over the IAM ARN — see the matching comment in
        # _parse_cloudwatch_event for why. CloudTrail surfaces API request
        # parameters (including requestMetadata, a small tag map) under the
        # same requestParameters object as modelId.
        ct_request_metadata = request_params.get("requestMetadata")
        if isinstance(ct_request_metadata, dict):
            rm_identity = ct_request_metadata.get("UserIdentity")
            if rm_identity:
                user_identity = rm_identity
                user_name = ct_request_metadata.get("UserName") or self._extract_user_name(rm_identity)
        # Capture the inference-profile ARN (Req 4.1) from the RAW reference
        # BEFORE normalization strips the profile identity. Non-null only when
        # the raw reference is an application inference-profile ARN.
        inference_profile_arn = _extract_inference_profile_arn(raw_model_id)
        model_id = raw_model_id
        if "/" in model_id:
            model_id = model_id.rsplit("/", 1)[-1]
        model_id = _strip_region_prefix(model_id)

        # Extract timestamp
        event_time_str = ct_event.get("eventTime", "")
        try:
            timestamp = datetime.fromisoformat(
                event_time_str.replace("Z", "+00:00")
            )
        except (ValueError, AttributeError):
            timestamp = event.get("EventTime", datetime.now(timezone.utc))

        # Extract source IP
        source_ip = ct_event.get("sourceIPAddress", "unknown")

        # Extract token counts from responseElements
        response_elements = ct_event.get("responseElements", {}) or {}
        usage = response_elements.get("usage", {}) or {}
        input_tokens = int(usage.get("inputTokens", 0))
        output_tokens = int(usage.get("outputTokens", 0))
        cache_read_tokens = int(usage.get("cacheReadInputTokens", 0))
        cache_write_tokens = int(usage.get("cacheWriteInputTokens", 0))

        # Identify source application from user-agent
        user_agent = ct_event.get("userAgent", "")
        source_application = self._identify_source_application(user_agent)

        return {
            "id": ct_event.get("eventID", str(uuid.uuid4())),
            "timestamp": timestamp.isoformat() if isinstance(timestamp, datetime) else str(timestamp),
            "userIdentity": user_identity,
            "userName": user_name,
            "modelId": model_id,
            "inferenceProfileArn": inference_profile_arn,
            # See the matching comment in _parse_cloudwatch_event for what
            # these two fields mean.
            "region": region,
            "usedCrossRegionInference": raw_model_id != model_id and "/" not in raw_model_id,
            "sourceApplication": source_application,
            "sourceIp": source_ip,
            "inputTokens": input_tokens,
            "outputTokens": output_tokens,
            "cacheReadTokens": cache_read_tokens,
            "cacheWriteTokens": cache_write_tokens,
            "totalTokens": input_tokens + output_tokens + cache_read_tokens + cache_write_tokens,
            "costEstimate": None,  # Populated later by CostService
        }

    @staticmethod
    def _identify_source_application(user_agent: str) -> str:
        """Identify the source application from the CloudTrail userAgent field.

        Patterns:
            - Contains "kiro" (case-insensitive) → "Kiro"
            - Contains "q-developer" (case-insensitive) → "Q Developer"
            - Otherwise → "Unknown"
        """
        if not user_agent:
            return "Unknown"
        ua_lower = user_agent.lower()
        for pattern, label in _SOURCE_APP_PATTERNS:
            if pattern in ua_lower:
                return label
        return "Unknown"

    @staticmethod
    def _extract_user_name(arn: str) -> str:
        """Extract a human-readable user name from an IAM ARN.

        Examples:
            arn:aws:iam::123456789012:user/johndoe → johndoe
            arn:aws:sts::123456789012:assumed-role/MyRole/session → MyRole/session
        """
        if "/" in arn:
            return arn.rsplit("/", 1)[-1]
        return arn
