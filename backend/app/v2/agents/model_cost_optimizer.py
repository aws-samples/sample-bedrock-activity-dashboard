"""Model Cost Optimizer Agent — identifies expensive models used for simple tasks.

Analyzes usage patterns to find cases where cheaper models could handle the
workload, then generates weekly cost-saving recommendations.
"""

import logging
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
    "You are a model cost optimization agent for the Bedrock Activity Dashboard. "
    "Your job is to analyze Bedrock model usage patterns, identify cases where "
    "expensive models are used for simple tasks, and recommend cheaper alternatives. "
    "Use analyze_usage_patterns to gather data, then calculate_savings to quantify "
    "potential savings for each recommendation."
)

# Models considered "cheaper alternatives" for simple tasks
CHEAPER_ALTERNATIVES = [
    "nova-lite",
    "nova-micro",
    "claude-haiku",
]

# Token threshold — prompts below this are considered "simple"
SIMPLE_TASK_TOKEN_THRESHOLD = 1000

# Distillation-candidate thresholds. A model is flagged as a distillation
# candidate (in addition to, or instead of, a plain cheaper-model switch) when
# its usage looks like a narrow, repetitive, high-volume automated task rather
# than broad human/ad-hoc usage — the profile AWS's model-distillation guidance
# targets (https://aws.amazon.com/bedrock/model-distillation/): teacher-model
# calls doing the same kind of simple/short-prompt task over and over, driven by
# a small number of callers (e.g. one service account), at high volume.
#   - simple_task_ratio: share of invocations under SIMPLE_TASK_TOKEN_THRESHOLD
#   - total_invocations: enough call volume to justify a distillation project
#   - affected_user_count: few distinct callers (an automated/service task,
#     not many different humans asking varied questions)
DISTILLATION_SIMPLE_RATIO_THRESHOLD = 0.7
DISTILLATION_MIN_INVOCATIONS = 50
DISTILLATION_MAX_USER_COUNT = 3

# Fallback per-1K input-token cost used only when a model has no pricing row
# in the shared `model_pricing` table (e.g. a brand-new model not yet synced).
# Chosen to match the CostService/pricing.py default for an unrecognized
# premium model — see backend/app/routes/pricing.py DEFAULT_PRICING.
_FALLBACK_INPUT_TOKEN_PRICE = 0.003


def is_distillation_candidate(
    total_invocations: int, simple_task_ratio: float, affected_user_count: int
) -> bool:
    """True when usage for a model looks like a good distillation candidate.

    Distillation trains a smaller "student" model from a frontier "teacher"
    model's outputs on a repetitive task — AWS reports up to ~75% cost
    reduction with <2% accuracy loss for exactly this profile: a narrow,
    repetitive task called at high volume by a small number of callers
    (typically an automated/service integration), NOT broad, varied human
    usage where a single cheaper-model swap is the simpler fix.
    """
    return (
        total_invocations >= DISTILLATION_MIN_INVOCATIONS
        and simple_task_ratio >= DISTILLATION_SIMPLE_RATIO_THRESHOLD
        and affected_user_count <= DISTILLATION_MAX_USER_COUNT
    )


def build_distillation_note(
    model_id: str, total_invocations: int, simple_task_ratio: float, affected_user_count: int
) -> str:
    """Build the human-readable distillation-candidate reasoning text."""
    model_short = model_id.split(".")[-1].split("-v")[0] if "." in model_id else model_id
    return (
        f"{model_short} also looks like a distillation candidate: {total_invocations} "
        f"invocations over the past 14 days, {simple_task_ratio * 100:.0f}% of them simple/"
        f"repetitive, driven by only {affected_user_count} caller"
        f"{'s' if affected_user_count != 1 else ''} — consistent with a narrow, high-volume "
        f"automated task. Distilling a smaller student model from {model_short} for this "
        "specific task can cut cost by up to ~75% with <2% accuracy loss (see AWS Bedrock "
        "Model Distillation)."
    )


def _load_input_price_map() -> dict[str, float]:
    """Load per-1K input-token prices for every seeded model, keyed by model_id.

    Reads from the shared `model_pricing` table (the same source of truth
    `CostService`/`pricing.py` use for every other cost figure in the
    dashboard) instead of a second, hand-maintained cost map, so this agent's
    savings estimates never drift out of sync with the dashboard's own
    pricing data or with an admin's manual price edits.
    """
    from app.database import SessionLocal
    from app.services.cost_service import CostService

    db = SessionLocal()
    try:
        pricing = CostService.load_pricing(db)
        return {
            model_id: float(fields["inputTokenPrice"])
            for model_id, fields in pricing.items()
        }
    finally:
        db.close()


