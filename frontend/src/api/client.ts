import axios from 'axios';
import { clearAuth, redirectToLogin } from '../auth/cognito';
import type {
  ActivityRecord,
  OverviewSummary,
  UserTokenUsage,
  DailyTokenUsage,
  ModelUsageSummary,
  UserModelCell,
  CostAnalysis,
  PromptHistoryEntry,
  PromptDetail,
  PaginatedResponse,
  Team,
  ModelPricingConfig,
  PricingSyncSummary,
  PricingGapsResponse,
  DashboardFilters,
  ExportRequest,
  AgentChatRequest,
  AgentChatResponse,
  SummaryReport,
  SpendBreakdownResponse,
} from '../types';

// 60s timeout: without this, axios has no default (0 = never times out), so a
// slow backend ingestion pass (e.g. a wide 30d/90d date range scanning many
// days of S3 historical logs) leaves the caller's request promise pending
// forever — the UI just spins with no error rather than surfacing a failure.
const apiClient = axios.create({
  baseURL: '/api',
  timeout: 60_000,
  headers: {
    'Content-Type': 'application/json',
  },
});

const TOKENS_KEY = 'cognito_tokens';

/** Read the stored Cognito ID token, if any. */
function getStoredIdToken(): string | null {
  try {
    const raw = localStorage.getItem(TOKENS_KEY);
    if (!raw) return null;
    const tokens = JSON.parse(raw) as { id_token?: string };
    return tokens.id_token ?? null;
  } catch {
    return null;
  }
}

// Request interceptor: attach the Cognito ID token as a Bearer credential.
apiClient.interceptors.request.use((config) => {
  const idToken = getStoredIdToken();
  if (idToken) {
    config.headers = config.headers ?? {};
    config.headers.Authorization = `Bearer ${idToken}`;
  }
  return config;
});

// Response interceptor: on 401 the token is missing/invalid/expired — clear
// stored auth and bounce the user back through the Cognito login flow.
apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error?.response?.status === 401) {
      clearAuth();
      // redirectToLogin awaits /api/health/config; if that fetch fails the
      // rejection would otherwise be unhandled and the user would sit on a
      // broken page. Reload as a last-resort fallback — LoginPage handles the
      // config-load failure case with a real error UI.
      redirectToLogin().catch(() => {
        window.location.reload();
      });
    }
    return Promise.reject(error);
  }
);

/** Convert DashboardFilters to URL query params */
export function filtersToParams(filters: DashboardFilters): Record<string, string> {
  const params: Record<string, string> = {};
  if (filters.dateRange.start) params.start_date = filters.dateRange.start;
  if (filters.dateRange.end) params.end_date = filters.dateRange.end;
  if (filters.userIdentities.length > 0)
    params.user_identities = filters.userIdentities.join(',');
  if (filters.teamId) params.team_id = filters.teamId;
  if (filters.sourceApplication) params.source_application = filters.sourceApplication;
  if (filters.regions && filters.regions.length > 0) params.regions = filters.regions.join(',');
  return params;
}

// ---------------------------------------------------------------------------
// Activity endpoints
// ---------------------------------------------------------------------------

/** GET /api/activity — aggregated activity data */
export async function getActivity(filters: DashboardFilters): Promise<ActivityRecord[]> {
  const { data } = await apiClient.get<ActivityRecord[]>('/activity', {
    params: filtersToParams(filters),
  });
  return data;
}

/** GET /api/activity/overview — top-level summary */
export async function getOverview(filters: DashboardFilters): Promise<OverviewSummary> {
  const { data } = await apiClient.get<OverviewSummary>('/activity/overview', {
    params: filtersToParams(filters),
  });
  return data;
}

/** GET /api/activity/tokens — token usage summary and daily breakdown */
export async function getTokenUsage(
  filters: DashboardFilters
): Promise<{ users: UserTokenUsage[]; daily: DailyTokenUsage[] }> {
  const { data } = await apiClient.get<{ byUser: UserTokenUsage[]; dailyBreakdown: DailyTokenUsage[] }>(
    '/activity/tokens',
    { params: filtersToParams(filters) }
  );
  return { users: data.byUser ?? [], daily: data.dailyBreakdown ?? [] };
}

/** GET /api/activity/models — model usage breakdown and user-to-model matrix */
export async function getModelUsage(
  filters: DashboardFilters
): Promise<{ models: ModelUsageSummary[]; matrix: UserModelCell[] }> {
  const { data } = await apiClient.get<{ models: ModelUsageSummary[]; userModelMatrix: UserModelCell[] }>(
    '/activity/models',
    { params: filtersToParams(filters) }
  );
  return { models: data.models ?? [], matrix: data.userModelMatrix ?? [] };
}

