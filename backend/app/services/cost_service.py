"""Cost calculation service for Bedrock activity records."""

import re
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.models import ModelPricing
from app.services.cache import TTLCache

# ---------------------------------------------------------------------------
# Batch inference pricing
# ---------------------------------------------------------------------------
#
# Bedrock Batch Inference offers a flat ~50% discount off the on-demand
# per-token price for every foundation model, in exchange for asynchronous
# (non-real-time) processing — well suited to workloads that can tolerate
# longer turnaround (e.g. overnight statement generation, bulk content
# summarization, large-scale model evaluations). Modeled as a discount rate
# applied to the existing on-demand price rather than a second set of stored
# columns, so there is a single source of truth per model and no risk of the
# on-demand and batch prices drifting out of sync when an admin edits pricing
# or a Price List sync updates it.
BATCH_INFERENCE_DISCOUNT_RATE = 0.5


def calculate_batch_price(on_demand_price: float) -> float:
    """Apply the flat batch-inference discount to an on-demand per-1K price."""
    return round(on_demand_price * (1 - BATCH_INFERENCE_DISCOUNT_RATE), 10)


# ---------------------------------------------------------------------------
# Pricing cache (thread-safe, user-agnostic)
# ---------------------------------------------------------------------------
#
# Model pricing is global reference data identical for every caller, so we cache
# the loaded pricing map behind a short TTL to avoid a DB round-trip on every
# request. Keyed by a single constant — never by user or request context — and
# the cached map is only ever read (never mutated) by cost calculation.
_PRICING_CACHE_TTL_SECONDS = 60
_PRICING_CACHE_KEY = "model_pricing"
_pricing_cache: TTLCache = TTLCache(_PRICING_CACHE_TTL_SECONDS)


def clear_pricing_cache() -> None:
    """Clear the pricing cache (used to isolate tests)."""
    _pricing_cache.clear()


# Known aliases mapping a model ID that lacks a seeded pricing row to a
# canonical ID that does. Kept deliberately conservative: only families where a
# short/undated ID unambiguously maps to the same price tier as a dated row.
_MODEL_ALIASES = {
    "anthropic.claude-3-5-sonnet": "anthropic.claude-3-5-sonnet-20241022-v2:0",
    "anthropic.claude-3-5-haiku": "anthropic.claude-3-5-haiku-20241022-v1:0",
    "anthropic.claude-3-haiku": "anthropic.claude-3-haiku-20240307-v1:0",
    "anthropic.claude-3-sonnet": "anthropic.claude-3-sonnet-20240229-v1:0",
    "anthropic.claude-3-opus": "anthropic.claude-3-opus-20240229-v1:0",
}

# Trailing ":0"/":1" version-invocation suffix (e.g. "...-v1:0" -> "...-v1").
_VERSION_SUFFIX_RE = re.compile(r":[01]$")


def _normalize_model_id(model_id: str) -> str:
    """Normalize a Bedrock model ID, stripping ARN and cross-region prefixes.

    Handles inference profile ARNs like:
      arn:aws:bedrock:us-west-2:123456789:inference-profile/us.anthropic.claude-opus-4-6-v1
    and cross-region prefixes like us., eu., ap.
    """
    if model_id.startswith("arn:"):
        model_id = model_id.rsplit("/", 1)[-1]
    for prefix in ("us.", "eu.", "ap.", "global."):
        if model_id.startswith(prefix):
            model_id = model_id[len(prefix):]
            break
    return model_id


def _strip_version_suffix(model_id: str) -> str:
    """Strip a trailing ':0'/':1' version-invocation suffix if present."""
    return _VERSION_SUFFIX_RE.sub("", model_id)


def _resolve_pricing(
    model_id: str,
    pricing: dict[str, dict[str, Decimal]],
) -> dict[str, Decimal] | None:
    """Resolve a pricing row for a model ID using progressive fallback lookups.

    Order (each attempted non-destructively, first match wins):
      1. raw model ID
      2. normalized (ARN + cross-region prefix stripped)
      3. normalized with trailing ':0'/':1' suffix removed
      4. known conservative aliases

    Returns None when no confident match is found rather than guessing.
    """
    if model_id in pricing:
        return pricing[model_id]

    normalized = _normalize_model_id(model_id)
    if normalized in pricing:
        return pricing[normalized]

    stripped = _strip_version_suffix(normalized)
    if stripped != normalized and stripped in pricing:
        return pricing[stripped]

    alias = _MODEL_ALIASES.get(normalized) or _MODEL_ALIASES.get(stripped)
    if alias and alias in pricing:
        return pricing[alias]

    return None


