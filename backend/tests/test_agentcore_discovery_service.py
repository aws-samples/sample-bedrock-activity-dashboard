"""Unit tests for AgentCoreDiscoveryService (account-wide AgentCore Runtime discovery)."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import StaticPool, create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.services.agentcore_discovery_service import (
    AgentCoreDiscoveryService,
    DiscoveredRuntimeCost,
    RegionScanResult,
    parse_regions,
)
from app.v2.models import V2AgentConfig, V2DiscoveredAgentcoreRuntime


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        bind=engine, tables=[V2DiscoveredAgentcoreRuntime.__table__, V2AgentConfig.__table__]
    )
    session_local = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = session_local()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def service():
    return AgentCoreDiscoveryService()


def _runtime(arn: str, rid: str, name: str, status: str = "READY") -> dict:
    return {
        "agentRuntimeArn": arn,
        "agentRuntimeId": rid,
        "agentRuntimeName": name,
        "status": status,
    }


class TestParseRegions:
    def test_parses_comma_separated_list(self):
        assert parse_regions("us-west-2,us-east-1") == ["us-west-2", "us-east-1"]

    def test_trims_whitespace(self):
        assert parse_regions(" us-west-2 , us-east-1 ") == ["us-west-2", "us-east-1"]

    def test_drops_blank_entries(self):
        assert parse_regions("us-west-2,,us-east-1,") == ["us-west-2", "us-east-1"]

    def test_empty_string_returns_empty_list(self):
        assert parse_regions("") == []


class TestIsConfigured:
    def test_true_by_default(self, service):
        assert service.is_configured() is True

    def test_false_when_disabled(self, service):
        with patch.object(service, "_settings") as mock_settings:
            mock_settings.agentcore_discovery_enabled = False
            mock_settings.agentcore_discovery_regions = "us-west-2"
            assert service.is_configured() is False

    def test_false_when_no_regions(self, service):
        with patch.object(service, "_settings") as mock_settings:
            mock_settings.agentcore_discovery_enabled = True
            mock_settings.agentcore_discovery_regions = ""
            assert service.is_configured() is False


class TestDiscover:
    def test_skipped_when_disabled(self, service, db):
        with patch.object(service, "_settings") as mock_settings:
            mock_settings.agentcore_discovery_enabled = False
            result = service.discover(db)
        assert result.status == "skipped"

    def test_skipped_when_no_regions(self, service, db):
        with patch.object(service, "_settings") as mock_settings:
            mock_settings.agentcore_discovery_enabled = True
            mock_settings.agentcore_discovery_regions = ""
            result = service.discover(db)
        assert result.status == "skipped"

    def test_success_scans_all_regions_and_upserts(self, service, db):
        with patch.object(service, "_settings") as mock_settings:
            mock_settings.agentcore_discovery_enabled = True
            mock_settings.agentcore_discovery_regions = "us-west-2,us-east-1"

            mock_client_west = MagicMock()
            mock_client_west.list_agent_runtimes.return_value = {
                "agentRuntimes": [_runtime("arn:aws:bedrock-agentcore:us-west-2:1:runtime/a", "a", "AgentA")]
            }
            mock_client_east = MagicMock()
            mock_client_east.list_agent_runtimes.return_value = {
                "agentRuntimes": [_runtime("arn:aws:bedrock-agentcore:us-east-1:1:runtime/b", "b", "AgentB")]
            }

            def _client_for(region):
                return mock_client_west if region == "us-west-2" else mock_client_east

            with patch.object(service, "_client_for", side_effect=_client_for):
                result = service.discover(db)

        assert result.status == "success"
        assert result.total_runtimes_found == 2
        assert {r.region for r in result.region_results} == {"us-west-2", "us-east-1"}

        rows = AgentCoreDiscoveryService.list_discovered(db)
        assert len(rows) == 2
        assert {r.agent_runtime_name for r in rows} == {"AgentA", "AgentB"}

    def test_paginates_within_a_region(self, service, db):
        with patch.object(service, "_settings") as mock_settings:
            mock_settings.agentcore_discovery_enabled = True
            mock_settings.agentcore_discovery_regions = "us-west-2"

            mock_client = MagicMock()
            mock_client.list_agent_runtimes.side_effect = [
                {
                    "agentRuntimes": [_runtime("arn:.../a", "a", "AgentA")],
                    "nextToken": "page2",
                },
                {"agentRuntimes": [_runtime("arn:.../b", "b", "AgentB")]},
            ]
            with patch.object(service, "_client_for", return_value=mock_client):
                result = service.discover(db)

        assert result.total_runtimes_found == 2
        assert mock_client.list_agent_runtimes.call_count == 2

    def test_one_region_failing_yields_partial_failure_but_still_upserts_others(self, service, db):
        with patch.object(service, "_settings") as mock_settings:
            mock_settings.agentcore_discovery_enabled = True
            mock_settings.agentcore_discovery_regions = "us-west-2,us-east-1"

            mock_client_ok = MagicMock()
            mock_client_ok.list_agent_runtimes.return_value = {
                "agentRuntimes": [_runtime("arn:.../ok", "ok", "AgentOk")]
            }
            mock_client_bad = MagicMock()
            mock_client_bad.list_agent_runtimes.side_effect = RuntimeError("boom")

            def _client_for(region):
                return mock_client_ok if region == "us-west-2" else mock_client_bad

            with patch.object(service, "_client_for", side_effect=_client_for):
                result = service.discover(db)

        assert result.status == "partial_failure"
        assert result.total_runtimes_found == 1
        statuses = {r.region: r.status for r in result.region_results}
        assert statuses == {"us-west-2": "success", "us-east-1": "failed"}
        # The successful region's row must still be persisted despite the
        # other region's failure and rollback.
        rows = AgentCoreDiscoveryService.list_discovered(db)
        assert len(rows) == 1
        assert rows[0].agent_runtime_name == "AgentOk"

    def test_all_regions_failing_yields_failed_status(self, service, db):
        with patch.object(service, "_settings") as mock_settings:
            mock_settings.agentcore_discovery_enabled = True
            mock_settings.agentcore_discovery_regions = "us-west-2"

            mock_client = MagicMock()
            mock_client.list_agent_runtimes.side_effect = RuntimeError("boom")
            with patch.object(service, "_client_for", return_value=mock_client):
                result = service.discover(db)

        assert result.status == "failed"
        assert result.total_runtimes_found == 0

    def test_rescanning_updates_last_seen_but_keeps_first_seen(self, service, db):
        with patch.object(service, "_settings") as mock_settings:
            mock_settings.agentcore_discovery_enabled = True
            mock_settings.agentcore_discovery_regions = "us-west-2"

            mock_client = MagicMock()
            mock_client.list_agent_runtimes.return_value = {
                "agentRuntimes": [_runtime("arn:.../a", "a", "AgentA", status="READY")]
            }
            with patch.object(service, "_client_for", return_value=mock_client):
                service.discover(db)
                first_row = AgentCoreDiscoveryService.list_discovered(db)[0]
                first_seen = first_row.first_seen_at

                # Second scan, same runtime now shows a different status.
                mock_client.list_agent_runtimes.return_value = {
                    "agentRuntimes": [_runtime("arn:.../a", "a", "AgentA", status="UPDATING")]
                }
                service.discover(db)

        rows = AgentCoreDiscoveryService.list_discovered(db)
        assert len(rows) == 1
        assert rows[0].first_seen_at == first_seen
        assert rows[0].status == "UPDATING"

    def test_skips_runtime_with_no_arn(self, service, db):
        with patch.object(service, "_settings") as mock_settings:
            mock_settings.agentcore_discovery_enabled = True
            mock_settings.agentcore_discovery_regions = "us-west-2"

            mock_client = MagicMock()
            mock_client.list_agent_runtimes.return_value = {
                "agentRuntimes": [{"agentRuntimeId": "a", "agentRuntimeName": "NoArn"}]
            }
            with patch.object(service, "_client_for", return_value=mock_client):
                result = service.discover(db)

        assert result.status == "success"
        assert result.total_runtimes_found == 1
        assert AgentCoreDiscoveryService.list_discovered(db) == []


class TestLastScanAt:
    def test_none_when_no_scans_recorded(self, db):
        assert AgentCoreDiscoveryService.last_scan_at(db) is None

    def test_returns_most_recent_last_seen_at(self, service, db):
        with patch.object(service, "_settings") as mock_settings:
            mock_settings.agentcore_discovery_enabled = True
            mock_settings.agentcore_discovery_regions = "us-west-2"

            mock_client = MagicMock()
            mock_client.list_agent_runtimes.return_value = {
                "agentRuntimes": [_runtime("arn:.../a", "a", "AgentA")]
            }
            with patch.object(service, "_client_for", return_value=mock_client):
                service.discover(db)

        last_scan = AgentCoreDiscoveryService.last_scan_at(db)
        assert last_scan is not None


class TestGetCosts:
    def test_returns_cost_grouped_by_region(self, service):
        mock_cw_west = MagicMock()
        mock_cw_west.get_metric_data.return_value = {
            "MetricDataResults": [
                {"Id": "cpu0", "Values": [0.01]},
                {"Id": "mem0", "Values": [0.5]},
            ]
        }
        mock_cw_east = MagicMock()
        mock_cw_east.get_metric_data.return_value = {
            "MetricDataResults": [
                {"Id": "cpu0", "Values": [0.02]},
                {"Id": "mem0", "Values": [1.0]},
            ]
        }

        def _cw_for(region):
            return mock_cw_west if region == "us-west-2" else mock_cw_east

        with patch.object(service, "_cloudwatch_client_for", side_effect=_cw_for), patch(
            "app.services.agentcore_discovery_service.settings"
        ) as mock_settings:
            mock_settings.agentcore_vcpu_hourly_rate = 0.0895
            mock_settings.agentcore_memory_gb_hourly_rate = 0.00945
            start = datetime(2024, 1, 1, tzinfo=timezone.utc)
            end = datetime(2024, 1, 2, tzinfo=timezone.utc)
            costs = service.get_costs(
                [("us-west-2", "arn:.../west-agent"), ("us-east-1", "arn:.../east-agent")], start, end
            )

        assert set(costs.keys()) == {"arn:.../west-agent", "arn:.../east-agent"}
        assert costs["arn:.../west-agent"].vcpu_hours == pytest.approx(0.01)
        assert costs["arn:.../east-agent"].vcpu_hours == pytest.approx(0.02)

    def test_region_failure_omits_its_runtimes_but_not_others(self, service):
        mock_cw_west = MagicMock()
        mock_cw_west.get_metric_data.return_value = {
            "MetricDataResults": [
                {"Id": "cpu0", "Values": [0.01]},
                {"Id": "mem0", "Values": [0.5]},
            ]
        }
        mock_cw_east = MagicMock()
        mock_cw_east.get_metric_data.side_effect = RuntimeError("boom")

        def _cw_for(region):
            return mock_cw_west if region == "us-west-2" else mock_cw_east

        with patch.object(service, "_cloudwatch_client_for", side_effect=_cw_for), patch(
            "app.services.agentcore_discovery_service.settings"
        ) as mock_settings:
            mock_settings.agentcore_vcpu_hourly_rate = 0.0895
            mock_settings.agentcore_memory_gb_hourly_rate = 0.00945
            start = datetime(2024, 1, 1, tzinfo=timezone.utc)
            end = datetime(2024, 1, 2, tzinfo=timezone.utc)
            costs = service.get_costs(
                [("us-west-2", "arn:.../west-agent"), ("us-east-1", "arn:.../east-agent")], start, end
            )

        assert set(costs.keys()) == {"arn:.../west-agent"}

    def test_empty_runtimes_returns_empty_dict(self, service):
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end = datetime(2024, 1, 2, tzinfo=timezone.utc)
        assert service.get_costs([], start, end) == {}


class TestDataclasses:
    def test_discovered_runtime_cost_fields(self):
        cost = DiscoveredRuntimeCost(agent_runtime_arn="arn:.../a", vcpu_hours=0.1, gb_hours=1.0, total_cost=0.05)
        assert cost.agent_runtime_arn == "arn:.../a"

    def test_region_scan_result_defaults(self):
        result = RegionScanResult(region="us-west-2", status="success")
        assert result.runtimes_found == 0
        assert result.error_detail is None
