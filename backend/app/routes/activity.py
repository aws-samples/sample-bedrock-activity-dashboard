"""Activity API routes for the Bedrock Activity Dashboard."""

import logging
from collections import defaultdict
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.auth import CurrentUser, get_current_user
from app.database import get_db
from app.services.aggregation_service import AggregationService
from app.services.cloudtrail_service import CloudTrailError, CloudTrailService
from app.services.cost_service import CostService
from app.services.cur_ingestion_service import CurIngestionService
from app.services.export_service import ExportService
from app.services.filter_service import ActivityFilters, FilterService, parse_iso_to_utc
from app.services.profile_tag_service import ProfileTagService
from app.services.prompt_category_service import build_spend_breakdown
from app.services.reconciliation_service import ReconciliationService

router = APIRouter(prefix="/api/activity", tags=["activity"])

logger = logging.getLogger(__name__)

# Shared service instances
_cloudtrail_service = CloudTrailService()
_aggregation_service = AggregationService()
_filter_service = FilterService()
_cost_service = CostService()
_export_service = ExportService()
# Cost-governance services (Tier A billed cost + profile enrichment). All are
# lazy about their AWS clients, so instantiating them at import time performs no
# I/O and never fails when credentials/region are unavailable.
_cur_ingestion_service = CurIngestionService()
_profile_tag_service = ProfileTagService()
_reconciliation_service = ReconciliationService()

# Default dimension value when a record has no resolvable inference-profile tag.
_UNKNOWN_DIMENSION = "unknown"

_VALID_SECTIONS = {"token-usage", "model-usage", "cost-analysis", "prompt-history"}


def _parse_filters(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    user_identities: Optional[str] = None,
    team_id: Optional[str] = None,
    source_application: Optional[str] = None,
    regions: Optional[str] = None,
) -> dict[str, Any]:
    """Parse and validate common query parameters into a filter dict."""
    if start_date and end_date:
        start_dt = parse_iso_to_utc(start_date)
        end_dt = parse_iso_to_utc(end_date, end_of_day_if_bare=True)
    elif start_date or end_date:
        default_start, default_end = _filter_service.get_default_date_range()
        start_dt = parse_iso_to_utc(start_date) if start_date else default_start
        if end_date:
            end_dt = parse_iso_to_utc(end_date, end_of_day_if_bare=True)
        else:
            end_dt = default_end
    else:
        start_dt, end_dt = _filter_service.get_default_date_range()

    _filter_service.validate_date_range(start_dt, end_dt)

    users = (
        [u.strip() for u in user_identities.split(",") if u.strip()]
        if user_identities
        else []
    )
    region_list = (
        [r.strip() for r in regions.split(",") if r.strip()]
        if regions
        else []
    )

    return {
        "start_date": start_dt,
        "end_date": end_dt,
        "user_identities": users,
        "team_id": team_id,
        "source_application": source_application,
        "regions": region_list,
    }


def _scope_to_user(params: dict[str, Any], current_user: CurrentUser) -> dict[str, Any]:
    """Best-effort per-user scoping.

    Callers without cross-user view rights (``can_view_all`` — admin, payer,
    or superuser groups) are restricted to their own activity records by
    overriding the effective ``user_identities`` filter with the caller's own
    identity (email or username). Callers with ``can_view_all`` keep
    full/unfiltered access and any client-supplied user filters. This is a
    READ-visibility check, distinct from ``is_admin`` (which gates mutating
    actions) — see ``CurrentUser`` in app/auth.py.
    """
    if current_user is not None and not current_user.can_view_all:
        params["user_identities"] = [current_user.identity]
    return params


def _fetch_and_filter(
    params: dict[str, Any],
    db: Session,
) -> list[dict[str, Any]]:
    """Fetch + cost + filter records, discarding the truncation/ingestion flags.

    Thin wrapper over :func:`_fetch_and_filter_with_meta` for the endpoints that
    do not surface truncation or ingestion errors.
    """
    records, _, _ = _fetch_and_filter_with_meta(params, db)
    return records


