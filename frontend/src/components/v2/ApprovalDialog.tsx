import { useState } from 'react';
import { colors, glass } from '../../theme';
import type { ApprovalAction, ApprovalResult, GuardrailRecommendation } from '../../types/v2';

interface ApprovalDialogProps {
  finding: GuardrailRecommendation;
  onClose: () => void;
  onAction: (action: ApprovalAction) => Promise<ApprovalResult | void> | void;
}

function formatPiiType(piiType: unknown): string {
  return String(piiType ?? 'unknown').replace(/_/g, ' ');
}

/**
 * Approve/Reject/Cancel popup for a Guardrail Recommender finding.
 *
 * Approving calls the backend, which applies the recommendation to a REAL
 * Amazon Bedrock Guardrail (create on first approval, update thereafter).
 * This dialog shows that outcome — the created/updated guardrail's id, or
 * the error if the real AWS call failed — rather than just closing blindly,
 * since "approved" here has a real infrastructure side effect.
 */
export default function ApprovalDialog({ finding, onClose, onAction }: ApprovalDialogProps) {
  const [mode, setMode] = useState<'choose' | 'reject' | 'submitting' | 'result'>('choose');
  const [reason, setReason] = useState('');
  const [result, setResult] = useState<ApprovalResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const rec = finding.recommendation ?? {};
  const piiType = formatPiiType(rec.pii_type ?? rec.type);
  const action = String(rec.action ?? 'ANONYMIZE');
  const description = String(rec.description ?? '');

  const alreadyDecided = finding.status === 'approved' || finding.status === 'rejected';

  const submit = async (payload: ApprovalAction) => {
    setMode('submitting');
    setError(null);
    try {
      const res = await onAction(payload);
      if (res) setResult(res);
      setMode('result');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update finding');
      setMode('choose');
    }
  };

  const handleApprove = () => submit({ status: 'approved' });
  const handleReject = () => {
    if (!reason.trim()) return;
    submit({ status: 'rejected', rejection_reason: reason.trim() });
  };

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(0,0,0,0.6)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 1000,
      }}
      onClick={onClose}
    >
      <div
        style={{
          ...glass.card,
          padding: '1.5rem',
          width: 440,
          maxWidth: '90vw',
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <h3 style={{ margin: '0 0 0.25rem', fontSize: '0.9375rem', color: colors.textPrimary }}>
          Guardrail Recommendation
        </h3>
        <p style={{ margin: '0 0 1rem', fontSize: '0.75rem', color: colors.textMuted }}>
          ID: {finding.id.slice(0, 8)}…
        </p>

        {/* Recommendation summary */}
        <div
          style={{
            background: colors.inputBg,
            border: `1px solid ${colors.cardBorder}`,
            borderRadius: 8,
            padding: '0.75rem 0.875rem',
            marginBottom: '1.25rem',
            fontSize: '0.8125rem',
            color: colors.textSecondary,
            lineHeight: 1.6,
          }}
        >
          <div style={{ fontWeight: 700, color: colors.textPrimary, marginBottom: '0.25rem' }}>
            {action} — {piiType}
          </div>
          {description && <div>{description}</div>}
          <div style={{ marginTop: '0.5rem', fontSize: '0.75rem', color: colors.textMuted }}>
            Approving applies this rule to the dashboard&apos;s managed Bedrock Guardrail
            (creating it on first approval, updating it thereafter) and enforces it on
            the dashboard&apos;s own Agent Chat and Demo Data calls. This does not affect
            Bedrock calls made by other applications in the account.
          </div>
        </div>

        {error && (
          <div
            style={{
              padding: '0.5rem 0.75rem',
              marginBottom: '0.875rem',
              borderRadius: 6,
              background: 'rgba(248,81,73,0.08)',
              border: '1px solid rgba(248,81,73,0.3)',
              color: '#f85149',
              fontSize: '0.75rem',
            }}
          >
            {error}
          </div>
        )}

        {/* Already-decided findings (opened again after the fact): show the
            outcome instead of re-offering approve/reject. */}
        {alreadyDecided && mode !== 'result' && (
          <DecidedOutcome finding={finding} />
        )}

        {mode === 'choose' && !alreadyDecided && (
          <div style={{ display: 'flex', gap: '0.75rem' }}>
            <button
              onClick={handleApprove}
              style={{
                flex: 1,
                padding: '0.5rem',
                border: `1px solid ${colors.accentTeal}44`,
                borderRadius: 6,
                background: `${colors.accentTeal}18`,
                color: colors.accentTeal,
                cursor: 'pointer',
                fontWeight: 600,
                fontSize: '0.8125rem',
              }}
            >
              ✓ Approve
            </button>
            <button
              onClick={() => setMode('reject')}
              style={{
                flex: 1,
                padding: '0.5rem',
                border: `1px solid ${colors.accentOrange}44`,
                borderRadius: 6,
                background: `${colors.accentOrange}18`,
                color: colors.accentOrange,
                cursor: 'pointer',
                fontWeight: 600,
                fontSize: '0.8125rem',
              }}
            >
              ✕ Reject
            </button>
          </div>
        )}

        {mode === 'reject' && (
          <div>
            <label style={{ fontSize: '0.75rem', color: colors.textSecondary, display: 'block', marginBottom: '0.375rem' }}>
              Rejection reason
            </label>
            <textarea
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              rows={3}
              style={{
                width: '100%',
                background: colors.inputBg,
                border: `1px solid ${colors.inputBorder}`,
                borderRadius: 6,
                color: colors.textPrimary,
                padding: '0.5rem',
                fontSize: '0.8125rem',
                resize: 'vertical',
                boxSizing: 'border-box',
              }}
              placeholder="Why is this recommendation being rejected?"
              autoFocus
            />
            <div style={{ display: 'flex', gap: '0.5rem', marginTop: '0.75rem', justifyContent: 'flex-end' }}>
              <button
                onClick={() => setMode('choose')}
                style={{
                  padding: '0.375rem 0.75rem',
                  border: `1px solid ${colors.cardBorder}`,
                  borderRadius: 6,
                  background: 'transparent',
                  color: colors.textSecondary,
                  cursor: 'pointer',
                  fontSize: '0.75rem',
                }}
              >
                Back
              </button>
              <button
                onClick={handleReject}
                disabled={!reason.trim()}
                style={{
                  padding: '0.375rem 0.75rem',
                  border: `1px solid ${colors.accentOrange}44`,
                  borderRadius: 6,
                  background: `${colors.accentOrange}18`,
                  color: reason.trim() ? colors.accentOrange : colors.textMuted,
                  cursor: reason.trim() ? 'pointer' : 'not-allowed',
                  fontWeight: 600,
                  fontSize: '0.75rem',
                }}
              >
                Confirm Reject
              </button>
            </div>
          </div>
        )}

        {mode === 'submitting' && (
          <div style={{ fontSize: '0.8125rem', color: colors.textMuted, textAlign: 'center', padding: '0.5rem 0' }}>
            Applying decision…
          </div>
        )}

        {mode === 'result' && result && (
          <ApprovalResultSummary result={result} />
        )}

        <button
          onClick={onClose}
          disabled={mode === 'submitting'}
          style={{
            marginTop: '1rem',
            width: '100%',
            padding: '0.375rem',
            border: `1px solid ${colors.cardBorder}`,
            borderRadius: 6,
            background: 'transparent',
            color: colors.textMuted,
            cursor: mode === 'submitting' ? 'wait' : 'pointer',
            fontSize: '0.75rem',
          }}
        >
          {mode === 'result' ? 'Close' : 'Cancel'}
        </button>
      </div>
    </div>
  );
}

