"""Unit tests for AgentService."""

import uuid
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from app.services.agent_service import (
    AgentService,
    BedrockConverseError,
    _fmt_cost,
    _supports_extended_thinking,
)


@pytest.fixture(autouse=True)
def _stub_source_records():
    """Default-stub the shared activity sourcing used by build_usage_context.

    build_usage_context now sources records via
    ``app.routes.activity.source_activity_records_with_meta`` (the same path the
    activity/teams routes use). By default we return no records so chat-focused
    tests exercise the model flow without hitting real AWS ingestion. Tests that
    care about context content re-patch this with their own return value.
    """
    with patch(
        "app.routes.activity.source_activity_records_with_meta",
        return_value=([], [], False, False),
    ) as m:
        yield m


def _mock_converse_response(text: str = "Here is my analysis.") -> dict:
    """Build a mock Bedrock Converse API response."""
    return {
        "output": {
            "message": {
                "role": "assistant",
                "content": [{"text": text}],
            }
        },
        "usage": {"inputTokens": 100, "outputTokens": 50},
        "stopReason": "end_turn",
    }


@patch("app.services.agent_service.boto3.Session")
class TestBuildUsageContext:
    """Tests for build_usage_context method."""

    def test_builds_context_with_records(self, mock_session, _stub_source_records):
        _stub_source_records.return_value = (
            [
                {
                    "id": "e1",
                    "timestamp": "2024-01-15T10:00:00+00:00",
                    "userIdentity": "arn:aws:iam::123:user/alice",
                    "userName": "alice",
                    "modelId": "anthropic.claude-3-haiku-20240307-v1:0",
                    "sourceApplication": "Kiro",
                    "sourceIp": "10.0.0.1",
                    "inputTokens": 500,
                    "outputTokens": 200,
                    "cacheReadTokens": 0,
                    "cacheWriteTokens": 0,
                    "totalTokens": 700,
                    "costEstimate": None,
                },
            ],
            [],
            False,
            False,
        )

        db = MagicMock()

        svc = AgentService()
        filters = {
            "start_date": "2024-01-01T00:00:00+00:00",
            "end_date": "2024-01-31T23:59:59+00:00",
        }
        context = svc.build_usage_context(filters, db)

        assert "Bedrock Usage Summary" in context
        assert "alice" in context
        assert "700" in context
        assert "anthropic.claude-3-haiku" in context
        # Source-application section is present.
        assert "Source Application Usage" in context
        assert "Kiro" in context

    def test_returns_no_data_message_on_empty_records(self, mock_session, _stub_source_records):
        _stub_source_records.return_value = ([], [], False, False)

        db = MagicMock()

        svc = AgentService()
        filters = {
            "start_date": "2024-01-01T00:00:00+00:00",
            "end_date": "2024-01-31T23:59:59+00:00",
        }
        context = svc.build_usage_context(filters, db)
        assert "No usage data available" in context

    def test_returns_no_data_on_sourcing_error(self, mock_session, _stub_source_records):
        _stub_source_records.side_effect = Exception("API error")

        db = MagicMock()
        svc = AgentService()
        filters = {}
        context = svc.build_usage_context(filters, db)
        assert "No usage data available" in context


