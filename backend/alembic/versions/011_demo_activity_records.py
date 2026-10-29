"""Add demo_activity_records table for on-demand demo data.

Revision ID: 011_demo_activity_records
Revises: 010_learning_agent
Create Date: 2026-08-01 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "011_demo_activity_records"
down_revision: Union[str, None] = "010_learning_agent"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Idempotency guard: only create the table if it isn't already present.
    # Migrations run on every container start; this keeps re-runs safe if the
    # table was created out of band.
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "demo_activity_records" in inspector.get_table_names():
        return

    op.create_table(
        "demo_activity_records",
        sa.Column("id", sa.String(255), primary_key=True),
        sa.Column("timestamp", sa.String(64), nullable=False),
        sa.Column("user_identity", sa.String(512), nullable=False),
        sa.Column("user_name", sa.String(255), nullable=False),
        sa.Column("model_id", sa.String(512), nullable=False),
        sa.Column("source_application", sa.String(255), nullable=True),
        sa.Column("source_ip", sa.String(64), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cache_read_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cache_write_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("prompt_text", sa.Text(), nullable=True),
        sa.Column("response_text", sa.Text(), nullable=True),
        sa.Column("pii_types", sa.JSON(), nullable=True),
        sa.Column("is_real_invocation", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("batch_id", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_demo_activity_records_batch_id",
        "demo_activity_records",
        ["batch_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_demo_activity_records_batch_id",
        table_name="demo_activity_records",
    )
    op.drop_table("demo_activity_records")
