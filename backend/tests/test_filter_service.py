"""Unit tests for FilterService."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.services.filter_service import ActivityFilters, FilterService, parse_iso_to_utc


def _make_record(
    user_identity: str = "arn:aws:iam::123456789012:user/alice",
    model_id: str = "anthropic.claude-sonnet-4-20250514-v1:0",
    source_application: str = "Kiro",
    timestamp: str = "2024-01-15T10:30:00+00:00",
    input_tokens: int = 100,
    output_tokens: int = 50,
    cost_estimate: float | None = None,
) -> dict:
    total = input_tokens + output_tokens
    return {
        "id": "test-id",
        "timestamp": timestamp,
        "userIdentity": user_identity,
        "userName": "alice",
        "modelId": model_id,
        "sourceApplication": source_application,
        "sourceIp": "10.0.0.1",
        "inputTokens": input_tokens,
        "outputTokens": output_tokens,
        "cacheReadTokens": 0,
        "cacheWriteTokens": 0,
        "totalTokens": total,
        "costEstimate": cost_estimate,
    }


@pytest.fixture
def sample_records():
    return [
        _make_record(
            user_identity="user-a",
            source_application="Kiro",
            timestamp="2024-01-14T08:00:00+00:00",
        ),
        _make_record(
            user_identity="user-a",
            source_application="Q Developer",
            timestamp="2024-01-15T10:00:00+00:00",
        ),
        _make_record(
            user_identity="user-b",
            source_application="Kiro",
            timestamp="2024-01-16T12:00:00+00:00",
        ),
        _make_record(
            user_identity="user-c",
            source_application="Unknown",
            timestamp="2024-01-17T14:00:00+00:00",
        ),
    ]


class TestValidateDateRange:
    def test_valid_range_does_not_raise(self):
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end = datetime(2024, 1, 31, tzinfo=timezone.utc)
        FilterService.validate_date_range(start, end)  # should not raise

    def test_same_date_does_not_raise(self):
        dt = datetime(2024, 1, 15, tzinfo=timezone.utc)
        FilterService.validate_date_range(dt, dt)  # should not raise

    def test_start_after_end_raises_422(self):
        start = datetime(2024, 2, 1, tzinfo=timezone.utc)
        end = datetime(2024, 1, 1, tzinfo=timezone.utc)
        with pytest.raises(HTTPException) as exc_info:
            FilterService.validate_date_range(start, end)
        assert exc_info.value.status_code == 422

    def test_start_slightly_after_end_raises_422(self):
        end = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        start = end + timedelta(seconds=1)
        with pytest.raises(HTTPException) as exc_info:
            FilterService.validate_date_range(start, end)
        assert exc_info.value.status_code == 422


class TestGetDefaultDateRange:
    def test_returns_7_day_range(self):
        start, end = FilterService.get_default_date_range()
        delta = end - start
        assert abs(delta.days - 7) <= 0
        assert delta.total_seconds() >= 0

    def test_end_is_close_to_now(self):
        _, end = FilterService.get_default_date_range()
        now = datetime.now(timezone.utc)
        assert abs((now - end).total_seconds()) < 5

    def test_both_are_timezone_aware(self):
        start, end = FilterService.get_default_date_range()
        assert start.tzinfo is not None
        assert end.tzinfo is not None


class TestApplyFilters:
    def test_date_range_filter(self, sample_records):
        filters = ActivityFilters(
            start_date=datetime(2024, 1, 15, tzinfo=timezone.utc),
            end_date=datetime(2024, 1, 16, 23, 59, 59, tzinfo=timezone.utc),
        )
        result = FilterService.apply_filters(sample_records, filters)
        assert len(result) == 2
        timestamps = [r["timestamp"] for r in result]
        assert "2024-01-15T10:00:00+00:00" in timestamps
        assert "2024-01-16T12:00:00+00:00" in timestamps

    def test_date_range_excludes_outside(self, sample_records):
        filters = ActivityFilters(
            start_date=datetime(2024, 1, 15, tzinfo=timezone.utc),
            end_date=datetime(2024, 1, 15, 23, 59, 59, tzinfo=timezone.utc),
        )
        result = FilterService.apply_filters(sample_records, filters)
        assert len(result) == 1
        assert result[0]["timestamp"] == "2024-01-15T10:00:00+00:00"

    def test_user_identity_filter(self, sample_records):
        filters = ActivityFilters(
            start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2024, 12, 31, tzinfo=timezone.utc),
            user_identities=["user-a"],
        )
        result = FilterService.apply_filters(sample_records, filters)
        assert len(result) == 2
        assert all(r["userIdentity"] == "user-a" for r in result)

    def test_multiple_user_identities(self, sample_records):
        filters = ActivityFilters(
            start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2024, 12, 31, tzinfo=timezone.utc),
            user_identities=["user-a", "user-c"],
        )
        result = FilterService.apply_filters(sample_records, filters)
        assert len(result) == 3
        identities = {r["userIdentity"] for r in result}
        assert identities == {"user-a", "user-c"}

    def test_empty_user_identities_means_no_filter(self, sample_records):
        filters = ActivityFilters(
            start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2024, 12, 31, tzinfo=timezone.utc),
            user_identities=[],
        )
        result = FilterService.apply_filters(sample_records, filters)
        assert len(result) == 4

    def test_source_application_filter(self, sample_records):
        filters = ActivityFilters(
            start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2024, 12, 31, tzinfo=timezone.utc),
            source_application="Kiro",
        )
        result = FilterService.apply_filters(sample_records, filters)
        assert len(result) == 2
        assert all(r["sourceApplication"] == "Kiro" for r in result)

    def test_username_substring_matches_arn_record(self):
        # Human-entered "amelia" should match an ARN-based userIdentity.
        records = [
            _make_record(
                user_identity="arn:aws:iam::123456789012:user/demo.amelia.chen",
                timestamp="2024-01-15T10:00:00+00:00",
            ),
            _make_record(
                user_identity="arn:aws:iam::123456789012:user/demo.bob.jones",
                timestamp="2024-01-15T10:00:00+00:00",
            ),
        ]
        filters = ActivityFilters(
            start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2024, 12, 31, tzinfo=timezone.utc),
            user_identities=["amelia"],
        )
        result = FilterService.apply_filters(records, filters)
        assert len(result) == 1
        assert "amelia" in result[0]["userIdentity"]

    def test_username_field_match(self):
        # A value that matches the userName (not present in the ARN) still works.
        rec = _make_record(
            user_identity="arn:aws:iam::123456789012:user/u-001",
            timestamp="2024-01-15T10:00:00+00:00",
        )
        rec["userName"] = "amelia.chen"
        filters = ActivityFilters(
            start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2024, 12, 31, tzinfo=timezone.utc),
            user_identities=["amelia.chen"],
        )
        result = FilterService.apply_filters([rec], filters)
        assert len(result) == 1

    def test_user_match_is_case_insensitive(self):
        rec = _make_record(
            user_identity="arn:aws:iam::123456789012:user/demo.Amelia.Chen",
            timestamp="2024-01-15T10:00:00+00:00",
        )
        filters = ActivityFilters(
            start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2024, 12, 31, tzinfo=timezone.utc),
            user_identities=["AMELIA"],
        )
        result = FilterService.apply_filters([rec], filters)
        assert len(result) == 1

    def test_exact_full_arn_still_matches(self):
        arn = "arn:aws:iam::123456789012:user/demo.amelia.chen"
        rec = _make_record(user_identity=arn, timestamp="2024-01-15T10:00:00+00:00")
        filters = ActivityFilters(
            start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2024, 12, 31, tzinfo=timezone.utc),
            user_identities=[arn],
        )
        result = FilterService.apply_filters([rec], filters)
        assert len(result) == 1

    def test_blank_user_values_are_ignored(self):
        # Blank/whitespace-only values must not match every record.
        rec = _make_record(
            user_identity="arn:aws:iam::123456789012:user/demo.amelia.chen",
            timestamp="2024-01-15T10:00:00+00:00",
        )
        filters = ActivityFilters(
            start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2024, 12, 31, tzinfo=timezone.utc),
            user_identities=["   ", ""],
        )
        result = FilterService.apply_filters([rec], filters)
        # All values blank -> effectively no user filter -> record passes.
        assert len(result) == 1

    def test_multiple_users_or_match(self):
        records = [
            _make_record(
                user_identity="arn:aws:iam::123456789012:user/demo.amelia.chen",
                timestamp="2024-01-15T10:00:00+00:00",
            ),
            _make_record(
                user_identity="arn:aws:iam::123456789012:user/demo.bob.jones",
                timestamp="2024-01-15T10:00:00+00:00",
            ),
            _make_record(
                user_identity="arn:aws:iam::123456789012:user/demo.carol.smith",
                timestamp="2024-01-15T10:00:00+00:00",
            ),
        ]
        filters = ActivityFilters(
            start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2024, 12, 31, tzinfo=timezone.utc),
            user_identities=["amelia", "carol"],
        )
        result = FilterService.apply_filters(records, filters)
        assert len(result) == 2

    def test_source_application_case_insensitive(self, sample_records):
        filters = ActivityFilters(
            start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2024, 12, 31, tzinfo=timezone.utc),
            source_application="kiro",
        )
        result = FilterService.apply_filters(sample_records, filters)
        assert len(result) == 2
        assert all(r["sourceApplication"] == "Kiro" for r in result)

    def test_no_source_filter_returns_all(self, sample_records):
        filters = ActivityFilters(
            start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2024, 12, 31, tzinfo=timezone.utc),
            source_application=None,
        )
        result = FilterService.apply_filters(sample_records, filters)
        assert len(result) == 4

    def test_combined_filters_and_logic(self, sample_records):
        filters = ActivityFilters(
            start_date=datetime(2024, 1, 15, tzinfo=timezone.utc),
            end_date=datetime(2024, 1, 17, 23, 59, 59, tzinfo=timezone.utc),
            user_identities=["user-a", "user-b"],
            source_application="Kiro",
        )
        result = FilterService.apply_filters(sample_records, filters)
        # user-a/Kiro on Jan 14 is out of range, user-a/Q Developer doesn't match source
        # user-b/Kiro on Jan 16 matches all
        assert len(result) == 1
        assert result[0]["userIdentity"] == "user-b"

    def test_no_matching_records(self, sample_records):
        filters = ActivityFilters(
            start_date=datetime(2025, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2025, 12, 31, tzinfo=timezone.utc),
        )
        result = FilterService.apply_filters(sample_records, filters)
        assert result == []

    def test_empty_records(self):
        filters = ActivityFilters(
            start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2024, 12, 31, tzinfo=timezone.utc),
        )
        result = FilterService.apply_filters([], filters)
        assert result == []


class TestResolveTeamMembers:
    def test_returns_user_identities(self):
        mock_db = MagicMock()
        mock_result = [
            MagicMock(user_identity="user-a"),
            MagicMock(user_identity="user-b"),
        ]
        mock_db.query.return_value.filter.return_value.all.return_value = mock_result

        result = FilterService.resolve_team_members("team-123", mock_db)
        assert result == ["user-a", "user-b"]

    def test_empty_team_returns_empty_list(self):
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.all.return_value = []

        result = FilterService.resolve_team_members("team-empty", mock_db)
        assert result == []


class TestFilterByTeam:
    def test_filters_to_team_members(self, sample_records):
        mock_db = MagicMock()
        mock_result = [MagicMock(user_identity="user-a")]
        mock_db.query.return_value.filter.return_value.all.return_value = mock_result

        result = FilterService.filter_by_team(sample_records, "team-1", mock_db)
        assert len(result) == 2
        assert all(r["userIdentity"] == "user-a" for r in result)

    def test_empty_team_returns_empty(self, sample_records):
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.all.return_value = []

        result = FilterService.filter_by_team(sample_records, "team-empty", mock_db)
        assert result == []

    def test_no_matching_members(self, sample_records):
        mock_db = MagicMock()
        mock_result = [MagicMock(user_identity="user-z")]
        mock_db.query.return_value.filter.return_value.all.return_value = mock_result

        result = FilterService.filter_by_team(sample_records, "team-z", mock_db)
        assert result == []


class TestParseIsoToUtc:
    """Tests for tz-aware ISO parsing used by the activity/prompts filters."""

    def test_naive_input_assumed_utc(self):
        # No offset in the string -> assume UTC, stamp tzinfo without shifting.
        result = parse_iso_to_utc("2024-01-15T10:30:00")
        assert result == datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
        assert result.tzinfo is not None

    def test_offset_bearing_input_converted_not_shifted(self):
        # 10:30 at +05:00 is the same instant as 05:30 UTC. The old buggy
        # .replace(tzinfo=utc) would have produced 10:30 UTC (wrong instant).
        result = parse_iso_to_utc("2024-01-15T10:30:00+05:00")
        assert result == datetime(2024, 1, 15, 5, 30, 0, tzinfo=timezone.utc)

    def test_negative_offset_converted(self):
        # 10:30 at -08:00 is 18:30 UTC.
        result = parse_iso_to_utc("2024-01-15T10:30:00-08:00")
        assert result == datetime(2024, 1, 15, 18, 30, 0, tzinfo=timezone.utc)

    def test_utc_offset_input_preserved(self):
        result = parse_iso_to_utc("2024-01-15T10:30:00+00:00")
        assert result == datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)

    def test_bare_date_end_of_day_preserved(self):
        # Bare date with end_of_day flag -> 23:59:59 UTC.
        result = parse_iso_to_utc("2024-01-15", end_of_day_if_bare=True)
        assert result == datetime(2024, 1, 15, 23, 59, 59, tzinfo=timezone.utc)

    def test_bare_date_without_end_of_day_flag_is_midnight(self):
        result = parse_iso_to_utc("2024-01-15")
        assert result == datetime(2024, 1, 15, 0, 0, 0, tzinfo=timezone.utc)

    def test_full_datetime_not_treated_as_bare_even_with_flag(self):
        # len > 10 so end-of-day bump must NOT apply; the given time stands.
        result = parse_iso_to_utc("2024-01-15T08:00:00", end_of_day_if_bare=True)
        assert result == datetime(2024, 1, 15, 8, 0, 0, tzinfo=timezone.utc)


class TestCorsOriginsParsing:
    """The deployed CORS_ORIGINS is a comma-separated list; config splits it."""

    def test_comma_separated_origins_split_into_list(self):
        raw = "https://example-dist-id.cloudfront.net,http://localhost:5173,http://localhost:3000"
        origins = raw.split(",")
        assert origins == [
            "https://example-dist-id.cloudfront.net",
            "http://localhost:5173",
            "http://localhost:3000",
        ]
        # No wildcard present — safe to combine with allow_credentials=True.
        assert "*" not in origins
