"""Onboarding Assistant Agent — generates personalized onboarding for new users.

Detects new Bedrock users and generates welcome messages with approved models,
budget limits, and usage guidelines. One onboarding per user (deduplicated).
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any


logger = logging.getLogger(__name__)

try:
    from strands import Agent
    from strands.tools import tool as strands_tool
except ImportError:
    strands_tool = lambda fn: fn  # noqa: E731
    Agent = None

SYSTEM_PROMPT = (
    "You are an onboarding assistant agent for the Bedrock Activity Dashboard. "
    "Your job is to detect new Bedrock users and generate personalized welcome "
    "messages with approved models, budget limits, and usage guidelines. "
    "Use detect_new_users to find users without onboarding records, then "
    "generate_welcome_message for each new user."
)


@strands_tool
def detect_new_users() -> list[dict[str, Any]]:
    """Detect Bedrock users who haven't been onboarded yet.

    Compares recent invocation users against existing onboarding records.
    Returns list of new user identities.
    """
    from app.services.cloudtrail_service import CloudTrailService
    from app.v2.models import V2AgentOnboardingFinding
    from app.database import SessionLocal

    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=2)

    ct = CloudTrailService()
    try:
        records = ct.get_activity_records(start, end)
    except Exception as exc:
        logger.warning("Failed to fetch records for onboarding detection: %s", exc)
        return []

    # Collect unique users from recent activity
    active_users = {r.get("userIdentity") for r in records if r.get("userIdentity")}

    # Check which users already have onboarding records
    db = SessionLocal()
    try:
        existing = db.query(V2AgentOnboardingFinding.user_identity).filter(
            V2AgentOnboardingFinding.user_identity.in_(list(active_users))
        ).all()
        onboarded = {row.user_identity for row in existing}
    finally:
        db.close()

    new_users = active_users - onboarded
    return [{"user_identity": u} for u in sorted(new_users)]


@strands_tool
def generate_welcome_message(user_identity: str) -> dict[str, Any]:
    """Generate a personalized onboarding message for a new user.

    Includes approved models, budget guidelines, and usage best practices.
    """
    from app.database import SessionLocal
    from app.v2.models import V2AgentPolicy

    db = SessionLocal()
    try:
        # Fetch active policies for context
        policies = db.query(V2AgentPolicy).filter(V2AgentPolicy.enabled.is_(True)).all()
        model_restrictions = [
            p.policy_rules for p in policies if p.policy_type == "model_restriction"
        ]
        token_limits = [
            p.policy_rules for p in policies if p.policy_type == "token_limit"
        ]
    finally:
        db.close()

    # Build guidance content
    sections = [f"Welcome to the Bedrock Activity Dashboard, {user_identity}!"]

    # Approved models section
    if model_restrictions:
        sections.append(
            "Approved Models: Please use only the following approved models: "
            + str(model_restrictions)
        )
    else:
        sections.append(
            "Approved Models: All available Bedrock models are currently approved for use."
        )

    # Budget limits
    if token_limits:
        sections.append(f"Token Limits: The following limits apply: {token_limits}")
    else:
        sections.append(
            "Budget: No specific token limits are configured. Please use resources responsibly."
        )

    # Best practices
    sections.append(
        "Best Practices:\n"
        "- Use the smallest model that meets your needs\n"
        "- Keep prompts concise and well-structured\n"
        "- Avoid including PII in prompts\n"
        "- Monitor your usage in the dashboard"
    )

    guidance = "\n\n".join(sections)

    return {
        "user_identity": user_identity,
        "guidance_content": guidance,
        "team_assignment": None,
    }


def create_agent():
    """Create and return a configured Onboarding Assistant Strands Agent."""
    if Agent is None:
        return None
    return Agent(
        system_prompt=SYSTEM_PROMPT,
        tools=[detect_new_users, generate_welcome_message],
        model="us.anthropic.claude-haiku-4-5-20251001-v1:0",
    )
