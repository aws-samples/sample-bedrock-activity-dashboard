import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import CostAnalysisTab from './CostAnalysisTab';
import type { DashboardFilters, CostAnalysis } from '../types';

// Mock recharts to avoid SVG rendering issues in jsdom
vi.mock('recharts', () => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports -- vi.mock factory is hoisted above ESM imports; require() avoids the TDZ on a top-level React import
  const React = require('react');
  return {
    ResponsiveContainer: ({ children }: { children: React.ReactNode }) =>
      React.createElement('div', { 'data-testid': 'responsive-container' }, children),
    LineChart: ({ children }: { children: React.ReactNode }) =>
      React.createElement('div', { 'data-testid': 'line-chart' }, children),
    AreaChart: ({ children }: { children: React.ReactNode }) =>
      React.createElement('div', { 'data-testid': 'area-chart' }, children),
    BarChart: ({ children }: { children: React.ReactNode }) =>
      React.createElement('div', { 'data-testid': 'bar-chart' }, children),
    PieChart: ({ children }: { children: React.ReactNode }) =>
      React.createElement('div', { 'data-testid': 'pie-chart' }, children),
    Line: () => null,
    Area: () => null,
    Bar: () => null,
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
  getCostAnalysis: vi.fn(),
}));

import { getCostAnalysis } from '../api/client';

const mockedGetCostAnalysis = vi.mocked(getCostAnalysis);

const mockFilters: DashboardFilters = {
  dateRange: { start: '2025-01-01', end: '2025-01-07' },
  userIdentities: [],
  teamId: null,
  sourceApplication: null,
};

const mockCostData: CostAnalysis = {
  dailyCosts: [
    { date: '2025-01-01', costEstimate: 15.5 },
    { date: '2025-01-02', costEstimate: 22.75 },
    { date: '2025-01-03', costEstimate: 18.0 },
  ],
  perUserCosts: [
    { userName: 'Alice', costEstimate: 30.25, percentageOfTotal: 53.8 },
    { userName: 'Bob', costEstimate: 20.0, percentageOfTotal: 35.5 },
    { userName: 'Charlie', costEstimate: 6.0, percentageOfTotal: 10.7 },
  ],
  perModelCosts: [
    { modelId: 'anthropic.claude-sonnet', costEstimate: 40.25, percentageOfTotal: 71.5 },
    { modelId: 'anthropic.claude-haiku', costEstimate: 16.0, percentageOfTotal: 28.5 },
  ],
  totalCost: 56.25,
  accountCost: 56.25,
};

beforeEach(() => {
  vi.clearAllMocks();
});

