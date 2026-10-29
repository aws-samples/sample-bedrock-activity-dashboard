"""Unit + property-based tests for team usage aggregation.

Covers the flexible member↔record matching (``FilterService.member_matches_record``)
and ``AggregationService.aggregate_team_totals`` that back the real Team usage
totals on ``GET /api/teams``. Pure functions only — no app boot required.
"""

import random

from hypothesis import given, settings
from hypothesis import strategies as st

from app.services.aggregation_service import AggregationService
from app.services.filter_service import FilterService
from tests.strategies import activity_record

svc = AggregationService()

ARN_AMELIA = "arn:aws:iam::123456789012:user/demo.amelia.chen"


def _make_record(
    user_identity: str = ARN_AMELIA,
    user_name: str = "demo.amelia.chen",
    input_tokens: int = 100,
    output_tokens: int = 50,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    cost_estimate: float | None = None,
) -> dict:
    total = input_tokens + output_tokens + cache_read_tokens + cache_write_tokens
    return {
        "id": "rec",
        "timestamp": "2024-01-15T10:30:00+00:00",
        "userIdentity": user_identity,
        "userName": user_name,
        "modelId": "anthropic.claude-3-haiku-20240307-v1:0",
        "sourceApplication": "Kiro",
        "sourceIp": "10.0.0.1",
        "inputTokens": input_tokens,
        "outputTokens": output_tokens,
        "cacheReadTokens": cache_read_tokens,
        "cacheWriteTokens": cache_write_tokens,
        "totalTokens": total,
        "costEstimate": cost_estimate,
    }


# ---------------------------------------------------------------------------
# member_matches_record
# ---------------------------------------------------------------------------


class TestMemberMatchesRecord:
    def test_username_matches_full_arn_identity(self):
        # A membership stored as a bare username matches the ARN-based record.
        rec = _make_record(user_identity=ARN_AMELIA, user_name="")
        assert FilterService.member_matches_record("demo.amelia.chen", rec) is True

    def test_email_substring_match_on_username(self):
        rec = _make_record(user_identity="u-001", user_name="amelia@example.com")
        # Exact email equals userName.
        assert FilterService.member_matches_record("amelia@example.com", rec) is True
        # Substring of the email also matches.
        assert FilterService.member_matches_record("amelia", rec) is True

    def test_case_insensitive(self):
        rec = _make_record(user_identity="arn:aws:iam::1:user/demo.Amelia.Chen", user_name="")
        assert FilterService.member_matches_record("AMELIA", rec) is True
        assert FilterService.member_matches_record("demo.amelia.chen", rec) is True

    def test_blank_member_never_matches(self):
        rec = _make_record()
        assert FilterService.member_matches_record("", rec) is False
        assert FilterService.member_matches_record("   ", rec) is False

    def test_non_matching_member(self):
        rec = _make_record(user_identity=ARN_AMELIA, user_name="demo.amelia.chen")
        assert FilterService.member_matches_record("bob", rec) is False

    def test_exact_full_arn_matches(self):
        rec = _make_record(user_identity=ARN_AMELIA, user_name="")
        assert FilterService.member_matches_record(ARN_AMELIA, rec) is True

    def test_blank_record_fields_never_match(self):
        rec = _make_record(user_identity="", user_name="")
        assert FilterService.member_matches_record("amelia", rec) is False


# ---------------------------------------------------------------------------
# aggregate_team_totals
# ---------------------------------------------------------------------------


