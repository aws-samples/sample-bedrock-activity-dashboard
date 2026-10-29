"""Unit tests for CostService."""

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from app.services.cost_service import (
    BATCH_INFERENCE_DISCOUNT_RATE,
    CostService,
    calculate_batch_price,
)


def _make_record(
    model_id: str = "anthropic.claude-sonnet-4-20250514-v1:0",
    input_tokens: int = 100,
    output_tokens: int = 50,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    cost_estimate: float | None = None,
) -> dict:
    total = input_tokens + output_tokens + cache_read_tokens + cache_write_tokens
    return {
        "id": "test-id",
        "timestamp": "2024-01-15T10:30:00+00:00",
        "userIdentity": "arn:aws:iam::123456789012:user/alice",
        "userName": "alice",
        "modelId": model_id,
        "sourceApplication": "Kiro",
        "sourceIp": "10.0.0.1",
        "inputTokens": input_tokens,
        "outputTokens": output_tokens,
        "cacheReadTokens": cache_read_tokens,
        "cacheWriteTokens": cache_write_tokens,
        "totalTokens": total,
        "costEstimate": cost_estimate,
    }


SAMPLE_PRICING = {
    "anthropic.claude-sonnet-4-20250514-v1:0": {
        "inputTokenPrice": Decimal("0.003"),
        "outputTokenPrice": Decimal("0.015"),
        "cacheReadTokenPrice": Decimal("0.0003"),
        "cacheWriteTokenPrice": Decimal("0.00375"),
    },
    "anthropic.claude-haiku-3-20250101-v1:0": {
        "inputTokenPrice": Decimal("0.00025"),
        "outputTokenPrice": Decimal("0.00125"),
        "cacheReadTokenPrice": Decimal("0.0"),
        "cacheWriteTokenPrice": Decimal("0.0"),
    },
}


