import React, { useEffect, useState } from 'react';
import type { DashboardFilters, PromptHistoryEntry, PromptDetail, PaginatedResponse } from '../types';
import { getPromptHistory, getPromptDetail } from '../api/client';
import { colors, glass } from '../theme';
import LoadingSpinner from './LoadingSpinner';
import ErrorBanner from './ErrorBanner';
import ExportButton from './ExportButton';
import DeploymentDisclaimer from './v2/DeploymentDisclaimer';
import { formatCost } from '../services/formatCost';

interface PromptHistoryTabProps {
  filters: DashboardFilters;
}

const PAGE_SIZE = 25;

function formatNumber(n: number): string { return n.toLocaleString(); }
function formatTimestamp(iso: string): string {
  return new Date(iso).toLocaleString();
}

const thStyle: React.CSSProperties = {
  padding: '0.625rem 0.875rem',
  textAlign: 'left',
  fontSize: '0.6875rem',
  fontWeight: 600,
  color: colors.textMuted,
  borderBottom: `1px solid ${colors.tableBorder}`,
  whiteSpace: 'nowrap',
  letterSpacing: '0.05em',
  textTransform: 'uppercase',
  background: colors.tableHeaderBg,
};

const tdStyle: React.CSSProperties = {
  padding: '0.625rem 0.875rem',
  fontSize: '0.8125rem',
  borderBottom: `1px solid ${colors.tableBorder}`,
  color: colors.textPrimary,
};