/** GET /api/activity/costs — cost analysis */
export async function getCostAnalysis(filters: DashboardFilters): Promise<CostAnalysis> {
  const { data } = await apiClient.get<CostAnalysis>('/activity/costs', {
    params: filtersToParams(filters),
  });
  return data;
}

/** GET /api/activity/users — unique users with stats */
export async function getUsers(
  filters: DashboardFilters
): Promise<{ userIdentity: string; userName: string; invocationCount: number; totalTokens: number; costEstimate: number | null }[]> {
  const { data } = await apiClient.get<{ users: { userIdentity: string; userName: string; invocationCount: number; totalTokens: number; costEstimate: number | null }[] }>(
    '/activity/users',
    { params: filtersToParams(filters) }
  );
  return data.users ?? [];
}

/** GET /api/activity/sources — source application breakdown */
export async function getSourceBreakdown(
  filters: DashboardFilters
): Promise<{ source: string; invocationCount: number; totalTokens: number; costEstimate: number | null }[]> {
  const { data } = await apiClient.get('/activity/sources', {
    params: filtersToParams(filters),
  });
  return data;
}

/**
 * GET /api/activity/categories — spend breakdown by content category
 * (falling back to the Application tag, then "Uncategorized") and by model,
 * powering the Overview tab's nested + standalone donut charts.
 */
export async function getSpendBreakdown(
  filters: DashboardFilters
): Promise<SpendBreakdownResponse> {
  const { data } = await apiClient.get<SpendBreakdownResponse>('/activity/categories', {
    params: filtersToParams(filters),
  });
  return data;
}

// ---------------------------------------------------------------------------
// Prompt endpoints
// ---------------------------------------------------------------------------

/** GET /api/activity/prompts — paginated prompt history */
export async function getPromptHistory(
  filters: DashboardFilters,
  page = 1,
  pageSize = 25
): Promise<PaginatedResponse<PromptHistoryEntry>> {
  const { data } = await apiClient.get<PaginatedResponse<PromptHistoryEntry>>(
    '/activity/prompts',
    { params: { ...filtersToParams(filters), page, page_size: pageSize } }
  );
  return data;
}

/** GET /api/activity/prompts/:id — full prompt detail */
export async function getPromptDetail(id: string, filters?: DashboardFilters): Promise<PromptDetail> {
  const { data } = await apiClient.get<PromptDetail>(`/activity/prompts/${id}`, {
    params: filters ? filtersToParams(filters) : undefined,
  });
  return data;
}

// ---------------------------------------------------------------------------
// Export endpoint
// ---------------------------------------------------------------------------

/** GET /api/activity/export — CSV export (returns Blob for download) */
export async function exportCsv(request: ExportRequest): Promise<Blob> {
  const { data } = await apiClient.get('/activity/export', {
    params: { section: request.section, ...filtersToParams(request.filters) },
    responseType: 'blob',
  });
  return data as Blob;
}

// ---------------------------------------------------------------------------
// Team endpoints
// ---------------------------------------------------------------------------

/** GET /api/teams */
export async function getTeams(): Promise<Team[]> {
  const { data } = await apiClient.get<Team[]>('/teams');
  return data;
}

/** POST /api/teams */
export async function createTeam(
  team: Pick<Team, 'name' | 'description' | 'members'>
): Promise<Team> {
  const { data } = await apiClient.post<Team>('/teams', team);
  return data;
}

/** PUT /api/teams/:id */
export async function updateTeam(
  id: string,
  team: Pick<Team, 'name' | 'description' | 'members'>
): Promise<Team> {
  const { data } = await apiClient.put<Team>(`/teams/${id}`, team);
  return data;
}

/** DELETE /api/teams/:id */
export async function deleteTeam(id: string): Promise<void> {
  await apiClient.delete(`/teams/${id}`);
}

// ---------------------------------------------------------------------------
// Pricing endpoints
// ---------------------------------------------------------------------------

/** GET /api/pricing */
export async function getPricing(): Promise<ModelPricingConfig[]> {
  const { data } = await apiClient.get<ModelPricingConfig[]>('/pricing');
  return data;
}

