"""Add the Department tag to inference_profile_tag.

Adds a single nullable `department` column to `inference_profile_tag`,
extending the cost-attribution taxonomy from 8 dimensions
(Application/Environment/Team/AgentName/CostCenter/UseCase/Owner/Project) to 9.
Additive: existing rows get NULL for the new column (untagged for that
dimension), matching the same graceful-degradation pattern the other extended
dimensions already follow (ProfileTagService resolves best-effort and never
fails on a missing tag).

Forward-only and re-run safe: migrations run on every container start, so the
add_column is wrapped in an existence guard (mirrors migration 015's pattern).

Revision ID: 016_add_department_tag
Revises: 015_extend_tag_taxonomy
Create Date: 2026-08-06 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "016_add_department_tag"
down_revision: Union[str, None] = "015_extend_tag_taxonomy"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLUMN_NAME = "department"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = {
        col["name"] for col in inspector.get_columns("inference_profile_tag")
    }

    if _COLUMN_NAME not in existing_columns:
        op.add_column(
            "inference_profile_tag",
            sa.Column(_COLUMN_NAME, sa.String(255), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = {
        col["name"] for col in inspector.get_columns("inference_profile_tag")
    }

    if _COLUMN_NAME in existing_columns:
        op.drop_column("inference_profile_tag", _COLUMN_NAME)
