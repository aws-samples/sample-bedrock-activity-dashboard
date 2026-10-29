"""Learning Agent API routes.

Provides endpoints for feedback collection, performance metrics, model
recommendations, document classification, prompt management, and analytics.
All endpoints require authentication via the Cognito ALB integration.
"""

from __future__ import annotations

import logging
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.routes.legal_review import get_current_user_id
from app.schemas_learning import (
    AnalyticsReportRead,
    ClassificationResultRead,
    FeedbackCreate,
    FeedbackRead,
    ModelRecommendationRead,
    PerformanceMetricsRead,
    PromptSuggestionRead,
    PromptVersionRead,
    WeeklyTrendRead,
)
from app.services.legal.classifier import DocumentTypeClassifier
from app.services.legal.learning_agent import (
    LearningAgentService,
    classify_safe,
    get_recommendation_safe,
    submit_feedback_with_retry,
    _correlation_id,
)
from app.services.legal.prompt_manager import PromptVersionManager

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/legal-review/learning",
    tags=["learning-agent"],
)

# Shared service instances
_learning_agent = LearningAgentService()
_classifier = DocumentTypeClassifier()
_prompt_manager = PromptVersionManager()


# ---------------------------------------------------------------------------
# Request body for classify endpoint
# ---------------------------------------------------------------------------

class ClassifyRequest(BaseModel):
    text: str


# ---------------------------------------------------------------------------
# POST /feedback — submit feedback on a finding
# ---------------------------------------------------------------------------

