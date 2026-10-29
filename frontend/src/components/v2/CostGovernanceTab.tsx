import React, { useEffect, useMemo, useState } from 'react';
import {
  BarChart,
  Bar,
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from 'recharts';
import type { DashboardFilters } from '../../types';
import type { UserRole } from '../../auth/cognito';
import type {
  CacheHitResponse,
  CacheHitRow,
  CanaryResponse,
  CostPerOutcomeRow,
  CurStatus,
  ExecutiveRollupResponse,
  ReconciliationSlice,
  SpendByApplicationResponse,
} from '../../types/governance';
import {
  getCacheHit,
  getCanary,
  getCostPerOutcome,
  getCurStatus,
  getExecutiveRollup,
  getReconciliation,
  getSpendByApplication,
  triggerCurIngest,
} from '../../api/governance';
import { formatCost } from '../../services/formatCost';
import LoadingSpinner from '../LoadingSpinner';
import ErrorBanner from '../ErrorBanner';
import { colors, glass } from '../../theme';

interface CostGovernanceTabProps {
  filters: DashboardFilters;
  /** Caller role — the "Ingest Now" trigger is shown only to admins. */
  role?: UserRole;
}

type Segment = 'overview' | 'reconciliation' | 'efficiency' | 'alerts';

type HealthStatus = 'good' | 'warn' | 'bad' | 'neutral';

const RECON_PAGE_SIZE = 15;

// ---------------------------------------------------------------------------
// Formatting helpers
// ---------------------------------------------------------------------------

/** Render a ratio in [0,1] as a percentage string, or "N/A" when null. */
function formatRatio(ratio: number | null | undefined): string {
  if (ratio === null || ratio === undefined || !Number.isFinite(ratio)) return 'N/A';
  return `${(ratio * 100).toFixed(1)}%`;
}

/** Render a reconciliation factor with 3 decimals. */
function formatFactor(factor: number): string {
  return Number.isFinite(factor) ? factor.toFixed(3) : 'N/A';
}

/**
 * Weighted average cache-hit ratio across a set of rows, or null when there's
 * no usable data (empty input or every row's denominator is zero).
 */
function aggregateCacheHitRatio(rows: CacheHitRow[]): number | null {
  const totalInput = rows.reduce((sum, r) => sum + r.inputTokens, 0);
  const totalCacheRead = rows.reduce((sum, r) => sum + r.cacheReadTokens, 0);
  const denominator = totalInput + totalCacheRead;
  if (denominator <= 0) return null;
  return totalCacheRead / denominator;
}

// ---------------------------------------------------------------------------
// Shared styles
// ---------------------------------------------------------------------------

const sectionStyle = (accent: string): React.CSSProperties => ({
  background: 'rgba(255,255,255,0.03)',
  border: '1px solid rgba(255,255,255,0.06)',
  borderLeft: `4px solid ${accent}`,
  borderRadius: '0 10px 10px 0',
  padding: '1.25rem',
  marginBottom: '1.5rem',
});

const sectionHeader: React.CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  gap: '0.5rem',
  marginBottom: '1rem',
  flexWrap: 'wrap',
};

const sectionTitle: React.CSSProperties = {
  fontWeight: 700,
  fontSize: '0.9375rem',
  color: colors.textPrimary,
};

const thStyle: React.CSSProperties = {
  padding: '0.5rem 0.75rem',
  textAlign: 'left',
  fontSize: '0.75rem',
  fontWeight: 600,
  color: colors.textSecondary,
  borderBottom: `1px solid ${colors.tableBorder}`,
  background: colors.tableHeaderBg,
  whiteSpace: 'nowrap',
};

const tdStyle: React.CSSProperties = {
  padding: '0.5rem 0.75rem',
  fontSize: '0.8125rem',
  borderBottom: `1px solid ${colors.tableBorder}`,
  color: colors.textPrimary,
};

const noteStyle: React.CSSProperties = {
  fontSize: '0.75rem',
  color: colors.textMuted,
  marginTop: '0.75rem',
  lineHeight: 1.5,
};

const emptyStyle: React.CSSProperties = {
  color: colors.textMuted,
  fontSize: '0.875rem',
  margin: 0,
};

const chartTooltipStyle = {
  background: 'rgba(13,17,23,0.95)',
  border: `1px solid ${colors.cardBorder}`,
  borderRadius: '8px',
  fontSize: '0.8125rem',
};

const HEALTH_COLOR: Record<HealthStatus, string> = {
  good: colors.accentTeal,
  warn: colors.accentYellow,
  bad: colors.accentOrange,
  neutral: colors.accentBlue,
};

// ---------------------------------------------------------------------------
// Small presentational atoms
// ---------------------------------------------------------------------------

/** Basis pill: Reconciled / Estimated. Kept subtle — this is provenance, not a KPI. */
function BasisPill({ label, accent }: { label: string; accent: string }): React.ReactElement {
  return (
    <span
      style={{
        fontSize: '0.6875rem',
        fontWeight: 700,
        letterSpacing: '0.04em',
        textTransform: 'uppercase',
        padding: '0.2rem 0.55rem',
        borderRadius: 999,
        background: `${accent}1f`,
        color: accent,
        border: `1px solid ${accent}55`,
      }}
    >
      {label}
    </span>
  );
}

function SummaryCard({
  label,
  value,
  accent,
  testId,
}: {
  label: string;
  value: string;
  accent: string;
  testId?: string;
}): React.ReactElement {
  return (
    <div
      style={{
        background: 'rgba(255,255,255,0.03)',
        border: '1px solid rgba(255,255,255,0.06)',
        borderLeft: `4px solid ${accent}`,
        borderRadius: '0 10px 10px 0',
        flex: '1 1 180px',
        padding: '1rem 1.25rem',
        minWidth: '160px',
      }}
    >
      <div style={{ fontSize: '0.75rem', color: colors.textMuted, fontWeight: 500, marginBottom: '0.375rem' }}>
        {label}
      </div>
      <div style={{ fontSize: '1.75rem', fontWeight: 700, color: accent }} data-testid={testId}>
        {value}
      </div>
    </div>
  );
}

/**
 * Top-of-page KPI card with a status dot (green/amber/red/neutral) so the
 * viewer can tell "healthy" from "needs attention" without reading every
 * number — the goal is a glanceable summary before anyone opens a segment.
 */
