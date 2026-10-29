"""Unit tests for PricingSyncService (AWS Price List API sync).

The boto3 ``pricing`` client's paginator is mocked to return crafted PriceList
JSON strings for BOTH service codes (``AmazonBedrockFoundationModels`` and
``AmazonBedrock``) so the parse / classify / unit-convert / tier-select / upsert
logic can be validated in isolation, without any AWS calls.
"""

import json
import uuid
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, StaticPool
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import ModelPricing
from app.services.pricing_sync_service import (
    CURATED_AB_MODEL_MAP,
    CURATED_FM_NAME_MAP,
    PricingSyncService,
    _classify_usagetype,
    _normalize_ab_model,
    _normalize_servicename,
    _to_per_1k,
)

_FM = "AmazonBedrockFoundationModels"
_AB = "AmazonBedrock"


# ---------------------------------------------------------------------------
# In-memory SQLite database for testing.
# ---------------------------------------------------------------------------

engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db_session(setup_db):
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


# ---------------------------------------------------------------------------
# PriceList entry builders
# ---------------------------------------------------------------------------


def _fm_product(
    servicename: str,
    usagetype: str,
    usd: str,
    unit: str = "Units",
    description: str = "$X per Million tokens",
) -> str:
    """FoundationModels product: keyed by servicename, priced per MILLION."""
    return json.dumps(
        {
            "product": {
                "attributes": {
                    "servicename": servicename,
                    "usagetype": usagetype,
                }
            },
            "terms": {
                "OnDemand": {
                    "TK.JRTCKXETXF": {
                        "priceDimensions": {
                            "TK.JRTCKXETXF.6YS6EN2CT7": {
                                "unit": unit,
                                "description": description,
                                "pricePerUnit": {"USD": usd},
                            }
                        }
                    }
                }
            },
        }
    )


def _ab_product(
    model: str,
    usagetype: str,
    usd: str,
    unit: str = "1K tokens",
    feature: str = "",
    description: str = "$X per 1K tokens",
) -> str:
    """AmazonBedrock product: keyed by model attribute, priced per 1K."""
    attributes = {"model": model, "usagetype": usagetype}
    if feature:
        attributes["feature"] = feature
    return json.dumps(
        {
            "product": {"attributes": attributes},
            "terms": {
                "OnDemand": {
                    "TK.ABCDEF": {
                        "priceDimensions": {
                            "TK.ABCDEF.1A2B3C": {
                                "unit": unit,
                                "description": description,
                                "pricePerUnit": {"USD": usd},
                            }
                        }
                    }
                }
            },
        }
    )


def _make_service(fm_products=None, ab_products=None) -> PricingSyncService:
    """Construct a PricingSyncService whose paginator returns per-service products."""
    fm_products = fm_products or []
    ab_products = ab_products or []

    def paginate(**kwargs):
        service_code = kwargs.get("ServiceCode")
        if service_code == _FM:
            return [{"PriceList": fm_products}]
        if service_code == _AB:
            return [{"PriceList": ab_products}]
        return [{"PriceList": []}]

    service = PricingSyncService()
    paginator = MagicMock()
    paginator.paginate.side_effect = paginate
    client = MagicMock()
    client.get_paginator.return_value = paginator
    service.client = client
    return service


# FoundationModels servicename that maps to Claude Sonnet 4.5.
SONNET45_NAME = "Claude Sonnet 4.5 (Amazon Bedrock Edition)"
SONNET45_ID = "anthropic.claude-sonnet-4-5-20250929-v1:0"

# A generic mapped FM name (Claude 3.5 Sonnet v1) used for shape tests.
SONNET_NAME = "Claude 3.5 Sonnet (Amazon Bedrock Edition)"
SONNET_ID = "anthropic.claude-3-5-sonnet-20240620-v1:0"

NOVA_LITE_ID = "amazon.nova-lite-v1:0"
NOVA_MICRO_ID = "amazon.nova-micro-v1:0"
NOVA_PRO_ID = "amazon.nova-pro-v1:0"


