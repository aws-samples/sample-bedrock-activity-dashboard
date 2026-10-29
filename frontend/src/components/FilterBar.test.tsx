import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import FilterBar from './FilterBar';
import type { DashboardFilters, HealthConfig } from '../types';

vi.mock('../api/health', () => ({
  getHealthConfig: vi.fn(),
}));

import { getHealthConfig } from '../api/health';

const mockedGetHealthConfig = vi.mocked(getHealthConfig);

function makeHealthConfig(availableRegions: string[] = []): HealthConfig {
  return {
    demoMode: false,
    appName: 'Bedrock Activity Dashboard',
    demoDataPresent: false,
    demoRecordCount: 0,
    cognito: { userPoolId: '', clientId: '', domain: '', region: 'us-west-2' },
    availableRegions,
  };
}

function makeFilters(overrides?: Partial<DashboardFilters>): DashboardFilters {
  const end = new Date();
  const start = new Date();
  start.setDate(end.getDate() - 7);
  return {
    dateRange: { start: start.toISOString().slice(0, 10), end: end.toISOString().slice(0, 10) },
    userIdentities: [],
    teamId: null,
    sourceApplication: null,
    ...overrides,
  };
}

describe('FilterBar', () => {
  beforeEach(() => {
    mockedGetHealthConfig.mockReset();
    // Default: single-region deployment, so the dropdown stays hidden unless
    // a test explicitly opts into a multi-region config.
    mockedGetHealthConfig.mockResolvedValue(makeHealthConfig(['us-west-2']));
  });

  it('renders date preset buttons', () => {
    render(<FilterBar filters={makeFilters()} onFiltersChange={() => {}} />);
    expect(screen.getByText('7d')).toBeTruthy();
    expect(screen.getByText('30d')).toBeTruthy();
    expect(screen.getByText('90d')).toBeTruthy();
  });

  it('calls onFiltersChange when a preset is clicked', () => {
    const onChange = vi.fn();
    render(<FilterBar filters={makeFilters()} onFiltersChange={onChange} />);
    fireEvent.click(screen.getByText('30d'));
    expect(onChange).toHaveBeenCalledOnce();
    const newFilters = onChange.mock.calls[0][0] as DashboardFilters;
    const expectedStart = new Date();
    expectedStart.setDate(expectedStart.getDate() - 30);
    expect(newFilters.dateRange.start).toBe(expectedStart.toISOString().slice(0, 10));
  });

  // Note: FilterBar exposes Date Range, Start/End, and User filters only. It has
  // no source-application dropdown (source filtering is applied server-side, not
  // via this component), so there are no source-app dropdown tests here.

  it('rejects invalid date range (start > end)', () => {
    const onChange = vi.fn();
    const filters = makeFilters();
    render(<FilterBar filters={filters} onFiltersChange={onChange} />);
    // Try setting start date to after end date
    const startInput = screen.getAllByDisplayValue(filters.dateRange.start)[0];
    fireEvent.change(startInput, { target: { value: '2099-01-01' } });
    expect(onChange).not.toHaveBeenCalled();
  });

  describe('User filter (submit-only)', () => {
    it('does NOT call onFiltersChange while typing in the user field', () => {
      const onChange = vi.fn();
      render(<FilterBar filters={makeFilters()} onFiltersChange={onChange} />);
      const input = screen.getByPlaceholderText('user or name, then Enter');
      fireEvent.change(input, { target: { value: 'amelia' } });
      fireEvent.change(input, { target: { value: 'amelia, bob' } });
      expect(onChange).not.toHaveBeenCalled();
      // raw text is preserved locally
      expect((input as HTMLInputElement).value).toBe('amelia, bob');
    });

    it('applies parsed userIdentities once when Apply is clicked', () => {
      const onChange = vi.fn();
      render(<FilterBar filters={makeFilters()} onFiltersChange={onChange} />);
      const input = screen.getByPlaceholderText('user or name, then Enter');
      fireEvent.change(input, { target: { value: 'amelia, bob, ' } });
      fireEvent.click(screen.getByText('Apply'));
      expect(onChange).toHaveBeenCalledOnce();
      expect((onChange.mock.calls[0][0] as DashboardFilters).userIdentities).toEqual([
        'amelia',
        'bob',
      ]);
    });

    it('applies parsed userIdentities once when Enter is pressed', () => {
      const onChange = vi.fn();
      render(<FilterBar filters={makeFilters()} onFiltersChange={onChange} />);
      const input = screen.getByPlaceholderText('user or name, then Enter');
      fireEvent.change(input, { target: { value: 'amelia' } });
      fireEvent.keyDown(input, { key: 'Enter' });
      expect(onChange).toHaveBeenCalledOnce();
      expect((onChange.mock.calls[0][0] as DashboardFilters).userIdentities).toEqual(['amelia']);
    });

    it('Clear applies empty userIdentities', () => {
      const onChange = vi.fn();
      render(
        <FilterBar
          filters={makeFilters({ userIdentities: ['amelia'] })}
          onFiltersChange={onChange}
        />
      );
      fireEvent.click(screen.getByText('Clear'));
      expect(onChange).toHaveBeenCalledOnce();
      expect((onChange.mock.calls[0][0] as DashboardFilters).userIdentities).toEqual([]);
    });

    it('locked (non-admin) user shows a read-only display, no input', () => {
      render(
        <FilterBar filters={makeFilters()} onFiltersChange={() => {}} lockedUser="amelia@example.com" />
      );
      expect(screen.queryByPlaceholderText('user or name, then Enter')).toBeNull();
      expect(screen.getByText('amelia@example.com')).toBeTruthy();
    });
  });

  describe('Region filter', () => {
    it('hides the region dropdown for a single-region deployment', async () => {
      mockedGetHealthConfig.mockResolvedValue(makeHealthConfig(['us-west-2']));
      render(<FilterBar filters={makeFilters()} onFiltersChange={() => {}} />);
      await waitFor(() => expect(mockedGetHealthConfig).toHaveBeenCalled());
      expect(screen.queryByText('Region')).toBeNull();
    });

    it('shows the region dropdown with an "All Regions" default when multi-region', async () => {
      mockedGetHealthConfig.mockResolvedValue(makeHealthConfig(['us-west-2', 'us-east-1']));
      render(<FilterBar filters={makeFilters()} onFiltersChange={() => {}} />);
      await waitFor(() => expect(screen.getByText('Region')).toBeTruthy());
      const select = screen.getByDisplayValue('All Regions') as HTMLSelectElement;
      expect(select).toBeTruthy();
      expect(screen.getByText('us-west-2')).toBeTruthy();
      expect(screen.getByText('us-east-1')).toBeTruthy();
    });

    it('calls onFiltersChange with the selected region', async () => {
      mockedGetHealthConfig.mockResolvedValue(makeHealthConfig(['us-west-2', 'us-east-1']));
      const onChange = vi.fn();
      render(<FilterBar filters={makeFilters()} onFiltersChange={onChange} />);
      await waitFor(() => expect(screen.getByText('Region')).toBeTruthy());
      const select = screen.getByDisplayValue('All Regions') as HTMLSelectElement;
      fireEvent.change(select, { target: { value: 'us-east-1' } });
      expect(onChange).toHaveBeenCalledOnce();
      expect((onChange.mock.calls[0][0] as DashboardFilters).regions).toEqual(['us-east-1']);
    });

    it('selecting "All Regions" clears the region filter', async () => {
      mockedGetHealthConfig.mockResolvedValue(makeHealthConfig(['us-west-2', 'us-east-1']));
      const onChange = vi.fn();
      render(
        <FilterBar
          filters={makeFilters({ regions: ['us-east-1'] })}
          onFiltersChange={onChange}
        />
      );
      await waitFor(() => expect(screen.getByText('Region')).toBeTruthy());
      const select = screen.getByDisplayValue('us-east-1') as HTMLSelectElement;
      fireEvent.change(select, { target: { value: '' } });
      expect(onChange).toHaveBeenCalledOnce();
      expect((onChange.mock.calls[0][0] as DashboardFilters).regions).toEqual([]);
    });

    it('stays hidden when the health config fetch fails', async () => {
      mockedGetHealthConfig.mockRejectedValue(new Error('network error'));
      render(<FilterBar filters={makeFilters()} onFiltersChange={() => {}} />);
      await waitFor(() => expect(mockedGetHealthConfig).toHaveBeenCalled());
      expect(screen.queryByText('Region')).toBeNull();
    });
  });
});
