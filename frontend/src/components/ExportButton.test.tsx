import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import ExportButton from './ExportButton';
import type { DashboardFilters } from '../types';

vi.mock('../api/client', () => ({
  exportCsv: vi.fn(),
}));

import { exportCsv } from '../api/client';

const mockedExportCsv = vi.mocked(exportCsv);

const mockFilters: DashboardFilters = {
  dateRange: { start: '2025-01-01', end: '2025-01-07' },
  userIdentities: [],
  teamId: null,
  sourceApplication: null,
};

beforeEach(() => {
  vi.clearAllMocks();
  // Mock URL.createObjectURL and revokeObjectURL
  (URL.createObjectURL as ReturnType<typeof vi.fn>) = vi.fn(() => 'blob:http://localhost/fake');
  (URL.revokeObjectURL as ReturnType<typeof vi.fn>) = vi.fn();
});

describe('ExportButton', () => {
  it('renders with "Export CSV" text', () => {
    render(<ExportButton section="token-usage" filters={mockFilters} />);
    expect(screen.getByTestId('export-btn-token-usage')).toBeTruthy();
    expect(screen.getByText('↓ Export CSV')).toBeTruthy();
  });

  it('shows "Exporting..." and disables button while loading', async () => {
    let resolveExport: (value: Blob) => void;
    mockedExportCsv.mockReturnValue(
      new Promise<Blob>((resolve) => {
        resolveExport = resolve;
      })
    );

    render(<ExportButton section="model-usage" filters={mockFilters} />);
    const btn = screen.getByTestId('export-btn-model-usage');

    fireEvent.click(btn);

    await waitFor(() => {
      expect(screen.getByText('⏳ Exporting...')).toBeTruthy();
    });
    expect((btn as HTMLButtonElement).disabled).toBe(true);

    // Resolve to finish
    resolveExport!(new Blob(['data'], { type: 'text/csv' }));
    await waitFor(() => {
      expect(screen.getByText('↓ Export CSV')).toBeTruthy();
    });
  });

  it('calls exportCsv with correct section and filters', async () => {
    mockedExportCsv.mockResolvedValue(new Blob(['data'], { type: 'text/csv' }));

    render(<ExportButton section="cost-analysis" filters={mockFilters} />);
    fireEvent.click(screen.getByTestId('export-btn-cost-analysis'));

    await waitFor(() => {
      expect(mockedExportCsv).toHaveBeenCalledWith({
        section: 'cost-analysis',
        filters: mockFilters,
      });
    });
  });

  it('creates a download link with correct filename format', async () => {
    mockedExportCsv.mockResolvedValue(new Blob(['data'], { type: 'text/csv' }));

    const clickSpy = vi.fn();
    const createElementOrig = document.createElement.bind(document);
    let capturedLink: HTMLAnchorElement | null = null;
    vi.spyOn(document, 'createElement').mockImplementation((tag: string) => {
      const el = createElementOrig(tag);
      if (tag === 'a') {
        capturedLink = el as HTMLAnchorElement;
        capturedLink.click = clickSpy;
      }
      return el;
    });

    render(<ExportButton section="prompt-history" filters={mockFilters} />);
    fireEvent.click(screen.getByTestId('export-btn-prompt-history'));

    await waitFor(() => {
      expect(capturedLink).not.toBeNull();
    });

    expect(capturedLink!.download).toMatch(/^prompt-history-export-\d{4}-\d{2}-\d{2}\.csv$/);
    expect(capturedLink!.href).toBe('blob:http://localhost/fake');
    expect(clickSpy).toHaveBeenCalled();

    vi.restoreAllMocks();
  });

  it('shows error state briefly on export failure', async () => {
    mockedExportCsv.mockRejectedValue(new Error('Network error'));

    render(<ExportButton section="token-usage" filters={mockFilters} />);
    fireEvent.click(screen.getByTestId('export-btn-token-usage'));

    await waitFor(() => {
      expect(screen.getByText('✕ Failed')).toBeTruthy();
    });
  });

  it('uses correct test id per section', () => {
    const { rerender } = render(<ExportButton section="token-usage" filters={mockFilters} />);
    expect(screen.getByTestId('export-btn-token-usage')).toBeTruthy();

    rerender(<ExportButton section="model-usage" filters={mockFilters} />);
    expect(screen.getByTestId('export-btn-model-usage')).toBeTruthy();

    rerender(<ExportButton section="cost-analysis" filters={mockFilters} />);
    expect(screen.getByTestId('export-btn-cost-analysis')).toBeTruthy();

    rerender(<ExportButton section="prompt-history" filters={mockFilters} />);
    expect(screen.getByTestId('export-btn-prompt-history')).toBeTruthy();
  });
});
