"""Add legal review tables.

Revision ID: 009_legal_review
Revises: 008_chargeback_tables
Create Date: 2026-07-01 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "009_legal_review"
down_revision: Union[str, None] = "008_chargeback_tables"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. legal_review_documents (parent — must exist before requests)
    op.create_table(
        "legal_review_documents",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", sa.String(512), nullable=False),
        sa.Column("filename", sa.String(512), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("extracted_text", sa.Text(), nullable=False),
        sa.Column("section_structure", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_legal_review_documents_user_id", "legal_review_documents", ["user_id"])

    # 2. legal_review_requests (child of documents, parent of findings)
    op.create_table(
        "legal_review_requests",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", sa.String(512), nullable=False),
        sa.Column("document_id", UUID(as_uuid=True), sa.ForeignKey("legal_review_documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("review_command", sa.String(100), nullable=False),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_legal_review_requests_user_id", "legal_review_requests", ["user_id"])

    # 3. legal_review_findings (child of requests)
    op.create_table(
        "legal_review_findings",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("review_request_id", UUID(as_uuid=True), sa.ForeignKey("legal_review_requests.id", ondelete="CASCADE"), nullable=False),
        sa.Column("risk_level", sa.String(20), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("affected_clause", sa.Text(), nullable=True),
        sa.Column("recommended_action", sa.Text(), nullable=True),
        sa.Column("raw_finding", sa.JSON(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    # Drop in FK-safe order: children before parents
    op.drop_table("legal_review_findings")
    op.drop_table("legal_review_requests")
    op.drop_table("legal_review_documents")
