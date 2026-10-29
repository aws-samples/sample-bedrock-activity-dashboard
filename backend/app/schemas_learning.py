"""Pydantic schemas for the Legal Review Learning Agent."""

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Feedback schemas
# ---------------------------------------------------------------------------

class FeedbackCreate(BaseModel):
    """Schema for submitting feedback on a finding."""

    finding_id: UUID
    rating: Literal["positive", "negative"]
    classification: Literal["false_positive", "missed_finding"] | None = None
    comment: str | None = None


class FeedbackRead(BaseModel):
    """Read schema for a feedback record."""

    id: UUID
    finding_id: UUID
    user_id: str
    rating: str
    classification: str | None
    comment: str | None
    model_id: str
    document_type: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class MissedFindingCreate(BaseModel):
    """Schema for reporting a missed finding on a review."""

    review_request_id: UUID
    description: str


# ---------------------------------------------------------------------------
# Performance metrics schemas
# ---------------------------------------------------------------------------

class PerformanceMetricsRead(BaseModel):
    """Read schema for computed performance metrics."""

    scope_type: str
    model_id: str | None
    document_type: str | None
    accuracy_score: float
    false_positive_rate: float
    total_rated: int
    is_low_confidence: bool
    computed_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Model recommendation schema
# ---------------------------------------------------------------------------

class ModelRecommendationRead(BaseModel):
    """Read schema for a model recommendation."""

    recommended_model: str
    accuracy_score: float
    false_positive_rate: float
    rated_finding_count: int
    confidence: str  # "high", "low", "insufficient_data"
    message: str | None = None


# ---------------------------------------------------------------------------
# Classification schema
# ---------------------------------------------------------------------------

class ClassificationResultRead(BaseModel):
    """Read schema for a document classification result."""

    document_type: str
    confidence: float
    scores: dict[str, float]


# ---------------------------------------------------------------------------
# Prompt version and suggestion schemas
# ---------------------------------------------------------------------------

class PromptVersionRead(BaseModel):
    """Read schema for a prompt version."""

    id: UUID
    review_command: str
    version_number: int
    prompt_text: str
    is_active: bool
    created_by: str | None
    created_at: datetime
    superseded_at: datetime | None

    model_config = {"from_attributes": True}


class PromptSuggestionRead(BaseModel):
    """Read schema for a prompt improvement suggestion."""

    id: UUID
    review_command: str
    pattern_description: str
    suggested_prompt_change: str
    supporting_feedback_count: int
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Analytics and trends schemas
# ---------------------------------------------------------------------------

class WeeklyTrendRead(BaseModel):
    """Read schema for a single week's trend data point."""

    week_start: date
    accuracy_score: float
    false_positive_rate: float
    total_rated: int
    model_id: str | None
    document_type: str | None


class AnalyticsReportRead(BaseModel):
    """Read schema for a full feedback analytics report."""

    total_feedback_count: int
    accuracy_trends: list[WeeklyTrendRead]
    fpr_trends: list[WeeklyTrendRead]
    top_patterns: list[PromptSuggestionRead]
    model_comparison: list[PerformanceMetricsRead]