@patch("app.services.agent_service.boto3.Session")
class TestChat:
    """Tests for the chat method end-to-end flow."""

    @patch("app.services.agent_service.CloudTrailService")
    def test_chat_returns_response_and_persists_messages(self, mock_ct_cls, mock_session):
        # Setup CloudTrail mock
        mock_ct = MagicMock()
        mock_ct.get_activity_records.return_value = []
        mock_ct_cls.return_value = mock_ct

        # Setup DB mock
        db = MagicMock()
        db.query.return_value.all.return_value = []
        db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []

        svc = AgentService()
        svc.client.converse = MagicMock(return_value=_mock_converse_response("Cost is $50."))

        result = svc.chat(
            session_id=None,
            message="What is my total cost?",
            filters={"start_date": "2024-01-01T00:00:00+00:00", "end_date": "2024-01-31T23:59:59+00:00"},
            db=db,
        )

        assert "sessionId" in result
        assert result["message"] == "Cost is $50."
        assert "timestamp" in result
        # Verify messages were persisted (2 add calls: user + assistant)
        assert db.add.call_count == 2
        db.commit.assert_called_once()

    @patch("app.services.agent_service.CloudTrailService")
    def test_chat_uses_existing_session_id(self, mock_ct_cls, mock_session):
        mock_ct = MagicMock()
        mock_ct.get_activity_records.return_value = []
        mock_ct_cls.return_value = mock_ct

        db = MagicMock()
        db.query.return_value.all.return_value = []
        db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []

        svc = AgentService()
        svc.client.converse = MagicMock(return_value=_mock_converse_response())

        existing_session = str(uuid.uuid4())
        result = svc.chat(
            session_id=existing_session,
            message="Hello",
            filters={},
            db=db,
        )
        assert result["sessionId"] == existing_session

    @patch("app.services.agent_service.CloudTrailService")
    def test_chat_includes_history_in_messages(self, mock_ct_cls, mock_session):
        mock_ct = MagicMock()
        mock_ct.get_activity_records.return_value = []
        mock_ct_cls.return_value = mock_ct

        # Create mock history messages
        hist_user = MagicMock()
        hist_user.role = "user"
        hist_user.content = "Previous question"
        hist_assistant = MagicMock()
        hist_assistant.role = "assistant"
        hist_assistant.content = "Previous answer"

        db = MagicMock()
        db.query.return_value.all.return_value = []
        # _load_history fetches the latest N by created_at DESC then reverses to
        # chronological ASC. The mocked limit().all() therefore returns DESC
        # order (assistant, then user) so the reversed result is user→assistant.
        db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = [
            hist_assistant,
            hist_user,
        ]

        svc = AgentService()
        svc.client.converse = MagicMock(return_value=_mock_converse_response())

        session_id = str(uuid.uuid4())
        svc.chat(session_id=session_id, message="Follow up", filters={}, db=db)

        # Verify converse was called with history + new message
        call_args = svc.client.converse.call_args
        messages = call_args.kwargs["messages"]
        assert len(messages) == 3  # 2 history + 1 new
        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == [{"text": "Previous question"}]
        assert messages[1]["role"] == "assistant"
        assert messages[2]["role"] == "user"
        assert messages[2]["content"] == [{"text": "Follow up"}]


@patch("app.services.agent_service.boto3.Session")
class TestGuardrailEnforcement:
    """Tests for wiring the dashboard's managed Guardrail into Agent Chat's
    own Converse calls (scoped enforcement, not account-wide)."""

    @patch("app.services.agent_service.CloudTrailService")
    @patch("app.services.agent_service.get_latest_published_guardrail")
    def test_guardrail_config_attached_when_published(
        self, mock_lookup, mock_ct_cls, mock_session
    ):
        mock_lookup.return_value = ("gr-abc123", "1")
        mock_ct_cls.return_value = MagicMock(get_activity_records=MagicMock(return_value=[]))

        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []

        svc = AgentService()
        svc.client.converse = MagicMock(return_value=_mock_converse_response())

        svc.chat(session_id=None, message="hi", filters={}, db=db)

        call_kwargs = svc.client.converse.call_args.kwargs
        assert call_kwargs["guardrailConfig"] == {
            "guardrailIdentifier": "gr-abc123",
            "guardrailVersion": "1",
            "trace": "enabled",
        }

    @patch("app.services.agent_service.CloudTrailService")
    @patch("app.services.agent_service.get_latest_published_guardrail")
    def test_no_guardrail_config_when_none_published(
        self, mock_lookup, mock_ct_cls, mock_session
    ):
        mock_lookup.return_value = None
        mock_ct_cls.return_value = MagicMock(get_activity_records=MagicMock(return_value=[]))

        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []

        svc = AgentService()
        svc.client.converse = MagicMock(return_value=_mock_converse_response())

        svc.chat(session_id=None, message="hi", filters={}, db=db)

        call_kwargs = svc.client.converse.call_args.kwargs
        assert "guardrailConfig" not in call_kwargs

    @patch("app.services.agent_service.CloudTrailService")
    @patch("app.services.agent_service.get_latest_published_guardrail")
    def test_guardrail_lookup_failure_does_not_block_chat(
        self, mock_lookup, mock_ct_cls, mock_session
    ):
        mock_lookup.side_effect = Exception("db unavailable")
        mock_ct_cls.return_value = MagicMock(get_activity_records=MagicMock(return_value=[]))

        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []

        svc = AgentService()
        svc.client.converse = MagicMock(return_value=_mock_converse_response())

        result = svc.chat(session_id=None, message="hi", filters={}, db=db)

        assert result["message"] == "Here is my analysis."
        call_kwargs = svc.client.converse.call_args.kwargs
        assert "guardrailConfig" not in call_kwargs

    @patch("app.services.agent_service.CloudTrailService")
    @patch("app.services.agent_service.get_latest_published_guardrail")
    def test_guardrail_intervened_surfaced_in_chat_response(
        self, mock_lookup, mock_ct_cls, mock_session
    ):
        mock_lookup.return_value = ("gr-abc123", "1")
        mock_ct_cls.return_value = MagicMock(get_activity_records=MagicMock(return_value=[]))

        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []

        svc = AgentService()
        response = _mock_converse_response("Sorry, I can't help with that.")
        response["stopReason"] = "guardrail_intervened"
        svc.client.converse = MagicMock(return_value=response)

        result = svc.chat(session_id=None, message="hi", filters={}, db=db)

        assert result["guardrailIntervened"] is True

    @patch("app.services.agent_service.CloudTrailService")
    @patch("app.services.agent_service.get_latest_published_guardrail")
    def test_guardrail_not_intervened_is_false_by_default(
        self, mock_lookup, mock_ct_cls, mock_session
    ):
        mock_lookup.return_value = None
        mock_ct_cls.return_value = MagicMock(get_activity_records=MagicMock(return_value=[]))

        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []

        svc = AgentService()
        svc.client.converse = MagicMock(return_value=_mock_converse_response())

        result = svc.chat(session_id=None, message="hi", filters={}, db=db)

        assert result["guardrailIntervened"] is False


