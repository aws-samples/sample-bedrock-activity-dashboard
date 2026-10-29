"""CUR 2.0 authoritative billed-cost ingestion via Athena (off-request).

This service runs the authoritative (Tier A) billed-cost ingestion. It queries
the CUR 2.0 export through AWS Athena (serverless SQL over the Glue table that
overlays the CUR Parquet), so the heavy aggregation runs inside Athena and the
Fargate task never loads raw line items. The coarse rollup the reconciliation
layer needs is expressed as a single SQL ``GROUP BY`` and the aggregated rows are
read back via ``GetQueryResults`` and upserted idempotently into
``cur_billed_cost``.

Design: this service implements the "CUR 2.0 Ingestion Path" decision and the
``CurIngestionService`` component from the cost-accountability-governance design.

The service is a passive, out-of-band observer: it only *consumes* the CUR export
and its Glue/Athena wiring, never configures them. When the Athena prerequisites
are unset it degrades gracefully (``is_configured()`` is False and ``ingest`` is a
no-op) so the rest of the app keeps serving estimated-only figures.

Athena is queried **only** here, off the request path. Interactive analytics and
reconciliation reads always come from the stored ``cur_billed_cost`` table.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.config import Settings, settings
from app.models import AttributionTargetConfig, CurBilledCost, CurIngestionState
from app.services.cost_service import _normalize_model_id
from app.services.profile_tag_service import ProfileTagService

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tuning constants for the Athena query lifecycle.
# ---------------------------------------------------------------------------
# Bounded overall wait for a single query to reach a terminal state, so a stuck
# query can never hang the (scheduled/background) ingestion run indefinitely.
_POLL_TIMEOUT_SECONDS = 300
# Incremental backoff between GetQueryExecution polls: start small, grow, cap.
_POLL_INITIAL_INTERVAL = 1.0
_POLL_MAX_INTERVAL = 10.0
_POLL_BACKOFF_FACTOR = 1.5
# Bounded retry for throttling on the Athena API calls.
_THROTTLE_MAX_RETRIES = 5
_THROTTLE_INITIAL_BACKOFF = 1.0
_THROTTLE_MAX_BACKOFF = 20.0
# Botocore throttling error codes we retry.
_THROTTLE_ERROR_CODES = frozenset(
    {"TooManyRequestsException", "ThrottlingException", "Throttling"}
)
# Truncate the diagnostic stored in cur_ingestion_state.error_detail so a giant
# AWS error body never bloats the row (full context is logged at ERROR instead).
_ERROR_DETAIL_MAX_CHARS = 2000
# Terminal Athena query states.
_TERMINAL_STATES = frozenset({"SUCCEEDED", "FAILED", "CANCELLED"})

# SQL identifier whitelist for Athena/Glue names interpolated into _build_query.
# Athena/Presto accept dotted names and quoted identifiers, but every value we
# interpolate is produced by either (a) an ECS task env var set at deploy time
# (``settings.athena_database`` / ``athena_table``) or (b) a Glue
# ``get_table`` response (column names). This regex is defense-in-depth: it
# rejects anything that is not a plain SQL identifier before interpolation so
# a mis-set env var or a crafted Glue response cannot break out of the
# intended identifier position in the query string.
_SQL_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")


def _validate_sql_identifier(value: str, kind: str) -> str:
    """Return ``value`` unchanged if it is a safe SQL identifier; raise otherwise.

    Protects :meth:`_build_query` against identifier-position injection via
    deployment config or Glue schema metadata. Not a substitute for parameter
    binding on value positions (we do not interpolate any user-supplied values
    into this query — only identifiers).
    """
    if not isinstance(value, str) or not _SQL_IDENTIFIER_RE.match(value):
        raise CurQueryError(
            f"Refusing to interpolate unsafe {kind} identifier into Athena query: "
            f"{value!r} does not match {_SQL_IDENTIFIER_RE.pattern}"
        )
    return value


@dataclass
class IngestionResult:
    """Outcome of a single :meth:`CurIngestionService.ingest` run."""

    status: str  # "success" | "failed" | "skipped"
    rows_upserted: int = 0
    covered_through: Optional[date] = None
    latest_billing_period: Optional[str] = None
    query_execution_id: Optional[str] = None
    error_detail: Optional[str] = None


@dataclass
class IngestionState:
    """Read-model of the persisted ``cur_ingestion_state`` plus derived staleness."""

    last_run_at: Optional[datetime]
    covered_through: Optional[date]
    latest_billing_period: Optional[str]
    status: Optional[str]
    error_detail: Optional[str]
    is_stale: bool


class CurQueryError(Exception):
    """Raised internally when the Athena query lifecycle fails.

    Never propagates out of :meth:`CurIngestionService.ingest` — it is caught and
    recorded on ``cur_ingestion_state`` so previously-ingested rows stay intact and
    no stack trace leaks to any caller.
    """


# ---------------------------------------------------------------------------
# Pure, testable helpers (kept module-level so property tests can target them
# without constructing a service or an Athena client).
# ---------------------------------------------------------------------------


def is_stale(
    covered_through: Optional[date],
    now: datetime,
    staleness_hours: int,
) -> bool:
    """Return True when billed data is stale: ``now - covered_through`` exceeds
    the window.

    Pure function (Property 13). ``covered_through`` is a day-granularity date;
    it is compared at the start of that day in UTC. When no data has ever been
    ingested (``covered_through is None``) the data is considered stale.
    """
    if covered_through is None:
        return True
    now_utc = now.astimezone(timezone.utc) if now.tzinfo else now.replace(tzinfo=timezone.utc)
    covered_dt = datetime(
        covered_through.year,
        covered_through.month,
        covered_through.day,
        tzinfo=timezone.utc,
    )
    return (now_utc - covered_dt) > timedelta(hours=staleness_hours)


# Matches an application/base inference-profile ARN, e.g.
# ``arn:aws:bedrock:us-west-2:123456789012:application-inference-profile/abc`` or
# ``arn:aws:bedrock:us-west-2:123456789012:inference-profile/xyz``.
_INFERENCE_PROFILE_ARN_RE = re.compile(
    r"^arn:aws:bedrock:[^:]*:[^:]*:(?:application-)?inference-profile/.+$"
)


def _is_inference_profile_arn(value: Optional[str]) -> bool:
    """Return True when ``value`` is a non-empty inference-profile ARN.

    Used to decide which CUR ``line_item_resource_id`` values carry profile tags
    worth caching (base-model resource ids are ignored).
    """
    if not value:
        return False
    return bool(_INFERENCE_PROFILE_ARN_RE.match(value.strip()))


def normalize_model_id(model_id: Optional[str]) -> str:
    """Normalize a CUR model reference to the bare model key.

    Reuses the same ARN-tail / cross-region-prefix stripping the rest of the
    codebase applies at ingestion (``cost_service._normalize_model_id``), so CUR
    model keys line up with activity-record ``modelId`` keys. Empty / missing
    values normalize to ``""``.
    """
    if not model_id:
        return ""
    return _normalize_model_id(model_id.strip())


def _billing_period_from_date(usage_date: date) -> str:
    """Derive the ``YYYY-MM`` billing period from a usage date."""
    return f"{usage_date.year:04d}-{usage_date.month:02d}"


def _parse_usage_date(raw: str) -> date:
    """Parse a CUR ``line_item_usage_start_date`` cell into a day-granularity date.

    Athena renders timestamps like ``2024-05-31 00:00:00.000`` or ISO-8601
    ``2024-05-31T00:00:00Z``; a bare ``2024-05-31`` is also accepted. Only the
    date component is retained (day granularity).
    """
    text = raw.strip()
    if not text:
        raise ValueError("empty usage_date")
    # Keep only the date component regardless of the time separator used.
    head = text.replace("T", " ").split(" ", 1)[0]
    return date.fromisoformat(head)


def _parse_cost(raw: str) -> float:
    """Parse a billed-cost cell into a float, tolerating empty cells as 0.0."""
    text = (raw or "").strip()
    if not text:
        return 0.0
    return float(text)


def parse_result_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Parse raw ``GetQueryResults`` rows into normalized billed-cost dicts.

    ``rows`` is the concatenation of every page's ``ResultSet.Rows`` **including**
    the header row, which is skipped here (Athena always echoes the column names
    as the first row of the first page). Column order matches the aggregation SQL
    in :meth:`CurIngestionService._build_query`:

        0 iam_principal, 1 team_tag, 2 resource_id, 3 usage_date,
        4 model_id, 5 application, 6 environment, 7 billed_cost

    Columns 5/6 carry the CUR activated resource tags for the inference profile
    (``resource_tags_user_application`` / ``resource_tags_user_environment``); the
    Team tag arrives at column 1. These are threaded through so the ingestion run
    can populate the profile-tag cache without a live Bedrock call.

    Model IDs are normalized so duplicate-normalizing keys can be collapsed by the
    upsert. Rows whose usage date cannot be parsed are skipped (logged WARNING)
    rather than aborting the whole run.
    """
    parsed: list[dict[str, Any]] = []
    for idx, row in enumerate(rows):
        if idx == 0:
            # Header row echoing the column names.
            continue
        # Athena renders a null cell as an empty Datum ({}); default to "" so the
        # column layout stays aligned regardless of nulls.
        cells = [(col or {}).get("VarCharValue") for col in row.get("Data", [])]
        if len(cells) < 8:
            logger.warning("Skipping malformed CUR result row %d: %r", idx, cells)
            continue
        iam_principal = (cells[0] or "").strip()
        team_tag = (cells[1] or "").strip()
        resource_id = (cells[2] or "").strip() or None
        try:
            usage_date = _parse_usage_date(cells[3] or "")
            billed_cost = _parse_cost(cells[7] or "")
        except (ValueError, TypeError) as exc:
            logger.warning("Skipping CUR result row %d (parse error): %s", idx, exc)
            continue
        application = (cells[5] or "").strip() or None
        environment = (cells[6] or "").strip() or None
        parsed.append(
            {
                "billing_period": _billing_period_from_date(usage_date),
                "usage_date": usage_date,
                "iam_principal": iam_principal,
                "team_tag": team_tag,
                "resource_id": resource_id,
                "model_id": normalize_model_id(cells[4]),
                "application": application,
                "environment": environment,
                "billed_cost": billed_cost,
            }
        )
    return parsed


