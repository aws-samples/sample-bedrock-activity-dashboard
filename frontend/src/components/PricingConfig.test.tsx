import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import PricingConfig from './PricingConfig';
import type { ModelPricingConfig, PricingGapsResponse, PricingSyncSummary } from '../types';

vi.mock('../api/client', () => ({
  getPricing: vi.fn(),
  updatePricing: vi.fn(),
  syncPricing: vi.fn(),
  getUsagePricingGaps: vi.fn(),
}));

import { getPricing, syncPricing, getUsagePricingGaps } from '../api/client';

const mockedGetPricing = vi.mocked(getPricing);
const mockedSyncPricing = vi.mocked(syncPricing);
const mockedGetUsagePricingGaps = vi.mocked(getUsagePricingGaps);

const emptyGaps: PricingGapsResponse = {
  usedModelIds: [],
  modelsMissingPricing: [],
  windowStart: '2024-01-01T00:00:00+00:00',
  windowEnd: '2024-01-31T00:00:00+00:00',
};

const mockPricing: ModelPricingConfig[] = [
  {
    modelId: 'anthropic.claude-3-5-sonnet-20240620-v1:0',
    inputTokenPrice: 0.003,
    outputTokenPrice: 0.015,
    cacheReadTokenPrice: 0.0003,
    cacheWriteTokenPrice: 0.00375,
    isDefault: true,
  },
];

const multiModelPricing: ModelPricingConfig[] = [
  {
    modelId: 'anthropic.claude-3-5-sonnet-20240620-v1:0',
    inputTokenPrice: 0.003,
    outputTokenPrice: 0.015,
    cacheReadTokenPrice: 0.0003,
    cacheWriteTokenPrice: 0.00375,
    isDefault: true,
  },
  {
    modelId: 'amazon.nova-pro-v1:0',
    inputTokenPrice: 0.0008,
    outputTokenPrice: 0.0032,
    cacheReadTokenPrice: 0,
    cacheWriteTokenPrice: 0,
    isDefault: false,
  },
];

const mockSummary: PricingSyncSummary = {
  modelsUpdated: [
    {
      modelId: 'anthropic.claude-3-5-sonnet-20240620-v1:0',
      servicename: 'Claude 3.5 Sonnet (Amazon Bedrock Edition)',
      changes: { input_token_price: { old: 0.001, new: 0.003 } },
    },
  ],
  modelsUnchanged: ['amazon.nova-pro-v1:0'],
  modelsCreated: [],
  unmatchedServiceNames: ['Some Future Model (Amazon Bedrock Edition)'],
  trackedModelsMissingPrice: [],
  errors: [],
  pricingSource: 'AWS Price List API (AmazonBedrockFoundationModels, us-west-2, on-demand)',
  syncedAt: '2025-01-01T00:00:00+00:00',
};

beforeEach(() => {
  vi.clearAllMocks();
  // Default: no pricing gaps, so existing tests don't need to mock this call.
  mockedGetUsagePricingGaps.mockResolvedValue(emptyGaps);
});

