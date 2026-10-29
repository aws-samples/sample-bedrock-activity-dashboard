"""Add app_runtime_settings key/value table for admin-managed runtime toggles.

Introduces a simple KV surface that lets admins flip behavior that must
survive container restarts and span every task without rebuilding/redeploying
the backend image. First consumer: the Prompt History on/off toggle, which
controls whether Agent Chat persists user and assistant message text (and
whether /api/activity/prompts surfaces anything beyond CloudTrail + S3
ingested records).

Idempotent: create_table only runs if the table doesn't already exist, so a
database that was already partway through this migration (or a re-run after
a failed deploy) still converges cleanly.

Revision ID: 021_app_runtime_settings
Revises: 020_guardrail_published_version
Create Date: 2026-10-06 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "021_app_runtime_settings"
down_revision: Union[str, None] = "020_guardrail_published_version"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE_NAME = "app_runtime_settings"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if _TABLE_NAME not in existing_tables:
        op.create_table(
            _TABLE_NAME,
            sa.Column("key", sa.String(100), primary_key=True),
            sa.Column("value", sa.Text(), nullable=False),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.Column("updated_by", sa.String(512), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if _TABLE_NAME in existing_tables:
        op.drop_table(_TABLE_NAME)
