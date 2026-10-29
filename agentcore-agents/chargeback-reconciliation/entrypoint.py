"""Chargeback Reconciliation Agent — deployed to Bedrock AgentCore Runtime.

Compares actual AWS Bedrock billing against per-user token usage estimates,
performs proportional allocation, and generates natural-language insights.
"""

from collections import defaultdict
from typing import Any

from bedrock_agentcore.runtime import app
from strands import Agent
from strands.tools import tool

SYSTEM_PROMPT = (
    "You are a chargeback reconciliation agent for the Bedrock Activity Dashboard. "
    "Compare actual AWS Bedrock billing from Cost Explorer against per-user token "
    "usage estimates from CloudWatch Logs. Allocate actual costs proportionally, "
    "identify variances, and provide insights. "
    "Note: This is a best-effort reconciliation — CloudWatch Logs may not capture "
    "all billing items (e.g., provisioned throughput, batch inference)."
)


@tool
def compare_billing_vs_usage(
    billing_data: dict[str, Any],
    usage_data: dict[str, Any],
) -> dict[str, Any]:
    """Compare actual billing line items against per-user estimated costs.

    Args:
        billing_data: Dict with total_actual cost and line_items.
        usage_data: Dict with per-user estimated costs.
    """
    total_actual = billing_data.get("total_actual", 0.0)
    total_estimated = usage_data.get("total_estimated", 0.0)
    variance = total_actual - total_estimated
    pct = (abs(variance) / total_estimated * 100) if total_estimated > 0 else 0
    return {
        "total_actual": total_actual,
        "total_estimated": total_estimated,
        "variance": round(variance, 4),
        "variance_percentage": round(pct, 2),
        "direction": "over" if variance > 0 else "under",
    }


@tool
def allocate_costs(
    user_estimated: dict[str, float],
    total_actual_cost: float,
) -> dict[str, Any]:
    """Perform proportional allocation and generate per-user breakdown.

    Args:
        user_estimated: Mapping of user_identity to estimated cost.
        total_actual_cost: Total actual cost from AWS billing.
    """
    total_estimated = sum(user_estimated.values())
    allocation: dict[str, dict[str, float]] = {}

    if total_estimated > 0:
        for user, est in user_estimated.items():
            proportion = est / total_estimated
            allocated = total_actual_cost * proportion
            allocation[user] = {
                "estimated": round(est, 4),
                "allocated": round(allocated, 4),
                "variance": round(allocated - est, 4),
                "share_pct": round(proportion * 100, 2),
            }
    elif user_estimated:
        equal = total_actual_cost / len(user_estimated)
        pct = 100.0 / len(user_estimated)
        for user, est in user_estimated.items():
            allocation[user] = {
                "estimated": round(est, 4),
                "allocated": round(equal, 4),
                "variance": round(equal - est, 4),
                "share_pct": round(pct, 2),
            }

    return {"allocation": allocation, "total_actual": total_actual_cost, "total_estimated": total_estimated}


@tool
def generate_insights(
    billing_data: dict[str, Any],
    usage_data: dict[str, Any],
) -> str:
    """Produce natural-language analysis explaining variances."""
    total_actual = billing_data.get("total_actual", 0.0)
    total_estimated = usage_data.get("total_estimated", 0.0)
    variance = total_actual - total_estimated
    pct = (abs(variance) / total_estimated * 100) if total_estimated > 0 else 0
    direction = "higher" if variance > 0 else "lower"

    return (
        f"Best-Effort Reconciliation: Actual ${total_actual:,.2f} vs "
        f"Estimated ${total_estimated:,.2f} — variance of ${variance:,.2f} "
        f"({pct:.1f}% {direction}). CloudWatch Logs may not capture all billing items."
    )


agent = Agent(
    system_prompt=SYSTEM_PROMPT,
    tools=[compare_billing_vs_usage, allocate_costs, generate_insights],
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
    billing_data = data.get("billing_data", {})
    usage_data = data.get("usage_data", {})
    user_estimated = data.get("user_estimated", {})

    findings = []
    if billing_data and usage_data:
        comparison = compare_billing_vs_usage(billing_data, usage_data)
        allocation = allocate_costs(user_estimated, billing_data.get("total_actual", 0))
        findings = [{"comparison": comparison, "allocation": allocation}]

    return {
        "result": str(result),
        "findings": findings,
        "finding_count": len(findings),
    }
