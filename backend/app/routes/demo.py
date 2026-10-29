"""Demo Data admin API routes.

Lets an admin generate an on-demand batch of demo activity, clear it, and read
current status. Every generated record is a genuine Bedrock invocation — real
prompt, real model response, real token usage — with embedded fake PII so the
PII Detector agent has real content to flag. Generation/clear are admin-only;
status is available to any authenticated user.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import require_admin
from app.database import get_db
from app.services import demo_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/demo", tags=["demo"])

# Cap requestable count per batch — every record is a real Bedrock call, so
# this bounds both latency and real AWS cost of a single generate request.
_MAX_COUNT = 100
_DEFAULT_COUNT = 30


# ---------------------------------------------------------------------------
# Schemas (Pydantic v2)
# ---------------------------------------------------------------------------

class GenerateDemoRequest(BaseModel):
    count: Optional[int] = Field(default=None, ge=0, le=_MAX_COUNT)
    # When True, a portion of generated jobs invoke Bedrock through a small
    # set of tagged application inference profiles (get-or-created by the
    # backend) instead of a bare model id, so Cost Governance's
    # Spend-by-Application view has real data to show instead of everything
    # landing in "unknown". Best-effort: if profile setup fails (missing IAM
    # permission, model not enabled), generation proceeds exactly as when
    # this is False.
    withApplications: bool = False


class DemoFailure(BaseModel):
    model: str
    error: str


class GenerateDemoResponse(BaseModel):
    requested: int
    succeeded: int
    failed: list[DemoFailure]
    batchId: str
    guardrailIntervenedCount: int = 0
    # Application names of any tagged inference profile actually used by at
    # least one succeeded invocation in this batch. Empty when
    # `withApplications` was False or tagged-profile setup failed entirely.
    applicationProfilesUsed: list[str] = Field(default_factory=list)


class ClearDemoResponse(BaseModel):
    deleted: int


class DemoStatusResponse(BaseModel):
    recordCount: int
    realCount: int
    lastGeneratedAt: Optional[str] = None
    lastBatchId: Optional[str] = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/generate", response_model=GenerateDemoResponse, dependencies=[Depends(require_admin)])
def generate_demo(
    body: Optional[GenerateDemoRequest] = None,
    db: Session = Depends(get_db),
) -> GenerateDemoResponse:
    """Generate a batch of demo data — every record a real Bedrock invocation. Admin only."""
    count = _DEFAULT_COUNT
    with_applications = False
    if body is not None:
        if body.count is not None:
            count = body.count
        with_applications = body.withApplications

    summary = demo_service.generate_demo_data(db, count=count, with_applications=with_applications)
    return GenerateDemoResponse(**summary)


@router.post("/clear", response_model=ClearDemoResponse, dependencies=[Depends(require_admin)])
def clear_demo(db: Session = Depends(get_db)) -> ClearDemoResponse:
    """Delete all persisted demo records. Admin only."""
    result = demo_service.clear_demo_data(db)
    return ClearDemoResponse(**result)


@router.get("/status", response_model=DemoStatusResponse)
def demo_status(db: Session = Depends(get_db)) -> DemoStatusResponse:
    """Return demo record counts and last-batch metadata (no prompt text)."""
    status = demo_service.get_demo_status(db)
    return DemoStatusResponse(**status)