def enrich_records_with_dimensions(
    records: list[dict[str, Any]],
    db: Session,
) -> None:
    """Attach additive workload dimensions to every record in place.

    Ensures each record carries the additive cost-governance fields without ever
    removing existing fields (especially ``modelId``):

      - ``inferenceProfileArn``: the raw inference-profile ARN when the invocation
        used one (already set upstream by ``CloudTrailService`` for CT/CW/S3
        records); defaulted to ``None`` for chat/demo records that lack it.
      - ``environment`` / ``application`` / ``attributedTeam``: resolved from the
        profile ARN via :meth:`ProfileTagService.resolve_map`, defaulting to
        ``"unknown"`` when unresolved or absent.
      - ``agentName`` / ``costCenter`` / ``useCase`` / ``owner`` / ``project`` /
        ``department``: the extended tag taxonomy, resolved from the same
        profile-tag lookup and defaulting to ``"unknown"`` on the same terms.
        Additive: existing consumers that only read the original 3 dimensions
        are unaffected.

    Resolution is batched — the distinct non-null ARNs are resolved in a single
    ``resolve_map`` call — and best-effort: any failure leaves every record with
    ``"unknown"`` dimensions rather than failing the request.
    """
    if not records:
        return

    arns = {
        record["inferenceProfileArn"]
        for record in records
        if record.get("inferenceProfileArn")
    }

    tag_map: dict[str, Any] = {}
    if arns:
        try:
            tag_map = _profile_tag_service.resolve_map(arns, db)
        except Exception:
            # Best-effort: never fail the request because tag resolution failed.
            logger.exception("Profile-tag resolution failed; defaulting to 'unknown'")
            tag_map = {}

    for record in records:
        arn = record.get("inferenceProfileArn")
        # Ensure the field is always present (None when the record had no ARN).
        record["inferenceProfileArn"] = arn if arn else None

        tags = tag_map.get(arn) if arn else None
        record["environment"] = (
            tags.environment if tags and tags.environment else _UNKNOWN_DIMENSION
        )
        record["application"] = (
            tags.application if tags and tags.application else _UNKNOWN_DIMENSION
        )
        record["attributedTeam"] = (
            tags.team if tags and tags.team else _UNKNOWN_DIMENSION
        )
        # Extended tag taxonomy (additive; same "unknown" default convention).
        record["agentName"] = (
            tags.agent_name if tags and tags.agent_name else _UNKNOWN_DIMENSION
        )
        record["costCenter"] = (
            tags.cost_center if tags and tags.cost_center else _UNKNOWN_DIMENSION
        )
        record["useCase"] = (
            tags.use_case if tags and tags.use_case else _UNKNOWN_DIMENSION
        )
        record["owner"] = (
            tags.owner if tags and tags.owner else _UNKNOWN_DIMENSION
        )
        record["project"] = (
            tags.project if tags and tags.project else _UNKNOWN_DIMENSION
        )
        record["department"] = (
            tags.department if tags and tags.department else _UNKNOWN_DIMENSION
        )


