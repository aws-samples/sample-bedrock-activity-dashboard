"""Weekly Digest Orchestrator Agent — compiles findings from all agents into a digest.

Meta-agent that collects findings from other agents over the past 7 days,
compiles them into a structured digest, and sends via SNS. On individual
agent failure: includes an error note and continues with remaining agents.
"""

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

import boto3
from botocore.exceptions import ClientError

from app.config import settings

logger = logging.getLogger(__name__)

try:
    from strands import Agent
    from strands.tools import tool as strands_tool
except ImportError:
    strands_tool = lambda fn: fn  # noqa: E731
    Agent = None

SYSTEM_PROMPT = (
    "You are the weekly digest orchestrator for the Bedrock Activity Dashboard. "
    "Your job is to collect findings from all other agents over the past 7 days, "
    "compile them into a structured weekly digest, and send it via SNS. "
    "Use collect_agent_findings to gather data, compile_digest to format it, "
    "and send_digest to deliver. If an individual agent fails, include an error "
    "note and continue with the remaining agents."
)

# All agent finding models and their table names.
# NOTE: ``pii_detector`` entry removed — PII Detector agent retired. The
# V2AgentPiiFinding model still exists for legacy row readability (see the
# guardrail_recommender), but no new PII findings are generated so the digest
# never has anything new to report for it.
AGENT_FINDING_MODELS = {
    "cost_anomaly_alerter": "V2AgentCostAnomaly",
    "guardrail_recommender": "V2AgentGuardrailRecommendation",
    "model_cost_optimizer": "V2AgentModelCostRecommendation",
    "prompt_quality_analyzer": "V2AgentPromptQualityFinding",
    "model_adoption_tracker": "V2AgentModelAdoptionFinding",
    "usage_forecaster": "V2AgentUsageForecast",
    "data_residency_auditor": "V2AgentResidencyViolation",
    "latency_error_monitor": "V2AgentLatencyErrorFinding",
    "chargeback_report_generator": "V2AgentChargebackReport",
    "onboarding_assistant": "V2AgentOnboardingFinding",
    "policy_compliance_checker": "V2AgentPolicyViolation",
}


@strands_tool
def collect_agent_findings(days: int = 7) -> dict[str, Any]:
    """Collect findings from all agents over the specified number of days.

    Returns per-agent summaries. On individual agent query failure,
    includes an error note for that agent and continues.
    """
    from app.database import SessionLocal
    from app.v2 import models as v2_models

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    db = SessionLocal()
    summaries: dict[str, Any] = {}
    failed_agents: list[str] = []

    try:
        for agent_name, model_class_name in AGENT_FINDING_MODELS.items():
            try:
                model_class = getattr(v2_models, model_class_name)
                count = db.query(model_class).filter(
                    model_class.created_at >= cutoff
                ).count()

                # Get severity breakdown
                severity_counts: dict[str, int] = {}
                if hasattr(model_class, "severity"):
                    for severity in ["critical", "high", "medium", "low"]:
                        sev_count = db.query(model_class).filter(
                            model_class.created_at >= cutoff,
                            model_class.severity == severity,
                        ).count()
                        if sev_count > 0:
                            severity_counts[severity] = sev_count

                summaries[agent_name] = {
                    "finding_count": count,
                    "severity_breakdown": severity_counts,
                }
            except Exception as exc:
                logger.warning("Failed to collect findings for %s: %s", agent_name, exc)
                failed_agents.append(agent_name)
                summaries[agent_name] = {"error": str(exc)}
    finally:
        db.close()

    return {
        "period_days": days,
        "agent_summaries": summaries,
        "failed_agents": failed_agents,
    }


@strands_tool
def compile_digest(findings: dict[str, Any]) -> dict[str, Any]:
    """Compile collected findings into a structured weekly digest.

    Organizes findings by category with summary statistics.
    """
    summaries = findings.get("agent_summaries", {})
    failed = findings.get("failed_agents", [])

    today = date.today()
    period_start = today - timedelta(days=7)

    # Organize by category
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


@strands_tool
def send_digest(digest: dict[str, Any]) -> dict[str, Any]:
    """Send the compiled digest via SNS. Returns delivery status."""
    topic_arn = settings.sns_topic_arn
    if not topic_arn:
        logger.warning("No SNS topic configured — skipping digest delivery")
        return {"delivery_status": "failed", "reason": "no_topic_configured"}

    # Format digest as readable text
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
        client = boto3.client("sns", region_name=settings.aws_region)
        client.publish(
            TopicArn=topic_arn,
            Subject="Bedrock Dashboard — Weekly Digest",
            Message=message,
        )
        return {"delivery_status": "sent"}
    except (ClientError, Exception) as exc:
        logger.warning("SNS digest delivery failed: %s", exc)
        return {"delivery_status": "failed", "reason": str(exc)}


def create_agent():
    """Create and return a configured Weekly Digest Orchestrator Strands Agent."""
    if Agent is None:
        return None
    return Agent(
        system_prompt=SYSTEM_PROMPT,
        tools=[collect_agent_findings, compile_digest, send_digest],
        model="us.anthropic.claude-haiku-4-5-20251001-v1:0",
    )
