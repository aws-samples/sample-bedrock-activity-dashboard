"""Drop all multi-step agent subsystem tables (security_alerts, guardrail_*, agent_config, scan_watermarks, autonomous_audit_log, advisor_jobs, multi_step_cycles, multi_step_cycle_steps)

Revision ID: 005_remove_multi_step_agent
Revises: 004_remove_recs
Create Date: 2025-03-01 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


# revision identifiers, used by Alembic.
revision: str = "005_remove_multi_step_agent"
down_revision: Union[str, None] = "004_remove_recs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop tables in FK-safe order (children before parents)

    # --- Tables from migration 003 ---
    op.drop_table("multi_step_cycle_steps")
    op.drop_table("multi_step_cycles")
    op.drop_table("advisor_jobs")
    op.drop_table("autonomous_audit_log")
    op.drop_table("scan_watermarks")
    op.drop_table("agent_config")

    # --- Tables from migration 002 (children first) ---
    op.drop_table("guardrail_mappings")
    op.drop_table("guardrail_audit_log")
    op.drop_table("guardrail_suggestions")
    op.drop_table("security_alerts")


def downgrade() -> None:
    # Recreate all 10 tables in reverse order (parents before children)
    # Schemas match the original definitions from migrations 002 and 003

    # --- Tables from migration 002 (parents first) ---

    # security_alerts (from migration 002, with columns added in 003)
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
        sa.Column("confidence_score", sa.Numeric(3, 2), nullable=True),
        sa.Column("classification_method", sa.String(30), nullable=False, server_default="regex_fallback"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("activity_record_id", "sensitivity_category", "source_field", name="uq_alert_record_cat_field"),
    )

    # guardrail_suggestions (from migration 002)
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

    # guardrail_audit_log (from migration 002)
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

    # guardrail_mappings (from migration 002)
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

    # --- Tables from migration 003 ---

    # agent_config
    op.create_table(
        "agent_config",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("agent_type", sa.String(50), nullable=False, unique=True),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("config_data", sa.JSON, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", sa.String(512), nullable=True),
    )

    # scan_watermarks
    op.create_table(
        "scan_watermarks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("watermark_type", sa.String(50), nullable=False, unique=True),
        sa.Column("last_processed_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    # autonomous_audit_log
    op.create_table(
        "autonomous_audit_log",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("source_service", sa.String(50), nullable=False),
        sa.Column("action_type", sa.String(100), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("input_summary", sa.Text, nullable=True),
        sa.Column("output_summary", sa.Text, nullable=True),
        sa.Column("duration_ms", sa.Integer, nullable=True),
        sa.Column("outcome", sa.String(50), nullable=False),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("user_identity", sa.String(512), nullable=True),
        sa.Column("metadata", sa.JSON, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    # advisor_jobs
    op.create_table(
        "advisor_jobs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("warning", sa.Text, nullable=True),
        sa.Column("standard_suggestions_count", sa.Integer, nullable=True),
        sa.Column("custom_suggestions_count", sa.Integer, nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    # multi_step_cycles
    op.create_table(
        "multi_step_cycles",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("status", sa.String(30), nullable=False, server_default="running"),
        sa.Column("current_step_number", sa.Integer, nullable=False, server_default="0"),
        sa.Column("max_steps", sa.Integer, nullable=False, server_default="10"),
        sa.Column("alerts_detected", sa.Integer, nullable=False, server_default="0"),
        sa.Column("suggestions_generated", sa.Integer, nullable=False, server_default="0"),
        sa.Column("suggestions_approved", sa.Integer, nullable=False, server_default="0"),
        sa.Column("suggestions_rejected", sa.Integer, nullable=False, server_default="0"),
        sa.Column("guardrails_created", sa.Integer, nullable=False, server_default="0"),
        sa.Column("guardrails_verified", sa.Integer, nullable=False, server_default="0"),
        sa.Column("errors_encountered", sa.Integer, nullable=False, server_default="0"),
        sa.Column("summary", sa.JSON, nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )

    # multi_step_cycle_steps
    op.create_table(
        "multi_step_cycle_steps",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("cycle_id", UUID(as_uuid=True), sa.ForeignKey("multi_step_cycles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("step_number", sa.Integer, nullable=False),
        sa.Column("step_type", sa.String(50), nullable=False),
        sa.Column("input_summary", sa.Text, nullable=True),
        sa.Column("output_summary", sa.Text, nullable=True),
        sa.Column("duration_ms", sa.Integer, nullable=True),
        sa.Column("outcome", sa.String(30), nullable=False),
        sa.Column("pending_suggestion_id", UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
