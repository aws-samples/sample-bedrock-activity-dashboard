import React, { useEffect, useState } from 'react';
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  PieChart,
  Pie,
  Cell,
  BarChart,
  Bar,
  TooltipProps,
} from 'recharts';
import type { DashboardFilters, CostAnalysis } from '../types';
import { getCostAnalysis } from '../api/client';
import LoadingSpinner from './LoadingSpinner';
import ErrorBanner from './ErrorBanner';
import { formatCost } from '../services/formatCost';
import ExportButton from './ExportButton';
import { colors, glass } from '../theme';

interface CostAnalysisTabProps {
  filters: DashboardFilters;
}

const MODEL_COLORS = ['#58a6ff', '#bc8cff', '#39d353', '#e3b341', '#f78166', '#56d364', '#79c0ff'];

function formatPercentage(pct: number): string {
  return `${pct.toFixed(1)}%`;
}

/** Human label for a cost-basis tier (Req 2.4). */
function basisLabel(basis: string | undefined): string {
  switch (basis) {
    case 'billed':
      return 'Billed';
    case 'reconciled':
      return 'Reconciled';
    default:
      return 'Estimated';
  }
}

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
          <span style={{ fontWeight: 600 }}>${(entry.value as number).toFixed(4)}</span>
        </div>
      ))}
    </div>
  );
};