@strands_tool
def analyze_usage_patterns() -> list[dict[str, Any]]:
    """Analyze recent Bedrock usage to find expensive models handling simple tasks.

    Returns findings with per-user counts and reasoning (never prompt text).
    """
    from app.services.cloudtrail_service import CloudTrailService

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=14)

    ct = CloudTrailService()
    try:
        records = ct.get_activity_records(start, end)
    except Exception as exc:
        logger.warning("Failed to fetch records for cost optimization: %s", exc)
        return []

    logger.info("Model cost optimizer: fetched %d records from %s to %s", len(records), start, end)

    input_price_map = _load_input_price_map()

    # Group by model
    model_usage: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        model_id = r.get("modelId", "unknown")
        model_usage[model_id].append(r)

    logger.info("Model cost optimizer: %d unique models found: %s", len(model_usage), list(model_usage.keys()))

    findings = []
    for model_id, invocations in model_usage.items():
        # Skip already-cheap models
        if any(cheap in model_id for cheap in CHEAPER_ALTERNATIVES):
            logger.info("Skipping cheap model: %s", model_id)
            continue

        total = len(invocations)
        if total == 0:
            continue

        # Identify simple tasks (low token count) or all tasks if token data unavailable
        simple_invocations = [
            inv for inv in invocations
            if (inv.get("inputTokens") or 0) < SIMPLE_TASK_TOKEN_THRESHOLD
        ]
        simple_count = len(simple_invocations)
        logger.info("Model %s: %d total, %d simple (<%d tokens)", model_id, total, simple_count, SIMPLE_TASK_TOKEN_THRESHOLD)

        # If no simple tasks found but model is expensive, treat all as candidates
        if simple_count == 0:
            # Check if most invocations have 0 tokens (data unavailable)
            zero_token_count = sum(1 for inv in invocations if (inv.get("inputTokens") or 0) == 0)
            if zero_token_count > total * 0.5:
                # Token data unavailable — treat all invocations as candidates
                simple_invocations = invocations
                simple_count = total
                logger.info("Model %s: token data unavailable for %d/%d, treating all as candidates", model_id, zero_token_count, total)
            else:
                continue

        # Build per-user breakdown (counts only; never copy prompt text into findings)
        user_details: dict[str, dict] = defaultdict(lambda: {
            "invocation_count": 0,
            "simple_count": 0,
            "total_input_tokens": 0,
        })

        for inv in invocations:
            user_name = inv.get("userName", "unknown")
            user_arn = inv.get("userIdentity", "unknown")
            display_name = user_name if user_name != "unknown" else user_arn.split("/")[-1] if "/" in user_arn else user_arn
            detail = user_details[display_name]
            detail["invocation_count"] += 1
            detail["total_input_tokens"] += inv.get("inputTokens", 0)
            is_simple = (inv.get("inputTokens") or 0) < SIMPLE_TASK_TOKEN_THRESHOLD
            if is_simple:
                detail["simple_count"] += 1
        # Pick the best cheaper alternative
        current_cost = input_price_map.get(model_id, _FALLBACK_INPUT_TOKEN_PRICE)
        best_alt = None
        best_alt_cost = current_cost
        for alt_id, alt_cost in input_price_map.items():
            # Skip the current model and expensive models
            if alt_id == model_id or alt_cost >= current_cost:
                continue
            # Only suggest models that are in the cheaper alternatives list
            if not any(cheap in alt_id for cheap in CHEAPER_ALTERNATIVES):
                continue
            if alt_cost < best_alt_cost:
                best_alt = alt_id
                best_alt_cost = alt_cost

        if not best_alt:
            continue

        # Calculate actual savings based on observed token usage
        total_simple_tokens = sum(
            inv.get("inputTokens", 0) for inv in simple_invocations
        )
        # Project monthly: observed_weekly * 4.3
        monthly_tokens_k = (total_simple_tokens / 1000) * 4.3
        monthly_savings = round((current_cost - best_alt_cost) * monthly_tokens_k, 2)

        # Build human-readable user summary
        user_summaries = []
        for uname, detail in sorted(user_details.items(), key=lambda x: x[1]["simple_count"], reverse=True):
            if detail["simple_count"] > 0:
                user_summaries.append({
                    "user": uname,
                    "simple_invocations": detail["simple_count"],
                    "total_invocations": detail["invocation_count"],
                })

        # Build the reasoning text
        model_short = model_id.split(".")[-1].split("-v")[0] if "." in model_id else model_id
        alt_short = best_alt.split(".")[-1].split("-v")[0] if "." in best_alt else best_alt

        # Check if we have token data or not
        zero_token_count = sum(1 for inv in invocations if (inv.get("inputTokens") or 0) == 0)
        has_token_data = zero_token_count < total * 0.5

        if has_token_data:
            reasoning_parts = [
                f"Over the past 14 days, {simple_count} of {total} invocations to {model_short} "
                f"used fewer than {SIMPLE_TASK_TOKEN_THRESHOLD} input tokens — these are simple tasks "
                f"that don't need a premium model.",
            ]
        else:
            reasoning_parts = [
                f"Over the past 14 days, {total} invocations used {model_short} (a premium model). "
                f"Many of these could likely be handled by a cheaper alternative.",
            ]

        # Add top users
        top_users = sorted(user_details.items(), key=lambda x: x[1]["simple_count"], reverse=True)[:5]
        for uname, detail in top_users:
            if detail["simple_count"] > 0:
                line = f"  \u2022 {uname}: {detail['simple_count']} calls"
                reasoning_parts.append(line)

        reasoning_parts.append(
            f"Switching these to {alt_short} would save ~${monthly_savings:.2f}/mo "
            f"({current_cost}/1K → {best_alt_cost}/1K tokens)."
        )

        affected_user_count = len([u for u in user_details.values() if u["simple_count"] > 0])
        simple_task_ratio = round(simple_count / total, 2)

        # Distillation candidate check — additive to the model-switch
        # recommendation above; both can apply to the same model at once.
        distillation_candidate = is_distillation_candidate(
            total, simple_task_ratio, affected_user_count
        )
        if distillation_candidate:
            reasoning_parts.append(
                build_distillation_note(model_id, total, simple_task_ratio, affected_user_count)
            )

        findings.append({
            "current_model": model_id,
            "suggested_model": best_alt,
            "estimated_monthly_savings": monthly_savings,
            "affected_user_count": affected_user_count,
            "severity": "high" if monthly_savings > 50 else "medium",
            "usage_pattern_summary": "\n".join(reasoning_parts),
            "user_details": user_summaries,
            "total_invocations": total,
            "simple_task_count": simple_count,
            "simple_task_ratio": simple_task_ratio,
            "is_distillation_candidate": distillation_candidate,
        })

    return findings


