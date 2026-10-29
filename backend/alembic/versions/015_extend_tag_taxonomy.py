"""Extend inference_profile_tag with the AgentName/CostCenter/UseCase/Owner/Project tags.

Adds five nullable columns to inference_profile_tag, extending the cost
attribution taxonomy from 3 dimensions (Application/Environment/Team) to 7.
All-additive: existing rows get NULL for the new columns (untagged for that
dimension), matching the graceful-degradation pattern the other 3 dimensions
already follow (ProfileTagService resolves best-effort and never fails on a
missing tag).

Forward-only and re-run safe: migrations run on every container start, so
each add_column is wrapped in an existence guard (mirrors migration 007's
pattern, extended with the guard style from 013/014).

Revision ID: 015_extend_tag_taxonomy
Revises: 014_generic_agent_findings
Create Date: 2026-08-05 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "015_extend_tag_taxonomy"
down_revision: Union[str, None] = "014_generic_agent_findings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NEW_COLUMNS = ("agent_name", "cost_center", "use_case", "owner", "project")


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = {
        col["name"] for col in inspector.get_columns("inference_profile_tag")
    }

    for column_name in _NEW_COLUMNS:
        if column_name not in existing_columns:
            op.add_column(
                "inference_profile_tag",
                sa.Column(column_name, sa.String(255), nullable=True),
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = {
        col["name"] for col in inspector.get_columns("inference_profile_tag")
    }

    for column_name in _NEW_COLUMNS:
        if column_name in existing_columns:
            op.drop_column("inference_profile_tag", column_name)