function KpiCard({
  label,
  value,
  status,
  detail,
  testId,
}: {
  label: string;
  value: string;
  status: HealthStatus;
  detail?: string;
  testId?: string;
}): React.ReactElement {
  const accent = HEALTH_COLOR[status];
  return (
    <div
      data-testid={testId}
      style={{
        ...glass.card,
        flex: '1 1 200px',
        minWidth: '180px',
        padding: '1.125rem 1.25rem',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.5rem' }}>
        <span
          aria-hidden="true"
          style={{
            width: 8,
            height: 8,
            borderRadius: '50%',
            background: accent,
            boxShadow: `0 0 6px ${accent}`,
            flexShrink: 0,
          }}
        />
        <span style={{ fontSize: '0.75rem', color: colors.textMuted, fontWeight: 500 }}>{label}</span>
      </div>
      <div style={{ fontSize: '1.5rem', fontWeight: 700, color: colors.textPrimary }}>{value}</div>
      {detail && <div style={{ fontSize: '0.6875rem', color: colors.textMuted, marginTop: '0.25rem' }}>{detail}</div>}
    </div>
  );
}

/** Segmented nav button — same visual language as ChargebackTab's sub-view tabs. */
function SegmentButton({
  label,
  active,
  onClick,
  testId,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
  testId: string;
}): React.ReactElement {
  return (
    <button
      onClick={onClick}
      data-testid={testId}
      style={{
        padding: '0.5rem 1.125rem',
        background: active ? colors.tabActiveBg : 'transparent',
        border: 'none',
        borderBottom: active ? `2px solid ${colors.tabActiveBorder}` : '2px solid transparent',
        color: active ? colors.accentBlue : colors.textSecondary,
        cursor: 'pointer',
        fontSize: '0.8125rem',
        fontWeight: active ? 600 : 400,
      }}
    >
      {label}
    </button>
  );
}

/** Small "?" info affordance with a native title tooltip — used to explain jargon columns inline. */
function InfoHint({ text }: { text: string }): React.ReactElement {
  return (
    <span
      title={text}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        width: 14,
        height: 14,
        borderRadius: '50%',
        background: 'rgba(255,255,255,0.08)',
        color: colors.textMuted,
        fontSize: '0.625rem',
        fontWeight: 700,
        marginLeft: '0.3rem',
        cursor: 'help',
      }}
    >
      ?
    </span>
  );
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

const CostGovernanceTab: React.FC<CostGovernanceTabProps> = ({ filters, role }) => {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [curStatus, setCurStatus] = useState<CurStatus | null>(null);
  const [reconciliation, setReconciliation] = useState<ReconciliationSlice[]>([]);
  const [cacheHit, setCacheHit] = useState<CacheHitResponse | null>(null);
  const [costPerOutcome, setCostPerOutcome] = useState<CostPerOutcomeRow[]>([]);
  const [canary, setCanary] = useState<CanaryResponse | null>(null);
  const [rollup, setRollup] = useState<ExecutiveRollupResponse | null>(null);
  const [spendByApplication, setSpendByApplication] = useState<SpendByApplicationResponse | null>(null);
  const [selectedDepartment, setSelectedDepartment] = useState<string | null>(null);
  const [rollupLoading, setRollupLoading] = useState(false);
  const [rollupError, setRollupError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [ingesting, setIngesting] = useState(false);
  const [ingestMessage, setIngestMessage] = useState<string | null>(null);
  const [ingestError, setIngestError] = useState<string | null>(null);

  const [segment, setSegment] = useState<Segment>('overview');

  // Reconciliation Detail: client-side search + sort + pagination (all rows
  // are already fetched; there's no need for a second network round trip
  // just to page through data we have).
  const [reconSearch, setReconSearch] = useState('');
  const [reconPage, setReconPage] = useState(1);
  const [reconSortKey, setReconSortKey] = useState<keyof ReconciliationSlice>('day');
  const [reconSortAsc, setReconSortAsc] = useState(false);

  const isAdmin = role === 'admin';

  const handleIngestNow = async (): Promise<void> => {
    setIngesting(true);
    setIngestError(null);
    setIngestMessage(null);
    try {
      const { runId } = await triggerCurIngest();
      setIngestMessage(
        `Ingestion started (run ${runId.slice(0, 8)}). It runs against Athena in the ` +
          'background and can take up to a few minutes — refresh this tab afterward to ' +
          'see the updated "covered through" date.'
      );
    } catch (err: unknown) {
      setIngestError(err instanceof Error ? err.message : 'Failed to trigger CUR ingestion');
    } finally {
      setIngesting(false);
    }
  };

  useEffect(() => {
    const controller = new AbortController();
    const { signal } = controller;

    async function load(): Promise<void> {
      setLoading(true);
      setError(null);
      try {
        const [status, recon, cache, cpo, can, spendByApp] = await Promise.all([
          getCurStatus(signal),
          getReconciliation(filters, signal),
          getCacheHit(filters, signal),
          getCostPerOutcome(filters, signal),
          getCanary(filters, signal),
          getSpendByApplication(filters, signal),
        ]);
        if (signal.aborted) return;
        setCurStatus(status);
        setReconciliation(recon);
        setCacheHit(cache);
        setCostPerOutcome(cpo);
        setCanary(can);
        setSpendByApplication(spendByApp);
      } catch (err: unknown) {
        // Ignore cancellations from filter change / unmount.
        if (signal.aborted) return;
        setError(err instanceof Error ? err.message : 'Failed to load cost governance data');
      } finally {
        if (!signal.aborted) setLoading(false);
      }
    }

    void load();
    return () => controller.abort();
  }, [filters, reloadKey]);

  // Reset the drill-down and the reconciliation table's search/page state
  // whenever the shared date-range/user filters change so a stale selection
  // from a previous window isn't carried over.
  useEffect(() => {
    setSelectedDepartment(null);
    setReconSearch('');
    setReconPage(1);
  }, [filters]);

  // Executive rollup (Total -> Department -> Agent) is fetched separately
  // from the other governance sections so drilling into a department only
  // re-fetches this one section, not the whole tab.
  useEffect(() => {
    const controller = new AbortController();
    const { signal } = controller;

    async function loadRollup(): Promise<void> {
      setRollupLoading(true);
      setRollupError(null);
      try {
        const data = await getExecutiveRollup(filters, selectedDepartment ?? undefined, signal);
        if (signal.aborted) return;
        setRollup(data);
      } catch (err: unknown) {
        if (signal.aborted) return;
        setRollupError(err instanceof Error ? err.message : 'Failed to load executive rollup');
      } finally {
        if (!signal.aborted) setRollupLoading(false);
      }
    }

    void loadRollup();
    return () => controller.abort();
  }, [filters, selectedDepartment, reloadKey]);

  // --- Derived figures, computed once per data change ---
  const curAvailable = curStatus?.curAvailable ?? false;
  const estimatedTotal = useMemo(
    () => reconciliation.reduce((sum, s) => sum + (s.estimated || 0), 0),
    [reconciliation]
  );
  const billedSlices = useMemo(
    () => reconciliation.filter((s) => s.billed !== null && s.billed !== undefined),
    [reconciliation]
  );
  const billedTotal = curAvailable && billedSlices.length > 0
    ? billedSlices.reduce((sum, s) => sum + (s.billed ?? 0), 0)
    : null;
  const varianceTotal = billedTotal !== null ? billedTotal - estimatedTotal : null;
  const variancePct = billedTotal !== null && estimatedTotal > 0
    ? (varianceTotal ?? 0) / estimatedTotal
    : null;
  const basisLabel = curAvailable ? 'Reconciled' : 'Estimated (no billed data available)';
  const basisAccent = curAvailable ? colors.accentTeal : colors.accentBlue;

  const aggregateCacheHit = useMemo(
    () => (cacheHit ? aggregateCacheHitRatio(cacheHit.byModel) : null),
    [cacheHit]
  );

  // --- KPI strip status classification (thresholds are a UI-only judgment
  // call, not a governance policy — the canary's own `meetsTarget` still
  // drives the authoritative Alerts status). ---
  const totalSpendValue = billedTotal ?? estimatedTotal;
  const varianceStatus: HealthStatus = variancePct === null
    ? 'neutral'
    : Math.abs(variancePct) <= 0.05 ? 'good' : Math.abs(variancePct) <= 0.15 ? 'warn' : 'bad';
  const unassignedStatus: HealthStatus = !canary ? 'neutral' : canary.meetsTarget ? 'good' : 'bad';
  const cacheHitStatus: HealthStatus = aggregateCacheHit === null
    ? 'neutral'
    : aggregateCacheHit >= 0.5 ? 'good' : aggregateCacheHit >= 0.2 ? 'warn' : 'bad';

  // --- Reconciliation Detail: filter -> sort -> paginate (client-side) ---
  const filteredRecon = useMemo(() => {
    const q = reconSearch.trim().toLowerCase();
    if (!q) return reconciliation;
    return reconciliation.filter(
      (s) =>
        (s.teamOrPrincipal || '').toLowerCase().includes(q) ||
        (s.model || '').toLowerCase().includes(q)
    );
  }, [reconciliation, reconSearch]);

  const sortedRecon = useMemo(() => {
    const rows = [...filteredRecon];
    rows.sort((a, b) => {
      const av = a[reconSortKey];
      const bv = b[reconSortKey];
      let diff: number;
      if (typeof av === 'number' && typeof bv === 'number') {
        diff = av - bv;
      } else {
        diff = String(av ?? '').localeCompare(String(bv ?? ''));
      }
      return reconSortAsc ? diff : -diff;
    });
    return rows;
  }, [filteredRecon, reconSortKey, reconSortAsc]);

  const reconTotalPages = Math.max(1, Math.ceil(sortedRecon.length / RECON_PAGE_SIZE));
  const reconPageClamped = Math.min(reconPage, reconTotalPages);
  const reconPageRows = sortedRecon.slice(
    (reconPageClamped - 1) * RECON_PAGE_SIZE,
    reconPageClamped * RECON_PAGE_SIZE
  );

  const handleReconSort = (key: keyof ReconciliationSlice) => {
    if (reconSortKey === key) {
      setReconSortAsc((v) => !v);
    } else {
      setReconSortKey(key);
      setReconSortAsc(true);
    }
    setReconPage(1);
  };

  const reconSortIndicator = (key: keyof ReconciliationSlice) =>
    reconSortKey === key ? (reconSortAsc ? ' ▲' : ' ▼') : '';

  if (loading) return <LoadingSpinner message="Loading cost governance..." />;
  if (error) return <ErrorBanner message={error} onRetry={() => setReloadKey((k) => k + 1)} />;

  return (
    <div data-testid="tab-cost-governance">
      {/* Info banner */}
      <div style={{ marginBottom: '1.25rem', padding: '1rem 1.25rem', background: 'rgba(88,166,255,0.04)', border: '1px solid rgba(88,166,255,0.12)', borderRadius: 10 }}>
        <div style={{ fontSize: '0.8125rem', color: colors.textSecondary, lineHeight: 1.6 }}>
          Cost governance layers authoritative billed cost (when a CUR export is available) on top of the
          token-based estimate. The dashboard is a passive observer: it reconciles and labels figures but never
          enforces or throttles. Per-prompt cost is always an estimate.
        </div>
      </div>

      {/* KPI strip — glanceable health check before drilling into any segment. */}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.75rem', marginBottom: '1.5rem' }} data-testid="kpi-strip">
        <KpiCard
          label="Total Spend"
          value={formatCost(totalSpendValue, 2)}
          status="neutral"
          detail={curAvailable ? 'Billed' : 'Estimated — no billed data yet'}
          testId="kpi-total-spend"
        />
        <KpiCard
          label="Billed vs Estimated"
          value={variancePct === null ? 'N/A' : `${variancePct >= 0 ? '+' : ''}${(variancePct * 100).toFixed(1)}%`}
          status={varianceStatus}
          detail={variancePct === null ? 'No billed data to compare yet' : 'vs. token-based estimate'}
          testId="kpi-variance"
        />
        <KpiCard
          label="Unassigned Spend"
          value={formatRatio(canary?.unattributedSpendShare)}
          status={unassignedStatus}
          detail={canary ? `Target: ${formatRatio(canary.target)}` : undefined}
          testId="kpi-unassigned-spend"
        />
        <KpiCard
          label="Cache-Hit Rate"
          value={formatRatio(aggregateCacheHit)}
          status={cacheHitStatus}
          detail="Share of input tokens served from cache"
          testId="kpi-cache-hit"
        />
      </div>

      {/* Segmented nav */}
      <div style={{ display: 'flex', gap: '0.25rem', marginBottom: '1.5rem', borderBottom: `1px solid ${colors.tableBorder}` }}>
        <SegmentButton label="Overview" active={segment === 'overview'} onClick={() => setSegment('overview')} testId="segment-overview" />
        <SegmentButton label="Reconciliation Detail" active={segment === 'reconciliation'} onClick={() => setSegment('reconciliation')} testId="segment-reconciliation" />
        <SegmentButton label="Efficiency" active={segment === 'efficiency'} onClick={() => setSegment('efficiency')} testId="segment-efficiency" />
        <SegmentButton label="Alerts" active={segment === 'alerts'} onClick={() => setSegment('alerts')} testId="segment-alerts" />
      </div>

      {/* ------------------------------------------------------------- */}
      {/* Overview: spend total + breadcrumb drill-down (Total -> Dept -> Agent) */}
      {/* ------------------------------------------------------------- */}
      {segment === 'overview' && (
        <div style={sectionStyle(colors.accentPurple)} data-testid="segment-panel-overview">
          <div style={sectionHeader}>
            <span style={{ fontSize: '1.125rem' }}>🏢</span>
            <span style={sectionTitle}>Spend Breakdown</span>
            {rollup && (
              <BasisPill
                label={rollup.basis === 'reconciled' ? 'Reconciled' : 'Estimated'}
                accent={rollup.basis === 'reconciled' ? colors.accentTeal : colors.accentBlue}
              />
            )}
          </div>

          {/* Breadcrumb: Total > Department (only when drilled in) */}
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', marginBottom: '1rem', fontSize: '0.8125rem' }} data-testid="rollup-breadcrumb">
            <button
              onClick={() => setSelectedDepartment(null)}
              data-testid="rollup-breadcrumb-total"
              style={{
                border: 'none',
                background: 'none',
                cursor: selectedDepartment ? 'pointer' : 'default',
                padding: 0,
                fontWeight: selectedDepartment ? 400 : 700,
                color: selectedDepartment ? colors.accentBlue : colors.textPrimary,
                fontSize: '0.8125rem',
              }}
            >
              Total Spend
            </button>
            {selectedDepartment && (
              <>
                <span style={{ color: colors.textMuted }}>›</span>
                <span style={{ fontWeight: 700, color: colors.textPrimary }}>{selectedDepartment}</span>
              </>
            )}
          </div>

          {rollupError ? (
            <ErrorBanner message={rollupError} onRetry={() => setReloadKey((k) => k + 1)} />
          ) : rollupLoading && !rollup ? (
            <p style={emptyStyle}>Loading spend breakdown…</p>
          ) : !rollup ? (
            <p style={emptyStyle}>No spend data available for the selected range.</p>
          ) : (
            <>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.75rem', marginBottom: '1.25rem' }}>
                <SummaryCard
                  label={selectedDepartment ? `Total — ${selectedDepartment}` : 'Total (All Departments)'}
                  value={formatCost(rollup.l1Total, 2)}
                  accent={colors.accentPurple}
                  testId="rollup-l1-total"
                />
                <SummaryCard
                  label="Invocations"
                  value={rollup.l1InvocationCount.toLocaleString()}
                  accent={colors.accentBlue}
                  testId="rollup-l1-invocations"
                />
              </div>

              {/* By Department — click a row to drill into its agents */}
              <div style={{ fontSize: '0.8125rem', fontWeight: 600, color: colors.textSecondary, marginBottom: '0.75rem' }}>
                By Department {!selectedDepartment && '— click a row to see its agents'}
              </div>
              {rollup.byDepartment.length === 0 ? (
                <p style={emptyStyle}>No department data available.</p>
              ) : (
                <div style={{ overflowX: 'auto', marginBottom: '1.25rem' }}>
                  <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                    <thead>
                      <tr>
                        <th style={thStyle}>Department</th>
                        <th style={{ ...thStyle, textAlign: 'right' }}>Cost</th>
                        <th style={{ ...thStyle, textAlign: 'right' }}>Invocations</th>
                      </tr>
                    </thead>
                    <tbody>
                      {rollup.byDepartment.map((row) => {
                        const isSelected = selectedDepartment === row.label;
                        return (
                          <tr
                            key={row.label}
                            onClick={() => setSelectedDepartment(isSelected ? null : row.label)}
                            data-testid={`rollup-department-row-${row.label}`}
                            style={{
                              cursor: 'pointer',
                              background: isSelected ? `${colors.accentPurple}22` : 'transparent',
                              boxShadow: isSelected ? `inset 3px 0 0 0 ${colors.accentPurple}` : undefined,
                            }}
                            onMouseEnter={(e) => {
                              if (!isSelected) (e.currentTarget as HTMLTableRowElement).style.background = colors.tableRowHover;
                            }}
                            onMouseLeave={(e) => {
                              if (!isSelected) (e.currentTarget as HTMLTableRowElement).style.background = 'transparent';
                            }}
                          >
                            <td style={{ ...tdStyle, fontWeight: isSelected ? 700 : 400, color: isSelected ? colors.accentPurple : colors.textPrimary }}>
                              {isSelected ? '▸ ' : ''}{row.label}
                            </td>
                            <td style={{ ...tdStyle, textAlign: 'right' }}>{formatCost(row.cost)}</td>
                            <td style={{ ...tdStyle, textAlign: 'right' }}>{row.invocationCount.toLocaleString()}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}

              {/* By Agent, scoped to the selected department (or all, if none selected) */}
              <div style={{ fontSize: '0.8125rem', fontWeight: 600, color: colors.textSecondary, marginBottom: '0.75rem' }}>
                By Agent {selectedDepartment ? `(within ${selectedDepartment})` : '(all departments)'}
              </div>
              {rollup.byAgent.length === 0 ? (
                <p style={emptyStyle}>No agent data available for this selection.</p>
              ) : (
                <div style={{ overflowX: 'auto' }}>
                  <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                    <thead>
                      <tr>
                        <th style={thStyle}>Agent</th>
                        <th style={{ ...thStyle, textAlign: 'right' }}>Cost</th>
                        <th style={{ ...thStyle, textAlign: 'right' }}>Invocations</th>
                      </tr>
                    </thead>
                    <tbody>
                      {rollup.byAgent.map((row) => (
                        <tr key={row.label} data-testid={`rollup-agent-row-${row.label}`}>
                          <td style={tdStyle}>{row.label}</td>
                          <td style={{ ...tdStyle, textAlign: 'right' }}>{formatCost(row.cost)}</td>
                          <td style={{ ...tdStyle, textAlign: 'right' }}>{row.invocationCount.toLocaleString()}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              <div style={noteStyle}>
                Need individual invocations? Use Prompt History or the Activity export filtered to a
                department/agent.
              </div>
            </>
          )}
        </div>
      )}

      {/* ------------------------------------------------------------- */}
      {/* Spend by Application: dedicated breakdown by the Application tag */}
      {/* (resolved from tagged inference profiles) — a separate dimension */}
      {/* from the Department/Agent rollup above. */}
      {/* ------------------------------------------------------------- */}
      {segment === 'overview' && (
        <div style={sectionStyle(colors.accentTeal)} data-testid="segment-panel-spend-by-application">
          <div style={sectionHeader}>
            <span style={{ fontSize: '1.125rem' }}>🏷️</span>
            <span style={sectionTitle}>Spend by Application</span>
            <InfoHint text="Grouped by the Application cost-allocation tag resolved from tagged Bedrock inference profiles. Usage with no tagged profile shows as 'unknown'." />
            {spendByApplication && (
              <BasisPill
                label={spendByApplication.basis === 'reconciled' ? 'Reconciled' : 'Estimated'}
                accent={spendByApplication.basis === 'reconciled' ? colors.accentTeal : colors.accentBlue}
              />
            )}
          </div>

          {!spendByApplication ? (
            <p style={emptyStyle}>Loading spend by application…</p>
          ) : spendByApplication.rows.length === 0 ? (
            <p style={emptyStyle}>No application spend data available for the selected range.</p>
          ) : (
            <>
              <div style={{ ...glass.card, padding: '1.25rem', marginBottom: '1.25rem' }}>
                <ResponsiveContainer width="100%" height={Math.max(220, spendByApplication.rows.length * 36)}>
                  <BarChart
                    data={spendByApplication.rows.map((r) => ({ label: r.label, cost: r.cost }))}
                    layout="vertical"
                    margin={{ top: 5, right: 20, left: 10, bottom: 5 }}
                  >
                    <CartesianGrid strokeDasharray="3 3" stroke={colors.tableBorder} />
                    <XAxis type="number" fontSize={11} tick={{ fill: colors.textMuted }} axisLine={false} tickLine={false} tickFormatter={(v: number) => formatCost(v, 0)} />
                    <YAxis type="category" dataKey="label" width={140} fontSize={11} tick={{ fill: colors.textMuted }} axisLine={false} tickLine={false} />
                    <Tooltip contentStyle={chartTooltipStyle} itemStyle={{ color: colors.textPrimary }} formatter={(v: number) => [formatCost(v, 2), 'Cost']} />
                    <Bar dataKey="cost" name="Cost" fill={colors.accentTeal} radius={[0, 4, 4, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>

              <div style={{ overflowX: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                  <thead>
                    <tr>
                      <th style={thStyle}>Application</th>
                      <th style={{ ...thStyle, textAlign: 'right' }}>Cost</th>
                      <th style={{ ...thStyle, textAlign: 'right' }}>Invocations</th>
                    </tr>
                  </thead>
                  <tbody>
                    {spendByApplication.rows.map((row) => (
                      <tr key={row.label} data-testid={`spend-by-application-row-${row.label}`}>
                        <td style={{ ...tdStyle, color: row.label === 'unknown' ? colors.textMuted : colors.textPrimary }}>
                          {row.label}
                        </td>
                        <td style={{ ...tdStyle, textAlign: 'right' }}>{formatCost(row.cost, 2)}</td>
                        <td style={{ ...tdStyle, textAlign: 'right' }}>{row.invocationCount.toLocaleString()}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <div style={noteStyle}>
                &quot;unknown&quot; covers usage that did not go through a tagged application inference profile — a
                base foundation model call, or a profile without an &quot;Application&quot; cost-allocation tag.
              </div>
            </>
          )}
        </div>
      )}

      {/* ------------------------------------------------------------- */}
      {/* Reconciliation Detail: searchable, sortable, paginated slice table */}
      {/* ------------------------------------------------------------- */}
      {segment === 'reconciliation' && (
        <div style={sectionStyle(colors.accentOrange)} data-testid="segment-panel-reconciliation">
          <div style={sectionHeader}>
            <span style={{ fontSize: '1.125rem' }}>⚖</span>
            <span style={sectionTitle}>Reconciliation by Slice</span>
            <BasisPill label={basisLabel} accent={basisAccent} />
            {isAdmin && (
              <button
                onClick={handleIngestNow}
                disabled={ingesting}
                data-testid="cur-ingest-now-btn"
                style={{
                  marginLeft: 'auto',
                  padding: '0.3rem 0.75rem',
                  border: `1px solid ${ingesting ? 'rgba(227,179,65,0.4)' : `${colors.accentTeal}44`}`,
                  borderRadius: 6,
                  background: ingesting ? 'rgba(227,179,65,0.08)' : `${colors.accentTeal}10`,
                  color: ingesting ? '#e3b341' : colors.accentTeal,
                  cursor: ingesting ? 'wait' : 'pointer',
                  fontSize: '0.6875rem',
                  fontWeight: 600,
                }}
              >
                {ingesting ? '⟳ Ingesting…' : '↻ Ingest Now'}
              </button>
            )}
          </div>
          {ingestMessage && (
            <div
              data-testid="cur-ingest-success"
              style={{
                fontSize: '0.75rem',
                color: colors.accentTeal,
                marginBottom: '0.75rem',
                padding: '0.5rem 0.75rem',
                background: `${colors.accentTeal}12`,
                border: `1px solid ${colors.accentTeal}33`,
                borderRadius: 6,
              }}
            >
              {ingestMessage}
            </div>
          )}
          {ingestError && (
            <div
              data-testid="cur-ingest-error"
              style={{
                fontSize: '0.75rem',
                color: colors.accentOrange,
                marginBottom: '0.75rem',
                padding: '0.5rem 0.75rem',
                background: `${colors.accentOrange}12`,
                border: `1px solid ${colors.accentOrange}33`,
                borderRadius: 6,
              }}
            >
              {ingestError}
            </div>
          )}

          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.75rem', marginBottom: '1rem' }}>
            <SummaryCard
              label="Billed Cost"
              value={billedTotal !== null ? formatCost(billedTotal, 2) : 'N/A'}
              accent={colors.accentTeal}
              testId="governance-billed-cost"
            />
            <SummaryCard
              label="Estimated Cost"
              value={formatCost(estimatedTotal, 2)}
              accent={colors.accentBlue}
              testId="governance-estimated-cost"
            />
            <SummaryCard
              label="Variance (Billed − Estimated)"
              value={varianceTotal !== null ? formatCost(varianceTotal, 2) : 'N/A'}
              accent={colors.accentPurple}
              testId="governance-variance"
            />
          </div>

          <input
            type="text"
            value={reconSearch}
            onChange={(e) => {
              setReconSearch(e.target.value);
              setReconPage(1);
            }}
            placeholder="Search by team or model…"
            data-testid="recon-search-input"
            style={{
              width: '100%',
              maxWidth: 320,
              padding: '0.5rem 0.75rem',
              marginBottom: '0.875rem',
              borderRadius: 6,
              border: `1px solid ${colors.inputBorder}`,
              background: colors.inputBg,
              color: colors.textPrimary,
              fontSize: '0.8125rem',
            }}
          />

          {reconciliation.length === 0 ? (
            <p style={emptyStyle}>No reconciliation data available for the selected range.</p>
          ) : sortedRecon.length === 0 ? (
            <p style={emptyStyle}>No slices match &quot;{reconSearch}&quot;.</p>
          ) : (
            <>
              <div style={{ overflowX: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                  <thead>
                    <tr>
                      <th style={{ ...thStyle, cursor: 'pointer' }} onClick={() => handleReconSort('teamOrPrincipal')}>
                        Team / Principal{reconSortIndicator('teamOrPrincipal')}
                      </th>
                      <th style={{ ...thStyle, cursor: 'pointer' }} onClick={() => handleReconSort('model')}>
                        Model{reconSortIndicator('model')}
                      </th>
                      <th style={{ ...thStyle, cursor: 'pointer' }} onClick={() => handleReconSort('day')}>
                        Day{reconSortIndicator('day')}
                      </th>
                      <th style={{ ...thStyle, textAlign: 'right', cursor: 'pointer' }} onClick={() => handleReconSort('billed')}>
                        Billed{reconSortIndicator('billed')}
                      </th>
                      <th style={{ ...thStyle, textAlign: 'right', cursor: 'pointer' }} onClick={() => handleReconSort('estimated')}>
                        Estimated{reconSortIndicator('estimated')}
                      </th>
                      <th style={{ ...thStyle, textAlign: 'right', cursor: 'pointer' }} onClick={() => handleReconSort('factor')}>
                        Factor{reconSortIndicator('factor')}
                        <InfoHint text="How much the estimate was scaled to match the billed total for this slice. 1.000 means no adjustment was needed." />
                      </th>
                      <th style={{ ...thStyle, textAlign: 'right', cursor: 'pointer' }} onClick={() => handleReconSort('variance')}>
                        Variance{reconSortIndicator('variance')}
                      </th>
                      <th style={thStyle}>Flag</th>
                      <th style={thStyle}>
                        Basis
                        <InfoHint text="'reconciled' means a billed figure was matched to this slice; 'estimated_only' means no billed data was available so only the token-based estimate is shown." />
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {reconPageRows.map((s, i) => (
                      <tr
                        key={`${s.teamOrPrincipal}|${s.model}|${s.day}|${i}`}
                        onMouseEnter={(e) => { (e.currentTarget as HTMLTableRowElement).style.background = colors.tableRowHover; }}
                        onMouseLeave={(e) => { (e.currentTarget as HTMLTableRowElement).style.background = 'transparent'; }}
                      >
                        <td style={tdStyle}>{s.teamOrPrincipal || '—'}</td>
                        <td style={tdStyle}>{s.model || '—'}</td>
                        <td style={tdStyle}>{s.day}</td>
                        <td style={{ ...tdStyle, textAlign: 'right' }}>{s.billed !== null && s.billed !== undefined ? formatCost(s.billed) : 'N/A'}</td>
                        <td style={{ ...tdStyle, textAlign: 'right' }}>{formatCost(s.estimated)}</td>
                        <td style={{ ...tdStyle, textAlign: 'right' }}>{formatFactor(s.factor)}</td>
                        <td style={{ ...tdStyle, textAlign: 'right' }}>{s.variance !== null && s.variance !== undefined ? formatCost(s.variance) : 'N/A'}</td>
                        <td style={{ ...tdStyle, color: colors.accentYellow }}>{s.flag ?? '—'}</td>
                        <td style={tdStyle}>{s.basis}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '1rem', fontSize: '0.8125rem', marginTop: '1rem' }} data-testid="recon-pagination-controls">
                <button
                  onClick={() => setReconPage((p) => Math.max(1, p - 1))}
                  disabled={reconPageClamped <= 1}
                  data-testid="recon-page-prev"
                  style={{
                    padding: '0.35rem 0.75rem',
                    border: `1px solid ${reconPageClamped <= 1 ? colors.tableBorder : colors.btnSecondaryBorder}`,
                    borderRadius: 5,
                    background: reconPageClamped <= 1 ? 'rgba(255,255,255,0.03)' : colors.btnSecondary,
                    color: reconPageClamped <= 1 ? colors.textMuted : colors.accentBlue,
                    cursor: reconPageClamped <= 1 ? 'default' : 'pointer',
                    fontSize: '0.75rem',
                  }}
                >
                  ← Prev
                </button>
                <span style={{ color: colors.textSecondary }} data-testid="recon-page-indicator">
                  Page {reconPageClamped} of {reconTotalPages} ({sortedRecon.length} slices)
                </span>
                <button
                  onClick={() => setReconPage((p) => Math.min(reconTotalPages, p + 1))}
                  disabled={reconPageClamped >= reconTotalPages}
                  data-testid="recon-page-next"
                  style={{
                    padding: '0.35rem 0.75rem',
                    border: `1px solid ${reconPageClamped >= reconTotalPages ? colors.tableBorder : colors.btnSecondaryBorder}`,
                    borderRadius: 5,
                    background: reconPageClamped >= reconTotalPages ? 'rgba(255,255,255,0.03)' : colors.btnSecondary,
                    color: reconPageClamped >= reconTotalPages ? colors.textMuted : colors.accentBlue,
                    cursor: reconPageClamped >= reconTotalPages ? 'default' : 'pointer',
                    fontSize: '0.75rem',
                  }}
                >
                  Next →
                </button>
              </div>
            </>
          )}
          <div style={noteStyle}>
            Note: per-prompt cost is always an estimate. The reconciliation factor scales the token-based estimate
            so a slice sums to the authoritative billed total; it does not make any single prompt&apos;s cost exact.
          </div>
        </div>
      )}

      {/* ------------------------------------------------------------- */}
      {/* Efficiency: cache-hit ratio + cost-per-outcome (diagnostic / optimization info) */}
      {/* ------------------------------------------------------------- */}
      {segment === 'efficiency' && (
        <div style={sectionStyle(colors.accentBlue)} data-testid="segment-panel-efficiency">
          <div style={sectionHeader}>
            <span style={{ fontSize: '1.125rem' }}>⚡</span>
            <span style={sectionTitle}>Efficiency</span>
          </div>

          {/* Cache-hit by model & application */}
          <div style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap' }}>
            <div style={{ ...glass.card, flex: '1 1 320px', padding: '1.25rem' }}>
              <div style={{ fontSize: '0.8125rem', fontWeight: 600, color: colors.textSecondary, marginBottom: '1rem' }}>
                Cache-Hit Ratio by Model
              </div>
              {cacheHit && cacheHit.byModel.length > 0 ? (
                <ResponsiveContainer width="100%" height={220}>
                  <BarChart
                    data={cacheHit.byModel.map((r) => ({ label: r.label, pct: r.cacheHitRatio === null ? 0 : r.cacheHitRatio * 100 }))}
                    margin={{ top: 5, right: 10, left: 0, bottom: 5 }}
                  >
                    <CartesianGrid strokeDasharray="3 3" stroke={colors.tableBorder} />
                    <XAxis dataKey="label" fontSize={11} tick={{ fill: colors.textMuted }} axisLine={false} tickLine={false} />
                    <YAxis fontSize={11} tick={{ fill: colors.textMuted }} axisLine={false} tickLine={false} domain={[0, 100]} tickFormatter={(v: number) => `${v}%`} />
                    <Tooltip contentStyle={chartTooltipStyle} itemStyle={{ color: colors.textPrimary }} formatter={(v: number) => [`${v.toFixed(1)}%`, 'Cache-hit']} />
                    <Bar dataKey="pct" name="Cache-hit" fill={colors.accentBlue} radius={[4, 4, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              ) : (
                <p style={emptyStyle}>No cache-hit data by model.</p>
              )}
            </div>

            <div style={{ ...glass.card, flex: '1 1 320px', padding: '1.25rem' }}>
              <div style={{ fontSize: '0.8125rem', fontWeight: 600, color: colors.textSecondary, marginBottom: '1rem' }}>
                Cache-Hit Ratio by Application
              </div>
              {cacheHit && cacheHit.byApplication.length > 0 ? (
                <ResponsiveContainer width="100%" height={220}>
                  <BarChart
                    data={cacheHit.byApplication.map((r) => ({ label: r.label, pct: r.cacheHitRatio === null ? 0 : r.cacheHitRatio * 100 }))}
                    margin={{ top: 5, right: 10, left: 0, bottom: 5 }}
                  >
                    <CartesianGrid strokeDasharray="3 3" stroke={colors.tableBorder} />
                    <XAxis dataKey="label" fontSize={11} tick={{ fill: colors.textMuted }} axisLine={false} tickLine={false} />
                    <YAxis fontSize={11} tick={{ fill: colors.textMuted }} axisLine={false} tickLine={false} domain={[0, 100]} tickFormatter={(v: number) => `${v}%`} />
                    <Tooltip contentStyle={chartTooltipStyle} itemStyle={{ color: colors.textPrimary }} formatter={(v: number) => [`${v.toFixed(1)}%`, 'Cache-hit']} />
                    <Bar dataKey="pct" name="Cache-hit" fill={colors.accentPurple} radius={[4, 4, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              ) : (
                <p style={emptyStyle}>No cache-hit data by application.</p>
              )}
            </div>
          </div>

          {/* Cache-hit ratios table (shows "N/A" for zero-denominator groups) */}
          {cacheHit && cacheHit.byModel.length > 0 && (
            <div style={{ overflowX: 'auto', marginTop: '1rem' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead>
                  <tr>
                    <th style={thStyle}>Model</th>
                    <th style={{ ...thStyle, textAlign: 'right' }}>Input Tokens</th>
                    <th style={{ ...thStyle, textAlign: 'right' }}>Cache-Read Tokens</th>
                    <th style={{ ...thStyle, textAlign: 'right' }}>Cache-Hit Ratio</th>
                  </tr>
                </thead>
                <tbody>
                  {cacheHit.byModel.map((r) => (
                    <tr key={r.label}>
                      <td style={tdStyle}>{r.label}</td>
                      <td style={{ ...tdStyle, textAlign: 'right' }}>{r.inputTokens.toLocaleString()}</td>
                      <td style={{ ...tdStyle, textAlign: 'right' }}>{r.cacheReadTokens.toLocaleString()}</td>
                      <td style={{ ...tdStyle, textAlign: 'right', color: r.cacheHitRatio === null ? colors.textMuted : colors.accentBlue }}>
                        {formatRatio(r.cacheHitRatio)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* Daily cache-hit trend */}
          <div style={{ ...glass.card, padding: '1.25rem', marginTop: '1rem' }}>
            <div style={{ fontSize: '0.8125rem', fontWeight: 600, color: colors.textSecondary, marginBottom: '1rem' }}>
              Daily Cache-Hit Trend
            </div>
            {cacheHit && cacheHit.trend.length > 0 ? (
              <ResponsiveContainer width="100%" height={220}>
                <LineChart
                  data={cacheHit.trend.map((p) => ({ date: p.date, pct: p.cacheHitRatio === null ? null : p.cacheHitRatio * 100 }))}
                  margin={{ top: 5, right: 10, left: 0, bottom: 5 }}
                >
                  <CartesianGrid strokeDasharray="3 3" stroke={colors.tableBorder} />
                  <XAxis dataKey="date" fontSize={11} tick={{ fill: colors.textMuted }} axisLine={false} tickLine={false} />
                  <YAxis fontSize={11} tick={{ fill: colors.textMuted }} axisLine={false} tickLine={false} domain={[0, 100]} tickFormatter={(v: number) => `${v}%`} />
                  <Tooltip contentStyle={chartTooltipStyle} itemStyle={{ color: colors.textPrimary }} formatter={(v: number) => [`${v.toFixed(1)}%`, 'Cache-hit']} />
                  <Line type="monotone" dataKey="pct" name="Cache-hit" stroke={colors.accentTeal} strokeWidth={2} dot={{ r: 3, fill: colors.accentTeal }} connectNulls />
                </LineChart>
              </ResponsiveContainer>
            ) : (
              <p style={emptyStyle}>No cache-hit trend data available.</p>
            )}
          </div>

          {/* Cost-per-outcome */}
          <div style={{ marginTop: '1.25rem' }}>
            <div style={{ fontSize: '0.8125rem', fontWeight: 600, color: colors.textSecondary, marginBottom: '0.75rem' }}>
              Cost per Outcome by Workload
            </div>
            {costPerOutcome.length === 0 ? (
              <p style={emptyStyle}>No cost-per-outcome data available.</p>
            ) : (
              <div style={{ overflowX: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                  <thead>
                    <tr>
                      <th style={thStyle}>Workload</th>
                      <th style={{ ...thStyle, textAlign: 'right' }}>Outcome Count</th>
                      <th style={{ ...thStyle, textAlign: 'right' }}>Cost / Outcome</th>
                      <th style={thStyle}>Basis</th>
                    </tr>
                  </thead>
                  <tbody>
                    {costPerOutcome.map((r) => (
                      <tr key={r.workload}>
                        <td style={tdStyle}>{r.workload}</td>
                        <td style={{ ...tdStyle, textAlign: 'right' }}>
                          {r.outcomeCount === null || r.outcomeCount === undefined ? 'unknown' : r.outcomeCount.toLocaleString()}
                        </td>
                        <td style={{ ...tdStyle, textAlign: 'right' }}>
                          {r.costPerOutcome === null || r.costPerOutcome === undefined ? (
                            <span style={{ color: colors.textMuted }}>not available</span>
                          ) : (
                            <>
                              {formatCost(r.costPerOutcome)}{' '}
                              {r.estimated && (
                                <span style={{ fontSize: '0.6875rem', color: colors.textMuted }}>(estimate)</span>
                              )}
                            </>
                          )}
                        </td>
                        <td style={tdStyle}>{r.basis}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <div style={noteStyle}>
              Cost per outcome is an estimate. It uses the reconciled cost when billed data is available, otherwise
              the token-based estimate, and is &quot;not available&quot; when the outcome count is zero or unknown.
            </div>
          </div>
        </div>
      )}

      {/* ------------------------------------------------------------- */}
      {/* Alerts: unattributed-spend canary + billing staleness, one combined status */}
      {/* ------------------------------------------------------------- */}
      {segment === 'alerts' && (
        <div style={sectionStyle(colors.accentYellow)} data-testid="segment-panel-alerts">
          <div style={sectionHeader}>
            <span style={{ fontSize: '1.125rem' }}>🐤</span>
            <span style={sectionTitle}>Attribution &amp; Billing Health</span>
            {canary && (
              <BasisPill
                label={canary.basis === 'billed' ? 'Billed basis' : 'Estimated basis'}
                accent={canary.basis === 'billed' ? colors.accentTeal : colors.accentBlue}
              />
            )}
          </div>

          {curStatus?.billedStale && (
            <div
              data-testid="staleness-alert"
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '0.5rem',
                fontSize: '0.8125rem',
                fontWeight: 600,
                padding: '0.6rem 0.875rem',
                borderRadius: 8,
                marginBottom: '1rem',
                background: `${colors.accentYellow}18`,
                color: colors.accentYellow,
                border: `1px solid ${colors.accentYellow}55`,
              }}
            >
              <span>⚠</span>
              <span>
                Billed cost may be stale
                {curStatus.coveredThrough ? ` — covered through ${curStatus.coveredThrough}` : ''}.
              </span>
            </div>
          )}

          {!canary ? (
            <p style={emptyStyle}>No attribution data available.</p>
          ) : (
            <>
              <div
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: '0.4rem',
                  fontSize: '0.8125rem',
                  fontWeight: 600,
                  padding: '0.35rem 0.75rem',
                  borderRadius: 8,
                  marginBottom: '1rem',
                  background: canary.meetsTarget ? `${colors.accentTeal}18` : `${colors.accentOrange}18`,
                  color: canary.meetsTarget ? colors.accentTeal : colors.accentOrange,
                  border: `1px solid ${canary.meetsTarget ? colors.accentTeal : colors.accentOrange}55`,
                }}
                data-testid="canary-meets-indicator"
              >
                <span>{canary.meetsTarget ? '✓' : '✗'}</span>
                <span>
                  {canary.meetsTarget
                    ? 'Meets target — unattributed share is within the configured limit.'
                    : 'Does not meet target — unattributed share exceeds the configured limit.'}
                </span>
              </div>

              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.75rem', marginBottom: '1rem' }}>
                <SummaryCard
                  label="Unattributed Spend Share"
                  value={formatRatio(canary.unattributedSpendShare)}
                  accent={canary.meetsTarget ? colors.accentTeal : colors.accentOrange}
                  testId="canary-spend-share"
                />
                <SummaryCard
                  label="Target"
                  value={formatRatio(canary.target)}
                  accent={colors.accentBlue}
                  testId="canary-target"
                />
                <SummaryCard
                  label="Base-Model / Untagged Share"
                  value={formatRatio(canary.baseModelShare)}
                  accent={colors.accentPurple}
                  testId="canary-base-model-share"
                />
              </div>

              {canary.trend.length > 0 ? (
                <div style={{ ...glass.card, padding: '1.25rem' }}>
                  <div style={{ fontSize: '0.8125rem', fontWeight: 600, color: colors.textSecondary, marginBottom: '1rem' }}>
                    Unattributed Spend Share — Daily Trend
                  </div>
                  <ResponsiveContainer width="100%" height={220}>
                    <LineChart
                      data={canary.trend.map((p) => ({
                        date: p.date,
                        spend: p.unattributedSpendShare * 100,
                        target: canary.target * 100,
                      }))}
                      margin={{ top: 5, right: 10, left: 0, bottom: 5 }}
                    >
                      <CartesianGrid strokeDasharray="3 3" stroke={colors.tableBorder} />
                      <XAxis dataKey="date" fontSize={11} tick={{ fill: colors.textMuted }} axisLine={false} tickLine={false} />
                      <YAxis fontSize={11} tick={{ fill: colors.textMuted }} axisLine={false} tickLine={false} domain={[0, 100]} tickFormatter={(v: number) => `${v}%`} />
                      <Tooltip contentStyle={chartTooltipStyle} itemStyle={{ color: colors.textPrimary }} formatter={(v: number) => `${v.toFixed(1)}%`} />
                      <Line type="monotone" dataKey="spend" name="Unattributed share" stroke={colors.accentYellow} strokeWidth={2} dot={{ r: 3, fill: colors.accentYellow }} />
                      <Line type="monotone" dataKey="target" name="Target" stroke={colors.accentBlue} strokeWidth={1.5} strokeDasharray="4 4" dot={false} />
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              ) : (
                <p style={emptyStyle}>No canary trend data available.</p>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
};

export default CostGovernanceTab;
