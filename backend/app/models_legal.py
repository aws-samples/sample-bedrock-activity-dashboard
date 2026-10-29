"""SQLAlchemy models for the Legal Review App."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# 1. legal_review_documents — uploaded documents with extracted text
# ---------------------------------------------------------------------------
class LegalReviewDocument(Base):
    __tablename__ = "legal_review_documents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(String(512), nullable=False, index=True)
    filename = Column(String(512), nullable=False)
    file_size = Column(Integer, nullable=False)
    content_type = Column(String(100), nullable=False)
    extracted_text = Column(Text, nullable=False)
    section_structure = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    review_requests = relationship(
        "LegalReviewRequest", back_populates="document", cascade="all, delete-orphan"
    )
    classification_logs = relationship(
        "LearningClassificationLog", back_populates="document", cascade="all, delete-orphan"
    )


# ---------------------------------------------------------------------------
# 2. legal_review_requests — review submissions linked to documents
# ---------------------------------------------------------------------------
class LegalReviewRequest(Base):
    __tablename__ = "legal_review_requests"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(String(512), nullable=False, index=True)
    document_id = Column(
        UUID(as_uuid=True),
        ForeignKey("legal_review_documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    review_command = Column(String(100), nullable=False)
    status = Column(String(50), nullable=False)
    error_message = Column(Text, nullable=True)
    retry_count = Column(Integer, nullable=False, server_default="0")
    model_id = Column(String(100), nullable=True)
    prompt_version_id = Column(
        UUID(as_uuid=True),
        ForeignKey("learning_prompt_versions.id"),
        nullable=True,
    )
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    document = relationship("LegalReviewDocument", back_populates="review_requests")
    findings = relationship(
        "LegalReviewFinding", back_populates="review_request", cascade="all, delete-orphan"
    )
    prompt_version = relationship("LearningPromptVersion")


# ---------------------------------------------------------------------------
# 3. legal_review_findings — individual findings from a review
# ---------------------------------------------------------------------------
class LegalReviewFinding(Base):
    __tablename__ = "legal_review_findings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    review_request_id = Column(
        UUID(as_uuid=True),
        ForeignKey("legal_review_requests.id", ondelete="CASCADE"),
        nullable=False,
    )
    risk_level = Column(String(20), nullable=False)
    description = Column(Text, nullable=False)
    affected_clause = Column(Text, nullable=True)
    recommended_action = Column(Text, nullable=True)
    raw_finding = Column(JSON, nullable=False)
    sort_order = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    review_request = relationship("LegalReviewRequest", back_populates="findings")
    feedback_records = relationship(
        "LearningFeedback", back_populates="finding", cascade="all, delete-orphan"
    )
