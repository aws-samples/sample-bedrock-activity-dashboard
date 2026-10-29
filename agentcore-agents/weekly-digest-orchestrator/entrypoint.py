"""Weekly Digest Orchestrator Agent — deployed to Bedrock AgentCore Runtime.

Meta-agent that collects findings from all agents, compiles them into a
structured digest, and sends via SNS. Accepts pre-collected summaries
via the invocation payload.
"""

import os
from datetime import date, timedelta
from typing import Any

import boto3
from botocore.exceptions import ClientError

# Resolve region from the runtime environment (AWS_REGION / AWS_DEFAULT_REGION)
# rather than pinning to a specific region, so this agent can be deployed to
# any AWS account and region.
_REGION = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-west-2"

from bedrock_agentcore.runtime import app
from strands import Agent
from strands.tools import tool

SYSTEM_PROMPT = (
    "You are the weekly digest orchestrator for the Bedrock Activity Dashboard. "
    "Your job is to collect findings from all other agents over the past 7 days, "
    "compile them into a structured weekly digest, and send it via SNS. "
    "Use collect_agent_findings to gather data, compile_digest to format it, "
    "and send_digest to deliver. If an individual agent fails, include an error "
    "note and continue with the remaining agents."
)


@tool
def collect_agent_findings(
    agent_summaries: dict[str, Any],
    days: int = 7,
) -> dict[str, Any]:
    """Collect findings from all agents using pre-gathered summaries.

    Args:
        agent_summaries: Dict mapping agent_name to summary dict with
                         finding_count, severity_breakdown, or error.
        days: Number of days the summaries cover.

    Returns per-agent summaries with failed agents noted.
    """
    failed_agents = [
        name for name, summary in agent_summaries.items()
        if "error" in summary
    ]

    return {
        "period_days": days,
        "agent_summaries": agent_summaries,
        "failed_agents": failed_agents,
    }


@tool
def compile_digest(findings: dict[str, Any]) -> dict[str, Any]:
    """Compile collected findings into a structured weekly digest.

    Organizes findings by category with summary statistics.
    """
    summaries = findings.get("agent_summaries", {})
    failed = findings.get("failed_agents", [])

    today = date.today()
    period_start = today - timedelta(days=7)

    # Category layout mirrors backend/app/v2/agents/weekly_digest_orchestrator.py.
    # ``pii_detector`` was removed when the PII Detector agent was retired.
    categories = {
        "Security & Guardrails": [
            "cost_anomaly_alerter", "guardrail_recommender", "model_cost_optimizer",
        ],
        "Usage Intelligence": [
            "prompt_quality_analyzer", "model_adoption_tracker", "usage_forecaster",
        ],
        "Compliance & Operations": [
            "data_residency_auditor", "latency_error_monitor",
            "chargeback_report_generator", "onboarding_assistant",
        ],
        "Orchestration": [
            "policy_compliance_checker",
        ],
    }

    sections = {}
    total_findings = 0

    for category, agents in categories.items():
        category_data = {}
        for agent_name in agents:
            summary = summaries.get(agent_name, {})
            if "error" in summary:
                category_data[agent_name] = {"status": "error", "error": summary["error"]}
            else:
                count = summary.get("finding_count", 0)
                total_findings += count
                category_data[agent_name] = {
                    "status": "ok",
                    "finding_count": count,
                    "severity_breakdown": summary.get("severity_breakdown", {}),
                }
        sections[category] = category_data

    return {
        "digest_period_start": period_start.isoformat(),
        "digest_period_end": today.isoformat(),
        "total_findings": total_findings,
        "sections": sections,
        "failed_agents": failed,
    }


@tool
def send_digest(digest: dict[str, Any], sns_topic_arn: str = "") -> dict[str, Any]:
    """Send the compiled digest via SNS. Returns delivery status.

    Args:
        digest: Compiled digest dict from compile_digest.
        sns_topic_arn: SNS topic ARN to publish to.
    """
    if not sns_topic_arn:
        return {"delivery_status": "failed", "reason": "no_topic_configured"}

    lines = [
        "Bedrock Activity Dashboard — Weekly Digest",
        f"Period: {digest.get('digest_period_start')} to {digest.get('digest_period_end')}",
        f"Total Findings: {digest.get('total_findings', 0)}",
        "",
    ]

    for category, agents in digest.get("sections", {}).items():
        lines.append(f"== {category} ==")
        for agent_name, data in agents.items():
            if data.get("status") == "error":
                lines.append(f"  {agent_name}: ERROR — {data.get('error', 'unknown')}")
            else:
                count = data.get("finding_count", 0)
                lines.append(f"  {agent_name}: {count} findings")
        lines.append("")

    failed = digest.get("failed_agents", [])
    if failed:
        lines.append(f"Failed agents: {', '.join(failed)}")

    message = "\n".join(lines)

    try:
        client = boto3.client("sns", region_name=_REGION)
        client.publish(
            TopicArn=sns_topic_arn,
            Subject="Bedrock Dashboard — Weekly Digest",
            Message=message,
        )
        return {"delivery_status": "sent"}
    except (ClientError, Exception) as exc:
        return {"delivery_status": "failed", "reason": str(exc)}


agent = Agent(
    system_prompt=SYSTEM_PROMPT,
    tools=[collect_agent_findings, compile_digest, send_digest],
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
    agent_summaries = data.get("agent_summaries", {})
    findings = []
    if agent_summaries:
        collected = collect_agent_findings(agent_summaries)
        digest = compile_digest(collected)
        findings = [digest]

    return {
        "result": str(result),
        "findings": findings,
        "finding_count": len(findings),
    }