describe('PricingConfig sync button', () => {
  it('does not show the sync button for non-admins', async () => {
    mockedGetPricing.mockResolvedValue(mockPricing);
    render(<PricingConfig role="user" />);
    await waitFor(() => expect(screen.getByTestId('pricing-table')).toBeTruthy());
    expect(screen.queryByTestId('sync-pricing-btn')).toBeNull();
  });

  it('shows the sync button for admins', async () => {
    mockedGetPricing.mockResolvedValue(mockPricing);
    render(<PricingConfig role="admin" />);
    await waitFor(() => expect(screen.getByTestId('sync-pricing-btn')).toBeTruthy());
  });

  it('calls syncPricing and renders the summary, then refreshes pricing', async () => {
    mockedGetPricing.mockResolvedValue(mockPricing);
    mockedSyncPricing.mockResolvedValue(mockSummary);

    render(<PricingConfig role="admin" />);
    await waitFor(() => expect(screen.getByTestId('sync-pricing-btn')).toBeTruthy());

    fireEvent.click(screen.getByTestId('sync-pricing-btn'));

    await waitFor(() => expect(mockedSyncPricing).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.getByTestId('sync-summary')).toBeTruthy());

    // Neutral/positive headline leads with updated/added/unchanged counts.
    expect(screen.getByTestId('sync-headline').textContent).toMatch(
      /1 updated, 0 added, 1 unchanged/,
    );
    // getPricing called on mount AND again after sync to refresh.
    expect(mockedGetPricing).toHaveBeenCalledTimes(2);
  });

  it('renders the untracked AWS models as a muted, non-alarming footnote', async () => {
    mockedGetPricing.mockResolvedValue(mockPricing);
    mockedSyncPricing.mockResolvedValue(mockSummary);

    render(<PricingConfig role="admin" />);
    await waitFor(() => expect(screen.getByTestId('sync-pricing-btn')).toBeTruthy());
    fireEvent.click(screen.getByTestId('sync-pricing-btn'));

    await waitFor(() => expect(screen.getByTestId('toggle-unmatched')).toBeTruthy());

    // Muted note: phrased as "not tracked", NOT as an error/failure.
    const note = screen.getByTestId('toggle-unmatched');
    expect(note.textContent).toMatch(/1 other AWS Bedrock models aren't tracked/);
    expect(note.textContent).not.toMatch(/could not be matched/);

    // Full list is behind the collapsible <details>.
    expect(screen.getByText('Some Future Model (Amazon Bedrock Edition)')).toBeTruthy();
  });

  it('does not render a "tracked models missing price" warning (removed — noisy/non-actionable)', async () => {
    mockedGetPricing.mockResolvedValue(mockPricing);
    mockedSyncPricing.mockResolvedValue({
      ...mockSummary,
      trackedModelsMissingPrice: ['mistral.mistral-small-2402-v1:0'],
    });

    render(<PricingConfig role="admin" />);
    await waitFor(() => expect(screen.getByTestId('sync-pricing-btn')).toBeTruthy());
    fireEvent.click(screen.getByTestId('sync-pricing-btn'));

    await waitFor(() => expect(screen.getByTestId('sync-summary')).toBeTruthy());
    expect(screen.queryByTestId('tracked-missing')).toBeNull();
  });

  it('shows an error message when sync fails', async () => {
    mockedGetPricing.mockResolvedValue(mockPricing);
    mockedSyncPricing.mockRejectedValue(new Error('boom'));

    render(<PricingConfig role="admin" />);
    await waitFor(() => expect(screen.getByTestId('sync-pricing-btn')).toBeTruthy());
    fireEvent.click(screen.getByTestId('sync-pricing-btn'));

    await waitFor(() => expect(screen.getByTestId('sync-error')).toBeTruthy());
    expect(screen.getByText('boom')).toBeTruthy();
  });
});

describe('PricingConfig search bar', () => {
  it('renders every model when the search box is empty', async () => {
    mockedGetPricing.mockResolvedValue(multiModelPricing);
    render(<PricingConfig role="user" />);
    await waitFor(() => expect(screen.getByTestId('pricing-table')).toBeTruthy());

    expect(screen.getByTestId('pricing-row-anthropic.claude-3-5-sonnet-20240620-v1:0')).toBeTruthy();
    expect(screen.getByTestId('pricing-row-amazon.nova-pro-v1:0')).toBeTruthy();
  });

  it('filters rows by model id substring, case-insensitively', async () => {
    mockedGetPricing.mockResolvedValue(multiModelPricing);
    render(<PricingConfig role="user" />);
    await waitFor(() => expect(screen.getByTestId('pricing-table')).toBeTruthy());

    fireEvent.change(screen.getByTestId('pricing-search-input'), {
      target: { value: 'CLAUDE' },
    });

    expect(screen.getByTestId('pricing-row-anthropic.claude-3-5-sonnet-20240620-v1:0')).toBeTruthy();
    expect(screen.queryByTestId('pricing-row-amazon.nova-pro-v1:0')).toBeNull();
  });

  it('shows an empty-state message when no model matches the query', async () => {
    mockedGetPricing.mockResolvedValue(multiModelPricing);
    render(<PricingConfig role="user" />);
    await waitFor(() => expect(screen.getByTestId('pricing-table')).toBeTruthy());

    fireEvent.change(screen.getByTestId('pricing-search-input'), {
      target: { value: 'nonexistent-model' },
    });

    expect(screen.queryByTestId('pricing-table')).toBeNull();
    expect(screen.getByTestId('pricing-search-empty').textContent).toMatch(
      /No models match "nonexistent-model"/,
    );
  });

  it('clearing the search box restores all rows', async () => {
    mockedGetPricing.mockResolvedValue(multiModelPricing);
    render(<PricingConfig role="user" />);
    await waitFor(() => expect(screen.getByTestId('pricing-table')).toBeTruthy());

    const input = screen.getByTestId('pricing-search-input');
    fireEvent.change(input, { target: { value: 'nova' } });
    expect(screen.queryByTestId('pricing-row-anthropic.claude-3-5-sonnet-20240620-v1:0')).toBeNull();

    fireEvent.change(input, { target: { value: '' } });
    expect(screen.getByTestId('pricing-row-anthropic.claude-3-5-sonnet-20240620-v1:0')).toBeTruthy();
    expect(screen.getByTestId('pricing-row-amazon.nova-pro-v1:0')).toBeTruthy();
  });
});

