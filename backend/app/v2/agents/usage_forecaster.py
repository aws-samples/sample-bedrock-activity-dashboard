"""Usage Forecaster Agent — projects 30/60/90-day token and cost forecasts.

Uses historical usage data to generate forecasts with confidence intervals.
Returns insufficient_data if fewer than 14 days of history are available.
"""

import logging
import math
from collections import defaultdict
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
    "You are a usage forecasting agent for the Bedrock Activity Dashboard. "
    "Your job is to analyze historical Bedrock usage and project future token "
    "consumption and costs. Use analyze_historical_trends to gather baseline data, "
    "then generate_projections to create 30/60/90-day forecasts with confidence intervals."
)

MIN_HISTORY_DAYS = 14


@strands_tool
def analyze_historical_trends(days: int = 90) -> dict[str, Any]:
    """Analyze historical usage trends over the specified number of days.

    Returns daily token counts, cost totals, and trend indicators.
    """
    from app.services.cloudtrail_service import CloudTrailService
    from app.services.cost_service import CostService
    from app.database import SessionLocal

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)

    ct = CloudTrailService()
    try:
        records = ct.get_activity_records(start, end)
    except Exception as exc:
        logger.warning("Failed to fetch records for forecasting: %s", exc)
        return {"daily_data": [], "days_available": 0}

    db = SessionLocal()
    try:
        CostService.apply_costs(records, db)
    finally:
        db.close()

    # Aggregate by date
    daily: dict[str, dict[str, float]] = defaultdict(lambda: {"tokens": 0, "cost": 0.0})
    for r in records:
        date_key = r.get("timestamp", "")[:10]
        if not date_key:
            continue
        daily[date_key]["tokens"] += (r.get("inputTokens") or 0) + (r.get("outputTokens") or 0)
        daily[date_key]["cost"] += r.get("costEstimate") or 0.0

    sorted_dates = sorted(daily.keys())
    daily_data = [
        {"date": d, "tokens": daily[d]["tokens"], "cost": round(daily[d]["cost"], 4)}
        for d in sorted_dates
    ]

    return {
        "daily_data": daily_data,
        "days_available": len(daily_data),
    }


@strands_tool
def generate_projections() -> dict[str, Any]:
    """Generate 30/60/90-day forecasts with confidence intervals.

    Returns insufficient_data if fewer than 14 days of history exist.
    """
    trends = analyze_historical_trends(days=90)
    days_available = trends["days_available"]

    if days_available < MIN_HISTORY_DAYS:
        return {
            "methodology": "insufficient_data",
            "days_available": days_available,
            "min_required": MIN_HISTORY_DAYS,
            "forecasts": [],
        }

    daily_data = trends["daily_data"]
    token_values = [d["tokens"] for d in daily_data]
    cost_values = [d["cost"] for d in daily_data]

    avg_tokens = sum(token_values) / len(token_values)
    avg_cost = sum(cost_values) / len(cost_values)

    # Standard deviation for confidence intervals
    cost_std = math.sqrt(sum((c - avg_cost) ** 2 for c in cost_values) / len(cost_values)) if len(cost_values) > 1 else 0

    forecasts = []
    for period in [30, 60, 90]:
        # Wider confidence intervals for longer projections
        confidence_factor = 1.96 * math.sqrt(period / days_available)

        projected_tokens = int(avg_tokens * period)
        projected_cost = round(avg_cost * period, 4)

        cost_margin = round(cost_std * confidence_factor * period, 4)

        forecasts.append({
            "forecast_period_days": period,
            "projected_tokens": projected_tokens,
            "projected_cost": projected_cost,
            "confidence_lower": round(max(0, projected_cost - cost_margin), 4),
            "confidence_upper": round(projected_cost + cost_margin, 4),
        })

    return {
        "methodology": "linear_extrapolation",
        "days_available": days_available,
        "forecasts": forecasts,
    }


def create_agent():
    """Create and return a configured Usage Forecaster Strands Agent."""
    if Agent is None:
        return None
    return Agent(
        system_prompt=SYSTEM_PROMPT,
        tools=[analyze_historical_trends, generate_projections],
        model="us.anthropic.claude-haiku-4-5-20251001-v1:0",
    )
