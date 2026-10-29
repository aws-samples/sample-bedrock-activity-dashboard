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

// Representative subset shaped like the live /api/activity/models response:
// - assumed-role ARN users (real CloudWatch/CloudTrail) with cross-region `us.` prefixed model ids
// - demo/synthetic users (amelia.chen) with non-prefixed model ids
const ARN_D7207402 =
  'arn:aws:sts::123456789012:assumed-role/BedrockDashboard-Backend-de-BackendTaskRole65DD5890-gOAhilEerqb3/d7207402b0df472299cf801ff07f8d4f';
const ARN_F9AEEA =
  'arn:aws:sts::123456789012:assumed-role/BedrockDashboard-Backend-de-BackendTaskRole65DD5890-gOAhilEerqb3/f9aeea861c804a7e910ae84786254526';

const mockModels: ModelUsageSummary[] = [
  {
    modelId: 'us.amazon.nova-pro-v1:0',
    invocationCount: 2,
    inputTokens: 1000,
    outputTokens: 500,
    cacheReadTokens: 0,
    cacheWriteTokens: 0,
    totalTokens: 1500,
    costEstimate: 0.5,
    percentageOfTotal: 10.0,
  },
  {
    modelId: 'us.amazon.nova-lite-v1:0',
    invocationCount: 5,
    inputTokens: 2000,
    outputTokens: 1000,
    cacheReadTokens: 0,
    cacheWriteTokens: 0,
    totalTokens: 3000,
    costEstimate: 0.3,
    percentageOfTotal: 25.0,
  },
  {
    modelId: 'anthropic.claude-sonnet-4-5-20250929-v1:0',
    invocationCount: 2,
    inputTokens: 800,
    outputTokens: 400,
    cacheReadTokens: 0,
    cacheWriteTokens: 0,
    totalTokens: 1200,
    costEstimate: 1.2,
    percentageOfTotal: 10.0,
  },
];

const mockMatrix: UserModelCell[] = [
  // d7207402 (assumed-role ARN) × us.amazon.nova-pro-v1:0 → 2
  {
    userIdentity: ARN_D7207402,
    userName: 'd7207402b0df472299cf801ff07f8d4f',
    modelId: 'us.amazon.nova-pro-v1:0',
    invocationCount: 2,
    costEstimate: 0.5,
  },
  // f9aeea (assumed-role ARN) × three prefixed models → 5 each
  {
    userIdentity: ARN_F9AEEA,
    userName: 'f9aeea861c804a7e910ae84786254526',
    modelId: 'us.amazon.nova-lite-v1:0',
    invocationCount: 5,
    costEstimate: 0.3,
  },
  {
    userIdentity: ARN_F9AEEA,
    userName: 'f9aeea861c804a7e910ae84786254526',
    modelId: 'us.amazon.nova-micro-v1:0',
    invocationCount: 5,
    costEstimate: 0.1,
  },
  {
    userIdentity: ARN_F9AEEA,
    userName: 'f9aeea861c804a7e910ae84786254526',
    modelId: 'us.meta.llama3-3-70b-instruct-v1:0',
    invocationCount: 5,
    costEstimate: 0.7,
  },
  // amelia.chen (demo user) × non-prefixed + prefixed claude → 2
  {
    userIdentity: 'amelia.chen',
    userName: 'amelia.chen',
    modelId: 'anthropic.claude-sonnet-4-5-20250929-v1:0',
    invocationCount: 2,
    costEstimate: 1.2,
  },
  {
    userIdentity: 'amelia.chen',
    userName: 'amelia.chen',
    modelId: 'amazon.nova-pro-v1:0',
    invocationCount: 3,
    costEstimate: 0.4,
  },
];

beforeEach(() => {
  vi.clearAllMocks();
});

/** Locate the compact per-user block whose header matches userName. */
function matrixRowFor(userName: string): HTMLElement {
  const cell = screen.getByText(userName);
  const row = cell.closest('[data-testid^="user-usage-"]');
  if (!row) throw new Error(`No block found for user ${userName}`);
  return row as HTMLElement;
}

describe('ModelUsageTab — per-user model usage counts (live-shaped payload)', () => {
  it('renders the served non-zero invocationCount for each model a user used', async () => {
    mockedGetModelUsage.mockResolvedValue({ models: mockModels, matrix: mockMatrix });
    render(<ModelUsageTab filters={mockFilters} />);

    await waitFor(() => {
      expect(screen.getByText('User Model Usage')).toBeTruthy();
    });

    // d7207402 used us.amazon.nova-pro-v1:0 → 2 (only model it used)
    const d7Row = matrixRowFor('d7207402b0df472299cf801ff07f8d4f');
    expect(within(d7Row).getByText('2')).toBeTruthy();

    // f9aeea used three prefixed models → 5 each (exactly three "5" counts)
    const f9Row = matrixRowFor('f9aeea861c804a7e910ae84786254526');
    expect(within(f9Row).getAllByText('5').length).toBe(3);

    // amelia.chen used claude-sonnet → 2 and amazon.nova-pro → 3
    const ameliaRow = matrixRowFor('amelia.chen');
    expect(within(ameliaRow).getByText('2')).toBeTruthy();
    expect(within(ameliaRow).getByText('3')).toBeTruthy();
  });
});
