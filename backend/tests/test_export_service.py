"""Unit tests for ExportService and the /api/activity/export endpoint."""

import csv
import io
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.services.export_service import ExportService


# ------------------------------------------------------------------
# ExportService unit tests
# ------------------------------------------------------------------


class TestExportServiceGenerateCSV:
    def _parse_csv(self, csv_str: str) -> list[list[str]]:
        return list(csv.reader(io.StringIO(csv_str)))

    def test_token_usage_section(self):
        records = [
            {
                "userName": "alice",
                "inputTokens": 100,
                "outputTokens": 50,
                "cacheReadTokens": 10,
                "cacheWriteTokens": 5,
                "totalTokens": 165,
                "costEstimate": 0.05,
            }
        ]
        filters = {
            "start_date": datetime(2024, 1, 1, tzinfo=timezone.utc),
            "end_date": datetime(2024, 1, 7, tzinfo=timezone.utc),
            "user_identities": [],
            "team_id": None,
            "source_application": None,
        }
        result = ExportService.generate_csv("token-usage", records, filters)
        rows = self._parse_csv(result)

        # Metadata rows
        assert rows[0][0] == "# Date Range"
        assert "2024-01-01" in rows[0][1]
        assert rows[1][0] == "# Section"
        # Blank separator
        assert rows[2] == []
        # Column header
        assert rows[3][0] == "userName"
        assert "totalTokens" in rows[3]
        # Data row
        assert rows[4][0] == "alice"
        assert rows[4][1] == "100"

    def test_model_usage_section(self):
        records = [
            {
                "modelId": "claude-sonnet",
                "invocationCount": 10,
                "inputTokens": 500,
                "outputTokens": 200,
                "totalTokens": 700,
                "costEstimate": 0.25,
                "percentageOfTotal": 80.0,
            }
        ]
        filters = {
            "start_date": "2024-01-01",
            "end_date": "2024-01-07",
            "user_identities": [],
            "team_id": None,
            "source_application": None,
        }
        result = ExportService.generate_csv("model-usage", records, filters)
        rows = self._parse_csv(result)

        # Find the header row (after metadata)
        header_idx = next(i for i, r in enumerate(rows) if r and r[0] == "modelId")
        assert "percentageOfTotal" in rows[header_idx]
        assert rows[header_idx + 1][0] == "claude-sonnet"

    def test_prompt_history_section(self):
        records = [
            {
                "timestamp": "2024-01-15T10:00:00+00:00",
                "userName": "bob",
                "modelId": "claude-haiku",
                "sourceApplication": "Kiro",
                "inputTokens": 50,
                "outputTokens": 25,
                "costEstimate": 0.01,
            }
        ]
        filters = {
            "start_date": datetime(2024, 1, 10, tzinfo=timezone.utc),
            "end_date": datetime(2024, 1, 17, tzinfo=timezone.utc),
            "user_identities": ["bob"],
            "team_id": None,
            "source_application": "Kiro",
        }
        result = ExportService.generate_csv("prompt-history", records, filters)
        rows = self._parse_csv(result)

        # Check user filter metadata
        user_row = next(r for r in rows if r[0] == "# Users")
        assert "bob" in user_row[1]

        # Check source app metadata
        src_row = next(r for r in rows if r[0] == "# Source Application")
        assert src_row[1] == "Kiro"

        header_idx = next(i for i, r in enumerate(rows) if r and r[0] == "timestamp")
        assert rows[header_idx + 1][0] == "2024-01-15T10:00:00+00:00"

    def test_cost_analysis_section(self):
        records = [
            {
                "userName": "alice",
                "modelId": "",
                "costEstimate": 1.50,
                "percentageOfTotal": 60.0,
            },
            {
                "userName": "",
                "modelId": "claude-sonnet",
                "costEstimate": 2.50,
                "percentageOfTotal": 100.0,
            },
        ]
        filters = {
            "start_date": "2024-01-01",
            "end_date": "2024-01-31",
            "user_identities": [],
            "team_id": None,
            "source_application": None,
        }
        result = ExportService.generate_csv("cost-analysis", records, filters)
        rows = self._parse_csv(result)

        header_idx = next(i for i, r in enumerate(rows) if r and r[0] == "userName")
        assert rows[header_idx + 1][0] == "alice"
        assert rows[header_idx + 2][1] == "claude-sonnet"

    def test_metadata_includes_team_filter(self):
        filters = {
            "start_date": "2024-01-01",
            "end_date": "2024-01-07",
            "user_identities": [],
            "team_id": "team-abc",
            "source_application": None,
        }
        result = ExportService.generate_csv("token-usage", [], filters)
        rows = self._parse_csv(result)
        team_row = next(r for r in rows if r[0] == "# Team")
        assert team_row[1] == "team-abc"

    def test_empty_records_produces_header_only(self):
        filters = {
            "start_date": "2024-01-01",
            "end_date": "2024-01-07",
            "user_identities": [],
            "team_id": None,
            "source_application": None,
        }
        result = ExportService.generate_csv("token-usage", [], filters)
        rows = self._parse_csv(result)
        # Should have metadata + blank + header, no data rows
        header_idx = next(i for i, r in enumerate(rows) if r and r[0] == "userName")
        # No rows after header (last row is the header)
        data_rows = [r for r in rows[header_idx + 1:] if any(r)]
        assert len(data_rows) == 0


