"""Pydantic v2 response/request schemas for the Cost Governance API.

These models describe the JSON contract for the ``/api/governance/*`` router
(tasks 12.2 / 12.3) and the additive ``costAccountability`` block surfaced on
the existing activity endpoints. They mirror the service dataclasses defined in
the ``cost-accountability-governance`` design (``reconciliation_service``,
``efficiency_service``, ``canary_service``, ``cur_ingestion_service``).

Convention: this feature exposes camelCase JSON. Consistent with the existing
response schemas in this package (e.g. ``TeamMemberResponse`` in
``routes/teams.py``), camelCase is expressed by naming the fields camelCase
directly rather than via aliases. All models use Pydantic v2 ``model_config``.
"""

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

# Populate-by-name is harmless for the literal-camelCase fields used here and
# keeps these models tolerant of programmatic construction by attribute name.
_MODEL_CONFIG = ConfigDict(populate_by_name=True)

# The three cost tiers every surfaced figure is labeled with (Req 2.4).
CostBasis = Literal["billed", "reconciled", "estimated"]


# ---------------------------------------------------------------------------
# Cost accountability (additive block on activity endpoints) — Req 2.4, 1.6/1.7
# ---------------------------------------------------------------------------

class CostAccountabilityBlock(BaseModel):
    """Always-present cost-accountability summary attached to activity responses.

    Labels the totals with their tier (``costBasis``) and reports CUR
    availability / staleness so the frontend can render the correct basis pill
    and staleness chip. Degrades to ``estimated`` with ``curAvailable=False``
    when no billed data is available.
    """

    model_config = _MODEL_CONFIG

    costBasis: CostBasis = Field(
        description="Tier label for the surfaced totals (Req 2.4)."
    )
    billedCost: Optional[float] = Field(
        default=None, description="Tier A billed total (scoped), or null when CUR absent."
    )
    estimatedCost: float = Field(description="Tier B token-based estimated total.")
    variance: Optional[float] = Field(
        default=None, description="billed - estimated when billed is present (Req 2.3)."
    )
    billedStale: bool = Field(
        default=False, description="True when billed data is older than the staleness window."
    )
    coveredThrough: Optional[str] = Field(
        default=None, description="ISO date (YYYY-MM-DD) the billed data covers through."
    )
    curAvailable: bool = Field(
        default=False, description="Whether CUR-sourced billed data is available (Req 1.6)."
    )


# ---------------------------------------------------------------------------
# Reconciliation — Req 3.7
# ---------------------------------------------------------------------------

class ReconciliationSlice(BaseModel):
    """A single reconciled slice keyed on (team-or-principal, model, day).

    Mirrors ``ReconciliationService.ReconciledSlice``: exposes the billed and
    estimated totals, the reconciliation factor (billed / estimated, or 1.0 when
    not applied), the variance, an optional flag, and the tier basis.
    """

    model_config = _MODEL_CONFIG

    teamOrPrincipal: str = Field(description="Team name or IAM principal for the slice.")
    model: str = Field(description="Normalized model id for the slice.")
    day: str = Field(description="ISO date (YYYY-MM-DD) the slice covers.")
    billed: Optional[float] = Field(
        default=None, description="Tier A billed cost for the slice, or null when absent."
    )
    estimated: float = Field(description="Aggregate Tier B estimated cost for the slice.")
    factor: float = Field(
        default=1.0, description="billed / estimated, or 1.0 when reconciliation is not applied."
    )
    variance: Optional[float] = Field(
        default=None, description="billed - estimated when billed is present."
    )
    flag: Optional[str] = Field(
        default=None, description='Reconciliation flag, e.g. "billed_no_estimate".'
    )
    basis: str = Field(description='Tier basis: "reconciled" or "estimated_only".')


# ---------------------------------------------------------------------------
# Efficiency: cache-hit ratio — Req 5.x
# ---------------------------------------------------------------------------

class CacheHitRow(BaseModel):
    """Cache-hit ratio grouped by a dimension (model or application).

    Mirrors ``EfficiencyService.CacheHitRow``. ``cacheHitRatio`` is
    ``cacheReadTokens / (inputTokens + cacheReadTokens)``, and is null when the
    denominator is zero (Req 5.4).
    """

    model_config = _MODEL_CONFIG

    label: str = Field(description="Dimension value (model id or application name).")
    inputTokens: int = Field(default=0, description="Total input tokens for the group.")
    cacheReadTokens: int = Field(default=0, description="Total cache-read tokens for the group.")
    cacheHitRatio: Optional[float] = Field(
        default=None, description="Ratio in [0, 1], or null when the denominator is zero."
    )


