"""Model Adoption Tracker Agent — lists Bedrock models and compares capabilities.

Queries available Bedrock models and compares them on capabilities and cost
to help teams make informed model selection decisions.
"""

import logging
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
    "You are a model adoption tracking agent for the Bedrock Activity Dashboard. "
    "Your job is to list available Bedrock models and compare their capabilities "
    "and costs. Use query_available_models to discover models, then compare_models "
    "to evaluate migration paths between a source and target model."
)

# Known model metadata for comparison (approximate pricing per 1K input tokens)
MODEL_METADATA: dict[str, dict[str, Any]] = {
    "anthropic.claude-sonnet-4-20250514-v1:0": {
        "provider": "Anthropic", "family": "Claude", "tier": "premium",
        "cost_per_1k_input": 0.003, "max_tokens": 200000,
        "capabilities": ["reasoning", "coding", "analysis", "vision"],
    },
    "anthropic.claude-haiku-4-5-20251001-v1:0": {
        "provider": "Anthropic", "family": "Claude", "tier": "standard",
        "cost_per_1k_input": 0.0008, "max_tokens": 200000,
        "capabilities": ["reasoning", "coding", "analysis", "vision"],
    },
    "amazon.nova-pro-v1:0": {
        "provider": "Amazon", "family": "Nova", "tier": "standard",
        "cost_per_1k_input": 0.0008, "max_tokens": 300000,
        "capabilities": ["reasoning", "coding", "analysis"],
    },
    "amazon.nova-lite-v1:0": {
        "provider": "Amazon", "family": "Nova", "tier": "economy",
        "cost_per_1k_input": 0.00006, "max_tokens": 300000,
        "capabilities": ["reasoning", "analysis"],
    },
    "amazon.nova-micro-v1:0": {
        "provider": "Amazon", "family": "Nova", "tier": "economy",
        "cost_per_1k_input": 0.000035, "max_tokens": 128000,
        "capabilities": ["reasoning"],
    },
}


@strands_tool
def query_available_models() -> list[dict[str, Any]]:
    """List Bedrock foundation models available in the configured region.

    Returns model IDs with provider, tier, and capability metadata.
    """
    region = settings.bedrock_region or settings.aws_region
    try:
        client = boto3.client("bedrock", region_name=region)
        response = client.list_foundation_models()
        models = []
        for m in response.get("modelSummaries", []):
            model_id = m.get("modelId", "")
            meta = MODEL_METADATA.get(model_id, {})
            models.append({
                "model_id": model_id,
                "model_name": m.get("modelName", ""),
                "provider": m.get("providerName", meta.get("provider", "Unknown")),
                "tier": meta.get("tier", "unknown"),
                "cost_per_1k_input": meta.get("cost_per_1k_input"),
                "capabilities": meta.get("capabilities", []),
            })
        return models
    except (ClientError, Exception) as exc:
        logger.warning("Failed to list Bedrock models: %s", exc)
        # Fall back to known metadata
        return [
            {"model_id": mid, **meta}
            for mid, meta in MODEL_METADATA.items()
        ]


@strands_tool
def compare_models(source: str, target: str) -> dict[str, Any]:
    """Compare two Bedrock models on capabilities and cost.

    Returns a comparison dict with capability overlap, cost delta, and recommendation.
    """
    source_meta = MODEL_METADATA.get(source, {})
    target_meta = MODEL_METADATA.get(target, {})

    if not source_meta or not target_meta:
        pricing_available = bool(source_meta and target_meta)
        return {
            "source": source,
            "target": target,
            "pricing_available": pricing_available,
            "comparison": "Insufficient metadata for one or both models.",
        }

    source_caps = set(source_meta.get("capabilities", []))
    target_caps = set(target_meta.get("capabilities", []))

    cost_delta = round(
        (target_meta.get("cost_per_1k_input", 0) - source_meta.get("cost_per_1k_input", 0)),
        6,
    )

    return {
        "source": source,
        "target": target,
        "pricing_available": True,
        "source_tier": source_meta.get("tier"),
        "target_tier": target_meta.get("tier"),
        "cost_delta_per_1k": cost_delta,
        "capabilities_gained": list(target_caps - source_caps),
        "capabilities_lost": list(source_caps - target_caps),
        "shared_capabilities": list(source_caps & target_caps),
        "recommendation_rationale": (
            f"Switching from {source} to {target} "
            f"{'saves' if cost_delta < 0 else 'costs'} "
            f"${abs(cost_delta):.6f}/1K tokens. "
            f"Lost capabilities: {list(source_caps - target_caps) or 'none'}."
        ),
    }


def create_agent():
    """Create and return a configured Model Adoption Tracker Strands Agent."""
    if Agent is None:
        return None
    return Agent(
        system_prompt=SYSTEM_PROMPT,
        tools=[query_available_models, compare_models],
        model="us.anthropic.claude-haiku-4-5-20251001-v1:0",
    )
