"""Cost Governance API routes (``/api/governance/*``).

Layers the four cost-governance capabilities onto the existing dashboard as a
read-mostly router: per-slice reconciliation, prompt-cache efficiency,
cost-per-outcome, and the unattributed-spend canary, plus CUR ingestion status,
an admin ingest trigger, and the two admin-owned config resources.

Handlers stay thin: they source + scope activity records through the shared
``source_activity_records_with_meta`` pipeline (via the activity route helpers
``_parse_filters`` / ``_scope_to_user`` / ``_fetch_and_filter``) and delegate all
math to the governance services (``ReconciliationService``, ``EfficiencyService``,
``CanaryService``, ``CurIngestionService``, ``ProfileTagService``).

Role scoping (Req 10): every read handler runs ``_scope_to_user`` so a non-admin
caller only ever sees figures computed over their own identity; billed slices are
filtered to the caller's matching ``iam_principal``. Config mutations and the CUR
ingest trigger require ``require_admin``. There is no second auth path — the
existing Cognito ``get_current_user`` dependency is reused (Req 10.5).
"""

import logging
import uuid
from collections import defaultdict
from datetime import date
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.auth import CurrentUser, get_current_user, require_admin
from app.database import get_db
from app.models import (
    AttributionTargetConfig,
    Team,
    WorkloadOutcomeOverride,
    utcnow,
)
from app.routes.activity import (
    _error_response,
    _fetch_and_filter,
    _parse_filters,
    _scope_to_user,
)
from app.routes.schemas_governance import (
    AttributionTargetConfigModel,
    CacheHitRow,
    CacheHitTrendPoint,
    CanaryResponse,
    CanaryTrendPoint,
    CostPerOutcomeRow,
    CurStatus,
    ExecutiveRollupLevel,
    ExecutiveRollupResponse,
    OutcomeOverrideModel,
    ReconciliationSlice,
    SpendByApplicationResponse,
)
from app.services.aggregation_service import AggregationService
from app.services.canary_service import CanaryService
from app.services.cloudtrail_service import CloudTrailError
from app.services.cur_ingestion_service import CurIngestionService
from app.services.efficiency_service import EfficiencyService
from app.services.reconciliation_service import ReconciliationService, SliceKey

router = APIRouter(prefix="/api/governance", tags=["governance"])

logger = logging.getLogger(__name__)

# Shared service instances (mirrors the activity/teams route pattern).
_reconciliation_service = ReconciliationService()
_efficiency_service = EfficiencyService()
_canary_service = CanaryService()
_cur_service = CurIngestionService()
_aggregation_service = AggregationService()

# Default unattributed-spend target when no config row exists yet (Req 7.4).
_DEFAULT_UNATTRIBUTED_TARGET = 0.05

_MODEL_CONFIG = ConfigDict(populate_by_name=True)


# ---------------------------------------------------------------------------
# Container response models colocated with the router (Pydantic v2)
# ---------------------------------------------------------------------------

class CacheHitResponse(BaseModel):
    """Cache-hit efficiency grouped by model and application, plus a daily trend."""

    model_config = _MODEL_CONFIG

    byModel: list[CacheHitRow] = Field(default_factory=list)
    byApplication: list[CacheHitRow] = Field(default_factory=list)
    trend: list[CacheHitTrendPoint] = Field(default_factory=list)


class IngestTriggerResponse(BaseModel):
    """202 response body for the admin CUR ingest trigger."""

    model_config = _MODEL_CONFIG

    runId: str = Field(description="Identifier for the scheduled background run.")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _scoped_principals(current_user: CurrentUser) -> Optional[list[str]]:
    """Billed-slice principal scope for the caller.

    Non-admin callers are restricted to their own identity so billed slices are
    filtered to their matching ``iam_principal`` (Req 10.1/10.3); admins get the
    full window (``None`` = no principal filter).
    """
    if current_user is not None and not current_user.can_view_all:
        return [current_user.identity]
    return None


def _slice_key(record: dict[str, Any]) -> SliceKey:
    """Coarse reconciliation key for a record: (team-or-principal, model, day).

    Prefers the enriched ``attributedTeam`` dimension (aligning with the CUR
    ``team_tag``) and falls back to the caller identity (aligning with the CUR
    ``iam_principal``), matching :meth:`ReconciliationService.load_billed_slices`.
    """
    team = record.get("attributedTeam")
    if team and team != "unknown":
        principal = team
    else:
        principal = record.get("userIdentity") or ""
    model = record.get("modelId") or ""
    day = (record.get("timestamp") or "")[:10]
    return SliceKey(team_or_principal=principal, model=model, day=day)


