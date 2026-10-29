/** V2 Agent Platform API client functions. */

import apiClient from '../api/client';
import type {
  AgentConfig,
  AgentConfigUpdate,
  AgentExecution,
  AgentFinding,
  AgentFindingSummary,
  ApprovalAction,
  ApprovalResult,
  FindingFilters,
  Policy,
  ResidencyPolicy,
} from '../types/v2';

const V2 = '/v2/agents';

// --- Agent execution ---

export async function executeAgent(agentName: string, triggerType = 'manual') {
  const { data } = await apiClient.post(`${V2}/${agentName}/execute`, { trigger_type: triggerType });
  return data;
}

// --- Agent config ---

export async function getAgentConfigs(): Promise<AgentConfig[]> {
  const { data } = await apiClient.get<AgentConfig[]>(`${V2}/config`);
  return data;
}

export async function updateAgentConfig(agentName: string, update: AgentConfigUpdate) {
  const { data } = await apiClient.patch(`${V2}/${agentName}/config`, update);
  return data;
}

// --- Execution history ---

export async function getExecutionHistory(agentName: string, limit = 50): Promise<AgentExecution[]> {
  const { data } = await apiClient.get<AgentExecution[]>(`${V2}/${agentName}/history`, { params: { limit } });
  return data;
}

// --- Findings ---

export async function getFindingsSummary(): Promise<AgentFindingSummary> {
  const { data } = await apiClient.get<AgentFindingSummary>(`${V2}/findings/summary`);
  return data;
}

export async function getAgentFindings(agentName: string, filters?: FindingFilters): Promise<AgentFinding[]> {
  const { data } = await apiClient.get<AgentFinding[]>(`${V2}/${agentName}/findings`, { params: filters });
  return data;
}

export async function updateFindingStatus(
  findingId: string,
  action: ApprovalAction,
): Promise<ApprovalResult> {
  const { data } = await apiClient.patch<ApprovalResult>(`${V2}/findings/${findingId}`, action);
  return data;
}

// --- Policies ---

export async function getPolicies(): Promise<Policy[]> {
  const { data } = await apiClient.get<Policy[]>(`${V2}/policies`);
  return data;
}

export async function createPolicy(policy: Omit<Policy, 'id' | 'created_at' | 'updated_at'>): Promise<Policy> {
  const { data } = await apiClient.post<Policy>(`${V2}/policies`, policy);
  return data;
}

export async function updatePolicy(id: string, update: Partial<Policy>): Promise<Policy> {
  const { data } = await apiClient.put<Policy>(`${V2}/policies/${id}`, update);
  return data;
}

export async function deletePolicy(id: string): Promise<void> {
  await apiClient.delete(`${V2}/policies/${id}`);
}

// --- Residency policies ---

export async function getResidencyPolicies(): Promise<ResidencyPolicy[]> {
  const { data } = await apiClient.get<ResidencyPolicy[]>(`${V2}/residency-policies`);
  return data;
}

export async function createResidencyPolicy(policy: Omit<ResidencyPolicy, 'id' | 'created_at' | 'updated_at'>): Promise<ResidencyPolicy> {
  const { data } = await apiClient.post<ResidencyPolicy>(`${V2}/residency-policies`, policy);
  return data;
}

export async function updateResidencyPolicy(id: string, update: Partial<ResidencyPolicy>): Promise<ResidencyPolicy> {
  const { data } = await apiClient.put<ResidencyPolicy>(`${V2}/residency-policies/${id}`, update);
  return data;
}

export async function deleteResidencyPolicy(id: string): Promise<void> {
  await apiClient.delete(`${V2}/residency-policies/${id}`);
}


// --- Chargeback ---

const CB = '/v2/chargeback';

export async function uploadChargebackCSV(file: File) {
  const form = new FormData();
  form.append('file', file);
  const { data } = await apiClient.post(`${CB}/uploads`, form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  });
  return data;
}

export async function getChargebackUploads() {
  const { data } = await apiClient.get(`${CB}/uploads`);
  return data;
}

export async function parseChargebackUpload(uploadId: string) {
  const { data } = await apiClient.post(`${CB}/uploads/${uploadId}/parse`);
  return data;
}

export async function generateChargebackReport(uploadId: string) {
  const { data } = await apiClient.post(`${CB}/uploads/${uploadId}/report`);
  return data;
}

export async function getChargebackReport(reportId: string) {
  const { data } = await apiClient.get(`${CB}/reports/${reportId}`);
  return data;
}

export async function exportChargebackReport(reportId: string): Promise<Blob> {
  const { data } = await apiClient.get(`${CB}/reports/${reportId}/export`, {
    responseType: 'blob',
  });
  return data;
}

export async function deleteChargebackUpload(uploadId: string): Promise<void> {
  await apiClient.delete(`${CB}/uploads/${uploadId}`);
}
