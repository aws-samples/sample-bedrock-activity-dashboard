"""Unit tests for AI Agent API routes."""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, StaticPool
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.models import SummaryReport


# ---------------------------------------------------------------------------
# In-memory SQLite database for testing.
# ---------------------------------------------------------------------------

engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(autouse=True)
def setup_db():
    """Create tables before each test and drop after."""
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db_session(setup_db):
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(db_session):
    from main import app

    def _override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_report(db_session, **overrides) -> SummaryReport:
    """Insert a SummaryReport directly into the DB and return it."""
    now = datetime.now(timezone.utc)
    defaults = {
        "period_start": now - timedelta(days=7),
        "period_end": now,
        "report_type": "weekly",
        "content": {
            "usageTrends": {"totalTokensDelta": 10.0, "totalCostDelta": 5.0, "activeUsersDelta": 2.0},
            "recommendations": [],
            "anomalies": [],
        },
    }
    defaults.update(overrides)
    report = SummaryReport(**defaults)
    db_session.add(report)
    db_session.commit()
    db_session.refresh(report)
    return report


# ---------------------------------------------------------------------------
# Tests: POST /api/agent/chat
# ---------------------------------------------------------------------------

class TestAgentChat:
    @patch("app.routes.agent.AgentService")
    def test_chat_success(self, MockAgentService, client):
        mock_instance = MagicMock()
        mock_instance.chat.return_value = {
            "sessionId": "sess-123",
            "message": "Here is your analysis.",
            "timestamp": "2024-01-01T00:00:00+00:00",
        }
        MockAgentService.return_value = mock_instance

        resp = client.post("/api/agent/chat", json={
            "message": "What is my usage?",
            "filters": {},
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "sessionId" in data
        assert data["sessionId"] == "sess-123"
        assert "message" in data
        assert "timestamp" in data

    @patch("app.routes.agent.AgentService")
    def test_chat_with_session_id(self, MockAgentService, client):
        mock_instance = MagicMock()
        mock_instance.chat.return_value = {
            "sessionId": "existing-session",
            "message": "Follow-up response.",
            "timestamp": "2024-01-01T00:00:00+00:00",
        }
        MockAgentService.return_value = mock_instance

        resp = client.post("/api/agent/chat", json={
            "sessionId": "existing-session",
            "message": "Tell me more",
            "filters": {"start_date": "2024-01-01", "end_date": "2024-01-07"},
        })
        assert resp.status_code == 200
        assert resp.json()["sessionId"] == "existing-session"

    @patch("app.routes.agent.AgentService")
    def test_chat_bedrock_error_returns_502(self, MockAgentService, client):
        from app.services.agent_service import BedrockConverseError

        mock_instance = MagicMock()
        mock_instance.chat.side_effect = BedrockConverseError(
            error_code="ThrottlingException",
            message="Rate exceeded",
        )
        MockAgentService.return_value = mock_instance

        resp = client.post("/api/agent/chat", json={
            "message": "What is my usage?",
            "filters": {},
        })
        assert resp.status_code == 502
        data = resp.json()
        assert "detail" in data
        error = data["detail"]["error"]
        assert error["code"] == "ThrottlingException"
        assert error["message"] == "Rate exceeded"


# ---------------------------------------------------------------------------
# Tests: POST /api/agent/reports
# ---------------------------------------------------------------------------

class TestGenerateReport:
    @patch("app.routes.agent.ReportService")
    def test_generate_report_success(self, MockReportService, client):
        mock_instance = MagicMock()
        mock_instance.generate_report.return_value = {
            "id": "report-123",
            "periodStart": "2024-01-01T00:00:00",
            "periodEnd": "2024-01-07T00:00:00",
            "reportType": "weekly",
            "usageTrends": {"totalTokensDelta": 10.0, "totalCostDelta": 5.0, "activeUsersDelta": 2.0},
            "recommendations": [],
            "anomalies": [],
            "createdAt": "2024-01-07T12:00:00",
        }
        MockReportService.return_value = mock_instance

        resp = client.post("/api/agent/reports", json={
            "periodStart": "2024-01-01T00:00:00",
            "periodEnd": "2024-01-07T00:00:00",
            "reportType": "weekly",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "report-123"
        assert data["reportType"] == "weekly"


# ---------------------------------------------------------------------------
# Tests: GET /api/agent/reports
# ---------------------------------------------------------------------------

class TestListReports:
    @patch("app.routes.agent.ReportService")
    def test_list_reports(self, MockReportService, client):
        mock_instance = MagicMock()
        mock_instance.list_reports.return_value = [
            {"id": "r1", "reportType": "daily"},
            {"id": "r2", "reportType": "weekly"},
        ]
        MockReportService.return_value = mock_instance

        resp = client.get("/api/agent/reports")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2

    @patch("app.routes.agent.ReportService")
    def test_list_reports_empty(self, MockReportService, client):
        mock_instance = MagicMock()
        mock_instance.list_reports.return_value = []
        MockReportService.return_value = mock_instance

        resp = client.get("/api/agent/reports")
        assert resp.status_code == 200
        assert resp.json() == []


# ---------------------------------------------------------------------------
# Tests: GET /api/agent/reports/{id}
# ---------------------------------------------------------------------------

class TestGetReport:
    @patch("app.routes.agent.ReportService")
    def test_get_report_success(self, MockReportService, client):
        mock_instance = MagicMock()
        mock_instance.get_report.return_value = {
            "id": "report-123",
            "periodStart": "2024-01-01T00:00:00",
            "periodEnd": "2024-01-07T00:00:00",
            "reportType": "weekly",
            "usageTrends": {},
            "recommendations": [],
            "anomalies": [],
            "createdAt": "2024-01-07T12:00:00",
        }
        MockReportService.return_value = mock_instance

        resp = client.get("/api/agent/reports/report-123")
        assert resp.status_code == 200
        assert resp.json()["id"] == "report-123"

    @patch("app.routes.agent.ReportService")
    def test_get_report_not_found(self, MockReportService, client):
        mock_instance = MagicMock()
        mock_instance.get_report.return_value = None
        MockReportService.return_value = mock_instance

        resp = client.get(f"/api/agent/reports/{uuid.uuid4()}")
        assert resp.status_code == 404
