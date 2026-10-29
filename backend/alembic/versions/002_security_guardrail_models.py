"""Add security_alerts, guardrail_suggestions, guardrail_audit_log, and guardrail_mappings tables

Revision ID: 002_security_guardrails
Revises: 001_initial
Create Date: 2025-01-15 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


# revision identifiers, used by Alembic.
revision: str = "002_security_guardrails"
down_revision: Union[str, None] = "001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- security_alerts ---
    op.create_table(
        "security_alerts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("activity_record_id", sa.String(512), nullable=False, index=True),
        sa.Column("sensitivity_category", sa.String(50), nullable=False),
        sa.Column("severity_level", sa.String(20), nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("matched_snippet", sa.Text, nullable=False),
        sa.Column("source_field", sa.String(20), nullable=False),
        sa.Column("user_identity", sa.String(512), nullable=False),
        sa.Column("user_name", sa.String(255), nullable=False),
        sa.Column("model_id", sa.String(512), nullable=False),
        sa.Column("source_application", sa.String(255), nullable=False, server_default="Unknown"),
        sa.Column("record_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("guardrail_id", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("activity_record_id", "sensitivity_category", "source_field", name="uq_alert_record_cat_field"),
    )

    # --- guardrail_suggestions ---
    op.create_table(
        "guardrail_suggestions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("suggestion_type", sa.String(20), nullable=False),
        sa.Column("policy_config", sa.JSON, nullable=False),
        sa.Column("matching_alert_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("confidence", sa.String(20), nullable=False),
        sa.Column("example_record_ids", sa.JSON, nullable=True),
        sa.Column("rationale", sa.Text, nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("rejection_reason", sa.Text, nullable=True),
        sa.Column("guardrail_id", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    # --- guardrail_audit_log ---
    op.create_table(
        "guardrail_audit_log",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("suggestion_id", UUID(as_uuid=True), sa.ForeignKey("guardrail_suggestions.id"), nullable=True),
        sa.Column("alert_id", UUID(as_uuid=True), sa.ForeignKey("security_alerts.id"), nullable=True),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("user_identity", sa.String(512), nullable=False),
        sa.Column("guardrail_id", sa.String(255), nullable=True),
        sa.Column("modification_details", sa.JSON, nullable=True),
        sa.Column("rejection_reason", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    # --- guardrail_mappings ---
    op.create_table(
        "guardrail_mappings",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("guardrail_id", sa.String(255), nullable=False, index=True),
        sa.Column("guardrail_version", sa.String(50), nullable=False),
        sa.Column("alert_id", UUID(as_uuid=True), sa.ForeignKey("security_alerts.id"), nullable=True),
        sa.Column("suggestion_id", UUID(as_uuid=True), sa.ForeignKey("guardrail_suggestions.id"), nullable=True),
        sa.Column("policy_config", sa.JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("guardrail_mappings")
    op.drop_table("guardrail_audit_log")
    op.drop_table("guardrail_suggestions")
    op.drop_table("security_alerts")