describe('PricingConfig usage-driven pricing gaps banner', () => {
  it('does not render the banner when there are no gaps', async () => {
    mockedGetPricing.mockResolvedValue(mockPricing);
    render(<PricingConfig role="user" />);
    await waitFor(() => expect(screen.getByTestId('pricing-table')).toBeTruthy());
    expect(screen.queryByTestId('pricing-gaps-banner')).toBeNull();
  });

  it('renders the banner listing models missing pricing', async () => {
    mockedGetPricing.mockResolvedValue(mockPricing);
    mockedGetUsagePricingGaps.mockResolvedValue({
      usedModelIds: ['some.brand-new-model-v1:0', 'anthropic.claude-3-5-sonnet-20240620-v1:0'],
      modelsMissingPricing: ['some.brand-new-model-v1:0'],
      windowStart: '2024-01-01T00:00:00+00:00',
      windowEnd: '2024-01-31T00:00:00+00:00',
    });

    render(<PricingConfig role="user" />);
    await waitFor(() => expect(screen.getByTestId('pricing-gaps-banner')).toBeTruthy());
    expect(screen.getByText('some.brand-new-model-v1:0')).toBeTruthy();
  });

  it('shows the banner to non-admin users too (read-only, no admin action shown)', async () => {
    mockedGetPricing.mockResolvedValue(mockPricing);
    mockedGetUsagePricingGaps.mockResolvedValue({
      usedModelIds: ['some.brand-new-model-v1:0'],
      modelsMissingPricing: ['some.brand-new-model-v1:0'],
      windowStart: '2024-01-01T00:00:00+00:00',
      windowEnd: '2024-01-31T00:00:00+00:00',
    });

    render(<PricingConfig role="user" />);
    await waitFor(() => expect(screen.getByTestId('pricing-gaps-banner')).toBeTruthy());
    // Non-admin: no "Update pricing from AWS" hint text and no sync button.
    expect(screen.queryByTestId('sync-pricing-btn')).toBeNull();
  });

  it('does not block the page when the gaps check fails', async () => {
    mockedGetPricing.mockResolvedValue(mockPricing);
    mockedGetUsagePricingGaps.mockRejectedValue(new Error('network error'));

    render(<PricingConfig role="user" />);
    await waitFor(() => expect(screen.getByTestId('pricing-table')).toBeTruthy());
    expect(screen.queryByTestId('pricing-gaps-banner')).toBeNull();
  });

  it('refreshes the gaps check after a successful sync', async () => {
    mockedGetPricing.mockResolvedValue(mockPricing);
    mockedGetUsagePricingGaps
      .mockResolvedValueOnce({
        usedModelIds: ['some.brand-new-model-v1:0'],
        modelsMissingPricing: ['some.brand-new-model-v1:0'],
        windowStart: '2024-01-01T00:00:00+00:00',
        windowEnd: '2024-01-31T00:00:00+00:00',
      })
      .mockResolvedValueOnce(emptyGaps);
    mockedSyncPricing.mockResolvedValue({
      modelsUpdated: [],
      modelsCreated: [],
      modelsUnchanged: [],
      unmatchedServiceNames: [],
      trackedModelsMissingPrice: [],
      errors: [],
      pricingSource: 'test',
      syncedAt: '2024-01-01T00:00:00+00:00',
    });

    render(<PricingConfig role="admin" />);
    await waitFor(() => expect(screen.getByTestId('pricing-gaps-banner')).toBeTruthy());

    fireEvent.click(screen.getByTestId('sync-pricing-btn'));

    await waitFor(() => expect(mockedGetUsagePricingGaps).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.queryByTestId('pricing-gaps-banner')).toBeNull());
  });
});
