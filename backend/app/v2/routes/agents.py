"""V2 Agent API routes for the Dashboard V2 Agent Platform."""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.auth import CurrentUser, get_current_user, require_admin
from app.services.audit_log import audit
from app.database import get_db
from app.services import settings_service
from app.v2.models import V2AgentConfig
from app.v2.schemas import (
    AgentConfigRead,
    AgentConfigUpdate,
    AgentExecutionTrigger,
    ApprovalAction,
    FindingFilters,
)
from app.v2.services.agent_execution_service import AgentExecutionService
from app.v2.services.agent_finding_service import AgentFindingService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v2/agents", tags=["v2-agents"])

_execution_service = AgentExecutionService()
_finding_service = AgentFindingService()


# ---------------------------------------------------------------------------
# Agent execution
# ---------------------------------------------------------------------------

@router.post("/{agent_name}/execute")
def execute_agent(
    agent_name: str,
    body: AgentExecutionTrigger = AgentExecutionTrigger(),
    db: Session = Depends(get_db),
    admin: CurrentUser = Depends(require_admin),
):
    """Trigger agent execution (manual or scheduled)."""
    audit(admin, "agent.execute", db=db, agent=agent_name, trigger=body.trigger_type)
    result = _execution_service.execute_agent(agent_name, body.trigger_type, db)
    return result


# ---------------------------------------------------------------------------
# Agent configuration
# ---------------------------------------------------------------------------

@router.get("/config", response_model=list[AgentConfigRead])
def list_agent_configs(db: Session = Depends(get_db)):
    """List all agent configurations."""
    configs = db.query(V2AgentConfig).order_by(V2AgentConfig.agent_name).all()
    return configs


@router.patch("/{agent_name}/config")
def update_agent_config(
    agent_name: str,
    body: AgentConfigUpdate,
    db: Session = Depends(get_db),
    admin: CurrentUser = Depends(require_admin),
):
    """Update agent configuration (schedule, enabled/disabled, etc.)."""
    update_data = body.model_dump(exclude_unset=True)
    if not update_data:
        raise HTTPException(status_code=400, detail="No fields to update.")
    result = _execution_service.update_agent_config(agent_name, update_data, db)
    if "error" in result and result["error"] == "not_found":
        raise HTTPException(status_code=404, detail=result["message"])
    if "error" in result and result["error"] == "invalid_model":
        raise HTTPException(status_code=422, detail=result["message"])
    audit(admin, "agent.config.update", db=db, agent=agent_name, changes=update_data)
    return result


# ---------------------------------------------------------------------------
# Execution history
# ---------------------------------------------------------------------------

@router.get("/{agent_name}/history")
def get_execution_history(
    agent_name: str,
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """Get execution history for an agent."""
    return _execution_service.get_execution_history(agent_name, db, limit=limit)


# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------

@router.get("/findings/summary")
def get_findings_summary(
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Aggregated finding counts per agent for the Insights summary view."""
    return _finding_service.get_findings_summary(
        db, viewer_identity=current_user.identity, can_view_all=current_user.can_view_all
    )


@router.get("/{agent_name}/findings")
def get_agent_findings(
    agent_name: str,
    date_start: Optional[str] = Query(default=None),
    date_end: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    severity: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Query agent findings with optional filters."""
    from datetime import datetime

    filters = FindingFilters(
        date_start=datetime.fromisoformat(date_start) if date_start else None,
        date_end=datetime.fromisoformat(date_end) if date_end else None,
        status=status,
        severity=severity,
        agent_name=agent_name,
    )
    return _finding_service.get_findings(
        agent_name,
        filters,
        db,
        viewer_identity=current_user.identity,
        can_view_all=current_user.can_view_all,
        include_prompt_text=settings_service.get_prompt_history_enabled(db),
    )


@router.patch("/findings/{finding_id}")
def update_finding_status(
    finding_id: str,
    body: ApprovalAction,
    db: Session = Depends(get_db),
    admin: CurrentUser = Depends(require_admin),
):
    """Update finding status (approve/reject)."""
    result = _finding_service.update_finding_status(
        finding_id, body.status, body.rejection_reason, db
    )
    if "error" in result and result["error"] == "not_found":
        raise HTTPException(status_code=404, detail=result["message"])
    audit(admin, "finding.status", db=db, finding=finding_id, status=body.status)
    return result