@patch("app.services.agent_service.boto3.Session")
class TestRetryLogic:
    """Tests for Converse API retry with exponential backoff.

    _MAX_RETRIES is set to 1 (single attempt, no retries) to keep total
    latency well under the ALB 120s idle timeout.
    """

    @patch("app.services.agent_service.time.sleep")
    def test_raises_immediately_on_client_error(self, mock_sleep, mock_session):
        svc = AgentService()
        error_response = {"Error": {"Code": "ThrottlingException", "Message": "Rate exceeded"}}
        svc.client.converse = MagicMock(
            side_effect=ClientError(error_response, "Converse")
        )
        with pytest.raises(BedrockConverseError) as exc_info:
            svc._converse_with_retry("system prompt", [{"role": "user", "content": [{"text": "hi"}]}])
        assert exc_info.value.error_code == "ThrottlingException"
        # With _MAX_RETRIES=1, there is only 1 attempt and no sleep
        assert mock_sleep.call_count == 0

    @patch("app.services.agent_service.time.sleep")
    def test_raises_after_max_retries(self, mock_sleep, mock_session):
        svc = AgentService()
        error_response = {"Error": {"Code": "ModelNotReadyException", "Message": "Model loading"}}
        svc.client.converse = MagicMock(
            side_effect=ClientError(error_response, "Converse")
        )
        with pytest.raises(BedrockConverseError) as exc_info:
            svc._converse_with_retry("system", [{"role": "user", "content": [{"text": "hi"}]}])
        assert exc_info.value.error_code == "ModelNotReadyException"
        assert "Model loading" in exc_info.value.message
        # 1 attempt, 0 sleeps
        assert mock_sleep.call_count == 0

    @patch("app.services.agent_service.time.sleep")
    def test_no_backoff_with_single_attempt(self, mock_sleep, mock_session):
        svc = AgentService()
        error_response = {"Error": {"Code": "ThrottlingException", "Message": "Slow down"}}
        svc.client.converse = MagicMock(
            side_effect=ClientError(error_response, "Converse")
        )
        with pytest.raises(BedrockConverseError):
            svc._converse_with_retry("sys", [])
        # No sleep calls with a single attempt
        assert mock_sleep.call_args_list == []


@patch("app.services.agent_service.boto3.Session")
class TestErrorPropagation:
    """Tests for error code and message propagation."""

    def test_bedrock_converse_error_contains_code_and_message(self, mock_session):
        err = BedrockConverseError(error_code="AccessDeniedException", message="Not authorized")
        assert err.error_code == "AccessDeniedException"
        assert err.message == "Not authorized"
        assert "AccessDeniedException" in str(err)
        assert "Not authorized" in str(err)

    @patch("app.services.agent_service.time.sleep")
    @patch("app.services.agent_service.CloudTrailService")
    def test_chat_propagates_converse_error(self, mock_ct_cls, mock_sleep, mock_session):
        mock_ct = MagicMock()
        mock_ct.get_activity_records.return_value = []
        mock_ct_cls.return_value = mock_ct

        db = MagicMock()
        db.query.return_value.all.return_value = []
        db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []

        svc = AgentService()
        error_response = {"Error": {"Code": "ValidationException", "Message": "Invalid input"}}
        svc.client.converse = MagicMock(
            side_effect=ClientError(error_response, "Converse")
        )

        with pytest.raises(BedrockConverseError) as exc_info:
            svc.chat(session_id=None, message="test", filters={}, db=db)
        assert exc_info.value.error_code == "ValidationException"
        assert "Invalid input" in exc_info.value.message


