import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import CostGovernanceTab from './CostGovernanceTab';

// jsdom has no ResizeObserver, which Recharts' ResponsiveContainer requires
// once it renders with non-empty data (e.g. the Spend by Application bar
// chart). Stub a no-op implementation (mirrors AgentCoreCostTab.test.tsx).
class ResizeObserverStub {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}
(globalThis as unknown as { ResizeObserver: typeof ResizeObserverStub }).ResizeObserver =
  ResizeObserverStub;
import type { DashboardFilters } from '../../types';
import type {
  CacheHitResponse,
  CanaryResponse,
  CostPerOutcomeRow,
  CurStatus,
  ExecutiveRollupResponse,
  ReconciliationSlice,
  SpendByApplicationResponse,
} from '../../types/governance';

const mockFilters: DashboardFilters = {
  dateRange: { start: '2025-01-01', end: '2025-01-07' },
  userIdentities: [],
  teamId: null,
  sourceApplication: null,
};

vi.mock('../../api/governance', () => ({
  getCurStatus: vi.fn(),
  getReconciliation: vi.fn(),
  getCacheHit: vi.fn(),
  getCostPerOutcome: vi.fn(),
  getCanary: vi.fn(),
  getExecutiveRollup: vi.fn(),
  getSpendByApplication: vi.fn(),
  triggerCurIngest: vi.fn(),
}));

import {
  getCurStatus,
  getReconciliation,
  getCacheHit,
  getCostPerOutcome,
  getCanary,
  getExecutiveRollup,
  getSpendByApplication,
} from '../../api/governance';

const mockedGetCurStatus = vi.mocked(getCurStatus);
const mockedGetReconciliation = vi.mocked(getReconciliation);
const mockedGetCacheHit = vi.mocked(getCacheHit);
const mockedGetCostPerOutcome = vi.mocked(getCostPerOutcome);
const mockedGetCanary = vi.mocked(getCanary);
const mockedGetExecutiveRollup = vi.mocked(getExecutiveRollup);
const mockedGetSpendByApplication = vi.mocked(getSpendByApplication);

const emptyCurStatus: CurStatus = {
  curAvailable: false,
  coveredThrough: null,
  billedStale: false,
  lastRunAt: null,
  status: null,
};

const emptyReconciliation: ReconciliationSlice[] = [];
const emptyCacheHit: CacheHitResponse = { byModel: [], byApplication: [], trend: [] };
const emptyCostPerOutcome: CostPerOutcomeRow[] = [];
const emptyCanary: CanaryResponse = {
  unattributedSpendShare: 0,
  unattributedRecordShare: 0,
  baseModelShare: 0,
  basis: 'estimated',
  target: 0.05,
  meetsTarget: true,
  trend: [],
};

const allDepartmentsRollup: ExecutiveRollupResponse = {
  l1Total: 6.0,
  l1InvocationCount: 3,
  byDepartment: [
    { label: 'Finance', cost: 3.0, invocationCount: 2 },
    { label: 'Engineering', cost: 3.0, invocationCount: 1 },
  ],
  byAgent: [
    { label: 'PaymentPlanAgent', cost: 3.0, invocationCount: 2 },
    { label: 'PatientOutreachAgent', cost: 3.0, invocationCount: 1 },
  ],
  basis: 'estimated',
};

const financeOnlyRollup: ExecutiveRollupResponse = {
  l1Total: 3.0,
  l1InvocationCount: 2,
  byDepartment: [
    { label: 'Finance', cost: 3.0, invocationCount: 2 },
    { label: 'Engineering', cost: 3.0, invocationCount: 1 },
  ],
  byAgent: [{ label: 'PaymentPlanAgent', cost: 3.0, invocationCount: 2 }],
  basis: 'estimated',
};

const emptySpendByApplication: SpendByApplicationResponse = { rows: [], basis: 'estimated' };