# ---------------------------------------------------------------------------
# Helper unit tests
# ---------------------------------------------------------------------------


class TestNormalize:
    def test_fm_strips_edition_suffix_and_lowercases(self):
        assert _normalize_servicename(SONNET45_NAME) == "claude sonnet 4.5"

    def test_fm_collapses_whitespace(self):
        assert _normalize_servicename("  Claude   3   Opus  ") == "claude 3 opus"

    def test_ab_model_normalizes_case_and_whitespace(self):
        assert _normalize_ab_model("  Nova   Lite ") == "nova lite"


class TestClaudeMythosCuratedMap:
    """Regression test for the "newest Claude models show no price" gap.

    AWS's real FoundationModels ``servicename`` values for the newest Claude
    releases ("Claude Mythos 5", "Claude Mythos Preview") had no curated-map
    entry, so ``sync_pricing()`` could never resolve a price for them even
    though AWS Price List actually carries pricing for them — they'd land in
    ``unmatchedServiceNames`` forever instead of updating ``model_pricing``.
    """

    def test_claude_mythos_5_is_mapped(self):
        name = "Claude Mythos 5 (Amazon Bedrock Edition)"
        assert _normalize_servicename(name) in CURATED_FM_NAME_MAP
        assert CURATED_FM_NAME_MAP[_normalize_servicename(name)] == "anthropic.claude-mythos-5"

    def test_claude_mythos_preview_is_mapped(self):
        name = "Claude Mythos Preview (Amazon Bedrock Edition)"
        assert _normalize_servicename(name) in CURATED_FM_NAME_MAP
        assert (
            CURATED_FM_NAME_MAP[_normalize_servicename(name)]
            == "anthropic.claude-mythos-preview"
        )

    def test_claude_mythos_5_sync_resolves_price(self, db_session):
        """End-to-end: a real AWS product for Claude Mythos 5 upserts a price."""
        name = "Claude Mythos 5 (Amazon Bedrock Edition)"
        service = _make_service(
            fm_products=[
                _fm_product(name, "USW2_InputTokenCount-Units", "5.00"),
                _fm_product(name, "USW2_OutputTokenCount-Units", "25.00"),
            ]
        )

        summary = service.sync_pricing(db_session)

        created_ids = {m["modelId"] for m in summary["modelsCreated"]}
        assert "anthropic.claude-mythos-5" in created_ids
        assert name not in summary["unmatchedServiceNames"]


class TestClassifyUsagetype:
    def test_input(self):
        assert _classify_usagetype("USW2_InputTokenCount-Units") == "input"
        assert _classify_usagetype("USW2-input-token-standard") == "input"

    def test_output(self):
        assert _classify_usagetype("USW2_OutputTokenCount-Units") == "output"
        assert _classify_usagetype("USW2-output-token-priority") == "output"

    def test_cache_read(self):
        assert _classify_usagetype("USW2_CacheReadInputTokenCount-Units") == "cache_read"
        assert _classify_usagetype("USW2-cache_read_tokens") == "cache_read"

    def test_cache_write(self):
        assert _classify_usagetype("USW2_CacheWriteInputTokenCount-Units") == "cache_write"
        assert _classify_usagetype("USW2-cache_write_tokens") == "cache_write"

    def test_batch_provisioned_reserved_modelunit_ignored(self):
        assert _classify_usagetype("USW2_BatchInputTokenCount-Units") is None
        assert _classify_usagetype("USW2_ProvisionedInputTokenCount") is None
        assert _classify_usagetype("USW2_ReservedOutputTokenCount") is None
        assert _classify_usagetype("USW2_ModelUnits") is None
        assert _classify_usagetype("USW2_Storage-GB") is None


