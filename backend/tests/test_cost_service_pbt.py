"""Property-based tests for CostService."""
# Feature: bedrock-activity-dashboard, Property 3: Cost Calculation Correctness


from hypothesis import given, settings

from app.services.cost_service import CostService
from tests.strategies import activity_record, pricing_config


class TestCostCalculationProperty:
    """Property 3: Cost Calculation Correctness.

    A cost is meaningful only when BOTH pricing AND usage are present:
      (a) no pricing               -> None
      (b) pricing but zero usage   -> None
      (c) pricing and usage > 0    -> equals the formula
    """

    @settings(max_examples=150, deadline=None)
    @given(record=activity_record(), pricing=pricing_config())
    def test_cost_matches_formula_when_usage_present(self, record, pricing):
        model_id = record["modelId"]
        pricing_map = {model_id: pricing}

        total_usage = (
            record["inputTokens"]
            + record["outputTokens"]
            + record["cacheReadTokens"]
            + record["cacheWriteTokens"]
        )

        result = CostService.calculate_cost(record, pricing_map)

        if total_usage == 0:
            # (b) pricing present but no usage -> None
            assert result is None
        else:
            # (c) pricing present with usage -> equals formula
            expected = float(
                (record["inputTokens"] * pricing["inputTokenPrice"] / 1000)
                + (record["outputTokens"] * pricing["outputTokenPrice"] / 1000)
                + (record["cacheReadTokens"] * pricing["cacheReadTokenPrice"] / 1000)
                + (record["cacheWriteTokens"] * pricing["cacheWriteTokenPrice"] / 1000)
            )
            assert result is not None
            assert abs(result - expected) < 1e-6

    @settings(max_examples=150, deadline=None)
    @given(record=activity_record())
    def test_no_pricing_returns_none(self, record):
        # (a) no pricing row matches -> None
        result = CostService.calculate_cost(record, {})
        assert result is None

    @settings(max_examples=150, deadline=None)
    @given(pricing=pricing_config())
    def test_zero_tokens_with_pricing_returns_none(self, pricing):
        # (b) pricing present but all tokens zero -> None
        record = {
            "modelId": "test-model",
            "inputTokens": 0,
            "outputTokens": 0,
            "cacheReadTokens": 0,
            "cacheWriteTokens": 0,
        }
        result = CostService.calculate_cost(record, {"test-model": pricing})
        assert result is None