beforeEach(() => {
  vi.clearAllMocks();
  mockedGetCurStatus.mockResolvedValue(emptyCurStatus);
  mockedGetReconciliation.mockResolvedValue(emptyReconciliation);
  mockedGetCacheHit.mockResolvedValue(emptyCacheHit);
  mockedGetCostPerOutcome.mockResolvedValue(emptyCostPerOutcome);
  mockedGetCanary.mockResolvedValue(emptyCanary);
  mockedGetExecutiveRollup.mockResolvedValue(allDepartmentsRollup);
  mockedGetSpendByApplication.mockResolvedValue(emptySpendByApplication);
});

describe('CostGovernanceTab — KPI strip', () => {
  it('renders all four KPI cards once data loads', async () => {
    render(<CostGovernanceTab filters={mockFilters} />);

    await waitFor(() => {
      expect(screen.getByTestId('kpi-strip')).toBeTruthy();
    });

    expect(screen.getByTestId('kpi-total-spend')).toBeTruthy();
    expect(screen.getByTestId('kpi-variance')).toBeTruthy();
    expect(screen.getByTestId('kpi-unassigned-spend')).toBeTruthy();
    expect(screen.getByTestId('kpi-cache-hit')).toBeTruthy();
  });

  it('shows the Overview segment by default', async () => {
    render(<CostGovernanceTab filters={mockFilters} />);

    await waitFor(() => {
      expect(screen.getByTestId('segment-panel-overview')).toBeTruthy();
    });
    expect(screen.queryByTestId('segment-panel-reconciliation')).toBeNull();
    expect(screen.queryByTestId('segment-panel-efficiency')).toBeNull();
    expect(screen.queryByTestId('segment-panel-alerts')).toBeNull();
  });
});

describe('CostGovernanceTab — segmented navigation', () => {
  it('switches to Reconciliation Detail and back to Overview', async () => {
    render(<CostGovernanceTab filters={mockFilters} />);

    await waitFor(() => {
      expect(screen.getByTestId('segment-reconciliation')).toBeTruthy();
    });

    fireEvent.click(screen.getByTestId('segment-reconciliation'));
    expect(screen.getByTestId('segment-panel-reconciliation')).toBeTruthy();
    expect(screen.queryByTestId('segment-panel-overview')).toBeNull();

    fireEvent.click(screen.getByTestId('segment-overview'));
    expect(screen.getByTestId('segment-panel-overview')).toBeTruthy();
    expect(screen.queryByTestId('segment-panel-reconciliation')).toBeNull();
  });

  it('switches to Efficiency', async () => {
    render(<CostGovernanceTab filters={mockFilters} />);

    await waitFor(() => {
      expect(screen.getByTestId('segment-efficiency')).toBeTruthy();
    });

    fireEvent.click(screen.getByTestId('segment-efficiency'));
    expect(screen.getByTestId('segment-panel-efficiency')).toBeTruthy();
  });

  it('switches to Alerts', async () => {
    render(<CostGovernanceTab filters={mockFilters} />);

    await waitFor(() => {
      expect(screen.getByTestId('segment-alerts')).toBeTruthy();
    });

    fireEvent.click(screen.getByTestId('segment-alerts'));
    expect(screen.getByTestId('segment-panel-alerts')).toBeTruthy();
    expect(screen.getByTestId('canary-meets-indicator')).toBeTruthy();
  });
});

