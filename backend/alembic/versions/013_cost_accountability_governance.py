"""Cost accountability & governance tables.

Adds the five new tables that back the Cost Accountability & Governance
feature: cur_billed_cost (Tier A billed rows), inference_profile_tag (ARN->tags
cache), workload_outcome_override (admin outcome counts),
attribution_target_config (singleton governance config), and
cur_ingestion_state (CUR ingestion tracking).

Forward-only and re-run safe: migrations run on every container start, so each
create/drop is wrapped in an existence guard (mirrors migrations 011/012).

Revision ID: 013_cost_acct_governance
Revises: 012_remove_thresholds
Create Date: 2026-10-01 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "013_cost_acct_governance"
down_revision: Union[str, None] = "012_remove_thresholds"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Idempotency guards: only create tables that aren't already present.
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = set(inspector.get_table_names())

    # --- cur_billed_cost (Tier A authoritative billed rows) ---
    if "cur_billed_cost" not in existing:
        op.create_table(
            "cur_billed_cost",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("billing_period", sa.String(7), nullable=False),
            sa.Column("usage_date", sa.Date, nullable=False),
            sa.Column("iam_principal", sa.String(512), nullable=False),
            sa.Column("team_tag", sa.String(255), nullable=False),
            sa.Column("resource_id", sa.String(1024), nullable=True),
            sa.Column("model_id", sa.String(512), nullable=False),
            sa.Column("billed_cost", sa.Numeric(20, 10), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "billing_period",
                "usage_date",
                "iam_principal",
                "team_tag",
                "model_id",
                name="uq_cur_billed_key",
            ),
        )
        op.create_index("ix_cur_billed_usage_date", "cur_billed_cost", ["usage_date"])
        op.create_index("ix_cur_billed_principal", "cur_billed_cost", ["iam_principal"])

    # --- inference_profile_tag (ARN -> Application/Environment/Team cache) ---
    if "inference_profile_tag" not in existing:
        op.create_table(
            "inference_profile_tag",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("profile_arn", sa.String(1024), nullable=False),
            sa.Column("application", sa.String(255), nullable=True),
            sa.Column("environment", sa.String(64), nullable=True),
            sa.Column("team", sa.String(255), nullable=True),
            sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("source", sa.String(20), nullable=False),
            sa.UniqueConstraint("profile_arn", name="uq_profile_arn"),
        )

    # --- workload_outcome_override (admin-supplied outcome counts) ---
    if "workload_outcome_override" not in existing:
        op.create_table(
            "workload_outcome_override",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("application", sa.String(255), nullable=False),
            sa.Column("period_start", sa.Date, nullable=False),
            sa.Column("period_end", sa.Date, nullable=False),
            sa.Column("outcome_count", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "application",
                "period_start",
                "period_end",
                name="uq_outcome_workload_period",
            ),
        )

    # --- attribution_target_config (singleton governance config) ---
    if "attribution_target_config" not in existing:
        op.create_table(
            "attribution_target_config",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("unattributed_target", sa.Numeric(5, 4), nullable=False),
            sa.Column("staleness_hours_override", sa.Integer(), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )

    # --- cur_ingestion_state (ingestion tracking) ---
    if "cur_ingestion_state" not in existing:
        op.create_table(
            "cur_ingestion_state",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("covered_through", sa.Date, nullable=True),
            sa.Column("latest_billing_period", sa.String(7), nullable=True),
            sa.Column("assembly_id", sa.String(255), nullable=True),
            sa.Column("status", sa.String(50), nullable=False),
            sa.Column("error_detail", sa.Text(), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )


def downgrade() -> None:
    # Drop in reverse dependency order; guard each so re-runs stay safe.
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = set(inspector.get_table_names())

    if "cur_ingestion_state" in existing:
        op.drop_table("cur_ingestion_state")
    if "attribution_target_config" in existing:
        op.drop_table("attribution_target_config")
    if "workload_outcome_override" in existing:
        op.drop_table("workload_outcome_override")
    if "inference_profile_tag" in existing:
        op.drop_table("inference_profile_tag")
    if "cur_billed_cost" in existing:
        op.drop_index("ix_cur_billed_principal", table_name="cur_billed_cost")
        op.drop_index("ix_cur_billed_usage_date", table_name="cur_billed_cost")
        op.drop_table("cur_billed_cost")
