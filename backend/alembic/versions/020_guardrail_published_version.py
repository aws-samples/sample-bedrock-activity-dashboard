"""Add guardrail_published_version to v2_agent_guardrail_recommendations.

Real enforcement (wiring guardrailConfig into this dashboard's own Bedrock
calls — Agent Chat, Demo Data) must reference a stable, numbered guardrail
version rather than the mutable DRAFT version (DRAFT keeps changing as more
findings get approved). After each successful create/update, the service now
also calls CreateGuardrailVersion and records the resulting version number
here so callers can look up "the current published version to use".

Additive and nullable: existing rows get NULL (no published version yet).

Revision ID: 020_guardrail_published_version
Revises: 019_guardrail_creation_fields
Create Date: 2026-08-06 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "020_guardrail_published_version"
down_revision: Union[str, None] = "019_guardrail_creation_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE_NAME = "v2_agent_guardrail_recommendations"
_COLUMN_NAME = "guardrail_published_version"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = {col["name"] for col in inspector.get_columns(_TABLE_NAME)}

    if _COLUMN_NAME not in existing_columns:
        op.add_column(_TABLE_NAME, sa.Column(_COLUMN_NAME, sa.String(20), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = {col["name"] for col in inspector.get_columns(_TABLE_NAME)}

    if _COLUMN_NAME in existing_columns:
        op.drop_column(_TABLE_NAME, _COLUMN_NAME)
