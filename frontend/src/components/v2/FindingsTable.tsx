import { useState, useEffect } from 'react';
import { colors, glass } from '../../theme';
import { getAgentFindings, updateFindingStatus } from '../../services/v2Api';
import type { AgentFinding, ApprovalAction, GuardrailRecommendation } from '../../types/v2';
import ApprovalDialog from './ApprovalDialog';

interface FindingsTableProps {
  agentName: string;
  /** Cosmetic only: the API enforces admin-only approve/reject (403 otherwise). */
  isAdmin?: boolean;
}

const GUARDRAIL_AGENT_NAME = 'guardrail-recommender';

function isGuardrailFinding(f: AgentFinding): f is GuardrailRecommendation {
  return f.agent_name === GUARDRAIL_AGENT_NAME;
}

const SEVERITY_COLOR: Record<string, string> = {
  critical: '#f85149',
  high: colors.accentOrange,
  medium: colors.accentYellow,
  low: colors.accentBlue,
};

const SEVERITY_GRADIENT: Record<string, string> = {
  critical: 'linear-gradient(135deg, #f85149, #da3633)',
  high: 'linear-gradient(135deg, #f78166, #e3b341)',
  medium: 'linear-gradient(135deg, #e3b341, #d2a832)',
  low: 'linear-gradient(135deg, #58a6ff, #388bfd)',
};

const STATUS_OPTIONS = ['', 'active', 'resolved', 'pending_review', 'approved', 'rejected'];
const SEVERITY_OPTIONS = ['', 'critical', 'high', 'medium', 'low'];

