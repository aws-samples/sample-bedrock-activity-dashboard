import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import ModelUsageTab from './ModelUsageTab';
import type { DashboardFilters, ModelUsageSummary, UserModelCell } from '../types';

// Mock recharts to avoid SVG rendering issues in jsdom
vi.mock('recharts', () => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports -- vi.mock factory is hoisted above ESM imports; require() avoids the TDZ on a top-level React import
  const React = require('react');
  const Box = ({ children }: { children?: React.ReactNode }) =>
    React.createElement('div', null, children);
  const Empty = () => React.createElement('div');
  return {
    ResponsiveContainer: ({ children }: { children: React.ReactNode }) =>
      React.createElement('div', { 'data-testid': 'responsive-container' }, children),
    // ModelUsageTab renders a horizontal BarChart; mock every recharts export
    // it uses so the chart subtree renders inertly in jsdom.
    BarChart: Box,
    Bar: Box,
    XAxis: Empty,
    YAxis: Empty,
    CartesianGrid: Empty,
    LabelList: Empty,
    PieChart: ({ children }: { children: React.ReactNode }) =>
      React.createElement('div', { 'data-testid': 'pie-chart' }, children),
    Pie: Empty,
    Cell: Empty,
    Tooltip: Empty,
    Legend: Empty,
  };
});

vi.mock('../api/client', () => ({
  getModelUsage: vi.fn(),
}));

import { getModelUsage } from '../api/client';

const mockedGetModelUsage = vi.mocked(getModelUsage);

const mockFilters: DashboardFilters = {
  dateRange: { start: '2025-01-01', end: '2025-01-07' },
  userIdentities: [],
  teamId: null,
  sourceApplication: null,
};

// ---- Faithful reconstruction of the user's ACTUAL live response ----
const ROLE = 'arn:aws:sts::123456789012:assumed-role/BedrockDashboard-Backend-de-BackendTaskRole65DD5890-gOAhilEerqb3';
const ARN_D7 = `${ROLE}/d7207402b0df472299cf801ff07f8d4f`;
const ARN_A4 = `${ROLE}/a439f00dcafe4b1a9b2c3d4e5f6a7b8c`;
const ARN_F9 = `${ROLE}/f9aeea861c804a7e910ae84786254526`;

const demoUser = (name: string) => ({
  userIdentity: `arn:aws:iam::000000000000:user/demo.${name}`,
  userName: name,
});

// ~14 model columns including duplicated prefixed + bare pairs
const M = {
  novaProPfx: 'us.amazon.nova-pro-v1:0',
  novaProBare: 'amazon.nova-pro-v1:0',
  novaLitePfx: 'us.amazon.nova-lite-v1:0',
  novaLiteBare: 'amazon.nova-lite-v1:0',
  novaMicroPfx: 'us.amazon.nova-micro-v1:0',
  novaMicroBare: 'amazon.nova-micro-v1:0',
  llamaPfx: 'us.meta.llama3-3-70b-instruct-v1:0',
  claude: 'anthropic.claude-sonnet-4-5-20250929-v1:0',
  claudeHaiku: 'anthropic.claude-3-5-haiku-20241022-v1:0',
  claudeSonnet37: 'anthropic.claude-3-7-sonnet-20250219-v1:0',
  titan: 'amazon.titan-text-express-v1',
  cohere: 'cohere.command-r-v1:0',
  mistral: 'mistral.mistral-large-2407-v1:0',
  novaPremier: 'us.amazon.nova-premier-v1:0',
};

const mockModels: ModelUsageSummary[] = Object.values(M).map((modelId, i) => ({
  modelId,
  invocationCount: (i % 5) + 1,
  inputTokens: 1000 * (i + 1),
  outputTokens: 500 * (i + 1),
  cacheReadTokens: 0,
  cacheWriteTokens: 0,
  totalTokens: 1500 * (i + 1),
  costEstimate: i === 3 ? null : 0.1 * (i + 1),
  percentageOfTotal: 100 / 14,
}));

const cell = (
  user: { userIdentity: string; userName: string },
  modelId: string,
  invocationCount: number,
  costEstimate: UserModelCell['costEstimate'] | unknown = 0.01
): UserModelCell =>
  ({
    userIdentity: user.userIdentity,
    userName: user.userName,
    modelId,
    invocationCount,
    costEstimate,
  } as UserModelCell);

