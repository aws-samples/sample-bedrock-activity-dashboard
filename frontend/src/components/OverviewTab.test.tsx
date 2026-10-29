import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import OverviewTab from './OverviewTab';
import type { DashboardFilters, OverviewSummary } from '../types';

const mockFilters: DashboardFilters = {
  dateRange: { start: '2025-01-01', end: '2025-01-07' },
  userIdentities: [],
  teamId: null,
  sourceApplication: null,
};

const mockOverview: OverviewSummary = {
  totalInvocations: 1234,
  totalTokens: 567890,
  totalInputTokens: 300000,
  totalOutputTokens: 200000,
  totalCacheReadTokens: 50000,
  totalCacheWriteTokens: 17890,
  totalCostEstimate: 42.5,
  activeUsers: 8,
  activeModels: 3,
  dateRange: { start: '2025-01-01', end: '2025-01-07' },
};

// Mock the API client
vi.mock('../api/client', () => ({
  getOverview: vi.fn(),
  getSpendBreakdown: vi.fn(),
}));

import { getOverview, getSpendBreakdown } from '../api/client';

const mockedGetOverview = vi.mocked(getOverview);
const mockedGetSpendBreakdown = vi.mocked(getSpendBreakdown);

beforeEach(() => {
  vi.clearAllMocks();
  // SpendBreakdownDonuts renders alongside the metric cards on every test in
  // this file; give it a resolved empty response by default so it doesn't
  // hang in a loading state or hit a real network call.
  mockedGetSpendBreakdown.mockResolvedValue({
    basis: 'estimated',
    byCategory: [],
    otherCategoryCost: 0,
    otherCategoryInvocationCount: 0,
    byModel: [],
    otherModelCost: 0,
    otherModelInvocationCount: 0,
  });
});

describe('OverviewTab', () => {
  it('shows loading spinner initially', () => {
    mockedGetOverview.mockReturnValue(new Promise(() => {})); // never resolves
    render(<OverviewTab filters={mockFilters} />);
    expect(screen.getByText('Loading overview...')).toBeTruthy();
  });

  it('renders summary cards with formatted values', async () => {
    mockedGetOverview.mockResolvedValue(mockOverview);
    render(<OverviewTab filters={mockFilters} />);

    await waitFor(() => {
      expect(screen.getByText('Total Invocations')).toBeTruthy();
    });

    expect(screen.getByText('1,234')).toBeTruthy();
    expect(screen.getByText('567,890')).toBeTruthy();
    // Total Cost renders at the dashboard-wide default precision (4 decimals)
    expect(screen.getByText('$42.5000')).toBeTruthy();
    expect(screen.getByText('8')).toBeTruthy();
    expect(screen.getByText('3')).toBeTruthy();
  });

  it('renders sub-cent total cost at full precision instead of $0.00', async () => {
    mockedGetOverview.mockResolvedValue({ ...mockOverview, totalCostEstimate: 0.0034 });
    render(<OverviewTab filters={mockFilters} />);

    await waitFor(() => {
      expect(screen.getByText('$0.0034')).toBeTruthy();
    });
  });

  it('displays N/A when cost is null', async () => {
    mockedGetOverview.mockResolvedValue({ ...mockOverview, totalCostEstimate: null });
    render(<OverviewTab filters={mockFilters} />);

    await waitFor(() => {
      expect(screen.getByText('N/A')).toBeTruthy();
    });
  });

  it('shows error banner with retry on API failure', async () => {
    mockedGetOverview.mockRejectedValue(new Error('Network error'));
    render(<OverviewTab filters={mockFilters} />);

    await waitFor(() => {
      expect(screen.getByText('Network error')).toBeTruthy();
    });

    expect(screen.getByText('Retry')).toBeTruthy();
  });

  it('calls getOverview with filters', async () => {
    mockedGetOverview.mockResolvedValue(mockOverview);
    render(<OverviewTab filters={mockFilters} />);

    await waitFor(() => {
      expect(mockedGetOverview).toHaveBeenCalledWith(mockFilters);
    });
  });
});