class TestCalculateCost:
    def test_basic_cost_calculation(self):
        record = _make_record(input_tokens=1000, output_tokens=500)
        cost = CostService.calculate_cost(record, SAMPLE_PRICING)
        # (1000 * 0.003 / 1000) + (500 * 0.015 / 1000) = 0.003 + 0.0075 = 0.0105
        assert cost == pytest.approx(0.0105)

    def test_cost_with_cache_tokens(self):
        record = _make_record(
            input_tokens=1000,
            output_tokens=500,
            cache_read_tokens=2000,
            cache_write_tokens=800,
        )
        cost = CostService.calculate_cost(record, SAMPLE_PRICING)
        # (1000*0.003/1000) + (500*0.015/1000) + (2000*0.0003/1000) + (800*0.00375/1000)
        # = 0.003 + 0.0075 + 0.0006 + 0.003 = 0.0141
        assert cost == pytest.approx(0.0141)

    def test_returns_none_when_no_pricing(self):
        record = _make_record(model_id="unknown-model")
        cost = CostService.calculate_cost(record, SAMPLE_PRICING)
        assert cost is None

    def test_arn_model_id_normalized_for_pricing_lookup(self):
        """ARN inference profile IDs should be normalized and matched to pricing."""
        arn_model_id = (
            "arn:aws:bedrock:us-west-2:123456789012:inference-profile/"
            "us.anthropic.claude-sonnet-4-20250514-v1:0"
        )
        record = _make_record(model_id=arn_model_id, input_tokens=1000, output_tokens=500)
        cost = CostService.calculate_cost(record, SAMPLE_PRICING)
        # Should resolve to anthropic.claude-sonnet-4-20250514-v1:0 pricing
        assert cost == pytest.approx(0.0105)

    def test_cross_region_prefix_stripped_for_pricing_lookup(self):
        """us./eu./ap. prefixed model IDs should match canonical pricing."""
        record = _make_record(
            model_id="eu.anthropic.claude-sonnet-4-20250514-v1:0",
            input_tokens=1000,
            output_tokens=500,
        )
        cost = CostService.calculate_cost(record, SAMPLE_PRICING)
        assert cost == pytest.approx(0.0105)

    def test_zero_tokens_with_pricing_returns_none(self):
        """Pricing exists but no usage -> None (N/A), not a misleading $0.00."""
        record = _make_record(
            input_tokens=0,
            output_tokens=0,
            cache_read_tokens=0,
            cache_write_tokens=0,
        )
        cost = CostService.calculate_cost(record, SAMPLE_PRICING)
        assert cost is None

    def test_only_input_tokens_returns_float(self):
        """Any real usage (even input-only) yields a float cost."""
        record = _make_record(
            input_tokens=1000,
            output_tokens=0,
            cache_read_tokens=0,
            cache_write_tokens=0,
        )
        cost = CostService.calculate_cost(record, SAMPLE_PRICING)
        # 1000 * 0.003 / 1000 = 0.003
        assert cost == pytest.approx(0.003)
        assert isinstance(cost, float)

    def test_trailing_version_suffix_fallback(self):
        """A model ID with an unseeded ':0' variant resolves via suffix strip."""
        pricing = {
            "anthropic.claude-3-5-sonnet-20241022-v2": {
                "inputTokenPrice": Decimal("0.003"),
                "outputTokenPrice": Decimal("0.015"),
                "cacheReadTokenPrice": Decimal("0.0"),
                "cacheWriteTokenPrice": Decimal("0.0"),
            }
        }
        record = _make_record(
            model_id="us.anthropic.claude-3-5-sonnet-20241022-v2:0",
            input_tokens=1000,
            output_tokens=0,
        )
        cost = CostService.calculate_cost(record, pricing)
        assert cost == pytest.approx(0.003)

    def test_alias_fallback(self):
        """A short family ID resolves to the canonical dated pricing row."""
        pricing = {
            "anthropic.claude-3-5-sonnet-20241022-v2:0": {
                "inputTokenPrice": Decimal("0.003"),
                "outputTokenPrice": Decimal("0.015"),
                "cacheReadTokenPrice": Decimal("0.0"),
                "cacheWriteTokenPrice": Decimal("0.0"),
            }
        }
        record = _make_record(
            model_id="anthropic.claude-3-5-sonnet",
            input_tokens=1000,
            output_tokens=0,
        )
        cost = CostService.calculate_cost(record, pricing)
        assert cost == pytest.approx(0.003)

    def test_cost_with_different_model(self):
        record = _make_record(
            model_id="anthropic.claude-haiku-3-20250101-v1:0",
            input_tokens=1000,
            output_tokens=500,
        )
        cost = CostService.calculate_cost(record, SAMPLE_PRICING)
        # (1000 * 0.00025 / 1000) + (500 * 0.00125 / 1000) = 0.00025 + 0.000625 = 0.000875
        assert cost == pytest.approx(0.000875)

    def test_empty_pricing_dict(self):
        record = _make_record()
        cost = CostService.calculate_cost(record, {})
        assert cost is None

    def test_missing_token_fields_returns_none(self):
        """No token fields means no usage to price -> None."""
        record = {"modelId": "anthropic.claude-sonnet-4-20250514-v1:0"}
        cost = CostService.calculate_cost(record, SAMPLE_PRICING)
        assert cost is None

    def test_cost_is_float(self):
        record = _make_record(input_tokens=1000, output_tokens=500)
        cost = CostService.calculate_cost(record, SAMPLE_PRICING)
        assert isinstance(cost, float)

    def test_large_token_counts(self):
        record = _make_record(
            input_tokens=1_000_000,
            output_tokens=500_000,
            cache_read_tokens=200_000,
            cache_write_tokens=100_000,
        )
        cost = CostService.calculate_cost(record, SAMPLE_PRICING)
        # (1000000*0.003/1000) + (500000*0.015/1000) + (200000*0.0003/1000) + (100000*0.00375/1000)
        # = 3.0 + 7.5 + 0.06 + 0.375 = 10.935
        assert cost == pytest.approx(10.935)