# ---------------------------------------------------------------------------
# Helper for extended thinking responses
# ---------------------------------------------------------------------------

def _mock_converse_response_with_thinking(
    text: str = "Here is my analysis.",
    thinking: str = "Let me think step by step...",
) -> dict:
    """Build a mock Bedrock Converse API response with a thinking block."""
    return {
        "output": {
            "message": {
                "role": "assistant",
                "content": [
                    {"thinking": thinking},
                    {"text": text},
                ],
            }
        },
        "usage": {"inputTokens": 200, "outputTokens": 100},
        "stopReason": "end_turn",
    }


# ---------------------------------------------------------------------------
# Tests: Extended Thinking in chat()
# ---------------------------------------------------------------------------

@patch("app.services.agent_service.boto3.Session")
class TestChatExtendedThinking:
    """Tests for extended thinking support in chat()."""

    @patch("app.services.agent_service.CloudTrailService")
    def test_chat_returns_thinking_field_when_present(self, mock_ct_cls, mock_session):
        """When the Converse API returns a thinking block, chat() should include it."""
        mock_ct = MagicMock()
        mock_ct.get_activity_records.return_value = []
        mock_ct_cls.return_value = mock_ct

        db = MagicMock()
        db.query.return_value.all.return_value = []
        db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []

        svc = AgentService()
        svc.client.converse = MagicMock(
            return_value=_mock_converse_response_with_thinking(
                text="The total cost is $50.",
                thinking="Step 1: Look at cost data. Step 2: Sum it up.",
            )
        )

        result = svc.chat(session_id=None, message="What is my cost?", filters={}, db=db)

        assert result["message"] == "The total cost is $50."
        assert result["thinking"] == "Step 1: Look at cost data. Step 2: Sum it up."
        assert result["thinkingUnavailable"] is False

    @patch("app.services.agent_service.CloudTrailService")
    def test_chat_returns_null_thinking_when_not_present(self, mock_ct_cls, mock_session):
        """When the Converse API returns no thinking block, thinking should be null."""
        mock_ct = MagicMock()
        mock_ct.get_activity_records.return_value = []
        mock_ct_cls.return_value = mock_ct

        db = MagicMock()
        db.query.return_value.all.return_value = []
        db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []

        svc = AgentService()
        svc.client.converse = MagicMock(return_value=_mock_converse_response("Just text."))

        result = svc.chat(session_id=None, message="Hello", filters={}, db=db)

        assert result["message"] == "Just text."
        assert result["thinking"] is None
        assert result["thinkingUnavailable"] is False

    @patch("app.services.agent_service.CloudTrailService")
    def test_chat_passes_thinking_config_to_converse(self, mock_ct_cls, mock_session):
        """chat() should pass additionalModelRequestFields with thinking config."""
        mock_ct = MagicMock()
        mock_ct.get_activity_records.return_value = []
        mock_ct_cls.return_value = mock_ct

        db = MagicMock()
        db.query.return_value.all.return_value = []
        db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []

        svc = AgentService()
        svc.model_id = "us.anthropic.claude-sonnet-4-20250514-v1:0"
        svc.client.converse = MagicMock(return_value=_mock_converse_response())

        svc.chat(session_id=None, message="Hi", filters={}, db=db, budget_tokens=4096)

        call_kwargs = svc.client.converse.call_args.kwargs
        assert "additionalModelRequestFields" in call_kwargs
        thinking_config = call_kwargs["additionalModelRequestFields"]["thinking"]
        assert thinking_config["type"] == "enabled"
        assert thinking_config["budget_tokens"] == 4096

    @patch("app.services.agent_service.CloudTrailService")
    def test_chat_default_budget_tokens_is_2048(self, mock_ct_cls, mock_session):
        """Default budget_tokens should be 2048."""
        mock_ct = MagicMock()
        mock_ct.get_activity_records.return_value = []
        mock_ct_cls.return_value = mock_ct

        db = MagicMock()
        db.query.return_value.all.return_value = []
        db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []

        svc = AgentService()
        svc.model_id = "us.anthropic.claude-sonnet-4-20250514-v1:0"
        svc.client.converse = MagicMock(return_value=_mock_converse_response())

        svc.chat(session_id=None, message="Hi", filters={}, db=db)

        call_kwargs = svc.client.converse.call_args.kwargs
        thinking_config = call_kwargs["additionalModelRequestFields"]["thinking"]
        assert thinking_config["budget_tokens"] == 2048

    @patch("app.services.agent_service.CloudTrailService")
    def test_chat_fallback_on_thinking_error(self, mock_ct_cls, mock_session):
        """If converse fails with thinking, retry without and set thinkingUnavailable."""
        mock_ct = MagicMock()
        mock_ct.get_activity_records.return_value = []
        mock_ct_cls.return_value = mock_ct

        db = MagicMock()
        db.query.return_value.all.return_value = []
        db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []

        svc = AgentService()
        svc.model_id = "us.anthropic.claude-sonnet-4-20250514-v1:0"
        error_response = {"Error": {"Code": "ValidationException", "Message": "Thinking not supported"}}

        # First call (with thinking) fails, second call (without) succeeds
        svc.client.converse = MagicMock(
            side_effect=[
                ClientError(error_response, "Converse"),
                _mock_converse_response("Fallback response."),
            ]
        )

        result = svc.chat(session_id=None, message="Hi", filters={}, db=db)

        assert result["message"] == "Fallback response."
        assert result["thinking"] is None
        assert result["thinkingUnavailable"] is True
        # Converse should have been called twice
        assert svc.client.converse.call_count == 2
        # Second call should NOT have additionalModelRequestFields
        second_call_kwargs = svc.client.converse.call_args_list[1].kwargs
        assert "additionalModelRequestFields" not in second_call_kwargs


