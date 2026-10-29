"""Unit tests for activity API routes."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.services.cloudtrail_service import CloudTrailError


def _make_record(
    user_identity: str = "arn:aws:iam::123456789012:user/alice",
    model_id: str = "anthropic.claude-sonnet-4-20250514-v1:0",
    source_application: str = "Kiro",
    timestamp: str = "2024-01-15T10:30:00+00:00",
    input_tokens: int = 100,
    output_tokens: int = 50,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    cost_estimate: float | None = 0.05,
) -> dict:
    total = input_tokens + output_tokens + cache_read_tokens + cache_write_tokens
    return {
        "id": "test-id",
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
    }


SAMPLE_RECORDS = [
    _make_record(
        user_identity="user-a",
        model_id="model-1",
        source_application="Kiro",
        timestamp="2024-01-15T08:00:00+00:00",
        input_tokens=100,
        output_tokens=50,
        cost_estimate=0.05,
    ),
    _make_record(
        user_identity="user-b",
        model_id="model-2",
        source_application="Q Developer",
        timestamp="2024-01-16T10:00:00+00:00",
        input_tokens=200,
        output_tokens=100,
        cost_estimate=0.10,
    ),
]


@pytest.fixture
def mock_services():
    """Patch all service dependencies used by the activity routes."""
    with (
        patch("app.routes.activity._cloudtrail_service") as mock_ct,
        patch("app.routes.activity._cost_service") as mock_cost,
        patch("app.routes.activity._filter_service") as mock_filter,
    ):
        mock_ct.get_activity_records.return_value = list(SAMPLE_RECORDS)
        mock_ct.get_activity_records_with_truncation.return_value = (list(SAMPLE_RECORDS), False)
        mock_ct.get_activity_records_with_meta.return_value = (list(SAMPLE_RECORDS), False, False)
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


class TestGetActivity:
    def test_returns_aggregated_data(self, client):
        resp = client.get("/api/activity")
        assert resp.status_code == 200
        data = resp.json()
        assert "overview" in data
        assert "byUser" in data
        assert "byModel" in data
        assert "byDate" in data
        assert "bySource" in data
        assert "recordCount" in data

    def test_passes_query_params(self, client, mock_services):
        resp = client.get(
            "/api/activity",
            params={
                "start_date": "2024-01-15",
                "end_date": "2024-01-16",
                "user_identities": "user-a,user-b",
                "source_application": "Kiro",
            },
        )
        assert resp.status_code == 200
        mock_services["filter"].validate_date_range.assert_called_once()

    def test_cloudtrail_error_returns_502(self, client, mock_services):
        mock_services["cloudtrail"].get_activity_records_with_meta.side_effect = CloudTrailError(
            error_code="AccessDeniedException",
            message="Not authorized",
        )
        resp = client.get("/api/activity")
        assert resp.status_code == 502
        data = resp.json()
        assert data["error"]["code"] == "AccessDeniedException"
        assert data["error"]["message"] == "Not authorized"

    def test_empty_records_returns_200(self, client, mock_services):
        mock_services["cloudtrail"].get_activity_records_with_meta.return_value = ([], False, False)
        resp = client.get("/api/activity")
        assert resp.status_code == 200
        data = resp.json()
        assert data["recordCount"] == 0

    def test_ingestion_error_flag_surfaced_when_all_sources_fail(self, client, mock_services):
        # Real fetch reported a hard failure (all sources errored, no records).
        mock_services["cloudtrail"].get_activity_records_with_meta.return_value = ([], False, True)
        resp = client.get("/api/activity")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ingestionError"] is True

    def test_ingestion_error_flag_false_when_records_present(self, client):
        resp = client.get("/api/activity")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ingestionError"] is False


class TestGetOverview:
    def test_returns_overview_summary(self, client):
        resp = client.get("/api/activity/overview")
        assert resp.status_code == 200
        data = resp.json()
        assert "totalInvocations" in data
        assert "totalTokens" in data
        assert "activeUsers" in data
        assert "activeModels" in data

    def test_cloudtrail_error_returns_502(self, client, mock_services):
        mock_services["cloudtrail"].get_activity_records_with_meta.side_effect = CloudTrailError(
            error_code="ThrottlingException",
            message="Rate exceeded",
        )
        resp = client.get("/api/activity/overview")
        assert resp.status_code == 502

    def test_overview_surfaces_ingestion_error(self, client, mock_services):
        mock_services["cloudtrail"].get_activity_records_with_meta.return_value = ([], False, True)
        resp = client.get("/api/activity/overview")
        assert resp.status_code == 200
        assert resp.json()["ingestionError"] is True


class TestGetTokens:
    def test_returns_token_data(self, client):
        resp = client.get("/api/activity/tokens")
        assert resp.status_code == 200
        data = resp.json()
        assert "summary" in data
        assert "byUser" in data
        assert "dailyBreakdown" in data
        assert "totalTokens" in data["summary"]

    def test_cloudtrail_error_returns_502(self, client, mock_services):
        mock_services["cloudtrail"].get_activity_records_with_meta.side_effect = CloudTrailError(
            error_code="InternalError",
            message="Service error",
        )
        resp = client.get("/api/activity/tokens")
        assert resp.status_code == 502


class TestGetModels:
    def test_returns_model_data(self, client):
        resp = client.get("/api/activity/models")
        assert resp.status_code == 200
        data = resp.json()
        assert "models" in data
        assert "userModelMatrix" in data

    def test_cloudtrail_error_returns_502(self, client, mock_services):
        mock_services["cloudtrail"].get_activity_records_with_meta.side_effect = CloudTrailError(
            error_code="AccessDeniedException",
            message="Forbidden",
        )
        resp = client.get("/api/activity/models")
        assert resp.status_code == 502


class TestGetCosts:
    def test_returns_cost_data(self, client):
        resp = client.get("/api/activity/costs")
        assert resp.status_code == 200
        data = resp.json()
        assert "totalCost" in data
        assert "dailyCosts" in data
        assert "perUserCosts" in data
        assert "perModelCosts" in data

    def test_per_user_costs_have_percentages(self, client):
        resp = client.get("/api/activity/costs")
        data = resp.json()
        for entry in data["perUserCosts"]:
            assert "percentageOfTotal" in entry
            assert "costEstimate" in entry

    def test_per_model_costs_have_percentages(self, client):
        resp = client.get("/api/activity/costs")
        data = resp.json()
        for entry in data["perModelCosts"]:
            assert "percentageOfTotal" in entry
            assert "modelId" in entry

    def test_cloudtrail_error_returns_502(self, client, mock_services):
        mock_services["cloudtrail"].get_activity_records_with_meta.side_effect = CloudTrailError(
            error_code="ServiceUnavailable",
            message="Try again",
        )
        resp = client.get("/api/activity/costs")
        assert resp.status_code == 502


class TestGetSources:
    def test_returns_source_data(self, client):
        resp = client.get("/api/activity/sources")
        assert resp.status_code == 200
        data = resp.json()
        assert "sources" in data

    def test_cloudtrail_error_returns_502(self, client, mock_services):
        mock_services["cloudtrail"].get_activity_records_with_meta.side_effect = CloudTrailError(
            error_code="InvalidParameterException",
            message="Bad param",
        )
        resp = client.get("/api/activity/sources")
        assert resp.status_code == 502


class TestGetCategories:
    def test_returns_breakdown_shape(self, client):
        resp = client.get("/api/activity/categories")
        assert resp.status_code == 200
        data = resp.json()
        assert data["basis"] == "estimated"
        assert "byCategory" in data
        assert "otherCategoryCost" in data
        assert "otherCategoryInvocationCount" in data
        assert "byModel" in data
        assert "otherModelCost" in data
        assert "otherModelInvocationCount" in data

    def test_classifies_sql_prompt_text(self, client, mock_services):
        records = [
            _make_record(model_id="model-1", cost_estimate=2.0),
        ]
        records[0]["promptText"] = "SELECT * FROM orders WHERE status = 'open'"
        mock_services["cloudtrail"].get_activity_records_with_meta.return_value = (
            records, False, False,
        )
        resp = client.get("/api/activity/categories")
        assert resp.status_code == 200
        data = resp.json()
        categories = {c["category"] for c in data["byCategory"]}
        assert "SQL / Database" in categories

    def test_respects_top_n_query_params(self, client, mock_services):
        records = []
        for i, text in enumerate([
            "SELECT * FROM a",
            "write a function to sort",
            "translate this into spanish",
        ]):
            r = _make_record(model_id=f"model-{i}", cost_estimate=float(10 - i))
            r["promptText"] = text
            records.append(r)
        mock_services["cloudtrail"].get_activity_records_with_meta.return_value = (
            records, False, False,
        )
        resp = client.get("/api/activity/categories", params={"top_categories": 1})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["byCategory"]) == 1
        assert data["otherCategoryCost"] > 0

    def test_cloudtrail_error_returns_502(self, client, mock_services):
        mock_services["cloudtrail"].get_activity_records_with_meta.side_effect = CloudTrailError(
            error_code="InvalidParameterException",
            message="Bad param",
        )
        resp = client.get("/api/activity/categories")
        assert resp.status_code == 502

    def test_empty_records_returns_empty_breakdown(self, client, mock_services):
        mock_services["cloudtrail"].get_activity_records_with_meta.return_value = ([], False, False)
        resp = client.get("/api/activity/categories")
        assert resp.status_code == 200
        data = resp.json()
        assert data["byCategory"] == []
        assert data["byModel"] == []


class TestDateValidation:
    def test_invalid_date_range_returns_422(self, client, mock_services):
        from fastapi import HTTPException

        mock_services["filter"].validate_date_range.side_effect = HTTPException(
            status_code=422,
            detail="Invalid date range: start date must not be after end date.",
        )
        resp = client.get(
            "/api/activity",
            params={"start_date": "2024-02-01", "end_date": "2024-01-01"},
        )
        assert resp.status_code == 422


class TestTeamFiltering:
    def test_team_filter_calls_filter_by_team(self, client, mock_services):
        resp = client.get("/api/activity", params={"team_id": "team-123"})
        assert resp.status_code == 200
        mock_services["filter"].filter_by_team.assert_called_once()
