"""Deterministic pricing sync from the AWS Price List API.

Fetches real Amazon Bedrock on-demand token prices from the AWS Price List API
and upserts them into the ``model_pricing`` table. No LLM is involved — the
authoritative Price List data is parsed directly.

Bedrock token pricing is split across TWO Price List service codes with
different structures, and this sync merges both:

* ``AmazonBedrockFoundationModels`` — third-party marketplace models (Anthropic
  Claude, Llama 2, Cohere, ...). Products are keyed by
  ``product.attributes.servicename`` (e.g. "Claude Sonnet 4.5 (Amazon Bedrock
  Edition)"). Token prices are quoted PER MILLION tokens (the priceDimension
  ``unit`` is "Units"; the description mentions "Million ... Tokens"). There is a
  single on-demand token dimension per (servicename, dimension), so no tier
  selection is needed.

* ``AmazonBedrock`` — Amazon first-party and newer models (Nova, Titan, Llama
  3.x, Mistral, Gemma, ...). Products are keyed by ``product.attributes.model``
  (falling back to ``titanModel``). Token prices are quoted PER 1K tokens (the
  priceDimension ``unit`` is "1K tokens"). ``usagetype`` values carry tier
  suffixes (``-standard``, ``-flex``, ``-priority``, ``-batch``,
  ``-cross-region-global``, ``ProvisionedThroughput``, ...), so a given
  (model, dimension) may expose several on-demand tiers; one is selected by a
  deterministic preference order.

Facts about the Price List API used here:

* ``get_products`` is only available in ``us-east-1`` / ``ap-south-1``. This
  service forces ``region_name="us-east-1"`` for the pricing client,
  independent of the application's own AWS region.
* Products are filtered to the ``us-west-2`` region (matching the dashboard's
  deployment region).

Because the exact human-readable names returned by the API may differ from the
curated maps below, the sync summary always reports ``unmatchedServiceNames`` —
token-priced names (from EITHER service) that did not map to a known
``model_id``. Those models keep their existing prices; the curated maps can then
be refined in a follow-up.
"""

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Callable, Optional

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import ModelPricing

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# The Price List API only lives in us-east-1 / ap-south-1 — force us-east-1
# regardless of the application region.
_PRICE_LIST_REGION = "us-east-1"

# The two Bedrock service codes in the Price List API.
_SERVICE_CODE_FM = "AmazonBedrockFoundationModels"  # third-party marketplace models
_SERVICE_CODE_AB = "AmazonBedrock"  # Amazon first-party + newer models

# Fallback region used only when ``settings.aws_region`` is unset (empty
# string). ``settings.aws_region`` defaults to "us-east-1", so under normal
# operation this fallback is not used — but keeping it defensive avoids a
# silent empty-region filter on an unusually-configured environment.
_PRICE_REGION_FALLBACK = "us-west-2"


def _price_region() -> str:
    """Region whose prices we sync — resolved at CALL time, not import time.

    Reading ``settings.aws_region`` lazily means the sync reflects whatever
    ``AWS_REGION`` the ECS task actually runs under (or whatever the test
    environment sets), instead of freezing whatever value happened to be in
    effect during module import.
    """
    return settings.aws_region or _PRICE_REGION_FALLBACK


def _pricing_source() -> str:
    """Human-readable pricing source string (includes the resolved region)."""
    return (
        "AWS Price List API (AmazonBedrock + AmazonBedrockFoundationModels, "
        f"{_price_region()}, on-demand)"
    )

# Per-1K prices are rounded to this many decimals to avoid float representation
# noise (the column is Numeric(20,10), so 8 decimals is safely within range).
_PER_1K_PRECISION = 8

# Two per-1K values within this epsilon are considered equal (change detection).
_PRICE_EPSILON = 1e-9


# ---------------------------------------------------------------------------
# Curated name -> model_id maps (EXACT — no fuzzy guessing)
# ---------------------------------------------------------------------------
#
# A mismapping silently corrupts every downstream cost calculation, so lookups
# are EXACT against normalized keys. Any token-priced name that does not match a
# key here is surfaced in the summary's ``unmatchedServiceNames`` so the maps can
# be refined rather than guessed.

