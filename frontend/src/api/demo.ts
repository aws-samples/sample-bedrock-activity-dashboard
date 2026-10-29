import apiClient from './client';
import type {
  GenerateDemoOptions,
  GenerateDemoSummary,
  ClearDemoResult,
  DemoStatus,
} from '../types';

/** POST /api/demo/generate — generate a batch of demo data (admin only). */
export async function generateDemoData(
  opts?: GenerateDemoOptions,
): Promise<GenerateDemoSummary> {
  const { data } = await apiClient.post<GenerateDemoSummary>('/demo/generate', opts ?? {});
  return data;
}

/** POST /api/demo/clear — delete all persisted demo records (admin only). */
export async function clearDemoData(): Promise<ClearDemoResult> {
  const { data } = await apiClient.post<ClearDemoResult>('/demo/clear', {});
  return data;
}

/** GET /api/demo/status — current demo record counts and last-batch metadata. */
export async function getDemoStatus(signal?: AbortSignal): Promise<DemoStatus> {
  const { data } = await apiClient.get<DemoStatus>('/demo/status', { signal });
  return data;
}
