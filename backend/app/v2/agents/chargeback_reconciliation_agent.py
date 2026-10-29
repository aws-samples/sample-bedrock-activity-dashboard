"""Chargeback Reconciliation Agent — LLM-powered billing vs. usage comparison.

Compares actual AWS Bedrock billing from Cost Explorer against per-user
token usage estimates from CloudWatch Logs. Allocates actual costs
proportionally, identifies variances, and provides natural-language insights.
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
    "You are a chargeback reconciliation agent for the Bedrock Activity Dashboard. "
    "Compare actual AWS Bedrock billing from Cost Explorer against per-user token "
    "usage estimates from CloudWatch Logs. Allocate actual costs proportionally, "
    "identify variances, and provide insights.\n\n"
    "IMPORTANT: This is a best-effort reconciliation. CloudWatch Logs may not capture "
    "all billing items (e.g., provisioned throughput, model customization, batch "
    "inference). Variance between estimated and actual costs is expected and does not "
    "indicate an error."
)


@strands_tool
def compare_billing_vs_usage(
    billing_data: dict[str, Any],
    usage_data: dict[str, Any],
) -> dict[str, Any]:
    """Compare actual billing line items against per-user estimated costs.

    Args:
        billing_data: Dict with total_actual cost and line_items from CSV.
        usage_data: Dict with per-user estimated costs from CloudWatch Logs.
    """
    total_actual = billing_data.get("total_actual", 0.0)
    total_estimated = usage_data.get("total_estimated", 0.0)
    variance = total_actual - total_estimated
    pct = (abs(variance) / total_estimated * 100) if total_estimated > 0 else 0

    return {
        "total_actual": total_actual,
        "total_estimated": total_estimated,
        "variance": variance,
        "variance_percentage": round(pct, 2),
        "direction": "over" if variance > 0 else "under",
        "user_count": usage_data.get("users", 0),
    }


@strands_tool
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


def generate_insights(
    billing_data: dict[str, Any],
    usage_data: dict[str, Any],
    team_data: dict[str, Any] | None = None,
) -> str:
    """Produce natural-language analysis explaining variances.

    Args:
        billing_data: Dict with total_actual cost.
        usage_data: Dict with total_estimated cost and user count.
        team_data: Optional team aggregation data.
    """
    total_actual = billing_data.get("total_actual", 0.0)
    total_estimated = usage_data.get("total_estimated", 0.0)
    variance = total_actual - total_estimated
    pct = (abs(variance) / total_estimated * 100) if total_estimated > 0 else 0
    direction = "higher" if variance > 0 else "lower"

    lines = [
        "⚠️ Best-Effort Reconciliation Disclaimer: This report compares actual AWS "
        "billing against CloudWatch Logs-based cost estimates. Variance is expected — "
        "actual billing may include charges not captured in CloudWatch Logs "
        "(e.g., provisioned throughput, model customization, batch inference).",
        "",
        f"Total actual cost: ${total_actual:,.2f}",
        f"Total estimated cost: ${total_estimated:,.2f}",
        f"Variance: ${variance:,.2f} ({pct:.1f}% {direction} than estimated)",
        "",
    ]

    user_count = usage_data.get("users", 0)
    if user_count:
        lines.append(f"Cost allocated across {user_count} active user(s).")

    if team_data:
        sorted_teams = sorted(
            team_data.items(),
            key=lambda x: x[1].get("allocated_actual_cost", 0) if isinstance(x[1], dict) else 0,
            reverse=True,
        )
        if sorted_teams:
            top = sorted_teams[0]
            top_cost = top[1].get("allocated_actual_cost", 0) if isinstance(top[1], dict) else 0
            lines.append(f"Largest cost center: {top[0]} (${top_cost:,.2f} allocated)")

    return "\n".join(lines)


def create_agent():
    """Create and return a configured Chargeback Reconciliation Strands Agent."""
    if Agent is None:
        return None
    return Agent(
        system_prompt=SYSTEM_PROMPT,
        tools=[compare_billing_vs_usage, allocate_costs],
        model="us.anthropic.claude-haiku-4-5-20251001-v1:0",
    )
