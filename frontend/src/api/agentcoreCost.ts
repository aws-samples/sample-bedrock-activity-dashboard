// AgentCore Runtime Consumption Cost API client.
//
// All HTTP goes through the shared axios instance in `./client`. Kept as its
// own module — separate from `api/governance.ts` — mirroring the standalone
// backend router (`/api/agentcore-cost`) and the standalone frontend tab, so
// AgentCore compute cost is never conflated with Bedrock token cost.

import type { DashboardFilters } from '../types';
import type { AgentCoreCostResponse, ScanTriggerResponse } from '../types/agentcoreCost';
import apiClient, { filtersToParams } from './client';

/**
 * GET /api/agentcore-cost — AgentCore Runtime consumption cost: total ->
 * per-Runtime drill-down, covering both this dashboard's own governed
 * agents and anything the background discovery scan has found. Reuses the
 * shared date-range filter (start/end only — user/team/source filters
 * don't apply to a compute-cost view).
 */
export async function getAgentCoreCost(
  filters: DashboardFilters,
  signal?: AbortSignal
): Promise<AgentCoreCostResponse> {
  // The backend route only reads start_date/end_date; any other params
  // filtersToParams emits (user/team/source) are simply ignored server-side.
  const { data } = await apiClient.get<AgentCoreCostResponse>('/agentcore-cost', {
    params: filtersToParams(filters),
    signal,
  });
  return data;
}

/**
 * POST /api/agentcore-cost/scan — trigger an off-request scan for new
 * AgentCore Runtimes (admin only). Runs in the background; results appear
 * on the next GET /api/agentcore-cost call.
 */
export async function triggerAgentCoreScan(): Promise<ScanTriggerResponse> {
  const { data } = await apiClient.post<ScanTriggerResponse>('/agentcore-cost/scan');
  return data;
}
