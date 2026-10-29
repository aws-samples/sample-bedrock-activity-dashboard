"""Model Pricing CRUD API routes for the Bedrock Activity Dashboard."""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import CurrentUser, get_current_user, require_admin
from app.database import get_db
from app.models import ModelPricing
from app.services.cost_service import CostService, calculate_batch_price
from app.services.filter_service import parse_iso_to_utc
from app.services.pricing_sync_service import PricingSyncService

router = APIRouter(prefix="/api/pricing", tags=["pricing"])

# Default lookback window for GET /api/pricing/usage-gaps when no explicit
# start/end is supplied — wide enough to catch anything invoked recently
# without scanning the account's entire history on every unfiltered call.
_USAGE_GAPS_DEFAULT_WINDOW = timedelta(days=30)


# ---------------------------------------------------------------------------
# Default pricing for common models
# ---------------------------------------------------------------------------

DEFAULT_PRICING = [
    # Claude 3 Haiku
    {
        "model_id": "anthropic.claude-3-haiku-20240307-v1:0",
        "input_token_price": 0.00025,
        "output_token_price": 0.00125,
        "cache_read_token_price": 0.000030,
        "cache_write_token_price": 0.0003,
        "is_default": True,
    },
    # Claude 3.5 Haiku
    {
        "model_id": "anthropic.claude-3-5-haiku-20241022-v1:0",
        "input_token_price": 0.0008,
        "output_token_price": 0.004,
        "cache_read_token_price": 0.00008,
        "cache_write_token_price": 0.001,
        "is_default": True,
    },
    # Claude Haiku 4.5 (us.anthropic.claude-haiku-4-5-20251001-v1:0 normalizes to this)
    {
        "model_id": "anthropic.claude-haiku-4-5-20251001-v1:0",
        "input_token_price": 0.0008,
        "output_token_price": 0.004,
        "cache_read_token_price": 0.00008,
        "cache_write_token_price": 0.001,
        "is_default": True,
    },
    # Claude 3.5 Sonnet v1 (original 20240620 release)
    {
        "model_id": "anthropic.claude-3-5-sonnet-20240620-v1:0",
        "input_token_price": 0.003,
        "output_token_price": 0.015,
        "cache_read_token_price": 0.0003,
        "cache_write_token_price": 0.00375,
        "is_default": True,
    },
    # Claude 3.5 Sonnet v2
    {
        "model_id": "anthropic.claude-3-5-sonnet-20241022-v2:0",
        "input_token_price": 0.003,
        "output_token_price": 0.015,
        "cache_read_token_price": 0.0003,
        "cache_write_token_price": 0.00375,
        "is_default": True,
    },
    # Claude Sonnet 4.5
    {
        "model_id": "anthropic.claude-sonnet-4-5-20250929-v1:0",
        "input_token_price": 0.003,
        "output_token_price": 0.015,
        "cache_read_token_price": 0.0003,
        "cache_write_token_price": 0.00375,
        "is_default": True,
    },
    # Claude Sonnet 4 (claude-sonnet-4)
    {
        "model_id": "anthropic.claude-sonnet-4-20250514-v1:0",
        "input_token_price": 0.003,
        "output_token_price": 0.015,
        "cache_read_token_price": 0.0003,
        "cache_write_token_price": 0.00375,
        "is_default": True,
    },
    # Claude Sonnet 4.6 — inference profile short ID (us.anthropic.claude-sonnet-4-6 strips to this)
    {
        "model_id": "anthropic.claude-sonnet-4-6",
        "input_token_price": 0.003,
        "output_token_price": 0.015,
        "cache_read_token_price": 0.0003,
        "cache_write_token_price": 0.00375,
        "is_default": True,
    },
    # Claude 3.7 Sonnet
    {
        "model_id": "anthropic.claude-3-7-sonnet-20250219-v1:0",
        "input_token_price": 0.003,
        "output_token_price": 0.015,
        "cache_read_token_price": 0.0003,
        "cache_write_token_price": 0.00375,
        "is_default": True,
    },
    # Claude Opus 4 / Opus 4.6 (cross-region inference profile normalizes to this)
    {
        "model_id": "anthropic.claude-opus-4-6-v1",
        "input_token_price": 0.015,
        "output_token_price": 0.075,
        "cache_read_token_price": 0.0015,
        "cache_write_token_price": 0.01875,
        "is_default": True,
    },
    # Claude Opus 4 canonical
    {
        "model_id": "anthropic.claude-opus-4-20250514-v1:0",
        "input_token_price": 0.015,
        "output_token_price": 0.075,
        "cache_read_token_price": 0.0015,
        "cache_write_token_price": 0.01875,
        "is_default": True,
    },
    # Claude 3 Sonnet
    {
        "model_id": "anthropic.claude-3-sonnet-20240229-v1:0",
        "input_token_price": 0.003,
        "output_token_price": 0.015,
        "cache_read_token_price": 0.0003,
        "cache_write_token_price": 0.00375,
        "is_default": True,
    },
    # Claude 3 Opus
    {
        "model_id": "anthropic.claude-3-opus-20240229-v1:0",
        "input_token_price": 0.015,
        "output_token_price": 0.075,
        "cache_read_token_price": 0.0015,
        "cache_write_token_price": 0.01875,
        "is_default": True,
    },
    # Amazon Nova Pro
    {
        "model_id": "amazon.nova-pro-v1:0",
        "input_token_price": 0.0008,
        "output_token_price": 0.0032,
        "cache_read_token_price": 0.0002,
        "cache_write_token_price": 0.0,
        "is_default": True,
    },
    # Amazon Nova Lite
    {
        "model_id": "amazon.nova-lite-v1:0",
        "input_token_price": 0.00006,
        "output_token_price": 0.00024,
        "cache_read_token_price": 0.000015,
        "cache_write_token_price": 0.0,
        "is_default": True,
    },
    # Amazon Nova Micro
    {
        "model_id": "amazon.nova-micro-v1:0",
        "input_token_price": 0.000035,
        "output_token_price": 0.00014,
        "cache_read_token_price": 0.0,
        "cache_write_token_price": 0.0,
        "is_default": True,
    },
    # Amazon Nova Premier
    {
        "model_id": "amazon.nova-premier-v1:0",
        "input_token_price": 0.0025,
        "output_token_price": 0.0125,
        "cache_read_token_price": 0.00025,
        "cache_write_token_price": 0.0,
        "is_default": True,
    },
    # Google Gemma 3 27B
    {
        "model_id": "google.gemma-3-27b-it",
        "input_token_price": 0.0003,
        "output_token_price": 0.0006,
        "cache_read_token_price": 0.0,
        "cache_write_token_price": 0.0,
        "is_default": True,
    },
    # Meta Llama 3.1 8B
    {
        "model_id": "meta.llama3-1-8b-instruct-v1:0",
        "input_token_price": 0.00022,
        "output_token_price": 0.00022,
        "cache_read_token_price": 0.0,
        "cache_write_token_price": 0.0,
        "is_default": True,
    },
    # Meta Llama 3.1 70B
    {
        "model_id": "meta.llama3-1-70b-instruct-v1:0",
        "input_token_price": 0.00099,
        "output_token_price": 0.00099,
        "cache_read_token_price": 0.0,
        "cache_write_token_price": 0.0,
        "is_default": True,
    },
    # Meta Llama 3.3 70B
    {
        "model_id": "meta.llama3-3-70b-instruct-v1:0",
        "input_token_price": 0.00072,
        "output_token_price": 0.00072,
        "cache_read_token_price": 0.0,
        "cache_write_token_price": 0.0,
        "is_default": True,
    },
    # Mistral Large
    {
        "model_id": "mistral.mistral-large-2402-v1:0",
        "input_token_price": 0.004,
        "output_token_price": 0.012,
        "cache_read_token_price": 0.0,
        "cache_write_token_price": 0.0,
        "is_default": True,
    },
    # Mistral Large 2 (2407)
    {
        "model_id": "mistral.mistral-large-2407-v1:0",
        "input_token_price": 0.002,
        "output_token_price": 0.006,
        "cache_read_token_price": 0.0,
        "cache_write_token_price": 0.0,
        "is_default": True,
    },
    # Mistral Small
    {
        "model_id": "mistral.mistral-small-2402-v1:0",
        "input_token_price": 0.001,
        "output_token_price": 0.003,
        "cache_read_token_price": 0.0,
        "cache_write_token_price": 0.0,
        "is_default": True,
    },
]


