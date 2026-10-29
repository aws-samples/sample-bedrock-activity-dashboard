"""Cost Anomaly Alerter Agent — detects spending spikes against rolling averages.

Mostly pure math — no AI needed for detection. Uses Strands Agent pattern
for consistency but the tools are computational, not LLM-driven.
"""

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
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
    "You are a cost anomaly detection agent for the Bedrock Activity Dashboard. "
    "Your job is to compare current daily Bedrock spend against rolling averages "
    "and flag spending spikes. Use compute_rolling_average to get baselines, "
    "detect_anomalies to find spikes, and send_sns_alert for notifications."
)

DEFAULT_WINDOW_DAYS = 7
DEFAULT_THRESHOLD_MULTIPLIER = 2.0
MIN_HISTORY_DAYS = 3


@strands_tool
def compute_rolling_average(window_days: int = DEFAULT_WINDOW_DAYS) -> dict[str, Any]:
    """Compute rolling average of daily spend for the trailing window.

    Returns {daily_spends: [{date, spend}], rolling_average: float, days_available: int}.
    """
    from app.services.cloudtrail_service import CloudTrailService
    from app.services.cost_service import CostService
    from app.database import SessionLocal

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=window_days + 1)

    ct = CloudTrailService()
    try:
        records = ct.get_activity_records(start, end)
    except Exception as exc:
        logger.warning("Failed to fetch records for cost anomaly: %s", exc)
        return {"daily_spends": [], "rolling_average": 0, "days_available": 0}

    db = SessionLocal()
    try:
        CostService.apply_costs(records, db)
    finally:
        db.close()

    # Group by date
    daily: dict[str, float] = defaultdict(float)
    for r in records:
        cost = r.get("costEstimate")
        if cost is not None:
            date_key = r.get("timestamp", "")[:10]
            if date_key:
                daily[date_key] += cost

    sorted_dates = sorted(daily.keys())
    spends = [{"date": d, "spend": daily[d]} for d in sorted_dates]
    avg = sum(daily.values()) / len(daily) if daily else 0

    return {
        "daily_spends": spends,
        "rolling_average": round(avg, 4),
        "days_available": len(daily),
    }


@strands_tool
def detect_anomalies(threshold_multiplier: float = DEFAULT_THRESHOLD_MULTIPLIER) -> list[dict[str, Any]]:
    """Compare current spend vs rolling average. Return anomalies."""
    data = compute_rolling_average()
    if data["days_available"] < MIN_HISTORY_DAYS:
        logger.info("Insufficient history (%d days) for anomaly detection", data["days_available"])
        return []

    avg = data["rolling_average"]
    if avg <= 0:
        return []

    anomalies = []
    for entry in data["daily_spends"]:
        spend = entry["spend"]
        if spend > avg * threshold_multiplier:
            anomalies.append({
                "anomaly_date": entry["date"],
                "current_spend": spend,
                "rolling_average": avg,
                "threshold_multiplier": threshold_multiplier,
                "severity": "critical" if spend > avg * 3 else "high",
            })
    return anomalies


@strands_tool
def send_sns_alert(anomaly_data: dict[str, Any]) -> dict[str, Any]:
    """Send SNS notification for a cost anomaly. Returns delivery status."""
    topic_arn = settings.sns_topic_arn
    if not topic_arn:
        logger.warning("No SNS topic configured — skipping alert")
        return {"delivered": False, "reason": "no_topic_configured"}

    try:
        client = boto3.client("sns", region_name=settings.aws_region)
        message = (
            f"Cost Anomaly Detected\n\n"
            f"Date: {anomaly_data.get('anomaly_date')}\n"
            f"Current Spend: ${anomaly_data.get('current_spend', 0):.2f}\n"
            f"Rolling Average: ${anomaly_data.get('rolling_average', 0):.2f}\n"
            f"Threshold: {anomaly_data.get('threshold_multiplier', 2.0)}x\n"
        )
        client.publish(TopicArn=topic_arn, Subject="Bedrock Cost Anomaly Alert", Message=message)
        return {"delivered": True}
    except (ClientError, Exception) as exc:
        logger.warning("SNS alert failed: %s", exc)
        return {"delivered": False, "reason": str(exc)}


def create_agent():
    """Create and return a configured Cost Anomaly Alerter Strands Agent."""
    if Agent is None:
        return None
    return Agent(
        system_prompt=SYSTEM_PROMPT,
        tools=[compute_rolling_average, detect_anomalies, send_sns_alert],
        model="us.anthropic.claude-haiku-4-5-20251001-v1:0",
    )
