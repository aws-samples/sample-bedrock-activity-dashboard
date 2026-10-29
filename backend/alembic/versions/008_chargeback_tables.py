"""Add chargeback tables and is_agentic column.

Revision ID: 008_chargeback_tables
Revises: 007_add_agentcore_agent_id
Create Date: 2026-06-15 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "008_chargeback_tables"
down_revision: Union[str, None] = "007_add_agentcore_agent_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add is_agentic column to v2_agent_configs
    op.add_column(
        "v2_agent_configs",
        sa.Column("is_agentic", sa.Boolean(), server_default="false", nullable=False),
    )

    # csv_uploads
    op.create_table(
        "csv_uploads",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("filename", sa.String(512), nullable=False),
        sa.Column("billing_period", sa.String(7), nullable=True),
        sa.Column("csv_content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="pending"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("warning_message", sa.Text(), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False),
    )

    # csv_bedrock_line_items
    op.create_table(
        "csv_bedrock_line_items",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("upload_id", UUID(as_uuid=True), sa.ForeignKey("csv_uploads.id", ondelete="CASCADE"), nullable=False),
        sa.Column("service", sa.String(255), nullable=False),
        sa.Column("usage_type", sa.String(512), nullable=True),
        sa.Column("unblended_cost", sa.Numeric(20, 10), nullable=False),
        sa.Column("raw_row", sa.JSON(), nullable=False),
    )

    # chargeback_reports
    op.create_table(
        "chargeback_reports",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("upload_id", UUID(as_uuid=True), sa.ForeignKey("csv_uploads.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("billing_period", sa.String(7), nullable=False),
        sa.Column("total_actual_cost", sa.Numeric(20, 4), nullable=False),
        sa.Column("total_estimated_cost", sa.Numeric(20, 4), nullable=False),
        sa.Column("total_variance", sa.Numeric(20, 4), nullable=False),
        sa.Column("insights", sa.Text(), nullable=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
    )

    # chargeback_report_lines
    op.create_table(
        "chargeback_report_lines",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("report_id", UUID(as_uuid=True), sa.ForeignKey("chargeback_reports.id", ondelete="CASCADE"), nullable=False),
        sa.Column("entity_type", sa.String(10), nullable=False),
        sa.Column("entity_name", sa.String(512), nullable=False),
        sa.Column("estimated_cost", sa.Numeric(20, 4), nullable=False),
        sa.Column("allocated_actual_cost", sa.Numeric(20, 4), nullable=False),
        sa.Column("variance", sa.Numeric(20, 4), nullable=False),
        sa.Column("percentage_share", sa.Numeric(7, 4), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("chargeback_report_lines")
    op.drop_table("chargeback_reports")
    op.drop_table("csv_bedrock_line_items")
    op.drop_table("csv_uploads")
    op.drop_column("v2_agent_configs", "is_agentic")