# AmazonBedrockFoundationModels: normalized ``servicename`` -> model_id.
# Normalization: strip trailing " (Amazon Bedrock Edition)", trim, lowercase,
# collapse internal whitespace (see ``_normalize_servicename``).
CURATED_FM_NAME_MAP: dict[str, str] = {
    "claude 3 haiku": "anthropic.claude-3-haiku-20240307-v1:0",
    "claude 3.5 haiku": "anthropic.claude-3-5-haiku-20241022-v1:0",
    "claude haiku 4.5": "anthropic.claude-haiku-4-5-20251001-v1:0",
    "claude 3.5 sonnet": "anthropic.claude-3-5-sonnet-20240620-v1:0",
    "claude 3.5 sonnet v2": "anthropic.claude-3-5-sonnet-20241022-v2:0",
    "claude sonnet 4": "anthropic.claude-sonnet-4-20250514-v1:0",
    "claude sonnet 4.5": "anthropic.claude-sonnet-4-5-20250929-v1:0",
    "claude sonnet 4.6": "anthropic.claude-sonnet-4-6",
    "claude sonnet 5": "anthropic.claude-sonnet-5",
    "claude 3.7 sonnet": "anthropic.claude-3-7-sonnet-20250219-v1:0",
    "claude 3 sonnet": "anthropic.claude-3-sonnet-20240229-v1:0",
    "claude 3 opus": "anthropic.claude-3-opus-20240229-v1:0",
    "claude opus 4": "anthropic.claude-opus-4-20250514-v1:0",
    "claude opus 4.1": "anthropic.claude-opus-4-1-20250805-v1:0",
    "claude opus 4.5": "anthropic.claude-opus-4-5-20251101-v1:0",
    "claude opus 4.6": "anthropic.claude-opus-4-6-v1",
    "claude opus 4.7": "anthropic.claude-opus-4-7",
    "claude opus 4.8": "anthropic.claude-opus-4-8",
    "claude opus 5": "anthropic.claude-opus-5",
    "claude fable 5": "anthropic.claude-fable-5",
    "claude mythos 5": "anthropic.claude-mythos-5",
    "claude mythos preview": "anthropic.claude-mythos-preview",
    "palmyra x4": "writer.palmyra-x4-v1:0",
    "palmyra x5": "writer.palmyra-x5-v1:0",
}

# AmazonBedrock: normalized ``model`` attribute -> model_id.
# Normalization: trim, lowercase, collapse internal whitespace
# (see ``_normalize_ab_model``). Matching is EXACT equality — e.g. "nova pro"
# must NOT match "Nova Pro Latency Optimized" or "Nova 2.0 Pro".
CURATED_AB_MODEL_MAP: dict[str, str] = {
    "nova pro": "amazon.nova-pro-v1:0",
    "nova lite": "amazon.nova-lite-v1:0",
    "nova micro": "amazon.nova-micro-v1:0",
    "nova premier": "amazon.nova-premier-v1:0",
    "llama 3.1 8b": "meta.llama3-1-8b-instruct-v1:0",
    "llama 3.1 70b": "meta.llama3-1-70b-instruct-v1:0",
    "llama 3.3 70b": "meta.llama3-3-70b-instruct-v1:0",
    "llama 4 maverick 17b": "meta.llama4-maverick-17b-instruct-v1:0",
    "llama 4 scout 17b": "meta.llama4-scout-17b-instruct-v1:0",
    "mistral large": "mistral.mistral-large-2402-v1:0",
    "mistral large 2407": "mistral.mistral-large-2407-v1:0",
    "mistral large 3": "mistral.mistral-large-3-675b-instruct",
    "mistral small": "mistral.mistral-small-2402-v1:0",
    "ministral 3b 3.0": "mistral.ministral-3-3b-instruct",
    "ministral 8b 3.0": "mistral.ministral-3-8b-instruct",
    "ministral 14b 3.0": "mistral.ministral-3-14b-instruct",
    "devstral": "mistral.devstral-2-123b",
    "gemma 3 4b": "google.gemma-3-4b-it",
    "gemma 3 12b": "google.gemma-3-12b-it",
    "gemma 3 27b": "google.gemma-3-27b-it",
    "r1": "deepseek.r1-v1:0",
    "deepseek v3.1": "deepseek.v3-v1:0",
    "deepseek v3.2": "deepseek.v3.2",
    "qwen3 32b": "qwen.qwen3-32b-v1:0",
    "qwen3 235b a22b 2507": "qwen.qwen3-235b-a22b-2507-v1:0",
    "qwen3 next 80b a3b": "qwen.qwen3-next-80b-a3b",
    "qwen3 vl 235b a22b": "qwen.qwen3-vl-235b-a22b",
    "qwen3 coder 30b a3b": "qwen.qwen3-coder-30b-a3b-v1:0",
    "qwen3 coder 480b a35b": "qwen.qwen3-coder-480b-a35b-v1:0",
    "glm 4.7": "zai.glm-4.7",
    "glm 4.7 flash": "zai.glm-4.7-flash",
    "glm 5": "zai.glm-5",
    "kimi k2 thinking": "moonshot.kimi-k2-thinking",
    "kimi k2.5": "moonshotai.kimi-k2.5",
    "minimax m2": "minimax.minimax-m2",
    "minimax m2.1": "minimax.minimax-m2.1",
    "minimax m2.5": "minimax.minimax-m2.5",
    "nvidia nemotron 3 super 120b a12b": "nvidia.nemotron-super-3-120b",
    "gpt-oss-120b": "openai.gpt-oss-120b-1:0",
    "gpt-oss-20b": "openai.gpt-oss-20b-1:0",
    "gpt oss safeguard 120b": "openai.gpt-oss-safeguard-120b",
    "gpt oss safeguard 20b": "openai.gpt-oss-safeguard-20b",
    "writer palmyra vision 7b": "writer.palmyra-vision-7b",
}

