"""Remove the budget_thresholds table (Thresholds feature removed).

Drops the budget_thresholds table and its uq_entity_period unique constraint
(dropping the table removes the constraint). Forward-only and re-run safe:
the table may already be absent on a fresh environment.

Revision ID: 012_remove_thresholds
Revises: 011_demo_activity_records
Create Date: 2026-09-01 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "012_remove_thresholds"
down_revision: Union[str, None] = "011_demo_activity_records"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Idempotency guard: migrations run on every container start, and the table
    # is already gone on fresh environments. Only drop if present.
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "budget_thresholds" in inspector.get_table_names():
        op.drop_table("budget_thresholds")


def downgrade() -> None:
    # Recreate the original budget_thresholds schema (mirrors migration 001).
    op.create_table(
        "budget_thresholds",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("entity_type", sa.String(50), nullable=False),
        sa.Column("entity_id", sa.String(512), nullable=False),
        sa.Column("threshold_amount", sa.Numeric(20, 4), nullable=False),
        sa.Column("time_period", sa.String(50), nullable=False),
        sa.Column("notification_channel", sa.String(512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("entity_type", "entity_id", "time_period", name="uq_entity_period"),
    )