def source_activity_records_with_meta(
    start_date: datetime,
    end_date: datetime,
    db: Session,
    regions: Optional[list[str]] = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool, bool]:
    """Source raw activity records for a window WITHOUT user/team filtering.

    Runs the shared ingestion pipeline used by every activity view:
    CloudTrail / CloudWatch / S3 (via CloudTrailService) + persisted demo records
    + Agent Chat records, then applies cost estimates in place. No user/team/date
    filtering is performed here so callers can layer their own scoping — this is
    the single sourcing path shared by the activity route and the teams route.

    Args:
        regions: Regions to scope INGESTION to (which regions CloudTrailService
            fetches from). Defaults to every configured region
            (``CLOUDTRAIL_REGIONS``) when omitted. This is separate from — and
            runs BEFORE — the region *filter* applied later in
            ``FilterService.apply_filters``; scoping ingestion itself avoids
            fetching regions the caller has already excluded via the filter.

    Returns ``(ct_records, chat_records, truncated, ingestion_error)`` where:
      - ``ct_records`` is CloudTrail/real records merged with persisted demo
        records (costs applied in place).
      - ``chat_records`` is Agent Chat records (costs applied in place).
      - ``truncated`` reflects whether the raw ingestion hit a record cap.
      - ``ingestion_error`` reflects whether the REAL fetch had every attempted
        source fail. Computed from the real ingestion only; DEMO_MODE synthetic
        records are injected afterward and do not clear the flag.
    """
    from app.services.chat_message_service import (
        get_chat_activity_records,
        ingestion_regions_for_chat_dedup,
        suppress_chat_placeholders_with_real_invocation,
    )
    from app.services.demo_service import (
        get_demo_records,
        suppress_real_duplicates_of_demo_invocations,
    )

    # Widen ingestion (not the display filter) to always include the region
    # Agent Chat's real Bedrock calls run in — otherwise a region filter that
    # excludes it starves the suppression step below of the real invocation
    # record it needs to match against, and the chat placeholder (0 tokens/
    # N/A cost) survives even though the real, priced invocation exists.
    ingestion_regions = ingestion_regions_for_chat_dedup(regions)
    records, truncated, ingestion_error = _cloudtrail_service.get_activity_records_with_meta(
        start_date, end_date, ingestion_regions
    )

    # Merge PERSISTED demo records into the same pipeline so costs get computed
    # for them alongside real records. These only exist when an admin has
    # generated a demo batch, so this is safe to always run (no-op otherwise).
    try:
        demo_records = get_demo_records(db, start_date, end_date)
        if demo_records:
            logger.info("Merged %d persisted demo records", len(demo_records))

            # Every demo record is backed by a genuine Bedrock call the backend
            # itself made, which independently shows up via CloudTrail/CloudWatch/
            # S3 ingestion (under the task-role identity, at the real invocation
            # time) — without this the same call is double-counted: once as the
            # friendly demo record, once as a bare real record. Drop the real
            # duplicate, keep the demo record. Best-effort; a real ingestion
            # failure here never blocks demo records from appearing.
            before = len(records)
            records = suppress_real_duplicates_of_demo_invocations(demo_records, records)
            suppressed = before - len(records)
            if suppressed:
                logger.info(
                    "Suppressed %d real record(s) duplicating a demo invocation", suppressed
                )
        records = records + demo_records
    except Exception:
        logger.exception("Failed to fetch persisted demo records")

    # Fetch Agent Chat records from database. Note: the Prompt History
    # toggle gates BODY visibility (prompt/response text in the detail
    # modal), not metadata records in the activity feed. Agent Chat
    # metadata (count, cost, token usage) is always included here so the
    # analytics tabs reflect every invocation; body text is gated
    # separately in routes/prompts.py::get_prompt_detail.
    chat_records: list[dict[str, Any]] = []
    try:
        chat_records = get_chat_activity_records(db, start_date, end_date)
        logger.info("Fetched %d Agent Chat records for activity", len(chat_records))
    except Exception:
        logger.exception("Failed to fetch Agent Chat records for activity")

    # Every Agent Chat turn also makes a real Bedrock call that independently
    # shows up via CloudTrail/CloudWatch/S3 ingestion (with real token counts
    # and cost), so without this step the same invocation is double-counted:
    # once as a real record, once as the chat placeholder (which always
    # carries inputTokens=0/costEstimate=None). Drop the placeholder whenever
    # a matching real record is present; best-effort, so an unmatched
    # placeholder is kept rather than silently dropped.
    try:
        before = len(chat_records)
        chat_records = suppress_chat_placeholders_with_real_invocation(chat_records, records)
        suppressed = before - len(chat_records)
        if suppressed:
            logger.info(
                "Suppressed %d Agent Chat placeholder(s) with a matching real invocation",
                suppressed,
            )
    except Exception:
        logger.exception("Failed to suppress duplicate Agent Chat placeholders")

    all_records = records + chat_records
    _cost_service.apply_costs(all_records, db)

    # Enrich ONCE here, right after costs are applied, so every downstream
    # consumer (activity + governance) sees the same enriched records. Mutates
    # each record dict in place; ``all_records`` shares dict references with both
    # ``records`` and ``chat_records``, so enriching it enriches both.
    enrich_records_with_dimensions(all_records, db)

    return records, chat_records, truncated, ingestion_error


