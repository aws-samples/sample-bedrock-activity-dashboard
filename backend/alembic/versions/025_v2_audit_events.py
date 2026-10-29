"""Add v2_audit_events: who approved/configured/uploaded/deleted what (AppSec F-1).

Revision ID: 025_v2_audit_events
Revises: 024_redact_agent_prompt_content
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "025_v2_audit_events"
down_revision: Union[str, Sequence[str], None] = "024_redact_agent_prompt_content"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table("v2_audit_events"):
        return  # idempotent
    op.create_table(
        "v2_audit_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor_identity", sa.String(512), nullable=False),
        sa.Column("actor_username", sa.String(255), nullable=True),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("details", sa.JSON(), nullable=True),
    )
    op.create_index("ix_v2_audit_events_created_at", "v2_audit_events", ["created_at"])
    op.create_index("ix_v2_audit_events_action", "v2_audit_events", ["action"])


def downgrade() -> None:
    op.drop_table("v2_audit_events")
