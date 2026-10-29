"""Add inference_profile_arn to demo_activity_records.

Supports the "tagged application spend" Demo Data option
(demo_service.generate_demo_data(with_applications=True)): a portion of demo
jobs invoke Bedrock through a tagged application inference profile instead of
a bare model id, so Cost Governance's Spend-by-Application view has real
(non-"unknown") data to show. This column records that profile's ARN so
ProfileTagService can resolve its Application/Environment/Team tags via the
same path real CloudTrail/CloudWatch records use.

Additive and nullable: existing rows (and demo records generated without the
option) get NULL, which the enrichment pipeline already treats as "no
profile" -> "unknown" dimension, same as any other untagged record.

Revision ID: 022_demo_inference_profile_arn
Revises: 021_chat_message_user_identity
Create Date: 2026-10-05 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "022_demo_inference_profile_arn"
down_revision: Union[str, None] = "021_chat_message_user_identity"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE_NAME = "demo_activity_records"
_COLUMN_NAME = "inference_profile_arn"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = {col["name"] for col in inspector.get_columns(_TABLE_NAME)}

    if _COLUMN_NAME not in existing_columns:
        op.add_column(
            _TABLE_NAME,
            sa.Column(_COLUMN_NAME, sa.String(1024), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = {col["name"] for col in inspector.get_columns(_TABLE_NAME)}

    if _COLUMN_NAME in existing_columns:
        op.drop_column(_TABLE_NAME, _COLUMN_NAME)
