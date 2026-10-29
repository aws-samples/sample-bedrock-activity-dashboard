"""Unit tests for AgentCoreCostService (live CloudWatch-based AgentCore Runtime cost)."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import StaticPool, create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.services.agentcore_cost_service import (
    AgentCoreCostService,
    AgentRuntimeCost,
    _window_period_seconds,
)
from app.v2.models import V2AgentConfig, V2DiscoveredAgentcoreRuntime


def _make_config(agent_name, display_name, agentcore_agent_id, is_agentic=True):
    cfg = MagicMock()
    cfg.agent_name = agent_name
    cfg.display_name = display_name
    cfg.agentcore_agent_id = agentcore_agent_id
    cfg.is_agentic = is_agentic
    return cfg


@pytest.fixture
def db():
    return MagicMock()


@pytest.fixture
def service():
    svc = AgentCoreCostService()
    # Avoid any real AWS calls for account id resolution in unit tests.
    svc._AgentCoreCostService__account_id = "123456789012"
    return svc


class TestWindowPeriodSeconds:
    def test_rounds_up_to_next_minute(self):
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end = datetime(2024, 1, 1, 0, 0, 30, tzinfo=timezone.utc)
        assert _window_period_seconds(start, end) == 60

    def test_exact_minute_boundary(self):
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end = datetime(2024, 1, 1, 0, 2, tzinfo=timezone.utc)
        assert _window_period_seconds(start, end) == 120

    def test_multi_day_window(self):
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end = datetime(2024, 1, 8, tzinfo=timezone.utc)
        assert _window_period_seconds(start, end) == 7 * 24 * 3600


class TestIsConfigured:
    def test_true_by_default(self, service):
        assert service.is_configured() is True

    def test_false_when_tracking_disabled(self, service):
        with patch("app.services.agentcore_cost_service.settings") as mock_settings:
            mock_settings.agentcore_cost_tracking_enabled = False
            assert service.is_configured() is False


class TestResolveArn:
    def test_passes_through_existing_arn(self, service):
        arn = "arn:aws:bedrock-agentcore:us-west-2:999999999999:runtime/foo-abc"
        assert service._resolve_arn(arn) == arn

    def test_builds_arn_from_bare_id(self, service):
        service._region = "us-west-2"
        result = service._resolve_arn("modelcostoptimizer_modelcostoptimizer-CijNhH6JHL")
        assert result == (
            "arn:aws:bedrock-agentcore:us-west-2:123456789012"
            ":runtime/modelcostoptimizer_modelcostoptimizer-CijNhH6JHL"
        )


class TestGetRuntimeAgentConfigs:
    def test_filters_to_agentic_agents_with_runtime_deployed(self, db):
        # The DB-level filter is mocked here (real filtering is exercised by
        # SQLAlchemy at runtime); this test only checks the query is built
        # with both required predicates in place, not just agentcore_agent_id.
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [
            _make_config("weekly-digest-orchestrator", "Weekly Digest Orchestrator", "rt-1"),
        ]
        result = AgentCoreCostService.get_runtime_agent_configs(db)
        assert len(result) == 1
        assert result[0].agent_name == "weekly-digest-orchestrator"
        # filter() called with both agentcore_agent_id.isnot(None) and is_agentic.is_(True).
        filter_call_args = db.query.return_value.filter.call_args[0]
        assert len(filter_call_args) == 2


class TestGetRuntimeAgentConfigsRealDb:
    """SQL-level proof (not mocked) that deterministic agents are excluded.

    Regression test for the bug where deterministic (⚙️) agents — which
    always resolve via AgentExecutionService._try_direct_execution and never
    invoke AgentCore Runtime — were showing up in the AgentCore Cost tab's
    per-agent drill-down just because a Runtime package happened to be
    deployed for them (agentcore_agent_id set). Only agentic (🤖) agents
    with a deployed Runtime should ever be eligible.
    """

    @pytest.fixture
    def real_db(self):
        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=engine, tables=[V2AgentConfig.__table__])
        session_local = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        session = session_local()
        try:
            yield session
        finally:
            session.close()

    def test_excludes_deterministic_agent_even_with_runtime_deployed(self, real_db):
        real_db.add(
            V2AgentConfig(
                agent_name="model-cost-optimizer",
                display_name="Model Cost Optimizer",
                category="Security & Guardrails",
                model_id="us.anthropic.claude-haiku-4-5-20251001-v1:0",
                agentcore_agent_id="modelcostoptimizer_modelcostoptimizer-CijNhH6JHL",
                is_agentic=False,
            )
        )
        real_db.add(
            V2AgentConfig(
                agent_name="weekly-digest-orchestrator",
                display_name="Weekly Digest Orchestrator",
                category="Orchestration",
                model_id="us.anthropic.claude-haiku-4-5-20251001-v1:0",
                agentcore_agent_id="weeklydigestorch_weeklydigestorch-E47C7wCD8P",
                is_agentic=True,
            )
        )
        real_db.commit()

        result = AgentCoreCostService.get_runtime_agent_configs(real_db)

        assert [r.agent_name for r in result] == ["weekly-digest-orchestrator"]

    def test_excludes_agentic_agent_without_deployed_runtime(self, real_db):
        real_db.add(
            V2AgentConfig(
                agent_name="chargeback-reconciliation",
                display_name="Chargeback Reconciliation",
                category="Compliance & Operations",
                model_id="us.anthropic.claude-haiku-4-5-20251001-v1:0",
                agentcore_agent_id=None,
                is_agentic=True,
            )
        )
        real_db.commit()

        result = AgentCoreCostService.get_runtime_agent_configs(real_db)

        assert result == []


class TestGetRuntimeCost:
    def test_returns_empty_when_not_configured(self, service, db):
        with patch("app.services.agentcore_cost_service.settings") as mock_settings:
            mock_settings.agentcore_cost_tracking_enabled = False
            start = datetime(2024, 1, 1, tzinfo=timezone.utc)
            end = datetime(2024, 1, 2, tzinfo=timezone.utc)
            result = service.get_runtime_cost(start, end, db)
        assert result == []

    def test_returns_empty_when_no_runtime_agents(self, service, db):
        with patch.object(
            AgentCoreCostService, "get_runtime_agent_configs", return_value=[]
        ):
            start = datetime(2024, 1, 1, tzinfo=timezone.utc)
            end = datetime(2024, 1, 2, tzinfo=timezone.utc)
            result = service.get_runtime_cost(start, end, db)
        assert result == []

    def test_computes_cost_from_cloudwatch_metrics(self, service, db):
        configs = [
            _make_config("model-cost-optimizer", "Model Cost Optimizer", "rt-mco"),
            _make_config("cost-anomaly-alerter", "Cost Anomaly Alerter", "rt-cost"),
        ]
        mock_client = MagicMock()
        mock_client.get_metric_data.return_value = {
            "MetricDataResults": [
                {"Id": "cpu0", "Values": [0.01]},
                {"Id": "mem0", "Values": [0.5]},
                {"Id": "cpu1", "Values": [0.002]},
                {"Id": "mem1", "Values": [0.1]},
            ]
        }
        with patch.object(
            AgentCoreCostService, "get_runtime_agent_configs", return_value=configs
        ), patch(
            "app.services.agentcore_cost_service.settings"
        ) as mock_settings:
            mock_settings.agentcore_cost_tracking_enabled = True
            mock_settings.agentcore_vcpu_hourly_rate = 0.0895
            mock_settings.agentcore_memory_gb_hourly_rate = 0.00945
            service.__dict__["_AgentCoreCostService__client"] = mock_client
            start = datetime(2024, 1, 1, tzinfo=timezone.utc)
            end = datetime(2024, 1, 2, tzinfo=timezone.utc)
            result = service.get_runtime_cost(start, end, db)

        assert len(result) == 2
        # Sorted descending by total_cost; model-cost-optimizer's usage is larger.
        assert result[0].agent_name == "model-cost-optimizer"
        assert result[0].vcpu_hours == pytest.approx(0.01)
        assert result[0].gb_hours == pytest.approx(0.5)
        expected_cpu_cost = 0.01 * 0.0895
        expected_mem_cost = 0.5 * 0.00945
        assert result[0].cpu_cost == pytest.approx(expected_cpu_cost, rel=1e-4)
        assert result[0].memory_cost == pytest.approx(expected_mem_cost, rel=1e-4)
        assert result[0].total_cost == pytest.approx(expected_cpu_cost + expected_mem_cost, rel=1e-4)
        assert result[1].agent_name == "cost-anomaly-alerter"

    def test_agents_with_no_metric_data_report_zero_cost(self, service, db):
        configs = [_make_config("onboarding-assistant", "Onboarding Assistant", "rt-onb")]
        mock_client = MagicMock()
        # No results at all for this agent (e.g. it never ran in the window).
        mock_client.get_metric_data.return_value = {"MetricDataResults": []}
        with patch.object(
            AgentCoreCostService, "get_runtime_agent_configs", return_value=configs
        ), patch(
            "app.services.agentcore_cost_service.settings"
        ) as mock_settings:
            mock_settings.agentcore_cost_tracking_enabled = True
            mock_settings.agentcore_vcpu_hourly_rate = 0.0895
            mock_settings.agentcore_memory_gb_hourly_rate = 0.00945
            service.__dict__["_AgentCoreCostService__client"] = mock_client
            start = datetime(2024, 1, 1, tzinfo=timezone.utc)
            end = datetime(2024, 1, 2, tzinfo=timezone.utc)
            result = service.get_runtime_cost(start, end, db)

        assert len(result) == 1
        assert result[0].vcpu_hours == 0.0
        assert result[0].gb_hours == 0.0
        assert result[0].total_cost == 0.0

    def test_cloudwatch_error_returns_empty_list_not_raise(self, service, db):
        from botocore.exceptions import ClientError

        configs = [_make_config("model-cost-optimizer", "Model Cost Optimizer", "rt-mco")]
        mock_client = MagicMock()
        mock_client.get_metric_data.side_effect = ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "nope"}}, "GetMetricData"
        )
        with patch.object(
            AgentCoreCostService, "get_runtime_agent_configs", return_value=configs
        ):
            service.__dict__["_AgentCoreCostService__client"] = mock_client
            start = datetime(2024, 1, 1, tzinfo=timezone.utc)
            end = datetime(2024, 1, 2, tzinfo=timezone.utc)
            result = service.get_runtime_cost(start, end, db)

        assert result == []

    def test_unexpected_error_returns_empty_list_not_raise(self, service, db):
        configs = [_make_config("model-cost-optimizer", "Model Cost Optimizer", "rt-mco")]
        mock_client = MagicMock()
        mock_client.get_metric_data.side_effect = RuntimeError("boom")
        with patch.object(
            AgentCoreCostService, "get_runtime_agent_configs", return_value=configs
        ):
            service.__dict__["_AgentCoreCostService__client"] = mock_client
            start = datetime(2024, 1, 1, tzinfo=timezone.utc)
            end = datetime(2024, 1, 2, tzinfo=timezone.utc)
            result = service.get_runtime_cost(start, end, db)

        assert result == []


class TestTotalCost:
    def test_sums_across_agents(self):
        rows = [
            AgentRuntimeCost("a", "A", "us-west-2", 1.0, 1.0, 0.1, 0.2, 0.3),
            AgentRuntimeCost("b", "B", "us-west-2", 2.0, 2.0, 0.4, 0.5, 0.9),
        ]
        assert AgentCoreCostService.total_cost(rows) == pytest.approx(1.2)

    def test_empty_list_returns_zero(self):
        assert AgentCoreCostService.total_cost([]) == 0.0


class TestGetAllRuntimeCost:
    """Verifies the merged flat list: governed registry + discovered Runtimes.

    Uses a real SQLite DB (not mocks) since the merge logic queries both
    V2AgentConfig and V2DiscoveredAgentcoreRuntime directly.
    """

    @pytest.fixture
    def real_db(self):
        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(
            bind=engine, tables=[V2AgentConfig.__table__, V2DiscoveredAgentcoreRuntime.__table__]
        )
        session_local = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        session = session_local()
        try:
            yield session
        finally:
            session.close()

    def test_empty_when_not_configured(self, service, real_db):
        with patch("app.services.agentcore_cost_service.settings") as mock_settings:
            mock_settings.agentcore_cost_tracking_enabled = False
            start = datetime(2024, 1, 1, tzinfo=timezone.utc)
            end = datetime(2024, 1, 2, tzinfo=timezone.utc)
            result = service.get_all_runtime_cost(start, end, real_db)
        assert result == []

    def test_empty_when_nothing_governed_or_discovered(self, service, real_db):
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end = datetime(2024, 1, 2, tzinfo=timezone.utc)
        result = service.get_all_runtime_cost(start, end, real_db)
        assert result == []

    def test_includes_discovered_runtime_with_no_governed_registration(self, service, real_db):
        real_db.add(V2DiscoveredAgentcoreRuntime(
            region="us-east-1",
            agent_runtime_arn="arn:aws:bedrock-agentcore:us-east-1:1:runtime/nimbus-abc",
            agent_runtime_id="nimbus-abc",
            agent_runtime_name="NimbusConcierge_NimbusConcierge",
            status="READY",
        ))
        real_db.commit()

        with patch.object(
            AgentCoreCostService, "get_runtime_agent_configs", return_value=[]
        ), patch(
            "app.services.agentcore_discovery_service.AgentCoreDiscoveryService.get_costs",
            return_value={},
        ):
            start = datetime(2024, 1, 1, tzinfo=timezone.utc)
            end = datetime(2024, 1, 2, tzinfo=timezone.utc)
            result = service.get_all_runtime_cost(start, end, real_db)

        assert len(result) == 1
        assert result[0].agent_name == "NimbusConcierge_NimbusConcierge"
        assert result[0].display_name == "NimbusConcierge_NimbusConcierge"
        assert result[0].region == "us-east-1"

    def test_governed_agent_identity_wins_on_arn_collision(self, service, real_db):
        # Same Runtime, once as a governed agent config and once as a
        # discovered row (as would happen once the daily scan finds it) —
        # the governed agent_name/display_name must win, not the raw
        # AgentCore Runtime name.
        arn = "arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/rt-guardrail"
        real_db.add(V2AgentConfig(
            agent_name="guardrail-recommender",
            display_name="Guardrail Recommender",
            category="Security & Guardrails",
            model_id="m",
            agentcore_agent_id=arn,
            is_agentic=True,
        ))
        real_db.add(V2DiscoveredAgentcoreRuntime(
            region="us-west-2",
            agent_runtime_arn=arn,
            agent_runtime_id="rt-guardrail",
            agent_runtime_name="guardrailrecommender_guardrailrecommender",
            status="READY",
        ))
        real_db.commit()

        with patch(
            "app.services.agentcore_discovery_service.AgentCoreDiscoveryService.get_costs",
            return_value={},
        ):
            start = datetime(2024, 1, 1, tzinfo=timezone.utc)
            end = datetime(2024, 1, 2, tzinfo=timezone.utc)
            result = service.get_all_runtime_cost(start, end, real_db)

        assert len(result) == 1
        assert result[0].agent_name == "guardrail-recommender"
        assert result[0].display_name == "Guardrail Recommender"

    def test_merges_governed_and_discovered_without_duplicates(self, service, real_db):
        real_db.add(V2AgentConfig(
            agent_name="weekly-digest-orchestrator",
            display_name="Weekly Digest Orchestrator",
            category="Orchestration",
            model_id="m",
            agentcore_agent_id="rt-weekly",
            is_agentic=True,
        ))
        real_db.add(V2DiscoveredAgentcoreRuntime(
            region="us-east-1",
            agent_runtime_arn="arn:aws:bedrock-agentcore:us-east-1:1:runtime/nimbus-abc",
            agent_runtime_id="nimbus-abc",
            agent_runtime_name="NimbusConcierge_NimbusConcierge",
            status="READY",
        ))
        real_db.commit()

        with patch(
            "app.services.agentcore_discovery_service.AgentCoreDiscoveryService.get_costs",
            return_value={},
        ):
            start = datetime(2024, 1, 1, tzinfo=timezone.utc)
            end = datetime(2024, 1, 2, tzinfo=timezone.utc)
            result = service.get_all_runtime_cost(start, end, real_db)

        assert len(result) == 2
        assert {r.agent_name for r in result} == {
            "weekly-digest-orchestrator",
            "NimbusConcierge_NimbusConcierge",
        }

    def test_applies_cost_from_discovery_service(self, service, real_db):
        arn = "arn:aws:bedrock-agentcore:us-east-1:1:runtime/nimbus-abc"
        real_db.add(V2DiscoveredAgentcoreRuntime(
            region="us-east-1",
            agent_runtime_arn=arn,
            agent_runtime_id="nimbus-abc",
            agent_runtime_name="NimbusConcierge_NimbusConcierge",
            status="READY",
        ))
        real_db.commit()

        from app.services.agentcore_discovery_service import DiscoveredRuntimeCost

        with patch.object(
            AgentCoreCostService, "get_runtime_agent_configs", return_value=[]
        ), patch(
            "app.services.agentcore_discovery_service.AgentCoreDiscoveryService.get_costs",
            return_value={arn: DiscoveredRuntimeCost(
                agent_runtime_arn=arn, vcpu_hours=0.02, gb_hours=1.0, total_cost=0.0113
            )},
        ), patch("app.services.agentcore_cost_service.settings") as mock_settings:
            mock_settings.agentcore_cost_tracking_enabled = True
            mock_settings.agentcore_vcpu_hourly_rate = 0.0895
            mock_settings.agentcore_memory_gb_hourly_rate = 0.00945
            start = datetime(2024, 1, 1, tzinfo=timezone.utc)
            end = datetime(2024, 1, 2, tzinfo=timezone.utc)
            result = service.get_all_runtime_cost(start, end, real_db)

        assert len(result) == 1
        assert result[0].vcpu_hours == pytest.approx(0.02)
        assert result[0].gb_hours == pytest.approx(1.0)
