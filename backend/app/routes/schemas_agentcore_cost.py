"""Pydantic v2 response schemas for the AgentCore Runtime cost API (``/api/agentcore-cost/*``).

Kept in its own module/router — separate from ``schemas_governance.py`` /
``governance.py`` — so AgentCore compute cost (running the agents
themselves) is never conflated with Bedrock token cost (what the agents'
LLM calls consume), matching the standalone frontend tab.

The response is a single flat list mixing this dashboard's own governed
agents with any other AgentCore Runtime discovered by the background
account-wide scan (``AgentCoreDiscoveryService``) — there is no
"governed vs. discovered" distinction in the API or the UI.
"""

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

_MODEL_CONFIG = ConfigDict(populate_by_name=True)


class AgentRuntimeCostRow(BaseModel):
    """AgentCore Runtime consumption + cost for a single Runtime over the window.

    Mirrors ``AgentCoreCostService.AgentRuntimeCost``: raw vCPU/memory
    consumption plus the cost each contributes and their sum.
    """

    model_config = _MODEL_CONFIG

    agentName: str = Field(
        description="Agent identifier for a governed agent (e.g. 'guardrail-recommender'), "
        "or the raw AgentCore Runtime name for one found only by the discovery scan."
    )
    displayName: str = Field(description="Human-readable name for display.")
    region: str = Field(description="AWS region this Runtime runs in.")
    vcpuHours: float = Field(description="Total vCPU-hours consumed by this Runtime.")
    gbHours: float = Field(description="Total GB-hours of memory consumed by this Runtime.")
    cpuCost: float = Field(description="Cost attributable to vCPU consumption.")
    memoryCost: float = Field(description="Cost attributable to memory consumption.")
    totalCost: float = Field(description="cpuCost + memoryCost for this Runtime.")


class AgentCoreCostResponse(BaseModel):
    """AgentCore Runtime cost rollup: total -> per-Runtime drill-down, one flat list.

    Computed live from CloudWatch (``AWS/Bedrock-AgentCore`` namespace) for
    every Runtime this account knows about — this dashboard's own governed
    agents plus anything the background discovery scan has found —
    no Athena/CUR dependency for the cost figures themselves. ``configured``
    is False (with empty ``byAgent`` and zero ``totalCost``) when AgentCore
    cost tracking is disabled via ``AGENTCORE_COST_TRACKING_ENABLED``.
    """

    model_config = _MODEL_CONFIG

    totalCost: float = Field(description="Sum of totalCost across every known Runtime.")
    byAgent: list[AgentRuntimeCostRow] = Field(
        default_factory=list, description="Per-Runtime drill-down, sorted descending by totalCost."
    )
    configured: bool = Field(
        description="Whether AgentCore cost tracking is enabled (kill switch)."
    )
    vcpuHourlyRate: float = Field(description="$/vCPU-hour rate used for this computation.")
    memoryGbHourlyRate: float = Field(description="$/GB-hour rate used for this computation.")
    scanRegions: list[str] = Field(
        default_factory=list, description="Regions the background discovery scan covers."
    )
    lastScanAt: Optional[str] = Field(
        default=None,
        description="ISO timestamp of the most recent discovery scan, or null if none has run yet.",
    )


class ScanTriggerResponse(BaseModel):
    """202 response body for the admin 'Scan for New Agents' trigger."""

    model_config = _MODEL_CONFIG

    runId: str = Field(description="Identifier for the background scan run.")
