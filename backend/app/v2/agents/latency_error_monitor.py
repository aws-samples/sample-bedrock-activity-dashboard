"""Latency & Error Monitor Agent — tracks response times and error rates.

Calculates avg/p50/p95/p99 response times and error rates per model/region.
Creates findings when p95 > 2x baseline or error rate > 5%.
"""

import logging
import math
from collections import defaultdict
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
    "You are a latency and error monitoring agent for the Bedrock Activity Dashboard. "
    "Your job is to compute response time percentiles and error rates for Bedrock "
    "invocations. Use compute_latency_metrics for response time analysis and "
    "compute_error_rates for failure tracking. Flag anomalies when p95 exceeds "
    "2x the trailing baseline or error rate exceeds 5%."
)

P95_THRESHOLD_MULTIPLIER = 2.0
ERROR_RATE_THRESHOLD = 0.05


def _percentile(sorted_values: list[float], p: float) -> float:
    """Compute the p-th percentile from a sorted list of values."""
    if not sorted_values:
        return 0.0
    k = (len(sorted_values) - 1) * (p / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_values[int(k)]
    return sorted_values[f] * (c - k) + sorted_values[c] * (k - f)


@strands_tool
def compute_latency_metrics() -> dict[str, Any]:
    """Compute latency percentiles (avg, p50, p95, p99) per model and region.

    Returns metrics grouped by model_id and region, plus any latency spike findings.
    """
    from app.services.cloudtrail_service import CloudTrailService

    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=4)
    baseline_start = end - timedelta(days=7)

    ct = CloudTrailService()
    try:
        recent_records = ct.get_activity_records(start, end)
        baseline_records = ct.get_activity_records(baseline_start, start)
    except Exception as exc:
        logger.warning("Failed to fetch records for latency monitoring: %s", exc)
        return {"metrics": [], "findings": []}

    def _group_latencies(records: list[dict]) -> dict[str, list[float]]:
        groups: dict[str, list[float]] = defaultdict(list)
        for r in records:
            latency = r.get("latencyMs") or r.get("responseLatency")
            if latency is not None and latency > 0:
                key = f"{r.get('modelId', 'unknown')}|{r.get('awsRegion', settings.aws_region)}"
                groups[key].append(float(latency))
        return groups

    recent_groups = _group_latencies(recent_records)
    baseline_groups = _group_latencies(baseline_records)

    metrics = []
    findings = []

    for key, latencies in recent_groups.items():
        model_id, region = key.split("|", 1)
        sorted_lat = sorted(latencies)

        avg = round(sum(sorted_lat) / len(sorted_lat), 2)
        p50 = round(_percentile(sorted_lat, 50), 2)
        p95 = round(_percentile(sorted_lat, 95), 2)
        p99 = round(_percentile(sorted_lat, 99), 2)

        metric = {
            "model_id": model_id,
            "region": region,
            "avg_ms": avg,
            "p50_ms": p50,
            "p95_ms": p95,
            "p99_ms": p99,
            "sample_count": len(sorted_lat),
        }
        metrics.append(metric)

        # Check against baseline
        baseline_lat = baseline_groups.get(key, [])
        if baseline_lat:
            baseline_p95 = _percentile(sorted(baseline_lat), 95)
            if baseline_p95 > 0 and p95 > baseline_p95 * P95_THRESHOLD_MULTIPLIER:
                findings.append({
                    "metric_type": "latency_spike",
                    "current_value": p95,
                    "baseline_value": round(baseline_p95, 2),
                    "affected_model": model_id,
                    "affected_region": region,
                    "severity": "high" if p95 > baseline_p95 * 3 else "medium",
                })

    return {"metrics": metrics, "findings": findings}


@strands_tool
def compute_error_rates() -> dict[str, Any]:
    """Compute error rates per model and region.

    Returns error rates and findings for models exceeding the 5% threshold.
    """
    from app.services.cloudtrail_service import CloudTrailService

    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=4)

    ct = CloudTrailService()
    try:
        records = ct.get_activity_records(start, end)
    except Exception as exc:
        logger.warning("Failed to fetch records for error rate monitoring: %s", exc)
        return {"rates": [], "findings": []}

    # Group by model|region
    groups: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "errors": 0})
    for r in records:
        key = f"{r.get('modelId', 'unknown')}|{r.get('awsRegion', settings.aws_region)}"
        groups[key]["total"] += 1
        if r.get("errorCode") or r.get("status") == "error":
            groups[key]["errors"] += 1

    rates = []
    findings = []

    for key, counts in groups.items():
        model_id, region = key.split("|", 1)
        total = counts["total"]
        errors = counts["errors"]
        rate = errors / total if total > 0 else 0.0

        rates.append({
            "model_id": model_id,
            "region": region,
            "total_invocations": total,
            "error_count": errors,
            "error_rate": round(rate, 4),
        })

        if rate > ERROR_RATE_THRESHOLD:
            findings.append({
                "metric_type": "error_rate",
                "current_value": round(rate, 4),
                "baseline_value": ERROR_RATE_THRESHOLD,
                "affected_model": model_id,
                "affected_region": region,
                "severity": "critical" if rate > 0.15 else "high",
            })

    return {"rates": rates, "findings": findings}


def create_agent():
    """Create and return a configured Latency & Error Monitor Strands Agent."""
    if Agent is None:
        return None
    return Agent(
        system_prompt=SYSTEM_PROMPT,
        tools=[compute_latency_metrics, compute_error_rates],
        model="us.anthropic.claude-haiku-4-5-20251001-v1:0",
    )