# Every model_id in DEFAULT_PRICING (backend/app/routes/pricing.py) MUST also
# appear as a value in CURATED_FM_NAME_MAP or CURATED_AB_MODEL_MAP above.
# Without a curated-map entry, sync_pricing() can never refresh that model's
# price from AWS — it silently stays pinned to its seeded default forever with
# no error surfaced anywhere. Enforced by
# test_pricing_sync_service.py::TestDefaultPricingCoverage so a future addition
# to one list without the other fails CI instead of shipping a silent gap.

# Suffix stripped from FM servicenames before normalization/lookup.
_BEDROCK_EDITION_SUFFIX = " (amazon bedrock edition)"

# The four price dimensions we track, in canonical order.
_DIMENSIONS = ("input", "output", "cache_read", "cache_write")

# model_pricing column name for each parsed dimension.
_DIMENSION_TO_COLUMN = {
    "input": "input_token_price",
    "output": "output_token_price",
    "cache_read": "cache_read_token_price",
    "cache_write": "cache_write_token_price",
}

# Markers that make an AmazonBedrock usagetype/feature ineligible as an on-demand
# token price (checked against a lowercased "usagetype feature" blob).
_EXCLUDED_TIER_MARKERS = (
    "provisioned",
    "reserved",
    "batch",
    "modelunit",
    "model unit",
    "custom model import",
    "latency-optimized",
    "latency optimized",
)

# Qualifiers that make an AmazonBedrock on-demand usagetype "non-plain".
_TIER_QUALIFIERS = (
    "flex",
    "priority",
    "cross-region",
    "cross_region",
    "crossregion",
    "global",
)


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _normalize_servicename(servicename: str) -> str:
    """Normalize a FoundationModels ``servicename`` for curated-map lookup.

    Removes a trailing " (Amazon Bedrock Edition)", trims, lowercases, and
    collapses internal whitespace to single spaces.
    """
    if not servicename:
        return ""
    name = servicename.strip().lower()
    if name.endswith(_BEDROCK_EDITION_SUFFIX):
        name = name[: -len(_BEDROCK_EDITION_SUFFIX)]
    name = re.sub(r"\s+", " ", name).strip()
    return name


def _normalize_ab_model(model: str) -> str:
    """Normalize an AmazonBedrock ``model`` attribute for curated-map lookup.

    Trims, lowercases, and collapses internal whitespace. No suffix stripping —
    matching is EXACT against the curated keys.
    """
    if not model:
        return ""
    return re.sub(r"\s+", " ", model.strip().lower()).strip()