# ---------------------------------------------------------------------------
# Tests: _extract_thinking_and_text
# ---------------------------------------------------------------------------

@patch("app.services.agent_service.boto3.Session")
class TestExtractThinkingAndText:
    """Tests for the _extract_thinking_and_text static method."""

    def test_extracts_both_thinking_and_text(self, mock_session):
        blocks = [
            {"thinking": "My reasoning..."},
            {"text": "Final answer."},
        ]
        text, thinking = AgentService._extract_thinking_and_text(blocks)
        assert text == "Final answer."
        assert thinking == "My reasoning..."

    def test_extracts_text_only_when_no_thinking(self, mock_session):
        blocks = [{"text": "Just text."}]
        text, thinking = AgentService._extract_thinking_and_text(blocks)
        assert text == "Just text."
        assert thinking == ""

    def test_concatenates_multiple_text_blocks(self, mock_session):
        blocks = [
            {"thinking": "Think A"},
            {"text": "Part 1. "},
            {"thinking": "Think B"},
            {"text": "Part 2."},
        ]
        text, thinking = AgentService._extract_thinking_and_text(blocks)
        assert text == "Part 1. Part 2."
        assert thinking == "Think AThink B"

    def test_empty_blocks_returns_empty_strings(self, mock_session):
        text, thinking = AgentService._extract_thinking_and_text([])
        assert text == ""
        assert thinking == ""


# ---------------------------------------------------------------------------
# Tests: Extended Thinking in chat_stream()
# ---------------------------------------------------------------------------