describe('CostGovernanceTab — Overview spend breakdown (breadcrumb drill-down)', () => {
  it('renders the total, department rows, and agent rows', async () => {
    render(<CostGovernanceTab filters={mockFilters} />);

    await waitFor(() => {
      expect(screen.getByTestId('rollup-l1-total')).toBeTruthy();
    });

    expect(screen.getByTestId('rollup-l1-total').textContent).toContain('6.00');
    expect(screen.getByTestId('rollup-department-row-Finance')).toBeTruthy();
    expect(screen.getByTestId('rollup-department-row-Engineering')).toBeTruthy();
    expect(screen.getByTestId('rollup-agent-row-PaymentPlanAgent')).toBeTruthy();
    expect(screen.getByTestId('rollup-agent-row-PatientOutreachAgent')).toBeTruthy();

    // Breadcrumb starts at "Total Spend" with no department segment.
    expect(screen.getByTestId('rollup-breadcrumb-total')).toBeTruthy();
  });

  it('drills into a department: clicking a row re-fetches scoped total/agent data', async () => {
    render(<CostGovernanceTab filters={mockFilters} />);

    await waitFor(() => {
      expect(screen.getByTestId('rollup-department-row-Finance')).toBeTruthy();
    });

    mockedGetExecutiveRollup.mockResolvedValue(financeOnlyRollup);
    fireEvent.click(screen.getByTestId('rollup-department-row-Finance'));

    await waitFor(() => {
      expect(mockedGetExecutiveRollup).toHaveBeenLastCalledWith(
        mockFilters,
        'Finance',
        expect.anything()
      );
    });

    await waitFor(() => {
      expect(screen.getByTestId('rollup-l1-total').textContent).toContain('3.00');
    });

    // Scoped view only shows Finance's agent, not Engineering's.
    expect(screen.getByTestId('rollup-agent-row-PaymentPlanAgent')).toBeTruthy();
    expect(screen.queryByTestId('rollup-agent-row-PatientOutreachAgent')).toBeNull();

    // Breadcrumb now shows "Total Spend > Finance".
    expect(screen.getByTestId('rollup-breadcrumb').textContent).toContain('Finance');
  });

  it('clears the drill-down by clicking the "Total Spend" breadcrumb', async () => {
    render(<CostGovernanceTab filters={mockFilters} />);

    await waitFor(() => {
      expect(screen.getByTestId('rollup-department-row-Finance')).toBeTruthy();
    });

    mockedGetExecutiveRollup.mockResolvedValue(financeOnlyRollup);
    fireEvent.click(screen.getByTestId('rollup-department-row-Finance'));

    await waitFor(() => {
      expect(screen.getByTestId('rollup-breadcrumb').textContent).toContain('Finance');
    });

    mockedGetExecutiveRollup.mockResolvedValue(allDepartmentsRollup);
    fireEvent.click(screen.getByTestId('rollup-breadcrumb-total'));

    await waitFor(() => {
      expect(mockedGetExecutiveRollup).toHaveBeenLastCalledWith(
        mockFilters,
        undefined,
        expect.anything()
      );
    });

    await waitFor(() => {
      expect(screen.getByTestId('rollup-agent-row-PatientOutreachAgent')).toBeTruthy();
    });
  });

  it('clears the drill-down by clicking the already-selected department row again', async () => {
    render(<CostGovernanceTab filters={mockFilters} />);

    await waitFor(() => {
      expect(screen.getByTestId('rollup-department-row-Finance')).toBeTruthy();
    });

    mockedGetExecutiveRollup.mockResolvedValue(financeOnlyRollup);
    fireEvent.click(screen.getByTestId('rollup-department-row-Finance'));

    await waitFor(() => {
      expect(screen.getByTestId('rollup-breadcrumb').textContent).toContain('Finance');
    });

    mockedGetExecutiveRollup.mockResolvedValue(allDepartmentsRollup);
    fireEvent.click(screen.getByTestId('rollup-department-row-Finance'));

    await waitFor(() => {
      expect(mockedGetExecutiveRollup).toHaveBeenLastCalledWith(
        mockFilters,
        undefined,
        expect.anything()
      );
    });
  });

  it('shows an empty state when there is no rollup data', async () => {
    mockedGetExecutiveRollup.mockResolvedValue({
      l1Total: 0,
      l1InvocationCount: 0,
      byDepartment: [],
      byAgent: [],
      basis: 'estimated',
    });

    render(<CostGovernanceTab filters={mockFilters} />);

    await waitFor(() => {
      expect(screen.getByText('No department data available.')).toBeTruthy();
    });
    expect(screen.getByText('No agent data available for this selection.')).toBeTruthy();
  });
});