def _fetch_and_filter_with_meta(
    params: dict[str, Any],
    db: Session,
) -> tuple[list[dict[str, Any]], bool, bool]:
    """Fetch records from CloudTrail + Agent Chat, apply costs, then apply filters.

    Pipeline: CloudTrailService + ChatMessageService → CostService → FilterService

    Returns (records, truncated, ingestion_error) where:
      - ``truncated`` reflects whether the raw ingestion hit a record cap.
      - ``ingestion_error`` reflects whether the REAL fetch had every attempted
        source fail (surfaced additively on the API response). Note this is
        computed from the real ingestion only; DEMO_MODE synthetic records are
        injected afterward and do not clear the flag.
    """
    records, chat_records, truncated, ingestion_error = source_activity_records_with_meta(
        params["start_date"], params["end_date"], db, params.get("regions") or None
    )

    # Apply filters to CloudTrail records (includes user identity filter)
    filters = ActivityFilters(
        start_date=params["start_date"],
        end_date=params["end_date"],
        user_identities=params["user_identities"],
        source_application=params["source_application"],
        regions=params.get("regions", []),
    )
    filtered_ct = _filter_service.apply_filters(records, filters)

    # Apply filters to Agent Chat records using the SAME user identity filter as
    # the CloudTrail records. When a user filter is set (params["user_identities"]
    # non-empty) Agent Chat records are only included if they match it — they
    # won't for a specific human user since their identity is "agent-chat" — so
    # agent-chat no longer leaks in. When no user filter is set (empty list),
    # apply_filters applies no user predicate and chat records are still included.
    #
    # NOTE: the region filter is intentionally NOT applied here (regions left
    # unset on chat_filters). Agent Chat records don't carry a `region` field
    # today — every chat turn runs through settings.bedrock_region, a single
    # fixed region, and tagging those records is out of scope for this pass
    # (same boundary as team_id, which also isn't applied to chat records
    # below). Region filtering only applies to the CloudTrail/CloudWatch/S3-
    # ingested `records`, where region actually varies per invocation.
    chat_filters = ActivityFilters(
        start_date=params["start_date"],
        end_date=params["end_date"],
        user_identities=params["user_identities"],
        source_application=params["source_application"],
    )
    filtered_chat = _filter_service.apply_filters(chat_records, chat_filters)

    combined = filtered_ct + filtered_chat

    if params["team_id"]:
        team_filtered_ct = _filter_service.filter_by_team(filtered_ct, params["team_id"], db)
        combined = team_filtered_ct + filtered_chat

    return combined, truncated, ingestion_error


def _error_response(exc: CloudTrailError) -> dict[str, Any]:
    """Build a 502 error response body from a CloudTrailError."""
    return {
        "error": {
            "code": exc.error_code,
            "message": exc.message,
        }
    }


def _scoped_principals(params: dict[str, Any]) -> Optional[list[str]]:
    """Return the effective principal scope for billed-slice lookups.

    When the request is scoped (non-admin, or a client-supplied user filter) the
    effective ``user_identities`` list is used so a caller only ever sees billed
    figures for their own principals. An empty list means "all principals"
    (``None``), matching the behavior of ``load_billed_slices``.
    """
    return params.get("user_identities") or None


def _load_billed_total(params: dict[str, Any], db: Session) -> Optional[float]:
    """Sum billed cost for the scoped window from ``cur_billed_cost``.

    Returns ``None`` when CUR is unavailable or the indexed lookup yields no
    billed rows. Best-effort: any failure logs and degrades to ``None`` (the
    request never fails because billed data could not be read). Athena is never
    queried here — this reads only the stored billed table.
    """
    try:
        slices = _reconciliation_service.load_billed_slices(
            db,
            params["start_date"],
            params["end_date"],
            _scoped_principals(params),
        )
    except Exception:
        logger.exception("Failed to load billed slices for cost accountability")
        return None
    if not slices:
        return None
    return sum(slices.values())