def seed_default_pricing(db: Session) -> None:
    """Insert default pricing rows if they don't already exist."""
    for entry in DEFAULT_PRICING:
        existing = db.query(ModelPricing).filter(
            ModelPricing.model_id == entry["model_id"]
        ).first()
        if not existing:
            db.add(ModelPricing(id=uuid.uuid4(), **entry))
    db.commit()


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class UpsertPricingRequest(BaseModel):
    inputTokenPrice: float
    outputTokenPrice: float
    cacheReadTokenPrice: float = 0
    cacheWriteTokenPrice: float = 0


class DimensionReport(BaseModel):
    """Full audit trail for a single synced price dimension.

    Records the chosen Price List ``usagetype``, the raw USD price and its
    ``unit`` as quoted by the API, the computed per-1K value, and the old->new
    per-1K transition (``old`` is null for newly-created rows).
    """

    usagetype: str
    usd: float
    unit: str
    per1k: float
    old: Optional[float] = None
    new: float


class SyncedModel(BaseModel):
    """A model created or updated during a sync, with per-dimension detail."""

    modelId: str
    # Price List service code the price came from: "AmazonBedrock" or
    # "AmazonBedrockFoundationModels".
    source: str
    # The raw API name (FM servicename or AB model attribute) that matched.
    matchedName: str
    # Map of dimension name (input/output/cache_read/cache_write) -> detail.
    dimensions: dict[str, DimensionReport] = Field(default_factory=dict)


