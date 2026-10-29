"""Data Residency Auditor Agent — checks invocation regions against approved policies.

Compares Bedrock invocation regions against configured approved regions.
Skips auditing if no residency policy is configured.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

try:
    from strands import Agent
    from strands.tools import tool as strands_tool
except ImportError:
    strands_tool = lambda fn: fn  # noqa: E731
    Agent = None

SYSTEM_PROMPT = (
    "You are a data residency auditing agent for the Bedrock Activity Dashboard. "
    "Your job is to verify that Bedrock invocations only occur in approved AWS regions. "
    "Use check_invocation_regions to gather invocation data, then flag_violations "
    "to identify any invocations outside approved regions."
)


@strands_tool
def check_invocation_regions() -> list[dict[str, Any]]:
    """Query recent Bedrock invocations and extract their target regions.

    Returns a list of invocations with user_identity, region, and timestamp.
    """
    from app.services.cloudtrail_service import CloudTrailService

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=1)

    ct = CloudTrailService()
    try:
        records = ct.get_activity_records(start, end)
    except Exception as exc:
        logger.warning("Failed to fetch records for residency audit: %s", exc)
        return []

    invocations = []
    for r in records:
        invocations.append({
            "user_identity": r.get("userIdentity", "unknown"),
            "region": r.get("awsRegion", settings.aws_region),
            "timestamp": r.get("timestamp", ""),
            "model_id": r.get("modelId", "unknown"),
            "record_id": r.get("id", ""),
        })
    return invocations


@strands_tool
def flag_violations(invocations: list[dict[str, Any]], policy: dict[str, Any]) -> list[dict[str, Any]]:
    """Compare invocation regions against an approved-regions policy.

    Args:
        invocations: List of invocation dicts with region and user_identity.
        policy: Dict with 'approved_regions' (list of region strings).

    Returns violations found. Returns empty list if no policy configured.
    """
    approved = policy.get("approved_regions", [])
    if not approved:
        logger.info("No residency policy configured — skipping audit")
        return []

    approved_set = set(approved)
    violations = []

    for inv in invocations:
        region = inv.get("region", "")
        if region and region not in approved_set:
            violations.append({
                "user_identity": inv.get("user_identity", "unknown"),
                "invocation_timestamp": inv.get("timestamp", ""),
                "target_region": region,
                "approved_regions": approved,
                "violation_severity": "critical" if region.startswith("cn-") else "warning",
            })

    return violations


def create_agent():
    """Create and return a configured Data Residency Auditor Strands Agent."""
    if Agent is None:
        return None
    return Agent(
        system_prompt=SYSTEM_PROMPT,
        tools=[check_invocation_regions, flag_violations],
        model="us.anthropic.claude-haiku-4-5-20251001-v1:0",
    )