# ------------------------------------------------------------------
# Export endpoint integration tests
# ------------------------------------------------------------------


def _make_record(
    user_identity: str = "user-a",
    model_id: str = "model-1",
    source_application: str = "Kiro",
    timestamp: str = "2024-01-15T08:00:00+00:00",
    input_tokens: int = 100,
    output_tokens: int = 50,
    cost_estimate: float | None = 0.05,
) -> dict:
    total = input_tokens + output_tokens
    return {
        "id": "test-id",
        "timestamp": timestamp,
        "userIdentity": user_identity,
        "userName": user_identity,
        "modelId": model_id,
        "sourceApplication": source_application,
        "sourceIp": "10.0.0.1",
        "inputTokens": input_tokens,
        "outputTokens": output_tokens,
        "cacheReadTokens": 0,
        "cacheWriteTokens": 0,
        "totalTokens": total,
        "costEstimate": cost_estimate,
    }


SAMPLE_RECORDS = [
    _make_record(user_identity="user-a", model_id="model-1", cost_estimate=0.05),
    _make_record(
        user_identity="user-b",
        model_id="model-2",
        timestamp="2024-01-16T10:00:00+00:00",
        input_tokens=200,
        output_tokens=100,
        cost_estimate=0.10,
    ),
]


@pytest.fixture
def mock_services():
    with (
        patch("app.routes.activity._cloudtrail_service") as mock_ct,
        patch("app.routes.activity._cost_service") as mock_cost,
        patch("app.routes.activity._filter_service") as mock_filter,
    ):
        mock_ct.get_activity_records.return_value = list(SAMPLE_RECORDS)
        mock_ct.get_activity_records_with_truncation.return_value = (list(SAMPLE_RECORDS), False)
        # The export route sources records via get_activity_records_with_meta,
        # which returns (records, truncated, ingestion_error).
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
    from main import app
    from app.database import get_db

    mock_db = MagicMock()
    app.dependency_overrides[get_db] = lambda: mock_db

    with TestClient(app) as c:
        yield c

    app.dependency_overrides.clear()


class TestExportEndpoint:
    def test_export_token_usage_returns_csv(self, client):
        resp = client.get("/api/activity/export", params={"section": "token-usage"})
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/csv")
        assert "Content-Disposition" in resp.headers
        assert "token-usage-export.csv" in resp.headers["Content-Disposition"]

        rows = list(csv.reader(io.StringIO(resp.text)))
        # Should have metadata, blank, header, and data rows
        header_idx = next(i for i, r in enumerate(rows) if r and r[0] == "userName")
        assert header_idx >= 0
        # At least one data row (aggregated by user)
        data_rows = [r for r in rows[header_idx + 1:] if any(r)]
        assert len(data_rows) >= 1

    def test_export_model_usage_returns_csv(self, client):
        resp = client.get("/api/activity/export", params={"section": "model-usage"})
        assert resp.status_code == 200
        rows = list(csv.reader(io.StringIO(resp.text)))
        header_idx = next(i for i, r in enumerate(rows) if r and r[0] == "modelId")
        data_rows = [r for r in rows[header_idx + 1:] if any(r)]
        assert len(data_rows) >= 1

    def test_export_cost_analysis_returns_csv(self, client):
        resp = client.get("/api/activity/export", params={"section": "cost-analysis"})
        assert resp.status_code == 200
        rows = list(csv.reader(io.StringIO(resp.text)))
        header_idx = next(i for i, r in enumerate(rows) if r and r[0] == "userName")
        data_rows = [r for r in rows[header_idx + 1:] if any(r)]
        # Should have per-user + per-model rows
        assert len(data_rows) >= 2

    def test_export_prompt_history_returns_csv(self, client):
        resp = client.get("/api/activity/export", params={"section": "prompt-history"})
        assert resp.status_code == 200
        rows = list(csv.reader(io.StringIO(resp.text)))
        header_idx = next(i for i, r in enumerate(rows) if r and r[0] == "timestamp")
        data_rows = [r for r in rows[header_idx + 1:] if any(r)]
        assert len(data_rows) == 2

    def test_export_invalid_section_returns_422(self, client):
        resp = client.get("/api/activity/export", params={"section": "invalid"})
        assert resp.status_code == 422

    def test_export_missing_section_returns_422(self, client):
        resp = client.get("/api/activity/export")
        assert resp.status_code == 422

    def test_export_with_filters(self, client):
        resp = client.get(
            "/api/activity/export",
            params={
                "section": "token-usage",
                "start_date": "2024-01-15",
                "end_date": "2024-01-16",
                "user_identities": "user-a",
            },
        )
        assert resp.status_code == 200
        assert "# Date Range" in resp.text

    def test_export_cloudtrail_error_returns_502(self, client, mock_services):
        from app.services.cloudtrail_service import CloudTrailError

        # The export route sources records via get_activity_records_with_meta.
        mock_services["cloudtrail"].get_activity_records_with_meta.side_effect = CloudTrailError(
            error_code="AccessDeniedException",
            message="Not authorized",
        )
        resp = client.get("/api/activity/export", params={"section": "token-usage"})
        assert resp.status_code == 502