class TestToPer1k:
    def test_units_is_per_million(self):
        assert _to_per_1k(3.30, "Units", "per Million tokens") == pytest.approx(0.0033)

    def test_1k_tokens_is_as_is(self):
        assert _to_per_1k(0.00006, "1K tokens", "per 1K tokens") == pytest.approx(0.00006)

    def test_other_units_skipped(self):
        assert _to_per_1k(1.0, "hour", "per hour") is None
        assert _to_per_1k(1.0, "image", "per image") is None
        assert _to_per_1k(1.0, "Model/month", "monthly") is None

    def test_ambiguous_unit_falls_back_to_description(self):
        assert _to_per_1k(3.30, "", "Price per Million input tokens") == pytest.approx(0.0033)
        assert _to_per_1k(0.00006, "", "Price per 1K input tokens") == pytest.approx(0.00006)
        assert _to_per_1k(1.0, "", "flat monthly fee") is None


# ---------------------------------------------------------------------------
# sync_pricing — core conversions across the two services
# ---------------------------------------------------------------------------


class TestSyncPricing:
    def test_fm_per_million_conversion(self, db_session):
        """FoundationModels 'Units' input 3.30 -> 0.0033 per-1K, mapped correctly."""
        db_session.add(
            ModelPricing(
                id=uuid.uuid4(),
                model_id=SONNET45_ID,
                input_token_price=0.001,
                output_token_price=0.001,
                cache_read_token_price=0.001,
                cache_write_token_price=0.001,
                is_default=True,
            )
        )
        db_session.commit()

        service = _make_service(
            fm_products=[
                _fm_product(SONNET45_NAME, "USW2_InputTokenCount-Units", "3.30"),
                _fm_product(SONNET45_NAME, "USW2_OutputTokenCount-Units", "16.50"),
            ]
        )

        summary = service.sync_pricing(db_session)

        row = (
            db_session.query(ModelPricing)
            .filter(ModelPricing.model_id == SONNET45_ID)
            .first()
        )
        assert float(row.input_token_price) == pytest.approx(0.0033)
        assert float(row.output_token_price) == pytest.approx(0.0165)
        assert row.is_default is False

        assert len(summary["modelsUpdated"]) == 1
        updated = summary["modelsUpdated"][0]
        assert updated["modelId"] == SONNET45_ID
        assert updated["source"] == _FM
        assert updated["matchedName"] == SONNET45_NAME
        idim = updated["dimensions"]["input"]
        assert idim["unit"] == "Units"
        assert idim["usd"] == pytest.approx(3.30)
        assert idim["per1k"] == pytest.approx(0.0033)
        assert idim["old"] == pytest.approx(0.001)
        assert idim["new"] == pytest.approx(0.0033)
        assert summary["errors"] == []

    def test_ab_per_1k_no_division(self, db_session):
        """AmazonBedrock '1K tokens' input 0.00006 -> 0.00006 per-1K (NO /1000)."""
        db_session.add(
            ModelPricing(
                id=uuid.uuid4(),
                model_id=NOVA_LITE_ID,
                input_token_price=0.001,
                output_token_price=0.001,
                cache_read_token_price=0.0,
                cache_write_token_price=0.0,
                is_default=True,
            )
        )
        db_session.commit()

        service = _make_service(
            ab_products=[
                _ab_product("Nova Lite", "USW2_InputTokenCount-standard", "0.00006"),
                _ab_product("Nova Lite", "USW2_OutputTokenCount-standard", "0.00024"),
            ]
        )

        summary = service.sync_pricing(db_session)

        row = (
            db_session.query(ModelPricing)
            .filter(ModelPricing.model_id == NOVA_LITE_ID)
            .first()
        )
        assert float(row.input_token_price) == pytest.approx(0.00006)
        assert float(row.output_token_price) == pytest.approx(0.00024)

        updated = summary["modelsUpdated"][0]
        assert updated["source"] == _AB
        assert updated["matchedName"] == "Nova Lite"
        assert updated["dimensions"]["input"]["unit"] == "1K tokens"
        assert updated["dimensions"]["input"]["per1k"] == pytest.approx(0.00006)

    def test_tier_selection_prefers_standard(self, db_session):
        """Among standard/priority/flex + excluded tiers, '-standard' wins."""
        service = _make_service(
            ab_products=[
                # Eligible on-demand tiers for input.
                _ab_product("Nova Micro", "USW2_InputTokenCount-priority", "0.0001"),
                _ab_product("Nova Micro", "USW2_InputTokenCount-flex", "0.00003"),
                _ab_product("Nova Micro", "USW2_InputTokenCount-standard", "0.00005"),
                # Excluded tiers must be ignored.
                _ab_product(
                    "Nova Micro",
                    "USW2_InputTokenCount-latency-optimized",
                    "0.999",
                    feature="Latency Optimized",
                ),
                _ab_product("Nova Micro", "USW2_ProvisionedInputTokenCount", "9.99"),
                _ab_product("Nova Micro", "USW2_BatchInputTokenCount-standard", "0.0"),
                # Output (single standard tier).
                _ab_product("Nova Micro", "USW2_OutputTokenCount-standard", "0.00014"),
            ]
        )

        summary = service.sync_pricing(db_session)

        row = (
            db_session.query(ModelPricing)
            .filter(ModelPricing.model_id == NOVA_MICRO_ID)
            .first()
        )
        # standard input (0.00005) chosen, not priority/flex/latency/provisioned.
        assert float(row.input_token_price) == pytest.approx(0.00005)

        created = summary["modelsCreated"][0]
        assert created["modelId"] == NOVA_MICRO_ID
        assert "standard" in created["dimensions"]["input"]["usagetype"].lower()
        assert created["dimensions"]["input"]["per1k"] == pytest.approx(0.00005)

    def test_exact_match_discipline(self, db_session):
        """Near-miss AB model names must NOT map; exact variant names must."""
        service = _make_service(
            ab_products=[
                # These must NOT map to amazon.nova-pro-v1:0.
                _ab_product(
                    "Nova Pro Latency Optimized",
                    "USW2_InputTokenCount-standard",
                    "0.001",
                    feature="Latency Optimized",
                ),
                _ab_product("Nova 2.0 Pro", "USW2_InputTokenCount-standard", "0.002"),
                _ab_product("Nova 2.0 Pro", "USW2_OutputTokenCount-standard", "0.008"),
                # Mistral Large 2407 must map to the 2407 model id, not 2402.
                _ab_product("Mistral Large 2407", "USW2_InputTokenCount-standard", "0.002"),
                _ab_product("Mistral Large 2407", "USW2_OutputTokenCount-standard", "0.006"),
                # Plain "Mistral Large" maps to 2402.
                _ab_product("Mistral Large", "USW2_InputTokenCount-standard", "0.004"),
                _ab_product("Mistral Large", "USW2_OutputTokenCount-standard", "0.012"),
            ]
        )

        summary = service.sync_pricing(db_session)

        # No nova-pro row created from the near-miss names.
        assert (
            db_session.query(ModelPricing)
            .filter(ModelPricing.model_id == NOVA_PRO_ID)
            .count()
            == 0
        )
        assert "Nova Pro Latency Optimized" in summary["unmatchedServiceNames"]
        assert "Nova 2.0 Pro" in summary["unmatchedServiceNames"]

        # Mistral Large 2407 -> 2407 id; Mistral Large -> 2402 id.
        m2407 = (
            db_session.query(ModelPricing)
            .filter(ModelPricing.model_id == "mistral.mistral-large-2407-v1:0")
            .first()
        )
        m2402 = (
            db_session.query(ModelPricing)
            .filter(ModelPricing.model_id == "mistral.mistral-large-2402-v1:0")
            .first()
        )
        assert m2407 is not None and float(m2407.input_token_price) == pytest.approx(0.002)
        assert m2402 is not None and float(m2402.input_token_price) == pytest.approx(0.004)

    def test_non_token_units_skipped(self, db_session):
        """Token-classified usagetypes with unit hour/image are not applied."""
        db_session.add(
            ModelPricing(
                id=uuid.uuid4(),
                model_id=SONNET_ID,
                input_token_price=0.003,
                output_token_price=0.015,
                cache_read_token_price=0.0,
                cache_write_token_price=0.0,
                is_default=False,
            )
        )
        db_session.commit()

        service = _make_service(
            fm_products=[
                # These classify as token dims but carry non-token units -> skip.
                _fm_product(
                    SONNET_NAME, "USW2_InputTokenCount-Units", "99.0", unit="hour",
                    description="per hour",
                ),
                _fm_product(
                    SONNET_NAME, "USW2_OutputTokenCount-Units", "88.0", unit="image",
                    description="per image",
                ),
            ]
        )

        summary = service.sync_pricing(db_session)

        row = (
            db_session.query(ModelPricing)
            .filter(ModelPricing.model_id == SONNET_ID)
            .first()
        )
        # Prices unchanged — the hour/image dims were skipped by unit guard.
        assert float(row.input_token_price) == pytest.approx(0.003)
        assert float(row.output_token_price) == pytest.approx(0.015)
        assert summary["modelsUpdated"] == []
        assert summary["modelsCreated"] == []

    def test_missing_cache_dimension_does_not_zero_existing(self, db_session):
        """Only found dimensions are updated; a missing cache price is preserved."""
        db_session.add(
            ModelPricing(
                id=uuid.uuid4(),
                model_id=SONNET45_ID,
                input_token_price=0.001,
                output_token_price=0.002,
                cache_read_token_price=0.0009,
                cache_write_token_price=0.0007,
                is_default=False,
            )
        )
        db_session.commit()

        service = _make_service(
            fm_products=[
                _fm_product(SONNET45_NAME, "USW2_InputTokenCount-Units", "3.00"),
                _fm_product(SONNET45_NAME, "USW2_OutputTokenCount-Units", "6.00"),
            ]
        )

        service.sync_pricing(db_session)

        row = (
            db_session.query(ModelPricing)
            .filter(ModelPricing.model_id == SONNET45_ID)
            .first()
        )
        assert float(row.input_token_price) == pytest.approx(0.003)
        assert float(row.output_token_price) == pytest.approx(0.006)
        # Existing cache prices preserved, NOT zeroed.
        assert float(row.cache_read_token_price) == pytest.approx(0.0009)
        assert float(row.cache_write_token_price) == pytest.approx(0.0007)

    def test_unmatched_name_captured_no_row(self, db_session):
        """An unmapped token-priced name is surfaced, not applied."""
        unknown = "Some Brand New Model (Amazon Bedrock Edition)"
        service = _make_service(
            fm_products=[
                _fm_product(unknown, "USW2_InputTokenCount-Units", "1.00"),
                _fm_product(unknown, "USW2_OutputTokenCount-Units", "2.00"),
            ]
        )

        summary = service.sync_pricing(db_session)

        assert unknown in summary["unmatchedServiceNames"]
        assert summary["modelsCreated"] == []
        assert summary["modelsUpdated"] == []
        assert db_session.query(ModelPricing).count() == 0

    def test_creates_row_with_input_and_output(self, db_session):
        service = _make_service(
            fm_products=[
                _fm_product(SONNET_NAME, "USW2_InputTokenCount-Units", "3.00"),
                _fm_product(SONNET_NAME, "USW2_OutputTokenCount-Units", "15.00"),
            ]
        )

        summary = service.sync_pricing(db_session)

        created_ids = [m["modelId"] for m in summary["modelsCreated"]]
        assert SONNET_ID in created_ids
        row = (
            db_session.query(ModelPricing)
            .filter(ModelPricing.model_id == SONNET_ID)
            .first()
        )
        assert row is not None
        assert float(row.input_token_price) == pytest.approx(0.003)
        assert float(row.output_token_price) == pytest.approx(0.015)
        # New synced rows are never defaults.
        assert row.is_default is False

    def test_does_not_create_row_without_input_and_output(self, db_session):
        """A cache-only mapped model with no existing row is not created."""
        service = _make_service(
            fm_products=[
                _fm_product(SONNET_NAME, "USW2_CacheReadInputTokenCount-Units", "0.30"),
            ]
        )

        summary = service.sync_pricing(db_session)

        assert summary["modelsCreated"] == []
        assert db_session.query(ModelPricing).count() == 0

    def test_unchanged_when_prices_match(self, db_session):
        db_session.add(
            ModelPricing(
                id=uuid.uuid4(),
                model_id=SONNET_ID,
                input_token_price=0.003,
                output_token_price=0.015,
                cache_read_token_price=0.0,
                cache_write_token_price=0.0,
                is_default=False,
            )
        )
        db_session.commit()

        service = _make_service(
            fm_products=[
                _fm_product(SONNET_NAME, "USW2_InputTokenCount-Units", "3.00"),
                _fm_product(SONNET_NAME, "USW2_OutputTokenCount-Units", "15.00"),
            ]
        )

        summary = service.sync_pricing(db_session)

        assert SONNET_ID in summary["modelsUnchanged"]
        assert summary["modelsUpdated"] == []

    def test_merges_both_services(self, db_session):
        """Products from BOTH service codes are merged into one sync."""
        service = _make_service(
            fm_products=[
                _fm_product(SONNET45_NAME, "USW2_InputTokenCount-Units", "3.30"),
                _fm_product(SONNET45_NAME, "USW2_OutputTokenCount-Units", "16.50"),
            ],
            ab_products=[
                _ab_product("Nova Lite", "USW2_InputTokenCount-standard", "0.00006"),
                _ab_product("Nova Lite", "USW2_OutputTokenCount-standard", "0.00024"),
            ],
        )

        summary = service.sync_pricing(db_session)

        created_ids = {m["modelId"] for m in summary["modelsCreated"]}
        assert SONNET45_ID in created_ids
        assert NOVA_LITE_ID in created_ids
        sources = {m["modelId"]: m["source"] for m in summary["modelsCreated"]}
        assert sources[SONNET45_ID] == _FM
        assert sources[NOVA_LITE_ID] == _AB

    def test_aws_error_returned_in_summary_not_raised(self, db_session):
        from botocore.exceptions import ClientError

        service = PricingSyncService()
        client = MagicMock()
        paginator = MagicMock()
        paginator.paginate.side_effect = ClientError(
            {"Error": {"Code": "AccessDeniedException", "Message": "no perms"}},
            "GetProducts",
        )
        client.get_paginator.return_value = paginator
        service.client = client

        summary = service.sync_pricing(db_session)

        assert len(summary["errors"]) == 1
        assert "pricing:GetProducts" in summary["errors"][0]
        assert summary["pricingSource"]
        assert summary["syncedAt"]

    def test_summary_shape_and_source(self, db_session):
        service = _make_service()
        summary = service.sync_pricing(db_session)
        assert set(summary.keys()) == {
            "modelsUpdated",
            "modelsUnchanged",
            "modelsCreated",
            "unmatchedServiceNames",
            "trackedModelsMissingPrice",
            "errors",
            "pricingSource",
            "syncedAt",
        }
        # Assert against the current settings.aws_region rather than a hardcoded
        # region literal — the pricing source is resolved lazily now, so the test
        # passes regardless of what AWS_REGION the environment sets. Keeps the
        # CI region pin in .gitlab-ci.yml as belt-and-suspenders, not load-bearing.
        from app.config import settings as _settings

        assert _settings.aws_region in summary["pricingSource"]
        assert _FM in summary["pricingSource"]
        assert _AB in summary["pricingSource"]


