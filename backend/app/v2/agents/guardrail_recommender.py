"""Guardrail Recommender Agent — suggests Bedrock Guardrail configs from PII findings.

Reads PII findings, generates guardrail configurations, persists as pending_review.
NEVER creates or modifies Bedrock Guardrails directly — human approval required.
"""

import logging
from typing import Any


logger = logging.getLogger(__name__)

try:
    from strands import Agent
    from strands.tools import tool as strands_tool
except ImportError:
    strands_tool = lambda fn: fn  # noqa: E731
    Agent = None

SYSTEM_PROMPT = (
    "You are a guardrail recommendation agent. Read PII findings from the database "
    "and generate Bedrock Guardrail configuration suggestions. You must NEVER create "
    "or modify guardrails directly — only generate recommendations for human review."
)


@strands_tool
def read_pii_findings() -> list[dict[str, Any]]:
    """Query active PII findings from the database."""
    from app.database import SessionLocal
    from app.v2.models import V2AgentPiiFinding

    db = SessionLocal()
    try:
        rows = db.query(V2AgentPiiFinding).filter(
            V2AgentPiiFinding.status == "active"
        ).all()
        return [{"pii_type": r.pii_type, "id": str(r.id), "confidence": r.confidence_score} for r in rows]
    finally:
        db.close()


@strands_tool
def generate_guardrail_config(pii_types: list[str]) -> dict[str, Any]:
    """Generate a Bedrock Guardrail configuration based on detected PII types."""
    config: dict[str, Any] = {"contentFilters": [], "deniedTopics": [], "piiRedaction": []}

    pii_to_entity = {
        "email": "EMAIL", "phone": "PHONE", "ssn": "US_SOCIAL_SECURITY_NUMBER",
        "credit_card": "CREDIT_DEBIT_NUMBER", "aws_access_key": "AWS_ACCESS_KEY",
    }
    for pt in set(pii_types):
        entity = pii_to_entity.get(pt)
        if entity:
            config["piiRedaction"].append({"type": entity, "action": "ANONYMIZE"})

    if any(pt in ("ssn", "credit_card") for pt in pii_types):
        config["contentFilters"].append({"type": "SENSITIVE_INFORMATION", "strength": "HIGH"})
    else:
        config["contentFilters"].append({"type": "SENSITIVE_INFORMATION", "strength": "MEDIUM"})

    return config


def create_agent():
    if Agent is None:
        return None
    return Agent(
        system_prompt=SYSTEM_PROMPT,
        tools=[read_pii_findings, generate_guardrail_config],
        model="us.anthropic.claude-haiku-4-5-20251001-v1:0",
    )
