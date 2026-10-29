// Cost Accountability & Governance API client.
//
// All HTTP goes through the shared axios instance in `./client`, reusing its
// baseURL (`/api`) and the Cognito Bearer auth attached by the request
// interceptor. Every read accepts an optional AbortSignal so callers can cancel
// in-flight requests on filter change / unmount. Query params are mapped from
// DashboardFilters via the shared `filtersToParams`, exactly like the existing
// activity client.

import type { DashboardFilters } from '../types';
import type {
  AttributionTargetConfigModel,
  CacheHitResponse,
  CanaryResponse,
  CostPerOutcomeRow,
  CurStatus,
  ExecutiveRollupResponse,
  IngestTriggerResponse,
  OutcomeOverrideModel,
  ReconciliationSlice,
  SpendByApplicationResponse,
} from '../types/governance';
import apiClient, { filtersToParams } from './client';

// ---------------------------------------------------------------------------
// Read endpoints (scoped) — accept an AbortSignal
// ---------------------------------------------------------------------------

/** GET /api/governance/reconciliation — per-slice billed vs estimated. */
export async function getReconciliation(
  filters: DashboardFilters,
  signal?: AbortSignal
): Promise<ReconciliationSlice[]> {
  const { data } = await apiClient.get<ReconciliationSlice[]>('/governance/reconciliation', {
    params: filtersToParams(filters),
    signal,
  });
  return data;
}

/** GET /api/governance/efficiency/cache-hit — cache-hit ratio by model & application + daily trend. */
export async function getCacheHit(
  filters: DashboardFilters,
  signal?: AbortSignal
): Promise<CacheHitResponse> {
  const { data } = await apiClient.get<CacheHitResponse>('/governance/efficiency/cache-hit', {
    params: filtersToParams(filters),
    signal,
  });
  return data;
}

/** GET /api/governance/efficiency/cost-per-outcome — cost-per-outcome per workload. */
export async function getCostPerOutcome(
  filters: DashboardFilters,
  signal?: AbortSignal
): Promise<CostPerOutcomeRow[]> {
  const { data } = await apiClient.get<CostPerOutcomeRow[]>(
    '/governance/efficiency/cost-per-outcome',
    { params: filtersToParams(filters), signal }
  );
  return data;
}

/** GET /api/governance/canary — unattributed-spend canary: share vs target + trend. */
export async function getCanary(
  filters: DashboardFilters,
  signal?: AbortSignal
): Promise<CanaryResponse> {
  const { data } = await apiClient.get<CanaryResponse>('/governance/canary', {
    params: filtersToParams(filters),
    signal,
  });
  return data;
}

/** GET /api/governance/cur/status — CUR ingestion status (availability, staleness, last run). */
export async function getCurStatus(signal?: AbortSignal): Promise<CurStatus> {
  const { data } = await apiClient.get<CurStatus>('/governance/cur/status', { signal });
  return data;
}

/**
 * GET /api/governance/rollup — executive cost rollup: L1 total -> L2 department -> L3 agent.
 *
 * When `department` is supplied, the L1 total and `byAgent` are scoped to that
 * department (an L2->L3 drill); `byDepartment` always reflects the full window
 * so the L2 selector stays populated regardless of the current drill.
 */
export async function getExecutiveRollup(
  filters: DashboardFilters,
  department?: string,
  signal?: AbortSignal
): Promise<ExecutiveRollupResponse> {
  const params = filtersToParams(filters);
  if (department) params.department = department;
  const { data } = await apiClient.get<ExecutiveRollupResponse>('/governance/rollup', {
    params,
    signal,
  });
  return data;
}

/** GET /api/governance/spend-by-application — spend breakdown by the Application tag dimension. */
export async function getSpendByApplication(
  filters: DashboardFilters,
  signal?: AbortSignal
): Promise<SpendByApplicationResponse> {
  const { data } = await apiClient.get<SpendByApplicationResponse>(
    '/governance/spend-by-application',
    { params: filtersToParams(filters), signal }
  );
  return data;
}

// ---------------------------------------------------------------------------
// Admin trigger + config endpoints
// ---------------------------------------------------------------------------

/** POST /api/governance/cur/ingest — trigger an off-request CUR ingestion run (admin only). */
export async function triggerCurIngest(): Promise<IngestTriggerResponse> {
  const { data } = await apiClient.post<IngestTriggerResponse>('/governance/cur/ingest');
  return data;
}

/** GET /api/governance/attribution-target — read the unattributed-spend target config. */
export async function getAttributionTarget(
  signal?: AbortSignal
): Promise<AttributionTargetConfigModel> {
  const { data } = await apiClient.get<AttributionTargetConfigModel>(
    '/governance/attribution-target',
    { signal }
  );
  return data;
}

/** PUT /api/governance/attribution-target — set the unattributed-spend target config (admin only). */
export async function putAttributionTarget(
  body: AttributionTargetConfigModel
): Promise<AttributionTargetConfigModel> {
  const { data } = await apiClient.put<AttributionTargetConfigModel>(
    '/governance/attribution-target',
    body
  );
  return data;
}

/** GET /api/governance/outcome-overrides — list admin per-workload outcome-count overrides. */
export async function getOutcomeOverrides(
  signal?: AbortSignal
): Promise<OutcomeOverrideModel[]> {
  const { data } = await apiClient.get<OutcomeOverrideModel[]>('/governance/outcome-overrides', {
    signal,
  });
  return data;
}

/** PUT /api/governance/outcome-overrides — upsert an admin per-workload outcome-count override (admin only). */
export async function putOutcomeOverride(
  body: OutcomeOverrideModel
): Promise<OutcomeOverrideModel> {
  const { data } = await apiClient.put<OutcomeOverrideModel>('/governance/outcome-overrides', body);
  return data;
}
