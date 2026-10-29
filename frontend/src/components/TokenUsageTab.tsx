import React, { useEffect, useState, useMemo } from 'react';
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
  PieChart,
  Pie,
  Cell,
  TooltipProps,
} from 'recharts';
import type { DashboardFilters, UserTokenUsage, DailyTokenUsage } from '../types';
import { getTokenUsage } from '../api/client';
import LoadingSpinner from './LoadingSpinner';
import ErrorBanner from './ErrorBanner';
import ExportButton from './ExportButton';
import { colors, glass } from '../theme';
import { formatCost } from '../services/formatCost';

interface TokenUsageTabProps {
  filters: DashboardFilters;
}

type SortKey = 'userName' | 'inputTokens' | 'outputTokens' | 'cacheReadTokens' | 'cacheWriteTokens' | 'totalTokens' | 'costEstimate';
type SortDir = 'asc' | 'desc';

const TOKEN_COLORS = {
  inputTokens:      '#58a6ff',
  outputTokens:     '#39d353',
  cacheReadTokens:  '#e3b341',
  cacheWriteTokens: '#bc8cff',
};

const PIE_COLORS = ['#58a6ff', '#39d353', '#e3b341', '#bc8cff'];

function formatNumber(n: number): string {
  return n.toLocaleString();
}

const thStyle: React.CSSProperties = {
  padding: '0.5rem 0.75rem',
  textAlign: 'left',
  fontSize: '0.75rem',
  fontWeight: 600,
  color: colors.textSecondary,
  borderBottom: `1px solid ${colors.tableBorder}`,
  background: colors.tableHeaderBg,
  cursor: 'pointer',
  userSelect: 'none',
  whiteSpace: 'nowrap',
};

const tdStyle: React.CSSProperties = {
  padding: '0.5rem 0.75rem',
  fontSize: '0.8125rem',
  borderBottom: `1px solid ${colors.tableBorder}`,
  color: colors.textPrimary,
};

// Custom dark tooltip
const DarkTooltip: React.FC<TooltipProps<number, string>> = ({ active, payload, label }) => {
  if (!active || !payload?.length) return null;
  return (
    <div style={{
      background: 'rgba(13,17,23,0.95)',
      border: `1px solid ${colors.cardBorder}`,
      borderRadius: '8px',
      padding: '0.625rem 0.875rem',
      fontSize: '0.8125rem',
      color: colors.textPrimary,
      boxShadow: '0 4px 16px rgba(0,0,0,0.5)',
    }}>
      <div style={{ fontWeight: 600, marginBottom: '0.375rem', color: colors.textSecondary }}>{label}</div>
      {payload.map((entry) => (
        <div key={entry.name} style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', marginBottom: '0.2rem' }}>
          <span style={{ width: '8px', height: '8px', borderRadius: '50%', background: entry.color, flexShrink: 0 }} />
          <span style={{ color: colors.textSecondary }}>{entry.name}:</span>
          <span style={{ fontWeight: 600 }}>{(entry.value as number).toLocaleString()}</span>
        </div>
      ))}
    </div>
  );
};