@router.post("/feedback", response_model=FeedbackRead)
def submit_feedback(
    body: FeedbackCreate,
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    """Submit or update feedback on a finding."""
    cid = _correlation_id()
    try:
        feedback = submit_feedback_with_retry(
            db=db,
            user_id=user_id,
            finding_id=body.finding_id,
            rating=body.rating,
            classification=body.classification,
            comment=body.comment,
            correlation_id=cid,
        )
        db.commit()
        return FeedbackRead.model_validate(feedback)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(
            "Feedback submission failed",
            extra={"correlation_id": cid, "error": str(e)},
        )
        raise HTTPException(status_code=500, detail="Failed to submit feedback.")


# ---------------------------------------------------------------------------
# GET /feedback/{finding_id} — get feedback for a finding
# ---------------------------------------------------------------------------

@router.get("/feedback/{finding_id}", response_model=FeedbackRead | None)
def get_feedback(
    finding_id: UUID,
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    """Get existing feedback for a finding from the current user."""
    feedback = _learning_agent.get_feedback(db, user_id, finding_id)
    if feedback is None:
        return None
    return FeedbackRead.model_validate(feedback)


# ---------------------------------------------------------------------------
# GET /metrics — get performance metrics
# ---------------------------------------------------------------------------

@router.get("/metrics", response_model=list[PerformanceMetricsRead])
def get_metrics(
    model_id: Optional[str] = Query(None),
    document_type: Optional[str] = Query(None),
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    """Get performance metrics, optionally filtered by model and/or document type."""
    from app.models_learning import LearningPerformanceMetric

    query = db.query(LearningPerformanceMetric)
    if model_id:
        query = query.filter(LearningPerformanceMetric.model_id == model_id)
    if document_type:
        query = query.filter(LearningPerformanceMetric.document_type == document_type)

    metrics = query.all()
    return [PerformanceMetricsRead.model_validate(m) for m in metrics]


# ---------------------------------------------------------------------------
# GET /metrics/trends — get weekly accuracy/FPR trends
# ---------------------------------------------------------------------------

@router.get("/metrics/trends", response_model=list[WeeklyTrendRead])
def get_metrics_trends(
    model_id: Optional[str] = Query(None),
    document_type: Optional[str] = Query(None),
    weeks: int = Query(12, ge=1, le=52),
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    """Get weekly accuracy and FPR trends."""
    return _learning_agent.get_weekly_trends(
        db, model_id=model_id, document_type=document_type, weeks=weeks
    )


# ---------------------------------------------------------------------------
# GET /recommend/{document_type} — get model recommendation
# ---------------------------------------------------------------------------

@router.get("/recommend/{document_type}", response_model=ModelRecommendationRead)
def get_recommendation(
    document_type: str,
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    """Get model recommendation for a document type (graceful degradation)."""
    return get_recommendation_safe(db, document_type)


# ---------------------------------------------------------------------------
# POST /classify — classify document type from text
# ---------------------------------------------------------------------------

@router.post("/classify", response_model=ClassificationResultRead)
def classify_document(
    body: ClassifyRequest,
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    """Classify document type from text content."""
    result = classify_safe(body.text)
    return ClassificationResultRead(
        document_type=result["document_type"],
        confidence=result["confidence"],
        scores=result.get("scores", {}),
    )


# ---------------------------------------------------------------------------
# GET /prompts — list prompt versions
# ---------------------------------------------------------------------------

@router.get("/prompts", response_model=list[PromptVersionRead])
def list_prompts(
    command: str = Query("legal_review"),
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    """List prompt versions for a review command."""
    versions = _prompt_manager.list_versions(db, command)
    return [PromptVersionRead.model_validate(v) for v in versions]


# ---------------------------------------------------------------------------
# POST /prompts/{id}/activate — activate a prompt version
# ---------------------------------------------------------------------------

@router.post("/prompts/{version_id}/activate", response_model=PromptVersionRead)
def activate_prompt(
    version_id: UUID,
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    """Activate a prompt version."""
    try:
        version = _prompt_manager.activate_version(db, version_id)
        db.commit()
        return PromptVersionRead.model_validate(version)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ---------------------------------------------------------------------------
# POST /prompts/{id}/rollback — rollback to a prompt version
# ---------------------------------------------------------------------------

@router.post("/prompts/{version_id}/rollback", response_model=PromptVersionRead)
def rollback_prompt(
    version_id: UUID,
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    """Rollback to a previous prompt version."""
    try:
        version = _prompt_manager.rollback_to_version(db, version_id)
        db.commit()
        return PromptVersionRead.model_validate(version)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ---------------------------------------------------------------------------
# GET /suggestions — list prompt improvement suggestions
# ---------------------------------------------------------------------------

@router.get("/suggestions", response_model=list[PromptSuggestionRead])
def list_suggestions(
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    """List pending prompt improvement suggestions."""
    from app.models_learning import LearningPromptSuggestion

    suggestions = (
        db.query(LearningPromptSuggestion)
        .filter(LearningPromptSuggestion.status == "pending")
        .order_by(LearningPromptSuggestion.supporting_feedback_count.desc())
        .all()
    )
    return [PromptSuggestionRead.model_validate(s) for s in suggestions]


# ---------------------------------------------------------------------------
# POST /suggestions/{id}/approve — approve a prompt suggestion
# ---------------------------------------------------------------------------

@router.post("/suggestions/{suggestion_id}/approve", response_model=PromptVersionRead)
def approve_suggestion(
    suggestion_id: UUID,
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    """Approve a prompt suggestion, creating a new prompt version."""
    try:
        version = _learning_agent.approve_suggestion(db, suggestion_id, user_id)
        db.commit()
        return PromptVersionRead.model_validate(version)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ---------------------------------------------------------------------------
# POST /suggestions/{id}/dismiss — dismiss a prompt suggestion
# ---------------------------------------------------------------------------

@router.post("/suggestions/{suggestion_id}/dismiss")
def dismiss_suggestion(
    suggestion_id: UUID,
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    """Dismiss a prompt suggestion."""
    from app.models_learning import LearningPromptSuggestion
    from datetime import datetime, timezone

    suggestion = (
        db.query(LearningPromptSuggestion)
        .filter(LearningPromptSuggestion.id == suggestion_id)
        .first()
    )
    if suggestion is None:
        raise HTTPException(status_code=404, detail="Suggestion not found")
    if suggestion.status != "pending":
        raise HTTPException(
            status_code=422, detail=f"Suggestion is already {suggestion.status}"
        )

    suggestion.status = "dismissed"
    suggestion.resolved_at = datetime.now(timezone.utc)
    suggestion.resolved_by = user_id
    db.commit()
    return {"status": "dismissed", "suggestion_id": str(suggestion_id)}


# ---------------------------------------------------------------------------
# GET /analytics/report — generate feedback analytics report
# ---------------------------------------------------------------------------

@router.get("/analytics/report", response_model=AnalyticsReportRead)
def get_analytics_report(
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    """Generate a full feedback analytics report."""
    return _learning_agent.generate_analytics_report(db)