describe('CostAnalysisTab', () => {
  it('shows loading spinner initially', () => {
    mockedGetCostAnalysis.mockReturnValue(new Promise(() => {}));
    render(<CostAnalysisTab filters={mockFilters} />);
    expect(screen.getByText('Loading cost analysis...')).toBeTruthy();
  });

  it('renders summary cards, chart, and tables after loading', async () => {
    mockedGetCostAnalysis.mockResolvedValue(mockCostData);
    render(<CostAnalysisTab filters={mockFilters} />);

    await waitFor(() => {
      expect(screen.getByText('Daily Cost Trend')).toBeTruthy();
    });

    expect(screen.getByTestId('total-cost')).toBeTruthy();
    expect(screen.getByTestId('area-chart')).toBeTruthy();
    expect(screen.getByText('Cost by User (Detail)')).toBeTruthy();
    expect(screen.getByText('Cost by Model (Detail)')).toBeTruthy();
  });

  it('displays total cost as a summary card', async () => {
    mockedGetCostAnalysis.mockResolvedValue(mockCostData);
    render(<CostAnalysisTab filters={mockFilters} />);

    await waitFor(() => {
      expect(screen.getByTestId('total-cost').textContent).toBe('$56.2500');
    });
  });

  it('displays per-user cost table sorted by cost descending', async () => {
    mockedGetCostAnalysis.mockResolvedValue(mockCostData);
    render(<CostAnalysisTab filters={mockFilters} />);

    await waitFor(() => {
      expect(screen.getByText('Alice')).toBeTruthy();
    });

    expect(screen.getByText('Bob')).toBeTruthy();
    expect(screen.getByText('Charlie')).toBeTruthy();

    // Verify order: Alice ($30.25) > Bob ($20.00) > Charlie ($6.00)
    const rows = screen.getAllByText(/Alice|Bob|Charlie/);
    expect(rows[0].textContent).toBe('Alice');
    expect(rows[1].textContent).toBe('Bob');
    expect(rows[2].textContent).toBe('Charlie');
  });

  it('displays per-user cost with percentage of total', async () => {
    mockedGetCostAnalysis.mockResolvedValue(mockCostData);
    render(<CostAnalysisTab filters={mockFilters} />);

    await waitFor(() => {
      expect(screen.getByText('Alice')).toBeTruthy();
    });

    expect(screen.getByText('$30.2500')).toBeTruthy();
    expect(screen.getByText('53.8%')).toBeTruthy();
    expect(screen.getByText('$20.0000')).toBeTruthy();
    expect(screen.getByText('35.5%')).toBeTruthy();
  });

  it('displays cost-by-model table with percentage of total', async () => {
    mockedGetCostAnalysis.mockResolvedValue(mockCostData);
    render(<CostAnalysisTab filters={mockFilters} />);

    await waitFor(() => {
      expect(screen.getAllByText('anthropic.claude-sonnet').length).toBeGreaterThan(0);
    });

    expect(screen.getByText('$40.2500')).toBeTruthy();
    expect(screen.getByText('71.5%')).toBeTruthy();
    expect(screen.getAllByText('anthropic.claude-haiku').length).toBeGreaterThan(0);
    expect(screen.getByText('$16.0000')).toBeTruthy();
    expect(screen.getByText('28.5%')).toBeTruthy();
  });

  it('formats cost as $X.XXXX', async () => {
    mockedGetCostAnalysis.mockResolvedValue(mockCostData);
    render(<CostAnalysisTab filters={mockFilters} />);

    await waitFor(() => {
      // $56.2500 is shown in the single Estimated Cost summary card.
      expect(screen.getAllByText('$56.2500').length).toBe(1);
    });

    expect(screen.getByText('$30.2500')).toBeTruthy();
    expect(screen.getByText('$40.2500')).toBeTruthy();
  });

  it('formats percentages as X.X%', async () => {
    mockedGetCostAnalysis.mockResolvedValue(mockCostData);
    render(<CostAnalysisTab filters={mockFilters} />);

    await waitFor(() => {
      expect(screen.getByText('53.8%')).toBeTruthy();
    });

    expect(screen.getByText('35.5%')).toBeTruthy();
    expect(screen.getByText('10.7%')).toBeTruthy();
    expect(screen.getByText('71.5%')).toBeTruthy();
    expect(screen.getByText('28.5%')).toBeTruthy();
  });

  it('shows error banner with retry on API failure', async () => {
    mockedGetCostAnalysis.mockRejectedValue(new Error('Network error'));
    render(<CostAnalysisTab filters={mockFilters} />);

    await waitFor(() => {
      expect(screen.getByText(/Network error/)).toBeTruthy();
    });

    expect(screen.getByText('Retry')).toBeTruthy();
  });

  it('retries on error when retry button is clicked', async () => {
    mockedGetCostAnalysis.mockRejectedValueOnce(new Error('Fail'));
    render(<CostAnalysisTab filters={mockFilters} />);

    await waitFor(() => {
      expect(screen.getByText(/Fail/)).toBeTruthy();
    });

    mockedGetCostAnalysis.mockResolvedValue(mockCostData);
    fireEvent.click(screen.getByText('Retry'));

    await waitFor(() => {
      expect(screen.getByText('Daily Cost Trend')).toBeTruthy();
    });
  });

  it('calls getCostAnalysis with filters', async () => {
    mockedGetCostAnalysis.mockResolvedValue(mockCostData);
    render(<CostAnalysisTab filters={mockFilters} />);

    await waitFor(() => {
      expect(mockedGetCostAnalysis).toHaveBeenCalledWith(mockFilters);
    });
  });

  it('shows empty state when no data', async () => {
    mockedGetCostAnalysis.mockResolvedValue({
      dailyCosts: [],
      perUserCosts: [],
      perModelCosts: [],
      totalCost: 0,
      accountCost: 0,
    });
    render(<CostAnalysisTab filters={mockFilters} />);

    await waitFor(() => {
      expect(screen.getByText('No daily cost data available.')).toBeTruthy();
    });

    expect(screen.getByText('No per-user cost data available.')).toBeTruthy();
    expect(screen.getByText('No per-model cost data available.')).toBeTruthy();
  });

  it('displays correct table column headers for per-user cost', async () => {
    mockedGetCostAnalysis.mockResolvedValue(mockCostData);
    render(<CostAnalysisTab filters={mockFilters} />);

    await waitFor(() => {
      expect(screen.getByText('Cost by User (Detail)')).toBeTruthy();
    });

    // Both tables have "Cost Estimate" and "% of Total" headers
    expect(screen.getAllByText('Cost Estimate').length).toBe(2);
    expect(screen.getAllByText('% of Total').length).toBe(2);
    expect(screen.getByText('User')).toBeTruthy();
    expect(screen.getByText('Model ID')).toBeTruthy();
  });
});
