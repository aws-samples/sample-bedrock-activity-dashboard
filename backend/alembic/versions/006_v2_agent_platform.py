"""Create all 17 v2_agent_* tables for the V2 Agent Platform.

Revision ID: 006_v2_agent_platform
Revises: 005_remove_multi_step_agent
Create Date: 2025-03-15 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


# revision identifiers, used by Alembic.
revision: str = "006_v2_agent_platform"
down_revision: Union[str, None] = "005_remove_multi_step_agent"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. v2_agent_configs
    op.create_table(
        "v2_agent_configs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("agent_name", sa.String(100), unique=True, nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("category", sa.String(100), nullable=False),
        sa.Column("model_id", sa.String(512), nullable=False),
        sa.Column("schedule_cron", sa.String(100), nullable=True),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    # 2. v2_agent_executions
    op.create_table(
        "v2_agent_executions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("agent_name", sa.String(100), nullable=False),
        sa.Column("trigger_type", sa.String(50), nullable=False),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("finding_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error_details", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    # 3. v2_agent_pii_findings
    op.create_table(
        "v2_agent_pii_findings",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("agent_name", sa.String(100), nullable=False),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_record_id", sa.String(255), nullable=False),
        sa.Column("pii_type", sa.String(100), nullable=False),
        sa.Column("confidence_score", sa.Float, nullable=False),
        sa.Column("detection_tier", sa.String(20), nullable=False),
        sa.Column("text_excerpt", sa.Text, nullable=True),
        sa.Column("user_identity", sa.String(512), nullable=True),
        sa.UniqueConstraint("source_record_id", "pii_type", name="uq_pii_source_type"),
    )

    # 4. v2_agent_cost_anomalies
    op.create_table(
        "v2_agent_cost_anomalies",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("agent_name", sa.String(100), nullable=False),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("anomaly_date", sa.Date, nullable=False),
        sa.Column("current_spend", sa.Numeric(20, 4), nullable=False),
        sa.Column("rolling_average", sa.Numeric(20, 4), nullable=False),
        sa.Column("threshold_multiplier", sa.Float, nullable=False),
        sa.Column("contributing_users", sa.JSON, nullable=True),
        sa.Column("team_id", UUID(as_uuid=True), nullable=True),
    )

    # 5. v2_agent_guardrail_recommendations
    op.create_table(
        "v2_agent_guardrail_recommendations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("agent_name", sa.String(100), nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="pending_review"),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recommendation", sa.JSON, nullable=False),
        sa.Column("based_on_findings", sa.JSON, nullable=True),
        sa.Column("rejection_reason", sa.Text, nullable=True),
    )

    # 6. v2_agent_model_cost_recommendations
    op.create_table(
        "v2_agent_model_cost_recommendations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("agent_name", sa.String(100), nullable=False),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("current_model", sa.String(512), nullable=False),
        sa.Column("suggested_model", sa.String(512), nullable=False),
        sa.Column("estimated_monthly_savings", sa.Numeric(20, 4), nullable=False),
        sa.Column("affected_user_count", sa.Integer, nullable=False),
        sa.Column("usage_pattern_summary", sa.Text, nullable=True),
    )

    # 7. v2_agent_prompt_quality_findings
    op.create_table(
        "v2_agent_prompt_quality_findings",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("agent_name", sa.String(100), nullable=False),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_identity", sa.String(512), nullable=False),
        sa.Column("anti_pattern_type", sa.String(100), nullable=False),
        sa.Column("prompt_excerpt", sa.Text, nullable=True),
        sa.Column("improvement_suggestion", sa.Text, nullable=True),
        sa.Column("quality_score", sa.Integer, nullable=True),
    )

    # 8. v2_agent_model_adoption_findings
    op.create_table(
        "v2_agent_model_adoption_findings",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("agent_name", sa.String(100), nullable=False),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_model", sa.String(512), nullable=False),
        sa.Column("target_model", sa.String(512), nullable=False),
        sa.Column("capability_comparison", sa.JSON, nullable=True),
        sa.Column("cost_delta", sa.Numeric(20, 4), nullable=True),
        sa.Column("recommendation_rationale", sa.Text, nullable=True),
        sa.Column("pricing_available", sa.Boolean, nullable=False, server_default="true"),
    )

    # 9. v2_agent_usage_forecasts
    op.create_table(
        "v2_agent_usage_forecasts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("agent_name", sa.String(100), nullable=False),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("forecast_period_days", sa.Integer, nullable=False),
        sa.Column("projected_tokens", sa.BigInteger, nullable=True),
        sa.Column("projected_cost", sa.Numeric(20, 4), nullable=True),
        sa.Column("confidence_lower", sa.Numeric(20, 4), nullable=True),
        sa.Column("confidence_upper", sa.Numeric(20, 4), nullable=True),
        sa.Column("methodology", sa.String(100), nullable=True),
        sa.Column("breakdown_by_team", sa.JSON, nullable=True),
        sa.Column("breakdown_by_model", sa.JSON, nullable=True),
    )

    # 10. v2_agent_residency_violations
    op.create_table(
        "v2_agent_residency_violations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("agent_name", sa.String(100), nullable=False),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_identity", sa.String(512), nullable=False),
        sa.Column("invocation_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("target_region", sa.String(50), nullable=False),
        sa.Column("approved_regions", sa.JSON, nullable=False),
        sa.Column("violation_severity", sa.String(20), nullable=False),
    )

    # 11. v2_agent_latency_error_findings
    op.create_table(
        "v2_agent_latency_error_findings",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("agent_name", sa.String(100), nullable=False),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metric_type", sa.String(50), nullable=False),
        sa.Column("current_value", sa.Float, nullable=False),
        sa.Column("baseline_value", sa.Float, nullable=False),
        sa.Column("affected_model", sa.String(512), nullable=True),
        sa.Column("affected_region", sa.String(50), nullable=True),
    )

    # 12. v2_agent_chargeback_reports
    op.create_table(
        "v2_agent_chargeback_reports",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("agent_name", sa.String(100), nullable=False),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("report_period_start", sa.Date, nullable=False),
        sa.Column("report_period_end", sa.Date, nullable=False),
        sa.Column("team_name", sa.String(255), nullable=False),
        sa.Column("total_cost", sa.Numeric(20, 4), nullable=False),
        sa.Column("cost_breakdown", sa.JSON, nullable=True),
    )

    # 13. v2_agent_onboarding_findings
    op.create_table(
        "v2_agent_onboarding_findings",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("agent_name", sa.String(100), nullable=False),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_identity", sa.String(512), nullable=False),
        sa.Column("team_assignment", sa.String(255), nullable=True),
        sa.Column("guidance_content", sa.Text, nullable=True),
        sa.UniqueConstraint("user_identity", name="uq_onboarding_user"),
    )

    # 14. v2_agent_policies (parent — must be created before policy_violations)
    op.create_table(
        "v2_agent_policies",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("policy_name", sa.String(255), nullable=False),
        sa.Column("policy_type", sa.String(100), nullable=False),
        sa.Column("policy_rules", sa.JSON, nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    # 15. v2_agent_policy_violations (child — FK to v2_agent_policies)
    op.create_table(
        "v2_agent_policy_violations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("agent_name", sa.String(100), nullable=False),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("policy_rule_id", UUID(as_uuid=True), sa.ForeignKey("v2_agent_policies.id"), nullable=False),
        sa.Column("user_identity", sa.String(512), nullable=False),
        sa.Column("invocation_details", sa.JSON, nullable=True),
        sa.Column("violation_severity", sa.String(20), nullable=False),
    )

    # 16. v2_agent_residency_policies
    op.create_table(
        "v2_agent_residency_policies",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("team_id", UUID(as_uuid=True), nullable=True),
        sa.Column("approved_regions", sa.JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    # 17. v2_agent_digest_findings
    op.create_table(
        "v2_agent_digest_findings",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("agent_name", sa.String(100), nullable=False),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("digest_period_start", sa.Date, nullable=False),
        sa.Column("digest_period_end", sa.Date, nullable=False),
        sa.Column("included_agent_summaries", sa.JSON, nullable=True),
        sa.Column("delivery_status", sa.String(50), nullable=False),
        sa.Column("recipient_list", sa.JSON, nullable=True),
        sa.Column("failed_agents", sa.JSON, nullable=True),
    )


def downgrade() -> None:
    # Drop in FK-safe order: children before parents
    op.drop_table("v2_agent_digest_findings")
    op.drop_table("v2_agent_residency_policies")
    op.drop_table("v2_agent_policy_violations")  # child — drop before v2_agent_policies
    op.drop_table("v2_agent_policies")            # parent
    op.drop_table("v2_agent_onboarding_findings")
    op.drop_table("v2_agent_chargeback_reports")
    op.drop_table("v2_agent_latency_error_findings")
    op.drop_table("v2_agent_residency_violations")
    op.drop_table("v2_agent_usage_forecasts")
    op.drop_table("v2_agent_model_adoption_findings")
    op.drop_table("v2_agent_prompt_quality_findings")
    op.drop_table("v2_agent_model_cost_recommendations")
    op.drop_table("v2_agent_guardrail_recommendations")
    op.drop_table("v2_agent_cost_anomalies")
    op.drop_table("v2_agent_pii_findings")
    op.drop_table("v2_agent_executions")
    op.drop_table("v2_agent_configs")
