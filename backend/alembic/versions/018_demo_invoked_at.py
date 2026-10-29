"""Add invoked_at to demo_activity_records.

Every demo record generated is now a genuine Bedrock invocation (see
demo_service.py). That real call independently gets ingested via CloudWatch
Logs/S3/CloudTrail under the backend's own task-role identity, so without a
way to match it, the SAME invocation appears twice in the activity feed: once
as the nicely-labeled demo record (staged display timestamp, fake demo user),
once as a bare task-role record at the real invocation time. This mirrors an
existing problem already solved for Agent Chat via
``suppress_chat_placeholders_with_real_invocation`` (matched by model id +
timestamp proximity).

This column records the REAL time the invocation happened (as opposed to
``timestamp``, which is staged for demo storytelling), so the same
matching/suppression approach can be applied to demo records.

Additive and nullable: existing rows get NULL (no suppression attempted for
demo batches generated before this column existed).

Revision ID: 018_demo_invoked_at
Revises: 017_agentcore_discovery
Create Date: 2026-08-06 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "018_demo_invoked_at"
down_revision: Union[str, None] = "017_agentcore_discovery"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE_NAME = "demo_activity_records"
_COLUMN_NAME = "invoked_at"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = {col["name"] for col in inspector.get_columns(_TABLE_NAME)}

    if _COLUMN_NAME not in existing_columns:
        op.add_column(
            _TABLE_NAME,
            sa.Column(_COLUMN_NAME, sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = {col["name"] for col in inspector.get_columns(_TABLE_NAME)}

    if _COLUMN_NAME in existing_columns:
        op.drop_column(_TABLE_NAME, _COLUMN_NAME)
