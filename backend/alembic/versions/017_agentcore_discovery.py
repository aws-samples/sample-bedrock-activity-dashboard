"""Create v2_discovered_agentcore_runtimes for account-wide Runtime discovery.

Adds a single new table populated by AgentCoreDiscoveryService's periodic
scan (bedrock-agentcore-control ListAgentRuntimes across every scanned
region). Purely additive and read-only from the rest of the app's
perspective — no existing table or column is touched, and nothing here
overlaps with v2_agent_configs (the governed 14-agent registry).

Forward-only and re-run safe: migrations run on every container start, so
op.create_table is wrapped in an existence guard (mirrors migration 016's
add_column guard pattern, extended to create_table).

Revision ID: 017_agentcore_discovery
Revises: 016_add_department_tag
Create Date: 2026-08-08 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "017_agentcore_discovery"
down_revision: Union[str, None] = "016_add_department_tag"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE_NAME = "v2_discovered_agentcore_runtimes"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table(_TABLE_NAME):
        op.create_table(
            _TABLE_NAME,
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("region", sa.String(20), nullable=False),
            sa.Column("agent_runtime_arn", sa.String(512), nullable=False, unique=True),
            sa.Column("agent_runtime_id", sa.String(255), nullable=False),
            sa.Column("agent_runtime_name", sa.String(255), nullable=False),
            sa.Column("status", sa.String(50), nullable=True),
            sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("last_scan_id", sa.String(64), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table(_TABLE_NAME):
        op.drop_table(_TABLE_NAME)
