"""Add agentic capability tables: agent_config, scan_watermarks, autonomous_audit_log, advisor_jobs, multi_step_cycles, multi_step_cycle_steps; add confidence_score and classification_method to security_alerts

Revision ID: 003_agentic_capabilities
Revises: 002_security_guardrails
Create Date: 2025-02-01 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


# revision identifiers, used by Alembic.
revision: str = "003_agentic_capabilities"
down_revision: Union[str, None] = "002_security_guardrails"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- agent_config ---
    op.create_table(
        "agent_config",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("agent_type", sa.String(50), nullable=False, unique=True),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("config_data", sa.JSON, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", sa.String(512), nullable=True),
    )

    # --- scan_watermarks ---
    op.create_table(
        "scan_watermarks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("watermark_type", sa.String(50), nullable=False, unique=True),
        sa.Column("last_processed_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    # --- autonomous_audit_log ---
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

    # --- advisor_jobs ---
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

    # --- multi_step_cycles ---
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

    # --- multi_step_cycle_steps ---
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

    # --- Add new columns to security_alerts ---
    op.add_column(
        "security_alerts",
        sa.Column("confidence_score", sa.Numeric(3, 2), nullable=True),
    )
    op.add_column(
        "security_alerts",
        sa.Column("classification_method", sa.String(30), nullable=False, server_default="regex_fallback"),
    )

    # --- Seed default AgentConfig rows ---
    op.execute(
        sa.text(
            "INSERT INTO agent_config (id, agent_type, enabled, config_data, updated_at, updated_by) VALUES "
            "(gen_random_uuid(), 'background_agent', false, "
            "'{\"scan_interval_seconds\": 300, \"max_records_per_cycle\": 500, \"scope_filters\": {}}'::jsonb, "
            "now(), NULL)"
        )
    )
    op.execute(
        sa.text(
            "INSERT INTO agent_config (id, agent_type, enabled, config_data, updated_at, updated_by) VALUES "
            "(gen_random_uuid(), 'multi_step_agent', false, "
            "'{\"max_steps_per_cycle\": 10, \"cooldown_seconds\": 300}'::jsonb, "
            "now(), NULL)"
        )
    )

    # --- Seed default ScanWatermark row ---
    op.execute(
        sa.text(
            "INSERT INTO scan_watermarks (id, watermark_type, last_processed_timestamp, updated_at) VALUES "
            "(gen_random_uuid(), 'background_agent', now(), now())"
        )
    )


def downgrade() -> None:
    # --- Remove columns from security_alerts ---
    op.drop_column("security_alerts", "classification_method")
    op.drop_column("security_alerts", "confidence_score")

    # --- Drop tables in reverse dependency order ---
    op.drop_table("multi_step_cycle_steps")
    op.drop_table("multi_step_cycles")
    op.drop_table("advisor_jobs")
    op.drop_table("autonomous_audit_log")
    op.drop_table("scan_watermarks")
    op.drop_table("agent_config")
