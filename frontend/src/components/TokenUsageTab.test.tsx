import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import TokenUsageTab from './TokenUsageTab';
import type { DashboardFilters, UserTokenUsage, DailyTokenUsage } from '../types';

// Mock recharts to avoid SVG rendering issues in jsdom
vi.mock('recharts', () => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports -- vi.mock factory is hoisted above ESM imports; require() avoids the TDZ on a top-level React import
  const React = require('react');
  return {
    ResponsiveContainer: ({ children }: { children: React.ReactNode }) =>
      React.createElement('div', { 'data-testid': 'responsive-container' }, children),
    BarChart: ({ children }: { children: React.ReactNode }) =>
      React.createElement('div', { 'data-testid': 'bar-chart' }, children),
    AreaChart: ({ children }: { children: React.ReactNode }) =>
      React.createElement('div', { 'data-testid': 'area-chart' }, children),
    PieChart: ({ children }: { children: React.ReactNode }) =>
      React.createElement('div', { 'data-testid': 'pie-chart' }, children),
    Bar: () => null,
    Area: () => null,
    Pie: () => null,
    Cell: () => null,
    XAxis: () => null,
    YAxis: () => null,
    CartesianGrid: () => null,
    Tooltip: () => null,
    Legend: () => null,
  };
});

vi.mock('../api/client', () => ({
  getTokenUsage: vi.fn(),
}));

import { getTokenUsage } from '../api/client';

const mockedGetTokenUsage = vi.mocked(getTokenUsage);

const mockFilters: DashboardFilters = {
  dateRange: { start: '2025-01-01', end: '2025-01-07' },
  userIdentities: [],
  teamId: null,
  sourceApplication: null,
};

const mockUsers: UserTokenUsage[] = [
  {
    userIdentity: 'user-1',
    userName: 'Alice',
    inputTokens: 50000,
    outputTokens: 30000,
    cacheReadTokens: 10000,
    cacheWriteTokens: 5000,
    totalTokens: 95000,
    costEstimate: 12.5,
  },
  {
    userIdentity: 'user-2',
    userName: 'Bob',
    inputTokens: 80000,
    outputTokens: 40000,
    cacheReadTokens: 20000,
    cacheWriteTokens: 10000,
    totalTokens: 150000,
    costEstimate: 25.0,
  },
  {
    userIdentity: 'user-3',
    userName: 'Charlie',
    inputTokens: 10000,
    outputTokens: 5000,
    cacheReadTokens: 0,
    cacheWriteTokens: 0,
    totalTokens: 15000,
    costEstimate: null,
  },
];

const mockDaily: DailyTokenUsage[] = [
  { date: '2025-01-01', inputTokens: 20000, outputTokens: 10000, cacheReadTokens: 5000, cacheWriteTokens: 2000 },
  { date: '2025-01-02', inputTokens: 30000, outputTokens: 15000, cacheReadTokens: 8000, cacheWriteTokens: 3000 },
];

beforeEach(() => {
  vi.clearAllMocks();
});