def _build_cost_accountability(
    estimated_cost: Optional[float],
    params: dict[str, Any],
    db: Session,
) -> dict[str, Any]:
    """Build the always-present, additive ``costAccountability`` block.

    Shape matches the ``CostAccountabilityBlock`` schema (camelCase keys):
    ``estimatedCost`` is the Tier B total; ``billedCost`` is the scoped Tier A
    total (``None`` when CUR is unavailable); ``variance`` is billed - estimated
    when both are present; ``costBasis`` labels the figure; ``curAvailable`` /
    ``billedStale`` / ``coveredThrough`` come from the ingestion state. Degrades
    to estimated-only labeling when CUR is absent.
    """
    cur_available = _cur_ingestion_service.is_configured()

    billed_cost = _load_billed_total(params, db) if cur_available else None

    variance: Optional[float] = None
    if billed_cost is not None and estimated_cost is not None:
        variance = billed_cost - estimated_cost

    cost_basis = "billed" if billed_cost is not None else "estimated"

    billed_stale = False
    covered_through: Optional[str] = None
    if cur_available:
        try:
            state = _cur_ingestion_service.get_state(db)
            billed_stale = state.is_stale
            covered_through = (
                state.covered_through.isoformat() if state.covered_through else None
            )
        except Exception:
            logger.exception("Failed to read CUR ingestion state")

    return {
        "costBasis": cost_basis,
        "billedCost": billed_cost,
        "estimatedCost": estimated_cost,
        "variance": variance,
        "billedStale": billed_stale,
        "coveredThrough": covered_through,
        "curAvailable": cur_available,
    }


# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------


@router.get("")
def get_activity(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    user_identities: Optional[str] = Query(None),
    team_id: Optional[str] = Query(None),
    source_application: Optional[str] = Query(None),
    regions: Optional[str] = Query(None, description="Comma-separated regions to filter to"),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Aggregated activity data with filter query params."""
    try:
        params = _parse_filters(
            start_date, end_date, user_identities, team_id, source_application, regions
        )
        params = _scope_to_user(params, current_user)
        records, truncated, ingestion_error = _fetch_and_filter_with_meta(params, db)

        overview = _aggregation_service.compute_overview(records)
        by_user = _aggregation_service.aggregate_by_user(records)
        by_model = _aggregation_service.aggregate_by_model(records)
        by_date = _aggregation_service.aggregate_by_date(records)
        by_source = _aggregation_service.aggregate_by_source(records)

        return {
            "overview": overview,
            "byUser": by_user,
            "byModel": by_model,
            "byDate": by_date,
            "bySource": by_source,
            "recordCount": len(records),
            # Additive cost-governance block: always present, existing keys
            # untouched. Degrades to estimated-only labeling when CUR is absent.
            "costAccountability": _build_cost_accountability(
                overview.get("totalCostEstimate"), params, db
            ),
            # Additive flag: True when raw ingestion hit a record cap (some
            # history may be omitted). Existing fields are unchanged.
            "truncated": truncated,
            # Additive flag: True when every attempted ingestion source errored
            # (data unavailable, not genuinely empty). In DEMO_MODE synthetic
            # records still populate the tabs, but this flag still reflects the
            # real fetch failure so the condition stays diagnosable.
            "ingestionError": ingestion_error,
        }
    except CloudTrailError as exc:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=502, content=_error_response(exc))


@router.get("/overview")
def get_overview(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    user_identities: Optional[str] = Query(None),
    team_id: Optional[str] = Query(None),
    source_application: Optional[str] = Query(None),
    regions: Optional[str] = Query(None, description="Comma-separated regions to filter to"),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Top-level summary: total invocations, tokens, cost, active users, active models."""
    try:
        params = _parse_filters(
            start_date, end_date, user_identities, team_id, source_application, regions
        )
        params = _scope_to_user(params, current_user)
        records, truncated, ingestion_error = _fetch_and_filter_with_meta(params, db)
        overview = _aggregation_service.compute_overview(records)
        # Additive flags surfaced alongside the overview summary. compute_overview
        # returns a dict; add the fields without disturbing existing keys.
        overview["truncated"] = truncated
        overview["ingestionError"] = ingestion_error
        # Additive cost-governance block: always present, existing keys untouched.
        overview["costAccountability"] = _build_cost_accountability(
            overview.get("totalCostEstimate"), params, db
        )
        return overview
    except CloudTrailError as exc:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=502, content=_error_response(exc))


