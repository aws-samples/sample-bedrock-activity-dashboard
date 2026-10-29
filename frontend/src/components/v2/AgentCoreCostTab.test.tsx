import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import AgentCoreCostTab from './AgentCoreCostTab';
import type { DashboardFilters } from '../../types';
import type { AgentCoreCostResponse } from '../../types/agentcoreCost';

// jsdom has no ResizeObserver, which Recharts' ResponsiveContainer requires
// once it renders with non-empty data. Stub a no-op implementation so the
// per-agent bar chart can mount during these tests (mirrors the pattern
// used elsewhere in the codebase for Recharts-backed components).
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

vi.mock('../../api/agentcoreCost', () => ({
  getAgentCoreCost: vi.fn(),
  triggerAgentCoreScan: vi.fn(),
}));

import { getAgentCoreCost, triggerAgentCoreScan } from '../../api/agentcoreCost';

const mockedGetAgentCoreCost = vi.mocked(getAgentCoreCost);
const mockedTriggerScan = vi.mocked(triggerAgentCoreScan);

const twoAgentResponse: AgentCoreCostResponse = {
  totalCost: 0.006744,
  byAgent: [
    {
      agentName: 'model-cost-optimizer',
      displayName: 'Model Cost Optimizer',
      region: 'us-west-2',
      vcpuHours: 0.01,
      gbHours: 0.5,
      cpuCost: 0.000895,
      memoryCost: 0.004725,
      totalCost: 0.00562,
    },
    {
      agentName: 'NimbusConcierge_NimbusConcierge',
      displayName: 'NimbusConcierge_NimbusConcierge',
      region: 'us-east-1',
      vcpuHours: 0.002,
      gbHours: 0.1,
      cpuCost: 0.000179,
      memoryCost: 0.000945,
      totalCost: 0.001124,
    },
  ],
  configured: true,
  vcpuHourlyRate: 0.0895,
  memoryGbHourlyRate: 0.00945,
  scanRegions: ['us-west-2', 'us-east-1'],
  lastScanAt: '2026-08-08T10:00:00Z',
};

const emptyResponse: AgentCoreCostResponse = {
  totalCost: 0,
  byAgent: [],
  configured: true,
  vcpuHourlyRate: 0.0895,
  memoryGbHourlyRate: 0.00945,
  scanRegions: ['us-west-2', 'us-east-1'],
  lastScanAt: null,
};

const notConfiguredResponse: AgentCoreCostResponse = {
  totalCost: 0,
  byAgent: [],
  configured: false,
  vcpuHourlyRate: 0.0895,
  memoryGbHourlyRate: 0.00945,
  scanRegions: [],
  lastScanAt: null,
};

beforeEach(() => {
  vi.clearAllMocks();
});