class CurIngestionService:
    """Off-request CUR ingestion: Athena query -> poll -> fetch -> upsert.

    The Athena client is created **lazily** (see :attr:`_athena`) so importing this
    module — and any router that instantiates the service at import time — never
    fails when credentials or the Athena service model are unavailable; only the
    first real query touches boto3.
    """

    def __init__(self, settings: Settings = settings) -> None:
        self._settings = settings
        # Created lazily on first use (mirrors the agentcore_invoker pattern).
        self.__athena: Any = None
        # Glue column probe result, cached per service instance (one per run).
        # False = not probed yet; None = probe failed; set = column names.
        self.__table_columns: Any = False

    @property
    def _athena(self) -> Any:
        """Lazily created ``athena`` client scoped to the configured region."""
        if self.__athena is None:
            import boto3

            region = self._settings.athena_region or self._settings.aws_region
            self.__athena = boto3.client("athena", region_name=region)
        return self.__athena

    # ------------------------------------------------------------------
    # Configuration gate
    # ------------------------------------------------------------------
    def is_configured(self) -> bool:
        """True only when ``athena_database``, ``athena_table`` and
        ``athena_output_s3`` are all set.

        Drives graceful degradation: when any is missing the app serves
        estimated-only figures and :meth:`ingest` is a no-op.
        """
        return bool(
            self._settings.athena_database
            and self._settings.athena_table
            and self._settings.athena_output_s3
        )

    # ------------------------------------------------------------------
    # Query construction
    # ------------------------------------------------------------------
    # CUR activated-tag columns only materialize in the export once the
    # corresponding cost-allocation tags are activated in the payer account.
    # Until then the Glue table simply lacks them, so each is probed and, when
    # absent, substituted with an empty-string literal to keep the output
    # column ORDER unchanged (parse_result_rows depends on positions).
    _TAG_COLUMNS: tuple[tuple[str, str], ...] = (
        ("resource_tags_user_team", "team_tag"),
        ("resource_tags_user_application", "application"),
        ("resource_tags_user_environment", "environment"),
    )

    def _table_columns(self) -> Optional[set[str]]:
        """Probe the Glue table's column names (cached per service instance).

        Returns the set of column names (data + partition keys), or ``None``
        when the probe fails. A run instantiates one service, so the probe
        happens at most once per ingestion run.
        """
        if self.__table_columns is not False:
            return self.__table_columns
        try:
            import boto3

            region = self._settings.athena_region or self._settings.aws_region
            glue = boto3.client("glue", region_name=region)
            response = glue.get_table(
                DatabaseName=self._settings.athena_database,
                Name=self._settings.athena_table,
            )
            table = response.get("Table", {})
            columns = {
                col["Name"]
                for col in table.get("StorageDescriptor", {}).get("Columns", [])
            }
            columns |= {col["Name"] for col in table.get("PartitionKeys", [])}
            self.__table_columns = columns
        except Exception as exc:  # noqa: BLE001 - degrade to tag-less projection
            logger.warning(
                "Glue table column probe failed (%s); assuming activated tag "
                "columns are absent",
                exc,
            )
            self.__table_columns = None
        return self.__table_columns

    def _build_query(self) -> str:
        """Build the coarse aggregation SQL run by Athena over the Glue table.

        Groups Bedrock line items by principal, activated Team tag,
        resource_id/profile ARN and usage day, summing the unblended cost.
        The model is derived from ``line_item_resource_id``: for Bedrock line
        items it carries the foundation-model or inference-profile ARN, whose
        tail :func:`normalize_model_id` strips (at parse time, column 4) to the
        bare model key that matches activity-record ``modelId`` keys. The
        resource id is therefore projected twice — position 2 stays the RAW
        resource_id (profile-tag caching), position 4 becomes ``model_id``
        (positions matter: ``parse_result_rows`` is positional).
        The CUR activated resource tags (``resource_tags_user_team`` /
        ``resource_tags_user_application`` / ``resource_tags_user_environment``)
        are projected **only when present** in the Glue table — cost-allocation
        tags may not be activated yet, in which case an empty-string literal is
        substituted so the output column order (and ``parse_result_rows``) stays
        unchanged. Identifiers are quoted with the configured database/table
        names.
        """
        # All three identifier positions interpolated below (``database``,
        # ``table``, projection column names) are validated against
        # ``_SQL_IDENTIFIER_RE`` before being spliced into the query string.
        # Values originate from ECS task env (``athena_database`` /
        # ``athena_table``) or Glue ``get_table`` metadata; neither is
        # end-user input, but the validator defends against misconfigured env
        # vars and crafted Glue schemas.
        database = _validate_sql_identifier(self._settings.athena_database, "athena_database")
        table = _validate_sql_identifier(self._settings.athena_table, "athena_table")

        columns = self._table_columns()

        def tag_projection(source_col: str, alias: str) -> tuple[str, Optional[str]]:
            """Return (SELECT expression, GROUP BY expression or None)."""
            if columns is not None and source_col in columns:
                safe_col = _validate_sql_identifier(source_col, "tag column")
                safe_alias = _validate_sql_identifier(alias, "tag alias")
                return f"{safe_col} AS {safe_alias}", safe_col
            return f"'' AS {_validate_sql_identifier(alias, 'tag alias')}", None

        team_expr, team_group = tag_projection(*self._TAG_COLUMNS[0])
        app_expr, app_group = tag_projection(*self._TAG_COLUMNS[1])
        env_expr, env_group = tag_projection(*self._TAG_COLUMNS[2])

        # Group by the real (non-literal) source expressions only; constant
        # literals need not (and must not) appear in the GROUP BY.
        # model_id derives from line_item_resource_id (already grouped), so no
        # extra GROUP BY term is needed; rows differing only by line-item type
        # (Usage/Tax/...) merge here and the SUM absorbs them.
        group_by = [
            "line_item_iam_principal",
            "line_item_resource_id",
            "line_item_usage_start_date",
        ]
        group_by.extend(g for g in (team_group, app_group, env_group) if g)

        # All interpolated identifiers (database, table, column projections,
        # group-by terms) are gated through ``_validate_sql_identifier`` above
        # or in ``tag_projection``; no user-controlled value is ever spliced
        # into this query.
        # SQL is built only from validated identifiers (see _validate_sql_identifier
        # above and in tag_projection); Athena cannot bind identifiers as query
        # parameters, so allowlist validation is the correct control.
        return (
            "SELECT line_item_iam_principal            AS iam_principal,\n"  # nosec B608
            f"       {team_expr},\n"
            "       line_item_resource_id              AS resource_id,\n"
            "       line_item_usage_start_date         AS usage_date,\n"
            "       line_item_resource_id              AS model_id,\n"
            f"       {app_expr},\n"
            f"       {env_expr},\n"
            "       SUM(line_item_unblended_cost)      AS billed_cost\n"
            f'FROM "{database}"."{table}"\n'
            "WHERE line_item_product_code = 'AmazonBedrock'\n"
            f"GROUP BY {', '.join(group_by)}"
        )

    # ------------------------------------------------------------------
    # Full ingestion run
    # ------------------------------------------------------------------
    def ingest(self, db: Session) -> IngestionResult:
        """Run a full ingestion: start query -> poll -> fetch -> upsert -> record state.

        Idempotent and off-request only. On any failure the run is recorded as
        ``failed`` on ``cur_ingestion_state`` (with a truncated diagnostic),
        previously-ingested rows are left intact, and no stack trace escapes.
        Returns an :meth:`IngestionResult` describing the outcome.
        """
        if not self._settings.cur_ingest_enabled:
            logger.info("CUR ingestion skipped: disabled via CUR_INGEST_ENABLED")
            return IngestionResult(status="skipped", error_detail="ingestion disabled")

        if not self.is_configured():
            logger.info(
                "CUR ingestion skipped: Athena not configured "
                "(athena_database/athena_table/athena_output_s3 unset)"
            )
            return IngestionResult(status="skipped", error_detail="athena not configured")

        self._mark_running(db)
        query_execution_id: Optional[str] = None
        try:
            query_execution_id = self._start_query()
            self._poll_until_terminal(query_execution_id)
            rows = self._fetch_all_rows(query_execution_id)
            parsed = parse_result_rows(rows)
            rows_upserted = self._upsert_rows(db, parsed)
            # Best-effort: after the billed-cost upsert succeeds, populate the
            # profile-tag cache from the CUR resource tags. Never fails the run.
            self._refresh_profile_tags(db, parsed)
            covered_through, latest_period = self._coverage(parsed)
            self._record_success(
                db,
                covered_through=covered_through,
                latest_billing_period=latest_period,
            )
            logger.info(
                "CUR ingestion succeeded: query=%s rows=%d covered_through=%s period=%s",
                query_execution_id,
                rows_upserted,
                covered_through,
                latest_period,
            )
            return IngestionResult(
                status="success",
                rows_upserted=rows_upserted,
                covered_through=covered_through,
                latest_billing_period=latest_period,
                query_execution_id=query_execution_id,
            )
        except CurQueryError as exc:
            detail = self._record_failure(db, exc, query_execution_id)
            return IngestionResult(
                status="failed",
                query_execution_id=query_execution_id,
                error_detail=detail,
            )
        except Exception as exc:  # noqa: BLE001 - defensive: never leak from ingest
            detail = self._record_failure(db, exc, query_execution_id)
            return IngestionResult(
                status="failed",
                query_execution_id=query_execution_id,
                error_detail=detail,
            )

    # ------------------------------------------------------------------
    # Athena lifecycle steps
    # ------------------------------------------------------------------
    def _start_query(self) -> str:
        """StartQueryExecution with the aggregation SQL; return the execution id."""
        query = self._build_query()

        def _call() -> dict[str, Any]:
            return self._athena.start_query_execution(
                QueryString=query,
                QueryExecutionContext={"Database": self._settings.athena_database},
                WorkGroup=self._settings.athena_workgroup,
                ResultConfiguration={"OutputLocation": self._settings.athena_output_s3},
            )

        response = self._call_with_throttle_retry(_call, "StartQueryExecution")
        query_execution_id = response.get("QueryExecutionId")
        if not query_execution_id:
            raise CurQueryError("StartQueryExecution returned no QueryExecutionId")
        logger.info("Started Athena query execution %s", query_execution_id)
        return query_execution_id

    def _poll_until_terminal(self, query_execution_id: str) -> None:
        """Poll GetQueryExecution until SUCCEEDED/FAILED/CANCELLED or timeout.

        Uses a bounded overall timeout with incremental backoff between polls. On
        FAILED/CANCELLED surfaces the Athena ``StateChangeReason``; on timeout
        calls ``StopQueryExecution`` and aborts.
        """
        deadline = time.monotonic() + _POLL_TIMEOUT_SECONDS
        interval = _POLL_INITIAL_INTERVAL

        while True:

            def _call() -> dict[str, Any]:
                return self._athena.get_query_execution(QueryExecutionId=query_execution_id)

            response = self._call_with_throttle_retry(_call, "GetQueryExecution")
            execution = response.get("QueryExecution", {})
            status = execution.get("Status", {})
            state = status.get("State", "")

            if state == "SUCCEEDED":
                return
            if state in _TERMINAL_STATES:  # FAILED or CANCELLED
                reason = status.get("StateChangeReason", "no reason provided")
                raise CurQueryError(f"Athena query {state}: {reason}")

            if time.monotonic() >= deadline:
                self._stop_query(query_execution_id)
                raise CurQueryError(
                    f"Athena query poll timed out after {_POLL_TIMEOUT_SECONDS}s "
                    f"(last state {state or 'UNKNOWN'})"
                )

            time.sleep(interval)
            interval = min(interval * _POLL_BACKOFF_FACTOR, _POLL_MAX_INTERVAL)

    def _stop_query(self, query_execution_id: str) -> None:
        """Best-effort StopQueryExecution (used on poll timeout)."""
        try:
            self._athena.stop_query_execution(QueryExecutionId=query_execution_id)
            logger.warning("Stopped Athena query execution %s after timeout", query_execution_id)
        except Exception as exc:  # noqa: BLE001 - best effort, do not mask the timeout
            logger.error(
                "Failed to stop Athena query execution %s: %s",
                query_execution_id,
                exc,
            )

    def _fetch_all_rows(self, query_execution_id: str) -> list[dict[str, Any]]:
        """Read every result page via GetQueryResults, following ``NextToken``."""
        rows: list[dict[str, Any]] = []
        next_token: Optional[str] = None
        page = 0
        while True:

            def _call() -> dict[str, Any]:
                kwargs: dict[str, Any] = {"QueryExecutionId": query_execution_id}
                if next_token:
                    kwargs["NextToken"] = next_token
                return self._athena.get_query_results(**kwargs)

            try:
                response = self._call_with_throttle_retry(_call, "GetQueryResults")
            except CurQueryError:
                raise
            except Exception as exc:  # noqa: BLE001 - wrap pagination errors uniformly
                raise CurQueryError(
                    f"GetQueryResults failed on page {page}: {exc}"
                ) from exc

            # Athena echoes the header row only on the first page, so the
            # concatenated list carries exactly one header (skipped in
            # parse_result_rows); subsequent pages are pure data rows.
            page_rows = response.get("ResultSet", {}).get("Rows", [])
            rows.extend(page_rows)
            next_token = response.get("NextToken")
            page += 1
            if not next_token:
                break
        return rows

    def _call_with_throttle_retry(self, call, op_name: str) -> dict[str, Any]:
        """Invoke a boto3 Athena call with bounded retry on throttling errors."""
        from botocore.exceptions import ClientError

        backoff = _THROTTLE_INITIAL_BACKOFF
        attempt = 0
        while True:
            try:
                return call()
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code", "")
                if code in _THROTTLE_ERROR_CODES and attempt < _THROTTLE_MAX_RETRIES:
                    attempt += 1
                    logger.warning(
                        "Athena %s throttled (%s), retry %d/%d after %.1fs",
                        op_name,
                        code,
                        attempt,
                        _THROTTLE_MAX_RETRIES,
                        backoff,
                    )
                    time.sleep(backoff)
                    backoff = min(backoff * _POLL_BACKOFF_FACTOR, _THROTTLE_MAX_BACKOFF)
                    continue
                message = exc.response.get("Error", {}).get("Message", "")
                raise CurQueryError(
                    f"Athena {op_name} failed ({code or 'ClientError'}): {message}"
                ) from exc

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def _upsert_rows(self, db: Session, parsed: list[dict[str, Any]]) -> int:
        """Idempotently upsert parsed rows into ``cur_billed_cost``.

        Duplicate-normalizing keys (rows that collapse to the same
        ``uq_cur_billed_key``) are summed in-memory first, then a Postgres
        ``ON CONFLICT DO UPDATE`` replaces the stored ``billed_cost`` (and bumps
        ``updated_at``). Re-ingesting a period therefore yields the same rows as
        ingesting it once (Req 1.4).
        """
        if not parsed:
            return 0

        # Collapse duplicate keys in the incoming batch so a single upsert per key
        # is issued (an ON CONFLICT statement cannot target the same key twice in
        # one INSERT).
        collapsed: dict[tuple, dict[str, Any]] = {}
        for row in parsed:
            key = (
                row["billing_period"],
                row["usage_date"],
                row["iam_principal"],
                row["team_tag"],
                row["model_id"],
            )
            if key in collapsed:
                collapsed[key]["billed_cost"] += row["billed_cost"]
                # Prefer a non-null resource_id if this duplicate carries one.
                if collapsed[key].get("resource_id") is None and row.get("resource_id"):
                    collapsed[key]["resource_id"] = row["resource_id"]
            else:
                collapsed[key] = dict(row)

        now = datetime.now(timezone.utc)
        for row in collapsed.values():
            stmt = pg_insert(CurBilledCost.__table__).values(
                billing_period=row["billing_period"],
                usage_date=row["usage_date"],
                iam_principal=row["iam_principal"],
                team_tag=row["team_tag"],
                resource_id=row["resource_id"],
                model_id=row["model_id"],
                billed_cost=row["billed_cost"],
            )
            stmt = stmt.on_conflict_do_update(
                constraint="uq_cur_billed_key",
                set_={
                    "billed_cost": stmt.excluded.billed_cost,
                    "resource_id": stmt.excluded.resource_id,
                    "updated_at": now,
                },
            )
            db.execute(stmt)
        db.commit()
        return len(collapsed)

    def _refresh_profile_tags(self, db: Session, parsed: list[dict[str, Any]]) -> None:
        """Populate the profile-tag cache from CUR resource tags (best-effort).

        Collects the DISTINCT ``(resource_id, application, environment, team)``
        tuples whose ``resource_id`` is a non-empty inference-profile ARN and hands
        them to :meth:`ProfileTagService.refresh_from_cur`, so tags carried by the
        CUR export populate ``inference_profile_tag`` without a live Bedrock call.

        This is best-effort and runs after the billed-cost upsert has succeeded:
        any error is logged at WARNING and swallowed so a tag-refresh failure never
        fails the ingestion run.
        """
        try:
            seen: set[tuple] = set()
            tag_rows: list[tuple] = []
            for row in parsed:
                resource_id = row.get("resource_id")
                if not _is_inference_profile_arn(resource_id):
                    continue
                tup = (
                    resource_id,
                    row.get("application"),
                    row.get("environment"),
                    row.get("team_tag") or None,
                )
                if tup in seen:
                    continue
                seen.add(tup)
                tag_rows.append(tup)
            if tag_rows:
                ProfileTagService().refresh_from_cur(db, tag_rows)
        except Exception as exc:  # noqa: BLE001 - never fail ingestion on tag refresh
            logger.warning("Failed to refresh profile tags from CUR rows: %s", exc)

    @staticmethod
    def _coverage(parsed: list[dict[str, Any]]) -> tuple[Optional[date], Optional[str]]:
        """Return ``(max usage_date, latest billing period)`` for the parsed rows."""
        if not parsed:
            return None, None
        covered_through = max(row["usage_date"] for row in parsed)
        latest_period = max(row["billing_period"] for row in parsed)
        return covered_through, latest_period

    # ------------------------------------------------------------------
    # Ingestion-state tracking
    # ------------------------------------------------------------------
    def _get_state_row(self, db: Session) -> Optional[CurIngestionState]:
        """Return the singleton ``cur_ingestion_state`` row, or None."""
        return db.execute(select(CurIngestionState).limit(1)).scalar_one_or_none()

    def _mark_running(self, db: Session) -> None:
        """Mark the ingestion run as ``running`` (fetch-or-create the singleton)."""
        row = self._get_state_row(db)
        if row is None:
            row = CurIngestionState(status="running")
            db.add(row)
        else:
            row.status = "running"
        db.commit()

    def _record_success(
        self,
        db: Session,
        covered_through: Optional[date],
        latest_billing_period: Optional[str],
    ) -> None:
        """Persist a successful run's coverage/state on ``cur_ingestion_state``."""
        row = self._get_state_row(db)
        if row is None:
            row = CurIngestionState(status="success")
            db.add(row)
        row.last_run_at = datetime.now(timezone.utc)
        row.status = "success"
        row.error_detail = None
        # Advance coverage monotonically; never regress on an empty/older run.
        if covered_through is not None and (
            row.covered_through is None or covered_through > row.covered_through
        ):
            row.covered_through = covered_through
        if latest_billing_period is not None and (
            row.latest_billing_period is None
            or latest_billing_period > row.latest_billing_period
        ):
            row.latest_billing_period = latest_billing_period
            row.assembly_id = latest_billing_period
        db.commit()

    def _record_failure(
        self,
        db: Session,
        exc: Exception,
        query_execution_id: Optional[str],
    ) -> str:
        """Record a failed run without disturbing previously-ingested rows.

        Logs full context at ERROR and stores a truncated diagnostic on
        ``cur_ingestion_state.error_detail``. Never re-raises.
        """
        detail = str(exc)[:_ERROR_DETAIL_MAX_CHARS]
        logger.error(
            "CUR ingestion failed: query=%s error=%s",
            query_execution_id,
            exc,
        )
        try:
            row = self._get_state_row(db)
            if row is None:
                row = CurIngestionState(status="failed")
                db.add(row)
            row.last_run_at = datetime.now(timezone.utc)
            row.status = "failed"
            row.error_detail = detail
            db.commit()
        except Exception as state_exc:  # noqa: BLE001 - state write must not raise
            db.rollback()
            logger.error("Failed to record CUR ingestion failure state: %s", state_exc)
        return detail

    # ------------------------------------------------------------------
    # State read-model + staleness
    # ------------------------------------------------------------------
    def _staleness_hours(self, db: Session) -> int:
        """Effective staleness window, honoring an admin per-env override."""
        override = db.execute(
            select(AttributionTargetConfig.staleness_hours_override).limit(1)
        ).scalar_one_or_none()
        if override is not None:
            return override
        return self._settings.cur_staleness_hours

    def get_state(self, db: Session) -> IngestionState:
        """Return the current ingestion state plus a derived ``is_stale`` flag."""
        row = self._get_state_row(db)
        staleness_hours = self._staleness_hours(db)
        now = datetime.now(timezone.utc)
        if row is None:
            return IngestionState(
                last_run_at=None,
                covered_through=None,
                latest_billing_period=None,
                status=None,
                error_detail=None,
                is_stale=is_stale(None, now, staleness_hours),
            )
        return IngestionState(
            last_run_at=row.last_run_at,
            covered_through=row.covered_through,
            latest_billing_period=row.latest_billing_period,
            status=row.status,
            error_detail=row.error_detail,
            is_stale=is_stale(row.covered_through, now, staleness_hours),
        )


def _main() -> int:
    """Module entrypoint for the scheduled path (``python -m ...``).

    Opens a DB session and runs a single ingestion. Returns a process exit code
    (0 on success/skip, 1 on failure) so a scheduled ECS task can signal outcome.
    """
    logging.basicConfig(level=logging.INFO)
    from app.database import SessionLocal

    service = CurIngestionService()
    db = SessionLocal()
    try:
        result = service.ingest(db)
        logger.info("CUR ingestion run finished: status=%s", result.status)
        return 0 if result.status in ("success", "skipped") else 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(_main())