class CacheHitTrendPoint(BaseModel):
    """A per-day cache-hit ratio point for the trend line (Req 5.x)."""

    model_config = _MODEL_CONFIG

    date: str = Field(description="ISO date (YYYY-MM-DD) for the trend point.")
    inputTokens: int = Field(default=0, description="Input tokens on the day.")
    cacheReadTokens: int = Field(default=0, description="Cache-read tokens on the day.")
    cacheHitRatio: Optional[float] = Field(
        default=None, description="Ratio in [0, 1], or null when the denominator is zero."
    )


# ---------------------------------------------------------------------------
# Efficiency: cost-per-outcome — Req 6.x
# ---------------------------------------------------------------------------

class CostPerOutcomeRow(BaseModel):
    """Cost-per-outcome for a workload, always explicitly labeled as an estimate.

    Mirrors ``EfficiencyService.cost_per_outcome`` output: ``costPerOutcome`` is
    ``cost / outcomeCount`` when the count is positive, else null (Req 6.5). The
    numerator uses the reconciled cost when billed data is available, otherwise
    the estimate (Req 6.6), indicated by ``estimated``.
    """

    model_config = _MODEL_CONFIG

    workload: str = Field(description="Workload / application key.")
    outcomeCount: Optional[int] = Field(
        default=None, description="Outcome count (override or derived), or null when unknown."
    )
    costPerOutcome: Optional[float] = Field(
        default=None, description="cost / outcomeCount, or null when count is zero/unknown."
    )
    basis: str = Field(description='Tier basis for the numerator: "reconciled" or "estimated".')
    estimated: bool = Field(
        default=True, description="True when the numerator is an estimate (no billed data)."
    )


# ---------------------------------------------------------------------------
# Unattributed-spend canary — Req 7.x
# ---------------------------------------------------------------------------

class CanaryTrendPoint(BaseModel):
    """A per-day point on the unattributed-share trend line (Req 7.x)."""

    model_config = _MODEL_CONFIG

    date: str = Field(description="ISO date (YYYY-MM-DD) for the trend point.")
    unattributedSpendShare: float = Field(description="Unattributed spend share in [0, 1].")
    unattributedRecordShare: float = Field(description="Unattributed record share in [0, 1].")


class CanaryResponse(BaseModel):
    """Unattributed-spend canary result.

    Mirrors ``CanaryService.CanaryResult``: the unattributed spend and record
    shares (each in [0, 1]), the base-model / untagged-profile share, the tier
    basis label (billed vs estimated, Req 7.6), the configured target, and
    whether the current share meets it (share <= target, Req 7.4).
    """

    model_config = _MODEL_CONFIG

    unattributedSpendShare: float = Field(description="Share of spend with no team match [0, 1].")
    unattributedRecordShare: float = Field(
        description="Share of records with no team match [0, 1]."
    )
    baseModelShare: float = Field(
        description="Share using a base model id / untagged profile [0, 1] (Req 7.2)."
    )
    basis: str = Field(description='Tier basis for the shares: "billed" or "estimated".')
    target: float = Field(description="Configured unattributed-spend target (e.g. 0.05).")
    meetsTarget: bool = Field(description="True when unattributed share <= target (Req 7.4).")
    trend: list[CanaryTrendPoint] = Field(
        default_factory=list, description="Optional per-day trend over the window."
    )


# ---------------------------------------------------------------------------
# CUR ingestion status — Req 1.5 / 1.7
# ---------------------------------------------------------------------------

class CurStatus(BaseModel):
    """CUR ingestion status surfaced by ``GET /api/governance/cur/status``.

    Mirrors ``CurIngestionService.get_state``: whether billed data is available,
    the covered-through date, staleness, the last run timestamp, and the last
    run status.
    """

    model_config = _MODEL_CONFIG

    curAvailable: bool = Field(description="Whether CUR-sourced billed data is available.")
    coveredThrough: Optional[str] = Field(
        default=None, description="ISO date (YYYY-MM-DD) the billed data covers through."
    )
    billedStale: bool = Field(
        default=False, description="True when covered-through age exceeds the staleness window."
    )
    lastRunAt: Optional[str] = Field(
        default=None, description="ISO timestamp of the last ingestion run, or null."
    )
    status: Optional[str] = Field(
        default=None, description='Last run status: "success" | "failed" | "running".'
    )


