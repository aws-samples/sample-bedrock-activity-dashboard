import { useState, useEffect, useRef } from 'react';
import { colors, glass } from '../../theme';
import {
  uploadChargebackCSV,
  getChargebackUploads,
  parseChargebackUpload,
  generateChargebackReport,
  getChargebackReport,
  exportChargebackReport,
  deleteChargebackUpload,
} from '../../services/v2Api';
import type { UploadRecord, ChargebackReportDetail, ChargebackReportLine } from '../../types/v2';
import UnderDevelopmentBanner from '../UnderDevelopmentBanner';

type SubView = 'upload' | 'history' | 'report';

const MAX_FILE_SIZE = 50 * 1024 * 1024;

/** `isAdmin` is cosmetic: the API rejects non-admin upload/parse/report/delete with 403. */
export default function ChargebackTab({ isAdmin = false }: { isAdmin?: boolean }) {
  const [view, setView] = useState<SubView>('history');
  const [uploads, setUploads] = useState<UploadRecord[]>([]);
  const [report, setReport] = useState<ChargebackReportDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const [sortCol, setSortCol] = useState<keyof ChargebackReportLine>('allocated_actual_cost');
  const [sortAsc, setSortAsc] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const fetchUploads = async () => {
    try {
      const data = await getChargebackUploads();
      setUploads(data);
    } catch { setError('Failed to load uploads'); }
  };

  useEffect(() => { fetchUploads(); }, []);

  const handleUpload = async () => {
    const file = fileRef.current?.files?.[0];
    if (!file) return;
    if (!file.name.toLowerCase().endsWith('.csv')) {
      setError('Only .csv files are accepted'); return;
    }
    if (file.size > MAX_FILE_SIZE) {
      setError('File exceeds 50 MB limit'); return;
    }
    setLoading(true); setError(''); setSuccess('');
    try {
      const rec = await uploadChargebackCSV(file);
      setSuccess(`Uploaded ${rec.filename}${rec.billing_period ? ` (${rec.billing_period})` : ''}`);
      if (fileRef.current) fileRef.current.value = '';
      await fetchUploads();
      setView('history');
    } catch { setError('Upload failed'); }
    finally { setLoading(false); }
  };

  const handleParse = async (id: string) => {
    setLoading(true); setError('');
    try { await parseChargebackUpload(id); await fetchUploads(); }
    catch { setError('Parse failed'); }
    finally { setLoading(false); }
  };

  const handleGenerate = async (id: string) => {
    setLoading(true); setError('');
    try {
      const result = await generateChargebackReport(id);
      if (result.report_id) {
        const rpt = await getChargebackReport(result.report_id);
        setReport(rpt);
        setView('report');
      }
    } catch (e) {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setError(detail || 'Report generation failed');
    }
    finally { setLoading(false); }
  };

  const handleViewReport = async (uploadId: string) => {
    setLoading(true); setError('');
    try {
      // Find report by generating (idempotent) or fetching
      const result = await generateChargebackReport(uploadId);
      if (result.report_id) {
        const rpt = await getChargebackReport(result.report_id);
        setReport(rpt);
        setView('report');
      }
    } catch (e) {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setError(detail || 'Failed to load report');
    }
    finally { setLoading(false); }
  };

  const handleExport = async () => {
    if (!report) return;
    try {
      const blob = await exportChargebackReport(report.id);
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `chargeback-${report.billing_period}.csv`;
      a.click();
      URL.revokeObjectURL(url);
    } catch { setError('Export failed'); }
  };

  const handleDelete = async (id: string) => {
    if (!confirm('Delete this upload and all associated data?')) return;
    try { await deleteChargebackUpload(id); await fetchUploads(); }
    catch { setError('Delete failed'); }
  };

  const handleSort = (col: keyof ChargebackReportLine) => {
    if (sortCol === col) setSortAsc(!sortAsc);
    else { setSortCol(col); setSortAsc(true); }
  };

  const sortedLines = report
    ? [...report.lines].sort((a, b) => {
        const av = a[sortCol], bv = b[sortCol];
        const diff = typeof av === 'number' && typeof bv === 'number' ? av - bv : String(av).localeCompare(String(bv));
        return sortAsc ? diff : -diff;
      })
    : [];

  const tabBtn = (label: string, v: SubView) => (
    <button
      onClick={() => setView(v)}
      style={{
        padding: '0.4rem 1rem',
        background: view === v ? colors.tabActiveBg : 'transparent',
        border: 'none',
        borderBottom: view === v ? `2px solid ${colors.tabActiveBorder}` : '2px solid transparent',
        color: view === v ? colors.accentBlue : colors.textSecondary,
        cursor: 'pointer',
        fontSize: '0.8125rem',
        fontWeight: view === v ? 600 : 400,
      }}
    >{label}</button>
  );

  const statusColor = (s: string) =>
    s === 'parsed' ? colors.accentTeal : s === 'error' ? colors.accentOrange : colors.textMuted;

  return (
    <div>
      <UnderDevelopmentBanner />
      <div style={{ display: 'flex', gap: '0.25rem', marginBottom: '1rem', borderBottom: `1px solid ${colors.tableBorder}` }}>
        {isAdmin && tabBtn('Upload', 'upload')}
        {tabBtn('History', 'history')}
        {tabBtn('Report', 'report')}
      </div>

      {error && <div style={{ color: colors.accentOrange, fontSize: '0.8125rem', marginBottom: '0.75rem' }}>⚠ {error}</div>}
      {success && <div style={{ color: colors.accentTeal, fontSize: '0.8125rem', marginBottom: '0.75rem' }}>✓ {success}</div>}

      {/* Upload sub-view */}
      {isAdmin && view === 'upload' && (
        <div style={{ ...glass.card, padding: '1.5rem', maxWidth: 500 }}>
          <h3 style={{ margin: '0 0 1rem', fontSize: '0.9375rem', color: colors.textPrimary }}>Upload Cost Explorer CSV</h3>
          <input
            ref={fileRef}
            type="file"
            accept=".csv"
            style={{ display: 'block', marginBottom: '1rem', color: colors.textSecondary, fontSize: '0.8125rem' }}
          />
          <button
            onClick={handleUpload}
            disabled={loading}
            style={{
              padding: '0.5rem 1.25rem',
              background: colors.btnPrimary,
              border: 'none',
              borderRadius: 6,
              color: '#fff',
              cursor: loading ? 'wait' : 'pointer',
              fontSize: '0.8125rem',
              fontWeight: 600,
            }}
          >
            {loading ? 'Uploading…' : 'Upload CSV'}
          </button>
          <p style={{ marginTop: '0.75rem', fontSize: '0.75rem', color: colors.textMuted }}>
            Accepts .csv files up to 50 MB. The billing period will be auto-detected.
          </p>
        </div>
      )}

      {/* History sub-view */}
      {view === 'history' && (
        <div style={{ ...glass.panel, overflow: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.8125rem' }}>
            <thead>
              <tr style={{ background: colors.tableHeaderBg }}>
                {['Filename', 'Billing Period', 'Uploaded', 'Status', 'Actions'].map(h => (
                  <th key={h} style={{ textAlign: 'left', padding: '0.5rem 0.75rem', color: colors.textMuted, fontWeight: 600, fontSize: '0.6875rem', textTransform: 'uppercase', letterSpacing: '0.04em', borderBottom: `1px solid ${colors.tableBorder}` }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {uploads.length === 0 ? (
                <tr><td colSpan={5} style={{ padding: '1.5rem', textAlign: 'center', color: colors.textMuted }}>No uploads yet</td></tr>
              ) : uploads.map(u => (
                <tr key={u.id} style={{ borderBottom: `1px solid ${colors.tableBorder}` }}>
                  <td style={{ padding: '0.5rem 0.75rem', color: colors.textPrimary }}>{u.filename}</td>
                  <td style={{ padding: '0.5rem 0.75rem', color: colors.textSecondary }}>{u.billing_period || '—'}</td>
                  <td style={{ padding: '0.5rem 0.75rem', color: colors.textMuted, fontSize: '0.75rem' }}>{new Date(u.uploaded_at).toLocaleString()}</td>
                  <td style={{ padding: '0.5rem 0.75rem' }}>
                    <span style={{ color: statusColor(u.status), fontWeight: 600, fontSize: '0.75rem' }}>{u.status}</span>
                    {u.error_message && <div style={{ color: colors.accentOrange, fontSize: '0.6875rem' }}>{u.error_message}</div>}
                    {u.warning_message && <div style={{ color: colors.accentYellow, fontSize: '0.6875rem' }}>{u.warning_message}</div>}
                  </td>
                  <td style={{ padding: '0.5rem 0.75rem' }}>
                    <div style={{ display: 'flex', gap: '0.375rem', flexWrap: 'wrap' }}>
                      {isAdmin && u.status === 'pending' && (
                        <button onClick={() => handleParse(u.id)} disabled={loading} style={actionBtnStyle}>Parse</button>
                      )}
                      {isAdmin && u.status === 'parsed' && (
                        <button onClick={() => handleGenerate(u.id)} disabled={loading} style={actionBtnStyle}>Generate Report</button>
                      )}
                      {u.status === 'parsed' && (
                        <button onClick={() => handleViewReport(u.id)} disabled={loading} style={actionBtnStyle}>View Report</button>
                      )}
                      {isAdmin && <button onClick={() => handleDelete(u.id)} style={{ ...actionBtnStyle, color: colors.accentOrange, borderColor: `${colors.accentOrange}44` }}>Delete</button>}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Report sub-view */}
      {view === 'report' && (
        <div>
          {/* Best-effort disclaimer banner */}
          <div style={{
            ...glass.card,
            padding: '1rem 1.25rem',
            marginBottom: '1rem',
            background: 'rgba(255,201,77,0.08)',
            border: '1px solid rgba(255,201,77,0.25)',
          }}>
            <div style={{ display: 'flex', alignItems: 'flex-start', gap: '0.75rem' }}>
              <span style={{ fontSize: '1.25rem', flexShrink: 0 }}>⚠️</span>
              <div style={{ fontSize: '0.8125rem', color: colors.textSecondary, lineHeight: 1.6 }}>
                <span style={{ fontWeight: 600, color: colors.accentYellow }}>Best-Effort Reconciliation</span> — This report compares actual AWS billing against CloudWatch Logs-based cost estimates. Variance is expected — actual billing may include charges not captured in CloudWatch Logs (e.g., provisioned throughput, model customization, batch inference). Use as a directional guide for proportional cost allocation, not as an exact accounting reconciliation.
              </div>
            </div>
          </div>

          {!report ? (
            <div style={{ color: colors.textMuted, fontSize: '0.8125rem', padding: '2rem 0', textAlign: 'center' }}>
              No report loaded. Select an upload from History and generate a report.
            </div>
          ) : (
            <>
              {/* Summary cards */}
              <div style={{ display: 'flex', gap: '1rem', marginBottom: '1rem', flexWrap: 'wrap' }}>
                {[
                  { label: 'Billing Period', value: report.billing_period, color: colors.accentBlue },
                  { label: 'Actual Cost', value: `$${Number(report.total_actual_cost).toLocaleString(undefined, { minimumFractionDigits: 2 })}`, color: colors.accentOrange },
                  { label: 'Estimated Cost', value: `$${Number(report.total_estimated_cost).toLocaleString(undefined, { minimumFractionDigits: 2 })}`, color: colors.accentTeal },
                  { label: 'Variance', value: `$${Number(report.total_variance).toLocaleString(undefined, { minimumFractionDigits: 2 })}`, color: Number(report.total_variance) > 0 ? colors.accentOrange : colors.accentTeal },
                ].map(c => (
                  <div key={c.label} style={{ ...glass.card, padding: '1rem 1.25rem', flex: '1 1 140px', minWidth: 140 }}>
                    <div style={{ fontSize: '0.6875rem', color: colors.textMuted, textTransform: 'uppercase', letterSpacing: '0.04em', marginBottom: '0.25rem' }}>{c.label}</div>
                    <div style={{ fontSize: '1.125rem', fontWeight: 700, color: c.color }}>{c.value}</div>
                  </div>
                ))}
              </div>

              {/* Insights section */}
              {report.insights && (
                <div style={{ ...glass.card, padding: '1rem 1.25rem', marginBottom: '1rem' }}>
                  <div style={{ fontSize: '0.75rem', color: colors.textMuted, textTransform: 'uppercase', letterSpacing: '0.04em', marginBottom: '0.5rem' }}>Agent Insights</div>
                  <div style={{ fontSize: '0.8125rem', color: colors.textSecondary, lineHeight: 1.7, whiteSpace: 'pre-line' }}>{report.insights}</div>
                </div>
              )}

              {/* Export button */}
              <div style={{ marginBottom: '0.75rem' }}>
                <button onClick={handleExport} style={{ padding: '0.4rem 1rem', background: `${colors.accentBlue}18`, border: `1px solid ${colors.accentBlue}44`, borderRadius: 6, color: colors.accentBlue, cursor: 'pointer', fontSize: '0.8125rem', fontWeight: 600 }}>
                  ⬇ Export CSV
                </button>
              </div>

              {/* Report table */}
              <div style={{ ...glass.panel, overflow: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.8125rem' }}>
                  <thead>
                    <tr style={{ background: colors.tableHeaderBg }}>
                      {([
                        ['entity_type', 'Type'],
                        ['entity_name', 'Name'],
                        ['estimated_cost', 'Estimated Cost'],
                        ['allocated_actual_cost', 'Actual Cost'],
                        ['variance', 'Variance'],
                        ['percentage_share', 'Share %'],
                      ] as [keyof ChargebackReportLine, string][]).map(([key, label]) => (
                        <th
                          key={key}
                          onClick={() => handleSort(key)}
                          style={{ textAlign: 'left', padding: '0.5rem 0.75rem', color: colors.textMuted, fontWeight: 600, fontSize: '0.6875rem', textTransform: 'uppercase', letterSpacing: '0.04em', borderBottom: `1px solid ${colors.tableBorder}`, cursor: 'pointer', userSelect: 'none' }}
                        >
                          {label} {sortCol === key ? (sortAsc ? '▲' : '▼') : ''}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {sortedLines.map((l, i) => (
                      <tr key={i} style={{ borderBottom: `1px solid ${colors.tableBorder}` }}>
                        <td style={{ padding: '0.5rem 0.75rem' }}>
                          <span style={{ fontSize: '0.6875rem', padding: '0.1rem 0.4rem', borderRadius: 4, background: l.entity_type === 'team' ? `${colors.accentPurple}18` : `${colors.accentBlue}18`, color: l.entity_type === 'team' ? colors.accentPurple : colors.accentBlue }}>{l.entity_type}</span>
                        </td>
                        <td style={{ padding: '0.5rem 0.75rem', color: colors.textPrimary, fontWeight: 500 }}>{l.entity_name}</td>
                        <td style={{ padding: '0.5rem 0.75rem', color: colors.textSecondary }}>${Number(l.estimated_cost).toFixed(4)}</td>
                        <td style={{ padding: '0.5rem 0.75rem', color: colors.textSecondary }}>${Number(l.allocated_actual_cost).toFixed(4)}</td>
                        <td style={{ padding: '0.5rem 0.75rem', color: Number(l.variance) > 0 ? colors.accentOrange : colors.accentTeal }}>${Number(l.variance).toFixed(4)}</td>
                        <td style={{ padding: '0.5rem 0.75rem', color: colors.textSecondary }}>{Number(l.percentage_share).toFixed(2)}%</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}

const actionBtnStyle: React.CSSProperties = {
  padding: '0.2rem 0.5rem',
  background: `rgba(96,176,255,0.08)`,
  border: `1px solid rgba(96,176,255,0.3)`,
  borderRadius: 4,
  color: '#60b0ff',
  cursor: 'pointer',
  fontSize: '0.6875rem',
  fontWeight: 600,
};
