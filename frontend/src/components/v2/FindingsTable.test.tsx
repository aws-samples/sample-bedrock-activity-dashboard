import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import FindingsTable from './FindingsTable';
import type { AgentFinding, CostAnomaly, GuardrailRecommendation } from '../../types/v2';

vi.mock('../../services/v2Api', () => ({
  getAgentFindings: vi.fn(),
  updateFindingStatus: vi.fn(),
}));

import { getAgentFindings, updateFindingStatus } from '../../services/v2Api';

const mockedGetAgentFindings = vi.mocked(getAgentFindings);
const mockedUpdateFindingStatus = vi.mocked(updateFindingStatus);

// A non-guardrail finding fixture used to prove the generic Finding Details
// modal (as opposed to the Approve/Reject popup) renders for anything that
// isn't a guardrail-recommender finding. Uses cost-anomaly-alerter now that
// the pii-detector agent has been retired from the platform.
function nonGuardrailFinding(): CostAnomaly {
  return {
    id: 'anom-1',
    agent_name: 'cost-anomaly-alerter',
    status: 'active',
    severity: 'high',
    created_at: '2024-01-15T12:00:00Z',
    updated_at: '2024-01-15T12:00:00Z',
    anomaly_date: '2024-01-14',
    current_spend: 450.0,
    rolling_average: 100.0,
    threshold_multiplier: 2.0,
    contributing_users: null,
    team_id: null,
  };
}

function guardrailFinding(overrides: Partial<GuardrailRecommendation> = {}): GuardrailRecommendation {
  return {
    id: 'guardrail-1',
    agent_name: 'guardrail-recommender',
    status: 'pending_review',
    severity: 'medium',
    created_at: '2024-01-15T12:00:00Z',
    updated_at: '2024-01-15T12:00:00Z',
    recommendation: { pii_type: 'ssn', type: 'PII', action: 'BLOCK', description: 'SSNs detected' },
    based_on_findings: ['ssn'],
    rejection_reason: null,
    guardrail_id: null,
    guardrail_arn: null,
    guardrail_version: null,
    guardrail_published_version: null,
    creation_error: null,
    ...overrides,
  };
}

describe('FindingsTable', () => {
  beforeEach(() => {
    mockedGetAgentFindings.mockReset();
    mockedUpdateFindingStatus.mockReset();
  });

  it('clicking a guardrail-recommender row opens the Approve/Reject/Cancel popup, not the raw-data modal', async () => {
    const gr = guardrailFinding();
    mockedGetAgentFindings.mockResolvedValue([gr as AgentFinding]);

    render(<FindingsTable agentName="guardrail-recommender" isAdmin />);

    await waitFor(() => expect(screen.getByText('guardrail-recommender')).toBeTruthy());

    fireEvent.click(screen.getByText('guardrail-recommender'));

    expect(screen.getByText('Guardrail Recommendation')).toBeTruthy();
    expect(screen.getByText('✓ Approve')).toBeTruthy();
    expect(screen.getByText('✕ Reject')).toBeTruthy();
    expect(screen.getByText('Cancel')).toBeTruthy();
    // The generic "Finding Details" raw-data modal must NOT open for guardrail findings.
    expect(screen.queryByText('Finding Details')).toBeNull();
  });

  it('non-admin never sees the Approve/Reject popup for a guardrail finding', async () => {
    mockedGetAgentFindings.mockResolvedValue([guardrailFinding() as AgentFinding]);
    render(<FindingsTable agentName="guardrail-recommender" />);
    await waitFor(() => expect(screen.getByText('guardrail-recommender')).toBeTruthy());
    expect(screen.queryByText('Review')).toBeNull();
    fireEvent.click(screen.getByText('guardrail-recommender'));
    expect(screen.queryByText('✓ Approve')).toBeNull();
  });
  it('clicking a non-guardrail row still opens the generic Finding Details modal', async () => {
    mockedGetAgentFindings.mockResolvedValue([nonGuardrailFinding() as AgentFinding]);

    render(<FindingsTable agentName="cost-anomaly-alerter" />);

    await waitFor(() => expect(screen.getByText('cost-anomaly-alerter')).toBeTruthy());
    fireEvent.click(screen.getByText('cost-anomaly-alerter'));

    expect(screen.getByText('Finding Details')).toBeTruthy();
    expect(screen.queryByText('Guardrail Recommendation')).toBeNull();
  });

  it('approving from the popup calls updateFindingStatus and refreshes findings', async () => {
    const gr = guardrailFinding();
    mockedGetAgentFindings.mockResolvedValue([gr as AgentFinding]);
    mockedUpdateFindingStatus.mockResolvedValue({
      id: 'guardrail-1',
      agent_name: 'guardrail-recommender',
      status: 'approved',
      updated_at: '2024-01-15T12:05:00Z',
      rejection_reason: null,
      guardrail_id: 'abc123',
      guardrail_arn: 'arn:aws:bedrock:us-west-2:123456789012:guardrail/abc123',
      guardrail_version: 'DRAFT',
      creation_error: null,
    });

    render(<FindingsTable agentName="guardrail-recommender" isAdmin />);
    await waitFor(() => expect(screen.getByText('guardrail-recommender')).toBeTruthy());

    fireEvent.click(screen.getByText('guardrail-recommender'));
    fireEvent.click(screen.getByText('✓ Approve'));

    await waitFor(() => {
      expect(mockedUpdateFindingStatus).toHaveBeenCalledWith('guardrail-1', { status: 'approved' });
    });
    // Table is refetched after the decision.
    await waitFor(() => expect(mockedGetAgentFindings).toHaveBeenCalledTimes(2));
  });

  it('the Review button also opens the popup without triggering the row-click detail modal', async () => {
    const gr = guardrailFinding();
    mockedGetAgentFindings.mockResolvedValue([gr as AgentFinding]);

    render(<FindingsTable agentName="guardrail-recommender" isAdmin />);
    await waitFor(() => expect(screen.getByText('Review')).toBeTruthy());

    fireEvent.click(screen.getByText('Review'));

    expect(screen.getByText('Guardrail Recommendation')).toBeTruthy();
    expect(screen.queryByText('Finding Details')).toBeNull();
  });
});
