import axios from 'axios';

// The shared axios instance in client.ts isn't exported, so we instantiate a
// local one with the same base URL + ID token convention. Keeping it local
// avoids broadening the surface of client.ts for a single new resource.
const api = axios.create({
  baseURL: '/api',
  headers: { 'Content-Type': 'application/json' },
});

const TOKENS_KEY = 'cognito_tokens';

api.interceptors.request.use((config) => {
  try {
    const raw = localStorage.getItem(TOKENS_KEY);
    if (raw) {
      const tokens = JSON.parse(raw) as { id_token?: string };
      if (tokens.id_token) {
        config.headers = config.headers ?? {};
        config.headers.Authorization = `Bearer ${tokens.id_token}`;
      }
    }
  } catch {
    // Ignore storage/parsing errors — request will fail with 401 and the
    // app's main interceptor will handle it.
  }
  return config;
});

/** Response shape of `GET /api/settings/prompt-history`. */
export interface PromptHistorySettings {
  enabled: boolean;
  encryption_available: boolean;
  encryption_error: string | null;
  updated_at: string | null;
  updated_by: string | null;
}

/** Fetch the current Prompt History toggle + encryption posture. */
export async function getPromptHistorySettings(signal?: AbortSignal): Promise<PromptHistorySettings> {
  const { data } = await api.get<PromptHistorySettings>('/settings/prompt-history', { signal });
  return data;
}

/** Flip the Prompt History toggle. Admin-only on the server.
 *
 * The backend rejects attempts to turn the toggle ON unless a valid at-rest
 * encryption key is loaded (HTTP 400 with `error: 'encryption_key_unavailable'`).
 * Callers should surface the resulting error so the admin knows to configure
 * `PROMPT_HISTORY_ENCRYPTION_KEY` before trying again.
 */
export async function updatePromptHistorySettings(
  enabled: boolean,
  signal?: AbortSignal,
): Promise<PromptHistorySettings> {
  const { data } = await api.put<PromptHistorySettings>(
    '/settings/prompt-history',
    { enabled },
    { signal },
  );
  return data;
}

/** Shape of the backend error payload when the gatekeeper rejects enabling. */
export interface PromptHistoryToggleError {
  error: string;
  message: string;
  encryption_error: string | null;
}

/** Extract a human-readable message from an axios error, if any. */
export function extractToggleError(err: unknown): string {
  if (err && typeof err === 'object' && 'response' in err) {
    const resp = (err as { response?: { data?: { detail?: PromptHistoryToggleError | string } } }).response;
    const detail = resp?.data?.detail;
    if (detail && typeof detail === 'object' && 'message' in detail) {
      return detail.message;
    }
    if (typeof detail === 'string') {
      return detail;
    }
  }
  return err instanceof Error ? err.message : 'Failed to update setting';
}
