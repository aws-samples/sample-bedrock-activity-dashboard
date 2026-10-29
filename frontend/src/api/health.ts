import apiClient from './client';
import type { HealthConfig } from '../types';

/** GET /api/health/config — runtime configuration (demo mode, app name) */
export async function getHealthConfig(signal?: AbortSignal): Promise<HealthConfig> {
  const { data } = await apiClient.get<HealthConfig>('/health/config', { signal });
  return data;
}