describe('AgentCoreCostTab', () => {
  it('shows a loading state initially', () => {
    mockedGetAgentCoreCost.mockReturnValue(new Promise(() => {}));
    render(<AgentCoreCostTab filters={mockFilters} />);
    expect(screen.getByText('Loading AgentCore Runtime cost...')).toBeTruthy();
  });

  it('renders total cost and per-agent rows, mixing governed and discovered Runtimes', async () => {
    mockedGetAgentCoreCost.mockResolvedValue(twoAgentResponse);
    render(<AgentCoreCostTab filters={mockFilters} />);

    await waitFor(() => {
      expect(screen.getByTestId('agentcore-total-cost')).toBeTruthy();
    });

    expect(screen.getByTestId('agentcore-agent-row-model-cost-optimizer')).toBeTruthy();
    expect(screen.getByTestId('agentcore-agent-row-NimbusConcierge_NimbusConcierge')).toBeTruthy();
    expect(screen.getByText('Model Cost Optimizer')).toBeTruthy();
    expect(screen.getByText('NimbusConcierge_NimbusConcierge')).toBeTruthy();
    // Region column shows both regions, with no governed/discovered distinction.
    expect(screen.getByTestId('agentcore-agent-row-model-cost-optimizer').textContent).toContain('us-west-2');
    expect(screen.getByTestId('agentcore-agent-row-NimbusConcierge_NimbusConcierge').textContent).toContain('us-east-1');
  });

  it('drills into a single agent: clicking a row scopes the summary cards', async () => {
    mockedGetAgentCoreCost.mockResolvedValue(twoAgentResponse);
    render(<AgentCoreCostTab filters={mockFilters} />);

    await waitFor(() => {
      expect(screen.getByTestId('agentcore-agent-row-model-cost-optimizer')).toBeTruthy();
    });

    fireEvent.click(screen.getByTestId('agentcore-agent-row-model-cost-optimizer'));

    await waitFor(() => {
      expect(screen.getByTestId('agentcore-clear-drill-btn')).toBeTruthy();
    });

    // Summary card now reflects Model Cost Optimizer's own vCPU-hours, not the total.
    expect(screen.getByTestId('agentcore-vcpu-hours').textContent).toContain('0.0100');
  });

  it('shows a "Viewing: <agent>" badge when a row is selected, even if cost values look unchanged', async () => {
    mockedGetAgentCoreCost.mockResolvedValue(twoAgentResponse);
    render(<AgentCoreCostTab filters={mockFilters} />);

    await waitFor(() => {
      expect(screen.getByTestId('agentcore-agent-row-model-cost-optimizer')).toBeTruthy();
    });

    expect(screen.queryByTestId('agentcore-drill-badge')).toBeNull();

    fireEvent.click(screen.getByTestId('agentcore-agent-row-model-cost-optimizer'));

    await waitFor(() => {
      expect(screen.getByTestId('agentcore-drill-badge')).toBeTruthy();
    });
    expect(screen.getByTestId('agentcore-drill-badge').textContent).toContain('Model Cost Optimizer');
  });

  it('clicking an already-selected row toggles the drill-down back off', async () => {
    mockedGetAgentCoreCost.mockResolvedValue(twoAgentResponse);
    render(<AgentCoreCostTab filters={mockFilters} />);

    await waitFor(() => {
      expect(screen.getByTestId('agentcore-agent-row-model-cost-optimizer')).toBeTruthy();
    });

    fireEvent.click(screen.getByTestId('agentcore-agent-row-model-cost-optimizer'));
    await waitFor(() => {
      expect(screen.getByTestId('agentcore-clear-drill-btn')).toBeTruthy();
    });

    fireEvent.click(screen.getByTestId('agentcore-agent-row-model-cost-optimizer'));
    await waitFor(() => {
      expect(screen.queryByTestId('agentcore-clear-drill-btn')).toBeNull();
    });
  });

  it('clears the drill-down back to all agents', async () => {
    mockedGetAgentCoreCost.mockResolvedValue(twoAgentResponse);
    render(<AgentCoreCostTab filters={mockFilters} />);

    await waitFor(() => {
      expect(screen.getByTestId('agentcore-agent-row-model-cost-optimizer')).toBeTruthy();
    });

    fireEvent.click(screen.getByTestId('agentcore-agent-row-model-cost-optimizer'));

    await waitFor(() => {
      expect(screen.getByTestId('agentcore-clear-drill-btn')).toBeTruthy();
    });

    fireEvent.click(screen.getByTestId('agentcore-clear-drill-btn'));

    await waitFor(() => {
      expect(screen.queryByTestId('agentcore-clear-drill-btn')).toBeNull();
    });

    // Back to the aggregate across both agents (0.01 + 0.002 = 0.012).
    expect(screen.getByTestId('agentcore-vcpu-hours').textContent).toContain('0.0120');
  });

  it('shows an empty state when no agents have Runtime usage', async () => {
    mockedGetAgentCoreCost.mockResolvedValue(emptyResponse);
    render(<AgentCoreCostTab filters={mockFilters} />);

    await waitFor(() => {
      expect(screen.getByText('No AgentCore Runtime usage recorded for the selected range.')).toBeTruthy();
    });
    expect(screen.getByText('No agents with a deployed AgentCore Runtime found.')).toBeTruthy();
  });

  it('shows a disabled-tracking message when configured is false', async () => {
    mockedGetAgentCoreCost.mockResolvedValue(notConfiguredResponse);
    render(<AgentCoreCostTab filters={mockFilters} />);

    await waitFor(() => {
      expect(screen.getByText('AgentCore Cost Tracking Disabled')).toBeTruthy();
    });
    // Should not render the cost cards when tracking is disabled.
    expect(screen.queryByTestId('agentcore-total-cost')).toBeNull();
  });

  it('shows an error banner with retry on API failure', async () => {
    mockedGetAgentCoreCost.mockRejectedValue(new Error('Network error'));
    render(<AgentCoreCostTab filters={mockFilters} />);

    await waitFor(() => {
      expect(screen.getByText('Network error')).toBeTruthy();
    });
    expect(screen.getByText('Retry')).toBeTruthy();
  });

  it('calls getAgentCoreCost with the shared filters', async () => {
    mockedGetAgentCoreCost.mockResolvedValue(emptyResponse);
    render(<AgentCoreCostTab filters={mockFilters} />);

    await waitFor(() => {
      expect(mockedGetAgentCoreCost).toHaveBeenCalledWith(mockFilters, expect.anything());
    });
  });

  it('shows the scanned regions and last scan time in the info banner', async () => {
    mockedGetAgentCoreCost.mockResolvedValue(twoAgentResponse);
    render(<AgentCoreCostTab filters={mockFilters} />);

    await waitFor(() => {
      expect(screen.getByText(/us-west-2, us-east-1/)).toBeTruthy();
    });
  });
});

