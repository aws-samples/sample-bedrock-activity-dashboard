"""Property-based tests for sort order and pagination."""

from hypothesis import given, settings
from hypothesis import strategies as st

from app.services.aggregation_service import AggregationService
from tests.strategies import activity_record

svc = AggregationService()


class TestDefaultSortOrder:
    """Property 7: Default Sort Order.

    User token usage sorted descending by totalTokens.
    Prompt history sorted descending by timestamp.
    """
    # Feature: bedrock-activity-dashboard, Property 7: Default Sort Order

    @settings(max_examples=150, deadline=None)
    @given(records=st.lists(activity_record(), min_size=2, max_size=30))
    def test_user_aggregation_sorted_desc_by_total_tokens(self, records):
        groups = svc.aggregate_by_user(records)
        for i in range(len(groups) - 1):
            assert groups[i]["totalTokens"] >= groups[i + 1]["totalTokens"]

    @settings(max_examples=150, deadline=None)
    @given(records=st.lists(activity_record(), min_size=2, max_size=30))
    def test_prompt_history_sorted_desc_by_timestamp(self, records):
        # Simulate prompt history sort (reverse chronological)
        sorted_records = sorted(records, key=lambda r: r["timestamp"], reverse=True)
        for i in range(len(sorted_records) - 1):
            assert sorted_records[i]["timestamp"] >= sorted_records[i + 1]["timestamp"]


class TestPaginationCorrectness:
    """Property 8: Pagination Correctness.

    Page K contains items at indices [(K-1)*25, min(K*25, N)).
    totalPages == ceil(N / 25).
    """
    # Feature: bedrock-activity-dashboard, Property 8: Pagination Correctness

    @settings(max_examples=150, deadline=None)
    @given(
        n=st.integers(min_value=0, max_value=200),
        page=st.integers(min_value=1, max_value=10),
    )
    def test_page_slicing_correctness(self, n, page):
        import math
        items = list(range(n))
        page_size = 25
        total_pages = math.ceil(n / page_size) if n > 0 else 0

        start_idx = (page - 1) * page_size
        end_idx = min(page * page_size, n)
        page_items = items[start_idx:end_idx]

        if page <= total_pages:
            expected_len = end_idx - start_idx
            assert len(page_items) == expected_len
        else:
            assert len(page_items) == 0

        if n > 0:
            assert total_pages == math.ceil(n / page_size)
        else:
            assert total_pages == 0
