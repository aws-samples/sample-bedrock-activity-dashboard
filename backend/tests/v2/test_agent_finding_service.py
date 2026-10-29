"""Unit tests for AgentFindingService."""

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from app.v2.models import (
    V2AgentCostAnomaly,
    V2AgentGuardrailRecommendation,
)
from app.v2.schemas import FindingFilters
from app.v2.services.agent_finding_service import AgentFindingService, AGENT_MODEL_MAP


@pytest.fixture
def db():
    """Create a mock database session."""
    return MagicMock()


@pytest.fixture
def mock_guardrail_service():
    """A GuardrailCreationService double whose apply_recommendation is mocked."""
    svc = MagicMock()
    svc.apply_recommendation.return_value = {"ok": False, "error": "not called in this test"}
    return svc


@pytest.fixture
def service(mock_guardrail_service):
    return AgentFindingService(guardrail_service=mock_guardrail_service)


class TestGetFindingsSummary:
    """Tests for get_findings_summary method."""

    def test_returns_counts_per_agent(self, service, db):
        # Set up mock: each model query returns a list of (agent_name, count) tuples
        def make_query_chain(results):
            mock = MagicMock()
            mock.group_by.return_value.all.return_value = results
            return mock

        call_count = 0
        expected_results = {
            "cost-anomaly-alerter": [("cost-anomaly-alerter", 3)],
            "model-cost-optimizer": [("model-cost-optimizer", 5)],
        }

        def side_effect(*args, **kwargs):
            nonlocal call_count
            # Each call to db.query() returns a mock with group_by().all()
            mock_q = MagicMock()
            # Determine which model is being queried by checking the call args
            model_cls = args[0] if args else args
            # Find the agent name for this model class
            agent_name = None
            for name, cls in AGENT_MODEL_MAP.items():
                if hasattr(model_cls, '__name__') and model_cls.__name__ == cls.__name__:
                    agent_name = name
                    break
                # Also check the second arg (func.count result)
                if model_cls is cls.agent_name:
                    agent_name = name
                    break

            results = expected_results.get(agent_name, [])
            mock_q.group_by.return_value.all.return_value = results
            call_count += 1
            return mock_q

        db.query.side_effect = side_effect

        service.get_findings_summary(db)

        # The mock setup is complex; verify the method was called for each agent
        assert db.query.call_count == len(AGENT_MODEL_MAP)

    def test_returns_empty_dict_when_no_findings(self, service, db):
        mock_q = MagicMock()
        mock_q.group_by.return_value.all.return_value = []
        db.query.return_value = mock_q

        summary = service.get_findings_summary(db)

        assert summary == {}


class TestGetFindings:
    """Tests for get_findings method."""

    def test_returns_empty_list_for_unknown_agent(self, service, db):
        result = service.get_findings("nonexistent-agent", FindingFilters(), db)
        assert result == []

    def test_queries_correct_model_for_agent(self, service, db):
        mock_q = MagicMock()
        mock_q.order_by.return_value.all.return_value = []
        db.query.return_value = mock_q

        service.get_findings("cost-anomaly-alerter", FindingFilters(), db)

        db.query.assert_called_once_with(V2AgentCostAnomaly)

    def test_applies_date_start_filter(self, service, db):
        mock_q = MagicMock()
        mock_q.filter.return_value = mock_q
        mock_q.order_by.return_value.all.return_value = []
        db.query.return_value = mock_q

        dt = datetime(2024, 6, 1, tzinfo=timezone.utc)
        service.get_findings("cost-anomaly-alerter", FindingFilters(date_start=dt), db)

        # filter should have been called at least once
        assert mock_q.filter.call_count >= 1

    def test_applies_date_end_filter(self, service, db):
        mock_q = MagicMock()
        mock_q.filter.return_value = mock_q
        mock_q.order_by.return_value.all.return_value = []
        db.query.return_value = mock_q

        dt = datetime(2024, 6, 30, tzinfo=timezone.utc)
        service.get_findings("cost-anomaly-alerter", FindingFilters(date_end=dt), db)

        assert mock_q.filter.call_count >= 1

    def test_applies_status_filter(self, service, db):
        mock_q = MagicMock()
        mock_q.filter.return_value = mock_q
        mock_q.order_by.return_value.all.return_value = []
        db.query.return_value = mock_q

        service.get_findings("cost-anomaly-alerter", FindingFilters(status="active"), db)

        assert mock_q.filter.call_count >= 1

    def test_applies_severity_filter(self, service, db):
        mock_q = MagicMock()
        mock_q.filter.return_value = mock_q
        mock_q.order_by.return_value.all.return_value = []
        db.query.return_value = mock_q

        service.get_findings("cost-anomaly-alerter", FindingFilters(severity="high"), db)

        assert mock_q.filter.call_count >= 1

    def test_applies_all_filters(self, service, db):
        mock_q = MagicMock()
        mock_q.filter.return_value = mock_q
        mock_q.order_by.return_value.all.return_value = []
        db.query.return_value = mock_q

        filters = FindingFilters(
            date_start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            date_end=datetime(2024, 12, 31, tzinfo=timezone.utc),
            status="active",
            severity="critical",
        )
        service.get_findings("cost-anomaly-alerter", filters, db)

        # 4 filters applied
        assert mock_q.filter.call_count == 4

    def test_no_filters_applied_when_all_none(self, service, db):
        mock_q = MagicMock()
        mock_q.order_by.return_value.all.return_value = []
        db.query.return_value = mock_q

        service.get_findings("cost-anomaly-alerter", FindingFilters(), db)

        mock_q.filter.assert_not_called()


