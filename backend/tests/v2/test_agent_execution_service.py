"""Unit tests for AgentExecutionService."""

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.v2.services.agent_execution_service import AgentExecutionService
from app.v2.services.agentcore_invoker import AgentUnavailableError


@pytest.fixture
def db():
    """Create a mock database session."""
    return MagicMock()


@pytest.fixture
def service():
    """Create an AgentExecutionService with a mocked invoker."""
    with patch(
        "app.v2.services.agent_execution_service.AgentCoreInvoker"
    ) as mock_invoker_cls:
        mock_invoker = MagicMock()
        mock_invoker_cls.return_value = mock_invoker
        svc = AgentExecutionService()
        svc._invoker = mock_invoker
        yield svc


class TestExecuteAgent:
    """Tests for execute_agent method."""

    def test_successful_execution(self, service, db):
        # Force the LLM/invoker fallback path so this test exercises the
        # mocked invoker (bypassing any direct-execution shortcut).
        service._try_direct_execution = MagicMock(return_value=None)
        # The service persists the invoker's findings and reports
        # finding_count as the number actually persisted. onboarding-assistant
        # dedups by user_identity, so three distinct user_identity values
        # produce three persisted rows.
        service._invoker.invoke_agent.return_value = {
            "findings": [
                {"user_identity": "u1", "team_assignment": "team-a",
                 "guidance_content": "Welcome u1", "severity": "low"},
                {"user_identity": "u2", "team_assignment": "team-b",
                 "guidance_content": "Welcome u2", "severity": "low"},
                {"user_identity": "u3", "team_assignment": "team-c",
                 "guidance_content": "Welcome u3", "severity": "low"},
            ]
        }
        # No pre-existing findings, so dedup lets all three persist.
        db.query.return_value.filter.return_value.first.return_value = None

        result = service.execute_agent("onboarding-assistant", "manual", db)

        assert result["status"] == "success"
        assert result["agent_name"] == "onboarding-assistant"
        assert result["finding_count"] == 3
        assert "execution_id" in result
        assert "start_time" in result
        assert "end_time" in result
        service._invoker.invoke_agent.assert_called_once()
        # The execution record plus the three findings were added.
        assert db.add.called
        db.commit.assert_called_once()

    def test_failure_records_error_details(self, service, db):
        service._try_direct_execution = MagicMock(return_value=None)
        service._invoker.invoke_agent.side_effect = AgentUnavailableError(
            "onboarding-assistant", "Connection refused"
        )
        db.query.return_value.filter.return_value.first.return_value = None

        with patch(
            "app.v2.services.agent_execution_service.AgentCoreInvoker.build_error_response",
            return_value={
                "error": "agent_unavailable",
                "agent": "onboarding-assistant",
                "message": "Agent 'onboarding-assistant' unavailable: Connection refused",
                "v1_status": "operational",
            },
        ):
            result = service.execute_agent("onboarding-assistant", "manual", db)

        assert result["error"] == "agent_unavailable"
        assert result["agent"] == "onboarding-assistant"
        assert result["v1_status"] == "operational"
        db.commit.assert_called_once()

    def test_unexpected_error_records_failure(self, service, db):
        service._try_direct_execution = MagicMock(return_value=None)
        service._invoker.invoke_agent.side_effect = RuntimeError("Unexpected boom")
        db.query.return_value.filter.return_value.first.return_value = None

        result = service.execute_agent("onboarding-assistant", "manual", db)

        assert result["status"] == "failure"
        assert "Unexpected boom" in result["error"]
        db.commit.assert_called_once()

    def test_skips_scheduled_execution_when_disabled(self, service, db):
        mock_config = MagicMock()
        mock_config.enabled = False
        db.query.return_value.filter.return_value.first.return_value = mock_config

        result = service.execute_agent("onboarding-assistant", "scheduled", db)

        assert result["status"] == "skipped"
        assert "disabled" in result["reason"].lower()
        service._invoker.invoke_agent.assert_not_called()

    def test_allows_manual_trigger_when_disabled(self, service, db):
        service._try_direct_execution = MagicMock(return_value=None)
        mock_config = MagicMock()
        mock_config.enabled = False
        db.query.return_value.filter.return_value.first.return_value = mock_config
        service._invoker.invoke_agent.return_value = {"findings": []}

        result = service.execute_agent("onboarding-assistant", "manual", db)

        assert result["status"] == "success"
        service._invoker.invoke_agent.assert_called_once()

    def test_allows_scheduled_execution_when_enabled(self, service, db):
        mock_config = MagicMock()
        mock_config.enabled = True
        db.query.return_value.filter.return_value.first.return_value = mock_config
        service._invoker.invoke_agent.return_value = {"finding_count": 0}

        result = service.execute_agent("onboarding-assistant", "scheduled", db)

        assert result["status"] == "success"

    def test_scheduled_execution_proceeds_when_no_config(self, service, db):
        """If no config exists, scheduled execution should proceed."""
        db.query.return_value.filter.return_value.first.return_value = None
        service._invoker.invoke_agent.return_value = {"finding_count": 0}

        result = service.execute_agent("unknown-agent", "scheduled", db)

        assert result["status"] == "success"


