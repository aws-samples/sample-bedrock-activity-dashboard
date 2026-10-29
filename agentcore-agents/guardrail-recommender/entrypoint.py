"""Guardrail Recommender Agent — deployed to Bedrock AgentCore Runtime.

Reads PII findings from payload and generates Bedrock Guardrail configuration
suggestions. NEVER creates or modifies guardrails directly — human approval required.
"""

from typing import Any

from bedrock_agentcore.runtime import app
from strands import Agent
from strands.tools import tool

SYSTEM_PROMPT = (
    "You are a guardrail recommendation agent. Read PII findings from the payload "
    "and generate Bedrock Guardrail configuration suggestions. You must NEVER create "
    "or modify guardrails directly — only generate recommendations for human review."
)


@tool
def read_pii_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Read PII findings from the provided payload data.

    Args:
        findings: List of PII finding dicts with pii_type, id, confidence.

    Returns the findings list for downstream processing.
    """
    return findings


@tool
def generate_guardrail_config(pii_types: list[str]) -> dict[str, Any]:
    """Generate a Bedrock Guardrail configuration based on detected PII types."""
    config: dict[str, Any] = {
        "contentFilters": [],
        "deniedTopics": [],
        "piiRedaction": [],
    }

    pii_to_entity = {
        "email": "EMAIL",
        "phone": "PHONE",
        "ssn": "US_SOCIAL_SECURITY_NUMBER",
        "credit_card": "CREDIT_DEBIT_NUMBER",
        "aws_access_key": "AWS_ACCESS_KEY",
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


agent = Agent(
    system_prompt=SYSTEM_PROMPT,
    tools=[read_pii_findings, generate_guardrail_config],
    model="us.anthropic.claude-haiku-4-5-20251001-v1:0",
)


@app.entrypoint
def handle_invocation(payload: dict) -> dict:
    """Handle an invocation from AgentCore Runtime."""
    prompt = payload.get("prompt", "")
    if not prompt:
        return {"result": "No prompt provided", "findings": []}

    result = agent(prompt)

    data = payload.get("data", {})
    pii_findings = data.get("pii_findings", [])
    findings = []
    if pii_findings:
        findings = read_pii_findings(pii_findings)

    return {
        "result": str(result),
        "findings": findings,
        "finding_count": len(findings),
    }