@router.get("/tokens")
def get_tokens(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    user_identities: Optional[str] = Query(None),
    team_id: Optional[str] = Query(None),
    source_application: Optional[str] = Query(None),
    regions: Optional[str] = Query(None, description="Comma-separated regions to filter to"),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Token usage summary and daily breakdown."""
    try:
        params = _parse_filters(
            start_date, end_date, user_identities, team_id, source_application, regions
        )
        params = _scope_to_user(params, current_user)
        records = _fetch_and_filter(params, db)

        by_user = _aggregation_service.aggregate_by_user(records)
        by_date = _aggregation_service.aggregate_by_date(records)
        overview = _aggregation_service.compute_overview(records)

        return {
            "summary": {
                "totalInputTokens": overview["totalInputTokens"],
                "totalOutputTokens": overview["totalOutputTokens"],
                "totalCacheReadTokens": overview["totalCacheReadTokens"],
                "totalCacheWriteTokens": overview["totalCacheWriteTokens"],
                "totalTokens": overview["totalTokens"],
                "totalCostEstimate": overview["totalCostEstimate"],
            },
            "byUser": by_user,
            "dailyBreakdown": by_date,
        }
    except CloudTrailError as exc:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=502, content=_error_response(exc))


@router.get("/models")
def get_models(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    user_identities: Optional[str] = Query(None),
    team_id: Optional[str] = Query(None),
    source_application: Optional[str] = Query(None),
    regions: Optional[str] = Query(None, description="Comma-separated regions to filter to"),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Model usage breakdown and user-to-model matrix."""
    try:
        params = _parse_filters(
            start_date, end_date, user_identities, team_id, source_application, regions
        )
        params = _scope_to_user(params, current_user)
        records = _fetch_and_filter(params, db)

        by_model = _aggregation_service.aggregate_by_model(records)
        matrix = _aggregation_service.compute_user_model_matrix(records)

        return {
            "models": by_model,
            "userModelMatrix": matrix,
        }
    except CloudTrailError as exc:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=502, content=_error_response(exc))


