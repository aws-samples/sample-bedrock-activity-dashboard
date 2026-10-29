// AgentCore Runtime Consumption Cost TypeScript DTOs.
//
// These types mirror the backend Pydantic v2 schemas in
// `backend/app/routes/schemas_agentcore_cost.py`. Deliberately kept in their
// own module — separate from `types/governance.ts` — so AgentCore compute
// cost (running the 14 agents themselves) is never conflated with Bedrock
// token cost (what the agents' own LLM calls consume). The feature exposes
// camelCase JSON, so the field names here match the backend field names
// exactly.

/**
 * AgentCore Runtime consumption + cost for a single Runtime over the window.
 * Mirrors `AgentCoreCostService.AgentRuntimeCost`. This dashboard's own
 * governed agents and any other AgentCore Runtime found by the background
 * discovery scan appear in the same flat list — there is no
 * governed-vs-discovered distinction in this type.
 */
export interface AgentRuntimeCostRow {
  /** Agent identifier for a governed agent, or the raw AgentCore Runtime name otherwise. */
  agentName: string;
  /** Human-readable name for display. */
  displayName: string;
  /** AWS region this Runtime runs in. */
  region: string;
  /** Total vCPU-hours consumed by this Runtime. */
  vcpuHours: number;
  /** Total GB-hours of memory consumed by this Runtime. */
  gbHours: number;
  /** Cost attributable to vCPU consumption. */
  cpuCost: number;
  /** Cost attributable to memory consumption. */
  memoryCost: number;
  /** cpuCost + memoryCost for this Runtime. */
  totalCost: number;
}

/**
 * AgentCore Runtime cost rollup: total -> per-Runtime drill-down, one flat
 * list covering every Runtime this account knows about. Computed live from
 * CloudWatch — no Athena/CUR dependency for the cost figures. `configured=false`
 * (with empty `byAgent` and zero `totalCost`) when AgentCore cost tracking is
 * disabled server-side.
 */
export interface AgentCoreCostResponse {
  /** Sum of totalCost across every known Runtime. */
  totalCost: number;
  /** Per-Runtime drill-down, sorted descending by totalCost. */
  byAgent: AgentRuntimeCostRow[];
  /** Whether AgentCore cost tracking is enabled (kill switch). */
  configured: boolean;
  /** $/vCPU-hour rate used for this computation. */
  vcpuHourlyRate: number;
  /** $/GB-hour rate used for this computation. */
  memoryGbHourlyRate: number;
  /** Regions the background discovery scan covers. */
  scanRegions: string[];
  /** ISO timestamp of the most recent discovery scan, or null if none has run yet. */
  lastScanAt: string | null;
}

/** 202 response body for the admin "Scan for New Agents" trigger. */
export interface ScanTriggerResponse {
  /** Identifier for the background scan run. */
  runId: string;
}