class TestTrackedModelsMissingPrice:
    def test_tracked_model_without_aws_price_is_flagged(self, db_session):
        """A tracked row that AWS does NOT return appears in trackedModelsMissingPrice."""
        # Seed two tracked rows: one that AWS will return a price for, and one
        # that AWS will NOT return at all.
        db_session.add(
            ModelPricing(
                id=uuid.uuid4(),
                model_id=SONNET45_ID,
                input_token_price=0.001,
                output_token_price=0.001,
                cache_read_token_price=0.0,
                cache_write_token_price=0.0,
                is_default=True,
            )
        )
        db_session.add(
            ModelPricing(
                id=uuid.uuid4(),
                model_id=NOVA_PRO_ID,  # tracked, but AWS returns no price for it
                input_token_price=0.0008,
                output_token_price=0.0032,
                cache_read_token_price=0.0,
                cache_write_token_price=0.0,
                is_default=True,
            )
        )
        db_session.commit()

        service = _make_service(
            fm_products=[
                _fm_product(SONNET45_NAME, "USW2_InputTokenCount-Units", "3.30"),
                _fm_product(SONNET45_NAME, "USW2_OutputTokenCount-Units", "16.50"),
            ]
        )

        summary = service.sync_pricing(db_session)

        # NOVA_PRO_ID was tracked but never matched by AWS -> flagged.
        assert summary["trackedModelsMissingPrice"] == [NOVA_PRO_ID]
        # SONNET45_ID was matched (updated), so it is NOT flagged.
        assert SONNET45_ID not in summary["trackedModelsMissingPrice"]

    def test_empty_when_all_tracked_models_matched(self, db_session):
        """When every tracked model is matched, trackedModelsMissingPrice is empty."""
        db_session.add(
            ModelPricing(
                id=uuid.uuid4(),
                model_id=SONNET45_ID,
                input_token_price=0.001,
                output_token_price=0.001,
                cache_read_token_price=0.0,
                cache_write_token_price=0.0,
                is_default=True,
            )
        )
        db_session.commit()

        service = _make_service(
            fm_products=[
                _fm_product(SONNET45_NAME, "USW2_InputTokenCount-Units", "3.30"),
                _fm_product(SONNET45_NAME, "USW2_OutputTokenCount-Units", "16.50"),
            ]
        )

        summary = service.sync_pricing(db_session)

        assert summary["trackedModelsMissingPrice"] == []

    def test_unchanged_tracked_model_is_not_flagged(self, db_session):
        """A tracked model left unchanged (price matched) is not flagged."""
        db_session.add(
            ModelPricing(
                id=uuid.uuid4(),
                model_id=SONNET_ID,
                input_token_price=0.003,
                output_token_price=0.015,
                cache_read_token_price=0.0,
                cache_write_token_price=0.0,
                is_default=False,
            )
        )
        db_session.commit()

        service = _make_service(
            fm_products=[
                _fm_product(SONNET_NAME, "USW2_InputTokenCount-Units", "3.00"),
                _fm_product(SONNET_NAME, "USW2_OutputTokenCount-Units", "15.00"),
            ]
        )

        summary = service.sync_pricing(db_session)

        assert SONNET_ID in summary["modelsUnchanged"]
        assert summary["trackedModelsMissingPrice"] == []


# ---------------------------------------------------------------------------
# DEFAULT_PRICING <-> curated-map coverage (prevents silent sync gaps)
# ---------------------------------------------------------------------------


class TestDefaultPricingCoverage:
    def test_every_default_pricing_model_is_in_a_curated_map(self):
        """Every seeded model_id must be reachable by sync_pricing().

        A model_id present in DEFAULT_PRICING but absent from BOTH curated maps
        can never have its price refreshed from AWS — it silently stays pinned
        to its seeded default forever, with no error surfaced anywhere. This
        guards against that exact class of gap recurring.
        """
        from app.routes.pricing import DEFAULT_PRICING

        curated_ids = set(CURATED_FM_NAME_MAP.values()) | set(CURATED_AB_MODEL_MAP.values())
        default_ids = {entry["model_id"] for entry in DEFAULT_PRICING}

        missing = sorted(default_ids - curated_ids)
        assert missing == [], (
            f"DEFAULT_PRICING model_id(s) {missing} have no curated-map entry "
            "in pricing_sync_service.py — sync_pricing() can never refresh "
            "their price from AWS. Add an entry to CURATED_FM_NAME_MAP or "
            "CURATED_AB_MODEL_MAP."
        )