describe('CostGovernanceTab — Spend by Application', () => {
  const spendByApp: SpendByApplicationResponse = {
    rows: [
      { label: 'ClaimsProcessor', cost: 42.5, invocationCount: 10 },
      { label: 'unknown', cost: 7.25, invocationCount: 3 },
    ],
    basis: 'estimated',
  };

  it('renders a row per application, sorted as returned by the API', async () => {
    mockedGetSpendByApplication.mockResolvedValue(spendByApp);
    render(<CostGovernanceTab filters={mockFilters} />);

    await waitFor(() => {
      expect(screen.getByTestId('segment-panel-spend-by-application')).toBeTruthy();
    });

    expect(screen.getByTestId('spend-by-application-row-ClaimsProcessor')).toBeTruthy();
    expect(screen.getByTestId('spend-by-application-row-unknown')).toBeTruthy();
    expect(screen.getByTestId('spend-by-application-row-ClaimsProcessor').textContent).toContain('42.50');
  });

  it('shows an empty state when there is no application spend data', async () => {
    mockedGetSpendByApplication.mockResolvedValue(emptySpendByApplication);
    render(<CostGovernanceTab filters={mockFilters} />);

    await waitFor(() => {
      expect(screen.getByTestId('segment-panel-spend-by-application')).toBeTruthy();
    });
    expect(screen.getByText('No application spend data available for the selected range.')).toBeTruthy();
  });
});

describe('CostGovernanceTab — Reconciliation Detail table', () => {
  const manySlices: ReconciliationSlice[] = Array.from({ length: 20 }, (_, i) => ({
    teamOrPrincipal: i % 2 === 0 ? 'Finance' : 'Engineering',
    model: i % 3 === 0 ? 'claude-haiku' : 'claude-sonnet',
    day: `2025-01-${String((i % 28) + 1).padStart(2, '0')}`,
    billed: i * 1.5,
    estimated: i * 1.4,
    factor: 1.07,
    variance: i * 0.1,
    flag: null,
    basis: 'reconciled',
  }));

  it('paginates when there are more slices than one page', async () => {
    mockedGetReconciliation.mockResolvedValue(manySlices);
    render(<CostGovernanceTab filters={mockFilters} />);

    await waitFor(() => {
      expect(screen.getByTestId('kpi-strip')).toBeTruthy();
    });
    fireEvent.click(screen.getByTestId('segment-reconciliation'));

    await waitFor(() => {
      expect(screen.getByTestId('recon-pagination-controls')).toBeTruthy();
    });
    expect(screen.getByTestId('recon-page-indicator').textContent).toContain('Page 1 of 2');

    fireEvent.click(screen.getByTestId('recon-page-next'));
    await waitFor(() => {
      expect(screen.getByTestId('recon-page-indicator').textContent).toContain('Page 2 of 2');
    });
  });

  it('filters slices via the search input', async () => {
    mockedGetReconciliation.mockResolvedValue(manySlices);
    render(<CostGovernanceTab filters={mockFilters} />);

    await waitFor(() => {
      expect(screen.getByTestId('kpi-strip')).toBeTruthy();
    });
    fireEvent.click(screen.getByTestId('segment-reconciliation'));

    await waitFor(() => {
      expect(screen.getByTestId('recon-search-input')).toBeTruthy();
    });

    fireEvent.change(screen.getByTestId('recon-search-input'), { target: { value: 'claude-haiku' } });

    await waitFor(() => {
      expect(screen.getByTestId('recon-page-indicator').textContent).toMatch(/of 1/);
    });
  });

  it('shows a "no matches" message for a search with zero results', async () => {
    mockedGetReconciliation.mockResolvedValue(manySlices);
    render(<CostGovernanceTab filters={mockFilters} />);

    await waitFor(() => {
      expect(screen.getByTestId('kpi-strip')).toBeTruthy();
    });
    fireEvent.click(screen.getByTestId('segment-reconciliation'));

    await waitFor(() => {
      expect(screen.getByTestId('recon-search-input')).toBeTruthy();
    });

    fireEvent.change(screen.getByTestId('recon-search-input'), { target: { value: 'nonexistent-team' } });

    await waitFor(() => {
      expect(screen.getByText('No slices match "nonexistent-team".')).toBeTruthy();
    });
  });
});
