"""Add user_identity/user_name to chat_messages.

Agent Chat previously persisted every turn with no caller identity, and the
Activity_Record reconstruction in chat_message_service.py hardcoded a
"agent-chat"/"Agent Chat User" placeholder for every user — so all Agent Chat
usage was indistinguishable across users in Model Usage / Token Usage / Cost
tabs. This adds columns to capture the real authenticated Cognito identity
(``CurrentUser.identity`` / ``CurrentUser.username``) at write time.

Additive and nullable: existing rows get NULL and the read path
(chat_message_service._pair_to_record) falls back to the old placeholder
strings for them, so no backfill is required.

Revision ID: 021_chat_message_user_identity
Revises: 020_guardrail_published_version
Create Date: 2026-08-19 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "021_chat_message_user_identity"
down_revision: Union[str, None] = "020_guardrail_published_version"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE_NAME = "chat_messages"
_USER_IDENTITY_COLUMN = "user_identity"
_USER_NAME_COLUMN = "user_name"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = {col["name"] for col in inspector.get_columns(_TABLE_NAME)}

    if _USER_IDENTITY_COLUMN not in existing_columns:
        op.add_column(_TABLE_NAME, sa.Column(_USER_IDENTITY_COLUMN, sa.String(512), nullable=True))
    if _USER_NAME_COLUMN not in existing_columns:
        op.add_column(_TABLE_NAME, sa.Column(_USER_NAME_COLUMN, sa.String(255), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = {col["name"] for col in inspector.get_columns(_TABLE_NAME)}

    if _USER_NAME_COLUMN in existing_columns:
        op.drop_column(_TABLE_NAME, _USER_NAME_COLUMN)
    if _USER_IDENTITY_COLUMN in existing_columns:
        op.drop_column(_TABLE_NAME, _USER_IDENTITY_COLUMN)