const PromptHistoryTab: React.FC<PromptHistoryTabProps> = ({ filters }) => {
  const [pageData, setPageData] = useState<PaginatedResponse<PromptHistoryEntry> | null>(null);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedDetail, setSelectedDetail] = useState<PromptDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);

  const fetchData = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await getPromptHistory(filters, page, PAGE_SIZE);
      setPageData(data);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to load prompt history');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { setPage(1); }, [filters]);
  // eslint-disable-next-line react-hooks/exhaustive-deps -- fetchData is recreated each render; effect must re-run only when filters/page change (adding it would loop)
  useEffect(() => { fetchData(); }, [filters, page]);

  const handleRowClick = async (id: string) => {
    setDetailLoading(true);
    setDetailError(null);
    try {
      const detail = await getPromptDetail(id, filters);
      setSelectedDetail(detail);
    } catch (err: unknown) {
      setDetailError(err instanceof Error ? err.message : 'Failed to load prompt detail');
    } finally {
      setDetailLoading(false);
    }
  };

  const closeModal = () => { setSelectedDetail(null); setDetailError(null); };

  if (loading) return <LoadingSpinner message="Loading prompt history..." />;
  if (error) return <ErrorBanner message={error} onRetry={fetchData} />;
  if (!pageData) return null;

  const { items, total, totalPages } = pageData;

  const paginationBtn = (label: string, onClick: () => void, disabled: boolean) => (
    <button
      onClick={onClick}
      disabled={disabled}
      style={{
        padding: '0.4rem 0.875rem',
        border: `1px solid ${disabled ? colors.tableBorder : colors.btnSecondaryBorder}`,
        borderRadius: '5px',
        background: disabled ? 'rgba(255,255,255,0.03)' : 'linear-gradient(135deg, #1a2332 0%, #243447 100%)',
        color: disabled ? colors.textMuted : colors.accentBlue,
        cursor: disabled ? 'default' : 'pointer',
        fontSize: '0.8125rem',
        fontWeight: 500,
      }}
    >
      {label}
    </button>
  );

  return (
    <div data-testid="tab-prompt-history">
      {/* Info banner */}
      <div style={{ marginBottom: '1rem', padding: '1rem 1.25rem', background: 'rgba(88,166,255,0.04)', border: '1px solid rgba(88,166,255,0.12)', borderRadius: 10 }}>
        <div style={{ fontSize: '0.8125rem', color: colors.textSecondary, lineHeight: 1.6 }}>
          Full history of Bedrock invocations with token counts and cost estimates. Click any row to view the prompt and response detail.
          The Prompt History toggle in Admin → Settings gates the <em>body</em> of every record (prompt and response text) across all sources — Agent Chat, demo, CloudWatch Logs, S3 invocation logs, and CloudTrail. Metadata remains visible either way. For records this app persists (Agent Chat and demo), bodies are encrypted at rest with a Fernet key provisioned via Secrets Manager; records fetched live from AWS log sources rely on the at-rest encryption AWS applies to those services.
        </div>
      </div>

      {/* Deployment disclaimer — visible every time an operator views stored
          prompt content, so no-warranty / no-liability language travels with
          the data. Rendered above the actions row so it isn't buried below
          the fold. */}
      <DeploymentDisclaimer
        heading="Prompt data handling"
        context={
          'This page may display prompt and response text that your end users submitted ' +
          'to Bedrock models — including any PII, PHI, financial, or other regulated data ' +
          'they entered. Compliance with HIPAA, GDPR, PCI, and any other applicable ' +
          'regulations for the content you choose to retain and surface here is entirely ' +
          'your responsibility.'
        }
        style={{ marginBottom: '1.25rem' }}
      />

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.875rem' }}>
        <div style={{ fontSize: '0.8125rem', color: colors.textMuted }}>
          {total} {total === 1 ? 'record' : 'records'}
        </div>
        <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
          <button
            onClick={fetchData}
            disabled={loading}
            style={{
              background: 'rgba(255,255,255,0.06)',
              border: `1px solid ${colors.inputBorder}`,
              borderRadius: 6,
              color: colors.textSecondary,
              cursor: loading ? 'not-allowed' : 'pointer',
              fontSize: '0.75rem',
              padding: '0.35rem 0.75rem',
              display: 'flex',
              alignItems: 'center',
              gap: '0.375rem',
              transition: 'background 0.15s',
            }}
            title="Refresh data"
          >
            <span style={{ display: 'inline-block', animation: loading ? 'spin 1s linear infinite' : 'none' }}>↻</span>
            Refresh
          </button>
          <ExportButton section="prompt-history" filters={filters} />
        </div>
      </div>

      {items.length === 0 ? (
        <div style={{ ...glass.panel, padding: '2rem', textAlign: 'center', color: colors.textMuted, fontSize: '0.875rem' }}>
          No prompt history available for the selected filters.
        </div>
      ) : (
        <>
          <div style={{
            background: 'rgba(255,255,255,0.03)',
            border: '1px solid rgba(255,255,255,0.06)',
            borderLeft: `4px solid ${colors.accentBlue}`,
            borderRadius: '0 10px 10px 0',
            overflow: 'hidden',
            marginBottom: '1rem',
          }}>
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead>
                  <tr>
                    <th style={thStyle}>Timestamp</th>
                    <th style={thStyle}>User</th>
                    <th style={thStyle}>Model</th>
                    <th style={thStyle}>Source</th>
                    <th style={{ ...thStyle, textAlign: 'right' }}>Input</th>
                    <th style={{ ...thStyle, textAlign: 'right' }}>Output</th>
                    <th style={{ ...thStyle, textAlign: 'right' }}>Cache R</th>
                    <th style={{ ...thStyle, textAlign: 'right' }}>Cache W</th>
                    <th style={{ ...thStyle, textAlign: 'right' }}>Cost</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((entry) => (
                    <tr
                      key={entry.id}
                      data-testid={`prompt-row-${entry.id}`}
                      onClick={() => handleRowClick(entry.id)}
                      style={{ cursor: 'pointer', transition: 'background 0.1s' }}
                      onMouseOver={(e) => { (e.currentTarget as HTMLTableRowElement).style.background = colors.tableRowHover; }}
                      onMouseOut={(e) => { (e.currentTarget as HTMLTableRowElement).style.background = ''; }}
                    >
                      <td style={tdStyle}>{formatTimestamp(entry.timestamp)}</td>
                      <td style={{ ...tdStyle, color: colors.accentBlue }}>{entry.userName}</td>
                      <td style={{ ...tdStyle, color: colors.accentPurple, fontSize: '0.75rem' }}>{entry.modelId}</td>
                      <td style={tdStyle}>{entry.sourceApplication}</td>
                      <td style={{ ...tdStyle, textAlign: 'right', color: entry.inputTokens > 5000 ? '#f85149' : entry.inputTokens > 1000 ? colors.accentOrange : colors.textPrimary, fontWeight: entry.inputTokens > 5000 ? 700 : 400 }}>
                        {formatNumber(entry.inputTokens)}
                        {entry.inputTokens > 5000 && <span style={{ marginLeft: '0.25rem', fontSize: '0.6rem' }}>🔥</span>}
                      </td>
                      <td style={{ ...tdStyle, textAlign: 'right', color: entry.outputTokens > 5000 ? '#f85149' : entry.outputTokens > 1000 ? colors.accentOrange : colors.textPrimary, fontWeight: entry.outputTokens > 5000 ? 700 : 400 }}>
                        {formatNumber(entry.outputTokens)}
                        {entry.outputTokens > 5000 && <span style={{ marginLeft: '0.25rem', fontSize: '0.6rem' }}>🔥</span>}
                      </td>
                      <td style={{ ...tdStyle, textAlign: 'right', color: colors.textMuted }}>{formatNumber(entry.cacheReadTokens)}</td>
                      <td style={{ ...tdStyle, textAlign: 'right', color: colors.textMuted }}>{formatNumber(entry.cacheWriteTokens)}</td>
                      <td style={{ ...tdStyle, textAlign: 'right', color: colors.accentTeal }}>{formatCost(entry.costEstimate)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '1rem', fontSize: '0.8125rem' }} data-testid="pagination-controls">
            {paginationBtn('← Prev', () => setPage((p) => Math.max(1, p - 1)), page <= 1)}
            <span style={{ color: colors.textSecondary }}>Page {page} of {totalPages}</span>
            {paginationBtn('Next →', () => setPage((p) => Math.min(totalPages, p + 1)), page >= totalPages)}
          </div>
        </>
      )}

      {/* Detail modal */}
      {(detailLoading || selectedDetail || detailError) && (
        <div
          style={{
            position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
            background: 'rgba(0,0,0,0.7)',
            backdropFilter: 'blur(4px)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            zIndex: 1000,
          }}
          data-testid="prompt-detail-modal"
          onClick={(e) => { if (e.target === e.currentTarget) closeModal(); }}
          role="dialog"
          aria-modal="true"
          aria-label="Prompt detail"
        >
          <div style={{
            ...glass.card,
            padding: '1.5rem',
            maxWidth: '720px',
            width: '90%',
            maxHeight: '82vh',
            overflow: 'auto',
          }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1.25rem' }}>
              <h3 style={{ margin: 0, fontSize: '1rem', color: colors.textPrimary, fontWeight: 600 }}>Prompt Detail</h3>
              <button
                onClick={closeModal}
                data-testid="close-detail-modal"
                style={{
                  background: 'rgba(255,255,255,0.06)',
                  border: `1px solid ${colors.cardBorder}`,
                  borderRadius: '6px',
                  fontSize: '0.875rem',
                  cursor: 'pointer',
                  color: colors.textSecondary,
                  padding: '0.25rem 0.5rem',
                  lineHeight: 1,
                }}
                aria-label="Close"
              >
                ✕
              </button>
            </div>

            {detailLoading && <LoadingSpinner message="Loading detail..." />}
            {detailError && <ErrorBanner message={detailError} />}
            {selectedDetail && !detailLoading && (
              <div>
                <div style={{
                  ...glass.panel,
                  padding: '0.875rem 1rem',
                  marginBottom: '1.25rem',
                  display: 'grid',
                  gridTemplateColumns: '1fr 1fr',
                  gap: '0.375rem 1.5rem',
                  fontSize: '0.8125rem',
                }}>
                  {[
                    ['Timestamp', formatTimestamp(selectedDetail.timestamp)],
                    ['User', selectedDetail.userName],
                    ['Model', selectedDetail.modelId],
                    ['Source', selectedDetail.sourceApplication],
                    ['Cost', formatCost(selectedDetail.costEstimate)],
                  ].map(([k, v]) => (
                    <div key={k}>
                      <span style={{ color: colors.textMuted, fontSize: '0.6875rem', textTransform: 'uppercase', letterSpacing: '0.05em' }}>{k}</span>
                      <div style={{ color: colors.textPrimary, marginTop: '1px' }}>{v}</div>
                    </div>
                  ))}
                </div>

                {selectedDetail.promptBodyHidden ? (
                  <div
                    data-testid="prompt-body-hidden-notice"
                    style={{
                      padding: '0.875rem 1rem',
                      borderRadius: '6px',
                      background: 'rgba(227,179,65,0.08)',
                      border: '1px solid rgba(227,179,65,0.35)',
                      color: colors.textSecondary,
                      fontSize: '0.8125rem',
                      lineHeight: 1.6,
                    }}
                  >
                    <strong style={{ color: '#e3b341' }}>Prompt and response content is hidden.</strong>{' '}
                    An administrator has disabled Prompt History in Admin → Settings,
                    so the body of this invocation is not surfaced through the UI
                    regardless of where the record came from (Agent Chat, demo,
                    CloudWatch Logs, S3 invocation logs, or CloudTrail). Metadata
                    (timestamp, user, model, source, cost, tokens) remains available
                    above. Re-enable Prompt History to view the content.
                  </div>
                ) : (
                  <>
                    <div style={{ marginBottom: '0.375rem', fontSize: '0.6875rem', color: colors.textMuted, textTransform: 'uppercase', letterSpacing: '0.05em', fontWeight: 600 }}>Prompt</div>
                    <pre
                      data-testid="prompt-text"
                      style={{
                        background: 'rgba(88,166,255,0.05)',
                        border: `1px solid rgba(88,166,255,0.15)`,
                        padding: '0.875rem',
                        borderRadius: '6px',
                        fontSize: '0.8125rem',
                        whiteSpace: 'pre-wrap',
                        wordBreak: 'break-word',
                        maxHeight: '200px',
                        overflow: 'auto',
                        marginBottom: '1rem',
                        color: colors.textPrimary,
                        lineHeight: 1.6,
                      }}
                    >
                      {selectedDetail.promptText || '(no prompt text available)'}
                    </pre>

                    <div style={{ marginBottom: '0.375rem', fontSize: '0.6875rem', color: colors.textMuted, textTransform: 'uppercase', letterSpacing: '0.05em', fontWeight: 600 }}>Response</div>
                    <pre
                      data-testid="response-text"
                      style={{
                        background: 'rgba(57,211,83,0.05)',
                        border: `1px solid rgba(57,211,83,0.15)`,
                        padding: '0.875rem',
                        borderRadius: '6px',
                        fontSize: '0.8125rem',
                        whiteSpace: 'pre-wrap',
                        wordBreak: 'break-word',
                        maxHeight: '200px',
                        overflow: 'auto',
                        color: colors.textPrimary,
                        lineHeight: 1.6,
                      }}
                    >
                      {selectedDetail.responseText || '(no response text available)'}
                    </pre>
                  </>
                )}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
};

export default PromptHistoryTab;
