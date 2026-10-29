"""Isolation tests for the persisted demo-record merge in the prompts route.

The full-app TestClient cannot start in this sandbox due to an outdated
botocore that lacks the 'bedrock-agentcore' service model. These tests exercise
``_fetch_and_filter`` directly with the CloudTrail/cost/filter services mocked,
so the persisted-demo merge logic is validated without app startup.

Demo data is now persisted (not injected at request time), so the merge always
runs and pulls rows from ``get_demo_records`` — there is no DEMO_MODE toggle.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from app.routes import prompts


def _params():
    return {
        "start_date": datetime(2024, 1, 10, tzinfo=timezone.utc),
        "end_date": datetime(2024, 1, 17, tzinfo=timezone.utc),
        "user_identities": [],
        "team_id": None,
        "source_application": None,
    }


def _demo_record():
    return {
        "id": "demo-abc123-0001",
        "timestamp": "2024-01-15T12:00:00+00:00",
        "userIdentity": "arn:aws:iam::000000000000:user/demo.amelia.chen",
        "userName": "amelia.chen",
        "modelId": "anthropic.claude-3-5-haiku-20241022-v1:0",
        "sourceApplication": "Kiro",
        "sourceIp": "203.0.113.10",
        "inputTokens": 500,
        "outputTokens": 200,
        "cacheReadTokens": 0,
        "cacheWriteTokens": 0,
        "totalTokens": 700,
        "costEstimate": None,
        "promptText": "Summarize the quarterly cost report. My SSN is 123-45-6789.",
        "responseText": "Here is a concise summary.",
        "isDemo": True,
    }


def test_persisted_demo_records_merged_before_filtering():
    with (
        patch.object(prompts, "_cloudtrail_service") as mock_ct,
        patch.object(prompts, "_cost_service") as mock_cost,
        patch.object(prompts, "_filter_service") as mock_filter,
        patch.object(prompts, "get_demo_records") as mock_demo,
        patch.object(prompts, "get_chat_activity_records", return_value=[]),
    ):
        mock_ct.get_activity_records.return_value = []
        mock_cost.apply_costs.side_effect = lambda records, db: records
        mock_filter.apply_filters.side_effect = lambda records, filters: records
        mock_demo.return_value = [_demo_record()]

        result = prompts._fetch_and_filter(_params(), MagicMock())

        mock_demo.assert_called_once()
        ids = [r["id"] for r in result]
        assert "demo-abc123-0001" in ids

        # Demo record must be included in the list handed to cost application.
        costed = mock_cost.apply_costs.call_args[0][0]
        assert any(r["id"] == "demo-abc123-0001" for r in costed)


def test_no_demo_records_yields_only_real():
    with (
        patch.object(prompts, "_cloudtrail_service") as mock_ct,
        patch.object(prompts, "_cost_service") as mock_cost,
        patch.object(prompts, "_filter_service") as mock_filter,
        patch.object(prompts, "get_demo_records", return_value=[]),
        patch.object(prompts, "get_chat_activity_records", return_value=[]),
    ):
        mock_ct.get_activity_records.return_value = []
        mock_cost.apply_costs.side_effect = lambda records, db: records
        mock_filter.apply_filters.side_effect = lambda records, filters: records

        result = prompts._fetch_and_filter(_params(), MagicMock())

        assert result == []


def test_demo_records_use_request_date_window():
    captured = {}

    def _capture(db, start, end):
        captured["start"] = start
        captured["end"] = end
        return []

    with (
        patch.object(prompts, "_cloudtrail_service") as mock_ct,
        patch.object(prompts, "_cost_service") as mock_cost,
        patch.object(prompts, "_filter_service") as mock_filter,
        patch.object(prompts, "get_demo_records", side_effect=_capture),
        patch.object(prompts, "get_chat_activity_records", return_value=[]),
    ):
        mock_ct.get_activity_records.return_value = []
        mock_cost.apply_costs.side_effect = lambda records, db: records
        mock_filter.apply_filters.side_effect = lambda records, filters: records

        params = _params()
        prompts._fetch_and_filter(params, MagicMock())

        assert captured["start"] == params["start_date"]
        assert captured["end"] == params["end_date"]


# ---------------------------------------------------------------------------
# Agent Chat user-identity filtering
#
# These tests exercise ``_fetch_and_filter`` with the REAL FilterService (only
# CloudTrail/cost/demo/chat sources are mocked) so the actual user-identity
# predicate runs. They pin down the fixed behavior: Agent Chat records
# ("agent-chat") respect the same user filter as CloudTrail records — excluded
# when filtering for a specific human user, included when no user filter is set.
# ---------------------------------------------------------------------------


def _human_record():
    return {
        "id": "ct-alice",
        "timestamp": "2024-01-15T08:00:00+00:00",
        "userIdentity": "arn:aws:iam::123456789012:user/alice",
        "userName": "alice",
        "modelId": "anthropic.claude-sonnet-4-20250514-v1:0",
        "sourceApplication": "Kiro",
        "sourceIp": "10.0.0.1",
        "inputTokens": 100,
        "outputTokens": 50,
        "cacheReadTokens": 0,
        "cacheWriteTokens": 0,
        "totalTokens": 150,
        "costEstimate": 0.05,
        "promptText": "hi",
        "responseText": "hello",
    }


def _chat_record():
    return {
        "id": "chat-1",
        "timestamp": "2024-01-15T09:00:00+00:00",
        "userIdentity": "agent-chat",
        "userName": "Agent Chat User",
        "modelId": "anthropic.claude-3-5-haiku-20241022-v1:0",
        "sourceApplication": "Agent Chat",
        "sourceIp": "N/A",
        "inputTokens": 0,
        "outputTokens": 0,
        "cacheReadTokens": 0,
        "cacheWriteTokens": 0,
        "totalTokens": 0,
        "costEstimate": None,
        "promptText": "chat prompt",
        "responseText": "chat response",
    }


def test_chat_records_included_when_no_user_filter():
    """With no user filter set, Agent Chat records are still included."""
    with (
        patch.object(prompts, "_cloudtrail_service") as mock_ct,
        patch.object(prompts, "_cost_service") as mock_cost,
        patch.object(prompts, "get_demo_records", return_value=[]),
        patch.object(prompts, "get_chat_activity_records", return_value=[_chat_record()]),
    ):
        mock_ct.get_activity_records.return_value = [_human_record()]
        mock_cost.apply_costs.side_effect = lambda records, db: records

        result = prompts._fetch_and_filter(_params(), MagicMock())

        ids = [r["id"] for r in result]
        assert "chat-1" in ids
        assert "ct-alice" in ids


def test_chat_records_excluded_when_filtering_for_specific_user():
    """Filtering for a specific human user excludes Agent Chat records."""
    params = _params()
    params["user_identities"] = ["alice"]
    with (
        patch.object(prompts, "_cloudtrail_service") as mock_ct,
        patch.object(prompts, "_cost_service") as mock_cost,
        patch.object(prompts, "get_demo_records", return_value=[]),
        patch.object(prompts, "get_chat_activity_records", return_value=[_chat_record()]),
    ):
        mock_ct.get_activity_records.return_value = [_human_record()]
        mock_cost.apply_costs.side_effect = lambda records, db: records

        result = prompts._fetch_and_filter(params, MagicMock())

        ids = [r["id"] for r in result]
        assert "chat-1" not in ids  # agent-chat no longer leaks in
        assert "ct-alice" in ids
