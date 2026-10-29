// Cost Accountability & Governance TypeScript DTOs.
//
// These types mirror the backend Pydantic v2 schemas in
// `backend/app/routes/schemas_governance.py` (and the container models colocated
// in `backend/app/routes/governance.py`). The feature exposes camelCase JSON, so
// the field names here match the backend field names exactly.

/** The three cost tiers every surfaced figure is labeled with (Req 2.4). */
export type CostBasis = 'billed' | 'reconciled' | 'estimated';

/**
 * Always-present cost-accountability summary attached to activity responses.
 * Degrades to `estimated` with `curAvailable=false` when no billed data exists.
 */
export interface CostAccountabilityBlock {
  /** Tier label for the surfaced totals (Req 2.4). */
  costBasis: CostBasis;
  /** Tier A billed total (scoped), or null when CUR absent. */
  billedCost: number | null;
  /** Tier B token-based estimated total. */
  estimatedCost: number;
  /** billed - estimated when billed is present (Req 2.3). */
  variance: number | null;
  /** True when billed data is older than the staleness window. */
  billedStale: boolean;
  /** ISO date (YYYY-MM-DD) the billed data covers through. */
  coveredThrough: string | null;
  /** Whether CUR-sourced billed data is available (Req 1.6). */
  curAvailable: boolean;
}

/**
 * A single reconciled slice keyed on (team-or-principal, model, day): billed and
 * estimated totals, the reconciliation factor, variance, an optional flag, and
 * the tier basis.
 */
export interface ReconciliationSlice {
  /** Team name or IAM principal for the slice. */
  teamOrPrincipal: string;
  /** Normalized model id for the slice. */
  model: string;
  /** ISO date (YYYY-MM-DD) the slice covers. */
  day: string;
  /** Tier A billed cost for the slice, or null when absent. */
  billed: number | null;
  /** Aggregate Tier B estimated cost for the slice. */
  estimated: number;
  /** billed / estimated, or 1.0 when reconciliation is not applied. */
  factor: number;
  /** billed - estimated when billed is present. */
  variance: number | null;
  /** Reconciliation flag, e.g. "billed_no_estimate". */
  flag: string | null;
  /** Tier basis: "reconciled" or "estimated_only". */
  basis: string;
}

/**
 * Cache-hit ratio grouped by a dimension (model or application). `cacheHitRatio`
 * is `cacheReadTokens / (inputTokens + cacheReadTokens)`, or null when the
 * denominator is zero (Req 5.4).
 */
export interface CacheHitRow {
  /** Dimension value (model id or application name). */
  label: string;
  /** Total input tokens for the group. */
  inputTokens: number;
  /** Total cache-read tokens for the group. */
  cacheReadTokens: number;
  /** Ratio in [0, 1], or null when the denominator is zero. */
  cacheHitRatio: number | null;
}

/** A per-day cache-hit ratio point for the trend line (Req 5.x). */
export interface CacheHitTrendPoint {
  /** ISO date (YYYY-MM-DD) for the trend point. */
  date: string;
  /** Input tokens on the day. */
  inputTokens: number;
  /** Cache-read tokens on the day. */
  cacheReadTokens: number;
  /** Ratio in [0, 1], or null when the denominator is zero. */
  cacheHitRatio: number | null;
}

/** Cache-hit efficiency grouped by model and application, plus a daily trend. */
export interface CacheHitResponse {
  byModel: CacheHitRow[];
  byApplication: CacheHitRow[];
  trend: CacheHitTrendPoint[];
}

/**
 * Cost-per-outcome for a workload, always explicitly labeled as an estimate.
 * `costPerOutcome` is `cost / outcomeCount` when the count is positive, else null
 * (Req 6.5). The numerator uses reconciled cost when billed data is available,
 * otherwise the estimate (Req 6.6), indicated by `estimated`.
 */
export interface CostPerOutcomeRow {
  /** Workload / application key. */
  workload: string;
  /** Outcome count (override or derived), or null when unknown. */
  outcomeCount: number | null;
  /** cost / outcomeCount, or null when count is zero/unknown. */
  costPerOutcome: number | null;
  /** Tier basis for the numerator: "reconciled" or "estimated". */
  basis: string;
  /** True when the numerator is an estimate (no billed data). */
  estimated: boolean;
}

/** A per-day point on the unattributed-share trend line (Req 7.x). */
export interface CanaryTrendPoint {
  /** ISO date (YYYY-MM-DD) for the trend point. */
  date: string;
  /** Unattributed spend share in [0, 1]. */
  unattributedSpendShare: number;
  /** Unattributed record share in [0, 1]. */
  unattributedRecordShare: number;
}