class TestAggregateTeamTotals:
    def test_username_member_matches_arn_records_and_sums_tokens(self):
        records = [
            _make_record(
                user_identity=ARN_AMELIA, user_name="", input_tokens=100, output_tokens=50
            ),
            _make_record(user_identity=ARN_AMELIA, user_name="", input_tokens=10, output_tokens=5),
            _make_record(
                user_identity="arn:aws:iam::123456789012:user/demo.bob.jones",
                user_name="",
                input_tokens=999,
                output_tokens=999,
            ),
        ]
        result = svc.aggregate_team_totals(records, ["demo.amelia.chen"])
        # Only the two amelia records count: (150) + (15) = 165 tokens.
        assert result["totalTokens"] == 165

    def test_record_matched_by_two_members_counts_once(self):
        # Both members flex-match the same single record; it must count once.
        rec = _make_record(
            user_identity=ARN_AMELIA,
            user_name="amelia@example.com",
            input_tokens=100,
            output_tokens=0,
        )
        result = svc.aggregate_team_totals([rec], ["demo.amelia.chen", "amelia@example.com"])
        assert result["totalTokens"] == 100

    def test_cost_none_when_no_matched_record_has_cost(self):
        records = [
            _make_record(user_identity=ARN_AMELIA, user_name="", cost_estimate=None),
            _make_record(user_identity=ARN_AMELIA, user_name="", cost_estimate=None),
        ]
        result = svc.aggregate_team_totals(records, ["demo.amelia.chen"])
        assert result["totalCostEstimate"] is None

    def test_cost_summed_when_matched_records_have_cost(self):
        records = [
            _make_record(user_identity=ARN_AMELIA, user_name="", cost_estimate=0.25),
            _make_record(user_identity=ARN_AMELIA, user_name="", cost_estimate=0.75),
            # A non-matching record's cost must be excluded.
            _make_record(
                user_identity="arn:aws:iam::1:user/other", user_name="", cost_estimate=5.0
            ),
        ]
        result = svc.aggregate_team_totals(records, ["demo.amelia.chen"])
        assert result["totalCostEstimate"] == 1.0

    def test_no_members_yields_zero_and_none(self):
        records = [_make_record(cost_estimate=1.0)]
        result = svc.aggregate_team_totals(records, [])
        assert result == {"totalTokens": 0, "totalCostEstimate": None}

    def test_blank_members_ignored(self):
        records = [_make_record(cost_estimate=1.0)]
        result = svc.aggregate_team_totals(records, ["", "   "])
        assert result == {"totalTokens": 0, "totalCostEstimate": None}

    def test_empty_records_yields_zero_and_none(self):
        result = svc.aggregate_team_totals([], ["demo.amelia.chen"])
        assert result == {"totalTokens": 0, "totalCostEstimate": None}


# ---------------------------------------------------------------------------
# Property-based tests
# ---------------------------------------------------------------------------

# Members drawn from the userIdentity space used by the shared activity_record
# strategy so matches actually occur.
_MEMBER_POOL = ["user-a", "user-b", "user-c", "user-d", "alice", "bob"]


class TestAggregateTeamTotalsProperties:
    """Team totals equal the sum over the matched subset and are order-independent."""

    @settings(max_examples=150, deadline=None)
    @given(
        records=st.lists(activity_record(), min_size=0, max_size=40),
        members=st.lists(st.sampled_from(_MEMBER_POOL), min_size=0, max_size=4),
    )
    def test_totals_equal_sum_over_matched_subset(self, records, members):
        normalized = [m for m in members if m and m.strip()]
        matched = [
            r for r in records if any(FilterService.member_matches_record(m, r) for m in normalized)
        ]
        expected_tokens = sum(r["totalTokens"] for r in matched)
        costs = [r["costEstimate"] for r in matched if r.get("costEstimate") is not None]
        expected_cost = sum(costs) if costs else None

        result = svc.aggregate_team_totals(records, members)
        assert result["totalTokens"] == expected_tokens
        assert result["totalCostEstimate"] == expected_cost

    @settings(max_examples=100, deadline=None)
    @given(
        records=st.lists(activity_record(), min_size=1, max_size=40),
        members=st.lists(st.sampled_from(_MEMBER_POOL), min_size=1, max_size=4),
    )
    def test_order_independent(self, records, members):
        base = svc.aggregate_team_totals(records, members)
        shuffled = list(records)
        random.Random(1234).shuffle(shuffled)
        assert svc.aggregate_team_totals(shuffled, members) == base
