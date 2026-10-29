import React, { useEffect, useState, useMemo } from 'react';
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Cell,
  LabelList,
  Tooltip,
  ResponsiveContainer,
} from 'recharts';
import type { DashboardFilters, ModelUsageSummary, UserModelCell } from '../types';
import { getModelUsage } from '../api/client';
import LoadingSpinner from './LoadingSpinner';
import ErrorBanner from './ErrorBanner';
import ExportButton from './ExportButton';
import { colors } from '../theme';
import { formatCost } from '../services/formatCost';

interface ModelUsageTabProps {
  filters: DashboardFilters;
}

/** Max number of bars shown before the "Show all" toggle collapses the chart. */
const TOP_N = 10;

/**
 * Sequential blue heat scale: map a model's total cost to a color, light blue
 * for low cost, dark/saturated blue for high cost. Null/zero cost renders as a
 * neutral muted grey so it stays readable on the dark theme.
 */
function costColor(cost: number | null, maxCost: number): string {
  if (cost === null || !Number.isFinite(cost) || cost <= 0 || maxCost <= 0) {
    return 'rgba(140,160,180,0.35)';
  }
  const t = Math.min(1, Math.max(0, cost / maxCost));
  const hue = 210;
  const sat = 55 + t * 35; // 55% -> 90%
  const light = 72 - t * 40; // 72% -> 32%
  return `hsl(${hue}, ${sat}%, ${light}%)`;
}

/** Shorten a long model id for the Y-axis label (strip provider prefix, truncate). */
function shortenModelId(modelId: string, maxLen = 22): string {
  // Strip a leading provider prefix like "anthropic." or "us.anthropic."
  let label = modelId;
  const parts = label.split('.');
  if (parts.length > 1) {
    // Drop known provider-ish prefixes (region/provider segments) but keep the model name
    const providerPrefixes = new Set(['us', 'eu', 'apac', 'anthropic', 'amazon', 'meta', 'cohere', 'ai21', 'mistral', 'stability']);
    while (parts.length > 1 && providerPrefixes.has(parts[0].toLowerCase())) {
      parts.shift();
    }
    label = parts.join('.');
  }
  if (label.length > maxLen) {
    return `${label.slice(0, maxLen - 1)}…`;
  }
  return label;
}

function formatNumber(n: number): string {
  return n.toLocaleString();
}

function formatPercentage(pct: number): string {
  return `${pct.toFixed(1)}%`;
}

const thStyle: React.CSSProperties = {
  padding: '0.5rem 0.75rem',
  textAlign: 'left',
  fontSize: '0.75rem',
  fontWeight: 600,
  color: '#555',
  borderBottom: '2px solid #e0e0e0',
  whiteSpace: 'nowrap',
};

const tdStyle: React.CSSProperties = {
  padding: '0.5rem 0.75rem',
  fontSize: '0.8125rem',
  borderBottom: '1px solid #f0f0f0',
};