class CostService:
    """Calculates cost estimates for activity records based on model pricing."""

    @staticmethod
    def load_pricing(db: Session) -> dict[str, dict[str, Decimal]]:
        """Load all model pricing from the database, cached behind a short TTL.

        Returns a dict keyed by model_id with pricing fields as Decimal values.
        Pricing is global reference data, so the loaded map is cached
        process-wide (user-agnostic) to avoid a DB query on every request.
        """
        cached = _pricing_cache.get(_PRICING_CACHE_KEY)
        if cached is not None:
            return cached

        rows = db.query(ModelPricing).all()
        pricing: dict[str, dict[str, Decimal]] = {}
        for row in rows:
            pricing[row.model_id] = {
                "inputTokenPrice": row.input_token_price,
                "outputTokenPrice": row.output_token_price,
                "cacheReadTokenPrice": row.cache_read_token_price,
                "cacheWriteTokenPrice": row.cache_write_token_price,
            }
        _pricing_cache.set(_PRICING_CACHE_KEY, pricing)
        return pricing

    @staticmethod
    def calculate_cost(
        record: dict[str, Any],
        pricing: dict[str, dict[str, Decimal]],
    ) -> float | None:
        """Calculate cost for a single activity record.

        A cost is only meaningful when we have BOTH pricing AND token usage:
          - No pricing row matches the model  -> None (unknown model, shown as N/A).
          - Pricing matches but total usage is 0 -> None (no usage data to price,
            e.g. CloudTrail-fallback records that carry no token counts).
          - Pricing matches and usage is non-zero -> the computed float cost.

        Model IDs are resolved via progressive fallback (raw -> normalized ->
        normalized-without-trailing-:N -> known aliases).
        """
        raw_model_id = record.get("modelId", "")
        model_pricing = _resolve_pricing(raw_model_id, pricing)
        if model_pricing is None:
            return None

        input_tokens = record.get("inputTokens") or 0
        output_tokens = record.get("outputTokens") or 0
        cache_read_tokens = record.get("cacheReadTokens") or 0
        cache_write_tokens = record.get("cacheWriteTokens") or 0

        # No usage means there is nothing to price -> N/A rather than $0.00.
        if input_tokens + output_tokens + cache_read_tokens + cache_write_tokens == 0:
            return None

        cost = (
            (input_tokens * model_pricing["inputTokenPrice"] / 1000)
            + (output_tokens * model_pricing["outputTokenPrice"] / 1000)
            + (cache_read_tokens * model_pricing["cacheReadTokenPrice"] / 1000)
            + (cache_write_tokens * model_pricing["cacheWriteTokenPrice"] / 1000)
        )
        return float(cost)

    @staticmethod
    def apply_costs(
        records: list[dict[str, Any]],
        db: Session,
    ) -> list[dict[str, Any]]:
        """Load pricing and apply cost estimates to all records.

        Mutates each record's 'costEstimate' field in place and returns
        the same list for convenience.
        """
        pricing = CostService.load_pricing(db)
        for record in records:
            record["costEstimate"] = CostService.calculate_cost(record, pricing)
        return records

    @staticmethod
    def get_used_model_ids(records: list[dict[str, Any]]) -> set[str]:
        """Return the set of distinct NORMALIZED model IDs invoked in records.

        Normalizes each record's raw ``modelId`` (stripping ARN prefixes,
        cross-region prefixes) so the returned set lines up with
        ``model_pricing.model_id`` keys and with whatever
        :meth:`calculate_cost` will actually look up for these same records —
        this is the "what models are actually being used" signal that pricing
        gap detection is built on.
        """
        ids: set[str] = set()
        for record in records:
            raw_model_id = record.get("modelId")
            if raw_model_id:
                ids.add(_normalize_model_id(raw_model_id))
        return ids

    @staticmethod
    def find_pricing_gaps(
        used_model_ids: set[str],
        pricing: dict[str, dict[str, Decimal]],
    ) -> list[str]:
        """Return the sorted list of used model IDs with no resolvable pricing row.

        Uses the exact same fallback resolution :meth:`calculate_cost` uses
        (:func:`_resolve_pricing`), so a model that WOULD successfully price via
        normalization/alias is correctly excluded — only models that would
        actually show "N/A" cost today are reported.
        """
        return sorted(
            model_id
            for model_id in used_model_ids
            if _resolve_pricing(model_id, pricing) is None
        )
