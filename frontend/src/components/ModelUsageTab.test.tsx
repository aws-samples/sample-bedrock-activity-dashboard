import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import ModelUsageTab from './ModelUsageTab';
import type { DashboardFilters, ModelUsageSummary, UserModelCell } from '../types';

// Mock recharts to avoid SVG rendering issues in jsdom
vi.mock('recharts', () => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports -- vi.mock factory is hoisted above ESM imports; require() avoids the TDZ on a top-level React import
  const React = require('react');
  return {
    ResponsiveContainer: ({ children }: { children: React.ReactNode }) =>
      React.createElement('div', { 'data-testid': 'responsive-container' }, children),
    BarChart: ({ children }: { children: React.ReactNode }) =>
      React.createElement('div', { 'data-testid': 'bar-chart' }, children),
    Bar: ({ children }: { children: React.ReactNode }) =>
      React.createElement('div', { 'data-testid': 'bar' }, children),
    XAxis: () => React.createElement('div'),
    YAxis: () => React.createElement('div'),
    CartesianGrid: () => React.createElement('div'),
    LabelList: () => React.createElement('div'),
    Cell: () => React.createElement('div'),
    Tooltip: () => React.createElement('div'),
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

const mockModels: ModelUsageSummary[] = [
  {
    modelId: 'anthropic.claude-sonnet',
    invocationCount: 150,
    inputTokens: 500000,
    outputTokens: 200000,
    cacheReadTokens: 50000,
    cacheWriteTokens: 25000,
    totalTokens: 775000,
    costEstimate: 45.5,
    percentageOfTotal: 60.0,
  },
  {
    modelId: 'anthropic.claude-haiku',
    invocationCount: 100,
    inputTokens: 300000,
    outputTokens: 100000,
    cacheReadTokens: 20000,
    cacheWriteTokens: 10000,
    totalTokens: 430000,
    costEstimate: null,
    percentageOfTotal: 40.0,
  },
];

const mockMatrix: UserModelCell[] = [
  {
    userIdentity: 'user-1',
    userName: 'Alice',
    modelId: 'anthropic.claude-sonnet',
    invocationCount: 80,
    costEstimate: 25.0,
  },
  {
    userIdentity: 'user-2',
    userName: 'Bob',
    modelId: 'anthropic.claude-sonnet',
    invocationCount: 70,
    costEstimate: 20.5,
  },
  {
    userIdentity: 'user-1',
    userName: 'Alice',
    modelId: 'anthropic.claude-haiku',
    invocationCount: 60,
    costEstimate: null,
  },
  {
    userIdentity: 'user-2',
    userName: 'Bob',
    modelId: 'anthropic.claude-haiku',
    invocationCount: 40,
    costEstimate: null,
  },
];

beforeEach(() => {
  vi.clearAllMocks();
});

describe('ModelUsageTab', () => {
  it('shows loading spinner initially', () => {
    mockedGetModelUsage.mockReturnValue(new Promise(() => {}));
    render(<ModelUsageTab filters={mockFilters} />);
    expect(screen.getByText('Loading model usage...')).toBeTruthy();
  });

  it('renders bar chart and model summary table after loading', async () => {
    mockedGetModelUsage.mockResolvedValue({ models: mockModels, matrix: mockMatrix });
    render(<ModelUsageTab filters={mockFilters} />);

    await waitFor(() => {
      expect(screen.getByText('Invocations by Model')).toBeTruthy();
    });

    expect(screen.getByTestId('bar-chart')).toBeTruthy();
    expect(screen.getByText('Model Summary')).toBeTruthy();
    // Model names appear in both summary table and matrix headers
    expect(screen.getAllByText('anthropic.claude-sonnet').length).toBeGreaterThan(0);
    expect(screen.getAllByText('anthropic.claude-haiku').length).toBeGreaterThan(0);
  });

  it('displays model summary table with correct columns', async () => {
    mockedGetModelUsage.mockResolvedValue({ models: mockModels, matrix: mockMatrix });
    render(<ModelUsageTab filters={mockFilters} />);

    await waitFor(() => {
      expect(screen.getByText('Model Summary')).toBeTruthy();
    });

    // Check column headers
    expect(screen.getByText('Model ID')).toBeTruthy();
    expect(screen.getByText('Invocations')).toBeTruthy();
    expect(screen.getByText('Input Tokens')).toBeTruthy();
    expect(screen.getByText('Output Tokens')).toBeTruthy();
    expect(screen.getByText('Total Tokens')).toBeTruthy();
    expect(screen.getByText('Cost')).toBeTruthy();
    expect(screen.getByText('% of Invocations')).toBeTruthy();
  });

  it('formats numbers with commas', async () => {
    mockedGetModelUsage.mockResolvedValue({ models: mockModels, matrix: mockMatrix });
    render(<ModelUsageTab filters={mockFilters} />);

    await waitFor(() => {
      expect(screen.getAllByText('anthropic.claude-sonnet').length).toBeGreaterThan(0);
    });

    // 775,000 total tokens for sonnet
    expect(screen.getByText('775,000')).toBeTruthy();
    // 500,000 input tokens for sonnet
    expect(screen.getByText('500,000')).toBeTruthy();
  });

  it('formats cost as $X.XX or N/A', async () => {
    mockedGetModelUsage.mockResolvedValue({ models: mockModels, matrix: mockMatrix });
    render(<ModelUsageTab filters={mockFilters} />);

    await waitFor(() => {
      expect(screen.getAllByText('anthropic.claude-sonnet').length).toBeGreaterThan(0);
    });

    expect(screen.getByText('$45.50')).toBeTruthy();
    // haiku has null cost — should show N/A (multiple N/A cells expected)
    expect(screen.getAllByText('N/A').length).toBeGreaterThan(0);
  });

  it('formats percentages as X.X%', async () => {
    mockedGetModelUsage.mockResolvedValue({ models: mockModels, matrix: mockMatrix });
    render(<ModelUsageTab filters={mockFilters} />);

    await waitFor(() => {
      expect(screen.getAllByText('anthropic.claude-sonnet').length).toBeGreaterThan(0);
    });

    expect(screen.getByText('60.0%')).toBeTruthy();
    expect(screen.getByText('40.0%')).toBeTruthy();
  });

  it('shows user list when a model row is clicked', async () => {
    mockedGetModelUsage.mockResolvedValue({ models: mockModels, matrix: mockMatrix });
    render(<ModelUsageTab filters={mockFilters} />);

    await waitFor(() => {
      expect(screen.getAllByText('anthropic.claude-sonnet').length).toBeGreaterThan(0);
    });

    const sonnetRow = screen.getByTestId('model-row-anthropic.claude-sonnet');
    fireEvent.click(sonnetRow);

    expect(screen.getByTestId('model-users-anthropic.claude-sonnet')).toBeTruthy();
    expect(screen.getByText('Users for anthropic.claude-sonnet')).toBeTruthy();
    // Alice and Bob appear in both the expanded user list and the matrix
    expect(screen.getAllByText('Alice').length).toBeGreaterThanOrEqual(2);
    expect(screen.getAllByText('Bob').length).toBeGreaterThanOrEqual(2);
  });

  it('collapses user list when same model row is clicked again', async () => {
    mockedGetModelUsage.mockResolvedValue({ models: mockModels, matrix: mockMatrix });
    render(<ModelUsageTab filters={mockFilters} />);

    await waitFor(() => {
      expect(screen.getAllByText('anthropic.claude-sonnet').length).toBeGreaterThan(0);
    });

    const sonnetRow = screen.getByTestId('model-row-anthropic.claude-sonnet');
    fireEvent.click(sonnetRow);
    expect(screen.getByTestId('model-users-anthropic.claude-sonnet')).toBeTruthy();

    fireEvent.click(sonnetRow);
    expect(screen.queryByTestId('model-users-anthropic.claude-sonnet')).toBeNull();
  });

  it('renders compact per-user model usage', async () => {
    mockedGetModelUsage.mockResolvedValue({ models: mockModels, matrix: mockMatrix });
    render(<ModelUsageTab filters={mockFilters} />);

    await waitFor(() => {
      expect(screen.getByText('User Model Usage')).toBeTruthy();
    });

    // Compact list shows user names as blocks (they also appear in the model table area)
    const aliceCells = screen.getAllByText('Alice');
    expect(aliceCells.length).toBeGreaterThan(0);
    const bobCells = screen.getAllByText('Bob');
    expect(bobCells.length).toBeGreaterThan(0);
  });

  it('renders the total invocations summary caption', async () => {
    mockedGetModelUsage.mockResolvedValue({ models: mockModels, matrix: mockMatrix });
    render(<ModelUsageTab filters={mockFilters} />);

    await waitFor(() => {
      expect(screen.getByText('Invocations by Model')).toBeTruthy();
    });

    // 150 + 100 = 250 invocations across 2 models
    expect(screen.getByText(/invocations across/)).toBeTruthy();
    expect(screen.getByText('250 invocations across 2 models')).toBeTruthy();
  });

  it('does not render the show-all toggle when models.length <= TOP_N', async () => {
    mockedGetModelUsage.mockResolvedValue({ models: mockModels, matrix: mockMatrix });
    render(<ModelUsageTab filters={mockFilters} />);

    await waitFor(() => {
      expect(screen.getByText('Invocations by Model')).toBeTruthy();
    });

    expect(screen.queryByTestId('toggle-show-all-models')).toBeNull();
  });

  it('shows error banner with retry on API failure', async () => {
    mockedGetModelUsage.mockRejectedValue(new Error('Network error'));
    render(<ModelUsageTab filters={mockFilters} />);

    await waitFor(() => {
      expect(screen.getByText('Network error')).toBeTruthy();
    });

    expect(screen.getByText('Retry')).toBeTruthy();
  });

  it('calls getModelUsage with filters', async () => {
    mockedGetModelUsage.mockResolvedValue({ models: [], matrix: [] });
    render(<ModelUsageTab filters={mockFilters} />);

    await waitFor(() => {
      expect(mockedGetModelUsage).toHaveBeenCalledWith(mockFilters);
    });
  });

  it('shows empty state when no data', async () => {
    mockedGetModelUsage.mockResolvedValue({ models: [], matrix: [] });
    render(<ModelUsageTab filters={mockFilters} />);

    await waitFor(() => {
      expect(screen.getAllByText('No model data available.').length).toBe(2);
    });

    expect(screen.getByText('No user/model usage in range.')).toBeTruthy();
  });

  it('retries on error when retry button is clicked', async () => {
    mockedGetModelUsage.mockRejectedValueOnce(new Error('Fail'));
    render(<ModelUsageTab filters={mockFilters} />);

    await waitFor(() => {
      expect(screen.getByText('Fail')).toBeTruthy();
    });

    mockedGetModelUsage.mockResolvedValue({ models: mockModels, matrix: mockMatrix });
    fireEvent.click(screen.getByText('Retry'));

    await waitFor(() => {
      expect(screen.getAllByText('anthropic.claude-sonnet').length).toBeGreaterThan(0);
    });
  });
});