const ModelUsageTab: React.FC<ModelUsageTabProps> = ({ filters }) => {
  const [models, setModels] = useState<ModelUsageSummary[]>([]);
  const [matrix, setMatrix] = useState<UserModelCell[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expandedModel, setExpandedModel] = useState<string | null>(null);
  const [showAllModels, setShowAllModels] = useState(false);

  const fetchData = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await getModelUsage(filters);
      setModels(data.models);
      setMatrix(data.matrix);
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Failed to load model usage data';
      setError(message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- fetchData is recreated each render; effect must re-run only when filters change (adding it would loop)
  }, [filters]);

  /** Bar chart data: models sorted by invocation count DESC */
  const barData = useMemo(
    () =>
      [...models]
        .sort((a, b) => b.invocationCount - a.invocationCount)
        .map((m) => ({
          modelId: m.modelId,
          shortName: shortenModelId(m.modelId),
          value: m.invocationCount,
          percentage: m.percentageOfTotal,
          costEstimate: m.costEstimate,
        })),
    [models]
  );

  /** Bars actually rendered: top-N unless the user expands to show all. */
  const shownBars = useMemo(
    () => (showAllModels ? barData : barData.slice(0, TOP_N)),
    [barData, showAllModels]
  );

  /** Max cost across the shown bars — drives the cost color scale. */
  const maxCost = useMemo(
    () =>
      shownBars.reduce(
        (max, b) => (b.costEstimate != null && b.costEstimate > max ? b.costEstimate : max),
        0
      ),
    [shownBars]
  );

  /** Totals across ALL models (not just the shown top-N) for the summary caption. */
  const totalInvocations = useMemo(
    () => models.reduce((sum, m) => sum + m.invocationCount, 0),
    [models]
  );
  const modelCount = models.length;

  /** Height sized to the number of bars actually shown so bars never crowd. */
  const chartHeight = Math.max(240, shownBars.length * 34);

  /** Users for a given model (from the matrix data) */
  const usersForModel = (modelId: string): UserModelCell[] =>
    matrix.filter((cell) => cell.modelId === modelId);

  /**
   * Compact per-user usage: group the matrix cells by user, keeping only the
   * models a user actually invoked (no empty cells). Users are sorted by total
   * invocations DESC; each user's models are sorted by invocation count DESC.
   */
  const userUsage = useMemo(() => {
    const groups = new Map<
      string,
      {
        userIdentity: string;
        userName: string;
        total: number;
        models: { modelId: string; invocationCount: number; costEstimate: number | null }[];
      }
    >();
    matrix.forEach((c) => {
      let group = groups.get(c.userIdentity);
      if (!group) {
        group = { userIdentity: c.userIdentity, userName: c.userName, total: 0, models: [] };
        groups.set(c.userIdentity, group);
      }
      group.total += c.invocationCount;
      group.models.push({
        modelId: c.modelId,
        invocationCount: c.invocationCount,
        costEstimate: c.costEstimate,
      });
    });
    const list = Array.from(groups.values());
    list.forEach((g) => g.models.sort((a, b) => b.invocationCount - a.invocationCount));
    list.sort((a, b) => b.total - a.total);
    return list;
  }, [matrix]);

  if (loading) return <LoadingSpinner message="Loading model usage..." />;
  if (error) return <ErrorBanner message={error} onRetry={fetchData} />;

  return (
    <div data-testid="tab-model-usage">
      {/* Info banner */}
      <div style={{ marginBottom: '1.25rem', padding: '1rem 1.25rem', background: 'rgba(88,166,255,0.04)', border: '1px solid rgba(88,166,255,0.12)', borderRadius: 10 }}>
        <div style={{ fontSize: '0.8125rem', color: colors.textSecondary, lineHeight: 1.6 }}>
          Model-level analytics showing invocation distribution, token consumption, and cost per model. Click a model row to see per-user breakdown.
        </div>
      </div>

      {/* Pie / donut chart — % of invocations per model */}
      <div style={{
        background: 'rgba(255,255,255,0.03)',
        border: '1px solid rgba(255,255,255,0.06)',
        borderLeft: `4px solid ${colors.accentBlue}`,
        borderRadius: '0 10px 10px 0',
        padding: '1.25rem',
        marginBottom: '1.5rem',
      }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.75rem' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <span style={{ fontSize: '1.125rem' }}>📊</span>
            <span style={{ fontWeight: 700, fontSize: '0.9375rem', color: colors.textPrimary }}>Invocations by Model</span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            {models.length > TOP_N && (
              <button
                type="button"
                onClick={() => setShowAllModels((v) => !v)}
                data-testid="toggle-show-all-models"
                style={{
                  background: colors.tableHeaderBg,
                  border: `1px solid ${colors.cardBorder}`,
                  color: colors.textSecondary,
                  borderRadius: 6,
                  padding: '0.25rem 0.625rem',
                  fontSize: '0.75rem',
                  fontWeight: 600,
                  cursor: 'pointer',
                  whiteSpace: 'nowrap',
                }}
              >
                {showAllModels ? `Show top ${TOP_N}` : `Show all (${models.length})`}
              </button>
            )}
            <ExportButton section="model-usage" filters={filters} />
          </div>
        </div>
        {models.length > 0 && (
          <div style={{ fontSize: '0.75rem', color: colors.textMuted, marginBottom: '0.75rem' }}>
            {`${formatNumber(totalInvocations)} invocations across ${modelCount} models`}
          </div>
        )}
      {barData.length > 0 ? (
        <div style={{ width: '100%', maxHeight: 480, overflowY: 'auto', marginBottom: '1.5rem' }}>
          <div style={{ width: '100%', height: chartHeight }}>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart
                data={shownBars}
                layout="vertical"
                margin={{ top: 8, right: 56, bottom: 8, left: 8 }}
              >
                <CartesianGrid horizontal={false} stroke="rgba(255,255,255,0.06)" />
                <XAxis type="number" tick={{ fontSize: 11, fill: colors.textSecondary }} />
                <YAxis
                  type="category"
                  dataKey="shortName"
                  width={180}
                  tick={{ fontSize: 11, fill: colors.textSecondary }}
                />
                <Tooltip
                  cursor={{ fill: 'rgba(255,255,255,0.04)' }}
                  contentStyle={{
                    background: '#0f1a35',
                    border: '1px solid rgba(88,166,255,0.3)',
                    borderRadius: 8,
                    fontSize: '0.75rem',
                  }}
                  formatter={(
                    value: number,
                    _name: string,
                    entry: { payload?: { percentage?: number; costEstimate?: number | null } }
                  ) => {
                    const pct = entry?.payload?.percentage ?? 0;
                    const cost = entry?.payload?.costEstimate ?? null;
                    return [
                      `${formatNumber(value)} (${pct.toFixed(1)}%) · ${formatCost(cost, 2)}`,
                      'Invocations',
                    ];
                  }}
                  labelFormatter={(_label: string, payload: ReadonlyArray<{ payload?: { modelId?: string } }>) =>
                    payload && payload.length > 0 ? payload[0].payload?.modelId ?? '' : ''
                  }
                />
                <Bar dataKey="value" radius={[0, 4, 4, 0]}>
                  {shownBars.map((entry, idx) => (
                    <Cell key={idx} fill={costColor(entry.costEstimate, maxCost)} />
                  ))}
                  <LabelList
                    dataKey="value"
                    position="right"
                    style={{ fill: colors.textSecondary, fontSize: 11 }}
                    formatter={(v: number) => formatNumber(v)}
                  />
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
          <div style={{ fontSize: '0.6875rem', color: colors.textMuted, marginTop: '0.5rem' }}>
            Bar color = total cost (darker = higher)
          </div>
        </div>
      ) : (
        <p style={{ color: '#666', fontSize: '0.875rem', marginBottom: '1.5rem' }}>
          No model data available.
        </p>
      )}
      </div>

      {/* Model summary table */}
      <div style={{
        background: 'rgba(255,255,255,0.03)',
        border: '1px solid rgba(255,255,255,0.06)',
        borderLeft: `4px solid ${colors.accentPurple}`,
        borderRadius: '0 10px 10px 0',
        padding: '1.25rem',
        marginBottom: '1.5rem',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.75rem' }}>
          <span style={{ fontSize: '1.125rem' }}>◈</span>
          <span style={{ fontWeight: 700, fontSize: '0.9375rem', color: colors.textPrimary }}>Model Summary</span>
        </div>
      {models.length === 0 ? (
        <p style={{ color: '#666', fontSize: '0.875rem' }}>No model data available.</p>
      ) : (
        <div style={{ overflowX: 'auto', marginBottom: '1.5rem' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr>
                <th style={thStyle}>Model ID</th>
                <th style={{ ...thStyle, textAlign: 'right' }}>Invocations</th>
                <th style={{ ...thStyle, textAlign: 'right' }}>Input Tokens</th>
                <th style={{ ...thStyle, textAlign: 'right' }}>Output Tokens</th>
                <th style={{ ...thStyle, textAlign: 'right' }}>Cache Read</th>
                <th style={{ ...thStyle, textAlign: 'right' }}>Cache Write</th>
                <th style={{ ...thStyle, textAlign: 'right' }}>Total Tokens</th>
                <th style={{ ...thStyle, textAlign: 'right' }}>Cost</th>
                <th style={{ ...thStyle, textAlign: 'right' }}>% of Invocations</th>
              </tr>
            </thead>
            <tbody>
              {models.map((model) => (
                <React.Fragment key={model.modelId}>
                  <tr
                    onClick={() =>
                      setExpandedModel(expandedModel === model.modelId ? null : model.modelId)
                    }
                    style={{
                      cursor: 'pointer',
                      background: expandedModel === model.modelId ? '#f0f7ff' : 'transparent',
                    }}
                    data-testid={`model-row-${model.modelId}`}
                  >
                    <td style={tdStyle}>{model.modelId}</td>
                    <td style={{ ...tdStyle, textAlign: 'right' }}>
                      {formatNumber(model.invocationCount)}
                    </td>
                    <td style={{ ...tdStyle, textAlign: 'right' }}>
                      {formatNumber(model.inputTokens)}
                    </td>
                    <td style={{ ...tdStyle, textAlign: 'right' }}>
                      {formatNumber(model.outputTokens)}
                    </td>
                    <td style={{ ...tdStyle, textAlign: 'right' }}>
                      {formatNumber(model.cacheReadTokens)}
                    </td>
                    <td style={{ ...tdStyle, textAlign: 'right' }}>
                      {formatNumber(model.cacheWriteTokens)}
                    </td>
                    <td style={{ ...tdStyle, textAlign: 'right', fontWeight: 600 }}>
                      {formatNumber(model.totalTokens)}
                    </td>
                    <td style={{ ...tdStyle, textAlign: 'right' }}>
                      {formatCost(model.costEstimate, 2)}
                    </td>
                    <td style={{ ...tdStyle, textAlign: 'right' }}>
                      {formatPercentage(model.percentageOfTotal)}
                    </td>
                  </tr>
                  {expandedModel === model.modelId && (
                    <tr data-testid={`model-users-${model.modelId}`}>
                      <td
                        colSpan={9}
                        style={{
                          padding: '0.75rem 1rem',
                          background: '#f8fafc',
                          borderBottom: '1px solid #e0e0e0',
                        }}
                      >
                        <div
                          style={{
                            fontSize: '0.8125rem',
                            fontWeight: 600,
                            marginBottom: '0.5rem',
                            color: '#16191f',
                          }}
                        >
                          Users for {model.modelId}
                        </div>
                        {usersForModel(model.modelId).length > 0 ? (
                          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                            <thead>
                              <tr>
                                <th style={{ ...thStyle, cursor: 'default' }}>User</th>
                                <th style={{ ...thStyle, textAlign: 'right', cursor: 'default' }}>
                                  Invocations
                                </th>
                                <th style={{ ...thStyle, textAlign: 'right', cursor: 'default' }}>
                                  Cost
                                </th>
                              </tr>
                            </thead>
                            <tbody>
                              {usersForModel(model.modelId).map((cell) => (
                                <tr key={cell.userIdentity}>
                                  <td style={tdStyle}>{cell.userName}</td>
                                  <td style={{ ...tdStyle, textAlign: 'right' }}>
                                    {formatNumber(cell.invocationCount)}
                                  </td>
                                  <td style={{ ...tdStyle, textAlign: 'right' }}>
                                    {formatCost(cell.costEstimate, 2)}
                                  </td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        ) : (
                          <p style={{ color: '#666', fontSize: '0.8125rem' }}>
                            No user data for this model.
                          </p>
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

      {/* Per-user model usage (compact) */}
      <div style={{
        background: 'rgba(255,255,255,0.03)',
        border: '1px solid rgba(255,255,255,0.06)',
        borderLeft: `4px solid ${colors.accentTeal}`,
        borderRadius: '0 10px 10px 0',
        padding: '1.25rem',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.75rem' }}>
          <span style={{ fontSize: '1.125rem' }}>⊞</span>
          <span style={{ fontWeight: 700, fontSize: '0.9375rem', color: colors.textPrimary }}>User Model Usage</span>
        </div>
        <div style={{ fontSize: '0.75rem', color: colors.textMuted, marginBottom: '0.75rem' }}>
          Models each user invoked in range, with per-model invocation counts.
        </div>
      {userUsage.length === 0 ? (
        <p style={{ color: '#666', fontSize: '0.875rem' }}>No user/model usage in range.</p>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
          {userUsage.map((user) => (
            <div
              key={user.userIdentity}
              data-testid={`user-usage-${user.userIdentity}`}
              style={{
                background: 'rgba(255,255,255,0.02)',
                border: `1px solid ${colors.tableBorder}`,
                borderRadius: 8,
                padding: '0.75rem 0.875rem',
              }}
            >
              <div
                style={{
                  display: 'flex',
                  alignItems: 'baseline',
                  justifyContent: 'space-between',
                  gap: '0.75rem',
                  marginBottom: '0.5rem',
                }}
              >
                <span
                  style={{
                    fontWeight: 700,
                    fontSize: '0.875rem',
                    color: colors.textPrimary,
                    wordBreak: 'break-all',
                  }}
                >
                  {user.userName}
                </span>
                <span style={{ fontSize: '0.75rem', color: colors.textMuted, whiteSpace: 'nowrap' }}>
                  {`${formatNumber(user.total)} invocations`}
                </span>
              </div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.375rem' }}>
                {user.models.map((m) => (
                  <span
                    key={m.modelId}
                    style={{
                      display: 'inline-flex',
                      alignItems: 'center',
                      gap: '0.375rem',
                      background: colors.tableHeaderBg,
                      border: `1px solid ${colors.cardBorder}`,
                      borderRadius: 6,
                      padding: '0.1875rem 0.5rem',
                      fontSize: '0.75rem',
                    }}
                  >
                    <span style={{ color: colors.textSecondary, fontFamily: 'monospace' }}>
                      {m.modelId}
                    </span>
                    <span style={{ color: colors.textMuted }}>·</span>
                    <span style={{ color: colors.accentTeal, fontWeight: 700 }}>
                      {formatNumber(m.invocationCount)}
                    </span>
                    <span style={{ color: colors.textMuted }}>
                      {formatCost(m.costEstimate, 2)}
                    </span>
                  </span>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
      </div>
    </div>
  );
};

export default ModelUsageTab;
