import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import ApprovalDialog from './ApprovalDialog';
import type { GuardrailRecommendation } from '../../types/v2';

function makeFinding(overrides: Partial<GuardrailRecommendation> = {}): GuardrailRecommendation {
  return {
    id: 'finding-1234567890',
    agent_name: 'guardrail-recommender',
    status: 'pending_review',
    severity: 'medium',
    created_at: '2024-01-15T12:00:00Z',
    updated_at: '2024-01-15T12:00:00Z',
    recommendation: { pii_type: 'ssn', type: 'PII', action: 'BLOCK', description: 'Social Security Numbers detected' },
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

describe('ApprovalDialog', () => {
  const onClose = vi.fn();
  const onAction = vi.fn();

  beforeEach(() => {
    onClose.mockReset();
    onAction.mockReset();
  });

  it('renders the recommendation summary and Approve/Reject/Cancel controls', () => {
    render(<ApprovalDialog finding={makeFinding()} onClose={onClose} onAction={onAction} />);

    expect(screen.getByText('Guardrail Recommendation')).toBeTruthy();
    expect(screen.getByText('BLOCK — ssn')).toBeTruthy();
    expect(screen.getByText('Social Security Numbers detected')).toBeTruthy();
    expect(screen.getByText('✓ Approve')).toBeTruthy();
    expect(screen.getByText('✕ Reject')).toBeTruthy();
    expect(screen.getByText('Cancel')).toBeTruthy();
  });

  it('Cancel button calls onClose without submitting an action', () => {
    render(<ApprovalDialog finding={makeFinding()} onClose={onClose} onAction={onAction} />);
    fireEvent.click(screen.getByText('Cancel'));
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(onAction).not.toHaveBeenCalled();
  });

  it('Approve submits an approved action and shows the created guardrail id on success', async () => {
    onAction.mockResolvedValue({
      id: 'finding-1234567890',
      agent_name: 'guardrail-recommender',
      status: 'approved',
      updated_at: '2024-01-15T12:05:00Z',
      rejection_reason: null,
      guardrail_id: 'abc123',
      guardrail_arn: 'arn:aws:bedrock:us-west-2:123456789012:guardrail/abc123',
      guardrail_version: 'DRAFT',
      creation_error: null,
    });

    render(<ApprovalDialog finding={makeFinding()} onClose={onClose} onAction={onAction} />);
    fireEvent.click(screen.getByText('✓ Approve'));

    expect(onAction).toHaveBeenCalledWith({ status: 'approved' });

    await waitFor(() => {
      expect(screen.getByText(/Applied to Bedrock Guardrail/)).toBeTruthy();
    });
    expect(screen.getByText('abc123')).toBeTruthy();
  });

  it('Approve shows the AWS failure message when the real guardrail call fails', async () => {
    onAction.mockResolvedValue({
      id: 'finding-1234567890',
      agent_name: 'guardrail-recommender',
      status: 'approved',
      updated_at: '2024-01-15T12:05:00Z',
      rejection_reason: null,
      guardrail_id: null,
      guardrail_arn: null,
      guardrail_version: null,
      creation_error: 'AccessDeniedException: missing permissions',
    });

    render(<ApprovalDialog finding={makeFinding()} onClose={onClose} onAction={onAction} />);
    fireEvent.click(screen.getByText('✓ Approve'));

    await waitFor(() => {
      expect(screen.getByText(/AccessDeniedException: missing permissions/)).toBeTruthy();
    });
  });

  it('Reject requires a reason before Confirm Reject is enabled', () => {
    render(<ApprovalDialog finding={makeFinding()} onClose={onClose} onAction={onAction} />);
    fireEvent.click(screen.getByText('✕ Reject'));

    const confirmBtn = screen.getByText('Confirm Reject') as HTMLButtonElement;
    expect(confirmBtn.disabled).toBe(true);

    fireEvent.change(screen.getByPlaceholderText('Why is this recommendation being rejected?'), {
      target: { value: 'Not applicable' },
    });
    expect(confirmBtn.disabled).toBe(false);
  });

  it('Confirm Reject submits a rejected action with the reason', async () => {
    onAction.mockResolvedValue({
      id: 'finding-1234567890',
      agent_name: 'guardrail-recommender',
      status: 'rejected',
      updated_at: '2024-01-15T12:05:00Z',
      rejection_reason: 'Not applicable',
    });

    render(<ApprovalDialog finding={makeFinding()} onClose={onClose} onAction={onAction} />);
    fireEvent.click(screen.getByText('✕ Reject'));
    fireEvent.change(screen.getByPlaceholderText('Why is this recommendation being rejected?'), {
      target: { value: 'Not applicable' },
    });
    fireEvent.click(screen.getByText('Confirm Reject'));

    expect(onAction).toHaveBeenCalledWith({ status: 'rejected', rejection_reason: 'Not applicable' });
    await waitFor(() => {
      expect(screen.getByText('Recommendation rejected.')).toBeTruthy();
    });
  });

  it('shows the already-decided outcome instead of Approve/Reject when finding is already approved', () => {
    render(
      <ApprovalDialog
        finding={makeFinding({
          status: 'approved',
          guardrail_id: 'abc123',
          guardrail_arn: 'arn:aws:bedrock:us-west-2:123456789012:guardrail/abc123',
        })}
        onClose={onClose}
        onAction={onAction}
      />,
    );

    expect(screen.queryByText('✓ Approve')).toBeNull();
    expect(screen.queryByText('✕ Reject')).toBeNull();
    expect(screen.getByText(/Applied to Bedrock Guardrail/)).toBeTruthy();
  });

  it('surfaces an error and re-enables actions when onAction rejects', async () => {
    onAction.mockRejectedValue(new Error('Network error'));

    render(<ApprovalDialog finding={makeFinding()} onClose={onClose} onAction={onAction} />);
    fireEvent.click(screen.getByText('✓ Approve'));

    await waitFor(() => {
      expect(screen.getByText('Network error')).toBeTruthy();
    });
    // Back to the choose state so the user can retry.
    expect(screen.getByText('✓ Approve')).toBeTruthy();
  });
});