def _group_by_slice(records: list[dict[str, Any]]) -> dict[SliceKey, list[dict[str, Any]]]:
    """Group records by their coarse reconciliation slice key."""
    groups: dict[SliceKey, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        groups[_slice_key(record)].append(record)
    return groups


def _estimated_by_slice(
    groups: dict[SliceKey, list[dict[str, Any]]],
) -> dict[SliceKey, float]:
    """Sum the Tier B estimate per slice."""
    return {
        key: sum(float(r.get("costEstimate") or 0.0) for r in records)
        for key, records in groups.items()
    }


def _reconcile(
    records: list[dict[str, Any]],
    db: Session,
    start: Any,
    end: Any,
    principals: Optional[list[str]],
) -> dict[SliceKey, Any]:
    """Reconcile records against billed cost, attaching ``reconciledCost`` in place.

    Groups records into slices, loads billed slices from ``cur_billed_cost``
    (never Athena, Req 11.2), computes factors, and scales each slice's per-prompt
    estimates. Returns the per-slice factor map.
    """
    groups = _group_by_slice(records)
    estimated = _estimated_by_slice(groups)
    billed = _reconciliation_service.load_billed_slices(db, start, end, principals)
    factors = _reconciliation_service.compute_factors(billed, estimated)
    for key, slice_records in groups.items():
        slice_ = factors.get(key)
        if slice_ is not None:
            _reconciliation_service.reconcile_prompts(slice_records, slice_)
    return factors


def _billed_available(db: Session) -> bool:
    """True when CUR is configured and at least one billing day has been ingested."""
    if not _cur_service.is_configured():
        return False
    return _cur_service.get_state(db).covered_through is not None


def _team_members_map(db: Session) -> dict[str, list[str]]:
    """Build ``{team_name: [member_identity, ...]}`` from the teams table.

    Mirrors how ``routes/teams.py`` derives member lists (``team.memberships`` ->
    ``user_identity``), so canary attribution agrees with the team rollups.
    """
    teams = db.query(Team).all()
    return {team.name: [m.user_identity for m in team.memberships] for team in teams}


def _fetch_or_create_target(db: Session) -> AttributionTargetConfig:
    """Fetch the singleton attribution-target config, creating a default if absent."""
    row = db.execute(select(AttributionTargetConfig).limit(1)).scalar_one_or_none()
    if row is None:
        row = AttributionTargetConfig(unattributed_target=_DEFAULT_UNATTRIBUTED_TARGET)
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def _parse_iso_date(value: str, field_name: str) -> date:
    """Parse an ISO ``YYYY-MM-DD`` string, raising HTTP 422 on a malformed value."""
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid {field_name}: expected an ISO date (YYYY-MM-DD).",
        )


def _run_ingest(run_id: str) -> None:
    """Background CUR ingestion run with its own DB session (off-request)."""
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        logger.info("Starting background CUR ingestion run %s", run_id)
        result = _cur_service.ingest(db)
        logger.info("CUR ingestion run %s finished: status=%s", run_id, result.status)
    except Exception:  # noqa: BLE001 - background task must never crash the worker
        logger.exception("CUR ingestion run %s crashed", run_id)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Read endpoints (scoped)
# ---------------------------------------------------------------------------


