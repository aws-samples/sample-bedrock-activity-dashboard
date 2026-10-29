import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import DemoData from './DemoData';

vi.mock('../../api/demo', () => ({
  getDemoStatus: vi.fn().mockResolvedValue({
    recordCount: 12,
    realCount: 9,
    lastGeneratedAt: '2024-01-15T12:00:00Z',
    lastBatchId: 'abc123',
  }),
  generateDemoData: vi.fn(),
  clearDemoData: vi.fn(),
}));

import { generateDemoData } from '../../api/demo';

const mockedGenerateDemoData = vi.mocked(generateDemoData);

describe('DemoData', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders the heading and action buttons', () => {
    render(<DemoData />);
    expect(screen.getByText('Demo Data')).toBeTruthy();
    expect(screen.getByText('Generate Demo Data')).toBeTruthy();
    expect(screen.getByText('Clear Demo Data')).toBeTruthy();
  });

  it('loads and shows current demo status', async () => {
    render(<DemoData />);
    await waitFor(() => {
      // recordCount surfaced in the status panel
      expect(screen.getByText('12')).toBeTruthy();
    });
    expect(screen.getByText('Total records')).toBeTruthy();
    expect(screen.getByText('9')).toBeTruthy();
    expect(screen.getByText('Real invocations')).toBeTruthy();
  });

  it('shows the Guardrail intervened stat when the managed guardrail blocked/anonymized some invocations', async () => {
    mockedGenerateDemoData.mockResolvedValue({
      requested: 30,
      succeeded: 28,
      failed: [],
      batchId: 'batch-1',
      guardrailIntervenedCount: 5,
      applicationProfilesUsed: [],
    });

    render(<DemoData />);
    fireEvent.click(screen.getByText('Generate Demo Data'));

    await waitFor(() => {
      expect(screen.getByText('Guardrail intervened')).toBeTruthy();
      expect(screen.getByText('5')).toBeTruthy();
    });
  });

  it('omits the Guardrail intervened stat when no guardrail has intervened', async () => {
    mockedGenerateDemoData.mockResolvedValue({
      requested: 30,
      succeeded: 30,
      failed: [],
      batchId: 'batch-1',
      guardrailIntervenedCount: 0,
      applicationProfilesUsed: [],
    });

    render(<DemoData />);
    fireEvent.click(screen.getByText('Generate Demo Data'));

    await waitFor(() => {
      expect(screen.getByText('Generation Summary')).toBeTruthy();
    });
    expect(screen.queryByText('Guardrail intervened')).toBeNull();
  });

  it('toggles the "Tag with applications" checkbox and includes it in the generate request', async () => {
    mockedGenerateDemoData.mockResolvedValue({
      requested: 30,
      succeeded: 30,
      failed: [],
      batchId: 'batch-1',
      guardrailIntervenedCount: 0,
      applicationProfilesUsed: [],
    });

    render(<DemoData />);
    const checkbox = screen.getByLabelText('Tag with applications') as HTMLInputElement;
    expect(checkbox.checked).toBe(false);

    fireEvent.click(checkbox);
    expect(checkbox.checked).toBe(true);

    fireEvent.click(screen.getByText('Generate Demo Data'));

    await waitFor(() => {
      expect(mockedGenerateDemoData).toHaveBeenCalledWith(
        expect.objectContaining({ withApplications: true })
      );
    });
  });

  it('shows the tagged applications used when the backend reports any', async () => {
    mockedGenerateDemoData.mockResolvedValue({
      requested: 40,
      succeeded: 40,
      failed: [],
      batchId: 'batch-2',
      guardrailIntervenedCount: 0,
      applicationProfilesUsed: ['ClaimsProcessor', 'SupportBot'],
    });

    render(<DemoData />);
    fireEvent.click(screen.getByLabelText('Tag with applications'));
    fireEvent.click(screen.getByText('Generate Demo Data'));

    await waitFor(() => {
      expect(screen.getByText('Tagged applications used:')).toBeTruthy();
    });
    expect(screen.getByText('ClaimsProcessor')).toBeTruthy();
    expect(screen.getByText('SupportBot')).toBeTruthy();
  });
});
