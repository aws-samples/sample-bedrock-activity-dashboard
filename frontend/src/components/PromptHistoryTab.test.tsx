import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import PromptHistoryTab from './PromptHistoryTab';
import type { DashboardFilters, PromptHistoryEntry, PromptDetail, PaginatedResponse } from '../types';

vi.mock('../api/client', () => ({
  getPromptHistory: vi.fn(),
  getPromptDetail: vi.fn(),
}));

import { getPromptHistory, getPromptDetail } from '../api/client';

const mockedGetPromptHistory = vi.mocked(getPromptHistory);
const mockedGetPromptDetail = vi.mocked(getPromptDetail);

const mockFilters: DashboardFilters = {
  dateRange: { start: '2025-01-01', end: '2025-01-07' },
  userIdentities: [],
  teamId: null,
  sourceApplication: null,
};

function makeEntry(id: string, overrides?: Partial<PromptHistoryEntry>): PromptHistoryEntry {
  return {
    id,
    timestamp: '2025-01-05T10:30:00Z',
    userName: 'Alice',
    modelId: 'anthropic.claude-sonnet',
    sourceApplication: 'Kiro',
    inputTokens: 1500,
    outputTokens: 800,
    cacheReadTokens: 200,
    cacheWriteTokens: 100,
    costEstimate: 0.45,
    ...overrides,
  };
}

const mockPageData: PaginatedResponse<PromptHistoryEntry> = {
  items: [
    makeEntry('p1'),
    makeEntry('p2', { userName: 'Bob', costEstimate: null }),
  ],
  total: 2,
  page: 1,
  pageSize: 25,
  totalPages: 1,
};

const mockDetail: PromptDetail = {
  ...makeEntry('p1'),
  promptText: 'Explain how to use React hooks.',
  responseText: 'React hooks allow you to use state and lifecycle features...',
};

beforeEach(() => {
  vi.clearAllMocks();
});