def _classify_usagetype(usagetype: str) -> Optional[str]:
    """Classify a Price List ``usagetype`` into a token price dimension.

    Returns one of ``"input"``, ``"output"``, ``"cache_read"``,
    ``"cache_write"``, or ``None`` for anything that is not a per-token price
    dimension. Classification is case-insensitive and works for both service
    codes (FoundationModels ``...InputTokenCount...`` and AmazonBedrock
    ``...input-token...``/``...input_token...`` variants, including tier
    suffixes).
    """
    if not usagetype:
        return None
    u = usagetype.lower()

    # Cache dimensions first — they also contain "inputtoken" substrings.
    if "cache-read" in u or "cache_read" in u or "cachereadinputtoken" in u:
        return "cache_read"
    if "cache-write" in u or "cache_write" in u or "cachewriteinputtoken" in u:
        return "cache_write"

    has_input = "inputtoken" in u or "input-token" in u or "input_token" in u
    if has_input and not any(
        bad in u
        for bad in ("cache", "output", "image", "batch", "provisioned", "reserved", "modelunit")
    ):
        return "input"

    has_output = "outputtoken" in u or "output-token" in u
    if has_output and not any(
        bad in u for bad in ("cache", "batch", "provisioned", "reserved", "modelunit")
    ):
        return "output"

    return None


def _to_per_1k(usd: float, unit: Optional[str], description: Optional[str]) -> Optional[float]:
    """Convert a raw USD price-per-unit to a per-1K-tokens price, unit-aware.

    * unit == "1K tokens"  -> already per-1K:   ``usd``
    * unit == "Units"       -> per-MILLION:      ``usd / 1000``
    * any other unit        -> not a per-token price: ``None``

    Belt-and-suspenders: if the unit is ambiguous, fall back to the description —
    "Million" implies per-million (``/1000``), "1K" implies per-1K (as-is).
    """
    unit_norm = (unit or "").strip().lower()
    if unit_norm == "1k tokens":
        return usd
    if unit_norm == "units":
        return usd / 1000.0

    desc = (description or "").lower()
    if "million" in desc:
        return usd / 1000.0
    if "1k" in desc:
        return usd
    return None


def _extract_ondemand_dimensions(
    terms: dict[str, Any]
) -> list[tuple[float, Optional[str], Optional[str]]]:
    """Extract every on-demand ``(usd, unit, description)`` triple from terms.

    Bedrock token products usually have a single on-demand price dimension, but
    tiered products may expose several; all valid ones are returned so the caller
    can pick.
    """
    out: list[tuple[float, Optional[str], Optional[str]]] = []
    ondemand = terms.get("OnDemand")
    if not isinstance(ondemand, dict):
        return out
    for term in ondemand.values():
        if not isinstance(term, dict):
            continue
        price_dimensions = term.get("priceDimensions")
        if not isinstance(price_dimensions, dict):
            continue
        for dimension in price_dimensions.values():
            if not isinstance(dimension, dict):
                continue
            price_per_unit = dimension.get("pricePerUnit")
            if not isinstance(price_per_unit, dict):
                continue
            usd = price_per_unit.get("USD")
            if usd is None:
                continue
            try:
                usd_f = float(usd)
            except (TypeError, ValueError):
                continue
            out.append((usd_f, dimension.get("unit"), dimension.get("description")))
    return out


def _is_excluded_tier(usagetype: str, feature: str) -> bool:
    """Return True if a usagetype/feature is not an eligible on-demand token price."""
    blob = f"{usagetype or ''} {feature or ''}".lower()
    return any(marker in blob for marker in _EXCLUDED_TIER_MARKERS)