/** PUT /api/pricing/:modelId */
export async function updatePricing(
  modelId: string,
  pricing: Omit<ModelPricingConfig, 'modelId' | 'isDefault'>
): Promise<ModelPricingConfig> {
  const { data } = await apiClient.put<ModelPricingConfig>(
    `/pricing/${encodeURIComponent(modelId)}`,
    pricing
  );
  return data;
}

/** POST /api/pricing/sync — sync pricing from the AWS Price List API (admin only) */
export async function syncPricing(): Promise<PricingSyncSummary> {
  const { data } = await apiClient.post<PricingSyncSummary>('/pricing/sync');
  return data;
}

/**
 * GET /api/pricing/usage-gaps — distinct models actually invoked that have no
 * resolvable pricing row today (would show cost as "N/A"). Defaults to the
 * last 30 days when no window is given.
 */
export async function getUsagePricingGaps(
  startDate?: string,
  endDate?: string
): Promise<PricingGapsResponse> {
  const params: Record<string, string> = {};
  if (startDate) params.start_date = startDate;
  if (endDate) params.end_date = endDate;
  const { data } = await apiClient.get<PricingGapsResponse>('/pricing/usage-gaps', { params });
  return data;
}

// ---------------------------------------------------------------------------
// Agent endpoints
// ---------------------------------------------------------------------------

/** POST /api/agent/chat — send question with filters, return agent response */
export async function agentChat(request: AgentChatRequest): Promise<AgentChatResponse> {
  const { data } = await apiClient.post<AgentChatResponse>('/agent/chat', request);
  return data;
}

/** Error surfaced by the streaming endpoint's `[ERROR:{...}]` event. */
export interface AgentStreamError {
  code: string;
  message: string;
}

/** Callbacks + control for {@link agentChatStream}. */
export interface AgentChatStreamHandlers {
  /** Fired for each thinking chunk (newlines already un-escaped). */
  onThinking?: (chunk: string) => void;
  /** Fired for each answer chunk (newlines already un-escaped). */
  onText?: (chunk: string) => void;
  /** Fired once if the model reports extended thinking is unavailable. */
  onThinkingUnavailable?: () => void;
  /** Fired once if the dashboard's managed Bedrock Guardrail blocked or
   * anonymized content in this turn. */
  onGuardrailIntervened?: () => void;
  /** Fired once when the stream completes, carrying the session id. */
  onDone?: (sessionId: string | null) => void;
  /** Fired if the backend emits an error event or the request fails. */
  onError?: (error: AgentStreamError) => void;
  /** Abort signal — aborting cancels the in-flight fetch and reader. */
  signal?: AbortSignal;
}

/** Strip a single leading `data:` (and optional following space) from an SSE line. */
function stripDataPrefix(line: string): string {
  if (!line.startsWith('data:')) return line;
  const rest = line.slice(5);
  return rest.startsWith(' ') ? rest.slice(1) : rest;
}

/** Un-escape the `\n` sequences the backend uses to keep chunks single-line. */
function unescapeNewlines(text: string): string {
  return text.replace(/\\n/g, '\n');
}

/**
 * POST /api/agent/chat/stream — consume the Server-Sent Events stream.
 *
 * EventSource can't issue a POST, so this uses `fetch` against the SAME base URL
 * the axios client uses and attaches the SAME Cognito ID token Bearer credential
 * the axios request interceptor attaches (reusing {@link getStoredIdToken} rather
 * than hardcoding anything). It reads the response body reader, splits on the SSE
 * `\n\n` delimiter, strips the `data:` prefix, and dispatches by prefix:
 *   - `[THINKING]<text>`        → onThinking (newlines un-escaped)
 *   - `[THINKING_UNAVAILABLE]`  → onThinkingUnavailable
 *   - `[GUARDRAIL_INTERVENED]`  → onGuardrailIntervened
 *   - `[DONE:<sessionId>]`      → onDone(sessionId)
 *   - `[ERROR:{json}]`          → onError({code, message})
 *   - anything else             → onText (answer chunk, newlines un-escaped)
 */