const CostAnalysisTab: React.FC<CostAnalysisTabProps> = ({ filters }) => {
  const [costData, setCostData] = useState<CostAnalysis | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchData = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await getCostAnalysis(filters);
      setCostData(data);
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Failed to load cost analysis data';
      setError(message);
    } finally {
      setLoading(false);
    }
  };

  // eslint-disable-next-line react-hooks/exhaustive-deps -- fetchData is recreated each render; effect must re-run only when filters change (adding it would loop)
  useEffect(() => { fetchData(); }, [filters]);

  if (loading) return <LoadingSpinner message="Loading cost analysis..." />;
  if (error) return <ErrorBanner message={error} onRetry={fetchData} />;
  if (!costData) return null;

  return (
    <div data-testid="tab-cost-analysis">
      {/* Info banner */}
      <div style={{ marginBottom: '1.25rem', padding: '1rem 1.25rem', background: 'rgba(88,166,255,0.04)', border: '1px solid rgba(88,166,255,0.12)', borderRadius: 10 }}>
        <div style={{ fontSize: '0.8125rem', color: colors.textSecondary, lineHeight: 1.6 }}>
          Cost breakdown by model, user, and time period. Costs are computed using the pricing configuration set in the Pricing tab.
        </div>
      </div>

      <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: '1rem' }}>
        <ExportButton section="cost-analysis" filters={filters} />
      </div>

      {/* Summary cards */}
      {(() => {
        // Additive cost-accountability labeling (Req 1.6, 2.4, 2.5). CUR-sourced
        // billed data is available only when the backend returns a billed total.
        const curAvailable = costData.billedCost !== null && costData.billedCost !== undefined;
        const pillLabel = curAvailable
          ? basisLabel(costData.costBasis)
          : 'Estimated (no billed data available)';
        const pillAccent = curAvailable ? colors.accentTeal : colors.accentBlue;
        return (
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.75rem', marginBottom: '1.5rem' }}>
            {/* Estimated Cost card with basis pill */}
            <div style={{
              background: 'rgba(255,255,255,0.03)',
              border: '1px solid rgba(255,255,255,0.06)',
              borderLeft: `4px solid ${colors.accentBlue}`,
              borderRadius: '0 10px 10px 0',
              flex: '1 1 180px',
              padding: '1rem 1.25rem',
              minWidth: '160px',
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.375rem', flexWrap: 'wrap' }}>
                <span style={{ fontSize: '0.75rem', color: colors.textMuted, fontWeight: 500 }}>Estimated Cost</span>
                <span
                  data-testid="cost-basis-pill"
                  style={{
                    fontSize: '0.625rem',
                    fontWeight: 700,
                    letterSpacing: '0.04em',
                    textTransform: 'uppercase',
                    padding: '0.15rem 0.45rem',
                    borderRadius: 999,
                    background: `${pillAccent}1f`,
                    color: pillAccent,
                    border: `1px solid ${pillAccent}55`,
                  }}
                >
                  {pillLabel}
                </span>
              </div>
              <div style={{ fontSize: '1.75rem', fontWeight: 700, color: colors.accentBlue }} data-testid="total-cost">
                {formatCost(costData.totalCost)}
              </div>
            </div>

            {/* Billed + Variance cards, shown only when CUR billed data is available */}
            {curAvailable && (
              <>
                <div style={{
                  background: 'rgba(255,255,255,0.03)',
                  border: '1px solid rgba(255,255,255,0.06)',
                  borderLeft: `4px solid ${colors.accentTeal}`,
                  borderRadius: '0 10px 10px 0',
                  flex: '1 1 180px',
                  padding: '1rem 1.25rem',
                  minWidth: '160px',
                }}>
                  <div style={{ fontSize: '0.75rem', color: colors.textMuted, fontWeight: 500, marginBottom: '0.375rem' }}>Billed Cost</div>
                  <div style={{ fontSize: '1.75rem', fontWeight: 700, color: colors.accentTeal }} data-testid="billed-cost">
                    {formatCost(costData.billedCost)}
                  </div>
                </div>
                <div style={{
                  background: 'rgba(255,255,255,0.03)',
                  border: '1px solid rgba(255,255,255,0.06)',
                  borderLeft: `4px solid ${colors.accentPurple}`,
                  borderRadius: '0 10px 10px 0',
                  flex: '1 1 180px',
                  padding: '1rem 1.25rem',
                  minWidth: '160px',
                }}>
                  <div style={{ fontSize: '0.75rem', color: colors.textMuted, fontWeight: 500, marginBottom: '0.375rem' }}>Variance (Billed − Estimated)</div>
                  <div style={{ fontSize: '1.75rem', fontWeight: 700, color: colors.accentPurple }} data-testid="variance-cost">
                    {costData.variance !== null && costData.variance !== undefined ? formatCost(costData.variance) : 'N/A'}
                  </div>
                </div>
              </>
            )}
          </div>
        );
      })()}

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
          <span style={{ fontWeight: 700, fontSize: '0.9375rem', color: colors.textPrimary }}>Cost Trends & Distribution</span>
        </div>
        <div style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap' }}>
        {/* Area chart — daily cost trend */}
        <div style={{ ...glass.card, flex: '1 1 480px', padding: '1.25rem' }}>
          <div style={{ fontSize: '0.8125rem', fontWeight: 600, color: colors.textSecondary, marginBottom: '1rem' }}>
            Daily Cost Trend
          </div>
          {costData.dailyCosts.length > 0 ? (
            <ResponsiveContainer width="100%" height={240}>
              <AreaChart data={costData.dailyCosts} margin={{ top: 5, right: 10, left: 0, bottom: 5 }}>
                <defs>
                  <linearGradient id="costGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor={colors.accentBlue} stopOpacity={0.35} />
                    <stop offset="95%" stopColor={colors.accentBlue} stopOpacity={0.02} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke={colors.tableBorder} />
                <XAxis dataKey="date" fontSize={11} tick={{ fill: colors.textMuted }} axisLine={false} tickLine={false} />
                <YAxis fontSize={11} tick={{ fill: colors.textMuted }} axisLine={false} tickLine={false} tickFormatter={(v: number) => `$${v.toFixed(2)}`} />
                <Tooltip content={<DarkTooltip />} />
                <Area
                  type="monotone"
                  dataKey="costEstimate"
                  name="Daily Cost"
                  stroke={colors.accentBlue}
                  fill="url(#costGrad)"
                  strokeWidth={2}
                  dot={{ r: 3, fill: colors.accentBlue, strokeWidth: 0 }}
                  activeDot={{ r: 5, fill: colors.accentBlue }}
                />
              </AreaChart>
            </ResponsiveContainer>
          ) : (
            <p style={{ color: colors.textMuted, fontSize: '0.875rem', margin: 0 }}>No daily cost data available.</p>
          )}
        </div>

        {/* Donut — per-model cost */}
        {costData.perModelCosts.length > 0 && (
          <div style={{ ...glass.card, flex: '0 0 260px', padding: '1.25rem', display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
            <div style={{ fontSize: '0.8125rem', fontWeight: 600, color: colors.textSecondary, marginBottom: '1rem', alignSelf: 'flex-start' }}>
              Cost by Model
            </div>
            <ResponsiveContainer width="100%" height={160}>
              <PieChart>
                <Pie
                  data={costData.perModelCosts}
                  cx="50%"
                  cy="50%"
                  innerRadius={45}
                  outerRadius={72}
                  paddingAngle={3}
                  dataKey="costEstimate"
                  nameKey="modelId"
                >
                  {costData.perModelCosts.map((_, i) => (
                    <Cell key={i} fill={MODEL_COLORS[i % MODEL_COLORS.length]} />
                  ))}
                </Pie>
                <Tooltip
                  contentStyle={{ background: 'rgba(13,17,23,0.95)', border: `1px solid ${colors.cardBorder}`, borderRadius: '8px', fontSize: '0.8125rem' }}
                  itemStyle={{ color: colors.textPrimary }}
                  formatter={(v: number) => `$${v.toFixed(4)}`}
                />
              </PieChart>
            </ResponsiveContainer>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.3rem', width: '100%', marginTop: '0.5rem' }}>
              {costData.perModelCosts.slice(0, 5).map((m, i) => (
                <div key={m.modelId} style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', fontSize: '0.7rem' }}>
                  <span style={{ width: '8px', height: '8px', borderRadius: '50%', background: MODEL_COLORS[i % MODEL_COLORS.length], flexShrink: 0 }} />
                  <span style={{ color: colors.textSecondary, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{m.modelId}</span>
                  <span style={{ color: colors.textPrimary, fontWeight: 600, flexShrink: 0 }}>{formatCost(m.costEstimate, 2)}</span>
                </div>
              ))}
            </div>
          </div>
        )}
        </div>
      </div>

      {/* Horizontal bar — per-user cost */}
      {costData.perUserCosts.length > 0 && (
        <div style={{
          background: 'rgba(255,255,255,0.03)',
          border: '1px solid rgba(255,255,255,0.06)',
          borderLeft: `4px solid ${colors.accentPurple}`,
          borderRadius: '0 10px 10px 0',
          padding: '1.25rem',
          marginBottom: '1.5rem',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '1rem' }}>
            <span style={{ fontSize: '1.125rem' }}>👤</span>
            <span style={{ fontWeight: 700, fontSize: '0.9375rem', color: colors.textPrimary }}>Cost by User</span>
          </div>
          <ResponsiveContainer width="100%" height={Math.max(120, costData.perUserCosts.length * 36)}>
            <BarChart
              data={costData.perUserCosts}
              layout="vertical"
              margin={{ top: 0, right: 60, left: 10, bottom: 0 }}
            >
              <CartesianGrid strokeDasharray="3 3" stroke={colors.tableBorder} horizontal={false} />
              <XAxis type="number" fontSize={11} tick={{ fill: colors.textMuted }} axisLine={false} tickLine={false} tickFormatter={(v: number) => `$${v.toFixed(2)}`} />
              <YAxis type="category" dataKey="userName" fontSize={11} tick={{ fill: colors.textSecondary }} axisLine={false} tickLine={false} width={90} />
              <Tooltip
                contentStyle={{ background: 'rgba(13,17,23,0.95)', border: `1px solid ${colors.cardBorder}`, borderRadius: '8px', fontSize: '0.8125rem' }}
                itemStyle={{ color: colors.textPrimary }}
                formatter={(v: number) => [`$${v.toFixed(4)}`, 'Cost']}
              />
              <Bar dataKey="costEstimate" name="Cost" fill={colors.accentPurple} radius={[0, 4, 4, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* Per-user cost table */}
      <div style={{
        background: 'rgba(255,255,255,0.03)',
        border: '1px solid rgba(255,255,255,0.06)',
        borderLeft: `4px solid ${colors.accentTeal}`,
        borderRadius: '0 10px 10px 0',
        padding: '1.25rem',
        marginBottom: '1.5rem',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.75rem' }}>
          <span style={{ fontSize: '1.125rem' }}>💰</span>
          <span style={{ fontWeight: 700, fontSize: '0.9375rem', color: colors.textPrimary }}>Cost by User (Detail)</span>
        </div>
        {costData.perUserCosts.length === 0 ? (
          <p style={{ color: colors.textMuted, fontSize: '0.875rem', margin: 0 }}>No per-user cost data available.</p>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr>
                  <th style={thStyle}>User</th>
                  <th style={{ ...thStyle, textAlign: 'right' }}>Cost Estimate</th>
                  <th style={{ ...thStyle, textAlign: 'right' }}>% of Total</th>
                </tr>
              </thead>
              <tbody>
                {costData.perUserCosts.map((user) => (
                  <tr key={user.userName} data-testid={`user-cost-row-${user.userName}`}
                    onMouseEnter={(e) => { (e.currentTarget as HTMLTableRowElement).style.background = colors.tableRowHover; }}
                    onMouseLeave={(e) => { (e.currentTarget as HTMLTableRowElement).style.background = 'transparent'; }}
                  >
                    <td style={tdStyle}>{user.userName}</td>
                    <td style={{ ...tdStyle, textAlign: 'right', color: colors.accentTeal }}>{formatCost(user.costEstimate)}</td>
                    <td style={{ ...tdStyle, textAlign: 'right', color: colors.textSecondary }}>{formatPercentage(user.percentageOfTotal)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Per-model cost table */}
      <div style={{
        background: 'rgba(255,255,255,0.03)',
        border: '1px solid rgba(255,255,255,0.06)',
        borderLeft: `4px solid ${colors.accentOrange}`,
        borderRadius: '0 10px 10px 0',
        padding: '1.25rem',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.75rem' }}>
          <span style={{ fontSize: '1.125rem' }}>◈</span>
          <span style={{ fontWeight: 700, fontSize: '0.9375rem', color: colors.textPrimary }}>Cost by Model (Detail)</span>
        </div>
        {costData.perModelCosts.length === 0 ? (
          <p style={{ color: colors.textMuted, fontSize: '0.875rem', margin: 0 }}>No per-model cost data available.</p>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr>
                  <th style={thStyle}>Model ID</th>
                  <th style={{ ...thStyle, textAlign: 'right' }}>Cost Estimate</th>
                  <th style={{ ...thStyle, textAlign: 'right' }}>% of Total</th>
                </tr>
              </thead>
              <tbody>
                {costData.perModelCosts.map((model) => (
                  <tr key={model.modelId} data-testid={`model-cost-row-${model.modelId}`}
                    onMouseEnter={(e) => { (e.currentTarget as HTMLTableRowElement).style.background = colors.tableRowHover; }}
                    onMouseLeave={(e) => { (e.currentTarget as HTMLTableRowElement).style.background = 'transparent'; }}
                  >
                    <td style={tdStyle}>{model.modelId}</td>
                    <td style={{ ...tdStyle, textAlign: 'right', color: colors.accentTeal }}>{formatCost(model.costEstimate)}</td>
                    <td style={{ ...tdStyle, textAlign: 'right', color: colors.textSecondary }}>{formatPercentage(model.percentageOfTotal)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
};

export default CostAnalysisTab;
