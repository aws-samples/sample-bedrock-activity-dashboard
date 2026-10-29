"""Merge the two heads at 021.

Both ``021_app_runtime_settings`` (added on this branch for the Prompt History
toggle KV table) and ``021_chat_message_user_identity`` (added on the main
branch for per-caller Bedrock request attribution) list
``020_guardrail_published_version`` as their ``down_revision``. ``022`` only
chains off the chat-identity revision, so ``alembic upgrade head`` sees two
heads and refuses to run.

This is a no-op merge revision that unites both paths. Alembic applies
``021_app_runtime_settings``, ``021_chat_message_user_identity``, and ``022``
in whatever order it chooses, then lands here with a single head going
forward.

Revision ID: 023_merge_heads
Revises: 021_app_runtime_settings, 022_demo_inference_profile_arn
Create Date: 2026-10-07 17:15:00.000000
"""
from typing import Sequence, Union

revision: str = "023_merge_heads"
down_revision: Union[str, Sequence[str], None] = (
    "021_app_runtime_settings",
    "022_demo_inference_profile_arn",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """No schema change — this revision only reunites the two heads."""


def downgrade() -> None:
    """No-op; downgrade splits back into two heads which Alembic handles."""
