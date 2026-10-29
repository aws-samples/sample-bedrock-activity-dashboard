"""Property-based tests for FilterService."""

from datetime import datetime, timedelta, timezone

from hypothesis import given, settings
from hypothesis import strategies as st
from fastapi import HTTPException
import pytest

from app.services.filter_service import ActivityFilters, FilterService
from tests.strategies import activity_record, date_range


class TestDateRangeFiltering:
    """Property 2: Date Range Filtering.

    All returned records must have timestamps within [start, end].
    """
    # Feature: bedrock-activity-dashboard, Property 2: Date Range Filtering

    @settings(max_examples=150, deadline=None)
    @given(records=st.lists(activity_record(), min_size=0, max_size=30), dr=date_range())
    def test_all_returned_records_within_range(self, records, dr):
        start, end = dr
        filters = ActivityFilters(start_date=start, end_date=end)
        result = FilterService.apply_filters(records, filters)
        start_iso = start.isoformat()
        end_iso = end.isoformat()
        for r in result:
            assert start_iso <= r["timestamp"] <= end_iso


class TestMultiFilterCorrectness:
    """Property 5: Multi-Filter Correctness.

    All returned records must satisfy every applied filter.
    """
    # Feature: bedrock-activity-dashboard, Property 5: Multi-Filter Correctness

    @settings(max_examples=150, deadline=None)
    @given(
        records=st.lists(activity_record(), min_size=0, max_size=30),
        dr=date_range(),
        user_filter=st.sampled_from([[], ["user-a"], ["user-a", "user-b"]]),
        source_filter=st.sampled_from([None, "Kiro", "Q Developer", "Unknown"]),
    )
    def test_all_returned_records_match_all_predicates(self, records, dr, user_filter, source_filter):
        start, end = dr
        filters = ActivityFilters(
            start_date=start,
            end_date=end,
            user_identities=user_filter,
            source_application=source_filter,
        )
        result = FilterService.apply_filters(records, filters)
        start_iso = start.isoformat()
        end_iso = end.isoformat()
        for r in result:
            assert start_iso <= r["timestamp"] <= end_iso
            if user_filter:
                assert r["userIdentity"] in user_filter
            if source_filter:
                assert r["sourceApplication"] == source_filter


class TestDateRangeValidation:
    """Property 9: Date Range Validation.

    start > end must raise HTTPException 422.
    """
    # Feature: bedrock-activity-dashboard, Property 9: Date Range Validation

    @settings(max_examples=150, deadline=None)
    @given(
        start=st.datetimes(min_value=datetime(2024, 1, 2), max_value=datetime(2026, 12, 31), timezones=st.just(timezone.utc)),
        delta=st.timedeltas(min_value=timedelta(seconds=1), max_value=timedelta(days=365)),
    )
    def test_invalid_range_raises_422(self, start, delta):
        end = start - delta  # end is before start
        with pytest.raises(HTTPException) as exc_info:
            FilterService.validate_date_range(start, end)
        assert exc_info.value.status_code == 422
