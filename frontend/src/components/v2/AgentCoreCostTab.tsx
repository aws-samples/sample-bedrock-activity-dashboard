import React, { useEffect, useState } from 'react';
import {
  BarChart,
  Bar,
  Cell,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from 'recharts';
import type { DashboardFilters } from '../../types';
import type { AgentRuntimeCostRow } from '../../types/agentcoreCost';
import { getAgentCoreCost, triggerAgentCoreScan } from '../../api/agentcoreCost';
import { formatCost } from '../../services/formatCost';
import LoadingSpinner from '../LoadingSpinner';
import ErrorBanner from '../ErrorBanner';
import { colors, glass } from '../../theme';

interface AgentCoreCostTabProps {
  filters: DashboardFilters;
  /** The "Scan for New Agents" trigger is shown only to admins, same convention as the CUR "Ingest Now" button. */
  role?: string;
}

// ---------------------------------------------------------------------------
// Shared styles (match CostGovernanceTab card styling — kept local rather
// than imported so this tab has zero coupling to the Bedrock token-cost
// components; the two views are deliberately independent.)
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

/** Cost values here are routinely sub-cent (AgentCore is billed per-second), so
 * always show full precision rather than the 2-decimal default. */
function formatMicroCost(value: number): string {
  return formatCost(value, value > 0 && value < 0.01 ? 6 : 4);
}

function formatTimestamp(iso: string): string {
  return new Date(iso).toLocaleString();
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

const AgentCoreCostTab: React.FC<AgentCoreCostTabProps> = ({ filters, role }) => {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [totalCost, setTotalCost] = useState(0);
  const [byAgent, setByAgent] = useState<AgentRuntimeCostRow[]>([]);
  const [configured, setConfigured] = useState(true);
  const [vcpuRate, setVcpuRate] = useState(0);
  const [memoryRate, setMemoryRate] = useState(0);
  const [scanRegions, setScanRegions] = useState<string[]>([]);
  const [lastScanAt, setLastScanAt] = useState<string | null>(null);
  const [selectedAgent, setSelectedAgent] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [scanning, setScanning] = useState(false);
  const [scanMessage, setScanMessage] = useState<string | null>(null);
  const [scanError, setScanError] = useState<string | null>(null);

  const isAdmin = role === 'admin';

  const handleScanNow = async (): Promise<void> => {
    setScanning(true);
    setScanError(null);
    setScanMessage(null);
    try {
      const { runId } = await triggerAgentCoreScan();
      setScanMessage(
        `Scan started (run ${runId.slice(0, 8)}). It runs in the background across every configured ` +
          'region and can take up to a minute — refresh this tab afterward to see any newly discovered ' +
          'Runtimes.'
      );
    } catch (err: unknown) {
      setScanError(err instanceof Error ? err.message : 'Failed to trigger AgentCore scan');
    } finally {
      setScanning(false);
    }
  };

  useEffect(() => {
    // A drill-down selection from a previous window shouldn't carry over
    // when the shared date range changes.
    setSelectedAgent(null);
  }, [filters]);

  useEffect(() => {
    const controller = new AbortController();
    const { signal } = controller;

    async function load(): Promise<void> {
      setLoading(true);
      setError(null);
      try {
        const data = await getAgentCoreCost(filters, signal);
        if (signal.aborted) return;
        setTotalCost(data.totalCost);
        setByAgent(data.byAgent);
        setConfigured(data.configured);
        setVcpuRate(data.vcpuHourlyRate);
        setMemoryRate(data.memoryGbHourlyRate);
        setScanRegions(data.scanRegions);
        setLastScanAt(data.lastScanAt);
      } catch (err: unknown) {
        if (signal.aborted) return;
        setError(err instanceof Error ? err.message : 'Failed to load AgentCore cost data');
      } finally {
        if (!signal.aborted) setLoading(false);
      }
    }

    void load();
    return () => controller.abort();
  }, [filters, reloadKey]);

  if (loading) return <LoadingSpinner message="Loading AgentCore Runtime cost..." />;
  if (error) return <ErrorBanner message={error} onRetry={() => setReloadKey((k) => k + 1)} />;

  const selectedRow = selectedAgent ? byAgent.find((a) => a.agentName === selectedAgent) ?? null : null;

  return (
    <div data-testid="tab-agentcore-cost">
      {/* Info banner */}
      <div style={{ marginBottom: '1.25rem', padding: '1rem 1.25rem', background: 'rgba(188,140,255,0.05)', border: '1px solid rgba(188,140,255,0.14)', borderRadius: 10 }}>
        <div style={{ fontSize: '0.8125rem', color: colors.textSecondary, lineHeight: 1.6 }}>
          This is the compute cost of running AgentCore Runtime agents (per-second vCPU + memory billing) — a
          separate line item from the Bedrock model-token cost the agents&apos; own LLM calls consume, which is
          tracked under Cost Analysis / Cost Governance. The list below includes every Runtime this dashboard
          knows about — its own agents plus anything else found by a periodic account-wide scan — with no
          separate category to check. Cost figures are computed live from CloudWatch, so nothing here is
          persisted.
        </div>
        <div style={{ fontSize: '0.75rem', color: colors.textMuted, lineHeight: 1.6, marginTop: '0.625rem' }}>
          <strong style={{ color: colors.textSecondary }}>Why some agents show $0:</strong> most of this
          dashboard&apos;s own agents are deterministic (⚙) and run their checks directly in the backend&apos;s
          ECS task rather than invoking AgentCore Runtime — that&apos;s a deliberate cost optimization, not a
          bug. Runtime cost only appears here for agents that actually invoke AgentCore Runtime.
        </div>
        <div style={{ fontSize: '0.75rem', color: colors.textMuted, lineHeight: 1.6, marginTop: '0.5rem' }}>
          Scanned regions: {scanRegions.length > 0 ? scanRegions.join(', ') : 'none configured'}
          {lastScanAt && <> · Last scan: {formatTimestamp(lastScanAt)}</>}. Runs automatically once a day; use
          &quot;Scan for New Agents&quot; below to check sooner.
        </div>
      </div>

      {!configured ? (
        <div style={sectionStyle(colors.accentYellow)}>
          <div style={sectionHeader}>
            <span style={{ fontSize: '1.125rem' }}>⏸</span>
            <span style={sectionTitle}>AgentCore Cost Tracking Disabled</span>
          </div>
          <p style={emptyStyle}>
            AgentCore Runtime cost tracking is currently disabled (AGENTCORE_COST_TRACKING_ENABLED=false).
            Enable it in the backend configuration to see live compute-cost figures here.
          </p>
        </div>
      ) : (
        <>
          {/* Total + rate summary */}
          <div style={sectionStyle(colors.accentPurple)}>
            <div style={sectionHeader}>
              <span style={{ fontSize: '1.125rem' }}>⚙</span>
              <span style={sectionTitle}>AgentCore Runtime Cost</span>
              {selectedAgent && (
                <span
                  data-testid="agentcore-drill-badge"
                  style={{
                    padding: '0.2rem 0.625rem',
                    borderRadius: 999,
                    background: `${colors.accentPurple}22`,
                    color: colors.accentPurple,
                    fontSize: '0.6875rem',
                    fontWeight: 700,
                  }}
                >
                  Viewing: {selectedRow?.displayName ?? selectedAgent}
                </span>
              )}
              {selectedAgent && (
                <button
                  onClick={() => setSelectedAgent(null)}
                  data-testid="agentcore-clear-drill-btn"
                  style={{
                    padding: '0.3rem 0.75rem',
                    border: `1px solid ${colors.accentPurple}44`,
                    borderRadius: 6,
                    background: `${colors.accentPurple}10`,
                    color: colors.accentPurple,
                    cursor: 'pointer',
                    fontSize: '0.6875rem',
                    fontWeight: 600,
                  }}
                >
                  ← All Agents
                </button>
              )}
              {isAdmin && (
                <button
                  onClick={handleScanNow}
                  disabled={scanning}
                  data-testid="agentcore-scan-now-btn"
                  style={{
                    marginLeft: 'auto',
                    padding: '0.3rem 0.75rem',
                    border: `1px solid ${scanning ? 'rgba(227,179,65,0.4)' : `${colors.accentYellow}44`}`,
                    borderRadius: 6,
                    background: scanning ? 'rgba(227,179,65,0.08)' : `${colors.accentYellow}10`,
                    color: scanning ? '#e3b341' : colors.accentYellow,
                    cursor: scanning ? 'wait' : 'pointer',
                    fontSize: '0.6875rem',
                    fontWeight: 600,
                  }}
                >
                  {scanning ? '⟳ Scanning…' : '↻ Scan for New Agents'}
                </button>
              )}
            </div>
            {scanMessage && (
              <div
                data-testid="agentcore-scan-success"
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
                {scanMessage}
              </div>
            )}
            {scanError && (
              <div
                data-testid="agentcore-scan-error"
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
                {scanError}
              </div>
            )}
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.75rem' }}>
              <SummaryCard
                label={selectedAgent ? `Cost — ${selectedRow?.displayName ?? selectedAgent}` : 'Total Cost (All Agents)'}
                value={formatMicroCost(selectedRow ? selectedRow.totalCost : totalCost)}
                accent={colors.accentPurple}
                testId="agentcore-total-cost"
              />
              <SummaryCard
                label="vCPU-Hours"
                value={(selectedRow ? selectedRow.vcpuHours : byAgent.reduce((s, a) => s + a.vcpuHours, 0)).toFixed(4)}
                accent={colors.accentBlue}
                testId="agentcore-vcpu-hours"
              />
              <SummaryCard
                label="GB-Hours (Memory)"
                value={(selectedRow ? selectedRow.gbHours : byAgent.reduce((s, a) => s + a.gbHours, 0)).toFixed(4)}
                accent={colors.accentTeal}
                testId="agentcore-gb-hours"
              />
            </div>
            <div style={noteStyle}>
              Rates: {formatCost(vcpuRate, 4)}/vCPU-hour, {formatCost(memoryRate, 5)}/GB-hour (published AgentCore
              Runtime on-demand pricing). AgentCore Runtime only bills for active processing time — LLM/tool I/O
              wait is free — so these figures are typically well under $1/month even at moderate agent volume.
            </div>
          </div>

          {/* Per-agent chart */}
          <div style={sectionStyle(colors.accentBlue)}>
            <div style={sectionHeader}>
              <span style={{ fontSize: '1.125rem' }}>📊</span>
              <span style={sectionTitle}>Cost by Agent</span>
            </div>
            {byAgent.length === 0 ? (
              <p style={emptyStyle}>No AgentCore Runtime usage recorded for the selected range.</p>
            ) : (
              <div style={{ ...glass.card, padding: '1.25rem' }}>
                <ResponsiveContainer width="100%" height={240}>
                  <BarChart
                    data={byAgent.map((a) => ({ label: a.displayName, cost: a.totalCost, agentName: a.agentName }))}
                    margin={{ top: 5, right: 10, left: 0, bottom: 40 }}
                  >
                    <CartesianGrid strokeDasharray="3 3" stroke={colors.tableBorder} />
                    <XAxis
                      dataKey="label"
                      fontSize={10}
                      tick={{ fill: colors.textMuted }}
                      axisLine={false}
                      tickLine={false}
                      angle={-30}
                      textAnchor="end"
                      height={60}
                    />
                    <YAxis fontSize={11} tick={{ fill: colors.textMuted }} axisLine={false} tickLine={false} />
                    <Tooltip
                      contentStyle={chartTooltipStyle}
                      itemStyle={{ color: colors.textPrimary }}
                      formatter={(v: number) => [formatMicroCost(v), 'Cost']}
                    />
                    <Bar
                      dataKey="cost"
                      name="Cost"
                      radius={[4, 4, 0, 0]}
                      onClick={(data: { agentName?: string }) => {
                        if (data?.agentName) setSelectedAgent(data.agentName);
                      }}
                      style={{ cursor: 'pointer' }}
                    >
                      {byAgent.map((a) => (
                        <Cell
                          key={a.agentName}
                          fill={!selectedAgent || selectedAgent === a.agentName ? colors.accentPurple : colors.tableBorder}
                        />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            )}
          </div>

          {/* Per-agent drill-down table */}
          <div style={sectionStyle(colors.accentTeal)}>
            <div style={sectionHeader}>
              <span style={{ fontSize: '1.125rem' }}>⚡</span>
              <span style={sectionTitle}>Per-Agent Drill-Down {!selectedAgent && '(click a row to focus)'}</span>
            </div>
            {byAgent.length === 0 ? (
              <p style={emptyStyle}>No agents with a deployed AgentCore Runtime found.</p>
            ) : (
              <div style={{ overflowX: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                  <thead>
                    <tr>
                      <th style={thStyle}>Agent</th>
                      <th style={thStyle}>Region</th>
                      <th style={{ ...thStyle, textAlign: 'right' }}>vCPU-Hours</th>
                      <th style={{ ...thStyle, textAlign: 'right' }}>GB-Hours</th>
                      <th style={{ ...thStyle, textAlign: 'right' }}>CPU Cost</th>
                      <th style={{ ...thStyle, textAlign: 'right' }}>Memory Cost</th>
                      <th style={{ ...thStyle, textAlign: 'right' }}>Total Cost</th>
                    </tr>
                  </thead>
                  <tbody>
                    {byAgent.map((row) => {
                      const isSelected = selectedAgent === row.agentName;
                      return (
                        <tr
                          key={row.agentName}
                          onClick={() => setSelectedAgent(isSelected ? null : row.agentName)}
                          data-testid={`agentcore-agent-row-${row.agentName}`}
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
                            {isSelected ? '▸ ' : ''}{row.displayName}
                          </td>
                          <td style={tdStyle}>{row.region}</td>
                          <td style={{ ...tdStyle, textAlign: 'right' }}>{row.vcpuHours.toFixed(4)}</td>
                          <td style={{ ...tdStyle, textAlign: 'right' }}>{row.gbHours.toFixed(4)}</td>
                          <td style={{ ...tdStyle, textAlign: 'right' }}>{formatMicroCost(row.cpuCost)}</td>
                          <td style={{ ...tdStyle, textAlign: 'right' }}>{formatMicroCost(row.memoryCost)}</td>
                          <td style={{ ...tdStyle, textAlign: 'right', fontWeight: 600 }}>{formatMicroCost(row.totalCost)}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
            <div style={noteStyle}>
              Every Runtime with a deployed AgentCore Runtime is listed, whether or not it belongs to this
              dashboard&apos;s own agents. Inline-only agents (invoked via `invoke_inline_agent`, no standalone
              Runtime deployment) have no Runtime compute cost to report and won&apos;t appear here.
            </div>
          </div>
        </>
      )}
    </div>
  );
};

export default AgentCoreCostTab;