@patch("app.services.agent_service.boto3.Session")
class TestChatStreamExtendedThinking:
    """Tests for extended thinking support in chat_stream()."""

    @patch("app.services.agent_service.CloudTrailService")
    def test_stream_emits_thinking_events_before_text(self, mock_ct_cls, mock_session):
        """Thinking SSE events should be emitted before text events."""
        mock_ct = MagicMock()
        mock_ct.get_activity_records.return_value = []
        mock_ct_cls.return_value = mock_ct

        db = MagicMock()
        db.query.return_value.all.return_value = []
        db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []

        svc = AgentService()

        # Simulate stream events: thinking delta, then text delta
        mock_stream = [
            {"contentBlockDelta": {"delta": {"thinking": "Reasoning..."}}},
            {"contentBlockDelta": {"delta": {"text": "Answer."}}},
        ]
        svc.client.converse_stream = MagicMock(return_value={"stream": mock_stream})

        events = list(svc.chat_stream(session_id=None, message="Hi", filters={}, db=db))

        # Find thinking and text events (exclude DONE)
        thinking_events = [e for e in events if "[THINKING]" in e and "[THINKING_UNAVAILABLE]" not in e]
        text_events = [e for e in events if "data: " in e and "[THINKING]" not in e and "[DONE:" not in e and "[THINKING_UNAVAILABLE]" not in e]

        assert len(thinking_events) >= 1
        assert "Reasoning..." in thinking_events[0]
        assert len(text_events) >= 1
        assert "Answer." in text_events[0]

        # Thinking events should come before text events in the list
        first_thinking_idx = events.index(thinking_events[0])
        first_text_idx = events.index(text_events[0])
        assert first_thinking_idx < first_text_idx

    @patch("app.services.agent_service.CloudTrailService")
    def test_stream_passes_thinking_config(self, mock_ct_cls, mock_session):
        """chat_stream() should pass additionalModelRequestFields to converse_stream."""
        mock_ct = MagicMock()
        mock_ct.get_activity_records.return_value = []
        mock_ct_cls.return_value = mock_ct

        db = MagicMock()
        db.query.return_value.all.return_value = []
        db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []

        svc = AgentService()
        svc.model_id = "us.anthropic.claude-sonnet-4-20250514-v1:0"
        svc.client.converse_stream = MagicMock(return_value={"stream": [
            {"contentBlockDelta": {"delta": {"text": "Hello"}}},
        ]})

        list(svc.chat_stream(session_id=None, message="Hi", filters={}, db=db, budget_tokens=1024))

        call_kwargs = svc.client.converse_stream.call_args.kwargs
        assert "additionalModelRequestFields" in call_kwargs
        thinking_config = call_kwargs["additionalModelRequestFields"]["thinking"]
        assert thinking_config["type"] == "enabled"
        assert thinking_config["budget_tokens"] == 1024

    @patch("app.services.agent_service.CloudTrailService")
    def test_stream_fallback_on_thinking_error(self, mock_ct_cls, mock_session):
        """If converse_stream fails with thinking, retry without and emit THINKING_UNAVAILABLE."""
        mock_ct = MagicMock()
        mock_ct.get_activity_records.return_value = []
        mock_ct_cls.return_value = mock_ct

        db = MagicMock()
        db.query.return_value.all.return_value = []
        db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []

        svc = AgentService()
        svc.model_id = "us.anthropic.claude-sonnet-4-20250514-v1:0"
        error_response = {"Error": {"Code": "ValidationException", "Message": "Not supported"}}

        # First call fails, second succeeds
        svc.client.converse_stream = MagicMock(
            side_effect=[
                ClientError(error_response, "ConverseStream"),
                {"stream": [{"contentBlockDelta": {"delta": {"text": "Fallback."}}}]},
            ]
        )

        events = list(svc.chat_stream(session_id=None, message="Hi", filters={}, db=db))

        # Should have THINKING_UNAVAILABLE event
        unavailable_events = [e for e in events if "THINKING_UNAVAILABLE" in e]
        assert len(unavailable_events) == 1

        # Should still have text content
        text_events = [e for e in events if "Fallback." in e]
        assert len(text_events) >= 1

        # converse_stream should have been called twice
        assert svc.client.converse_stream.call_count == 2