class TestLoadPricing:
    def test_loads_pricing_from_db(self):
        mock_row = MagicMock()
        mock_row.model_id = "model-1"
        mock_row.input_token_price = Decimal("0.003")
        mock_row.output_token_price = Decimal("0.015")
        mock_row.cache_read_token_price = Decimal("0.0003")
        mock_row.cache_write_token_price = Decimal("0.00375")

        mock_db = MagicMock()
        mock_db.query.return_value.all.return_value = [mock_row]

        pricing = CostService.load_pricing(mock_db)
        assert "model-1" in pricing
        assert pricing["model-1"]["inputTokenPrice"] == Decimal("0.003")
        assert pricing["model-1"]["outputTokenPrice"] == Decimal("0.015")
        assert pricing["model-1"]["cacheReadTokenPrice"] == Decimal("0.0003")
        assert pricing["model-1"]["cacheWriteTokenPrice"] == Decimal("0.00375")

    def test_empty_db_returns_empty_dict(self):
        mock_db = MagicMock()
        mock_db.query.return_value.all.return_value = []

        pricing = CostService.load_pricing(mock_db)
        assert pricing == {}

    def test_multiple_models(self):
        row1 = MagicMock()
        row1.model_id = "model-a"
        row1.input_token_price = Decimal("0.001")
        row1.output_token_price = Decimal("0.002")
        row1.cache_read_token_price = Decimal("0.0")
        row1.cache_write_token_price = Decimal("0.0")

        row2 = MagicMock()
        row2.model_id = "model-b"
        row2.input_token_price = Decimal("0.01")
        row2.output_token_price = Decimal("0.02")
        row2.cache_read_token_price = Decimal("0.005")
        row2.cache_write_token_price = Decimal("0.005")

        mock_db = MagicMock()
        mock_db.query.return_value.all.return_value = [row1, row2]

        pricing = CostService.load_pricing(mock_db)
        assert len(pricing) == 2
        assert "model-a" in pricing
        assert "model-b" in pricing


class TestApplyCosts:
    def test_applies_costs_to_all_records(self):
        mock_row = MagicMock()
        mock_row.model_id = "anthropic.claude-sonnet-4-20250514-v1:0"
        mock_row.input_token_price = Decimal("0.003")
        mock_row.output_token_price = Decimal("0.015")
        mock_row.cache_read_token_price = Decimal("0.0003")
        mock_row.cache_write_token_price = Decimal("0.00375")

        mock_db = MagicMock()
        mock_db.query.return_value.all.return_value = [mock_row]

        records = [
            _make_record(input_tokens=1000, output_tokens=500),
            _make_record(input_tokens=2000, output_tokens=1000),
        ]

        result = CostService.apply_costs(records, mock_db)

        assert len(result) == 2
        assert result[0]["costEstimate"] == pytest.approx(0.0105)
        # (2000*0.003/1000) + (1000*0.015/1000) = 0.006 + 0.015 = 0.021
        assert result[1]["costEstimate"] == pytest.approx(0.021)

    def test_sets_none_for_unknown_models(self):
        mock_db = MagicMock()
        mock_db.query.return_value.all.return_value = []

        records = [_make_record(model_id="unknown-model")]
        result = CostService.apply_costs(records, mock_db)

        assert result[0]["costEstimate"] is None

    def test_mixed_known_and_unknown_models(self):
        mock_row = MagicMock()
        mock_row.model_id = "known-model"
        mock_row.input_token_price = Decimal("0.001")
        mock_row.output_token_price = Decimal("0.002")
        mock_row.cache_read_token_price = Decimal("0.0")
        mock_row.cache_write_token_price = Decimal("0.0")

        mock_db = MagicMock()
        mock_db.query.return_value.all.return_value = [mock_row]

        records = [
            _make_record(model_id="known-model", input_tokens=1000, output_tokens=500),
            _make_record(model_id="unknown-model", input_tokens=1000, output_tokens=500),
        ]

        result = CostService.apply_costs(records, mock_db)

        # known-model: (1000*0.001/1000) + (500*0.002/1000) = 0.001 + 0.001 = 0.002
        assert result[0]["costEstimate"] == pytest.approx(0.002)
        assert result[1]["costEstimate"] is None

    def test_empty_records(self):
        mock_db = MagicMock()
        mock_db.query.return_value.all.return_value = []

        result = CostService.apply_costs([], mock_db)
        assert result == []

    def test_mutates_records_in_place(self):
        mock_db = MagicMock()
        mock_db.query.return_value.all.return_value = []

        records = [_make_record(model_id="unknown")]
        original = records[0]
        CostService.apply_costs(records, mock_db)

        assert original["costEstimate"] is None
        assert records[0] is original


# ---------------------------------------------------------------------------
# Tests: get_used_model_ids / find_pricing_gaps (usage-driven pricing gaps)
# ---------------------------------------------------------------------------

