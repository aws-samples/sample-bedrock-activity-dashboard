"""Generic agent findings table.

Adds v2_agent_generic_findings — an agent-agnostic finding surface for
operational/informational agents (e.g. cur-ingestion) whose runs produce a
single status summary rather than domain-shaped findings. Without this table
the CUR Ingestion agent's informational finding had no persistence target and
was silently dropped (UI always showed 0 findings).

Forward-only and re-run safe: migrations run on every container start, so the
create/drop is wrapped in an existence guard (mirrors migration 013).

Revision ID: 014_generic_agent_findings
Revises: 013_cost_acct_governance
Create Date: 2026-07-23 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "014_generic_agent_findings"
down_revision: Union[str, None] = "013_cost_acct_governance"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Idempotency guard: only create the table if it isn't already present.
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = set(inspector.get_table_names())

    if "v2_agent_generic_findings" not in existing:
        op.create_table(
            "v2_agent_generic_findings",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("agent_name", sa.String(100), nullable=False),
            sa.Column("status", sa.String(50), nullable=False),
            sa.Column("severity", sa.String(20), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("title", sa.String(512), nullable=False),
            sa.Column("details", sa.JSON(), nullable=True),
        )
        op.create_index(
            "ix_v2_generic_findings_agent",
            "v2_agent_generic_findings",
            ["agent_name"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = set(inspector.get_table_names())

    if "v2_agent_generic_findings" in existing:
        op.drop_index(
            "ix_v2_generic_findings_agent", table_name="v2_agent_generic_findings"
        )
        op.drop_table("v2_agent_generic_findings")
