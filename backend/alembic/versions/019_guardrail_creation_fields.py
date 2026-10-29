"""Add real-Bedrock-Guardrail-creation fields to v2_agent_guardrail_recommendations.

Approving a Guardrail Recommender finding now calls the real Bedrock
CreateGuardrail (or UpdateGuardrail, when a guardrail from a prior approval
already exists) control-plane API. These columns persist the result of that
call so the UI can show the created guardrail's identity and so re-approving
a related finding can update the SAME guardrail instead of creating a
duplicate one.

Additive and nullable: existing rows get NULL (no guardrail created yet).

Revision ID: 019_guardrail_creation_fields
Revises: 018_demo_invoked_at
Create Date: 2026-08-06 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "019_guardrail_creation_fields"
down_revision: Union[str, None] = "018_demo_invoked_at"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE_NAME = "v2_agent_guardrail_recommendations"

_NEW_COLUMNS = [
    ("guardrail_id", sa.String(64)),
    ("guardrail_arn", sa.String(2048)),
    ("guardrail_version", sa.String(20)),
    ("creation_error", sa.Text()),
]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = {col["name"] for col in inspector.get_columns(_TABLE_NAME)}

    for name, col_type in _NEW_COLUMNS:
        if name not in existing_columns:
            op.add_column(_TABLE_NAME, sa.Column(name, col_type, nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = {col["name"] for col in inspector.get_columns(_TABLE_NAME)}

    for name, _col_type in _NEW_COLUMNS:
        if name in existing_columns:
            op.drop_column(_TABLE_NAME, name)