/**
 * Unattributed-spend canary result: the unattributed spend and record shares,
 * the base-model / untagged-profile share, the tier basis label (Req 7.6), the
 * configured target, and whether the current share meets it (Req 7.4).
 */
export interface CanaryResponse {
  /** Share of spend with no team match [0, 1]. */
  unattributedSpendShare: number;
  /** Share of records with no team match [0, 1]. */
  unattributedRecordShare: number;
  /** Share using a base model id / untagged profile [0, 1] (Req 7.2). */
  baseModelShare: number;
  /** Tier basis for the shares: "billed" or "estimated". */
  basis: string;
  /** Configured unattributed-spend target (e.g. 0.05). */
  target: number;
  /** True when unattributed share <= target (Req 7.4). */
  meetsTarget: boolean;
  /** Optional per-day trend over the window. */
  trend: CanaryTrendPoint[];
}

/**
 * CUR ingestion status surfaced by `GET /api/governance/cur/status`: whether
 * billed data is available, the covered-through date, staleness, the last run
 * timestamp, and the last run status.
 */
export interface CurStatus {
  /** Whether CUR-sourced billed data is available. */
  curAvailable: boolean;
  /** ISO date (YYYY-MM-DD) the billed data covers through. */
  coveredThrough: string | null;
  /** True when covered-through age exceeds the staleness window. */
  billedStale: boolean;
  /** ISO timestamp of the last ingestion run, or null. */
  lastRunAt: string | null;
  /** Last run status: "success" | "failed" | "running". */
  status: string | null;
}

/** 202 response body for the admin CUR ingest trigger. */
export interface IngestTriggerResponse {
  /** Identifier for the scheduled background run. */
  runId: string;
}

/**
 * Read/write model for the singleton unattributed-spend target config: the
 * unattributed target (fraction, e.g. 0.05 for < 5%) and an optional
 * per-environment override of the CUR staleness window in hours.
 */
export interface AttributionTargetConfigModel {
  /** Unattributed-spend target as a fraction in [0, 1]. */
  unattributedTarget: number;
  /** Optional override of the CUR staleness window (hours). */
  stalenessHoursOverride: number | null;
}

/**
 * Read/write model for an admin-supplied per-workload outcome count: the
 * workload key, the date window the count applies to, and the admin-supplied
 * outcome count used as the cost-per-outcome denominator (Req 6.3).
 */
export interface OutcomeOverrideModel {
  /** Workload / application key the count applies to. */
  application: string;
  /** ISO date (YYYY-MM-DD) window start. */
  periodStart: string;
  /** ISO date (YYYY-MM-DD) window end. */
  periodEnd: string;
  /** Admin-supplied outcome count for the window. */
  outcomeCount: number;
}

/**
 * A single row in an executive rollup level (L2 department or L3 agent): the
 * dimension label, its summed cost (basis matches the parent response's
 * `basis`), and its invocation count.
 */
export interface ExecutiveRollupLevel {
  /** Dimension value, e.g. department name or agent name ("unknown" when unset). */
  label: string;
  /** Summed cost for the group, in the same basis as the parent response. */
  cost: number;
  /** Invocation count for the group. */
  invocationCount: number;
}

/**
 * Executive cost rollup: L1 grand total -> L2 by department -> L3 by agent.
 * L4 (per-invocation) is not duplicated here — drill in via
 * `GET /api/activity` filtered to a specific department/agent.
 */
export interface ExecutiveRollupResponse {
  /** L1 grand total cost across all scoped records. */
  l1Total: number;
  /** L1 total invocation count across all scoped records. */
  l1InvocationCount: number;
  /** L2: cost + invocation count grouped by department. */
  byDepartment: ExecutiveRollupLevel[];
  /** L3: cost + invocation count grouped by agent name. */
  byAgent: ExecutiveRollupLevel[];
  /** Tier basis for every cost figure: "reconciled" or "estimated". */
  basis: string;
}

/**
 * Dedicated spend breakdown keyed on the Application tag dimension (resolved
 * from tagged Bedrock inference profiles — distinct from `sourceApplication`,
 * the calling-client label used elsewhere). Rows are sorted descending by
 * cost. Untagged usage groups under the `"unknown"` label.
 */
export interface SpendByApplicationResponse {
  /** Per-application cost and invocation count, sorted descending by cost. */
  rows: ExecutiveRollupLevel[];
  /** Tier basis for every cost figure: "reconciled" or "estimated". */
  basis: string;
}