# ---------------------------------------------------------------------------
# Governance config request/response models — Req 7.4, 6.3, 10.4
# ---------------------------------------------------------------------------

class AttributionTargetConfigModel(BaseModel):
    """Read/write model for the singleton unattributed-spend target config.

    Mirrors the ``attribution_target_config`` table: the unattributed target
    (fraction, e.g. 0.05 for < 5%) and an optional per-environment override of
    the CUR staleness window in hours.
    """

    model_config = _MODEL_CONFIG

    unattributedTarget: float = Field(
        ge=0.0, le=1.0, description="Unattributed-spend target as a fraction in [0, 1]."
    )
    stalenessHoursOverride: Optional[int] = Field(
        default=None, ge=0, description="Optional override of the CUR staleness window (hours)."
    )


class ExecutiveRollupLevel(BaseModel):
    """A single row in the executive cost rollup (L2 department or L3 agent).

    ``cost`` uses the reconciled cost when billed data is available for the
    window, otherwise the token-based estimate — same basis-selection rule as
    ``CostPerOutcomeRow`` (Req 6.6) — labeled by the response-level ``basis``
    field rather than repeated per row.
    """

    model_config = _MODEL_CONFIG

    label: str = Field(description="Dimension value (department name or agent name).")
    cost: float = Field(description="Total cost for this row, in the response's basis.")
    invocationCount: int = Field(description="Total invocation count for this row.")


class ExecutiveRollupResponse(BaseModel):
    """Executive cost rollup: L1 total -> L2 by department -> L3 by agent.

    L4 (per-invocation) is not duplicated here — callers drill into it via the
    existing ``GET /api/activity`` endpoint filtered to a specific department/
    agent, since raw activity records are already available there.
    """

    model_config = _MODEL_CONFIG

    l1Total: float = Field(description="Grand total cost across all scoped records.")
    l1InvocationCount: int = Field(description="Grand total invocation count.")
    byDepartment: list[ExecutiveRollupLevel] = Field(
        default_factory=list, description="L2: cost and invocation count by department."
    )
    byAgent: list[ExecutiveRollupLevel] = Field(
        default_factory=list, description="L3: cost and invocation count by agent name."
    )
    basis: str = Field(
        description='Tier basis for every cost figure: "reconciled" or "estimated".'
    )


class SpendByApplicationResponse(BaseModel):
    """Dedicated spend breakdown keyed on the Application tag dimension.

    ``application`` is resolved from tagged Bedrock inference profiles (see
    ``ProfileTagService`` / ``routes/activity.enrich_records_with_dimensions``)
    — distinct from ``sourceApplication`` (the calling client label) and from
    the executive rollup's Department/Agent hierarchy. Each row's cost uses the
    reconciled cost when billed data is available for the window, otherwise the
    token-based estimate — same basis-selection rule as
    :class:`ExecutiveRollupResponse` — labeled by the response-level ``basis``
    field rather than repeated per row. Rows are sorted descending by cost.
    """

    model_config = _MODEL_CONFIG

    rows: list[ExecutiveRollupLevel] = Field(
        default_factory=list,
        description="Per-application cost and invocation count, sorted descending by cost.",
    )
    basis: str = Field(
        description='Tier basis for every cost figure: "reconciled" or "estimated".'
    )


class OutcomeOverrideModel(BaseModel):
    """Read/write model for an admin-supplied per-workload outcome count.

    Mirrors the ``workload_outcome_override`` table: the workload key, the
    date window the count applies to, and the admin-supplied outcome count used
    as the cost-per-outcome denominator (Req 6.3).
    """

    model_config = _MODEL_CONFIG

    application: str = Field(description="Workload / application key the count applies to.")
    periodStart: str = Field(description="ISO date (YYYY-MM-DD) window start.")
    periodEnd: str = Field(description="ISO date (YYYY-MM-DD) window end.")
    outcomeCount: int = Field(ge=0, description="Admin-supplied outcome count for the window.")