function getDetailText(f: AgentFinding): string {
  const raw: Record<string, unknown> = f as unknown as Record<string, unknown>;
  // Generic informational finding (e.g. CUR Ingestion) — title carries the summary
  if (raw.title) {
    const lines: string[] = [String(raw.title)];
    const details = raw.details as Record<string, unknown> | null | undefined;
    if (details?.latest_billing_period) lines.push(`Latest billing period: ${details.latest_billing_period}`);
    return lines.join('\n');
  }
  // Model cost optimizer — natural language recommendation with reasoning
  if (raw.current_model && raw.suggested_model) {
    const isDistillationCandidate = raw.is_distillation_candidate === true;
    const distillationPrefix = isDistillationCandidate ? '🧪 Distillation candidate — ' : '';
    // If we have a rich usage_pattern_summary with reasoning, show it
    if (raw.usage_pattern_summary && String(raw.usage_pattern_summary).includes('\n')) {
      return String(raw.usage_pattern_summary);
    }
    const savings = Number(raw.estimated_monthly_savings ?? 0);
    const users = Number(raw.affected_user_count ?? 0);
    const current = String(raw.current_model).split('.').pop()?.replace(/-v\d.*/, '') ?? raw.current_model;
    const suggested = String(raw.suggested_model).split('.').pop()?.replace(/-v\d.*/, '') ?? raw.suggested_model;
    if (suggested === 'unknown' || savings === 0) {
      return `${distillationPrefix}${users} user${users !== 1 ? 's' : ''} using ${current} for simple tasks — consider switching to a cheaper model`;
    }
    return `${distillationPrefix}${users} user${users !== 1 ? 's' : ''} could save ~$${savings.toFixed(2)}/mo by switching from ${current} to ${suggested} for low-token tasks`;
  }
  // PII detector
  if (raw.pii_type) {
    const type = String(raw.pii_type).replace(/_/g, ' ');
    const tier = raw.detection_tier === 'ai' ? 'AI-confirmed' : 'regex match';
    return `${type} detected (${tier}, ${Math.round(Number(raw.confidence_score ?? 0) * 100)}% confidence)`;
  }
  // Cost anomaly
  if (raw.anomaly_date) {
    const spend = Number(raw.current_spend ?? 0).toFixed(2);
    const avg = Number(raw.rolling_average ?? 0).toFixed(2);
    const ratio = Number(raw.rolling_average) > 0 ? (Number(raw.current_spend) / Number(raw.rolling_average)).toFixed(1) : '?';
    return `Spend of $${spend} on ${raw.anomaly_date} was ${ratio}x the $${avg} rolling average`;
  }
  // Latency/error
  if (raw.metric_type) {
    const model = String(raw.affected_model ?? 'unknown').split('.').pop() ?? '';
    if (raw.metric_type === 'latency_spike') return `${model} p95 latency spiked to ${raw.current_value}ms (baseline: ${raw.baseline_value}ms)`;
    return `${model} error rate at ${(Number(raw.current_value) * 100).toFixed(1)}% (threshold: 5%)`;
  }
  // Onboarding
  if (raw.user_identity && raw.guidance_content) return `New user ${String(raw.user_identity).split('/').pop()} onboarded with guidelines`;
  // Residency violation
  if (raw.target_region) return `Invocation to ${raw.target_region} violates data residency policy`;
  // Chargeback
  if (raw.team_name) return `${raw.team_name}: $${Number(raw.total_cost ?? 0).toFixed(2)} for the period`;
  // Model adoption
  if (raw.source_model && raw.target_model) {
    const delta = Number(raw.cost_delta ?? 0);
    return `Consider migrating from ${String(raw.source_model).split('.').pop()} to ${String(raw.target_model).split('.').pop()} (${delta < 0 ? 'saves' : 'costs'} $${Math.abs(delta).toFixed(4)}/1K tokens)`;
  }
  // Prompt quality
  if (raw.anti_pattern_type) {
    const pattern = String(raw.anti_pattern_type).replace(/_/g, ' ');
    return `${pattern} detected${raw.quality_score != null ? ` (quality score: ${raw.quality_score}/100)` : ''}`;
  }
  // Usage forecast
  if (raw.forecast_period_days) return `${raw.forecast_period_days}-day forecast: ~${Number(raw.projected_tokens ?? 0).toLocaleString()} tokens, ~$${Number(raw.projected_cost ?? 0).toFixed(2)}`;
  // Policy violation
  if (raw.policy_rule_id) return `Policy violation by ${String(raw.user_identity ?? 'unknown').split('/').pop()}`;
  // Digest
  if (raw.delivery_status) return `Weekly digest ${raw.delivery_status}`;
  // Guardrail recommendation
  if (raw.recommendation) {
    const rec = raw.recommendation as Record<string, unknown>;
    const piiType = String(rec.pii_type ?? rec.type ?? 'unknown').replace(/_/g, ' ');
    const action = String(rec.guardrail_action ?? rec.action ?? 'ANONYMIZE');
    const desc = String(rec.description ?? '');
    const count = Number(rec.affected_count ?? 0);

    const lines: string[] = [];
    lines.push(`Guardrail Recommendation: ${action} ${piiType}`);
    if (desc) lines.push(desc);
    if (count > 0) lines.push(`Based on ${count} PII finding${count !== 1 ? 's' : ''} detected`);
    if (raw.guardrail_id) {
      lines.push(`Applied to Bedrock Guardrail ${raw.guardrail_id}`);
    } else if (raw.creation_error) {
      lines.push(`Approved, but Bedrock Guardrail creation failed: ${raw.creation_error}`);
    }
    return lines.join('\n');
  }
  if (raw.usage_pattern_summary) return String(raw.usage_pattern_summary);
  return '—';
}