describe('AgentCoreCostTab — Scan for New Agents (admin only)', () => {
  it('shows the scan button for admins', async () => {
    mockedGetAgentCoreCost.mockResolvedValue(emptyResponse);
    render(<AgentCoreCostTab filters={mockFilters} role="admin" />);

    await waitFor(() => {
      expect(screen.getByTestId('agentcore-scan-now-btn')).toBeTruthy();
    });
  });

  it('hides the scan button for non-admins', async () => {
    mockedGetAgentCoreCost.mockResolvedValue(emptyResponse);
    render(<AgentCoreCostTab filters={mockFilters} role="user" />);

    await waitFor(() => {
      expect(screen.getByTestId('agentcore-total-cost')).toBeTruthy();
    });
    expect(screen.queryByTestId('agentcore-scan-now-btn')).toBeNull();
  });

  it('triggers a scan and shows a success message', async () => {
    mockedGetAgentCoreCost.mockResolvedValue(emptyResponse);
    mockedTriggerScan.mockResolvedValue({ runId: 'abc12345-def6-7890' });
    render(<AgentCoreCostTab filters={mockFilters} role="admin" />);

    await waitFor(() => {
      expect(screen.getByTestId('agentcore-scan-now-btn')).toBeTruthy();
    });

    fireEvent.click(screen.getByTestId('agentcore-scan-now-btn'));

    await waitFor(() => {
      expect(screen.getByTestId('agentcore-scan-success')).toBeTruthy();
    });
    expect(screen.getByTestId('agentcore-scan-success').textContent).toContain('abc12345');
  });

  it('shows an error message when the trigger fails', async () => {
    mockedGetAgentCoreCost.mockResolvedValue(emptyResponse);
    mockedTriggerScan.mockRejectedValue(new Error('forbidden'));
    render(<AgentCoreCostTab filters={mockFilters} role="admin" />);

    await waitFor(() => {
      expect(screen.getByTestId('agentcore-scan-now-btn')).toBeTruthy();
    });

    fireEvent.click(screen.getByTestId('agentcore-scan-now-btn'));

    await waitFor(() => {
      expect(screen.getByTestId('agentcore-scan-error')).toBeTruthy();
    });
    expect(screen.getByTestId('agentcore-scan-error').textContent).toContain('forbidden');
  });
});