@router.get("/reconciliation", response_model=list[ReconciliationSlice])
def get_reconciliation(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    user_identities: Optional[str] = Query(None),
    team_id: Optional[str] = Query(None),
    source_application: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Per-slice reconciliation: billed vs estimated, factor, variance, flag, basis."""
    try:
        params = _parse_filters(start_date, end_date, user_identities, team_id, source_application)
        params = _scope_to_user(params, current_user)
        records = _fetch_and_filter(params, db)

        groups = _group_by_slice(records)
        estimated = _estimated_by_slice(groups)
        billed = _reconciliation_service.load_billed_slices(
            db, params["start_date"], params["end_date"], _scoped_principals(current_user)
        )
        factors = _reconciliation_service.compute_factors(billed, estimated)

        return [
            ReconciliationSlice(
                teamOrPrincipal=key.team_or_principal,
                model=key.model,
                day=key.day,
                billed=slice_.billed_cost,
                estimated=slice_.estimated_cost,
                factor=slice_.factor,
                variance=slice_.variance,
                flag=slice_.flag,
                basis=slice_.basis,
            )
            for key, slice_ in factors.items()
        ]
    except CloudTrailError as exc:
        return JSONResponse(status_code=502, content=_error_response(exc))


@router.get("/efficiency/cache-hit", response_model=CacheHitResponse)
def get_cache_hit(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    user_identities: Optional[str] = Query(None),
    team_id: Optional[str] = Query(None),
    source_application: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Prompt-cache hit ratio by model and application, plus a daily trend."""
    try:
        params = _parse_filters(start_date, end_date, user_identities, team_id, source_application)
        params = _scope_to_user(params, current_user)
        records = _fetch_and_filter(params, db)

        by_model = [
            CacheHitRow(
                label=row["model"],
                inputTokens=row["inputTokens"],
                cacheReadTokens=row["cacheReadTokens"],
                cacheHitRatio=row["cacheHitRatio"],
            )
            for row in _efficiency_service.cache_hit_by_dimension(records, "model")
        ]
        by_application = [
            CacheHitRow(
                label=row["application"],
                inputTokens=row["inputTokens"],
                cacheReadTokens=row["cacheReadTokens"],
                cacheHitRatio=row["cacheHitRatio"],
            )
            for row in _efficiency_service.cache_hit_by_dimension(records, "application")
        ]
        trend = [
            CacheHitTrendPoint(
                date=point["date"],
                inputTokens=point["inputTokens"],
                cacheReadTokens=point["cacheReadTokens"],
                cacheHitRatio=point["cacheHitRatio"],
            )
            for point in _efficiency_service.cache_hit_trend(records)
        ]
        return CacheHitResponse(byModel=by_model, byApplication=by_application, trend=trend)
    except CloudTrailError as exc:
        return JSONResponse(status_code=502, content=_error_response(exc))


@router.get("/efficiency/cost-per-outcome", response_model=list[CostPerOutcomeRow])
def get_cost_per_outcome(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    user_identities: Optional[str] = Query(None),
    team_id: Optional[str] = Query(None),
    source_application: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Per-workload cost-per-outcome, using reconciled cost when billed is available."""
    try:
        params = _parse_filters(start_date, end_date, user_identities, team_id, source_application)
        params = _scope_to_user(params, current_user)
        records = _fetch_and_filter(params, db)

        if _billed_available(db):
            _reconcile(
                records, db, params["start_date"], params["end_date"],
                _scoped_principals(current_user),
            )
            basis = "reconciled"
            cost_field = "reconciledCost"
        else:
            basis = "estimated"
            cost_field = "costEstimate"

        # Group records by workload using the service's own workload resolution so
        # the workload set matches what cost_per_outcome_row counts internally.
        workloads: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for record in records:
            workloads[_efficiency_service._record_workload(record)].append(record)

        rows: list[CostPerOutcomeRow] = []
        for workload in sorted(workloads):
            cost = sum(float(r.get(cost_field) or 0.0) for r in workloads[workload])
            result = _efficiency_service.cost_per_outcome_row(
                records, workload, db, cost=cost, basis=basis
            )
            rows.append(
                CostPerOutcomeRow(
                    workload=result["workload"],
                    outcomeCount=result["outcomeCount"],
                    costPerOutcome=result["costPerOutcome"],
                    basis=result["basis"],
                    estimated=result["estimated"],
                )
            )
        return rows
    except CloudTrailError as exc:
        return JSONResponse(status_code=502, content=_error_response(exc))


@router.get("/canary", response_model=CanaryResponse)
def get_canary(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    user_identities: Optional[str] = Query(None),
    team_id: Optional[str] = Query(None),
    source_application: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Unattributed-spend canary: share vs target, base-model share, and daily trend."""
    try:
        params = _parse_filters(start_date, end_date, user_identities, team_id, source_application)
        params = _scope_to_user(params, current_user)
        records = _fetch_and_filter(params, db)

        if _billed_available(db):
            _reconcile(
                records, db, params["start_date"], params["end_date"],
                _scoped_principals(current_user),
            )
            # Reconciled per-prompt cost sums to billed per slice, so it is the
            # billed allocation used for the billed-basis spend shares.
            for record in records:
                record["billedCost"] = record.get("reconciledCost")
            basis = "billed"
        else:
            basis = "estimated"

        team_members_map = _team_members_map(db)
        result = _canary_service.unattributed_share(records, team_members_map, basis)
        base_share = _canary_service.base_model_share(records, basis)

        records_by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for record in records:
            records_by_day[(record.get("timestamp") or "")[:10]].append(record)
        trend = [
            CanaryTrendPoint(
                date=point["date"],
                unattributedSpendShare=point["unattributedSpendShare"],
                unattributedRecordShare=point["unattributedRecordShare"],
            )
            for point in _canary_service.trend(records_by_day, team_members_map, basis)
        ]

        target = float(_fetch_or_create_target(db).unattributed_target)
        return CanaryResponse(
            unattributedSpendShare=result.unattributed_spend_share,
            unattributedRecordShare=result.unattributed_record_share,
            baseModelShare=base_share,
            basis=basis,
            target=target,
            meetsTarget=_canary_service.evaluate_target(result.unattributed_spend_share, target),
            trend=trend,
        )
    except CloudTrailError as exc:
        return JSONResponse(status_code=502, content=_error_response(exc))


@router.get("/rollup", response_model=ExecutiveRollupResponse)
def get_executive_rollup(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    user_identities: Optional[str] = Query(None),
    team_id: Optional[str] = Query(None),
    source_application: Optional[str] = Query(None),
    department: Optional[str] = Query(
        None,
        description=(
            "Scope the rollup to a single department (case-insensitive). When "
            "set, l1Total/l1InvocationCount and byAgent are computed over that "
            "department's records only, giving an L2->L3 drill-down; "
            "byDepartment still reports every department in the window so the "
            "L2 selector stays populated."
        ),
    ),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Executive cost rollup: L1 grand total -> L2 by department -> L3 by agent.

    Uses the reconciled cost when billed data is available for the window,
    otherwise the token-based estimate (same basis-selection rule as
    cost-per-outcome). L4 (per-invocation) is not duplicated here — drill into
    it via ``GET /api/activity`` filtered to a specific department/agent.

    When ``department`` is supplied, ``l1Total``/``l1InvocationCount``/
    ``byAgent`` are scoped to that department's records so the L3 agent
    breakdown reflects only agents run under that department (a true L2->L3
    drill, not a repeat of the global agent totals). ``byDepartment`` is always
    computed over the full (unscoped) window so the L2 selector/table keeps
    showing every department regardless of the current drill.
    """
    try:
        params = _parse_filters(start_date, end_date, user_identities, team_id, source_application)
        params = _scope_to_user(params, current_user)
        all_records = _fetch_and_filter(params, db)

        if _billed_available(db):
            _reconcile(
                all_records, db, params["start_date"], params["end_date"],
                _scoped_principals(current_user),
            )
            basis = "reconciled"
            cost_field = "reconciledCost"
        else:
            basis = "estimated"
            cost_field = "costEstimate"

        def _cost_of(rows: list[dict[str, Any]]) -> float:
            return sum(float(r.get(cost_field) or 0.0) for r in rows)

        # byDepartment always reflects the FULL window (unscoped) so the L2
        # selector keeps listing every department regardless of the current
        # drill-down.
        by_department = [
            ExecutiveRollupLevel(
                label=row["department"],
                cost=_cost_of(
                    [r for r in all_records if (r.get("department") or "unknown") == row["department"]]
                ),
                invocationCount=row["invocationCount"],
            )
            for row in _aggregation_service.aggregate_by_department(all_records)
        ]

        # L1 total and byAgent are scoped to the requested department (when
        # given) so byAgent becomes a true L2->L3 drill-down rather than a
        # repeat of the global agent breakdown.
        department_value = department.strip().lower() if department and department.strip() else None
        if department_value is not None:
            records = [
                r for r in all_records
                if (r.get("department") or "unknown").strip().lower() == department_value
            ]
        else:
            records = all_records

        by_agent = [
            ExecutiveRollupLevel(
                label=row["agentName"],
                cost=_cost_of([r for r in records if (r.get("agentName") or "unknown") == row["agentName"]]),
                invocationCount=row["invocationCount"],
            )
            for row in _aggregation_service.aggregate_by_agent_name(records)
        ]

        return ExecutiveRollupResponse(
            l1Total=_cost_of(records),
            l1InvocationCount=len(records),
            byDepartment=by_department,
            byAgent=by_agent,
            basis=basis,
        )
    except CloudTrailError as exc:
        return JSONResponse(status_code=502, content=_error_response(exc))


@router.get("/spend-by-application", response_model=SpendByApplicationResponse)
def get_spend_by_application(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    user_identities: Optional[str] = Query(None),
    team_id: Optional[str] = Query(None),
    source_application: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Dedicated spend breakdown by the Application tag dimension.

    ``application`` is resolved from tagged Bedrock inference profiles — e.g. an
    application inference profile's ``Application`` cost-allocation tag — not
    the ``sourceApplication`` calling-client label used elsewhere. Untagged
    usage (no profile, or a profile with no ``Application`` tag) is grouped
    under ``"unknown"``.

    Uses the reconciled cost when billed data is available for the window,
    otherwise the token-based estimate (same basis-selection rule as the
    executive rollup / cost-per-outcome). Rows are sorted descending by cost
    (not just invocation count) so the biggest spenders surface first.
    """
    try:
        params = _parse_filters(start_date, end_date, user_identities, team_id, source_application)
        params = _scope_to_user(params, current_user)
        records = _fetch_and_filter(params, db)

        if _billed_available(db):
            _reconcile(
                records, db, params["start_date"], params["end_date"],
                _scoped_principals(current_user),
            )
            basis = "reconciled"
            cost_field = "reconciledCost"
        else:
            basis = "estimated"
            cost_field = "costEstimate"

        def _cost_of(rows: list[dict[str, Any]]) -> float:
            return sum(float(r.get(cost_field) or 0.0) for r in rows)

        rows = [
            ExecutiveRollupLevel(
                label=row["application"],
                cost=_cost_of(
                    [r for r in records if (r.get("application") or "unknown") == row["application"]]
                ),
                invocationCount=row["invocationCount"],
            )
            for row in _aggregation_service.aggregate_by_application(records)
        ]
        rows.sort(key=lambda r: r.cost, reverse=True)

        return SpendByApplicationResponse(rows=rows, basis=basis)
    except CloudTrailError as exc:
        return JSONResponse(status_code=502, content=_error_response(exc))


@router.get("/cur/status", response_model=CurStatus)
def get_cur_status(
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """CUR ingestion status: availability, covered-through, staleness, last run."""
    state = _cur_service.get_state(db)
    return CurStatus(
        curAvailable=_cur_service.is_configured() and state.covered_through is not None,
        coveredThrough=state.covered_through.isoformat() if state.covered_through else None,
        billedStale=state.is_stale,
        lastRunAt=state.last_run_at.isoformat() if state.last_run_at else None,
        status=state.status,
    )


# ---------------------------------------------------------------------------
# Admin trigger + config endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/cur/ingest",
    status_code=202,
    response_model=IngestTriggerResponse,
    dependencies=[Depends(require_admin)],
)
def trigger_cur_ingest(background_tasks: BackgroundTasks):
    """Trigger an off-request CUR ingestion run (admin only); returns 202 {runId}."""
    run_id = str(uuid.uuid4())
    background_tasks.add_task(_run_ingest, run_id)
    return IngestTriggerResponse(runId=run_id)


@router.get("/attribution-target", response_model=AttributionTargetConfigModel)
def get_attribution_target(
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Read the singleton unattributed-spend target config (fetch-or-create default)."""
    row = _fetch_or_create_target(db)
    return AttributionTargetConfigModel(
        unattributedTarget=float(row.unattributed_target),
        stalenessHoursOverride=row.staleness_hours_override,
    )


@router.put(
    "/attribution-target",
    response_model=AttributionTargetConfigModel,
    dependencies=[Depends(require_admin)],
)
def put_attribution_target(
    body: AttributionTargetConfigModel,
    db: Session = Depends(get_db),
):
    """Set the singleton unattributed-spend target config (admin only)."""
    row = _fetch_or_create_target(db)
    row.unattributed_target = body.unattributedTarget
    row.staleness_hours_override = body.stalenessHoursOverride
    db.commit()
    db.refresh(row)
    return AttributionTargetConfigModel(
        unattributedTarget=float(row.unattributed_target),
        stalenessHoursOverride=row.staleness_hours_override,
    )


@router.get("/outcome-overrides", response_model=list[OutcomeOverrideModel])
def get_outcome_overrides(
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """List admin-supplied per-workload outcome-count overrides."""
    rows = db.execute(select(WorkloadOutcomeOverride)).scalars().all()
    return [
        OutcomeOverrideModel(
            application=row.application,
            periodStart=row.period_start.isoformat(),
            periodEnd=row.period_end.isoformat(),
            outcomeCount=row.outcome_count,
        )
        for row in rows
    ]


@router.put(
    "/outcome-overrides",
    response_model=OutcomeOverrideModel,
    dependencies=[Depends(require_admin)],
)
def put_outcome_override(
    body: OutcomeOverrideModel,
    db: Session = Depends(get_db),
):
    """Upsert an admin per-workload outcome-count override (admin only)."""
    period_start = _parse_iso_date(body.periodStart, "periodStart")
    period_end = _parse_iso_date(body.periodEnd, "periodEnd")
    if period_start > period_end:
        raise HTTPException(
            status_code=422,
            detail="Invalid period: periodStart must not be after periodEnd.",
        )

    stmt = pg_insert(WorkloadOutcomeOverride).values(
        application=body.application,
        period_start=period_start,
        period_end=period_end,
        outcome_count=body.outcomeCount,
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_outcome_workload_period",
        set_={"outcome_count": body.outcomeCount, "updated_at": utcnow()},
    )
    db.execute(stmt)
    db.commit()
    return body
