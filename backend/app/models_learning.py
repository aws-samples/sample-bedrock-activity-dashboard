"""SQLAlchemy models for the Legal Review Learning Agent."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# 1. learning_prompt_versions — versioned system prompts per review command
# ---------------------------------------------------------------------------
class LearningPromptVersion(Base):
    __tablename__ = "learning_prompt_versions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    review_command = Column(String(100), nullable=False, index=True)
    version_number = Column(Integer, nullable=False)
    prompt_text = Column(Text, nullable=False)
    is_active = Column(Boolean, nullable=False, server_default="false")
    created_by = Column(String(512), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    superseded_at = Column(DateTime(timezone=True), nullable=True)

    feedback_records = relationship("LearningFeedback", back_populates="prompt_version")
    performance_metrics = relationship("LearningPerformanceMetric", back_populates="prompt_version")


# ---------------------------------------------------------------------------
# 2. learning_feedback — user feedback on individual findings
# ---------------------------------------------------------------------------
class LearningFeedback(Base):
    __tablename__ = "learning_feedback"
    __table_args__ = (
        UniqueConstraint("finding_id", "user_id", name="uq_learning_feedback_finding_user"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    finding_id = Column(
        UUID(as_uuid=True),
        ForeignKey("legal_review_findings.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id = Column(String(512), nullable=False, index=True)
    rating = Column(String(20), nullable=False)
    classification = Column(String(30), nullable=True)
    comment = Column(Text, nullable=True)
    model_id = Column(String(100), nullable=False)
    prompt_version_id = Column(
        UUID(as_uuid=True),
        ForeignKey("learning_prompt_versions.id"),
        nullable=True,
    )
    document_type = Column(String(50), nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    finding = relationship("LegalReviewFinding", back_populates="feedback_records")
    prompt_version = relationship("LearningPromptVersion", back_populates="feedback_records")


# ---------------------------------------------------------------------------
# 3. learning_performance_metrics — cached accuracy / FPR stats
# ---------------------------------------------------------------------------
class LearningPerformanceMetric(Base):
    __tablename__ = "learning_performance_metrics"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    scope_type = Column(String(50), nullable=False)
    model_id = Column(String(100), nullable=True)
    document_type = Column(String(50), nullable=True)
    prompt_version_id = Column(
        UUID(as_uuid=True),
        ForeignKey("learning_prompt_versions.id"),
        nullable=True,
    )
    accuracy_score = Column(Float, nullable=False)
    false_positive_rate = Column(Float, nullable=False)
    total_rated = Column(Integer, nullable=False)
    positive_count = Column(Integer, nullable=False)
    negative_count = Column(Integer, nullable=False)
    false_positive_count = Column(Integer, nullable=False)
    is_low_confidence = Column(Boolean, nullable=False, server_default="false")
    computed_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    prompt_version = relationship("LearningPromptVersion", back_populates="performance_metrics")


# ---------------------------------------------------------------------------
# 4. learning_prompt_suggestions — AI-generated prompt improvement ideas
# ---------------------------------------------------------------------------
class LearningPromptSuggestion(Base):
    __tablename__ = "learning_prompt_suggestions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    review_command = Column(String(100), nullable=False)
    pattern_description = Column(Text, nullable=False)
    suggested_prompt_change = Column(Text, nullable=False)
    supporting_feedback_count = Column(Integer, nullable=False)
    status = Column(String(30), nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    resolved_by = Column(String(512), nullable=True)


# ---------------------------------------------------------------------------
# 5. learning_classification_logs — document type classification audit trail
# ---------------------------------------------------------------------------
class LearningClassificationLog(Base):
    __tablename__ = "learning_classification_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id = Column(
        UUID(as_uuid=True),
        ForeignKey("legal_review_documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    classified_type = Column(String(50), nullable=False)
    confidence_score = Column(Float, nullable=False)
    user_override_type = Column(String(50), nullable=True)
    scores = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    document = relationship("LegalReviewDocument", back_populates="classification_logs")
