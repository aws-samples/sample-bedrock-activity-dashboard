"""Property-based tests for AggregationService."""

from hypothesis import given, settings
from hypothesis import strategies as st

from app.services.aggregation_service import AggregationService
from tests.strategies import activity_record

svc = AggregationService()


class TestAggregationInvariant:
    """Property 4: Aggregation Invariant.

    For any set of records, group totals must sum to overall total.
    """
    # Feature: bedrock-activity-dashboard, Property 4: Aggregation Invariant

    @settings(max_examples=150, deadline=None)
    @given(records=st.lists(activity_record(), min_size=1, max_size=50))
    def test_user_group_totals_sum_to_overall(self, records):
        groups = svc.aggregate_by_user(records)
        overview = svc.compute_overview(records)
        assert sum(g["totalTokens"] for g in groups) == overview["totalTokens"]
        assert sum(g["invocationCount"] for g in groups) == overview["totalInvocations"]

    @settings(max_examples=150, deadline=None)
    @given(records=st.lists(activity_record(), min_size=1, max_size=50))
    def test_model_group_totals_sum_to_overall(self, records):
        groups = svc.aggregate_by_model(records)
        overview = svc.compute_overview(records)
        assert sum(g["invocationCount"] for g in groups) == overview["totalInvocations"]
        assert sum(g["totalTokens"] for g in groups) == overview["totalTokens"]

    @settings(max_examples=150, deadline=None)
    @given(records=st.lists(activity_record(), min_size=1, max_size=50))
    def test_source_group_totals_sum_to_overall(self, records):
        groups = svc.aggregate_by_source(records)
        overview = svc.compute_overview(records)
        assert sum(g["invocationCount"] for g in groups) == overview["totalInvocations"]

    @settings(max_examples=150, deadline=None)
    @given(records=st.lists(activity_record(), min_size=1, max_size=50))
    def test_date_group_totals_sum_to_overall(self, records):
        groups = svc.aggregate_by_date(records)
        overview = svc.compute_overview(records)
        assert sum(g["totalTokens"] for g in groups) == overview["totalTokens"]

    @settings(max_examples=150, deadline=None)
    @given(records=st.lists(activity_record(), min_size=1, max_size=50))
    def test_user_model_matrix_invocations_sum(self, records):
        matrix = svc.compute_user_model_matrix(records)
        assert sum(c["invocationCount"] for c in matrix) == len(records)


class TestPercentageCalculation:
    """Property 6: Percentage Calculation.

    Percentages must sum to 100% (within floating-point tolerance).
    """
    # Feature: bedrock-activity-dashboard, Property 6: Percentage Calculation

    @settings(max_examples=150, deadline=None)
    @given(values=st.lists(st.floats(min_value=0.01, max_value=1e6, allow_nan=False, allow_infinity=False), min_size=1, max_size=20))
    def test_percentages_sum_to_100(self, values):
        pcts = AggregationService.compute_percentages(values)
        assert abs(sum(pcts) - 100.0) < 0.01

    @settings(max_examples=50, deadline=None)
    @given(n=st.integers(min_value=1, max_value=20))
    def test_all_zeros_returns_all_zeros(self, n):
        pcts = AggregationService.compute_percentages([0.0] * n)
        assert all(p == 0.0 for p in pcts)
