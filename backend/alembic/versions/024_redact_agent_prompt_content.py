"""Scrub raw prompt text previously stored in agent findings (AppSec F-2).

Revision ID: 024_redact_agent_prompt_content
Revises: 023_merge_heads
"""
import re
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "024_redact_agent_prompt_content"
down_revision: Union[str, Sequence[str], None] = "023_merge_heads"
branch_labels = None
depends_on = None

# Legacy model-cost-optimizer summaries embedded a per-user sample as
#   "  • user: N calls (e.g. "<prompt text>")"
# at the end of the line. The sample is lazily matched up to the first `")`
# that ends a line, so a prompt containing newlines or quotes is still removed
# in full and neighbouring users' lines are left intact. (Done in Python rather
# than SQL: Postgres regexes take their greediness from the first quantifier,
# which made a single regexp_replace delete the lines between samples.)
_SAMPLE_RE = re.compile(r'[ \t]*\(e\.g\. ".*?"\)(?=\n|\Z)', re.DOTALL)


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("UPDATE v2_agent_prompt_quality_findings SET prompt_excerpt = NULL"))

    rows = bind.execute(
        sa.text(
            "SELECT id, usage_pattern_summary FROM v2_agent_model_cost_recommendations "
            "WHERE usage_pattern_summary LIKE :needle"
        ),
        {"needle": '%(e.g. "%'},
    ).fetchall()
    for row_id, summary in rows:
        bind.execute(
            sa.text(
                "UPDATE v2_agent_model_cost_recommendations "
                "SET usage_pattern_summary = :s WHERE id = :id"
            ),
            {"s": _SAMPLE_RE.sub("", summary), "id": row_id},
        )


def downgrade() -> None:
    # Redaction is irreversible by design.
    pass