@patch("app.services.agent_service.boto3.Session")
class TestChatStreamGuardrailEnforcement:
    """Tests for wiring the dashboard's managed Guardrail into chat_stream()'s
    own ConverseStream calls."""

    @patch("app.services.agent_service.CloudTrailService")
    @patch("app.services.agent_service.get_latest_published_guardrail")
    def test_stream_passes_guardrail_config_when_published(
        self, mock_lookup, mock_ct_cls, mock_session
    ):
        mock_lookup.return_value = ("gr-abc123", "1")
        mock_ct_cls.return_value = MagicMock(get_activity_records=MagicMock(return_value=[]))

        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []

        svc = AgentService()
        svc.client.converse_stream = MagicMock(return_value={"stream": [
            {"contentBlockDelta": {"delta": {"text": "Hello"}}},
        ]})

        list(svc.chat_stream(session_id=None, message="Hi", filters={}, db=db))

        call_kwargs = svc.client.converse_stream.call_args.kwargs
        assert call_kwargs["guardrailConfig"] == {
            "guardrailIdentifier": "gr-abc123",
            "guardrailVersion": "1",
            "trace": "enabled",
        }

    @patch("app.services.agent_service.CloudTrailService")
    @patch("app.services.agent_service.get_latest_published_guardrail")
    def test_stream_no_guardrail_config_when_none_published(
        self, mock_lookup, mock_ct_cls, mock_session
    ):
        mock_lookup.return_value = None
        mock_ct_cls.return_value = MagicMock(get_activity_records=MagicMock(return_value=[]))

        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []

        svc = AgentService()
        svc.client.converse_stream = MagicMock(return_value={"stream": [
            {"contentBlockDelta": {"delta": {"text": "Hello"}}},
        ]})

        list(svc.chat_stream(session_id=None, message="Hi", filters={}, db=db))

        call_kwargs = svc.client.converse_stream.call_args.kwargs
        assert "guardrailConfig" not in call_kwargs

    @patch("app.services.agent_service.CloudTrailService")
    @patch("app.services.agent_service.get_latest_published_guardrail")
    def test_stream_emits_guardrail_intervened_event(
        self, mock_lookup, mock_ct_cls, mock_session
    ):
        mock_lookup.return_value = ("gr-abc123", "1")
        mock_ct_cls.return_value = MagicMock(get_activity_records=MagicMock(return_value=[]))

        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []

        svc = AgentService()
        svc.client.converse_stream = MagicMock(return_value={"stream": [
            {"contentBlockDelta": {"delta": {"text": "Sorry, can't help."}}},
            {"messageStop": {"stopReason": "guardrail_intervened"}},
        ]})

        events = list(svc.chat_stream(session_id=None, message="Hi", filters={}, db=db))

        intervened_events = [e for e in events if "GUARDRAIL_INTERVENED" in e]
        assert len(intervened_events) == 1

    @patch("app.services.agent_service.CloudTrailService")
    @patch("app.services.agent_service.get_latest_published_guardrail")
    def test_stream_no_intervened_event_on_normal_stop(
        self, mock_lookup, mock_ct_cls, mock_session
    ):
        mock_lookup.return_value = None
        mock_ct_cls.return_value = MagicMock(get_activity_records=MagicMock(return_value=[]))

        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []

        svc = AgentService()
        svc.client.converse_stream = MagicMock(return_value={"stream": [
            {"contentBlockDelta": {"delta": {"text": "Hello"}}},
            {"messageStop": {"stopReason": "end_turn"}},
        ]})

        events = list(svc.chat_stream(session_id=None, message="Hi", filters={}, db=db))

        assert not any("GUARDRAIL_INTERVENED" in e for e in events)


# ---------------------------------------------------------------------------
# Tests: _fmt_cost helper (cost precision)
# ---------------------------------------------------------------------------


class TestFmtCost:
    """Tests for the _fmt_cost cost-formatting helper."""

    def test_none_returns_na(self):
        assert _fmt_cost(None) == "N/A"

    def test_zero_returns_two_decimals(self):
        assert _fmt_cost(0) == "$0.00"
        assert _fmt_cost(0.0) == "$0.00"

    def test_sub_cent_uses_four_decimals(self):
        assert _fmt_cost(0.0004) == "$0.0004"

    def test_sub_cent_boundary_just_below_one_cent(self):
        # 0.009 is < 0.01 so uses 4 decimals
        assert _fmt_cost(0.009) == "$0.0090"

    def test_at_or_above_one_cent_uses_two_decimals(self):
        assert _fmt_cost(0.5) == "$0.50"
        assert _fmt_cost(0.01) == "$0.01"
        assert _fmt_cost(12.3456) == "$12.35"


# ---------------------------------------------------------------------------
# Tests: _supports_extended_thinking helper (gating)
# ---------------------------------------------------------------------------

class TestSupportsExtendedThinking:
    """Tests for the _supports_extended_thinking gating helper."""

    def test_claude_sonnet_4_supported(self):
        assert _supports_extended_thinking("us.anthropic.claude-sonnet-4-20250514-v1:0") is True
        # Without regional prefix
        assert _supports_extended_thinking("anthropic.claude-sonnet-4-20250514-v1:0") is True

    def test_claude_3_7_supported(self):
        assert _supports_extended_thinking("us.anthropic.claude-3-7-sonnet-20250219-v1:0") is True
        assert _supports_extended_thinking("anthropic.claude-3-7-sonnet-20250219-v1:0") is True

    def test_claude_opus_and_haiku_4_supported(self):
        assert _supports_extended_thinking("eu.anthropic.claude-opus-4-20250514-v1:0") is True
        assert _supports_extended_thinking("us.anthropic.claude-haiku-4-5-20251001-v1:0") is True

    def test_nova_not_supported(self):
        assert _supports_extended_thinking("us.amazon.nova-pro-v1:0") is False

    def test_llama_and_mistral_not_supported(self):
        assert _supports_extended_thinking("us.meta.llama3-1-70b-instruct-v1:0") is False
        assert _supports_extended_thinking("mistral.mistral-large-2407-v1:0") is False

    def test_older_claude_3_and_3_5_not_supported(self):
        assert _supports_extended_thinking("anthropic.claude-3-haiku-20240307-v1:0") is False
        assert _supports_extended_thinking("anthropic.claude-3-5-sonnet-20241022-v2:0") is False

    def test_empty_returns_false(self):
        assert _supports_extended_thinking("") is False


