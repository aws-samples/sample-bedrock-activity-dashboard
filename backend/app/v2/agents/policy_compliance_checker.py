"""Policy Compliance Checker Agent — enforces organizational policies.

Checks model restrictions, proxy requirements, and token limits against
recent Bedrock invocations. Skips if no policies are configured.
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
    "You are a policy compliance checking agent for the Bedrock Activity Dashboard. "
    "Your job is to evaluate recent Bedrock invocations against configured "
    "organizational policies — model restrictions, proxy requirements, and token "
    "limits. Use evaluate_invocations to check compliance. Skip if no policies exist."
)


@strands_tool
def evaluate_invocations(policies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Evaluate recent Bedrock invocations against organizational policies.

    Args:
        policies: List of policy dicts with policy_type, policy_rules, and id.

    Returns violations found. Returns empty list if no policies provided.
    """
    if not policies:
        logger.info("No policies configured — skipping compliance check")
        return []

    from app.services.cloudtrail_service import CloudTrailService

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=1)

    ct = CloudTrailService()
    try:
        records = ct.get_activity_records(start, end)
    except Exception as exc:
        logger.warning("Failed to fetch records for policy compliance: %s", exc)
        return []

    violations = []

    for policy in policies:
        policy_type = policy.get("policy_type", "")
        rules = policy.get("policy_rules", {})
        policy_id = policy.get("id", "")

        if policy_type == "model_restriction":
            allowed_models = rules.get("allowed_models", [])
            if not allowed_models:
                continue
            for r in records:
                model_id = r.get("modelId", "")
                if model_id and not any(allowed in model_id for allowed in allowed_models):
                    violations.append({
                        "policy_rule_id": policy_id,
                        "user_identity": r.get("userIdentity", "unknown"),
                        "violation_severity": "critical",
                        "invocation_details": {
                            "model_id": model_id,
                            "timestamp": r.get("timestamp", ""),
                            "reason": f"Model {model_id} not in allowed list",
                        },
                    })

        elif policy_type == "token_limit":
            max_input = rules.get("max_input_tokens")
            max_output = rules.get("max_output_tokens")
            for r in records:
                input_tokens = r.get("inputTokens") or 0
                output_tokens = r.get("outputTokens") or 0
                reasons = []
                if max_input and input_tokens > max_input:
                    reasons.append(f"Input tokens ({input_tokens}) exceeds limit ({max_input})")
                if max_output and output_tokens > max_output:
                    reasons.append(f"Output tokens ({output_tokens}) exceeds limit ({max_output})")
                if reasons:
                    violations.append({
                        "policy_rule_id": policy_id,
                        "user_identity": r.get("userIdentity", "unknown"),
                        "violation_severity": "warning",
                        "invocation_details": {
                            "model_id": r.get("modelId", ""),
                            "timestamp": r.get("timestamp", ""),
                            "input_tokens": input_tokens,
                            "output_tokens": output_tokens,
                            "reasons": reasons,
                        },
                    })

        elif policy_type == "proxy_required":
            required_proxy = rules.get("proxy_endpoint")
            if not required_proxy:
                continue
            for r in records:
                source = r.get("sourceIPAddress", "")
                if source and source != required_proxy:
                    violations.append({
                        "policy_rule_id": policy_id,
                        "user_identity": r.get("userIdentity", "unknown"),
                        "violation_severity": "critical",
                        "invocation_details": {
                            "model_id": r.get("modelId", ""),
                            "timestamp": r.get("timestamp", ""),
                            "source_ip": source,
                            "reason": f"Invocation not routed through required proxy {required_proxy}",
                        },
                    })

    return violations


def create_agent():
    """Create and return a configured Policy Compliance Checker Strands Agent."""
    if Agent is None:
        return None
    return Agent(
        system_prompt=SYSTEM_PROMPT,
        tools=[evaluate_invocations],
        model="us.anthropic.claude-haiku-4-5-20251001-v1:0",
    )
