"""Add learning agent tables.

Revision ID: 010_learning_agent
Revises: 009_legal_review
Create Date: 2026-07-15 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "010_learning_agent"
down_revision: Union[str, None] = "009_legal_review"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. learning_prompt_versions (must exist before tables that FK to it)
    op.create_table(
        "learning_prompt_versions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("review_command", sa.String(100), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("prompt_text", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_by", sa.String(512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_learning_prompt_versions_review_command",
        "learning_prompt_versions",
        ["review_command"],
    )
    # Partial unique index: only one active version per review_command
    op.create_index(
        "ix_learning_prompt_versions_active_command",
        "learning_prompt_versions",
        ["review_command"],
        unique=True,
        postgresql_where=sa.text("is_active = true"),
    )

    # 2. learning_feedback (FKs to legal_review_findings and learning_prompt_versions)
    op.create_table(
        "learning_feedback",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "finding_id",
            UUID(as_uuid=True),
            sa.ForeignKey("legal_review_findings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", sa.String(512), nullable=False),
        sa.Column("rating", sa.String(20), nullable=False),
        sa.Column("classification", sa.String(30), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("model_id", sa.String(100), nullable=False),
        sa.Column(
            "prompt_version_id",
            UUID(as_uuid=True),
            sa.ForeignKey("learning_prompt_versions.id"),
            nullable=True,
        ),
        sa.Column("document_type", sa.String(50), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("finding_id", "user_id", name="uq_learning_feedback_finding_user"),
    )
    op.create_index("ix_learning_feedback_user_id", "learning_feedback", ["user_id"])

    # 3. learning_performance_metrics
    op.create_table(
        "learning_performance_metrics",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("scope_type", sa.String(50), nullable=False),
        sa.Column("model_id", sa.String(100), nullable=True),
        sa.Column("document_type", sa.String(50), nullable=True),
        sa.Column(
            "prompt_version_id",
            UUID(as_uuid=True),
            sa.ForeignKey("learning_prompt_versions.id"),
            nullable=True,
        ),
        sa.Column("accuracy_score", sa.Float(), nullable=False),
        sa.Column("false_positive_rate", sa.Float(), nullable=False),
        sa.Column("total_rated", sa.Integer(), nullable=False),
        sa.Column("positive_count", sa.Integer(), nullable=False),
        sa.Column("negative_count", sa.Integer(), nullable=False),
        sa.Column("false_positive_count", sa.Integer(), nullable=False),
        sa.Column("is_low_confidence", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
    )

    # 4. learning_prompt_suggestions
    op.create_table(
        "learning_prompt_suggestions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("review_command", sa.String(100), nullable=False),
        sa.Column("pattern_description", sa.Text(), nullable=False),
        sa.Column("suggested_prompt_change", sa.Text(), nullable=False),
        sa.Column("supporting_feedback_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by", sa.String(512), nullable=True),
    )

    # 5. learning_classification_logs (FK to legal_review_documents)
    op.create_table(
        "learning_classification_logs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "document_id",
            UUID(as_uuid=True),
            sa.ForeignKey("legal_review_documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("classified_type", sa.String(50), nullable=False),
        sa.Column("confidence_score", sa.Float(), nullable=False),
        sa.Column("user_override_type", sa.String(50), nullable=True),
        sa.Column("scores", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    # 6. Add new nullable columns to legal_review_requests
    op.add_column(
        "legal_review_requests",
        sa.Column("model_id", sa.String(100), nullable=True),
    )
    op.add_column(
        "legal_review_requests",
        sa.Column(
            "prompt_version_id",
            UUID(as_uuid=True),
            sa.ForeignKey("learning_prompt_versions.id"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    # Drop columns from legal_review_requests first
    op.drop_column("legal_review_requests", "prompt_version_id")
    op.drop_column("legal_review_requests", "model_id")

    # Drop tables in reverse FK-safe order
    op.drop_table("learning_classification_logs")
    op.drop_table("learning_prompt_suggestions")
    op.drop_table("learning_performance_metrics")
    op.drop_table("learning_feedback")
    op.drop_index("ix_learning_prompt_versions_active_command", table_name="learning_prompt_versions")
    op.drop_index("ix_learning_prompt_versions_review_command", table_name="learning_prompt_versions")
    op.drop_table("learning_prompt_versions")
