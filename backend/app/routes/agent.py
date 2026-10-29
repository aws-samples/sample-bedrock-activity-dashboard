"""AI Agent API routes for the Bedrock Activity Dashboard."""

import asyncio
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import CurrentUser, get_current_user
from app.database import get_db
from app.services.agent_service import AgentService, BedrockConverseError
from app.services.cloudtrail_service import CloudTrailService
from app.services.report_service import ReportService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agent", tags=["agent"])

# Shared CloudTrailService instance — reused across all agent requests so we
# don't re-create boto3 clients on every chat call (same instance the activity
# routes use, avoiding duplicate region queries).
_shared_cloudtrail = CloudTrailService()


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class ChatFilters(BaseModel):
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    user_identities: list[str] = Field(default_factory=list)
    team_id: Optional[str] = None
    source_application: Optional[str] = None


class ChatRequest(BaseModel):
    sessionId: Optional[str] = None
    message: str
    filters: ChatFilters = Field(default_factory=ChatFilters)
    budgetTokens: Optional[int] = None


class ReportRequest(BaseModel):
    periodStart: str
    periodEnd: str
    reportType: str = "daily"  # "daily" or "weekly"


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/chat")
async def agent_chat(
    body: ChatRequest,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Send a natural-language question to the AI Agent with usage context."""
    agent = AgentService(cloudtrail_service=_shared_cloudtrail)
    filters = {
        "start_date": body.filters.start_date,
        "end_date": body.filters.end_date,
        "user_identities": body.filters.user_identities,
        "team_id": body.filters.team_id,
        "source_application": body.filters.source_application,
    }
    # SECURITY: scope the chat usage context to the caller. Callers without
    # can_view_all (same rule as activity.py `_scope_to_user`) are forced to
    # their own identity so they cannot query other users' data.
    # build_usage_context honors filters["user_identities"].
    if current_user is not None and not current_user.can_view_all:
        filters["user_identities"] = [current_user.identity]
    budget_tokens = body.budgetTokens or 2048
    try:
        result = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: agent.chat(
                body.sessionId,
                body.message,
                filters,
                db,
                budget_tokens=budget_tokens,
                user_identity=current_user.identity if current_user else None,
                user_name=current_user.username if current_user else None,
            ),
        )
    except BedrockConverseError as exc:
        raise HTTPException(
            status_code=502,
            detail={"error": {"code": exc.error_code, "message": exc.message}},
        )

    return result


@router.post("/chat/stream")
async def agent_chat_stream(
    body: ChatRequest,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Stream AI Agent response token-by-token using Server-Sent Events."""
    agent = AgentService(cloudtrail_service=_shared_cloudtrail)
    filters = {
        "start_date": body.filters.start_date,
        "end_date": body.filters.end_date,
        "user_identities": body.filters.user_identities,
        "team_id": body.filters.team_id,
        "source_application": body.filters.source_application,
    }
    # SECURITY: scope the chat usage context to the caller. Callers without
    # can_view_all (same rule as activity.py `_scope_to_user`) are forced to
    # their own identity so they cannot stream answers built from other users'
    # data.
    if current_user is not None and not current_user.can_view_all:
        filters["user_identities"] = [current_user.identity]
    budget_tokens = body.budgetTokens or 2048

    def generate():
        try:
            yield from agent.chat_stream(
                body.sessionId,
                body.message,
                filters,
                db,
                budget_tokens=budget_tokens,
                user_identity=current_user.identity if current_user else None,
                user_name=current_user.username if current_user else None,
            )
        except BedrockConverseError as exc:
            import json
            yield f"data: [ERROR:{json.dumps({'code': exc.error_code, 'message': exc.message})}]\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable nginx/ALB buffering
            "Transfer-Encoding": "chunked",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/reports")
def generate_report(body: ReportRequest, db: Session = Depends(get_db)):
    """Trigger generation of a summary report."""
    period_start = datetime.fromisoformat(body.periodStart)
    period_end = datetime.fromisoformat(body.periodEnd)

    svc = ReportService()
    result = svc.generate_report(period_start, period_end, body.reportType, db)
    return result


@router.get("/reports")
def list_reports(db: Session = Depends(get_db)):
    """List all generated summary reports."""
    svc = ReportService()
    return svc.list_reports(db)


@router.get("/reports/{report_id}")
def get_report(report_id: str, db: Session = Depends(get_db)):
    """Retrieve a specific summary report."""
    svc = ReportService()
    result = svc.get_report(report_id, db)
    if result is None:
        raise HTTPException(status_code=404, detail="Report not found.")
    return result