class TestGetExecutionHistory:
    """Tests for get_execution_history method."""

    def test_returns_history_ordered_by_start_time(self, service, db):
        row1 = MagicMock()
        row1.id = uuid.uuid4()
        row1.agent_name = "onboarding-assistant"
        row1.trigger_type = "manual"
        row1.start_time = datetime(2024, 1, 2, tzinfo=timezone.utc)
        row1.end_time = datetime(2024, 1, 2, 0, 5, tzinfo=timezone.utc)
        row1.status = "success"
        row1.finding_count = 5
        row1.error_details = None
        row1.created_at = datetime(2024, 1, 2, tzinfo=timezone.utc)

        row2 = MagicMock()
        row2.id = uuid.uuid4()
        row2.agent_name = "onboarding-assistant"
        row2.trigger_type = "scheduled"
        row2.start_time = datetime(2024, 1, 1, tzinfo=timezone.utc)
        row2.end_time = None
        row2.status = "failure"
        row2.finding_count = 0
        row2.error_details = "Connection timeout"
        row2.created_at = datetime(2024, 1, 1, tzinfo=timezone.utc)

        db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = [
            row1, row2
        ]

        history = service.get_execution_history("onboarding-assistant", db)

        assert len(history) == 2
        assert history[0]["status"] == "success"
        assert history[0]["finding_count"] == 5
        assert history[0]["error_details"] is None
        assert history[1]["status"] == "failure"
        assert history[1]["error_details"] == "Connection timeout"
        assert history[1]["end_time"] is None

    def test_returns_empty_list_when_no_history(self, service, db):
        db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []

        history = service.get_execution_history("unknown-agent", db)

        assert history == []

    def test_respects_limit_parameter(self, service, db):
        db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []

        service.get_execution_history("onboarding-assistant", db, limit=10)

        db.query.return_value.filter.return_value.order_by.return_value.limit.assert_called_with(10)


class TestUpdateAgentConfig:
    """Tests for update_agent_config method."""

    def test_updates_schedule_and_enabled(self, service, db):
        mock_config = MagicMock()
        mock_config.id = uuid.uuid4()
        mock_config.agent_name = "onboarding-assistant"
        mock_config.display_name = "Onboarding Assistant"
        mock_config.category = "compliance"
        mock_config.model_id = "claude-haiku"
        mock_config.schedule_cron = "0 */6 * * *"
        mock_config.enabled = True
        mock_config.updated_at = datetime(2024, 1, 1, tzinfo=timezone.utc)
        db.query.return_value.filter.return_value.first.return_value = mock_config

        result = service.update_agent_config(
            "onboarding-assistant",
            {"schedule_cron": "0 */12 * * *", "enabled": False},
            db,
        )

        assert mock_config.schedule_cron == "0 */12 * * *"
        assert mock_config.enabled is False
        db.commit.assert_called_once()
        assert result["agent_name"] == "onboarding-assistant"

    def test_returns_error_when_agent_not_found(self, service, db):
        db.query.return_value.filter.return_value.first.return_value = None

        result = service.update_agent_config("nonexistent", {"enabled": False}, db)

        assert result["error"] == "not_found"
        db.commit.assert_not_called()

    def test_partial_update_only_changes_provided_fields(self, service, db):
        mock_config = MagicMock()
        mock_config.id = uuid.uuid4()
        mock_config.agent_name = "onboarding-assistant"
        mock_config.display_name = "Onboarding Assistant"
        mock_config.category = "compliance"
        mock_config.model_id = "claude-haiku"
        mock_config.schedule_cron = "0 */6 * * *"
        mock_config.enabled = True
        mock_config.updated_at = datetime(2024, 1, 1, tzinfo=timezone.utc)
        db.query.return_value.filter.return_value.first.return_value = mock_config

        service.update_agent_config("onboarding-assistant", {"enabled": False}, db)

        # schedule_cron should remain unchanged
        assert mock_config.schedule_cron == "0 */6 * * *"
        assert mock_config.enabled is False


class TestTryDirectExecutionRoutingBoundary:
    """Regression tests for which agents _try_direct_execution short-circuits.

    guardrail-recommender is agentic (is_agentic=True) with a real
    AgentCore Runtime package that makes an LLM call. It must return None
    here so execute_agent() falls through to AgentCoreInvoker.invoke_agent()
    and actually reaches Runtime — a prior bug had a branch for this agent
    that always returned a (possibly empty) list, which meant every
    execution silently bypassed AgentCore Runtime and never accrued any
    Runtime cost, no matter how many times it ran.
    """

    def test_guardrail_recommender_returns_none_falls_through_to_invoker(self, service, db):
        result = service._try_direct_execution("guardrail-recommender", db)
        assert result is None

    def test_guardrail_recommender_execution_reaches_agentcore_invoker(self, service, db):
        db.query.return_value.filter.return_value.first.return_value = None
        service._invoker.invoke_agent.return_value = {
            "findings": [
                {
                    "recommendation": {"pii_type": "email", "guardrail_action": "ANONYMIZE"},
                    "based_on_findings": ["abc"],
                    "severity": "medium",
                }
            ]
        }

        service.execute_agent("guardrail-recommender", "manual", db)

        service._invoker.invoke_agent.assert_called_once()
        call_args = service._invoker.invoke_agent.call_args[0]
        assert call_args[0] == "guardrail-recommender"

    # NOTE: The pii-detector short-circuit regression test was removed when
    # the PII Detector agent was retired from the platform. The remaining
    # agents with direct-execution branches (cost-anomaly-alerter,
    # chargeback-report-generator, etc.) are each individually exercised in
    # the service-level integration suites — see
    # test_agent_finding_service.py for the finding-side coverage.