const TokenUsageTab: React.FC<TokenUsageTabProps> = ({ filters }) => {
  const [users, setUsers] = useState<UserTokenUsage[]>([]);
  const [daily, setDaily] = useState<DailyTokenUsage[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [sortKey, setSortKey] = useState<SortKey>('totalTokens');
  const [sortDir, setSortDir] = useState<SortDir>('desc');
  const [expandedUser, setExpandedUser] = useState<string | null>(null);

  const fetchData = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await getTokenUsage(filters);
      setUsers(data.users);
      setDaily(data.daily);
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Failed to load token usage data';
      setError(message);
    } finally {
      setLoading(false);
    }
  };

  // eslint-disable-next-line react-hooks/exhaustive-deps -- fetchData is recreated each render; effect must re-run only when filters change (adding it would loop)
  useEffect(() => { fetchData(); }, [filters]);

  const handleSort = (key: SortKey) => {
    if (sortKey === key) setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    else { setSortKey(key); setSortDir('desc'); }
  };

  const sortedUsers = useMemo(() => {
    return [...users].sort((a, b) => {
      const aVal = a[sortKey] ?? -1;
      const bVal = b[sortKey] ?? -1;
      if (aVal < bVal) return sortDir === 'asc' ? -1 : 1;
      if (aVal > bVal) return sortDir === 'asc' ? 1 : -1;
      return 0;
    });
  }, [users, sortKey, sortDir]);

  const sortIndicator = (key: SortKey) => sortKey === key ? (sortDir === 'asc' ? ' ▲' : ' ▼') : '';

  // Aggregate totals for pie chart
  const tokenTypeTotals = useMemo(() => {
    const totals = { inputTokens: 0, outputTokens: 0, cacheReadTokens: 0, cacheWriteTokens: 0 };
    users.forEach((u) => {
      totals.inputTokens += u.inputTokens;
      totals.outputTokens += u.outputTokens;
      totals.cacheReadTokens += u.cacheReadTokens;
      totals.cacheWriteTokens += u.cacheWriteTokens;
    });
    return [
      { name: 'Input', value: totals.inputTokens },
      { name: 'Output', value: totals.outputTokens },
      { name: 'Cache Read', value: totals.cacheReadTokens },
      { name: 'Cache Write', value: totals.cacheWriteTokens },
    ].filter((d) => d.value > 0);
  }, [users]);

  if (loading) return <LoadingSpinner message="Loading token usage..." />;
  if (error) return <ErrorBanner message={error} onRetry={fetchData} />;

  return (
    <div data-testid="tab-token-usage">
      {/* Info banner */}
      <div style={{ marginBottom: '1.25rem', padding: '1rem 1.25rem', background: 'rgba(88,166,255,0.04)', border: '1px solid rgba(88,166,255,0.12)', borderRadius: 10 }}>
        <div style={{ fontSize: '0.8125rem', color: colors.textSecondary, lineHeight: 1.6 }}>
          Detailed token consumption breakdown by type (input, output, cache read, cache write) with daily trends and per-user drill-down.
        </div>
      </div>

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
        <h3 style={{ fontSize: '0.9375rem', color: colors.textPrimary, margin: 0, fontWeight: 600 }}>
          Token Usage
        </h3>
        <ExportButton section="token-usage" filters={filters} />
      </div>

      {/* Charts row */}
      <div style={{
        background: 'rgba(255,255,255,0.03)',
        border: '1px solid rgba(255,255,255,0.06)',
        borderLeft: `4px solid ${colors.accentBlue}`,
        borderRadius: '0 10px 10px 0',
        padding: '1.25rem',
        marginBottom: '1.5rem',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '1rem' }}>
          <span style={{ fontSize: '1.125rem' }}>📈</span>
          <span style={{ fontWeight: 700, fontSize: '0.9375rem', color: colors.textPrimary }}>Daily Trends</span>
        </div>
        <div style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap' }}>
        {/* Area chart — daily breakdown */}
        <div style={{ ...glass.card, flex: '1 1 480px', padding: '1.25rem' }}>
          <div style={{ fontSize: '0.8125rem', fontWeight: 600, color: colors.textSecondary, marginBottom: '1rem' }}>
            Daily Token Usage
          </div>
          {daily.length > 0 ? (
            <ResponsiveContainer width="100%" height={260}>
              <AreaChart data={daily} margin={{ top: 5, right: 10, left: 0, bottom: 5 }}>
                <defs>
                  {Object.entries(TOKEN_COLORS).map(([key, color]) => (
                    <linearGradient key={key} id={`grad-${key}`} x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor={color} stopOpacity={0.3} />
                      <stop offset="95%" stopColor={color} stopOpacity={0.02} />
                    </linearGradient>
                  ))}
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke={colors.tableBorder} />
                <XAxis dataKey="date" fontSize={11} tick={{ fill: colors.textMuted }} axisLine={false} tickLine={false} />
                <YAxis fontSize={11} tick={{ fill: colors.textMuted }} axisLine={false} tickLine={false} tickFormatter={(v: number) => v >= 1000 ? `${(v/1000).toFixed(0)}k` : String(v)} />
                <Tooltip content={<DarkTooltip />} />
                <Legend wrapperStyle={{ fontSize: '0.75rem', color: colors.textSecondary }} />
                <Area type="monotone" dataKey="inputTokens" name="Input" stackId="1" stroke={TOKEN_COLORS.inputTokens} fill={`url(#grad-inputTokens)`} strokeWidth={1.5} />
                <Area type="monotone" dataKey="outputTokens" name="Output" stackId="1" stroke={TOKEN_COLORS.outputTokens} fill={`url(#grad-outputTokens)`} strokeWidth={1.5} />
                <Area type="monotone" dataKey="cacheReadTokens" name="Cache Read" stackId="1" stroke={TOKEN_COLORS.cacheReadTokens} fill={`url(#grad-cacheReadTokens)`} strokeWidth={1.5} />
                <Area type="monotone" dataKey="cacheWriteTokens" name="Cache Write" stackId="1" stroke={TOKEN_COLORS.cacheWriteTokens} fill={`url(#grad-cacheWriteTokens)`} strokeWidth={1.5} />
              </AreaChart>
            </ResponsiveContainer>
          ) : (
            <p style={{ color: colors.textMuted, fontSize: '0.875rem', margin: 0 }}>No daily data available.</p>
          )}
        </div>

        {/* Pie chart — token type breakdown */}
        {tokenTypeTotals.length > 0 && (
          <div style={{ ...glass.card, flex: '0 0 260px', padding: '1.25rem', display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
            <div style={{ fontSize: '0.8125rem', fontWeight: 600, color: colors.textSecondary, marginBottom: '1rem', alignSelf: 'flex-start' }}>
              Token Type Breakdown
            </div>
            <ResponsiveContainer width="100%" height={180}>
              <PieChart>
                <Pie
                  data={tokenTypeTotals}
                  cx="50%"
                  cy="50%"
                  innerRadius={50}
                  outerRadius={80}
                  paddingAngle={3}
                  dataKey="value"
                >
                  {tokenTypeTotals.map((_, i) => (
                    <Cell key={i} fill={PIE_COLORS[i % PIE_COLORS.length]} />
                  ))}
                </Pie>
                <Tooltip
                  contentStyle={{ background: 'rgba(13,17,23,0.95)', border: `1px solid ${colors.cardBorder}`, borderRadius: '8px', fontSize: '0.8125rem' }}
                  itemStyle={{ color: colors.textPrimary }}
                  formatter={(v: number) => v.toLocaleString()}
                />
              </PieChart>
            </ResponsiveContainer>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.3rem', width: '100%', marginTop: '0.5rem' }}>
              {tokenTypeTotals.map((d, i) => (
                <div key={d.name} style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', fontSize: '0.75rem' }}>
                  <span style={{ width: '8px', height: '8px', borderRadius: '50%', background: PIE_COLORS[i % PIE_COLORS.length], flexShrink: 0 }} />
                  <span style={{ color: colors.textSecondary, flex: 1 }}>{d.name}</span>
                  <span style={{ color: colors.textPrimary, fontWeight: 600 }}>{d.value.toLocaleString()}</span>
                </div>
              ))}
            </div>
          </div>
        )}
        </div>
      </div>

      {/* Per-user token table */}
      <div style={{
        background: 'rgba(255,255,255,0.03)',
        border: '1px solid rgba(255,255,255,0.06)',
        borderLeft: `4px solid ${colors.accentPurple}`,
        borderRadius: '0 10px 10px 0',
        padding: '1.25rem',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.75rem' }}>
          <span style={{ fontSize: '1.125rem' }}>👤</span>
          <span style={{ fontWeight: 700, fontSize: '0.9375rem', color: colors.textPrimary }}>Per-User Token Usage</span>
        </div>
        {sortedUsers.length === 0 ? (
          <p style={{ color: colors.textMuted, fontSize: '0.875rem', margin: 0 }}>No user data available.</p>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr>
                  <th style={thStyle} onClick={() => handleSort('userName')}>User{sortIndicator('userName')}</th>
                  <th style={{ ...thStyle, textAlign: 'right' }} onClick={() => handleSort('inputTokens')}>Input{sortIndicator('inputTokens')}</th>
                  <th style={{ ...thStyle, textAlign: 'right' }} onClick={() => handleSort('outputTokens')}>Output{sortIndicator('outputTokens')}</th>
                  <th style={{ ...thStyle, textAlign: 'right' }} onClick={() => handleSort('cacheReadTokens')}>Cache Read{sortIndicator('cacheReadTokens')}</th>
                  <th style={{ ...thStyle, textAlign: 'right' }} onClick={() => handleSort('cacheWriteTokens')}>Cache Write{sortIndicator('cacheWriteTokens')}</th>
                  <th style={{ ...thStyle, textAlign: 'right' }} onClick={() => handleSort('totalTokens')}>Total{sortIndicator('totalTokens')}</th>
                  <th style={{ ...thStyle, textAlign: 'right' }} onClick={() => handleSort('costEstimate')}>Cost{sortIndicator('costEstimate')}</th>
                </tr>
              </thead>
              <tbody>
                {sortedUsers.map((user) => (
                  <React.Fragment key={user.userIdentity}>
                    <tr
                      onClick={() => setExpandedUser(expandedUser === user.userIdentity ? null : user.userIdentity)}
                      style={{ cursor: 'pointer', background: expandedUser === user.userIdentity ? 'rgba(88,166,255,0.08)' : 'transparent' }}
                      onMouseEnter={(e) => { if (expandedUser !== user.userIdentity) (e.currentTarget as HTMLTableRowElement).style.background = colors.tableRowHover; }}
                      onMouseLeave={(e) => { if (expandedUser !== user.userIdentity) (e.currentTarget as HTMLTableRowElement).style.background = 'transparent'; }}
                      data-testid={`user-row-${user.userIdentity}`}
                    >
                      <td style={tdStyle}>{user.userName}</td>
                      <td style={{ ...tdStyle, textAlign: 'right' }}>{formatNumber(user.inputTokens)}</td>
                      <td style={{ ...tdStyle, textAlign: 'right' }}>{formatNumber(user.outputTokens)}</td>
                      <td style={{ ...tdStyle, textAlign: 'right' }}>{formatNumber(user.cacheReadTokens)}</td>
                      <td style={{ ...tdStyle, textAlign: 'right' }}>{formatNumber(user.cacheWriteTokens)}</td>
                      <td style={{ ...tdStyle, textAlign: 'right', fontWeight: 600, color: user.totalTokens > 100000 ? '#f85149' : user.totalTokens > 50000 ? colors.accentOrange : colors.accentBlue }}>
                        {formatNumber(user.totalTokens)}
                        {user.totalTokens > 100000 && <span style={{ marginLeft: '0.25rem', fontSize: '0.6rem' }}>🔥</span>}
                      </td>
                      <td style={{ ...tdStyle, textAlign: 'right', color: colors.accentTeal }}>{formatCost(user.costEstimate)}</td>
                    </tr>
                    {expandedUser === user.userIdentity && (
                      <tr data-testid={`user-drilldown-${user.userIdentity}`}>
                        <td colSpan={7} style={{ padding: '0.75rem 1rem', background: 'rgba(88,166,255,0.04)', borderBottom: `1px solid ${colors.tableBorder}` }}>
                          <div style={{ fontSize: '0.8125rem', fontWeight: 600, marginBottom: '0.5rem', color: colors.textSecondary }}>
                            Daily Breakdown for {user.userName}
                          </div>
                          {daily.length > 0 ? (
                            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                              <thead>
                                <tr>
                                  <th style={{ ...thStyle, cursor: 'default' }}>Date</th>
                                  <th style={{ ...thStyle, textAlign: 'right', cursor: 'default' }}>Input</th>
                                  <th style={{ ...thStyle, textAlign: 'right', cursor: 'default' }}>Output</th>
                                  <th style={{ ...thStyle, textAlign: 'right', cursor: 'default' }}>Cache Read</th>
                                  <th style={{ ...thStyle, textAlign: 'right', cursor: 'default' }}>Cache Write</th>
                                </tr>
                              </thead>
                              <tbody>
                                {daily.map((d) => (
                                  <tr key={d.date}>
                                    <td style={tdStyle}>{d.date}</td>
                                    <td style={{ ...tdStyle, textAlign: 'right' }}>{formatNumber(d.inputTokens)}</td>
                                    <td style={{ ...tdStyle, textAlign: 'right' }}>{formatNumber(d.outputTokens)}</td>
                                    <td style={{ ...tdStyle, textAlign: 'right' }}>{formatNumber(d.cacheReadTokens)}</td>
                                    <td style={{ ...tdStyle, textAlign: 'right' }}>{formatNumber(d.cacheWriteTokens)}</td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          ) : (
                            <p style={{ color: colors.textMuted, fontSize: '0.8125rem', margin: 0 }}>No daily breakdown available.</p>
                          )}
                        </td>
                      </tr>
                    )}
                  </React.Fragment>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
};

export default TokenUsageTab;
