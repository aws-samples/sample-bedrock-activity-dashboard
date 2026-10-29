"""Unit tests for the deterministic CUR Ingestion agent (v2 direct-execution path)."""

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from app.services.cur_ingestion_service import IngestionResult
from app.v2.agents.cur_ingestion import CurIngestionFailedError, run_ingestion
from app.v2.models import V2AgentGenericFinding
from app.v2.seed_agent_configs import DEFAULT_AGENTS
from app.v2.services.agent_execution_service import AgentExecutionService
from app.v2.services.agent_finding_service import AGENT_MODEL_MAP


@pytest.fixture
def db():
    """Mock database session (no pre-existing rows)."""
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    return db


@pytest.fixture
def service():
    """AgentExecutionService with a mocked AgentCore invoker."""
    with patch(
        "app.v2.services.agent_execution_service.AgentCoreInvoker"
    ) as mock_invoker_cls:
        mock_invoker = MagicMock()
        mock_invoker_cls.return_value = mock_invoker
        svc = AgentExecutionService()
        svc._invoker = mock_invoker
        yield svc


def _patch_ingest(result: IngestionResult):
    return patch(
        "app.services.cur_ingestion_service.CurIngestionService.ingest",
        return_value=result,
    )


class TestRunIngestion:
    """Tests for the run_ingestion conversion of IngestionResult -> findings."""

    def test_success_returns_informational_finding(self, db):
        result = IngestionResult(
            status="success",
            rows_upserted=42,
            covered_through=date(2026, 1, 31),
            latest_billing_period="2026-01",
        )
        with _patch_ingest(result):
            findings = run_ingestion(db)

        assert len(findings) == 1
        f = findings[0]
        assert f["status"] == "success"
        assert f["rows_upserted"] == 42
        assert f["covered_through"] == "2026-01-31"
        assert f["latest_billing_period"] == "2026-01"
        assert f["error_detail"] is None
        assert f["severity"] == "low"

    def test_skipped_is_benign_informational_finding(self, db):
        result = IngestionResult(status="skipped", error_detail="athena not configured")
        with _patch_ingest(result):
            findings = run_ingestion(db)

        assert len(findings) == 1
        assert findings[0]["status"] == "skipped"
        assert findings[0]["rows_upserted"] == 0
        assert findings[0]["covered_through"] is None
        assert findings[0]["error_detail"] == "athena not configured"

    def test_failed_raises(self, db):
        result = IngestionResult(status="failed", error_detail="athena boom")
        with _patch_ingest(result), pytest.raises(CurIngestionFailedError, match="athena boom"):
            run_ingestion(db)


class TestExecuteAgentCurIngestion:
    """Tests for cur-ingestion routed through AgentExecutionService."""

    def test_skipped_ingestion_surfaces_as_success(self, service, db):
        with _patch_ingest(IngestionResult(status="skipped", error_detail="disabled")):
            result = service.execute_agent("cur-ingestion", "manual", db)

        assert result["status"] == "success"
        service._invoker.invoke_agent.assert_not_called()

    def test_failed_ingestion_surfaces_as_failure_without_llm_fallback(self, service, db):
        with _patch_ingest(IngestionResult(status="failed", error_detail="athena boom")):
            result = service.execute_agent("cur-ingestion", "manual", db)

        assert result["status"] == "failure"
        assert "athena boom" in result["error"]
        service._invoker.invoke_agent.assert_not_called()

    def test_successful_ingestion_surfaces_as_success(self, service, db):
        ingest_result = IngestionResult(
            status="success",
            rows_upserted=7,
            covered_through=date(2026, 2, 1),
            latest_billing_period="2026-02",
        )
        with _patch_ingest(ingest_result):
            result = service.execute_agent("cur-ingestion", "manual", db)

        assert result["status"] == "success"
        service._invoker.invoke_agent.assert_not_called()


def _added_generic_findings(db) -> list[V2AgentGenericFinding]:
    """Extract V2AgentGenericFinding instances passed to db.add()."""
    return [
        call.args[0]
        for call in db.add.call_args_list
        if isinstance(call.args[0], V2AgentGenericFinding)
    ]


class TestCurIngestionFindingPersistence:
    """Tests that cur-ingestion findings are persisted and counted (UI-visible)."""

    def test_successful_ingestion_persists_one_generic_finding(self, service, db):
        ingest_result = IngestionResult(
            status="success",
            rows_upserted=42,
            covered_through=date(2026, 7, 21),
            latest_billing_period="2026-07",
        )
        with _patch_ingest(ingest_result):
            result = service.execute_agent("cur-ingestion", "manual", db)

        assert result["status"] == "success"
        assert result["finding_count"] == 1

        added = _added_generic_findings(db)
        assert len(added) == 1
        finding = added[0]
        assert finding.agent_name == "cur-ingestion"
        assert finding.status == "active"
        assert finding.severity == "low"
        assert "42 rows upserted" in finding.title
        assert "2026-07-21" in finding.title
        assert finding.details["status"] == "success"
        assert finding.details["rows_upserted"] == 42
        assert finding.details["covered_through"] == "2026-07-21"
        assert finding.details["latest_billing_period"] == "2026-07"

    def test_skipped_ingestion_persists_one_generic_finding(self, service, db):
        with _patch_ingest(
            IngestionResult(status="skipped", error_detail="athena not configured")
        ):
            result = service.execute_agent("cur-ingestion", "manual", db)

        assert result["status"] == "success"
        assert result["finding_count"] == 1

        added = _added_generic_findings(db)
        assert len(added) == 1
        finding = added[0]
        assert finding.status == "active"
        assert "skipped" in finding.title
        assert "athena not configured" in finding.title
        assert finding.details["status"] == "skipped"

    def test_failed_ingestion_persists_no_finding(self, service, db):
        with _patch_ingest(IngestionResult(status="failed", error_detail="boom")):
            result = service.execute_agent("cur-ingestion", "manual", db)

        assert result["status"] == "failure"
        assert _added_generic_findings(db) == []

    def test_cur_ingestion_registered_in_finding_service_map(self):
        assert AGENT_MODEL_MAP.get("cur-ingestion") is V2AgentGenericFinding


class TestSeedRegistry:
    """Tests for the cur-ingestion registry seed entry."""

    def test_cur_ingestion_seeded_as_deterministic(self):
        entry = next(
            (a for a in DEFAULT_AGENTS if a["agent_name"] == "cur-ingestion"), None
        )
        assert entry is not None
        assert entry["display_name"] == "CUR Ingestion"
        assert entry["category"] == "Compliance & Operations"
        assert entry["is_agentic"] is False
        assert entry["schedule_cron"] == "0 9 * * *"

    def test_registry_has_fourteen_agents(self):
        assert len(DEFAULT_AGENTS) == 14
