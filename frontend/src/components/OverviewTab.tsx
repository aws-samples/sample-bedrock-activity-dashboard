import React, { useEffect, useState } from 'react';
import type { DashboardFilters, OverviewSummary } from '../types';
import { getOverview } from '../api/client';
import { colors, metricColors } from '../theme';
import LoadingSpinner from './LoadingSpinner';
import ErrorBanner from './ErrorBanner';
import ActiveUsersPanel from './ActiveUsersPanel';
import SpendBreakdownDonuts from './SpendBreakdownDonuts';
import { formatCost } from '../services/formatCost';

interface OverviewTabProps {
  filters: DashboardFilters;
  onNavigate?: (tab: 'token-usage' | 'model-usage' | 'cost-analysis' | 'prompt-history') => void;
}

function formatNumber(n: number): string { return n.toLocaleString(); }

// Map each metric card to a navigation target (null = show users panel)
type NavTarget = 'token-usage' | 'model-usage' | 'cost-analysis' | 'prompt-history' | 'users' | null;

const metrics: { key: string; label: string; icon: string; navTarget: NavTarget }[] = [
  { key: 'totalInvocations', label: 'Total Invocations', icon: '⬡', navTarget: 'prompt-history' },
  { key: 'totalTokens', label: 'Total Tokens', icon: '◎', navTarget: 'token-usage' },
  { key: 'totalCostEstimate', label: 'Total Cost', icon: '◇', navTarget: 'cost-analysis' },
  { key: 'activeUsers', label: 'Users', icon: '⊞', navTarget: 'users' },
  { key: 'activeModels', label: 'Active Models', icon: '◈', navTarget: 'model-usage' },
] as const;

const OverviewTab: React.FC<OverviewTabProps> = ({ filters, onNavigate }) => {
  const [overview, setOverview] = useState<OverviewSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [hoveredCard, setHoveredCard] = useState<number | null>(null);
  const [showUsersPanel, setShowUsersPanel] = useState(false);

  const fetchData = async () => {
    setLoading(true);
    setError(null);
    try {
      const overviewData = await getOverview(filters);
      setOverview(overviewData);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to load overview data');
    } finally {
      setLoading(false);
    }
  };

  // eslint-disable-next-line react-hooks/exhaustive-deps -- fetchData is recreated each render; effect must re-run only when filters change (adding it would loop)
  useEffect(() => { fetchData(); }, [filters]);

  if (loading) return <LoadingSpinner message="Loading overview..." />;
  if (error) return <ErrorBanner message={error} onRetry={fetchData} />;
  if (!overview) return null;

  const handleCardClick = (navTarget: NavTarget) => {
    if (navTarget === 'users') {
      setShowUsersPanel(true);
    } else if (navTarget && onNavigate) {
      onNavigate(navTarget);
    }
  };

  const formatValue = (key: string, value: number | null) => {
    if (key === 'totalCostEstimate') return formatCost(value);
    return formatNumber(value ?? 0);
  };

  return (
    <div data-testid="tab-overview">
      {/* Info banner */}
      <div style={{ marginBottom: '1.25rem', padding: '1rem 1.25rem', background: 'rgba(88,166,255,0.04)', border: '1px solid rgba(88,166,255,0.12)', borderRadius: 10 }}>
        <div style={{ fontSize: '0.8125rem', color: colors.textSecondary, lineHeight: 1.6 }}>
          High-level summary of your Bedrock activity. Click any metric card to drill into the corresponding tab.
        </div>
      </div>

      {/* Refresh button */}
      <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: '0.75rem' }}>
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
      </div>

      {showUsersPanel && (
        <ActiveUsersPanel filters={filters} onClose={() => setShowUsersPanel(false)} />
      )}

      {/* Metric cards */}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.875rem', marginBottom: '1.75rem' }}>
        {metrics.map((m, i) => {
          const mc = metricColors[i % metricColors.length];
          const isHovered = hoveredCard === i;
          const isClickable = !!m.navTarget;
          const value = overview[m.key as keyof OverviewSummary] as number | null;
          return (
            <div
              key={m.key}
              onClick={() => handleCardClick(m.navTarget)}
              onMouseEnter={() => setHoveredCard(i)}
              onMouseLeave={() => setHoveredCard(null)}
              style={{
                background: 'rgba(255,255,255,0.03)',
                border: '1px solid rgba(255,255,255,0.06)',
                borderLeft: `4px solid ${mc.accent}`,
                borderRadius: '0 10px 10px 0',
                flex: '1 1 180px',
                minWidth: '160px',
                padding: '1.25rem 1.5rem',
                position: 'relative',
                overflow: 'hidden',
                transition: 'transform 0.2s ease, box-shadow 0.2s ease',
                transform: isHovered ? 'translateY(-2px)' : 'none',
                boxShadow: isHovered
                  ? `0 8px 32px rgba(0,0,0,0.5), 0 0 0 1px ${mc.accent}40, 0 0 20px ${mc.glow}`
                  : '0 4px 24px rgba(0,0,0,0.4)',
                cursor: isClickable ? 'pointer' : 'default',
              }}
            >
              {/* Left accent border is handled by borderLeft */}
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                <div style={{ fontSize: '1.25rem', marginBottom: '0.5rem', opacity: 0.7 }}>{m.icon}</div>
                {isClickable && (
                  <div style={{ fontSize: '0.625rem', color: mc.accent, opacity: isHovered ? 1 : 0, transition: 'opacity 0.2s', letterSpacing: '0.05em' }}>
                    VIEW →
                  </div>
                )}
              </div>
              <div style={{ fontSize: '0.6875rem', color: colors.textMuted, fontWeight: 600, letterSpacing: '0.05em', textTransform: 'uppercase', marginBottom: '0.375rem' }}>
                {m.label}
              </div>
              <div style={{ fontSize: '1.75rem', fontWeight: 700, color: colors.textPrimary, lineHeight: 1 }}>
                {formatValue(m.key, value)}
              </div>
            </div>
          );
        })}
      </div>

      {/* Trends: spend by category/model */}
      <SpendBreakdownDonuts filters={filters} />
    </div>
  );
};

export default OverviewTab;
