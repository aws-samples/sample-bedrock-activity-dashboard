"""Unit tests for the executive cost rollup endpoint (GET /api/governance/rollup).

Mocks the activity-sourcing pipeline the same way test_activity_routes.py does,
and asserts the L1 (total) -> L2 (department) -> L3 (agent) shape, basis
selection (estimated when no CUR data is configured), and empty-input behavior.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


def _make_record(
    user_identity: str = "arn:aws:iam::123456789012:user/alice",
    model_id: str = "anthropic.claude-sonnet-4-20250514-v1:0",
    source_application: str = "Kiro",
    timestamp: str = "2024-01-15T10:30:00+00:00",
    input_tokens: int = 100,
    output_tokens: int = 50,
    cost_estimate: float | None = 0.05,
    department: str = "unknown",
    agent_name: str = "unknown",
    application: str = "unknown",
) -> dict:
    total = input_tokens + output_tokens
    return {
        "id": f"test-id-{department}-{agent_name}-{timestamp}",
        "timestamp": timestamp,
        "userIdentity": user_identity,
        "userName": user_identity.split("/")[-1] if "/" in user_identity else user_identity,
        "modelId": model_id,
        "sourceApplication": source_application,
        "sourceIp": "10.0.0.1",
        "inputTokens": input_tokens,
        "outputTokens": output_tokens,
        "cacheReadTokens": 0,
        "cacheWriteTokens": 0,
        "totalTokens": total,
        "costEstimate": cost_estimate,
        "department": department,
        "agentName": agent_name,
        "application": application,
    }


SAMPLE_RECORDS = [
    _make_record(
        user_identity="user-a",
        timestamp="2024-01-15T08:00:00+00:00",
        cost_estimate=1.0,
        department="Finance",
        agent_name="PaymentPlanAgent",
    ),
    _make_record(
        user_identity="user-b",
        timestamp="2024-01-16T10:00:00+00:00",
        cost_estimate=2.0,
        department="Finance",
        agent_name="PaymentPlanAgent",
    ),
    _make_record(
        user_identity="user-c",
        timestamp="2024-01-16T11:00:00+00:00",
        cost_estimate=3.0,
        department="Engineering",
        agent_name="PatientOutreachAgent",
    ),
]


@pytest.fixture
def mock_services():
    """Patch the activity-sourcing dependencies used by the governance router."""
    with (
        patch("app.routes.activity._cloudtrail_service") as mock_ct,
        patch("app.routes.activity._cost_service") as mock_cost,
        patch("app.routes.activity._filter_service") as mock_filter,
        patch("app.routes.activity.enrich_records_with_dimensions") as mock_enrich,
        patch("app.routes.governance._cur_service") as mock_cur,
    ):
        mock_ct.get_activity_records_with_meta.return_value = (list(SAMPLE_RECORDS), False, False)
        mock_cost.apply_costs.side_effect = lambda records, db: records
        # The fake records already carry their department/agentName directly
        # (this test isn't exercising ProfileTagService resolution), so make
        # enrichment a no-op instead of overwriting those pre-set dimensions.
        mock_enrich.side_effect = lambda records, db: None
        mock_filter.get_default_date_range.return_value = (
            datetime(2024, 1, 10, tzinfo=timezone.utc),
            datetime(2024, 1, 17, tzinfo=timezone.utc),
        )
        mock_filter.validate_date_range.return_value = None
        mock_filter.apply_filters.side_effect = lambda records, filters: records
        mock_filter.filter_by_team.side_effect = lambda records, team_id, db: records
        # No CUR configured -> estimated basis (the common/default case).
        mock_cur.is_configured.return_value = False

        yield {
            "cloudtrail": mock_ct,
            "cost": mock_cost,
            "filter": mock_filter,
            "cur": mock_cur,
        }


@pytest.fixture
def client(mock_services):
    from main import app
    from app.database import get_db

    mock_db = MagicMock()
    app.dependency_overrides[get_db] = lambda: mock_db

    with TestClient(app) as c:
        yield c

    app.dependency_overrides.clear()


class TestExecutiveRollup:
    def test_returns_l1_l2_l3_shape(self, client):
        resp = client.get("/api/governance/rollup")
        assert resp.status_code == 200
        data = resp.json()
        assert "l1Total" in data
        assert "l1InvocationCount" in data
        assert "byDepartment" in data
        assert "byAgent" in data
        assert "basis" in data

    def test_l1_total_sums_all_records(self, client):
        resp = client.get("/api/governance/rollup")
        data = resp.json()
        assert data["l1Total"] == pytest.approx(6.0)
        assert data["l1InvocationCount"] == 3

    def test_basis_is_estimated_when_no_cur(self, client):
        resp = client.get("/api/governance/rollup")
        data = resp.json()
        assert data["basis"] == "estimated"

    def test_department_rollup_groups_correctly(self, client):
        resp = client.get("/api/governance/rollup")
        data = resp.json()
        by_dept = {row["label"]: row for row in data["byDepartment"]}
        assert set(by_dept.keys()) == {"Finance", "Engineering"}
        assert by_dept["Finance"]["cost"] == pytest.approx(3.0)
        assert by_dept["Finance"]["invocationCount"] == 2
        assert by_dept["Engineering"]["cost"] == pytest.approx(3.0)
        assert by_dept["Engineering"]["invocationCount"] == 1

    def test_agent_rollup_groups_correctly(self, client):
        resp = client.get("/api/governance/rollup")
        data = resp.json()
        by_agent = {row["label"]: row for row in data["byAgent"]}
        assert set(by_agent.keys()) == {"PaymentPlanAgent", "PatientOutreachAgent"}
        assert by_agent["PaymentPlanAgent"]["cost"] == pytest.approx(3.0)
        assert by_agent["PaymentPlanAgent"]["invocationCount"] == 2

    def test_department_param_scopes_l1_total_and_by_agent(self, client):
        resp = client.get("/api/governance/rollup", params={"department": "Finance"})
        assert resp.status_code == 200
        data = resp.json()
        # L1 is scoped to Finance's two records (1.0 + 2.0).
        assert data["l1Total"] == pytest.approx(3.0)
        assert data["l1InvocationCount"] == 2
        # byAgent only reflects Finance's agent (PaymentPlanAgent), not
        # Engineering's PatientOutreachAgent.
        by_agent = {row["label"]: row for row in data["byAgent"]}
        assert set(by_agent.keys()) == {"PaymentPlanAgent"}
        assert by_agent["PaymentPlanAgent"]["cost"] == pytest.approx(3.0)
        assert by_agent["PaymentPlanAgent"]["invocationCount"] == 2

    def test_department_param_is_case_insensitive(self, client):
        resp = client.get("/api/governance/rollup", params={"department": "finance"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["l1Total"] == pytest.approx(3.0)

    def test_department_param_still_lists_every_department_in_by_department(self, client):
        resp = client.get("/api/governance/rollup", params={"department": "Finance"})
        data = resp.json()
        # byDepartment always reflects the full window, regardless of drill.
        by_dept = {row["label"] for row in data["byDepartment"]}
        assert by_dept == {"Finance", "Engineering"}

    def test_unknown_department_param_yields_zero_scoped_totals(self, client):
        resp = client.get("/api/governance/rollup", params={"department": "Nonexistent"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["l1Total"] == 0.0
        assert data["l1InvocationCount"] == 0
        assert data["byAgent"] == []
        # byDepartment is unaffected by the (unmatched) drill.
        assert {row["label"] for row in data["byDepartment"]} == {"Finance", "Engineering"}

    def test_empty_records_returns_zero_totals(self, client, mock_services):
        mock_services["cloudtrail"].get_activity_records_with_meta.return_value = ([], False, False)
        resp = client.get("/api/governance/rollup")
        assert resp.status_code == 200
        data = resp.json()
        assert data["l1Total"] == 0.0
        assert data["l1InvocationCount"] == 0
        assert data["byDepartment"] == []
        assert data["byAgent"] == []


SPEND_BY_APPLICATION_RECORDS = [
    _make_record(
        user_identity="user-a",
        timestamp="2024-01-15T08:00:00+00:00",
        cost_estimate=1.0,
        application="ClaimsProcessor",
    ),
    _make_record(
        user_identity="user-b",
        timestamp="2024-01-16T10:00:00+00:00",
        cost_estimate=4.0,
        application="ClaimsProcessor",
    ),
    _make_record(
        user_identity="user-c",
        timestamp="2024-01-16T11:00:00+00:00",
        cost_estimate=2.0,
        application="unknown",
    ),
]


class TestSpendByApplication:
    def test_returns_rows_and_basis(self, client, mock_services):
        mock_services["cloudtrail"].get_activity_records_with_meta.return_value = (
            list(SPEND_BY_APPLICATION_RECORDS), False, False,
        )
        resp = client.get("/api/governance/spend-by-application")
        assert resp.status_code == 200
        data = resp.json()
        assert data["basis"] == "estimated"
        assert "rows" in data

    def test_groups_by_application_and_sums_cost(self, client, mock_services):
        mock_services["cloudtrail"].get_activity_records_with_meta.return_value = (
            list(SPEND_BY_APPLICATION_RECORDS), False, False,
        )
        resp = client.get("/api/governance/spend-by-application")
        data = resp.json()
        by_app = {row["label"]: row for row in data["rows"]}
        assert set(by_app.keys()) == {"ClaimsProcessor", "unknown"}
        assert by_app["ClaimsProcessor"]["cost"] == pytest.approx(5.0)
        assert by_app["ClaimsProcessor"]["invocationCount"] == 2
        assert by_app["unknown"]["cost"] == pytest.approx(2.0)
        assert by_app["unknown"]["invocationCount"] == 1

    def test_rows_sorted_descending_by_cost(self, client, mock_services):
        mock_services["cloudtrail"].get_activity_records_with_meta.return_value = (
            list(SPEND_BY_APPLICATION_RECORDS), False, False,
        )
        resp = client.get("/api/governance/spend-by-application")
        data = resp.json()
        costs = [row["cost"] for row in data["rows"]]
        assert costs == sorted(costs, reverse=True)
        assert data["rows"][0]["label"] == "ClaimsProcessor"

    def test_empty_records_returns_empty_rows(self, client, mock_services):
        mock_services["cloudtrail"].get_activity_records_with_meta.return_value = ([], False, False)
        resp = client.get("/api/governance/spend-by-application")
        assert resp.status_code == 200
        data = resp.json()
        assert data["rows"] == []
        assert data["basis"] == "estimated"