def _select_candidate(
    candidates: list[dict[str, Any]], use_tier_logic: bool
) -> Optional[dict[str, Any]]:
    """Select one price candidate for a (model, dimension).

    Excluded tiers (provisioned / reserved / batch / model-units / custom import /
    latency-optimized) are dropped first. For FoundationModels
    (``use_tier_logic=False``) the first remaining candidate is returned. For
    AmazonBedrock (``use_tier_logic=True``) the preference order is:

        (a) usagetype containing "standard"
        (b) a "plain" on-demand usagetype (no flex/priority/cross-region/global)
        (c) "priority"
        (d) "flex"
        (e) "cross-region-global"
        (f) the first remaining candidate
    """
    eligible = [
        c for c in candidates if not _is_excluded_tier(c["usagetype"], c.get("feature", ""))
    ]
    if not eligible:
        return None
    if not use_tier_logic:
        return eligible[0]

    def find(pred: Callable[[str], bool]) -> Optional[dict[str, Any]]:
        for cand in eligible:
            if pred(cand["usagetype"].lower()):
                return cand
        return None

    return (
        find(lambda u: "standard" in u)
        or find(lambda u: not any(q in u for q in _TIER_QUALIFIERS))
        or find(lambda u: "priority" in u)
        or find(lambda u: "flex" in u)
        or find(lambda u: "cross-region" in u or "global" in u)
        or eligible[0]
    )


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class PricingSyncService:
    """Fetches Bedrock on-demand token prices from BOTH Price List service codes
    and upserts them into ``model_pricing``.
    """

    def __init__(self) -> None:
        # Reuse the settings-based session/credentials pattern, but FORCE the
        # Price List region (us-east-1) — the API is not available in us-west-2.
        session_kwargs: dict[str, Any] = {"region_name": _PRICE_LIST_REGION}
        if settings.aws_access_key_id:
            session_kwargs["aws_access_key_id"] = settings.aws_access_key_id
        if settings.aws_secret_access_key:
            session_kwargs["aws_secret_access_key"] = settings.aws_secret_access_key
        if settings.aws_session_token:
            session_kwargs["aws_session_token"] = settings.aws_session_token
        session = boto3.Session(**session_kwargs)
        self.client = session.client("pricing", region_name=_PRICE_LIST_REGION)

    # -----------------------------------------------------------------------
    # Fetch + parse
    # -----------------------------------------------------------------------

    def _fetch_products(self, service_code: str) -> list[dict[str, Any]]:
        """Fetch and JSON-decode all products for a Price List service code.

        Raises the underlying botocore error on failure so ``sync_pricing`` can
        translate it into an actionable summary error.
        """
        products: list[dict[str, Any]] = []
        paginator = self.client.get_paginator("get_products")
        pages = paginator.paginate(
            ServiceCode=service_code,
            Filters=[
                {
                    "Type": "TERM_MATCH",
                    "Field": "regionCode",
                    "Value": _price_region(),
                }
            ],
        )
        for page in pages:
            for entry in page.get("PriceList", []):
                try:
                    products.append(json.loads(entry))
                except (json.JSONDecodeError, TypeError):
                    logger.warning("Skipping unparseable PriceList entry")
                    continue
        return products

    def _collect_candidates(
        self, products: list[dict[str, Any]], key_fn: Callable[[dict[str, Any]], Optional[str]]
    ) -> dict[str, dict[str, list[dict[str, Any]]]]:
        """Accumulate per-key per-dimension price candidates from raw products.

        Returns ``{key: {dimension: [candidate, ...]}}`` where ``key`` is the raw
        name returned by ``key_fn`` (servicename for FM, model for AB) and each
        candidate is ``{usagetype, usd, unit, per_1k, feature}`` with a
        unit-aware, rounded per-1K price. Only per-token dimensions are kept.
        """
        acc: dict[str, dict[str, list[dict[str, Any]]]] = {}

        for product in products:
            attributes = (product.get("product") or {}).get("attributes") or {}
            key = key_fn(attributes)
            usagetype = attributes.get("usagetype")
            if not key or not usagetype:
                continue

            dimension = _classify_usagetype(usagetype)
            if dimension is None:
                continue

            feature = attributes.get("feature") or ""
            for usd, unit, description in _extract_ondemand_dimensions(
                product.get("terms") or {}
            ):
                per_1k = _to_per_1k(usd, unit, description)
                if per_1k is None:
                    continue
                candidate = {
                    "usagetype": usagetype,
                    "usd": usd,
                    "unit": unit or "",
                    "per_1k": round(per_1k, _PER_1K_PRECISION),
                    "feature": feature,
                }
                acc.setdefault(key, {}).setdefault(dimension, []).append(candidate)

        return acc

    def _build_model_prices(
        self,
        fm_acc: dict[str, dict[str, list[dict[str, Any]]]],
        ab_acc: dict[str, dict[str, list[dict[str, Any]]]],
    ) -> tuple[dict[str, dict[str, Any]], set[str]]:
        """Map raw keys to model_ids and select one candidate per dimension.

        Returns ``(model_data, unmatched)`` where ``model_data`` maps
        ``model_id -> {"source", "matchedName", "dims": {dimension: candidate}}``
        and ``unmatched`` is the set of token-priced raw names (from either
        service) that did not map to a known model_id.
        """
        model_data: dict[str, dict[str, Any]] = {}
        unmatched: set[str] = set()

        # FoundationModels — single on-demand token dimension per name.
        for servicename, dim_map in fm_acc.items():
            model_id = CURATED_FM_NAME_MAP.get(_normalize_servicename(servicename))
            if model_id is None:
                unmatched.add(servicename)
                continue
            dims: dict[str, dict[str, Any]] = {}
            for dimension, candidates in dim_map.items():
                chosen = _select_candidate(candidates, use_tier_logic=False)
                if chosen is not None:
                    dims[dimension] = chosen
            if dims:
                model_data[model_id] = {
                    "source": _SERVICE_CODE_FM,
                    "matchedName": servicename,
                    "dims": dims,
                }

        # AmazonBedrock — tier selection among on-demand candidates.
        for model_name, dim_map in ab_acc.items():
            model_id = CURATED_AB_MODEL_MAP.get(_normalize_ab_model(model_name))
            if model_id is None:
                unmatched.add(model_name)
                continue
            dims = {}
            for dimension, candidates in dim_map.items():
                chosen = _select_candidate(candidates, use_tier_logic=True)
                if chosen is not None:
                    dims[dimension] = chosen
            if not dims:
                continue
            if model_id in model_data:
                # Extremely unlikely (same model_id from both services); keep the
                # already-selected dims and only fill gaps.
                for dimension, candidate in dims.items():
                    model_data[model_id]["dims"].setdefault(dimension, candidate)
            else:
                model_data[model_id] = {
                    "source": _SERVICE_CODE_AB,
                    "matchedName": model_name,
                    "dims": dims,
                }

        return model_data, unmatched

    # -----------------------------------------------------------------------
    # Upsert
    # -----------------------------------------------------------------------

    def sync_pricing(self, db: Session) -> dict[str, Any]:
        """Sync Bedrock on-demand token pricing into ``model_pricing``.

        Queries BOTH Price List service codes, merges their results, and upserts
        per-model prices. Returns a summary dict describing which models were
        updated, created, or left unchanged (with a full per-dimension audit
        trail), plus any names that carried token prices but did not map to a
        known ``model_id`` (``unmatchedServiceNames``) and any errors.

        AWS failures do not raise — they are reported in the ``errors`` list with
        an actionable message (mentioning the required ``pricing:GetProducts``
        permission) so the endpoint can still return a 200 body describing the
        problem.
        """
        summary: dict[str, Any] = {
            "modelsUpdated": [],
            "modelsUnchanged": [],
            "modelsCreated": [],
            "unmatchedServiceNames": [],
            "trackedModelsMissingPrice": [],
            "errors": [],
            "pricingSource": _pricing_source(),
            "syncedAt": datetime.now(timezone.utc).isoformat(),
        }

        # Snapshot the set of model_ids already tracked in ``model_pricing`` at
        # sync time. After processing, any tracked model_id that AWS did not
        # update / create / leave-unchanged is the genuinely actionable list
        # (``trackedModelsMissingPrice``) — a curated map likely needs updating.
        tracked: set[str] = {
            row for row in db.execute(select(ModelPricing.model_id)).scalars().all()
        }

        # Fetch from BOTH service codes (AWS failures are captured, not raised).
        try:
            fm_products = self._fetch_products(_SERVICE_CODE_FM)
            ab_products = self._fetch_products(_SERVICE_CODE_AB)
        except (ClientError, BotoCoreError) as exc:
            code = ""
            if isinstance(exc, ClientError):
                code = exc.response.get("Error", {}).get("Code", "")
            message = (
                "Failed to fetch pricing from the AWS Price List API "
                f"(get_products on {_SERVICE_CODE_FM}/{_SERVICE_CODE_AB}"
                + (f", error code {code}" if code else "")
                + "). Ensure the task role has the 'pricing:GetProducts' "
                "permission and that the Price List API is reachable from "
                f"{_PRICE_LIST_REGION}."
            )
            logger.error("%s: %s", message, exc)
            summary["errors"].append(message)
            return summary
        except Exception as exc:  # defensive: never leak internals
            message = f"Unexpected error fetching pricing: {type(exc).__name__}"
            logger.error("%s: %s", message, exc)
            summary["errors"].append(message)
            return summary

        fm_acc = self._collect_candidates(
            fm_products, key_fn=lambda a: a.get("servicename")
        )
        ab_acc = self._collect_candidates(
            ab_products, key_fn=lambda a: a.get("model") or a.get("titanModel")
        )

        model_data, unmatched = self._build_model_prices(fm_acc, ab_acc)

        for model_id in sorted(model_data.keys()):
            data = model_data[model_id]
            self._upsert_model(
                db,
                model_id=model_id,
                source=data["source"],
                matched_name=data["matchedName"],
                dims=data["dims"],
                summary=summary,
            )

        # Commit once at the end so a failed row does not partially apply.
        try:
            db.commit()
        except Exception as exc:  # pragma: no cover - defensive
            db.rollback()
            message = f"Failed to persist pricing updates: {type(exc).__name__}"
            logger.error("%s: %s", message, exc)
            summary["errors"].append(message)
            # Reflect that nothing was actually persisted.
            summary["modelsUpdated"] = []
            summary["modelsCreated"] = []
            summary["modelsUnchanged"] = []

        summary["unmatchedServiceNames"] = sorted(unmatched)

        # Tracked models that AWS returned no mappable price for. modelsUpdated
        # / modelsCreated are lists of dicts keyed by "modelId"; modelsUnchanged
        # is a list of model_id strings.
        matched_tracked = (
            {m["modelId"] for m in summary["modelsUpdated"]}
            | {m["modelId"] for m in summary["modelsCreated"]}
            | set(summary["modelsUnchanged"])
        )
        summary["trackedModelsMissingPrice"] = sorted(tracked - matched_tracked)
        return summary

    def _upsert_model(
        self,
        db: Session,
        model_id: str,
        source: str,
        matched_name: str,
        dims: dict[str, dict[str, Any]],
        summary: dict[str, Any],
    ) -> None:
        """Upsert a single model's pricing row, updating only found dimensions.

        Appends a rich, auditable report (source, matchedName, and per-dimension
        chosen usagetype / raw USD / unit / per-1K / old->new) to the summary.
        """
        existing = db.execute(
            select(ModelPricing).where(ModelPricing.model_id == model_id)
        ).scalar_one_or_none()

        if existing is None:
            # Only create when we have at least input + output prices — a partial
            # row (e.g. cache-only) would be misleading.
            if "input" not in dims or "output" not in dims:
                logger.info(
                    "Skipping creation of %s (matchedName=%s): missing input/output price",
                    model_id,
                    matched_name,
                )
                return
            new_row = ModelPricing(
                model_id=model_id,
                input_token_price=dims["input"]["per_1k"],
                output_token_price=dims["output"]["per_1k"],
                cache_read_token_price=dims.get("cache_read", {}).get("per_1k", 0),
                cache_write_token_price=dims.get("cache_write", {}).get("per_1k", 0),
                is_default=False,
            )
            db.add(new_row)
            dimension_report = {
                dimension: self._dimension_report(dims[dimension], old_value=None)
                for dimension in _DIMENSIONS
                if dimension in dims
            }
            summary["modelsCreated"].append(
                {
                    "modelId": model_id,
                    "source": source,
                    "matchedName": matched_name,
                    "dimensions": dimension_report,
                }
            )
            return

        # Update ONLY the dimensions found in the API; never zero-out a missing
        # dimension.
        dimension_report: dict[str, dict[str, Any]] = {}
        changed = False
        for dimension in _DIMENSIONS:
            if dimension not in dims:
                continue
            candidate = dims[dimension]
            column = _DIMENSION_TO_COLUMN[dimension]
            old_value = round(float(getattr(existing, column)), _PER_1K_PRECISION)
            new_value = candidate["per_1k"]
            if abs(old_value - new_value) > _PRICE_EPSILON:
                setattr(existing, column, new_value)
                changed = True
            dimension_report[dimension] = self._dimension_report(
                candidate, old_value=old_value
            )

        # Synced rows are authoritative — no longer a seeded default.
        if bool(existing.is_default):
            existing.is_default = False

        if changed:
            summary["modelsUpdated"].append(
                {
                    "modelId": model_id,
                    "source": source,
                    "matchedName": matched_name,
                    "dimensions": dimension_report,
                }
            )
        else:
            summary["modelsUnchanged"].append(model_id)

    @staticmethod
    def _dimension_report(
        candidate: dict[str, Any], old_value: Optional[float]
    ) -> dict[str, Any]:
        """Build the per-dimension audit record for the summary."""
        new_value = candidate["per_1k"]
        return {
            "usagetype": candidate["usagetype"],
            "usd": candidate["usd"],
            "unit": candidate["unit"],
            "per1k": new_value,
            "old": old_value,
            "new": new_value,
        }
