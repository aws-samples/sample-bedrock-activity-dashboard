"""AgentCore Runtime consumption cost API (``/api/agentcore-cost/*``).

Deliberately a standalone router — not merged into ``governance.py`` or the
``/api/activity`` cost endpoints — so AgentCore compute cost (the cost of
running the agents themselves on AgentCore Runtime) is never conflated with
Bedrock token cost (what the agents' own LLM calls consume, already covered
by ``CostAnalysisTab`` / ``CostGovernanceTab``). The frontend mirrors this
with its own standalone tab.

The GET endpoint returns a single flat list combining this dashboard's own
governed agents with any other AgentCore Runtime the background discovery
scan has found in the account — there is no "governed vs. discovered"
split anywhere in this API. New Runtimes only show up here after a scan has
recorded them (scheduled daily, or triggered on demand via the admin-only
POST /scan endpoint below); the GET path itself never scans live.

Cost figures are computed live from CloudWatch (``AgentCoreCostService``) —
no Athena/CUR dependency, nothing persisted for the cost side. Every read is
scoped by the same Cognito ``get_current_user`` dependency the rest of the
app uses; there is no second auth path.
"""

import logging
import uuid
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy.orm import Session

from app.auth import CurrentUser, get_current_user, require_admin
from app.config import settings
from app.database import get_db
from app.routes.schemas_agentcore_cost import AgentCoreCostResponse, AgentRuntimeCostRow, ScanTriggerResponse
from app.services.agentcore_cost_service import AgentCoreCostService
from app.services.agentcore_discovery_service import AgentCoreDiscoveryService
from app.services.filter_service import FilterService, parse_iso_to_utc

router = APIRouter(prefix="/api/agentcore-cost", tags=["agentcore-cost"])

logger = logging.getLogger(__name__)

_cost_service = AgentCoreCostService()
_discovery_service = AgentCoreDiscoveryService()
_filter_service = FilterService()


def _resolve_window(start_date: Optional[str], end_date: Optional[str]):
    """Parse the optional start/end query params, defaulting like /api/activity."""
    if start_date and end_date:
        start = parse_iso_to_utc(start_date)
        end = parse_iso_to_utc(end_date, end_of_day_if_bare=True)
    elif start_date or end_date:
        default_start, default_end = _filter_service.get_default_date_range()
        start = parse_iso_to_utc(start_date) if start_date else default_start
        end = parse_iso_to_utc(end_date, end_of_day_if_bare=True) if end_date else default_end
    else:
        start, end = _filter_service.get_default_date_range()

    _filter_service.validate_date_range(start, end)
    return start, end


def _run_scan(run_id: str) -> None:
    """Background discovery scan with its own DB session (off-request)."""
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        logger.info("Starting background AgentCore scan %s", run_id)
        result = _discovery_service.discover(db)
        logger.info("AgentCore scan %s finished: status=%s", run_id, result.status)
    except Exception:  # noqa: BLE001 - background task must never crash the worker
        logger.exception("AgentCore scan %s crashed", run_id)
    finally:
        db.close()


@router.get("", response_model=AgentCoreCostResponse)
def get_agentcore_cost(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """AgentCore Runtime consumption cost: total -> per-Runtime drill-down.

    Every Runtime this account knows about — this dashboard's own governed
    agents plus anything the background discovery scan has found — in one
    flat list. Defaults to the last 7 days (same default window as
    /api/activity) when no date range is supplied. Returns zero/empty with
    ``configured=false`` when AgentCore cost tracking is disabled.
    """
    start, end = _resolve_window(start_date, end_date)

    configured = _cost_service.is_configured()
    per_agent = _cost_service.get_all_runtime_cost(start, end, db) if configured else []
    last_scan = AgentCoreDiscoveryService.last_scan_at(db) if configured else None

    by_agent = [
        AgentRuntimeCostRow(
            agentName=row.agent_name,
            displayName=row.display_name,
            region=row.region,
            vcpuHours=row.vcpu_hours,
            gbHours=row.gb_hours,
            cpuCost=row.cpu_cost,
            memoryCost=row.memory_cost,
            totalCost=row.total_cost,
        )
        for row in per_agent
    ]

    return AgentCoreCostResponse(
        totalCost=AgentCoreCostService.total_cost(per_agent),
        byAgent=by_agent,
        configured=configured,
        vcpuHourlyRate=settings.agentcore_vcpu_hourly_rate,
        memoryGbHourlyRate=settings.agentcore_memory_gb_hourly_rate,
        scanRegions=_discovery_service.regions(),
        lastScanAt=last_scan.isoformat() if last_scan else None,
    )


@router.post(
    "/scan",
    status_code=202,
    response_model=ScanTriggerResponse,
    dependencies=[Depends(require_admin)],
)
def trigger_agentcore_scan(background_tasks: BackgroundTasks):
    """Trigger an off-request scan for new AgentCore Runtimes (admin only).

    Scans every configured region for AgentCore Runtimes and records what it
    finds; results appear the next time GET /api/agentcore-cost is called
    (usually within a few seconds — this returns 202 immediately and runs in
    the background). Runs automatically once a day regardless of this
    trigger.
    """
    run_id = str(uuid.uuid4())
    background_tasks.add_task(_run_scan, run_id)
    return ScanTriggerResponse(runId=run_id)