class TestGetUsedModelIds:
    def test_returns_distinct_normalized_ids(self):
        records = [
            _make_record(model_id="anthropic.claude-sonnet-4-20250514-v1:0"),
            _make_record(model_id="us.anthropic.claude-sonnet-4-20250514-v1:0"),
            _make_record(model_id="amazon.nova-pro-v1:0"),
        ]
        result = CostService.get_used_model_ids(records)
        # The ARN-prefixed and cross-region-prefixed variant of the same model
        # normalize to one entry, not two.
        assert result == {
            "anthropic.claude-sonnet-4-20250514-v1:0",
            "amazon.nova-pro-v1:0",
        }

    def test_empty_records_yields_empty_set(self):
        assert CostService.get_used_model_ids([]) == set()

    def test_missing_or_blank_model_id_is_skipped(self):
        records = [
            {"modelId": ""},
            {},
            _make_record(model_id="amazon.nova-lite-v1:0"),
        ]
        assert CostService.get_used_model_ids(records) == {"amazon.nova-lite-v1:0"}

    def test_arn_form_normalizes_to_bare_model_id(self):
        records = [
            _make_record(
                model_id=(
                    "arn:aws:bedrock:us-west-2:123456789012:inference-profile/"
                    "us.anthropic.claude-opus-4-6-v1"
                )
            ),
        ]
        assert CostService.get_used_model_ids(records) == {"anthropic.claude-opus-4-6-v1"}


class TestFindPricingGaps:
    def test_model_with_no_pricing_row_is_a_gap(self):
        pricing: dict = {}
        gaps = CostService.find_pricing_gaps({"some.brand-new-model-v1:0"}, pricing)
        assert gaps == ["some.brand-new-model-v1:0"]

    def test_model_with_a_pricing_row_is_not_a_gap(self):
        pricing = {
            "amazon.nova-pro-v1:0": {
                "inputTokenPrice": Decimal("0.0008"),
                "outputTokenPrice": Decimal("0.0032"),
                "cacheReadTokenPrice": Decimal("0.0002"),
                "cacheWriteTokenPrice": Decimal("0.0"),
            }
        }
        gaps = CostService.find_pricing_gaps({"amazon.nova-pro-v1:0"}, pricing)
        assert gaps == []

    def test_model_resolvable_via_alias_is_not_a_gap(self):
        """A used model that CostService.calculate_cost WOULD resolve (via the
        same fallback chain _resolve_pricing uses) must not be reported as
        missing pricing — only genuine "would show N/A" cases are gaps."""
        pricing = {
            "anthropic.claude-3-5-sonnet-20241022-v2:0": {
                "inputTokenPrice": Decimal("0.003"),
                "outputTokenPrice": Decimal("0.015"),
                "cacheReadTokenPrice": Decimal("0.0003"),
                "cacheWriteTokenPrice": Decimal("0.00375"),
            }
        }
        # "anthropic.claude-3-5-sonnet" is a known alias for the dated model_id.
        gaps = CostService.find_pricing_gaps({"anthropic.claude-3-5-sonnet"}, pricing)
        assert gaps == []

    def test_gaps_are_sorted(self):
        pricing: dict = {}
        gaps = CostService.find_pricing_gaps(
            {"z.model-v1:0", "a.model-v1:0", "m.model-v1:0"}, pricing
        )
        assert gaps == ["a.model-v1:0", "m.model-v1:0", "z.model-v1:0"]

    def test_empty_used_ids_yields_no_gaps(self):
        assert CostService.find_pricing_gaps(set(), {}) == []


# ---------------------------------------------------------------------------
# Tests: calculate_batch_price
# ---------------------------------------------------------------------------

class TestCalculateBatchPrice:
    def test_applies_the_configured_discount_rate(self):
        assert calculate_batch_price(0.003) == pytest.approx(
            0.003 * (1 - BATCH_INFERENCE_DISCOUNT_RATE)
        )

    def test_current_discount_rate_is_fifty_percent(self):
        # Pins the documented ~50% Bedrock batch-inference discount; if AWS
        # pricing changes, update BATCH_INFERENCE_DISCOUNT_RATE and this test.
        assert calculate_batch_price(0.01) == pytest.approx(0.005)

    def test_zero_price_stays_zero(self):
        assert calculate_batch_price(0.0) == 0.0

    def test_never_produces_a_negative_price(self):
        for price in (0.0, 0.0001, 0.003, 0.015, 0.075, 1.0):
            assert calculate_batch_price(price) >= 0.0

    def test_batch_price_never_exceeds_on_demand_price(self):
        for price in (0.0, 0.0001, 0.003, 0.015, 0.075, 1.0):
            assert calculate_batch_price(price) <= price
