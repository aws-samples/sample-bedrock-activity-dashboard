"""Unit tests for prompt history API routes."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.services.cloudtrail_service import CloudTrailError


def _make_record(
    record_id: str = "rec-1",
    user_identity: str = "arn:aws:iam::123456789012:user/alice",
    model_id: str = "anthropic.claude-sonnet-4-20250514-v1:0",
    source_application: str = "Kiro",
    timestamp: str = "2024-01-15T10:30:00+00:00",
    input_tokens: int = 100,
    output_tokens: int = 50,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    cost_estimate: float | None = 0.05,
    prompt_text: str = "",
    response_text: str = "",
) -> dict:
    total = input_tokens + output_tokens + cache_read_tokens + cache_write_tokens
    return {
        "id": record_id,
        "timestamp": timestamp,
        "userIdentity": user_identity,
        "userName": user_identity.split("/")[-1] if "/" in user_identity else user_identity,
        "modelId": model_id,
        "sourceApplication": source_application,
        "sourceIp": "10.0.0.1",
        "inputTokens": input_tokens,
        "outputTokens": output_tokens,
        "cacheReadTokens": cache_read_tokens,
        "cacheWriteTokens": cache_write_tokens,
        "totalTokens": total,
        "costEstimate": cost_estimate,
        "promptText": prompt_text,
        "responseText": response_text,
    }


SAMPLE_RECORDS = [
    _make_record(
        record_id="rec-1",
        timestamp="2024-01-15T08:00:00+00:00",
        input_tokens=100,
        output_tokens=50,
        prompt_text="Hello",
        response_text="Hi there",
    ),
    _make_record(
        record_id="rec-2",
        timestamp="2024-01-16T10:00:00+00:00",
        input_tokens=200,
        output_tokens=100,
        prompt_text="How are you?",
        response_text="I'm fine",
    ),
    _make_record(
        record_id="rec-3",
        timestamp="2024-01-14T06:00:00+00:00",
        input_tokens=50,
        output_tokens=25,
    ),
]


@pytest.fixture
def mock_services():
    """Patch all service dependencies used by the prompts routes."""
    with (
        patch("app.routes.prompts._cloudtrail_service") as mock_ct,
        patch("app.routes.prompts._cost_service") as mock_cost,
        patch("app.routes.prompts._filter_service") as mock_filter,
    ):
        mock_ct.get_activity_records.return_value = list(SAMPLE_RECORDS)
        mock_cost.apply_costs.side_effect = lambda records, db: records
        mock_filter.get_default_date_range.return_value = (
            datetime(2024, 1, 10, tzinfo=timezone.utc),
            datetime(2024, 1, 17, tzinfo=timezone.utc),
        )
        mock_filter.validate_date_range.return_value = None
        mock_filter.apply_filters.side_effect = lambda records, filters: records
        mock_filter.filter_by_team.side_effect = lambda records, team_id, db: records

        yield {
            "cloudtrail": mock_ct,
            "cost": mock_cost,
            "filter": mock_filter,
        }


@pytest.fixture
def client(mock_services):
    """Create a test client with mocked DB dependency."""
    from main import app
    from app.database import get_db

    mock_db = MagicMock()
    app.dependency_overrides[get_db] = lambda: mock_db

    with TestClient(app) as c:
        yield c

    app.dependency_overrides.clear()


class TestGetPrompts:
    def test_returns_paginated_response(self, client):
        resp = client.get("/api/activity/prompts")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "total" in data
        assert "page" in data
        assert "pageSize" in data
        assert "totalPages" in data

    def test_default_page_size_is_25(self, client):
        resp = client.get("/api/activity/prompts")
        data = resp.json()
        assert data["pageSize"] == 25

    def test_default_page_is_1(self, client):
        resp = client.get("/api/activity/prompts")
        data = resp.json()
        assert data["page"] == 1

    def test_returns_correct_total(self, client):
        resp = client.get("/api/activity/prompts")
        data = resp.json()
        assert data["total"] == 3

    def test_total_pages_calculation(self, client):
        resp = client.get("/api/activity/prompts")
        data = resp.json()
        # 3 items with page_size=25 → 1 page
        assert data["totalPages"] == 1

    def test_reverse_chronological_order(self, client):
        resp = client.get("/api/activity/prompts")
        data = resp.json()
        items = data["items"]
        timestamps = [item["timestamp"] for item in items]
        assert timestamps == sorted(timestamps, reverse=True)

    def test_items_have_expected_fields(self, client):
        resp = client.get("/api/activity/prompts")
        data = resp.json()
        for item in data["items"]:
            assert "id" in item
            assert "timestamp" in item
            assert "userName" in item
            assert "modelId" in item
            assert "sourceApplication" in item
            assert "inputTokens" in item
            assert "outputTokens" in item
            assert "cacheReadTokens" in item
            assert "cacheWriteTokens" in item
            assert "costEstimate" in item

    def test_custom_page_size(self, client):
        resp = client.get("/api/activity/prompts", params={"page_size": 2})
        data = resp.json()
        assert data["pageSize"] == 2
        assert len(data["items"]) == 2
        assert data["totalPages"] == 2

    def test_page_2(self, client):
        resp = client.get("/api/activity/prompts", params={"page_size": 2, "page": 2})
        data = resp.json()
        assert data["page"] == 2
        assert len(data["items"]) == 1  # 3 total, page_size=2, page 2 has 1

    def test_page_beyond_total_returns_empty_items(self, client):
        resp = client.get("/api/activity/prompts", params={"page": 99})
        data = resp.json()
        assert data["items"] == []
        assert data["total"] == 3

    def test_filter_params_passed(self, client, mock_services):
        resp = client.get(
            "/api/activity/prompts",
            params={
                "start_date": "2024-01-15",
                "end_date": "2024-01-16",
                "user_identities": "user-a",
                "source_application": "Kiro",
            },
        )
        assert resp.status_code == 200
        mock_services["filter"].validate_date_range.assert_called_once()

    def test_team_filter(self, client, mock_services):
        resp = client.get("/api/activity/prompts", params={"team_id": "team-abc"})
        assert resp.status_code == 200
        mock_services["filter"].filter_by_team.assert_called_once()

    def test_cloudtrail_error_returns_502(self, client, mock_services):
        mock_services["cloudtrail"].get_activity_records.side_effect = CloudTrailError(
            error_code="AccessDeniedException",
            message="Not authorized",
        )
        resp = client.get("/api/activity/prompts")
        assert resp.status_code == 502
        data = resp.json()
        assert data["error"]["code"] == "AccessDeniedException"

    def test_empty_records_returns_200(self, client, mock_services):
        mock_services["cloudtrail"].get_activity_records.return_value = []
        resp = client.get("/api/activity/prompts")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["items"] == []
        assert data["totalPages"] == 0


class TestPersistedDemoMerge:
    """Verify PERSISTED demo records are merged into prompt history.

    Demo data is now persisted (generated on demand by an admin) rather than
    injected at request time via DEMO_MODE. The prompts route always merges rows
    returned by ``get_demo_records``; when none exist the merge is a no-op.
    """

    def _demo(self):
        return _make_record(
            record_id="demo-abc123-0001",
            timestamp="2024-01-15T12:00:00+00:00",
            user_identity="arn:aws:iam::000000000000:user/demo.amelia.chen",
            prompt_text="Summarize the quarterly cost report. My SSN is 123-45-6789.",
            response_text="Here is a concise summary.",
        )

    def test_persisted_demo_records_appear_in_list(self, client, mock_services):
        with patch("app.routes.prompts.get_demo_records") as mock_demo:
            mock_demo.return_value = [self._demo()]

            resp = client.get("/api/activity/prompts")
            assert resp.status_code == 200
            data = resp.json()
            mock_demo.assert_called_once()
            ids = [item["id"] for item in data["items"]]
            assert "demo-abc123-0001" in ids
            # 3 CloudTrail sample records + 1 persisted demo record
            assert data["total"] == 4

    def test_persisted_demo_record_available_in_detail(self, client, mock_services):
        with patch("app.routes.prompts.get_demo_records") as mock_demo:
            mock_demo.return_value = [self._demo()]

            resp = client.get("/api/activity/prompts/demo-abc123-0001")
            assert resp.status_code == 200
            data = resp.json()
            assert data["id"] == "demo-abc123-0001"
            assert "123-45-6789" in data["promptText"]
            assert data["responseText"] == "Here is a concise summary."

    def test_no_demo_records_yields_only_real(self, client, mock_services):
        with patch("app.routes.prompts.get_demo_records") as mock_demo:
            mock_demo.return_value = []

            resp = client.get("/api/activity/prompts")
            assert resp.status_code == 200
            data = resp.json()
            mock_demo.assert_called_once()
            assert data["total"] == 3


class TestChatPlaceholderDedup:
    """Agent Chat placeholders that already have a matching real invocation
    (same underlying Bedrock call, independently ingested via CloudWatch/S3/
    CloudTrail) must not double-count in prompt history.

    Regression test: routes/prompts.py has its own separate ingestion
    pipeline from routes/activity.py and previously never called
    suppress_chat_placeholders_with_real_invocation, so every Agent Chat
    turn showed up twice — once as the zero-token/N/A placeholder, once as
    the real record with actual tokens/cost.
    """

    def _chat_record(self):
        return _make_record(
            record_id="chat-turn-1",
            timestamp="2024-01-15T08:00:05+00:00",  # a few seconds after the real record
            user_identity="agent-chat",
            source_application="Agent Chat",
            model_id="anthropic.claude-haiku-4-5-20251001-v1:0",
            input_tokens=0,
            output_tokens=0,
            cost_estimate=None,
            prompt_text="What's my usage?",
            response_text="Here's your usage summary.",
        )

    def test_chat_placeholder_suppressed_when_real_invocation_matches(self, client, mock_services):
        real_record = _make_record(
            record_id="real-1",
            timestamp="2024-01-15T08:00:00+00:00",
            model_id="anthropic.claude-haiku-4-5-20251001-v1:0",
            source_application="CloudWatch Logs",
            input_tokens=1046,
            output_tokens=230,
            cost_estimate=0.0023,
        )
        mock_services["cloudtrail"].get_activity_records.return_value = [real_record]

        with (
            patch("app.routes.prompts.get_demo_records", return_value=[]),
            patch("app.routes.prompts.get_chat_activity_records") as mock_chat,
            patch(
                "app.config.settings.bedrock_model_id",
                "us.anthropic.claude-haiku-4-5-20251001-v1:0",
            ),
        ):
            mock_chat.return_value = [self._chat_record()]
            resp = client.get("/api/activity/prompts")
            assert resp.status_code == 200
            data = resp.json()

            # Only the real record should appear — the chat placeholder was
            # suppressed as a duplicate of the same underlying invocation.
            assert data["total"] == 1
            ids = [item["id"] for item in data["items"]]
            assert "real-1" in ids
            assert "chat-turn-1" not in ids

    def test_chat_placeholder_kept_when_no_matching_real_record(self, client, mock_services):
        mock_services["cloudtrail"].get_activity_records.return_value = []

        with (
            patch("app.routes.prompts.get_demo_records", return_value=[]),
            patch("app.routes.prompts.get_chat_activity_records") as mock_chat,
        ):
            mock_chat.return_value = [self._chat_record()]
            resp = client.get("/api/activity/prompts")
            assert resp.status_code == 200
            data = resp.json()

            # No real record to match against — the placeholder must be kept,
            # never silently dropped (best-effort matching, see the service
            # docstring).
            ids = [item["id"] for item in data["items"]]
            assert "chat-turn-1" in ids


class TestGetPromptDetail:
    def test_returns_detail_for_existing_record(self, client):
        resp = client.get("/api/activity/prompts/rec-1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "rec-1"
        assert "promptText" in data
        assert "responseText" in data
        assert "userIdentity" in data
        assert "sourceIp" in data
        assert "totalTokens" in data

    def test_returns_prompt_and_response_text(self, client):
        resp = client.get("/api/activity/prompts/rec-1")
        data = resp.json()
        assert data["promptText"] == "Hello"
        assert data["responseText"] == "Hi there"

    def test_returns_404_for_missing_record(self, client):
        resp = client.get("/api/activity/prompts/nonexistent-id")
        assert resp.status_code == 404
        data = resp.json()
        assert data["error"]["code"] == "NOT_FOUND"

    def test_cloudtrail_error_returns_502(self, client, mock_services):
        mock_services["cloudtrail"].get_activity_records.side_effect = CloudTrailError(
            error_code="ThrottlingException",
            message="Rate exceeded",
        )
        resp = client.get("/api/activity/prompts/rec-1")
        assert resp.status_code == 502

    def test_detail_includes_list_entry_fields(self, client):
        resp = client.get("/api/activity/prompts/rec-2")
        assert resp.status_code == 200
        data = resp.json()
        assert "timestamp" in data
        assert "userName" in data
        assert "modelId" in data
        assert "sourceApplication" in data
        assert "inputTokens" in data
        assert "outputTokens" in data
        assert "costEstimate" in data
