"""AgentCore Runtime discovery: account-wide scan feeding the AgentCore Cost tab.

Periodically calls ``bedrock-agentcore-control:ListAgentRuntimes`` across
every configured region and records what it finds in
``v2_discovered_agentcore_runtimes`` — independent of whether that Runtime
also happens to be one of the 14 agents this dashboard's own registry
(``v2_agent_configs``) governs. ``AgentCoreCostService`` merges this table
with the governed registry into a single flat list (see
``AgentCoreCostService.get_all_runtime_cost``); there is no separate
"governed vs. discovered" surface anywhere in the UI — every Runtime the
scan finds simply shows up in the AgentCore Cost tab like any other.

Best-effort like ``CurIngestionService``: a scan failure in one region never
aborts the whole run (the other configured regions still get scanned), and
:meth:`AgentCoreDiscoveryService.discover` never raises — callers (the
scheduled CLI entrypoint, the on-demand "Scan for New Agents" trigger) always
get back a :class:`DiscoveryResult` describing what happened.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

import boto3
from botocore.exceptions import ClientError, ConnectTimeoutError, EndpointConnectionError, ReadTimeoutError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.config import Settings, settings
from app.v2.models import V2DiscoveredAgentcoreRuntime

logger = logging.getLogger(__name__)

# Same CloudWatch namespace/metrics AgentCoreCostService reads for the
# governed registry — AgentCore Runtime vends these for every Runtime
# regardless of whether the dashboard governs it.
_NAMESPACE = "AWS/Bedrock-AgentCore"
_METRIC_CPU = "CPUUsed-vCPUHours"
_METRIC_MEMORY = "MemoryUsed-GBHours"
_SERVICE_DIMENSION_VALUE = "AgentCore.Runtime"
_MIN_PERIOD_SECONDS = 60


def _window_period_seconds(start: datetime, end: datetime) -> int:
    """Round the [start, end) window up to a valid single-bucket CloudWatch period."""
    seconds = (end - start).total_seconds()
    return max(_MIN_PERIOD_SECONDS, int(math.ceil(seconds / 60.0)) * 60)


@dataclass
class DiscoveredRuntimeCost:
    """Live CloudWatch-derived consumption + cost for one discovered Runtime."""

    agent_runtime_arn: str
    vcpu_hours: float
    gb_hours: float
    total_cost: float


@dataclass
class RegionScanResult:
    """Outcome of scanning a single region."""

    region: str
    status: str  # "success" | "failed"
    runtimes_found: int = 0
    error_detail: Optional[str] = None


@dataclass
class DiscoveryResult:
    """Outcome of a full :meth:`AgentCoreDiscoveryService.discover` run."""

    status: str  # "success" | "partial_failure" | "failed" | "skipped"
    scan_id: str
    total_runtimes_found: int = 0
    region_results: list[RegionScanResult] = field(default_factory=list)
    error_detail: Optional[str] = None


def parse_regions(raw: str) -> list[str]:
    """Parse a comma-separated region list, trimming whitespace and blanks."""
    return [r.strip() for r in raw.split(",") if r.strip()]


class AgentCoreDiscoveryService:
    """Scans configured regions for AgentCore Runtimes and records what's found.

    Per-region ``bedrock-agentcore-control`` clients are created lazily so
    importing this module — or instantiating the service at router import
    time — never performs AWS I/O or requires credentials to be present at
    boot (mirrors ``AgentCoreInvoker``/``AgentCoreCostService``).
    """

    def __init__(self, settings: Settings = settings) -> None:
        self._settings = settings
        self.__clients: dict[str, Any] = {}
        self.__cw_clients: dict[str, Any] = {}

    def _client_for(self, region: str) -> Any:
        if region not in self.__clients:
            self.__clients[region] = boto3.client("bedrock-agentcore-control", region_name=region)
        return self.__clients[region]

    def _cloudwatch_client_for(self, region: str) -> Any:
        if region not in self.__cw_clients:
            self.__cw_clients[region] = boto3.client("cloudwatch", region_name=region)
        return self.__cw_clients[region]

    def is_configured(self) -> bool:
        """True when discovery is enabled and at least one region is configured."""
        return self._settings.agentcore_discovery_enabled and bool(self.regions())

    def regions(self) -> list[str]:
        """The configured list of regions to scan."""
        return parse_regions(self._settings.agentcore_discovery_regions)

    # ------------------------------------------------------------------
    # Full discovery run
    # ------------------------------------------------------------------
    def discover(self, db: Session) -> DiscoveryResult:
        """Scan every configured region and upsert what's found.

        Never raises. Each region is scanned independently — a failure in
        one region (e.g. missing permissions, a region-specific outage)
        never prevents the others from being scanned; the aggregate
        ``status`` reflects whether any region failed.
        """
        scan_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")

        if not self._settings.agentcore_discovery_enabled:
            logger.info("AgentCore discovery skipped: disabled via AGENTCORE_DISCOVERY_ENABLED")
            return DiscoveryResult(status="skipped", scan_id=scan_id, error_detail="discovery disabled")

        regions = self.regions()
        if not regions:
            logger.info("AgentCore discovery skipped: no regions configured")
            return DiscoveryResult(status="skipped", scan_id=scan_id, error_detail="no regions configured")

        region_results: list[RegionScanResult] = []
        total_found = 0
        for region in regions:
            result = self._scan_region(db, region, scan_id)
            region_results.append(result)
            total_found += result.runtimes_found

        any_failed = any(r.status == "failed" for r in region_results)
        all_failed = all(r.status == "failed" for r in region_results)
        if all_failed:
            overall_status = "failed"
        elif any_failed:
            overall_status = "partial_failure"
        else:
            overall_status = "success"

        logger.info(
            "AgentCore discovery scan %s finished: status=%s regions=%s total_runtimes=%d",
            scan_id,
            overall_status,
            [r.region for r in region_results],
            total_found,
        )
        return DiscoveryResult(
            status=overall_status,
            scan_id=scan_id,
            total_runtimes_found=total_found,
            region_results=region_results,
        )

    def _scan_region(self, db: Session, region: str, scan_id: str) -> RegionScanResult:
        """List every Runtime in ``region`` (paginated) and upsert each row.

        Any error (permissions, throttling, network) is caught and reported
        as a failed :class:`RegionScanResult` for this region only — it never
        propagates to :meth:`discover`, and rows from previous successful
        scans of this region are left untouched (not deleted).
        """
        try:
            client = self._client_for(region)
            runtimes: list[dict[str, Any]] = []
            next_token: Optional[str] = None
            while True:
                kwargs: dict[str, Any] = {}
                if next_token:
                    kwargs["nextToken"] = next_token
                response = client.list_agent_runtimes(**kwargs)
                runtimes.extend(response.get("agentRuntimes", []))
                next_token = response.get("nextToken")
                if not next_token:
                    break

            for runtime in runtimes:
                self._upsert_runtime(db, region, runtime, scan_id)
            db.commit()

            logger.info("AgentCore discovery: region=%s found=%d runtimes", region, len(runtimes))
            return RegionScanResult(region=region, status="success", runtimes_found=len(runtimes))
        except (ClientError, EndpointConnectionError, ConnectTimeoutError, ReadTimeoutError) as exc:
            db.rollback()
            logger.warning("AgentCore discovery: region=%s scan failed: %s", region, exc)
            return RegionScanResult(region=region, status="failed", error_detail=str(exc))
        except Exception:
            db.rollback()
            logger.exception("AgentCore discovery: region=%s scan failed unexpectedly", region)
            return RegionScanResult(region=region, status="failed", error_detail="unexpected error")

    def _upsert_runtime(
        self, db: Session, region: str, runtime: dict[str, Any], scan_id: str
    ) -> None:
        """Upsert one discovered Runtime, keyed on its ARN.

        ``first_seen_at`` is set only on insert; ``last_seen_at``/``status``/
        ``last_scan_id`` are refreshed on every scan that still finds this
        Runtime, so a Runtime that stops appearing keeps its history (rows
        are never deleted here) while its ``last_seen_at`` naturally falls
        behind.
        """
        arn = runtime.get("agentRuntimeArn")
        if not arn:
            logger.warning("AgentCore discovery: skipping runtime with no ARN in region=%s", region)
            return

        now = datetime.now(timezone.utc)
        stmt = pg_insert(V2DiscoveredAgentcoreRuntime.__table__).values(
            region=region,
            agent_runtime_arn=arn,
            agent_runtime_id=runtime.get("agentRuntimeId", ""),
            agent_runtime_name=runtime.get("agentRuntimeName", ""),
            status=runtime.get("status"),
            first_seen_at=now,
            last_seen_at=now,
            last_scan_id=scan_id,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["agent_runtime_arn"],
            set_={
                "agent_runtime_id": stmt.excluded.agent_runtime_id,
                "agent_runtime_name": stmt.excluded.agent_runtime_name,
                "status": stmt.excluded.status,
                "last_seen_at": stmt.excluded.last_seen_at,
                "last_scan_id": stmt.excluded.last_scan_id,
                "updated_at": now,
            },
        )
        db.execute(stmt)

    # ------------------------------------------------------------------
    # Read model
    # ------------------------------------------------------------------
    @staticmethod
    def list_discovered(db: Session) -> list[V2DiscoveredAgentcoreRuntime]:
        """All discovered Runtimes, most recently seen first."""
        return list(
            db.execute(
                select(V2DiscoveredAgentcoreRuntime).order_by(
                    V2DiscoveredAgentcoreRuntime.last_seen_at.desc()
                )
            ).scalars()
        )

    @staticmethod
    def last_scan_at(db: Session) -> Optional[datetime]:
        """The most recent ``last_seen_at`` across every discovered Runtime, or None."""
        return db.execute(
            select(V2DiscoveredAgentcoreRuntime.last_seen_at).order_by(
                V2DiscoveredAgentcoreRuntime.last_seen_at.desc()
            ).limit(1)
        ).scalar_one_or_none()

    def get_costs(
        self, runtime_refs: list[tuple[str, str]], start: datetime, end: datetime
    ) -> dict[str, DiscoveredRuntimeCost]:
        """Live CloudWatch-derived cost for each ``(region, arn)`` pair over ``[start, end)``.

        Generalized over a plain list of ``(region, agent_runtime_arn)``
        tuples rather than ORM rows so it can be used for both persisted
        discovery rows and the governed registry's agents in a single call
        (see ``AgentCoreCostService.get_all_runtime_cost``). Grouped by
        region so each region's Runtimes share a single ``GetMetricData``
        call. Returns a dict keyed by ARN; a Runtime whose region call fails
        or that has no usage in the window is simply absent from the result
        (never raises) — callers should treat a missing key as zero/unknown
        cost.
        """
        by_region: dict[str, list[str]] = {}
        for region, arn in runtime_refs:
            by_region.setdefault(region, []).append(arn)

        results: dict[str, DiscoveredRuntimeCost] = {}
        period = _window_period_seconds(start, end)
        for region, arns in by_region.items():
            try:
                client = self._cloudwatch_client_for(region)
                queries: list[dict[str, Any]] = []
                query_index: dict[str, tuple[str, str]] = {}
                for idx, arn in enumerate(arns):
                    dimensions = [
                        {"Name": "Service", "Value": _SERVICE_DIMENSION_VALUE},
                        {"Name": "Resource", "Value": arn},
                    ]
                    cpu_id = f"cpu{idx}"
                    mem_id = f"mem{idx}"
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
                    query_index[cpu_id] = (arn, "cpu")
                    query_index[mem_id] = (arn, "memory")

                response = client.get_metric_data(MetricDataQueries=queries, StartTime=start, EndTime=end)

                vcpu_hours: dict[str, float] = {}
                gb_hours: dict[str, float] = {}
                for result in response.get("MetricDataResults", []):
                    info = query_index.get(result.get("Id", ""))
                    if not info:
                        continue
                    arn, metric_type = info
                    total = sum(result.get("Values", []) or [])
                    if metric_type == "cpu":
                        vcpu_hours[arn] = vcpu_hours.get(arn, 0.0) + total
                    else:
                        gb_hours[arn] = gb_hours.get(arn, 0.0) + total

                for arn in arns:
                    cpu_h = vcpu_hours.get(arn, 0.0)
                    mem_h = gb_hours.get(arn, 0.0)
                    cpu_cost = cpu_h * self._settings.agentcore_vcpu_hourly_rate
                    mem_cost = mem_h * self._settings.agentcore_memory_gb_hourly_rate
                    results[arn] = DiscoveredRuntimeCost(
                        agent_runtime_arn=arn,
                        vcpu_hours=round(cpu_h, 6),
                        gb_hours=round(mem_h, 6),
                        total_cost=round(cpu_cost + mem_cost, 6),
                    )
            except (ClientError, EndpointConnectionError, ConnectTimeoutError, ReadTimeoutError) as exc:
                logger.warning("AgentCore discovery cost: region=%s GetMetricData failed: %s", region, exc)
            except Exception:
                logger.exception("AgentCore discovery cost: region=%s unexpected error", region)

        return results


def _main() -> int:
    """Module entrypoint for the scheduled path (``python -m ...``).

    Opens a DB session and runs a single discovery scan. Returns a process
    exit code (0 on success/partial_failure/skipped, 1 on total failure) so a
    scheduled ECS task can signal outcome. A partial failure (some regions
    succeeded) still exits 0 since useful data was recorded; only a run where
    every configured region failed is treated as a failure.
    """
    logging.basicConfig(level=logging.INFO)
    from app.database import SessionLocal

    service = AgentCoreDiscoveryService()
    db = SessionLocal()
    try:
        result = service.discover(db)
        logger.info("AgentCore discovery run finished: status=%s", result.status)
        return 0 if result.status in ("success", "partial_failure", "skipped") else 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(_main())
