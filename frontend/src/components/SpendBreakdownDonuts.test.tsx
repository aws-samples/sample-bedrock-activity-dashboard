import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import SpendBreakdownDonuts from './SpendBreakdownDonuts';
import type { DashboardFilters, SpendBreakdownResponse } from '../types';

// jsdom has no ResizeObserver, which Recharts' ResponsiveContainer requires
// once it renders with non-empty data (mirrors AgentCoreCostTab.test.tsx).
class ResizeObserverStub {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}
(globalThis as unknown as { ResizeObserver: typeof ResizeObserverStub }).ResizeObserver =
  ResizeObserverStub;

const mockFilters: DashboardFilters = {
  dateRange: { start: '2025-01-01', end: '2025-01-07' },
  userIdentities: [],
  teamId: null,
  sourceApplication: null,
};

vi.mock('../api/client', () => ({
  getSpendBreakdown: vi.fn(),
}));

import { getSpendBreakdown } from '../api/client';

const mockedGetSpendBreakdown = vi.mocked(getSpendBreakdown);

const emptyResponse: SpendBreakdownResponse = {
  basis: 'estimated',
  byCategory: [],
  otherCategoryCost: 0,
  otherCategoryInvocationCount: 0,
  byModel: [],
  otherModelCost: 0,
  otherModelInvocationCount: 0,
};

const sampleResponse: SpendBreakdownResponse = {
  basis: 'estimated',
  byCategory: [
    {
      category: 'Coding',
      cost: 100,
      invocationCount: 40,
      byModel: [
        { modelId: 'anthropic.claude-sonnet-4-5', cost: 70, invocationCount: 20 },
        { modelId: 'amazon.nova-lite-v1:0', cost: 30, invocationCount: 20 },
      ],
      otherModelCost: 0,
      otherModelInvocationCount: 0,
    },
    {
      category: 'SQL / Database',
      cost: 50,
      invocationCount: 10,
      byModel: [
        { modelId: 'anthropic.claude-sonnet-4-5', cost: 50, invocationCount: 10 },
      ],
      otherModelCost: 0,
      otherModelInvocationCount: 0,
    },
  ],
  otherCategoryCost: 5,
  otherCategoryInvocationCount: 2,
  byModel: [
    { modelId: 'anthropic.claude-sonnet-4-5', cost: 120, invocationCount: 30 },
    { modelId: 'amazon.nova-lite-v1:0', cost: 30, invocationCount: 20 },
  ],
  otherModelCost: 5,
  otherModelInvocationCount: 2,
};

beforeEach(() => {
  vi.clearAllMocks();
});

describe('SpendBreakdownDonuts', () => {
  it('shows loading spinner initially', () => {
    mockedGetSpendBreakdown.mockReturnValue(new Promise(() => {}));
    render(<SpendBreakdownDonuts filters={mockFilters} />);
    expect(screen.getByText('Loading spend breakdown...')).toBeTruthy();
  });

  it('shows error banner on API failure', async () => {
    mockedGetSpendBreakdown.mockRejectedValue(new Error('Network error'));
    render(<SpendBreakdownDonuts filters={mockFilters} />);

    await waitFor(() => {
      expect(screen.getByText('Network error')).toBeTruthy();
    });
  });

  it('shows empty state when there is no categorized activity', async () => {
    mockedGetSpendBreakdown.mockResolvedValue(emptyResponse);
    render(<SpendBreakdownDonuts filters={mockFilters} />);

    await waitFor(() => {
      expect(screen.getByText('No categorized activity in this range.')).toBeTruthy();
    });
  });

  it('renders the chart title once data loads', async () => {
    mockedGetSpendBreakdown.mockResolvedValue(sampleResponse);
    render(<SpendBreakdownDonuts filters={mockFilters} />);

    await waitFor(() => {
      expect(screen.getByText('Spend by Category & Model')).toBeTruthy();
    });
  });

  it('renders category and model legend entries with cost', async () => {
    mockedGetSpendBreakdown.mockResolvedValue(sampleResponse);
    render(<SpendBreakdownDonuts filters={mockFilters} />);

    await waitFor(() => {
      expect(screen.getByText('Coding')).toBeTruthy();
    });
    expect(screen.getByText('SQL / Database')).toBeTruthy();
    // "Other" category bucket (otherCategoryCost > 0)
    expect(screen.getAllByText('Other').length).toBeGreaterThan(0);
    // Center label: chart total = 100 + 50 + 5 = 155.
    expect(screen.getByText('$155.00')).toBeTruthy();
  });

  it('calls getSpendBreakdown with filters', async () => {
    mockedGetSpendBreakdown.mockResolvedValue(sampleResponse);
    render(<SpendBreakdownDonuts filters={mockFilters} />);

    await waitFor(() => {
      expect(mockedGetSpendBreakdown).toHaveBeenCalledWith(mockFilters);
    });
  });

  it('omits the Other legend row when there is no overflow', async () => {
    mockedGetSpendBreakdown.mockResolvedValue({
      ...sampleResponse,
      otherCategoryCost: 0,
      otherCategoryInvocationCount: 0,
      otherModelCost: 0,
      otherModelInvocationCount: 0,
    });
    render(<SpendBreakdownDonuts filters={mockFilters} />);

    await waitFor(() => {
      expect(screen.getByText('Coding')).toBeTruthy();
    });
    expect(screen.queryByText('Other')).toBeNull();
  });
});