function DecidedOutcome({ finding }: { finding: GuardrailRecommendation }) {
  if (finding.status === 'rejected') {
    return (
      <div style={{ fontSize: '0.8125rem', color: colors.accentOrange, marginBottom: '0.5rem' }}>
        Rejected{finding.rejection_reason ? `: ${finding.rejection_reason}` : '.'}
      </div>
    );
  }
  return (
    <GuardrailOutcome
      guardrailId={finding.guardrail_id}
      guardrailArn={finding.guardrail_arn}
      publishedVersion={finding.guardrail_published_version}
      creationError={finding.creation_error}
    />
  );
}

function ApprovalResultSummary({ result }: { result: ApprovalResult }) {
  if (result.status === 'rejected') {
    return (
      <div style={{ fontSize: '0.8125rem', color: colors.accentOrange }}>
        Recommendation rejected.
      </div>
    );
  }
  return (
    <GuardrailOutcome
      guardrailId={result.guardrail_id ?? null}
      guardrailArn={result.guardrail_arn ?? null}
      publishedVersion={result.guardrail_published_version ?? null}
      creationError={result.creation_error ?? null}
    />
  );
}

function GuardrailOutcome({
  guardrailId,
  guardrailArn,
  publishedVersion,
  creationError,
}: {
  guardrailId?: string | null;
  guardrailArn?: string | null;
  publishedVersion?: string | null;
  creationError?: string | null;
}) {
  if (creationError) {
    return (
      <div
        style={{
          fontSize: '0.8125rem',
          color: '#f85149',
          background: 'rgba(248,81,73,0.08)',
          border: '1px solid rgba(248,81,73,0.3)',
          borderRadius: 6,
          padding: '0.5rem 0.75rem',
        }}
      >
        Approved, but the real Bedrock Guardrail could not be applied: {creationError}
      </div>
    );
  }
  if (guardrailId) {
    return (
      <div
        style={{
          fontSize: '0.8125rem',
          color: colors.accentTeal,
          background: `${colors.accentTeal}10`,
          border: `1px solid ${colors.accentTeal}33`,
          borderRadius: 6,
          padding: '0.5rem 0.75rem',
        }}
      >
        Applied to Bedrock Guardrail <code>{guardrailId}</code>
        {publishedVersion ? ` (version ${publishedVersion})` : ''}.
        Now enforced on Agent Chat and Demo Data.
        {guardrailArn && (
          <div style={{ fontSize: '0.6875rem', color: colors.textMuted, marginTop: '0.25rem', wordBreak: 'break-all' }}>
            {guardrailArn}
          </div>
        )}
      </div>
    );
  }
  return (
    <div style={{ fontSize: '0.8125rem', color: colors.textMuted }}>
      Approved. No guardrail details are available for this recommendation.
    </div>
  );
}
