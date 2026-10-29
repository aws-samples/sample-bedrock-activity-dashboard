"""Add agentcore_agent_id column to v2_agent_configs.

Revision ID: 007_add_agentcore_agent_id
Revises: 006_v2_agent_platform
Create Date: 2026-04-03 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "007_add_agentcore_agent_id"
down_revision: Union[str, None] = "006_v2_agent_platform"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("v2_agent_configs", sa.Column("agentcore_agent_id", sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column("v2_agent_configs", "agentcore_agent_id")
