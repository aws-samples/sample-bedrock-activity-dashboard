"""Unit tests for the AgentCore Runtime cost endpoints (/api/agentcore-cost/*).

Mocks AgentCoreCostService / AgentCoreDiscoveryService directly (the routes
are thin wrappers), the same way other route tests mock their underlying
service, and asserts response shape, the "not configured" degraded state,
default-window behavior, and the admin-only /scan trigger.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.services.agentcore_cost_service import AgentRuntimeCost


@pytest.fixture
def mock_services():
    with patch("app.routes.agentcore_cost._cost_service") as mock_cost, patch(
        "app.routes.agentcore_cost._filter_service"
    ) as mock_filter, patch(
        "app.routes.agentcore_cost._discovery_service"
    ) as mock_discovery, patch(
        "app.routes.agentcore_cost.AgentCoreDiscoveryService"
    ) as mock_discovery_cls:
        mock_filter.get_default_date_range.return_value = (
            datetime(2024, 1, 10, tzinfo=timezone.utc),
            datetime(2024, 1, 17, tzinfo=timezone.utc),
        )
        mock_filter.validate_date_range.return_value = None
        mock_cost.is_configured.return_value = True
        mock_cost.get_all_runtime_cost.return_value = []
        mock_discovery.regions.return_value = ["us-west-2", "us-east-1"]
        mock_discovery_cls.last_scan_at.return_value = None
        yield {
            "cost": mock_cost,
            "filter": mock_filter,
            "discovery": mock_discovery,
            "discovery_cls": mock_discovery_cls,
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


class TestGetAgentCoreCost:
    def test_returns_expected_shape(self, client):
        resp = client.get("/api/agentcore-cost")
        assert resp.status_code == 200
        data = resp.json()
        assert "totalCost" in data
        assert "byAgent" in data
        assert "configured" in data
        assert "vcpuHourlyRate" in data
        assert "memoryGbHourlyRate" in data
        assert "scanRegions" in data
        assert "lastScanAt" in data

    def test_empty_when_no_agents_have_runtime(self, client, mock_services):
        mock_services["cost"].get_all_runtime_cost.return_value = []
        resp = client.get("/api/agentcore-cost")
        data = resp.json()
        assert data["totalCost"] == 0.0
        assert data["byAgent"] == []
        assert data["configured"] is True

    def test_returns_total_and_per_agent_breakdown(self, client, mock_services):
        mock_services["cost"].get_all_runtime_cost.return_value = [
            AgentRuntimeCost(
                agent_name="model-cost-optimizer",
                display_name="Model Cost Optimizer",
                region="us-west-2",
                vcpu_hours=0.01,
                gb_hours=0.5,
                cpu_cost=0.000895,
                memory_cost=0.004725,
                total_cost=0.00562,
            ),
            AgentRuntimeCost(
                agent_name="NimbusConcierge_NimbusConcierge",
                display_name="NimbusConcierge_NimbusConcierge",
                region="us-east-1",
                vcpu_hours=0.002,
                gb_hours=0.1,
                cpu_cost=0.000179,
                memory_cost=0.000945,
                total_cost=0.001124,
            ),
        ]
        resp = client.get("/api/agentcore-cost")
        data = resp.json()
        assert data["totalCost"] == pytest.approx(0.006744, abs=1e-6)
        assert len(data["byAgent"]) == 2
        assert data["byAgent"][0]["agentName"] == "model-cost-optimizer"
        assert data["byAgent"][0]["displayName"] == "Model Cost Optimizer"
        assert data["byAgent"][0]["region"] == "us-west-2"
        assert data["byAgent"][0]["totalCost"] == pytest.approx(0.00562, abs=1e-6)
        assert data["byAgent"][1]["agentName"] == "NimbusConcierge_NimbusConcierge"

    def test_not_configured_returns_zero_and_flag(self, client, mock_services):
        mock_services["cost"].is_configured.return_value = False
        resp = client.get("/api/agentcore-cost")
        data = resp.json()
        assert data["configured"] is False
        assert data["totalCost"] == 0.0
        assert data["byAgent"] == []
        # Service should not even be queried when tracking is disabled.
        mock_services["cost"].get_all_runtime_cost.assert_not_called()

    def test_default_date_range_used_when_no_params(self, client, mock_services):
        resp = client.get("/api/agentcore-cost")
        assert resp.status_code == 200
        mock_services["filter"].get_default_date_range.assert_called_once()

    def test_custom_date_range_is_parsed_and_passed_through(self, client, mock_services):
        resp = client.get(
            "/api/agentcore-cost",
            params={"start_date": "2024-02-01", "end_date": "2024-02-07"},
        )
        assert resp.status_code == 200
        mock_services["cost"].get_all_runtime_cost.assert_called_once()
        call_args = mock_services["cost"].get_all_runtime_cost.call_args
        start_arg, end_arg = call_args[0][0], call_args[0][1]
        assert start_arg.date().isoformat() == "2024-02-01"
        assert end_arg.date().isoformat() == "2024-02-07"

    def test_rejects_invalid_date_range(self, client, mock_services):
        mock_services["filter"].validate_date_range.side_effect = None
        from fastapi import HTTPException

        mock_services["filter"].validate_date_range.side_effect = HTTPException(
            status_code=422, detail="Invalid date range: start date must not be after end date."
        )
        resp = client.get(
            "/api/agentcore-cost",
            params={"start_date": "2024-02-10", "end_date": "2024-02-01"},
        )
        assert resp.status_code == 422

    def test_includes_scan_regions(self, client, mock_services):
        mock_services["discovery"].regions.return_value = ["us-west-2", "us-east-1"]
        resp = client.get("/api/agentcore-cost")
        assert resp.json()["scanRegions"] == ["us-west-2", "us-east-1"]

    def test_includes_last_scan_at_when_present(self, client, mock_services):
        mock_services["discovery_cls"].last_scan_at.return_value = datetime(
            2026, 8, 8, 10, 0, tzinfo=timezone.utc
        )
        resp = client.get("/api/agentcore-cost")
        assert resp.json()["lastScanAt"] is not None

    def test_last_scan_at_null_when_not_configured(self, client, mock_services):
        mock_services["cost"].is_configured.return_value = False
        resp = client.get("/api/agentcore-cost")
        assert resp.json()["lastScanAt"] is None
        mock_services["discovery_cls"].last_scan_at.assert_not_called()


class TestTriggerAgentCoreScan:
    def test_returns_202_with_run_id(self, client):
        resp = client.post("/api/agentcore-cost/scan")
        assert resp.status_code == 202
        assert "runId" in resp.json()