@strands_tool
def calculate_savings(current_model: str, suggested_model: str) -> dict[str, Any]:
    """Calculate estimated monthly savings from switching models.

    Returns savings estimate with current vs suggested cost breakdown.
    """
    input_price_map = _load_input_price_map()
    current_cost = input_price_map.get(current_model, _FALLBACK_INPUT_TOKEN_PRICE)
    suggested_cost = input_price_map.get(suggested_model, 0.0008)

    if suggested_cost >= current_cost:
        return {
            "current_model": current_model,
            "suggested_model": suggested_model,
            "has_savings": False,
            "estimated_monthly_savings": 0,
        }

    # Estimate based on ~100K tokens/day for simple tasks over 30 days
    estimated_monthly_tokens_k = 100 * 30
    current_monthly = current_cost * estimated_monthly_tokens_k
    suggested_monthly = suggested_cost * estimated_monthly_tokens_k
    savings = round(current_monthly - suggested_monthly, 4)

    return {
        "current_model": current_model,
        "suggested_model": suggested_model,
        "has_savings": True,
        "current_cost_per_1k": current_cost,
        "suggested_cost_per_1k": suggested_cost,
        "estimated_monthly_savings": savings,
    }


def create_agent():
    """Create and return a configured Model Cost Optimizer Strands Agent."""
    if Agent is None:
        return None
    return Agent(
        system_prompt=SYSTEM_PROMPT,
        tools=[analyze_usage_patterns, calculate_savings],
        model="us.anthropic.claude-haiku-4-5-20251001-v1:0",
    )