export default function FindingsTable({ agentName, isAdmin = false }: FindingsTableProps) {
  const [findings, setFindings] = useState<AgentFinding[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [severityFilter, setSeverityFilter] = useState('');
  const [sortAsc, setSortAsc] = useState(false);
  const [approvalTarget, setApprovalTarget] = useState<GuardrailRecommendation | null>(null);
  const [detailFinding, setDetailFinding] = useState<AgentFinding | null>(null);

  const fetchFindings = async () => {
    try {
      setLoading(true);
      const filters: Record<string, string> = {};
      if (statusFilter) filters.status = statusFilter;
      if (severityFilter) filters.severity = severityFilter;
      const data = await getAgentFindings(agentName, filters);
      setFindings(data);
      setError('');
    } catch {
      setError('Failed to load findings');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchFindings();
  }, [agentName, statusFilter, severityFilter]); // eslint-disable-line react-hooks/exhaustive-deps

  const sorted = [...findings].sort((a, b) => {
    const diff = new Date(a.created_at).getTime() - new Date(b.created_at).getTime();
    return sortAsc ? diff : -diff;
  });

  const handleApproval = async (action: ApprovalAction) => {
    if (!approvalTarget) return;
    // Let the result/error propagate to ApprovalDialog so it can show the
    // real Bedrock Guardrail outcome (or surface the failure) — only refresh
    // the underlying table here, don't close the dialog ourselves.
    const result = await updateFindingStatus(approvalTarget.id, action);
    fetchFindings();
    return result;
  };

  const handleRowClick = (f: AgentFinding) => {
    if (isAdmin && isGuardrailFinding(f)) {
      setApprovalTarget(f);
    } else {
      setDetailFinding(f);
    }
  };

  const selectStyle: React.CSSProperties = {
    background: colors.inputBg,
    border: `1px solid ${colors.inputBorder}`,
    borderRadius: 6,
    color: colors.textPrimary,
    padding: '0.3rem 0.5rem',
    fontSize: '0.75rem',
  };

  return (
    <div>
      {/* Filters */}
      <div style={{ display: 'flex', gap: '0.75rem', marginBottom: '0.75rem', alignItems: 'center' }}>
        <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)} style={selectStyle}>
          <option value="">All statuses</option>
          {STATUS_OPTIONS.filter(Boolean).map((s) => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>
        <select value={severityFilter} onChange={(e) => setSeverityFilter(e.target.value)} style={selectStyle}>
          <option value="">All severities</option>
          {SEVERITY_OPTIONS.filter(Boolean).map((s) => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>
        <button
          onClick={() => setSortAsc((p) => !p)}
          style={{ ...selectStyle, cursor: 'pointer' }}
        >
          Date {sortAsc ? '▲' : '▼'}
        </button>
      </div>

      {error && (
        <div style={{ color: colors.accentOrange, fontSize: '0.8125rem', marginBottom: '0.5rem' }}>⚠ {error}</div>
      )}

      {loading ? (
        <div style={{ color: colors.textMuted, fontSize: '0.8125rem', padding: '1rem 0' }}>Loading…</div>
      ) : (
        <div style={{ ...glass.panel, overflow: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.8125rem' }}>
            <thead>
              <tr style={{ background: colors.tableHeaderBg }}>
                {['Severity', 'Agent', 'Status', 'Created', 'Details', ''].map((h) => (
                  <th
                    key={h}
                    style={{
                      textAlign: 'left',
                      padding: '0.5rem 0.75rem',
                      color: colors.textMuted,
                      fontWeight: 600,
                      fontSize: '0.6875rem',
                      textTransform: 'uppercase',
                      letterSpacing: '0.04em',
                      borderBottom: `1px solid ${colors.tableBorder}`,
                    }}
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {sorted.length === 0 ? (
                <tr>
                  <td colSpan={6} style={{ padding: '1.5rem', textAlign: 'center', color: colors.textMuted }}>
                    No findings
                  </td>
                </tr>
              ) : (
                sorted.map((f) => (
                  <tr key={f.id} style={{ borderBottom: `1px solid ${colors.tableBorder}`, cursor: 'pointer' }} onClick={() => handleRowClick(f)}>
                    <td style={{ padding: '0.5rem 0.75rem' }}>
                      <span
                        style={{
                          display: 'inline-block',
                          padding: '0.2rem 0.6rem',
                          borderRadius: 6,
                          fontSize: '0.6875rem',
                          fontWeight: 700,
                          color: '#fff',
                          background: SEVERITY_GRADIENT[f.severity] ?? colors.textSecondary,
                          boxShadow: `0 2px 8px ${SEVERITY_COLOR[f.severity] ?? colors.textSecondary}44`,
                          letterSpacing: '0.03em',
                          textTransform: 'uppercase',
                        }}
                      >
                        {f.severity}
                      </span>
                    </td>
                    <td style={{ padding: '0.5rem 0.75rem', color: colors.textSecondary }}>{f.agent_name}</td>
                    <td style={{ padding: '0.5rem 0.75rem', color: colors.textSecondary }}>{f.status}</td>
                    <td style={{ padding: '0.5rem 0.75rem', color: colors.textMuted, fontSize: '0.75rem' }}>
                      {new Date(f.created_at).toLocaleString()}
                    </td>
                    <td style={{ padding: '0.5rem 0.75rem', color: colors.textSecondary, maxWidth: 320, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {getDetailText(f)}
                    </td>
                    <td style={{ padding: '0.5rem 0.75rem' }}>
                      {isAdmin && isGuardrailFinding(f) && (
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            setApprovalTarget(f);
                          }}
                          style={{
                            background: `${colors.accentBlue}18`,
                            border: `1px solid ${colors.accentBlue}44`,
                            borderRadius: 4,
                            color: colors.accentBlue,
                            cursor: 'pointer',
                            fontSize: '0.6875rem',
                            padding: '0.2rem 0.5rem',
                          }}
                        >
                          Review
                        </button>
                      )}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      )}

      {approvalTarget && (
        <ApprovalDialog
          finding={approvalTarget}
          onClose={() => setApprovalTarget(null)}
          onAction={handleApproval}
        />
      )}

      {detailFinding && (
        <div
          style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000 }}
          onClick={() => setDetailFinding(null)}
        >
          <div
            style={{ ...glass.card, padding: '1.5rem', width: 560, maxWidth: '90vw', maxHeight: '80vh', overflowY: 'auto' }}
            onClick={(e) => e.stopPropagation()}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
              <h3 style={{ margin: 0, fontSize: '0.9375rem', color: colors.textPrimary }}>Finding Details</h3>
              <button onClick={() => setDetailFinding(null)} style={{ background: 'none', border: 'none', color: colors.textMuted, cursor: 'pointer', fontSize: '1.25rem' }}>✕</button>
            </div>
            <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '1rem' }}>
              <span style={{ fontSize: '0.6875rem', padding: '0.15rem 0.5rem', borderRadius: 4, fontWeight: 600, color: SEVERITY_COLOR[detailFinding.severity] ?? colors.textSecondary, background: `${SEVERITY_COLOR[detailFinding.severity] ?? colors.textSecondary}18`, border: `1px solid ${SEVERITY_COLOR[detailFinding.severity] ?? colors.textSecondary}44` }}>{detailFinding.severity}</span>
              <span style={{ fontSize: '0.6875rem', padding: '0.15rem 0.5rem', borderRadius: 4, color: colors.textSecondary, background: colors.inputBg }}>{detailFinding.status}</span>
            </div>
            <div style={{ fontSize: '0.875rem', color: colors.textPrimary, lineHeight: 1.6, marginBottom: '1rem', whiteSpace: 'pre-line' }}>{getDetailText(detailFinding)}</div>
            <div style={{ fontSize: '0.75rem', color: colors.textMuted, marginBottom: '1rem' }}>
              <div>Agent: {detailFinding.agent_name}</div>
              <div>Created: {new Date(detailFinding.created_at).toLocaleString()}</div>
              <div>ID: {detailFinding.id}</div>
            </div>
            <details style={{ fontSize: '0.75rem', color: colors.textMuted }}>
              <summary style={{ cursor: 'pointer', marginBottom: '0.5rem' }}>Raw data</summary>
              <pre style={{ background: colors.inputBg, padding: '0.75rem', borderRadius: 6, overflow: 'auto', maxHeight: 200, fontSize: '0.6875rem', lineHeight: 1.4 }}>
                {JSON.stringify(detailFinding, null, 2)}
              </pre>
            </details>
          </div>
        </div>
      )}
    </div>
  );
}
