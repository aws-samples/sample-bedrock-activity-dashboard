"""Drop recommendations table (removed feature)

Revision ID: 004_remove_recs
Revises: 003_agentic_capabilities
Create Date: 2025-02-15 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


# revision identifiers, used by Alembic.
revision: str = "004_remove_recs"
down_revision: Union[str, None] = "003_agentic_capabilities"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table("recommendations")


def downgrade() -> None:
    op.create_table(
        "recommendations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("type", sa.String(100), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("affected_entity_type", sa.String(50), nullable=False),
        sa.Column("affected_entity_id", sa.String(512), nullable=False),
        sa.Column("estimated_impact", sa.Numeric(20, 4), nullable=True),
        sa.Column("suggested_action", sa.Text, nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="active"),
        sa.Column("metadata", sa.JSON, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True),
    )