# ---------------------------------------------------------------------------
# Tests: build_usage_context distinct metrics + cost precision
# ---------------------------------------------------------------------------

@patch("app.services.agent_service.boto3.Session")
class TestBuildUsageContextDistinctMetrics:
    """Tests that per-model output has distinct, labeled invocation vs token shares."""

    def test_per_model_has_distinct_invocation_and_token_shares(
        self, mock_session, _stub_source_records
    ):
        # Two models with DIFFERENT invocation share vs token share:
        #  model A: 3 invocations, 300 tokens
        #  model B: 1 invocation, 700 tokens
        # invocations: A=75%, B=25%; tokens: A=30%, B=70%
        records = [
            {
                "id": "a1", "timestamp": "2024-01-15T10:00:00+00:00",
                "userIdentity": "u/alice", "userName": "alice",
                "modelId": "modelA", "sourceApplication": "Kiro", "sourceIp": "10.0.0.1",
                "inputTokens": 100, "outputTokens": 0, "cacheReadTokens": 0,
                "cacheWriteTokens": 0, "totalTokens": 100, "costEstimate": 0.0004,
            },
            {
                "id": "a2", "timestamp": "2024-01-15T10:01:00+00:00",
                "userIdentity": "u/alice", "userName": "alice",
                "modelId": "modelA", "sourceApplication": "Kiro", "sourceIp": "10.0.0.1",
                "inputTokens": 100, "outputTokens": 0, "cacheReadTokens": 0,
                "cacheWriteTokens": 0, "totalTokens": 100, "costEstimate": 0.0004,
            },
            {
                "id": "a3", "timestamp": "2024-01-15T10:02:00+00:00",
                "userIdentity": "u/alice", "userName": "alice",
                "modelId": "modelA", "sourceApplication": "Kiro", "sourceIp": "10.0.0.1",
                "inputTokens": 100, "outputTokens": 0, "cacheReadTokens": 0,
                "cacheWriteTokens": 0, "totalTokens": 100, "costEstimate": 0.0002,
            },
            {
                "id": "b1", "timestamp": "2024-01-15T10:03:00+00:00",
                "userIdentity": "u/bob", "userName": "bob",
                "modelId": "modelB", "sourceApplication": "Kiro", "sourceIp": "10.0.0.2",
                "inputTokens": 700, "outputTokens": 0, "cacheReadTokens": 0,
                "cacheWriteTokens": 0, "totalTokens": 700, "costEstimate": 5.0,
            },
        ]
        # source_activity_records_with_meta returns (ct_records, chat_records,
        # truncated, ingestion_error) with costs already applied in place.
        _stub_source_records.return_value = (records, [], False, False)

        db = MagicMock()
        svc = AgentService()
        filters = {
            "start_date": "2024-01-01T00:00:00+00:00",
            "end_date": "2024-01-31T23:59:59+00:00",
        }
        context = svc.build_usage_context(filters, db)

        # Distinct, labeled shares present
        assert "% of invocations" in context
        assert "% of tokens" in context

        # modelA: 75.0% of invocations, 30.0% of tokens
        assert "modelA: 3 invocations (75.0% of invocations), 300 tokens (30.0% of tokens)" in context
        # modelB: 25.0% of invocations, 70.0% of tokens
        assert "modelB: 1 invocations (25.0% of invocations), 700 tokens (70.0% of tokens)" in context

        # Sub-cent model cost (modelA total 0.001) is NOT rendered as $0.00
        # It should appear with 4 decimals.
        assert "cost $0.0010" in context
        # No misleading "$0.00" for the priced sub-cent model
        assert "300 tokens (30.0% of tokens), cost $0.00\n" not in context