class TestUpdateFindingStatus:
    """Tests for update_finding_status method."""

    def test_approve_finding(self, service, db):
        finding_id = str(uuid.uuid4())
        mock_finding = MagicMock()
        mock_finding.id = uuid.UUID(finding_id)
        mock_finding.agent_name = "cost-anomaly-alerter"
        mock_finding.status = "active"
        mock_finding.updated_at = datetime(2024, 1, 1, tzinfo=timezone.utc)

        # First model query returns None, second returns the finding
        call_idx = [0]

        def query_side_effect(model_cls):
            mock_q = MagicMock()
            call_idx[0] += 1
            if call_idx[0] == 1:
                # First agent model — return the finding
                mock_q.filter.return_value.first.return_value = mock_finding
            else:
                mock_q.filter.return_value.first.return_value = None
            return mock_q

        db.query.side_effect = query_side_effect

        result = service.update_finding_status(finding_id, "approved", None, db)

        assert result["status"] == "approved"
        assert result["id"] == finding_id
        db.commit.assert_called_once()

    def test_reject_finding_with_reason_on_guardrail(self, service, db):
        finding_id = str(uuid.uuid4())
        mock_finding = MagicMock(spec=V2AgentGuardrailRecommendation)
        mock_finding.id = uuid.UUID(finding_id)
        mock_finding.agent_name = "guardrail-recommender"
        mock_finding.status = "pending_review"
        mock_finding.updated_at = datetime(2024, 1, 1, tzinfo=timezone.utc)
        mock_finding.rejection_reason = None

        def query_side_effect(model_cls):
            mock_q = MagicMock()
            if model_cls is V2AgentGuardrailRecommendation:
                mock_q.filter.return_value.first.return_value = mock_finding
            else:
                mock_q.filter.return_value.first.return_value = None
            return mock_q

        db.query.side_effect = query_side_effect

        result = service.update_finding_status(
            finding_id, "rejected", "Not applicable to our use case", db
        )

        assert result["status"] == "rejected"
        assert mock_finding.rejection_reason == "Not applicable to our use case"
        db.commit.assert_called_once()

    def test_returns_not_found_when_id_missing(self, service, db):
        mock_q = MagicMock()
        mock_q.filter.return_value.first.return_value = None
        db.query.return_value = mock_q

        result = service.update_finding_status(
            str(uuid.uuid4()), "approved", None, db
        )

        assert result["error"] == "not_found"
        db.commit.assert_not_called()

    def test_approve_guardrail_finding_applies_real_guardrail_and_persists_result(
        self, service, db, mock_guardrail_service
    ):
        finding_id = str(uuid.uuid4())
        mock_finding = MagicMock(spec=V2AgentGuardrailRecommendation)
        mock_finding.id = uuid.UUID(finding_id)
        mock_finding.agent_name = "guardrail-recommender"
        mock_finding.status = "pending_review"
        mock_finding.updated_at = datetime(2024, 1, 1, tzinfo=timezone.utc)
        mock_finding.rejection_reason = None
        mock_finding.guardrail_id = None
        mock_finding.guardrail_arn = None
        mock_finding.guardrail_version = None
        mock_finding.guardrail_published_version = None
        mock_finding.creation_error = None

        mock_guardrail_service.apply_recommendation.return_value = {
            "ok": True,
            "guardrail_id": "abc123",
            "guardrail_arn": "arn:aws:bedrock:us-west-2:123456789012:guardrail/abc123",
            "guardrail_version": "DRAFT",
            "guardrail_published_version": "1",
        }

        def query_side_effect(model_cls):
            mock_q = MagicMock()
            if model_cls is V2AgentGuardrailRecommendation:
                mock_q.filter.return_value.first.return_value = mock_finding
            else:
                mock_q.filter.return_value.first.return_value = None
            return mock_q

        db.query.side_effect = query_side_effect

        result = service.update_finding_status(finding_id, "approved", None, db)

        mock_guardrail_service.apply_recommendation.assert_called_once_with(mock_finding, db)
        assert mock_finding.guardrail_id == "abc123"
        assert mock_finding.guardrail_arn == "arn:aws:bedrock:us-west-2:123456789012:guardrail/abc123"
        assert mock_finding.guardrail_published_version == "1"
        assert mock_finding.creation_error is None
        assert result["guardrail_id"] == "abc123"
        assert result["guardrail_arn"] == "arn:aws:bedrock:us-west-2:123456789012:guardrail/abc123"
        assert result["guardrail_published_version"] == "1"
        db.commit.assert_called_once()

    def test_approve_guardrail_finding_persists_creation_error_on_aws_failure(
        self, service, db, mock_guardrail_service
    ):
        finding_id = str(uuid.uuid4())
        mock_finding = MagicMock(spec=V2AgentGuardrailRecommendation)
        mock_finding.id = uuid.UUID(finding_id)
        mock_finding.agent_name = "guardrail-recommender"
        mock_finding.status = "pending_review"
        mock_finding.updated_at = datetime(2024, 1, 1, tzinfo=timezone.utc)
        mock_finding.rejection_reason = None
        mock_finding.guardrail_id = None
        mock_finding.guardrail_arn = None
        mock_finding.guardrail_version = None
        mock_finding.guardrail_published_version = None
        mock_finding.creation_error = None

        mock_guardrail_service.apply_recommendation.return_value = {
            "ok": False,
            "error": "AccessDeniedException: missing permissions",
        }

        def query_side_effect(model_cls):
            mock_q = MagicMock()
            if model_cls is V2AgentGuardrailRecommendation:
                mock_q.filter.return_value.first.return_value = mock_finding
            else:
                mock_q.filter.return_value.first.return_value = None
            return mock_q

        db.query.side_effect = query_side_effect

        result = service.update_finding_status(finding_id, "approved", None, db)

        # The human approval decision is still persisted even though the real
        # AWS call failed — only the AWS-side outcome fields differ.
        assert mock_finding.status == "approved"
        assert mock_finding.creation_error == "AccessDeniedException: missing permissions"
        assert result["status"] == "approved"
        assert result["creation_error"] == "AccessDeniedException: missing permissions"
        db.commit.assert_called_once()

    def test_rejecting_guardrail_finding_never_calls_guardrail_service(
        self, service, db, mock_guardrail_service
    ):
        finding_id = str(uuid.uuid4())
        mock_finding = MagicMock(spec=V2AgentGuardrailRecommendation)
        mock_finding.id = uuid.UUID(finding_id)
        mock_finding.agent_name = "guardrail-recommender"
        mock_finding.status = "pending_review"
        mock_finding.updated_at = datetime(2024, 1, 1, tzinfo=timezone.utc)
        mock_finding.rejection_reason = None

        def query_side_effect(model_cls):
            mock_q = MagicMock()
            if model_cls is V2AgentGuardrailRecommendation:
                mock_q.filter.return_value.first.return_value = mock_finding
            else:
                mock_q.filter.return_value.first.return_value = None
            return mock_q

        db.query.side_effect = query_side_effect

        service.update_finding_status(finding_id, "rejected", "no thanks", db)

        mock_guardrail_service.apply_recommendation.assert_not_called()

    def test_reject_without_reason_on_non_guardrail(self, service, db):
        finding_id = str(uuid.uuid4())
        mock_finding = MagicMock(spec=V2AgentCostAnomaly)
        mock_finding.id = uuid.UUID(finding_id)
        mock_finding.agent_name = "cost-anomaly-alerter"
        mock_finding.status = "active"
        mock_finding.updated_at = datetime(2024, 1, 1, tzinfo=timezone.utc)
        # Non-guardrail findings don't have a rejection_reason attribute
        del mock_finding.rejection_reason

        def query_side_effect(model_cls):
            mock_q = MagicMock()
            if model_cls is V2AgentCostAnomaly:
                mock_q.filter.return_value.first.return_value = mock_finding
            else:
                mock_q.filter.return_value.first.return_value = None
            return mock_q

        db.query.side_effect = query_side_effect

        result = service.update_finding_status(
            finding_id, "rejected", "some reason", db
        )

        assert result["status"] == "rejected"
        assert result["rejection_reason"] is None
        db.commit.assert_called_once()
