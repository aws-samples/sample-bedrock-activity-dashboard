"""AgentCore Runtime consumption cost service (live, CloudWatch-based).

Computes the compute cost of running the V2 agent platform itself — the
per-second vCPU/memory billing for Bedrock AgentCore Runtime — as distinct
from the Bedrock *token* cost the agents' LLM calls consume (that side is
already covered by ``CostAnalysisTab`` / ``CostGovernanceTab``).

AgentCore Runtime vends its own resource-usage metrics directly to
CloudWatch (namespace ``AWS/Bedrock-AgentCore``, metrics
``CPUUsed-vCPUHours`` / ``MemoryUsed-GBHours``, dimensioned by
``Service=AgentCore.Runtime`` + ``Resource=<agent runtime ARN>``), so this
is computed live via ``cloudwatch:GetMetricData`` — there is no Athena/CUR
dependency and nothing is persisted. Rates are the published on-demand
prices (https://aws.amazon.com/bedrock/agentcore/pricing/), overridable via
``AGENTCORE_VCPU_HOURLY_RATE`` / ``AGENTCORE_MEMORY_GB_HOURLY_RATE`` so a
price change or a different region never requires a code change.

Best-effort like ``ProfileTagService``: any CloudWatch failure logs a
WARNING and returns an empty/zero result rather than raising, so an
AgentCore Cost page failure never takes down the rest of the dashboard.

Two independent conditions gate whether a *governed* agent can appear via
:meth:`get_runtime_cost`:
  - it must have a deployed AgentCore Runtime (``agentcore_agent_id`` set);
  - it must be agentic (``is_agentic`` — the same flag that drives the
    🤖/⚙️ icon in Agent Management), since deterministic agents resolve
    entirely in-process via ``AgentExecutionService._try_direct_execution``
    and never reach AgentCore Runtime even when a Runtime package happens
    to be deployed for them (most agents got one during initial rollout).
    Without this second filter, deterministic ECS-only agents would show
    up in the per-agent breakdown despite being structurally unable to
    ever accrue Runtime cost.

The AgentCore Cost tab itself calls :meth:`get_all_runtime_cost`, which
additionally merges in every Runtime ``AgentCoreDiscoveryService`` has found
in a background account-wide scan — so a Runtime someone deployed outside
this dashboard's own 14-agent registry still shows up here once discovered,
with no separate "governed vs. discovered" UI distinction.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

import boto3
from botocore.exceptions import ClientError, ConnectTimeoutError, EndpointConnectionError, ReadTimeoutError
from sqlalchemy.orm import Session

from app.config import settings
from app.services.agentcore_discovery_service import AgentCoreDiscoveryService
from app.v2.models import V2AgentConfig, V2DiscoveredAgentcoreRuntime

logger = logging.getLogger(__name__)

# AgentCore Runtime publishes these directly to the caller's account.
_NAMESPACE = "AWS/Bedrock-AgentCore"
_METRIC_CPU = "CPUUsed-vCPUHours"
_METRIC_MEMORY = "MemoryUsed-GBHours"
_SERVICE_DIMENSION_VALUE = "AgentCore.Runtime"

# CloudWatch GetMetricData periods must be a positive multiple of 60 seconds
# once they exceed 60s. A single bucket spanning the whole requested window
# is requested per metric per agent, so each query returns at most one
# datapoint (its Sum) rather than a time series we'd have to re-aggregate.
_MIN_PERIOD_SECONDS = 60


def _window_period_seconds(start: datetime, end: datetime) -> int:
    """Round the [start, end) window up to a valid single-bucket CloudWatch period."""
    seconds = (end - start).total_seconds()
    return max(_MIN_PERIOD_SECONDS, int(math.ceil(seconds / 60.0)) * 60)


@dataclass
class AgentRuntimeCost:
    """AgentCore Runtime consumption + cost for one agent/Runtime over a window.

    ``agent_name``/``display_name`` are the dashboard's own registry values
    for a governed agent, or fall back to the raw AgentCore Runtime name for
    a Runtime found only by discovery (no governed registration) — see
    :meth:`AgentCoreCostService.get_all_runtime_cost`.
    """

    agent_name: str
    display_name: str
    region: str
    vcpu_hours: float
    gb_hours: float
    cpu_cost: float
    memory_cost: float
    total_cost: float


class AgentCoreCostService:
    """Computes AgentCore Runtime consumption cost from live CloudWatch metrics."""

    def __init__(self) -> None:
        region = settings.bedrock_region or settings.aws_region
        self._region = region
        # Lazily created so importing this module never performs AWS I/O or
        # requires credentials to be present at boot (mirrors AgentCoreInvoker).
        self.__client: Any = None
        self.__account_id: Optional[str] = None

    @property
    def _client(self) -> Any:
        """Lazily created ``cloudwatch`` client."""
        if self.__client is None:
            self.__client = boto3.client("cloudwatch", region_name=self._region)
        return self.__client

    @property
    def _account_id(self) -> str:
        """Resolve the caller's AWS account id lazily for ARN construction."""
        if self.__account_id is None:
            try:
                sts = boto3.client("sts", region_name=self._region)
                self.__account_id = sts.get_caller_identity()["Account"]
            except Exception:
                self.__account_id = "unknown"
        return self.__account_id

    def is_configured(self) -> bool:
        """True when AgentCore cost tracking is enabled (kill switch)."""
        return settings.agentcore_cost_tracking_enabled

    def _resolve_arn(self, agentcore_agent_id: str) -> str:
        """Build the full runtime ARN from a bare id, or pass an existing ARN through."""
        if agentcore_agent_id.startswith("arn:"):
            return agentcore_agent_id
        return (
            f"arn:aws:bedrock-agentcore:{self._region}:{self._account_id}"
            f":runtime/{agentcore_agent_id}"
        )

    @staticmethod
    def get_runtime_agent_configs(db: Session) -> list[V2AgentConfig]:
        """Agent configs that can actually accrue AgentCore Runtime cost.

        Requires both a deployed Runtime (``agentcore_agent_id`` set) and
        ``is_agentic=True``. Deterministic (⚙️) agents are excluded even if
        a Runtime package exists for them, since their execution always
        resolves directly in the backend's ECS task and never invokes
        AgentCore Runtime — see module docstring.
        """
        return (
            db.query(V2AgentConfig)
            .filter(
                V2AgentConfig.agentcore_agent_id.isnot(None),
                V2AgentConfig.is_agentic.is_(True),
            )
            .order_by(V2AgentConfig.agent_name)
            .all()
        )

    def get_runtime_cost(
        self,
        start: datetime,
        end: datetime,
        db: Session,
    ) -> list[AgentRuntimeCost]:
        """Per-agent AgentCore Runtime cost over ``[start, end)``.

        Returns one :class:`AgentRuntimeCost` per agent with a deployed
        Runtime, sorted descending by ``total_cost``. Returns an empty list
        when cost tracking is disabled, no agents have a Runtime deployed, or
        the CloudWatch call fails (logged, never raised).
        """
        if not self.is_configured():
            return []

        configs = self.get_runtime_agent_configs(db)
        if not configs:
            return []

        period = _window_period_seconds(start, end)
        queries: list[dict[str, Any]] = []
        # Maps a MetricDataQueries "Id" back to (agent_name, "cpu"|"memory").
        query_index: dict[str, tuple[str, str]] = {}

        for idx, cfg in enumerate(configs):
            arn = self._resolve_arn(cfg.agentcore_agent_id)
            cpu_id = f"cpu{idx}"
            mem_id = f"mem{idx}"
            dimensions = [
                {"Name": "Service", "Value": _SERVICE_DIMENSION_VALUE},
                {"Name": "Resource", "Value": arn},
            ]
            queries.append({
                "Id": cpu_id,
                "MetricStat": {
                    "Metric": {"Namespace": _NAMESPACE, "MetricName": _METRIC_CPU, "Dimensions": dimensions},
                    "Period": period,
                    "Stat": "Sum",
                },
                "ReturnData": True,
            })
            queries.append({
                "Id": mem_id,
                "MetricStat": {
                    "Metric": {"Namespace": _NAMESPACE, "MetricName": _METRIC_MEMORY, "Dimensions": dimensions},
                    "Period": period,
                    "Stat": "Sum",
                },
                "ReturnData": True,
            })
            query_index[cpu_id] = (cfg.agent_name, "cpu")
            query_index[mem_id] = (cfg.agent_name, "memory")

        try:
            response = self._client.get_metric_data(
                MetricDataQueries=queries,
                StartTime=start,
                EndTime=end,
            )
        except (ClientError, EndpointConnectionError, ConnectTimeoutError, ReadTimeoutError) as exc:
            logger.warning("AgentCore cost: GetMetricData failed: %s", exc)
            return []
        except Exception:
            logger.exception("AgentCore cost: unexpected error querying CloudWatch")
            return []

        vcpu_hours: dict[str, float] = {}
        gb_hours: dict[str, float] = {}
        for result in response.get("MetricDataResults", []):
            info = query_index.get(result.get("Id", ""))
            if not info:
                continue
            agent_name, metric_type = info
            total = sum(result.get("Values", []) or [])
            if metric_type == "cpu":
                vcpu_hours[agent_name] = vcpu_hours.get(agent_name, 0.0) + total
            else:
                gb_hours[agent_name] = gb_hours.get(agent_name, 0.0) + total

        results: list[AgentRuntimeCost] = []
        for cfg in configs:
            cpu_h = vcpu_hours.get(cfg.agent_name, 0.0)
            mem_h = gb_hours.get(cfg.agent_name, 0.0)
            cpu_cost = cpu_h * settings.agentcore_vcpu_hourly_rate
            mem_cost = mem_h * settings.agentcore_memory_gb_hourly_rate
            results.append(AgentRuntimeCost(
                agent_name=cfg.agent_name,
                display_name=cfg.display_name,
                region=self._region,
                vcpu_hours=round(cpu_h, 6),
                gb_hours=round(mem_h, 6),
                cpu_cost=round(cpu_cost, 6),
                memory_cost=round(mem_cost, 6),
                total_cost=round(cpu_cost + mem_cost, 6),
            ))

        results.sort(key=lambda r: r.total_cost, reverse=True)
        return results

    # ------------------------------------------------------------------
    # Unified read model: governed registry + discovered Runtimes, merged
    # ------------------------------------------------------------------
    def get_all_runtime_cost(
        self,
        start: datetime,
        end: datetime,
        db: Session,
    ) -> list[AgentRuntimeCost]:
        """Every AgentCore Runtime this account knows about, in one flat list.

        Merges two sources so the AgentCore Cost tab never needs a separate
        "governed vs. discovered" view:
          - the governed registry (agentic agents in ``v2_agent_configs``
            with a deployed Runtime), same as :meth:`get_runtime_cost`;
          - every Runtime ``AgentCoreDiscoveryService`` has ever recorded in
            a background scan (``v2_discovered_agentcore_runtimes``), which
            covers any AgentCore Runtime in the account — including ones
            this dashboard doesn't govern (e.g. an agent someone deployed
            separately).

        Deduplicated by resolved ARN: a Runtime present in both sources
        (i.e. a governed agent that the scan also found) appears once,
        using the governed registry's ``agent_name``/``display_name`` so
        its identity in the UI doesn't change. A Runtime found only by
        discovery uses its raw AgentCore Runtime name for both fields.

        Returns an empty list when cost tracking is disabled. Never raises —
        each underlying CloudWatch call is already best-effort in
        :meth:`AgentCoreDiscoveryService.get_costs`.
        """
        if not self.is_configured():
            return []

        # (region, arn) -> (agent_name, display_name), so the governed
        # registry's identity wins on ARN collision.
        refs: dict[str, tuple[str, str, str]] = {}  # arn -> (region, agent_name, display_name)

        for cfg in self.get_runtime_agent_configs(db):
            arn = self._resolve_arn(cfg.agentcore_agent_id)
            refs[arn] = (self._region, cfg.agent_name, cfg.display_name)

        discovery = AgentCoreDiscoveryService()
        discovered: list[V2DiscoveredAgentcoreRuntime] = discovery.list_discovered(db)
        for rt in discovered:
            if rt.agent_runtime_arn not in refs:
                # No governed registration for this Runtime — use its raw
                # AgentCore name as both the identifier and display label.
                refs[rt.agent_runtime_arn] = (rt.region, rt.agent_runtime_name, rt.agent_runtime_name)

        if not refs:
            return []

        runtime_refs = [(region, arn) for arn, (region, _, _) in refs.items()]
        costs = discovery.get_costs(runtime_refs, start, end)

        results: list[AgentRuntimeCost] = []
        for arn, (region, agent_name, display_name) in refs.items():
            cost = costs.get(arn)
            cpu_h = cost.vcpu_hours if cost else 0.0
            mem_h = cost.gb_hours if cost else 0.0
            cpu_cost = cpu_h * settings.agentcore_vcpu_hourly_rate
            mem_cost = mem_h * settings.agentcore_memory_gb_hourly_rate
            results.append(AgentRuntimeCost(
                agent_name=agent_name,
                display_name=display_name,
                region=region,
                vcpu_hours=round(cpu_h, 6),
                gb_hours=round(mem_h, 6),
                cpu_cost=round(cpu_cost, 6),
                memory_cost=round(mem_cost, 6),
                total_cost=round(cpu_cost + mem_cost, 6),
            ))

        results.sort(key=lambda r: r.total_cost, reverse=True)
        return results

    @staticmethod
    def total_cost(per_agent: list[AgentRuntimeCost]) -> float:
        """Sum ``total_cost`` across a per-agent breakdown."""
        return round(sum(r.total_cost for r in per_agent), 6)