export async function agentChatStream(
  request: AgentChatRequest,
  handlers: AgentChatStreamHandlers
): Promise<void> {
  const { onThinking, onText, onThinkingUnavailable, onGuardrailIntervened, onDone, onError, signal } = handlers;
  const baseURL = apiClient.defaults.baseURL ?? '/api';
  const idToken = getStoredIdToken();

  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (idToken) headers.Authorization = `Bearer ${idToken}`;

  let response: Response;
  try {
    response = await fetch(`${baseURL}/agent/chat/stream`, {
      method: 'POST',
      headers,
      body: JSON.stringify(request),
      signal,
    });
  } catch (err) {
    // Aborts are expected when the user hits Stop / unmounts — stay quiet.
    if (err instanceof DOMException && err.name === 'AbortError') return;
    onError?.({ code: 'NETWORK_ERROR', message: err instanceof Error ? err.message : 'Network error' });
    return;
  }

  // Mirror the axios 401 handling so an expired token bounces through login.
  if (response.status === 401) {
    clearAuth();
    void redirectToLogin();
    onError?.({ code: 'UNAUTHORIZED', message: 'Your session has expired. Please sign in again.' });
    return;
  }

  if (!response.ok || !response.body) {
    let code = `HTTP_${response.status}`;
    let message = `Request failed with status ${response.status}`;
    try {
      const body = await response.json();
      const err = body?.detail?.error ?? body?.error;
      if (err?.code) code = err.code;
      if (err?.message) message = err.message;
    } catch {
      // non-JSON body — keep the generic message
    }
    onError?.({ code, message });
    return;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  const dispatch = (rawEvent: string): void => {
    // An event may span multiple `data:` lines; join their payloads.
    const payload = rawEvent
      .split('\n')
      .filter((l) => l.length > 0)
      .map(stripDataPrefix)
      .join('');
    if (!payload) return;

    if (payload.startsWith('[THINKING_UNAVAILABLE]')) {
      onThinkingUnavailable?.();
      return;
    }
    if (payload.startsWith('[GUARDRAIL_INTERVENED]')) {
      onGuardrailIntervened?.();
      return;
    }
    if (payload.startsWith('[THINKING]')) {
      onThinking?.(unescapeNewlines(payload.slice('[THINKING]'.length)));
      return;
    }
    if (payload.startsWith('[DONE:')) {
      const sessionId = payload.slice('[DONE:'.length, payload.lastIndexOf(']'));
      onDone?.(sessionId || null);
      return;
    }
    if (payload.startsWith('[ERROR:')) {
      const json = payload.slice('[ERROR:'.length, payload.lastIndexOf(']'));
      try {
        const parsed = JSON.parse(json) as Partial<AgentStreamError>;
        onError?.({ code: parsed.code ?? 'ERROR', message: parsed.message ?? 'Agent error' });
      } catch {
        onError?.({ code: 'ERROR', message: 'Agent error' });
      }
      return;
    }
    onText?.(unescapeNewlines(payload));
  };

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let sep: number;
      while ((sep = buffer.indexOf('\n\n')) !== -1) {
        const rawEvent = buffer.slice(0, sep);
        buffer = buffer.slice(sep + 2);
        if (rawEvent.trim().length > 0) dispatch(rawEvent);
      }
    }
    // Flush any trailing event without a terminating blank line.
    const tail = buffer.trim();
    if (tail.length > 0) dispatch(tail);
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') return;
    onError?.({ code: 'STREAM_ERROR', message: err instanceof Error ? err.message : 'Stream error' });
  }
}


/** POST /api/agent/reports — trigger summary report generation */
export async function generateReport(
  periodStart: string,
  periodEnd: string,
  reportType: 'daily' | 'weekly'
): Promise<SummaryReport> {
  const { data } = await apiClient.post<SummaryReport>('/agent/reports', {
    periodStart,
    periodEnd,
    reportType,
  });
  return data;
}

/** GET /api/agent/reports — list generated reports */
export async function getReports(): Promise<SummaryReport[]> {
  const { data } = await apiClient.get<SummaryReport[]>('/agent/reports');
  return data;
}

/** GET /api/agent/reports/:id — retrieve specific report */
export async function getReport(id: string): Promise<SummaryReport> {
  const { data } = await apiClient.get<SummaryReport>(`/agent/reports/${id}`);
  return data;
}

// ---------------------------------------------------------------------------
// Health endpoints
// ---------------------------------------------------------------------------

export interface SystemCheck {
  name: string;
  status: 'healthy' | 'unhealthy' | 'warning';
  detail: string;
}

export interface SystemHealth {
  overall: 'healthy' | 'degraded' | 'unhealthy';
  timestamp: string;
  region: string;
  systems: SystemCheck[];
}

/** GET /api/health/systems — system health (admin only) */
export async function getSystemHealth(): Promise<SystemHealth> {
  const { data } = await apiClient.get<SystemHealth>('/health/systems');
  return data;
}

export default apiClient;