describe('PromptHistoryTab', () => {
  it('shows loading spinner initially', () => {
    mockedGetPromptHistory.mockReturnValue(new Promise(() => {}));
    render(<PromptHistoryTab filters={mockFilters} />);
    expect(screen.getByText('Loading prompt history...')).toBeTruthy();
  });

  it('renders table with correct columns after loading', async () => {
    mockedGetPromptHistory.mockResolvedValue(mockPageData);
    render(<PromptHistoryTab filters={mockFilters} />);

    await waitFor(() => {
      expect(screen.getByText('Timestamp')).toBeTruthy();
    });

    expect(screen.getByText('User')).toBeTruthy();
    expect(screen.getByText('Model')).toBeTruthy();
    expect(screen.getByText('Source')).toBeTruthy();
    expect(screen.getByText('Input')).toBeTruthy();
    expect(screen.getByText('Output')).toBeTruthy();
    expect(screen.getByText('Cache R')).toBeTruthy();
    expect(screen.getByText('Cache W')).toBeTruthy();
    expect(screen.getByText('Cost')).toBeTruthy();
  });

  it('displays rows with formatted data', async () => {
    mockedGetPromptHistory.mockResolvedValue(mockPageData);
    render(<PromptHistoryTab filters={mockFilters} />);

    await waitFor(() => {
      expect(screen.getByText('Alice')).toBeTruthy();
    });

    expect(screen.getByText('Bob')).toBeTruthy();
    expect(screen.getByText('$0.4500')).toBeTruthy();
    expect(screen.getByText('N/A')).toBeTruthy(); // Bob's null cost
  });

  it('formats numbers with commas', async () => {
    mockedGetPromptHistory.mockResolvedValue({
      items: [makeEntry('p1', { inputTokens: 12345 })],
      total: 1,
      page: 1,
      pageSize: 25,
      totalPages: 1,
    });
    render(<PromptHistoryTab filters={mockFilters} />);

    await waitFor(() => {
      expect(screen.getByText('12,345')).toBeTruthy();
    });
  });

  it('shows record count', async () => {
    mockedGetPromptHistory.mockResolvedValue(mockPageData);
    render(<PromptHistoryTab filters={mockFilters} />);

    await waitFor(() => {
      expect(screen.getByText('2 records')).toBeTruthy();
    });
  });

  it('shows error banner with retry on API failure', async () => {
    mockedGetPromptHistory.mockRejectedValue(new Error('Network error'));
    render(<PromptHistoryTab filters={mockFilters} />);

    await waitFor(() => {
      expect(screen.getByText('Network error')).toBeTruthy();
    });

    expect(screen.getByText('Retry')).toBeTruthy();
  });

  it('retries on error when retry button is clicked', async () => {
    mockedGetPromptHistory.mockRejectedValueOnce(new Error('Fail'));
    render(<PromptHistoryTab filters={mockFilters} />);

    await waitFor(() => {
      expect(screen.getByText('Fail')).toBeTruthy();
    });

    mockedGetPromptHistory.mockResolvedValue(mockPageData);
    fireEvent.click(screen.getByText('Retry'));

    await waitFor(() => {
      expect(screen.getByText('Alice')).toBeTruthy();
    });
  });

  it('calls getPromptHistory with filters, page 1, and page size 25', async () => {
    mockedGetPromptHistory.mockResolvedValue(mockPageData);
    render(<PromptHistoryTab filters={mockFilters} />);

    await waitFor(() => {
      expect(mockedGetPromptHistory).toHaveBeenCalledWith(mockFilters, 1, 25);
    });
  });

  it('shows empty state when no items', async () => {
    mockedGetPromptHistory.mockResolvedValue({
      items: [],
      total: 0,
      page: 1,
      pageSize: 25,
      totalPages: 0,
    });
    render(<PromptHistoryTab filters={mockFilters} />);

    await waitFor(() => {
      expect(screen.getByText('No prompt history available for the selected filters.')).toBeTruthy();
    });
  });

  it('shows pagination controls', async () => {
    mockedGetPromptHistory.mockResolvedValue({
      ...mockPageData,
      total: 50,
      totalPages: 2,
    });
    render(<PromptHistoryTab filters={mockFilters} />);

    await waitFor(() => {
      expect(screen.getByText('Page 1 of 2')).toBeTruthy();
    });

    expect(screen.getByText('← Prev')).toBeTruthy();
    expect(screen.getByText('Next →')).toBeTruthy();
  });

  it('navigates to next page when Next is clicked', async () => {
    mockedGetPromptHistory.mockResolvedValue({
      ...mockPageData,
      total: 50,
      totalPages: 2,
    });
    render(<PromptHistoryTab filters={mockFilters} />);

    await waitFor(() => {
      expect(screen.getByText('Page 1 of 2')).toBeTruthy();
    });

    mockedGetPromptHistory.mockResolvedValue({
      ...mockPageData,
      page: 2,
      total: 50,
      totalPages: 2,
    });
    fireEvent.click(screen.getByText('Next →'));

    await waitFor(() => {
      expect(mockedGetPromptHistory).toHaveBeenCalledWith(mockFilters, 2, 25);
    });
  });

  it('disables Previous button on first page', async () => {
    mockedGetPromptHistory.mockResolvedValue(mockPageData);
    render(<PromptHistoryTab filters={mockFilters} />);

    await waitFor(() => {
      expect(screen.getByText('← Prev')).toBeTruthy();
    });

    expect((screen.getByText('← Prev') as HTMLButtonElement).disabled).toBe(true);
  });

  it('opens detail modal on row click', async () => {
    mockedGetPromptHistory.mockResolvedValue(mockPageData);
    mockedGetPromptDetail.mockResolvedValue(mockDetail);
    render(<PromptHistoryTab filters={mockFilters} />);

    await waitFor(() => {
      expect(screen.getByTestId('prompt-row-p1')).toBeTruthy();
    });

    fireEvent.click(screen.getByTestId('prompt-row-p1'));

    await waitFor(() => {
      expect(screen.getByTestId('prompt-detail-modal')).toBeTruthy();
    });

    expect(screen.getByTestId('prompt-text').textContent).toBe('Explain how to use React hooks.');
    expect(screen.getByTestId('response-text').textContent).toBe(
      'React hooks allow you to use state and lifecycle features...'
    );
  });

  it('calls getPromptDetail with correct id on row click', async () => {
    mockedGetPromptHistory.mockResolvedValue(mockPageData);
    mockedGetPromptDetail.mockResolvedValue(mockDetail);
    render(<PromptHistoryTab filters={mockFilters} />);

    await waitFor(() => {
      expect(screen.getByTestId('prompt-row-p1')).toBeTruthy();
    });

    fireEvent.click(screen.getByTestId('prompt-row-p1'));

    await waitFor(() => {
      expect(mockedGetPromptDetail).toHaveBeenCalledWith('p1', mockFilters);
    });
  });

  it('closes detail modal when close button is clicked', async () => {
    mockedGetPromptHistory.mockResolvedValue(mockPageData);
    mockedGetPromptDetail.mockResolvedValue(mockDetail);
    render(<PromptHistoryTab filters={mockFilters} />);

    await waitFor(() => {
      expect(screen.getByTestId('prompt-row-p1')).toBeTruthy();
    });

    fireEvent.click(screen.getByTestId('prompt-row-p1'));

    await waitFor(() => {
      expect(screen.getByTestId('prompt-detail-modal')).toBeTruthy();
    });

    fireEvent.click(screen.getByTestId('close-detail-modal'));

    await waitFor(() => {
      expect(screen.queryByTestId('prompt-detail-modal')).toBeNull();
    });
  });
});