describe('TokenUsageTab', () => {
  it('shows loading spinner initially', () => {
    mockedGetTokenUsage.mockReturnValue(new Promise(() => {}));
    render(<TokenUsageTab filters={mockFilters} />);
    expect(screen.getByText('Loading token usage...')).toBeTruthy();
  });

  it('renders daily chart and per-user table after loading', async () => {
    mockedGetTokenUsage.mockResolvedValue({ users: mockUsers, daily: mockDaily });
    render(<TokenUsageTab filters={mockFilters} />);

    await waitFor(() => {
      expect(screen.getByText('Daily Token Usage')).toBeTruthy();
    });

    expect(screen.getByTestId('area-chart')).toBeTruthy();
    expect(screen.getByText('Per-User Token Usage')).toBeTruthy();
    expect(screen.getByText('Alice')).toBeTruthy();
    expect(screen.getByText('Bob')).toBeTruthy();
    expect(screen.getByText('Charlie')).toBeTruthy();
  });

  it('default sorts by totalTokens descending', async () => {
    mockedGetTokenUsage.mockResolvedValue({ users: mockUsers, daily: mockDaily });
    render(<TokenUsageTab filters={mockFilters} />);

    await waitFor(() => {
      expect(screen.getByText('Bob')).toBeTruthy();
    });

    // Bob (150k) should appear before Alice (95k) before Charlie (15k)
    const rows = screen.getAllByText(/Alice|Bob|Charlie/);
    expect(rows[0].textContent).toBe('Bob');
    expect(rows[1].textContent).toBe('Alice');
    expect(rows[2].textContent).toBe('Charlie');
  });

  it('toggles sort direction when clicking same column header', async () => {
    mockedGetTokenUsage.mockResolvedValue({ users: mockUsers, daily: mockDaily });
    render(<TokenUsageTab filters={mockFilters} />);

    await waitFor(() => {
      expect(screen.getByText('Bob')).toBeTruthy();
    });

    // Click Total Tokens header to toggle to ascending
    const totalHeader = screen.getByText(/^Total ▼$|^Total$/);
    fireEvent.click(totalHeader);

    // Now Charlie (15k) should be first
    const rows = screen.getAllByText(/Alice|Bob|Charlie/);
    expect(rows[0].textContent).toBe('Charlie');
    expect(rows[2].textContent).toBe('Bob');
  });

  it('sorts by a different column when clicking a new header', async () => {
    mockedGetTokenUsage.mockResolvedValue({ users: mockUsers, daily: mockDaily });
    render(<TokenUsageTab filters={mockFilters} />);

    await waitFor(() => {
      expect(screen.getByText('Bob')).toBeTruthy();
    });

    // Click Input Tokens header — should sort desc by inputTokens
    const inputHeaders = screen.getAllByText(/^Input$/);
    const inputHeader = inputHeaders.find((el) => el.tagName === 'TH') ?? inputHeaders[0];
    fireEvent.click(inputHeader);

    const rows = screen.getAllByText(/Alice|Bob|Charlie/);
    // Bob (80k) > Alice (50k) > Charlie (10k)
    expect(rows[0].textContent).toBe('Bob');
    expect(rows[1].textContent).toBe('Alice');
    expect(rows[2].textContent).toBe('Charlie');
  });

  it('shows drill-down when a user row is clicked', async () => {
    mockedGetTokenUsage.mockResolvedValue({ users: mockUsers, daily: mockDaily });
    render(<TokenUsageTab filters={mockFilters} />);

    await waitFor(() => {
      expect(screen.getByText('Alice')).toBeTruthy();
    });

    // Click Alice's row
    const aliceRow = screen.getByTestId('user-row-user-1');
    fireEvent.click(aliceRow);

    expect(screen.getByTestId('user-drilldown-user-1')).toBeTruthy();
    expect(screen.getByText('Daily Breakdown for Alice')).toBeTruthy();
    // Should show daily dates
    expect(screen.getByText('2025-01-01')).toBeTruthy();
    expect(screen.getByText('2025-01-02')).toBeTruthy();
  });

  it('collapses drill-down when same row is clicked again', async () => {
    mockedGetTokenUsage.mockResolvedValue({ users: mockUsers, daily: mockDaily });
    render(<TokenUsageTab filters={mockFilters} />);

    await waitFor(() => {
      expect(screen.getByText('Alice')).toBeTruthy();
    });

    const aliceRow = screen.getByTestId('user-row-user-1');
    fireEvent.click(aliceRow);
    expect(screen.getByTestId('user-drilldown-user-1')).toBeTruthy();

    fireEvent.click(aliceRow);
    expect(screen.queryByTestId('user-drilldown-user-1')).toBeNull();
  });

  it('formats cost as $X.XX or N/A', async () => {
    mockedGetTokenUsage.mockResolvedValue({ users: mockUsers, daily: mockDaily });
    render(<TokenUsageTab filters={mockFilters} />);

    await waitFor(() => {
      expect(screen.getByText('Alice')).toBeTruthy();
    });

    expect(screen.getByText('$12.5000')).toBeTruthy();
    expect(screen.getByText('$25.0000')).toBeTruthy();
    expect(screen.getByText('N/A')).toBeTruthy();
  });

  it('shows error banner with retry on API failure', async () => {
    mockedGetTokenUsage.mockRejectedValue(new Error('Network error'));
    render(<TokenUsageTab filters={mockFilters} />);

    await waitFor(() => {
      expect(screen.getByText(/Network error/)).toBeTruthy();
    });

    expect(screen.getByText('Retry')).toBeTruthy();
  });

  it('calls getTokenUsage with filters', async () => {
    mockedGetTokenUsage.mockResolvedValue({ users: [], daily: [] });
    render(<TokenUsageTab filters={mockFilters} />);

    await waitFor(() => {
      expect(mockedGetTokenUsage).toHaveBeenCalledWith(mockFilters);
    });
  });

  it('shows empty state when no data', async () => {
    mockedGetTokenUsage.mockResolvedValue({ users: [], daily: [] });
    render(<TokenUsageTab filters={mockFilters} />);

    await waitFor(() => {
      expect(screen.getByText('No daily data available.')).toBeTruthy();
    });

    expect(screen.getByText('No user data available.')).toBeTruthy();
  });

  it('formats numbers with commas', async () => {
    mockedGetTokenUsage.mockResolvedValue({ users: mockUsers, daily: mockDaily });
    render(<TokenUsageTab filters={mockFilters} />);

    await waitFor(() => {
      expect(screen.getByText('Bob')).toBeTruthy();
    });

    // Bob's totalTokens = 150,000
    expect(screen.getByText('150,000')).toBeTruthy();
    // Alice's totalTokens = 95,000
    expect(screen.getByText('95,000')).toBeTruthy();
  });
});