@router.get("/costs")
def get_costs(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    user_identities: Optional[str] = Query(None),
    team_id: Optional[str] = Query(None),
    source_application: Optional[str] = Query(None),
    regions: Optional[str] = Query(None, description="Comma-separated regions to filter to"),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Cost analysis with daily totals, per-user, per-model."""
    try:
        params = _parse_filters(
            start_date, end_date, user_identities, team_id, source_application, regions
        )
        params = _scope_to_user(params, current_user)
        records = _fetch_and_filter(params, db)

        by_date = _aggregation_service.aggregate_by_date(records)
        by_user = _aggregation_service.aggregate_by_user(records)
        by_model = _aggregation_service.aggregate_by_model(records)
        overview = _aggregation_service.compute_overview(records)

        # Compute per-user cost percentages
        total_cost = overview["totalCostEstimate"] or 0.0

        # Load the scoped billed slices once so the additive Tier A fields on the
        # total and per-model rows come from a single indexed lookup. When CUR is
        # unavailable this is an empty context and every row falls back to
        # estimated-only labeling. Athena is never queried on this read path.
        cur_available = _cur_ingestion_service.is_configured()
        billed_total: Optional[float] = None
        billed_by_model: dict[str, float] = {}
        if cur_available:
            try:
                slices = _reconciliation_service.load_billed_slices(
                    db,
                    params["start_date"],
                    params["end_date"],
                    _scoped_principals(params),
                )
            except Exception:
                logger.exception("Failed to load billed slices for /costs")
                slices = {}
            if slices:
                billed_total = sum(slices.values())
                agg: dict[str, float] = defaultdict(float)
                for key, value in slices.items():
                    agg[key.model] += value
                billed_by_model = dict(agg)

        per_user_costs = []
        for u in by_user:
            user_cost = u.get("costEstimate") or 0.0
            pct = (user_cost / total_cost * 100.0) if total_cost > 0 else 0.0
            per_user_costs.append({
                "userIdentity": u["userIdentity"],
                "userName": u["userName"],
                "costEstimate": u["costEstimate"],
                "percentageOfTotal": pct,
                # Per-user billed attribution is not reliable (CUR principals do
                # not map cleanly to activity user identities), so per-user rows
                # stay estimated-only labeled. Additive fields only.
                "billedCost": None,
                "reconciledCost": None,
                "variance": None,
                "costBasis": "estimated",
            })

        # Compute per-model cost percentages
        per_model_costs = []
        for m in by_model:
            model_cost = m.get("costEstimate") or 0.0
            pct = (model_cost / total_cost * 100.0) if total_cost > 0 else 0.0
            model_billed = billed_by_model.get(m["modelId"])
            model_variance = (
                model_billed - model_cost if model_billed is not None else None
            )
            per_model_costs.append({
                "modelId": m["modelId"],
                "costEstimate": m["costEstimate"],
                "percentageOfTotal": pct,
                # Additive Tier A fields: billed summed from cur_billed_cost by
                # model; reconciled total for the model equals its billed figure.
                "billedCost": model_billed,
                "reconciledCost": model_billed,
                "variance": model_variance,
                "costBasis": "billed" if model_billed is not None else "estimated",
            })

        # Daily costs
        daily_costs = [
            {"date": d["date"], "costEstimate": d.get("costEstimate") or 0.0}
            for d in by_date
        ]

        total_variance = (
            billed_total - total_cost if billed_total is not None else None
        )

        return {
            "totalCost": total_cost,
            "dailyCosts": daily_costs,
            "perUserCosts": per_user_costs,
            "perModelCosts": per_model_costs,
            # Additive Tier A totals: billed summed from cur_billed_cost for the
            # scoped window; the reconciled total sums to billed by construction.
            # Defaults to estimated-only labeling when CUR is absent.
            "billedCost": billed_total,
            "reconciledCost": billed_total,
            "variance": total_variance,
            "costBasis": "billed" if billed_total is not None else "estimated",
        }
    except CloudTrailError as exc:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=502, content=_error_response(exc))


@router.get("/users")
def get_users(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    user_identities: Optional[str] = Query(None),
    team_id: Optional[str] = Query(None),
    source_application: Optional[str] = Query(None),
    regions: Optional[str] = Query(None, description="Comma-separated regions to filter to"),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """List unique users with their activity stats."""
    try:
        params = _parse_filters(
            start_date, end_date, user_identities, team_id, source_application, regions
        )
        params = _scope_to_user(params, current_user)
        records = _fetch_and_filter(params, db)
        by_user = _aggregation_service.aggregate_by_user(records)
        return {"users": by_user}
    except CloudTrailError as exc:
        return JSONResponse(status_code=502, content=_error_response(exc))


@router.get("/sources")
def get_sources(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    user_identities: Optional[str] = Query(None),
    team_id: Optional[str] = Query(None),
    source_application: Optional[str] = Query(None),
    regions: Optional[str] = Query(None, description="Comma-separated regions to filter to"),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Source application breakdown."""
    try:
        params = _parse_filters(
            start_date, end_date, user_identities, team_id, source_application, regions
        )
        params = _scope_to_user(params, current_user)
        records = _fetch_and_filter(params, db)
        by_source = _aggregation_service.aggregate_by_source(records)
        return {"sources": by_source}
    except CloudTrailError as exc:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=502, content=_error_response(exc))


