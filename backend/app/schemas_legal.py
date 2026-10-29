"""Pydantic schemas for the Legal Review App."""

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Response schema for document upload
# ---------------------------------------------------------------------------

class DocumentUploadResponse(BaseModel):
    """Response returned after a successful document upload and review creation."""

    review_request_id: UUID
    document_id: UUID
    status: str
    filename: str


# ---------------------------------------------------------------------------
# Read schemas for review requests and findings
# ---------------------------------------------------------------------------

class ReviewRequestRead(BaseModel):
    """Read schema for a legal review request."""

    id: UUID
    document_id: UUID
    review_command: str
    status: str
    error_message: Optional[str] = None
    created_at: datetime
    completed_at: Optional[datetime] = None
    document_name: str
    finding_count: int

    model_config = {"from_attributes": True}


class FindingRead(BaseModel):
    """Read schema for a single legal review finding."""

    id: UUID
    risk_level: str
    description: str
    affected_clause: Optional[str] = None
    recommended_action: Optional[str] = None
    sort_order: int

    model_config = {"from_attributes": True}


class ReviewWithFindingsRead(BaseModel):
    """A review request together with its findings and risk-level summary."""

    review: ReviewRequestRead
    findings: list[FindingRead]
    summary: dict[str, int]  # risk_level -> count


# ---------------------------------------------------------------------------
# Query parameters for listing reviews
# ---------------------------------------------------------------------------

class ReviewListParams(BaseModel):
    """Query parameters for paginated review history."""

    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)
    search: Optional[str] = None
    date_start: Optional[str] = None
    date_end: Optional[str] = None


# ---------------------------------------------------------------------------
# Export schema
# ---------------------------------------------------------------------------

class FindingsExport(BaseModel):
    """Schema used for exporting findings to JSON."""

    review_id: UUID
    document_name: str
    review_command: str
    reviewed_at: datetime
    findings: list[FindingRead]
    metadata: dict[str, Any]