const U = {
  d7: { userIdentity: ARN_D7, userName: 'd7207402b0df472299cf801ff07f8d4f' },
  a4: { userIdentity: ARN_A4, userName: 'a439f00dcafe4b1a9b2c3d4e5f6a7b8c' },
  f9: { userIdentity: ARN_F9, userName: 'f9aeea861c804a7e910ae84786254526' },
  amelia: demoUser('amelia.chen'),
  ben: demoUser('ben.wong'),
  carla: demoUser('carla.diaz'),
  dan: demoUser('dan.smith'),
  eve: demoUser('eve.jones'),
  finn: demoUser('finn.oliver'),
  grace: demoUser('grace.lee'),
  hank: demoUser('hank.miller'),
  demoReal: { userIdentity: 'demo-real-invocation', userName: 'Demo (real invocation)' },
  agent: { userIdentity: 'agent-chat', userName: 'Agent Chat User' },
};

const mockMatrix: UserModelCell[] = [
  cell(U.d7, M.novaProPfx, 2, 0.5),
  cell(U.a4, M.novaProPfx, 4, 0.9),
  cell(U.f9, M.novaLitePfx, 5, 0.3),
  cell(U.f9, M.novaMicroPfx, 5, 0.1),
  cell(U.f9, M.llamaPfx, 5, 0.7),
  cell(U.f9, M.novaProPfx, 2, 0.4),
  cell(U.amelia, M.claude, 2, 0.6),
  // CRITICAL anomaly: costEstimate is an OBJECT, not a number
  cell(U.amelia, M.novaMicroBare, 1, { source: '5.586e-05', parsedValue: 0.00005586 }),
  cell(U.ben, M.claudeHaiku, 3, 0.2),
  cell(U.carla, M.titan, 6, 0.15),
  cell(U.dan, M.cohere, 2, 0.12),
  cell(U.eve, M.mistral, 4, 0.33),
  cell(U.finn, M.novaProBare, 3, 0.44),
  cell(U.grace, M.claudeSonnet37, 2, 0.55),
  cell(U.hank, M.novaLiteBare, 7, 0.22),
  cell(U.demoReal, M.novaPremier, 1, 0.05),
  cell(U.agent, M.claude, 9, 1.11),
];

beforeEach(() => {
  vi.clearAllMocks();
});

function matrixRowFor(userName: string): HTMLElement {
  const cellEl = screen.getByText(userName);
  const row = cellEl.closest('[data-testid^="user-usage-"]');
  if (!row) throw new Error(`No block found for user ${userName}`);
  return row as HTMLElement;
}

describe('ModelUsageTab — full live payload (11+ users, object costEstimate)', () => {
  it('renders every non-zero invocationCount in the correct per-user block without throwing', async () => {
    mockedGetModelUsage.mockResolvedValue({ models: mockModels, matrix: mockMatrix });
    render(<ModelUsageTab filters={mockFilters} />);

    await waitFor(() => {
      expect(screen.getByText('User Model Usage')).toBeTruthy();
    });

    // d7207402 × us.amazon.nova-pro-v1:0 → 2
    expect(within(matrixRowFor(U.d7.userName)).getByText('2')).toBeTruthy();
    // a439 × us.amazon.nova-pro-v1:0 → 4
    expect(within(matrixRowFor(U.a4.userName)).getByText('4')).toBeTruthy();
    // f9aeea → three 5's and a 2
    const f9 = matrixRowFor(U.f9.userName);
    expect(within(f9).getAllByText('5').length).toBe(3);
    expect(within(f9).getByText('2')).toBeTruthy();
    // amelia.chen × claude → 2, and the anomalous object-costEstimate cell → 1
    const amelia = matrixRowFor(U.amelia.userName);
    expect(within(amelia).getByText('2')).toBeTruthy();
    expect(within(amelia).getByText('1')).toBeTruthy();
    // spot check other users
    expect(within(matrixRowFor(U.carla.userName)).getByText('6')).toBeTruthy();
    expect(within(matrixRowFor(U.hank.userName)).getByText('7')).toBeTruthy();
    expect(within(matrixRowFor(U.agent.userName)).getByText('9')).toBeTruthy();
  });

  it('renders cost cells for the object-costEstimate anomaly when its model row is expanded', async () => {
    mockedGetModelUsage.mockResolvedValue({ models: mockModels, matrix: mockMatrix });
    const { container } = render(<ModelUsageTab filters={mockFilters} />);

    await waitFor(() => {
      expect(screen.getByText('Model Summary')).toBeTruthy();
    });

    // Expand the bare nova-micro model row (which has amelia's object-costEstimate cell)
    const row = container.querySelector(`[data-testid="model-row-${M.novaMicroBare}"]`) as HTMLElement | null;
    expect(row).toBeTruthy();
    row!.click();

    // Should not throw; amelia should appear in the expanded per-user table
    await waitFor(() => {
      const expanded = container.querySelector(`[data-testid="model-users-${M.novaMicroBare}"]`);
      expect(expanded).toBeTruthy();
    });
  });
});