@router.get("/categories")
def get_categories(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    user_identities: Optional[str] = Query(None),
    team_id: Optional[str] = Query(None),
    source_application: Optional[str] = Query(None),
    regions: Optional[str] = Query(None, description="Comma-separated regions to filter to"),
    top_categories: int = Query(6, ge=1, le=50, description="Max categories shown before collapsing into Other"),
    top_models_per_category: int = Query(4, ge=1, le=20, description="Max models shown per category before collapsing into Other"),
    top_models: int = Query(6, ge=1, le=50, description="Max models shown in the overall model breakdown before collapsing into Other"),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Spend breakdown for the Overview tab's category/model donut charts.

    Each record resolves to exactly one category (content-type keyword match
    first, then its resolved Application tag, then "Uncategorized" — see
    ``prompt_category_service.resolve_category``) and contributes to both the
    nested ``byCategory[].byModel`` breakdown and the independent top-level
    ``byModel`` totals. Cost basis is always "estimated" (Tier B), matching
    every other figure on the Overview tab.
    """
    try:
        params = _parse_filters(
            start_date, end_date, user_identities, team_id, source_application, regions
        )
        params = _scope_to_user(params, current_user)
        records = _fetch_and_filter(params, db)
        return build_spend_breakdown(
            records,
            top_categories=top_categories,
            top_models_per_category=top_models_per_category,
            top_models=top_models,
        )
    except CloudTrailError as exc:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=502, content=_error_response(exc))


@router.get("/export")
def export_csv(
    section: str = Query(..., description="Section to export"),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    user_identities: Optional[str] = Query(None),
    team_id: Optional[str] = Query(None),
    source_application: Optional[str] = Query(None),
    regions: Optional[str] = Query(None, description="Comma-separated regions to filter to"),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Export filtered data as CSV for a given section."""
    if section not in _VALID_SECTIONS:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid section: {section}. Must be one of {sorted(_VALID_SECTIONS)}.",
        )

    try:
        params = _parse_filters(
            start_date, end_date, user_identities, team_id, source_application, regions
        )
        params = _scope_to_user(params, current_user)
        records = _fetch_and_filter(params, db)

        # Aggregate records based on section
        if section == "token-usage":
            export_records = _aggregation_service.aggregate_by_user(records)
        elif section == "model-usage":
            export_records = _aggregation_service.aggregate_by_model(records)
        elif section == "cost-analysis":
            # Combine per-user and per-model cost rows
            by_user = _aggregation_service.aggregate_by_user(records)
            by_model = _aggregation_service.aggregate_by_model(records)
            overview = _aggregation_service.compute_overview(records)
            total_cost = overview["totalCostEstimate"] or 0.0

            export_records = []
            for u in by_user:
                user_cost = u.get("costEstimate") or 0.0
                pct = (user_cost / total_cost * 100.0) if total_cost > 0 else 0.0
                export_records.append({
                    "userName": u.get("userName", ""),
                    "modelId": "",
                    "costEstimate": u.get("costEstimate"),
                    "percentageOfTotal": pct,
                })
            for m in by_model:
                model_cost = m.get("costEstimate") or 0.0
                pct = (model_cost / total_cost * 100.0) if total_cost > 0 else 0.0
                export_records.append({
                    "userName": "",
                    "modelId": m.get("modelId", ""),
                    "costEstimate": m.get("costEstimate"),
                    "percentageOfTotal": pct,
                })
        else:
            # prompt-history: sort by (timestamp, id) descending for a stable,
            # deterministic order matching the paginated prompt history.
            records.sort(key=lambda r: (r.get("timestamp", ""), r.get("id", "")), reverse=True)
            export_records = records

        csv_content = _export_service.generate_csv(section, export_records, params)

        return StreamingResponse(
            iter([csv_content]),
            media_type="text/csv",
            headers={
                "Content-Disposition": f'attachment; filename="{section}-export.csv"',
            },
        )
    except CloudTrailError as exc:
        return JSONResponse(status_code=502, content=_error_response(exc))
