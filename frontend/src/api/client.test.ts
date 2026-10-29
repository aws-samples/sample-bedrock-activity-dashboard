import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import type { AxiosResponse, InternalAxiosRequestConfig } from 'axios';

// Mock the Cognito helpers so the 401 handler doesn't trigger real redirects.
vi.mock('../auth/cognito', () => ({
  clearAuth: vi.fn(),
  redirectToLogin: vi.fn(() => Promise.resolve()),
}));

import apiClient, { filtersToParams, agentChatStream } from './client';
import type { AgentStreamError } from './client';
import { clearAuth, redirectToLogin } from '../auth/cognito';
import type { DashboardFilters } from '../types';

describe('apiClient auth interceptors', () => {
  beforeEach(() => {
    localStorage.clear();
    vi.clearAllMocks();
  });

  afterEach(() => {
    apiClient.defaults.adapter = undefined;
  });

  it('attaches the stored Cognito ID token as a Bearer header', async () => {
    localStorage.setItem('cognito_tokens', JSON.stringify({ id_token: 'id-abc' }));
    apiClient.defaults.adapter = async (
      config: InternalAxiosRequestConfig
    ): Promise<AxiosResponse> => ({
      data: config.headers.Authorization ?? null,
      status: 200,
      statusText: 'OK',
      headers: {},
      config,
    });

    const res = await apiClient.get('/whatever');
    expect(res.data).toBe('Bearer id-abc');
  });

  it('sends no Authorization header when no token is stored', async () => {
    apiClient.defaults.adapter = async (
      config: InternalAxiosRequestConfig
    ): Promise<AxiosResponse> => ({
      data: config.headers.Authorization ?? null,
      status: 200,
      statusText: 'OK',
      headers: {},
      config,
    });

    const res = await apiClient.get('/whatever');
    expect(res.data).toBeNull();
  });

  it('clears auth and redirects to login on a 401 response', async () => {
    apiClient.defaults.adapter = async (
      config: InternalAxiosRequestConfig
    ): Promise<AxiosResponse> => {
      const error = Object.assign(new Error('Unauthorized'), {
        config,
        response: {
          status: 401,
          data: {},
          statusText: 'Unauthorized',
          headers: {},
          config,
        },
      });
      return Promise.reject(error);
    };

    await expect(apiClient.get('/whatever')).rejects.toBeTruthy();
    expect(clearAuth).toHaveBeenCalledTimes(1);
    expect(redirectToLogin).toHaveBeenCalledTimes(1);
  });
});

describe('filtersToParams', () => {
  function makeFilters(overrides: Partial<DashboardFilters> = {}): DashboardFilters {
    return {
      dateRange: { start: '', end: '' },
      userIdentities: [],
      teamId: null,
      sourceApplication: null,
      ...overrides,
    };
  }

  it('maps userIdentities to the backend `user_identities` param (comma-joined)', () => {
    const params = filtersToParams(makeFilters({ userIdentities: ['amelia', 'bob'] }));
    expect(params.user_identities).toBe('amelia,bob');
    // Must NOT emit the old, silently-dropped param name.
    expect(params.user).toBeUndefined();
  });

  it('maps sourceApplication to the backend `source_application` param', () => {
    const params = filtersToParams(makeFilters({ sourceApplication: 'Kiro' }));
    expect(params.source_application).toBe('Kiro');
    // Must NOT emit the old, silently-dropped param name.
    expect(params.source_app).toBeUndefined();
  });

  it('maps date range and teamId to start_date/end_date/team_id', () => {
    const params = filtersToParams(
      makeFilters({
        dateRange: { start: '2024-01-01', end: '2024-01-31' },
        teamId: 'team-123',
      })
    );
    expect(params.start_date).toBe('2024-01-01');
    expect(params.end_date).toBe('2024-01-31');
    expect(params.team_id).toBe('team-123');
  });

  it('omits empty/blank filter values', () => {
    const params = filtersToParams(makeFilters());
    expect(params.user_identities).toBeUndefined();
    expect(params.source_application).toBeUndefined();
    expect(params.team_id).toBeUndefined();
    expect(params.start_date).toBeUndefined();
    expect(params.end_date).toBeUndefined();
  });
});

// ---------------------------------------------------------------------------
// agentChatStream — SSE consumption
// ---------------------------------------------------------------------------

function sseStream(chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (const c of chunks) controller.enqueue(encoder.encode(c));
      controller.close();
    },
  });
}

const streamFilters: DashboardFilters = {
  dateRange: { start: '2025-01-01', end: '2025-01-07' },
  userIdentities: [],
  teamId: null,
  sourceApplication: null,
};

describe('agentChatStream', () => {
  const originalFetch = global.fetch;

  beforeEach(() => {
    localStorage.clear();
    vi.clearAllMocks();
  });

  afterEach(() => {
    global.fetch = originalFetch;
  });

  it('dispatches thinking, text and done events; un-escapes newlines and captures sessionId', async () => {
    localStorage.setItem('cognito_tokens', JSON.stringify({ id_token: 'id-xyz' }));

    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      body: sseStream([
        'data: [THINKING]Reason\\nmore\n\n',
        'data: Hello ',
        'world\n\n',
        'data: [DONE:sess-9]\n\n',
      ]),
    } as unknown as Response);
    global.fetch = fetchMock as unknown as typeof fetch;

    const thinking: string[] = [];
    const text: string[] = [];
    let doneSession: string | null | undefined;
    let errored: AgentStreamError | undefined;

    await agentChatStream(
      { sessionId: null, message: 'hi', filters: streamFilters },
      {
        onThinking: (c) => thinking.push(c),
        onText: (c) => text.push(c),
        onDone: (sid) => {
          doneSession = sid;
        },
        onError: (e) => {
          errored = e;
        },
      }
    );

    // Reused the axios base URL + Bearer id_token (no hardcoding).
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('/api/agent/chat/stream');
    expect((init.headers as Record<string, string>).Authorization).toBe('Bearer id-xyz');
    expect(init.method).toBe('POST');

    // Newlines within the thinking chunk are un-escaped to a real newline.
    expect(thinking.join('')).toBe('Reason\nmore');
    // Text chunks arrive split across SSE events but concatenate to the answer.
    expect(text.join('')).toBe('Hello world');
    expect(doneSession).toBe('sess-9');
    expect(errored).toBeUndefined();
  });

  it('surfaces an [ERROR:{...}] event via onError', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      body: sseStream([
        'data: [ERROR:{"code":"BEDROCK_ERROR","message":"boom"}]\n\n',
      ]),
    } as unknown as Response);
    global.fetch = fetchMock as unknown as typeof fetch;

    let errored: AgentStreamError | undefined;
    const text: string[] = [];

    await agentChatStream(
      { sessionId: null, message: 'hi', filters: streamFilters },
      {
        onText: (c) => text.push(c),
        onError: (e) => {
          errored = e;
        },
      }
    );

    expect(errored).toEqual({ code: 'BEDROCK_ERROR', message: 'boom' });
    expect(text).toEqual([]);
  });

  it('sends no Authorization header when no token is stored', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      body: sseStream(['data: hi\n\n', 'data: [DONE:s1]\n\n']),
    } as unknown as Response);
    global.fetch = fetchMock as unknown as typeof fetch;

    await agentChatStream(
      { sessionId: null, message: 'hi', filters: streamFilters },
      {}
    );

    const [, init] = fetchMock.mock.calls[0];
    expect((init.headers as Record<string, string>).Authorization).toBeUndefined();
  });
});