class PricingSyncSummary(BaseModel):
    """Summary returned by POST /api/pricing/sync."""

    modelsUpdated: list[SyncedModel] = Field(default_factory=list)
    modelsUnchanged: list[str] = Field(default_factory=list)
    modelsCreated: list[SyncedModel] = Field(default_factory=list)
    # AWS Bedrock model names that carry token prices but are not tracked by this
    # dashboard (expected noise — not modified).
    unmatchedServiceNames: list[str] = Field(default_factory=list)
    # Tracked model_ids (rows in model_pricing) for which AWS returned no mappable
    # price this sync — the actionable list; near-empty when curated maps are correct.
    trackedModelsMissingPrice: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    pricingSource: str
    syncedAt: str


class PricingGapsResponse(BaseModel):
    """Response for GET /api/pricing/usage-gaps.

    Reports which of the DISTINCT models actually invoked in the given window
    have no resolvable pricing row today (i.e. would show cost as "N/A" right
    now), rather than which models are merely absent from the static
    DEFAULT_PRICING/curated-name maps. This is the usage-driven signal those
    static lists don't provide on their own.
    """

    usedModelIds: list[str] = Field(
        default_factory=list,
        description="Distinct normalized model IDs invoked in the window.",
    )
    modelsMissingPricing: list[str] = Field(
        default_factory=list,
        description=(
            "Used model IDs with no resolvable pricing row (would price as "
            "N/A right now)."
        ),
    )
    windowStart: str = Field(description="ISO start of the window checked.")
    windowEnd: str = Field(description="ISO end of the window checked.")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pricing_to_response(p: ModelPricing) -> dict:
    """Convert a ModelPricing ORM object to the API response dict.

    Batch inference prices are derived at read time via
    ``cost_service.calculate_batch_price`` rather than stored, so admin edits
    to the on-demand price are automatically reflected in the batch price with
    no separate write path to keep in sync.
    """
    input_price = float(p.input_token_price)
    output_price = float(p.output_token_price)
    return {
        "modelId": p.model_id,
        "inputTokenPrice": input_price,
        "outputTokenPrice": output_price,
        "cacheReadTokenPrice": float(p.cache_read_token_price),
        "cacheWriteTokenPrice": float(p.cache_write_token_price),
        "isDefault": p.is_default,
        "batchInputTokenPrice": calculate_batch_price(input_price),
        "batchOutputTokenPrice": calculate_batch_price(output_price),
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("")
def list_pricing(db: Session = Depends(get_db)):
    """List all model pricing configurations."""
    rows = db.query(ModelPricing).order_by(ModelPricing.model_id).all()
    return [_pricing_to_response(r) for r in rows]


@router.get("/usage-gaps", response_model=PricingGapsResponse)
def get_usage_pricing_gaps(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> PricingGapsResponse:
    """Report distinct models actually invoked that have no resolvable pricing row.

    Pricing today is a closed set (the static ``DEFAULT_PRICING`` list plus
    ``PricingSyncService``'s curated AWS-name-to-model_id maps) with no
    connection to what this account is actually invoking — a model outside
    those lists silently shows cost as "N/A" forever with no signal to anyone.
    This endpoint closes that gap: it sources the same activity records every
    other tab uses, finds the distinct normalized model IDs actually invoked
    in the window, and cross-checks each against the current pricing table
    using the exact same resolution logic ``CostService.calculate_cost`` uses
    — so a reported gap is guaranteed to be a real "would show N/A" case, not
    a false positive from a model that resolves via normalization/alias.

    Available to any authenticated user (read-only, no PII) so the pricing
    tab can surface actionable gaps without requiring admin access first.
    """
    from app.routes.activity import source_activity_records_with_meta

    if start_date and end_date:
        start_dt = parse_iso_to_utc(start_date)
        end_dt = parse_iso_to_utc(end_date, end_of_day_if_bare=True)
    else:
        end_dt = datetime.now(timezone.utc)
        start_dt = end_dt - _USAGE_GAPS_DEFAULT_WINDOW

    ct_records, chat_records, _, _ = source_activity_records_with_meta(start_dt, end_dt, db)
    all_records = ct_records + chat_records

    used_model_ids = CostService.get_used_model_ids(all_records)
    pricing = CostService.load_pricing(db)
    missing = CostService.find_pricing_gaps(used_model_ids, pricing)

    return PricingGapsResponse(
        usedModelIds=sorted(used_model_ids),
        modelsMissingPricing=missing,
        windowStart=start_dt.isoformat(),
        windowEnd=end_dt.isoformat(),
    )


@router.post(
    "/sync",
    response_model=PricingSyncSummary,
    dependencies=[Depends(require_admin)],
)
def sync_pricing(db: Session = Depends(get_db)) -> PricingSyncSummary:
    """Sync model pricing from the AWS Price List API (admin only).

    Fetches real Bedrock foundation-model on-demand token prices and upserts them
    into ``model_pricing``. AWS failures are returned in the summary's ``errors``
    list rather than raising, so the caller always receives an actionable body.
    """
    summary = PricingSyncService().sync_pricing(db)
    return PricingSyncSummary(**summary)


@router.put("/{model_id:path}", dependencies=[Depends(require_admin)])
def upsert_pricing(model_id: str, body: UpsertPricingRequest, db: Session = Depends(get_db)):
    """Create or update pricing for a model (upsert)."""
    existing = db.query(ModelPricing).filter(ModelPricing.model_id == model_id).first()

    if existing:
        existing.input_token_price = body.inputTokenPrice
        existing.output_token_price = body.outputTokenPrice
        existing.cache_read_token_price = body.cacheReadTokenPrice
        existing.cache_write_token_price = body.cacheWriteTokenPrice
        # Once a user manually updates, it's no longer a default
        existing.is_default = False
    else:
        existing = ModelPricing(
            id=uuid.uuid4(),
            model_id=model_id,
            input_token_price=body.inputTokenPrice,
            output_token_price=body.outputTokenPrice,
            cache_read_token_price=body.cacheReadTokenPrice,
            cache_write_token_price=body.cacheWriteTokenPrice,
            is_default=False,
        )
        db.add(existing)

    db.commit()
    db.refresh(existing)
    return _pricing_to_response(existing)
