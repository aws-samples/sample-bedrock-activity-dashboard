"""Chargeback Report Generator Agent — calculates per-team Bedrock costs.

Computes costs per team, broken down by model, user, and token type.
Users not assigned to any team go to the "Unassigned" category.
"""

import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any


logger = logging.getLogger(__name__)

try:
    from strands import Agent
    from strands.tools import tool as strands_tool
except ImportError:
    strands_tool = lambda fn: fn  # noqa: E731
    Agent = None

SYSTEM_PROMPT = (
    "You are a chargeback report generation agent for the Bedrock Activity Dashboard. "
    "Your job is to calculate Bedrock costs per team for a given month and produce "
    "detailed breakdowns by model, user, and token type. Use compute_team_costs "
    "to get team-level totals, then generate_breakdown for detailed per-team reports."
)

UNASSIGNED_TEAM = "Unassigned"


def _get_user_team_map(db_session) -> dict[str, str]:
    """Build a user_identity → team_name mapping from the database."""
    try:
        from app.models import Team, TeamMembership
        memberships = db_session.query(TeamMembership).all()
        team_map = {}
        for m in memberships:
            team = db_session.query(Team).filter(Team.id == m.team_id).first()
            if team:
                team_map[m.user_identity] = team.name
        return team_map
    except Exception:
        # Teams table may not exist yet
        return {}


@strands_tool
def compute_team_costs(month: str) -> dict[str, Any]:
    """Compute total Bedrock costs per team for a given month (YYYY-MM format).

    Unassigned users are grouped under the 'Unassigned' category.
    """
    from app.services.cloudtrail_service import CloudTrailService
    from app.services.cost_service import CostService
    from app.database import SessionLocal

    try:
        year, mon = month.split("-")
        start = datetime(int(year), int(mon), 1, tzinfo=timezone.utc)
        if int(mon) == 12:
            end = datetime(int(year) + 1, 1, 1, tzinfo=timezone.utc)
        else:
            end = datetime(int(year), int(mon) + 1, 1, tzinfo=timezone.utc)
    except (ValueError, IndexError):
        return {"error": f"Invalid month format: {month}. Expected YYYY-MM."}

    ct = CloudTrailService()
    try:
        records = ct.get_activity_records(start, end)
    except Exception as exc:
        logger.warning("Failed to fetch records for chargeback: %s", exc)
        return {"teams": {}, "period": month}

    db = SessionLocal()
    try:
        CostService.apply_costs(records, db)
        user_team = _get_user_team_map(db)
    finally:
        db.close()

    # Aggregate costs by team
    team_costs: dict[str, float] = defaultdict(float)
    for r in records:
        user = r.get("userIdentity", "unknown")
        team = user_team.get(user, UNASSIGNED_TEAM)
        cost = r.get("costEstimate") or 0.0
        team_costs[team] += cost

    return {
        "period": month,
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "teams": {team: round(cost, 4) for team, cost in sorted(team_costs.items())},
        "total": round(sum(team_costs.values()), 4),
    }


@strands_tool
def generate_breakdown(team: str) -> dict[str, Any]:
    """Generate a detailed cost breakdown for a specific team.

    Breaks down by model, user, and token type (input vs output).
    """
    from app.services.cloudtrail_service import CloudTrailService
    from app.services.cost_service import CostService
    from app.database import SessionLocal

    end = datetime.now(timezone.utc)
    # Default to current month
    start = end.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    ct = CloudTrailService()
    try:
        records = ct.get_activity_records(start, end)
    except Exception as exc:
        logger.warning("Failed to fetch records for team breakdown: %s", exc)
        return {"team": team, "by_model": {}, "by_user": {}, "by_token_type": {}}

    db = SessionLocal()
    try:
        CostService.apply_costs(records, db)
        user_team = _get_user_team_map(db)
    finally:
        db.close()

    # Filter to team members
    team_records = [
        r for r in records
        if user_team.get(r.get("userIdentity", "unknown"), UNASSIGNED_TEAM) == team
    ]

    by_model: dict[str, float] = defaultdict(float)
    by_user: dict[str, float] = defaultdict(float)
    by_token_type: dict[str, float] = {"input": 0.0, "output": 0.0}

    for r in team_records:
        cost = r.get("costEstimate") or 0.0
        model_id = r.get("modelId", "unknown")
        user = r.get("userIdentity", "unknown")
        input_tokens = r.get("inputTokens") or 0
        output_tokens = r.get("outputTokens") or 0
        total_tokens = input_tokens + output_tokens

        by_model[model_id] += cost
        by_user[user] += cost

        if total_tokens > 0:
            input_ratio = input_tokens / total_tokens
            by_token_type["input"] += cost * input_ratio
            by_token_type["output"] += cost * (1 - input_ratio)

    return {
        "team": team,
        "total_cost": round(sum(by_model.values()), 4),
        "by_model": {k: round(v, 4) for k, v in sorted(by_model.items())},
        "by_user": {k: round(v, 4) for k, v in sorted(by_user.items())},
        "by_token_type": {k: round(v, 4) for k, v in by_token_type.items()},
    }


def create_agent():
    """Create and return a configured Chargeback Report Generator Strands Agent."""
    if Agent is None:
        return None
    return Agent(
        system_prompt=SYSTEM_PROMPT,
        tools=[compute_team_costs, generate_breakdown],
        model="us.anthropic.claude-haiku-4-5-20251001-v1:0",
    )
