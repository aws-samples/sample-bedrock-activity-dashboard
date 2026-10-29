"""Unit tests for ChatMessageService and prompts route integration.

Validates: Requirements 1.2, 1.3, 1.4, 2.1, 2.2, 5.1, 5.2
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.services.chat_message_service import (
    get_chat_activity_records,
    get_chat_record_by_id,
    ingestion_regions_for_chat_dedup,
    suppress_chat_placeholders_with_real_invocation,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_chat_msg(
    role: str,
    content: str,
    session_id: uuid.UUID | None = None,
    created_at: datetime | None = None,
    msg_id: uuid.UUID | None = None,
    user_identity: str | None = None,
    user_name: str | None = None,
):
    """Build a mock ChatMessage row.

    user_identity/user_name default to None (matching a real nullable column
    with no value) rather than being left unset — a MagicMock's default
    attribute access returns a truthy child mock, which would silently defeat
    the `user_msg.user_identity or "agent-chat"` fallback in
    ``_pair_to_record``.
    """
    msg = MagicMock()
    msg.id = msg_id or uuid.uuid4()
    msg.session_id = session_id or uuid.uuid4()
    msg.role = role
    msg.content = content
    msg.created_at = created_at or datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    msg.user_identity = user_identity
    msg.user_name = user_name
    return msg


# ---------------------------------------------------------------------------
# ChatMessageService — pairing logic
# ---------------------------------------------------------------------------

class TestGetChatActivityRecordsPairing:
    """Validates: Requirements 1.2, 1.3"""

    def test_two_user_two_assistant_produces_two_pairs(self):
        """2 user + 2 assistant messages in one session → 2 Activity_Records."""
        sid = uuid.uuid4()
        msgs = [
            _make_chat_msg("user", "Q1", session_id=sid, created_at=datetime(2024, 6, 1, 10, 0, tzinfo=timezone.utc)),
            _make_chat_msg("assistant", "A1", session_id=sid, created_at=datetime(2024, 6, 1, 10, 1, tzinfo=timezone.utc)),
            _make_chat_msg("user", "Q2", session_id=sid, created_at=datetime(2024, 6, 1, 10, 2, tzinfo=timezone.utc)),
            _make_chat_msg("assistant", "A2", session_id=sid, created_at=datetime(2024, 6, 1, 10, 3, tzinfo=timezone.utc)),
        ]

        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = msgs

        records = get_chat_activity_records(
            db,
            datetime(2024, 6, 1, tzinfo=timezone.utc),
            datetime(2024, 6, 2, tzinfo=timezone.utc),
        )

        assert len(records) == 2
        assert records[0]["promptText"] == "Q1"
        assert records[0]["responseText"] == "A1"
        assert records[1]["promptText"] == "Q2"
        assert records[1]["responseText"] == "A2"

    def test_trailing_user_message_excluded(self):
        """A trailing user message with no assistant reply is excluded."""
        sid = uuid.uuid4()
        msgs = [
            _make_chat_msg("user", "Q1", session_id=sid, created_at=datetime(2024, 6, 1, 10, 0, tzinfo=timezone.utc)),
            _make_chat_msg("assistant", "A1", session_id=sid, created_at=datetime(2024, 6, 1, 10, 1, tzinfo=timezone.utc)),
            _make_chat_msg("user", "Q2-trailing", session_id=sid, created_at=datetime(2024, 6, 1, 10, 2, tzinfo=timezone.utc)),
        ]

        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = msgs

        records = get_chat_activity_records(
            db,
            datetime(2024, 6, 1, tzinfo=timezone.utc),
            datetime(2024, 6, 2, tzinfo=timezone.utc),
        )

        assert len(records) == 1
        assert records[0]["promptText"] == "Q1"
        # The trailing user message "Q2-trailing" must not appear
        all_prompts = [r["promptText"] for r in records]
        assert "Q2-trailing" not in all_prompts

    def test_empty_session_returns_no_records(self):
        """No messages → no records."""
        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []

        records = get_chat_activity_records(
            db,
            datetime(2024, 6, 1, tzinfo=timezone.utc),
            datetime(2024, 6, 2, tzinfo=timezone.utc),
        )
        assert records == []


# ---------------------------------------------------------------------------
# ChatMessageService — field mapping
# ---------------------------------------------------------------------------

class TestFieldMapping:
    """Validates: Requirements 1.4"""

    @patch("app.services.chat_message_service.settings")
    def test_paired_exchange_field_mapping(self, mock_settings):
        """Verify all Activity_Record fields for a specific paired exchange."""
        mock_settings.bedrock_model_id = "us.anthropic.claude-sonnet-4-6"

        sid = uuid.uuid4()
        user_id = uuid.uuid4()
        user_ts = datetime(2024, 6, 15, 14, 30, 0, tzinfo=timezone.utc)

        msgs = [
            _make_chat_msg("user", "Hello world", session_id=sid, created_at=user_ts, msg_id=user_id),
            _make_chat_msg("assistant", "Hi there!", session_id=sid, created_at=datetime(2024, 6, 15, 14, 30, 5, tzinfo=timezone.utc)),
        ]

        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = msgs

        records = get_chat_activity_records(
            db,
            datetime(2024, 6, 15, tzinfo=timezone.utc),
            datetime(2024, 6, 16, tzinfo=timezone.utc),
        )

        assert len(records) == 1
        rec = records[0]
        assert rec["id"] == str(user_id)
        assert rec["timestamp"] == user_ts.isoformat()
        # No user_identity was set on the mock (pre-migration row / auth
        # disabled) — falls back to the legacy placeholder.
        assert rec["userIdentity"] == "agent-chat"
        assert rec["userName"] == "Agent Chat User"
        assert rec["modelId"] == "us.anthropic.claude-sonnet-4-6"
        assert rec["sourceApplication"] == "Agent Chat"
        assert rec["sourceIp"] == "N/A"
        assert rec["inputTokens"] == 0
        assert rec["outputTokens"] == 0
        assert rec["cacheReadTokens"] == 0
        assert rec["cacheWriteTokens"] == 0
        assert rec["totalTokens"] == 0
        assert rec["costEstimate"] is None
        assert rec["promptText"] == "Hello world"
        assert rec["responseText"] == "Hi there!"

    @patch("app.services.chat_message_service.settings")
    def test_real_user_identity_is_used_when_present(self, mock_settings):
        """When the user ChatMessage row carries a real identity (Track 1 —
        threaded from CurrentUser via AgentService.chat), the record reflects
        the actual caller instead of the 'agent-chat' placeholder."""
        mock_settings.bedrock_model_id = "us.anthropic.claude-sonnet-4-6"

        sid = uuid.uuid4()
        msgs = [
            _make_chat_msg(
                "user", "Hello world", session_id=sid,
                user_identity="alice@example.com", user_name="alice",
            ),
            _make_chat_msg(
                "assistant", "Hi there!", session_id=sid,
                created_at=datetime(2024, 6, 1, 12, 0, 5, tzinfo=timezone.utc),
            ),
        ]

        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = msgs

        records = get_chat_activity_records(
            db,
            datetime(2024, 6, 1, tzinfo=timezone.utc),
            datetime(2024, 6, 2, tzinfo=timezone.utc),
        )

        assert len(records) == 1
        assert records[0]["userIdentity"] == "alice@example.com"
        assert records[0]["userName"] == "alice"


# ---------------------------------------------------------------------------
# ChatMessageService — suppressing duplicate placeholders
# ---------------------------------------------------------------------------

def _real_record(
    record_id: str = "real-1",
    model_id: str = "anthropic.claude-sonnet-4-6",
    timestamp: str = "2024-06-15T14:30:03+00:00",
    input_tokens: int = 298,
    output_tokens: int = 218,
    cost_estimate: float | None = 0.0015,
) -> dict:
    return {
        "id": record_id,
        "timestamp": timestamp,
        "userIdentity": "arn:aws:iam::123456789012:role/ecs-task-role",
        "userName": "ecs-task-role",
        "modelId": model_id,
        "sourceApplication": "CloudWatch Logs",
        "sourceIp": "unknown",
        "inputTokens": input_tokens,
        "outputTokens": output_tokens,
        "cacheReadTokens": 0,
        "cacheWriteTokens": 0,
        "totalTokens": input_tokens + output_tokens,
        "costEstimate": cost_estimate,
    }


def _chat_placeholder(
    record_id: str = "chat-1",
    timestamp: str = "2024-06-15T14:30:05+00:00",
) -> dict:
    return {
        "id": record_id,
        "timestamp": timestamp,
        "userIdentity": "agent-chat",
        "userName": "Agent Chat User",
        "modelId": "anthropic.claude-sonnet-4-6",
        "sourceApplication": "Agent Chat",
        "sourceIp": "N/A",
        "inputTokens": 0,
        "outputTokens": 0,
        "cacheReadTokens": 0,
        "cacheWriteTokens": 0,
        "totalTokens": 0,
        "costEstimate": None,
    }


class TestSuppressChatPlaceholdersWithRealInvocation:

    @patch("app.services.chat_message_service.settings")
    def test_suppresses_placeholder_with_matching_real_record(self, mock_settings):
        mock_settings.bedrock_model_id = "anthropic.claude-sonnet-4-6"
        chat_records = [_chat_placeholder()]
        real_records = [_real_record()]

        result = suppress_chat_placeholders_with_real_invocation(chat_records, real_records)

        assert result == []

    @patch("app.services.chat_message_service.settings")
    def test_keeps_placeholder_when_no_real_record_matches(self, mock_settings):
        mock_settings.bedrock_model_id = "anthropic.claude-sonnet-4-6"
        chat_records = [_chat_placeholder()]
        # Real record uses a different model id — no match.
        real_records = [_real_record(model_id="amazon.nova-pro-v1:0")]

        result = suppress_chat_placeholders_with_real_invocation(chat_records, real_records)

        assert result == chat_records

    @patch("app.services.chat_message_service.settings")
    def test_keeps_placeholder_when_timestamps_too_far_apart(self, mock_settings):
        mock_settings.bedrock_model_id = "anthropic.claude-sonnet-4-6"
        chat_records = [_chat_placeholder(timestamp="2024-06-15T14:30:05+00:00")]
        # 10 minutes apart — well outside the tolerance window.
        real_records = [_real_record(timestamp="2024-06-15T14:20:00+00:00")]

        result = suppress_chat_placeholders_with_real_invocation(chat_records, real_records)

        assert result == chat_records

    @patch("app.services.chat_message_service.settings")
    def test_empty_chat_records_returns_empty(self, mock_settings):
        mock_settings.bedrock_model_id = "anthropic.claude-sonnet-4-6"
        assert suppress_chat_placeholders_with_real_invocation([], [_real_record()]) == []

    @patch("app.services.chat_message_service.settings")
    def test_empty_real_records_keeps_all_chat_records(self, mock_settings):
        mock_settings.bedrock_model_id = "anthropic.claude-sonnet-4-6"
        chat_records = [_chat_placeholder()]
        assert suppress_chat_placeholders_with_real_invocation(chat_records, []) == chat_records

    @patch("app.services.chat_message_service.settings")
    def test_real_record_can_only_suppress_one_placeholder(self, mock_settings):
        """One real record must not silently absorb multiple chat turns."""
        mock_settings.bedrock_model_id = "anthropic.claude-sonnet-4-6"
        chat_records = [
            _chat_placeholder(record_id="chat-1", timestamp="2024-06-15T14:30:05+00:00"),
            _chat_placeholder(record_id="chat-2", timestamp="2024-06-15T14:30:06+00:00"),
        ]
        real_records = [_real_record(record_id="real-1", timestamp="2024-06-15T14:30:03+00:00")]

        result = suppress_chat_placeholders_with_real_invocation(chat_records, real_records)

        # Exactly one placeholder is suppressed; the other survives.
        assert len(result) == 1

    @patch("app.services.chat_message_service.settings")
    def test_unrelated_model_records_are_ignored_as_candidates(self, mock_settings):
        mock_settings.bedrock_model_id = "anthropic.claude-sonnet-4-6"
        chat_records = [_chat_placeholder()]
        real_records = [
            _real_record(record_id="unrelated", model_id="amazon.titan-text-v1"),
            _real_record(record_id="matching"),
        ]

        result = suppress_chat_placeholders_with_real_invocation(chat_records, real_records)

        assert result == []

    @patch("app.services.chat_message_service.settings")
    def test_cross_region_prefixed_model_id_still_matches(self, mock_settings):
        """The configured chat model id may carry a cross-region prefix (e.g.
        'us.anthropic...'); the real record's modelId is already stripped by
        ingestion, so the comparison must normalize the same way."""
        mock_settings.bedrock_model_id = "us.anthropic.claude-sonnet-4-6"
        chat_records = [_chat_placeholder()]
        real_records = [_real_record(model_id="anthropic.claude-sonnet-4-6")]

        result = suppress_chat_placeholders_with_real_invocation(chat_records, real_records)

        assert result == []

    @patch("app.services.chat_message_service.settings")
    def test_malformed_timestamp_is_kept_not_dropped(self, mock_settings):
        mock_settings.bedrock_model_id = "anthropic.claude-sonnet-4-6"
        chat_records = [_chat_placeholder(timestamp="not-a-timestamp")]
        real_records = [_real_record()]

        result = suppress_chat_placeholders_with_real_invocation(chat_records, real_records)

        assert result == chat_records


# ---------------------------------------------------------------------------
# ChatMessageService — widening ingestion regions for chat dedup
# ---------------------------------------------------------------------------

class TestIngestionRegionsForChatDedup:
    """Regression test: a region filter that excludes Agent Chat's home
    region must not starve the dedup step of the real invocation record it
    needs — see the docstring on ``ingestion_regions_for_chat_dedup``."""

    @patch("app.services.chat_message_service.settings")
    def test_none_regions_returned_unchanged(self, mock_settings):
        """None means 'every configured region' already — nothing to widen."""
        mock_settings.bedrock_region = "us-west-2"
        mock_settings.aws_region = "us-west-2"
        assert ingestion_regions_for_chat_dedup(None) is None

    @patch("app.services.chat_message_service.settings")
    def test_empty_regions_returned_unchanged(self, mock_settings):
        mock_settings.bedrock_region = "us-west-2"
        mock_settings.aws_region = "us-west-2"
        assert ingestion_regions_for_chat_dedup([]) == []

    @patch("app.services.chat_message_service.settings")
    def test_chat_region_already_included_returned_unchanged(self, mock_settings):
        mock_settings.bedrock_region = "us-west-2"
        mock_settings.aws_region = "us-west-2"
        regions = ["us-east-1", "us-west-2"]
        assert ingestion_regions_for_chat_dedup(regions) == regions

    @patch("app.services.chat_message_service.settings")
    def test_chat_region_missing_is_appended(self, mock_settings):
        """The exact bug scenario: filtering to us-east-1 must still fetch
        us-west-2 (Agent Chat's home region) so its real invocation records
        are available to suppress the chat placeholder."""
        mock_settings.bedrock_region = "us-west-2"
        mock_settings.aws_region = "us-west-2"
        result = ingestion_regions_for_chat_dedup(["us-east-1"])
        assert result == ["us-east-1", "us-west-2"]

    @patch("app.services.chat_message_service.settings")
    def test_falls_back_to_aws_region_when_bedrock_region_unset(self, mock_settings):
        mock_settings.bedrock_region = None
        mock_settings.aws_region = "us-west-2"
        result = ingestion_regions_for_chat_dedup(["us-east-1"])
        assert result == ["us-east-1", "us-west-2"]

    @patch("app.services.chat_message_service.settings")
    def test_does_not_mutate_input_list(self, mock_settings):
        mock_settings.bedrock_region = "us-west-2"
        mock_settings.aws_region = "us-west-2"
        original = ["us-east-1"]
        result = ingestion_regions_for_chat_dedup(original)
        assert original == ["us-east-1"]
        assert result == ["us-east-1", "us-west-2"]


# ---------------------------------------------------------------------------
# ChatMessageService — detail retrieval
# ---------------------------------------------------------------------------

class TestGetChatRecordById:
    """Validates: Requirements 5.1, 5.2"""

    @patch("app.services.chat_message_service.settings")
    def test_returns_record_for_valid_user_message_id(self, mock_settings):
        """Looking up a user message ID returns the paired Activity_Record."""
        mock_settings.bedrock_model_id = "us.anthropic.claude-sonnet-4-6"

        sid = uuid.uuid4()
        user_id = uuid.uuid4()
        user_ts = datetime(2024, 6, 15, 14, 30, 0, tzinfo=timezone.utc)

        user_msg = _make_chat_msg("user", "What is 2+2?", session_id=sid, created_at=user_ts, msg_id=user_id)
        asst_msg = _make_chat_msg("assistant", "4", session_id=sid, created_at=datetime(2024, 6, 15, 14, 30, 5, tzinfo=timezone.utc))

        db = MagicMock()
        # First query: find user message by ID
        db.query.return_value.filter.return_value.first.return_value = user_msg
        # Second query: find next assistant message
        db.query.return_value.filter.return_value.order_by.return_value.first.return_value = asst_msg

        record = get_chat_record_by_id(db, str(user_id), can_view_all=True)

        assert record is not None
        assert record["id"] == str(user_id)
        assert record["promptText"] == "What is 2+2?"
        assert record["responseText"] == "4"

    def test_returns_none_for_nonexistent_id(self):
        """Non-existent ID returns None."""
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None

        record = get_chat_record_by_id(db, str(uuid.uuid4()), can_view_all=True)
        assert record is None

    def test_returns_none_for_assistant_message_id(self):
        """Looking up an assistant message ID returns None (only user IDs are valid)."""
        asst_msg = _make_chat_msg("assistant", "I'm an assistant reply")

        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = asst_msg

        record = get_chat_record_by_id(db, str(asst_msg.id), can_view_all=True)
        assert record is None

    def test_returns_none_when_no_assistant_reply(self):
        """User message with no following assistant reply returns None."""
        user_msg = _make_chat_msg("user", "Unanswered question")

        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = user_msg
        db.query.return_value.filter.return_value.order_by.return_value.first.return_value = None

        record = get_chat_record_by_id(db, str(user_msg.id), can_view_all=True)
        assert record is None


# ---------------------------------------------------------------------------
# Prompts route integration — detail 404
# ---------------------------------------------------------------------------

class TestPromptsRouteDetail404:
    """Validates: Requirements 5.2"""

    @pytest.fixture
    def mock_services(self):
        with (
            patch("app.routes.prompts._cloudtrail_service") as mock_ct,
            patch("app.routes.prompts._cost_service") as mock_cost,
            patch("app.routes.prompts._filter_service") as mock_filter,
            patch("app.routes.prompts.get_chat_activity_records") as mock_chat_list,
            patch("app.routes.prompts.get_chat_record_by_id") as mock_chat_detail,
        ):
            mock_ct.get_activity_records.return_value = []
            mock_cost.apply_costs.side_effect = lambda records, db: records
            mock_filter.get_default_date_range.return_value = (
                datetime(2024, 1, 10, tzinfo=timezone.utc),
                datetime(2024, 1, 17, tzinfo=timezone.utc),
            )
            mock_filter.validate_date_range.return_value = None
            mock_filter.apply_filters.side_effect = lambda records, filters: records
            mock_filter.filter_by_team.side_effect = lambda records, team_id, db: records
            mock_chat_list.return_value = []
            mock_chat_detail.return_value = None

            yield {
                "cloudtrail": mock_ct,
                "cost": mock_cost,
                "filter": mock_filter,
                "chat_list": mock_chat_list,
                "chat_detail": mock_chat_detail,
            }

    @pytest.fixture
    def client(self, mock_services):
        from main import app
        from app.database import get_db

        mock_db = MagicMock()
        app.dependency_overrides[get_db] = lambda: mock_db

        with TestClient(app) as c:
            yield c

        app.dependency_overrides.clear()

    def test_detail_returns_404_for_nonexistent_id(self, client):
        resp = client.get("/api/activity/prompts/nonexistent-chat-id")
        assert resp.status_code == 404
        data = resp.json()
        assert data["error"]["code"] == "NOT_FOUND"


# ---------------------------------------------------------------------------
# Prompts route integration — merged output
# ---------------------------------------------------------------------------

class TestPromptsRouteMergedOutput:
    """Validates: Requirements 2.1, 2.2"""

    @pytest.fixture
    def mock_services(self):
        with (
            patch("app.routes.prompts._cloudtrail_service") as mock_ct,
            patch("app.routes.prompts._cost_service") as mock_cost,
            patch("app.routes.prompts._filter_service") as mock_filter,
            patch("app.routes.prompts.get_chat_activity_records") as mock_chat_list,
        ):
            # CloudTrail returns one record
            mock_ct.get_activity_records.return_value = [
                {
                    "id": "ct-rec-1",
                    "timestamp": "2024-06-15T12:00:00+00:00",
                    "userIdentity": "arn:aws:iam::123:user/alice",
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
                    "promptText": "CT prompt",
                    "responseText": "CT response",
                },
            ]

            # Agent Chat returns one record
            mock_chat_list.return_value = [
                {
                    "id": "chat-rec-1",
                    "timestamp": "2024-06-15T14:00:00+00:00",
                    "userIdentity": "agent-chat",
                    "userName": "Agent Chat User",
                    "modelId": "us.anthropic.claude-sonnet-4-6",
                    "sourceApplication": "Agent Chat",
                    "sourceIp": "N/A",
                    "inputTokens": 0,
                    "outputTokens": 0,
                    "cacheReadTokens": 0,
                    "cacheWriteTokens": 0,
                    "totalTokens": 0,
                    "costEstimate": None,
                    "promptText": "Chat prompt",
                    "responseText": "Chat response",
                },
            ]

            mock_cost.apply_costs.side_effect = lambda records, db: records
            mock_filter.get_default_date_range.return_value = (
                datetime(2024, 6, 10, tzinfo=timezone.utc),
                datetime(2024, 6, 20, tzinfo=timezone.utc),
            )
            mock_filter.validate_date_range.return_value = None
            mock_filter.apply_filters.side_effect = lambda records, filters: records
            mock_filter.filter_by_team.side_effect = lambda records, team_id, db: records

            yield {
                "cloudtrail": mock_ct,
                "cost": mock_cost,
                "filter": mock_filter,
                "chat_list": mock_chat_list,
            }

    @pytest.fixture
    def client(self, mock_services):
        from main import app
        from app.database import get_db

        mock_db = MagicMock()
        app.dependency_overrides[get_db] = lambda: mock_db

        with TestClient(app) as c:
            yield c

        app.dependency_overrides.clear()

    def test_merged_output_contains_both_sources(self, client):
        """GET /api/activity/prompts returns records from both CloudTrail and Agent Chat."""
        resp = client.get("/api/activity/prompts")
        assert resp.status_code == 200
        data = resp.json()

        assert data["total"] == 2
        ids = [item["id"] for item in data["items"]]
        assert "ct-rec-1" in ids
        assert "chat-rec-1" in ids

    def test_merged_output_sorted_descending(self, client):
        """Merged records are sorted by timestamp descending (most recent first)."""
        resp = client.get("/api/activity/prompts")
        data = resp.json()
        items = data["items"]

        # chat-rec-1 (14:00) should come before ct-rec-1 (12:00)
        assert items[0]["id"] == "chat-rec-1"
        assert items[1]["id"] == "ct-rec-1"

        timestamps = [item["timestamp"] for item in items]
        assert timestamps == sorted(timestamps, reverse=True)

    def test_agent_chat_entry_has_correct_source(self, client):
        """Agent Chat entries have sourceApplication='Agent Chat'."""
        resp = client.get("/api/activity/prompts")
        data = resp.json()

        chat_items = [i for i in data["items"] if i["sourceApplication"] == "Agent Chat"]
        assert len(chat_items) == 1
        assert chat_items[0]["id"] == "chat-rec-1"


class TestGetChatRecordByIdOwnership:
    """AppSec F-2: a caller must not read another user's chat record by ID."""

    def _db(self, owner: str):
        sid = uuid.uuid4()
        ts = datetime(2024, 6, 15, 14, 30, 0, tzinfo=timezone.utc)
        user_msg = _make_chat_msg("user", "secret", session_id=sid, created_at=ts, user_identity=owner)
        asst_msg = _make_chat_msg("assistant", "reply", session_id=sid, created_at=ts)
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = user_msg
        db.query.return_value.filter.return_value.order_by.return_value.first.return_value = asst_msg
        return db, user_msg

    @patch("app.services.chat_message_service.settings")
    def test_owner_can_read(self, _s):
        db, msg = self._db("alice@example.com")
        assert get_chat_record_by_id(db, str(msg.id), viewer_identity="alice@example.com") is not None

    def test_other_user_gets_none(self):
        db, msg = self._db("alice@example.com")
        assert get_chat_record_by_id(db, str(msg.id), viewer_identity="bob@example.com") is None

    def test_missing_viewer_gets_none(self):
        db, msg = self._db("alice@example.com")
        assert get_chat_record_by_id(db, str(msg.id)) is None

    @patch("app.services.chat_message_service.settings")
    def test_view_all_can_read(self, _s):
        db, msg = self._db("alice@example.com")
        assert get_chat_record_by_id(db, str(msg.id), viewer_identity="boss", can_view_all=True) is not None
