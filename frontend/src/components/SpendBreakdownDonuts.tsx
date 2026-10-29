import React, { useEffect, useMemo, useState } from 'react';
import {
  PieChart,
  Pie,
  Cell,
  Tooltip,
  ResponsiveContainer,
  Sector,
} from 'recharts';
import type { PieProps } from 'recharts';
import type { DashboardFilters, CategorySpendRow, CategoryModelRow, SpendBreakdownResponse } from '../types';
import { getSpendBreakdown } from '../api/client';
import { colors, glass } from '../theme';
import { formatCost } from '../services/formatCost';
import LoadingSpinner from './LoadingSpinner';
import ErrorBanner from './ErrorBanner';

interface SpendBreakdownDonutsProps {
  filters: DashboardFilters;
}

const OTHER_LABEL = 'Other';
const OTHER_COLOR = '#4a5568';

// Base hues for the inner (category) ring — the outer (model) ring reuses
// each category's hue at a lighter/darker tint so the visual grouping reads
// correctly even before hovering (standard sunburst convention). Reuses the
// dashboard's existing accent palette (theme.ts) rather than introducing new
// colors.
const CATEGORY_HUES = [
  colors.accentBlue,
  colors.accentPurple,
  colors.accentTeal,
  colors.accentOrange,
  colors.accentYellow,
  '#ff6ad5',
];

/** Lighten or darken a hex color by `amount` in [-1, 1] (positive = lighter). */
function shadeHex(hex: string, amount: number): string {
  const normalized = hex.replace('#', '');
  if (normalized.length !== 6) return hex;
  const num = parseInt(normalized, 16);
  let r = (num >> 16) & 0xff;
  let g = (num >> 8) & 0xff;
  let b = num & 0xff;
  const blend = (channel: number) =>
    amount >= 0
      ? Math.round(channel + (255 - channel) * amount)
      : Math.round(channel * (1 + amount));
  r = Math.max(0, Math.min(255, blend(r)));
  g = Math.max(0, Math.min(255, blend(g)));
  b = Math.max(0, Math.min(255, blend(b)));
  return `#${r.toString(16).padStart(2, '0')}${g.toString(16).padStart(2, '0')}${b.toString(16).padStart(2, '0')}`;
}

interface InnerSlice {
  name: string;
  value: number;
  invocationCount: number;
  color: string;
}

interface OuterSlice {
  name: string;
  value: number;
  invocationCount: number;
  color: string;
  categoryName: string;
}

/** Flatten byCategory into the inner (category) ring's pie data. */
function buildInnerSlices(
  byCategory: CategorySpendRow[],
  otherCategoryCost: number,
  otherCategoryInvocationCount: number
): InnerSlice[] {
  const slices: InnerSlice[] = byCategory.map((row, i) => ({
    name: row.category,
    value: row.cost,
    invocationCount: row.invocationCount,
    color: CATEGORY_HUES[i % CATEGORY_HUES.length],
  }));
  // Trigger on invocation count, not cost: an overflow model/category with
  // no resolvable pricing (unknown model id -> costEstimate None -> treated
  // as $0) would otherwise vanish from the chart entirely instead of
  // appearing as an "Other" slice — see the matching comment on
  // otherModelInvocationCount below.
  if (otherCategoryInvocationCount > 0) {
    slices.push({
      name: OTHER_LABEL,
      value: otherCategoryCost,
      invocationCount: otherCategoryInvocationCount,
      color: OTHER_COLOR,
    });
  }
  return slices;
}

/**
 * Flatten byCategory[].byModel (+ each category's own "Other model" bucket)
 * into the outer (model) ring's pie data, in the SAME angular order as the
 * inner ring so each category's model arcs sit directly outside its wedge.
 * Models within a category are tinted shades of that category's hue.
 */
function buildOuterSlices(byCategory: CategorySpendRow[], innerSlices: InnerSlice[]): OuterSlice[] {
  const slices: OuterSlice[] = [];
  byCategory.forEach((row, i) => {
    const baseColor = CATEGORY_HUES[i % CATEGORY_HUES.length];
    const shadeStep = row.byModel.length > 1 ? 0.3 / (row.byModel.length - 1 || 1) : 0;
    row.byModel.forEach((m: CategoryModelRow, j: number) => {
      slices.push({
        name: m.modelId,
        value: m.cost,
        invocationCount: m.invocationCount,
        color: shadeHex(baseColor, -0.15 + j * shadeStep),
        categoryName: row.category,
      });
    });
    // Trigger on invocation count, not cost — see the matching comment in
    // buildInnerSlices. A model with unresolved pricing (costEstimate None,
    // treated as $0) would otherwise disappear from the chart instead of
    // surfacing in the "Other" slice.
    if (row.otherModelInvocationCount > 0) {
      slices.push({
        name: `${OTHER_LABEL} (${row.category})`,
        value: row.otherModelCost,
        invocationCount: row.otherModelInvocationCount,
        color: shadeHex(baseColor, 0.35),
        categoryName: row.category,
      });
    }
  });
  // The "Other category" wedge (if present) has no model breakdown — give it
  // a single matching outer arc so the two rings stay the same total angle.
  const otherInner = innerSlices.find((s) => s.name === OTHER_LABEL);
  if (otherInner) {
    slices.push({
      name: OTHER_LABEL,
      value: otherInner.value,
      invocationCount: otherInner.invocationCount,
      color: OTHER_COLOR,
      categoryName: OTHER_LABEL,
    });
  }
  return slices;
}

const chartTooltipStyle = {
  background: 'rgba(13,17,23,0.95)',
  border: `1px solid ${colors.cardBorder}`,
  borderRadius: '8px',
  fontSize: '0.8125rem',
};

/** Active-shape renderer: pops the hovered slice out slightly with a glow. */
function renderActiveShape(props: unknown): React.ReactElement {
  const p = props as {
    cx: number; cy: number; innerRadius: number; outerRadius: number;
    startAngle: number; endAngle: number; fill: string;
  };
  return (
    <g>
      <Sector
        cx={p.cx}
        cy={p.cy}
        innerRadius={p.innerRadius}
        outerRadius={p.outerRadius + 6}
        startAngle={p.startAngle}
        endAngle={p.endAngle}
        fill={p.fill}
        style={{ filter: `drop-shadow(0 0 8px ${p.fill}aa)` }}
      />
    </g>
  );
}

function totalOf(slices: { value: number }[]): number {
  return slices.reduce((sum, s) => sum + s.value, 0);
}

/** Donut center label: total spend + a caption, overlaid via absolute positioning. */
function CenterLabel({ total, caption }: { total: number; caption: string }): React.ReactElement {
  return (
    <div
      style={{
        position: 'absolute',
        top: '50%',
        left: '50%',
        transform: 'translate(-50%, -50%)',
        textAlign: 'center',
        pointerEvents: 'none',
      }}
    >
      <div style={{ fontSize: '1.25rem', fontWeight: 700, color: colors.textPrimary }}>
        {formatCost(total, 2)}
      </div>
      <div style={{ fontSize: '0.625rem', color: colors.textMuted, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
        {caption}
      </div>
    </div>
  );
}

function Legend({
  items,
  total,
  twoColumn,
}: {
  items: { name: string; value: number; invocationCount: number; color: string; sub?: string }[];
  total: number;
  twoColumn?: boolean;
}): React.ReactElement {
  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: '0.4rem',
        maxHeight: 220,
        overflowY: 'auto',
        paddingRight: '0.25rem',
      }}
    >
      {items.map((item) => {
        const pct = total > 0 ? (item.value / total) * 100 : 0;
        return (
          <div key={`${item.name}-${item.sub ?? ''}`} style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', fontSize: '0.75rem' }}>
            <span style={{ width: 9, height: 9, borderRadius: '50%', background: item.color, flexShrink: 0 }} />
            <span
              style={{
                color: colors.textSecondary,
                flex: twoColumn ? undefined : 1,
                maxWidth: twoColumn ? 140 : undefined,
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
              }}
              title={item.sub ? `${item.name} (${item.sub})` : item.name}
            >
              {item.name}
            </span>
            <span style={{ color: colors.textPrimary, fontWeight: 600, flexShrink: 0 }}>
              {formatCost(item.value, 2)}
            </span>
            <span style={{ color: colors.textMuted, flexShrink: 0, minWidth: 36, textAlign: 'right' }}>
              {pct.toFixed(1)}%
            </span>
          </div>
        );
      })}
    </div>
  );
}

const emptyStyle: React.CSSProperties = { color: colors.textMuted, fontSize: '0.875rem', margin: 0 };

const SpendBreakdownDonuts: React.FC<SpendBreakdownDonutsProps> = ({ filters }) => {
  const [data, setData] = useState<SpendBreakdownResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeCategoryIndex, setActiveCategoryIndex] = useState<number | undefined>(undefined);

  useEffect(() => {
    const controller = new AbortController();
    let cancelled = false;

    async function load(): Promise<void> {
      setLoading(true);
      setError(null);
      try {
        const result = await getSpendBreakdown(filters);
        if (!cancelled) setData(result);
      } catch (err: unknown) {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Failed to load spend breakdown');
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    void load();
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [filters]);

  const innerSlices = useMemo(
    () => (data ? buildInnerSlices(data.byCategory, data.otherCategoryCost, data.otherCategoryInvocationCount) : []),
    [data]
  );
  const outerSlices = useMemo(
    () => (data ? buildOuterSlices(data.byCategory, innerSlices) : []),
    [data, innerSlices]
  );

  const categoryTotal = totalOf(innerSlices);

  if (loading) return <LoadingSpinner message="Loading spend breakdown..." />;
  if (error) return <ErrorBanner message={error} onRetry={() => setData(null)} />;
  if (!data) return null;

  const onCategoryEnter: NonNullable<PieProps['onMouseEnter']> = (_entry, index) => setActiveCategoryIndex(index);
  const onCategoryLeave = () => setActiveCategoryIndex(undefined);

  return (
    <div style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap', marginBottom: '1.75rem' }}>
      {/* Chart 1: Category -> Model nested donut */}
      <div style={{ ...glass.card, flex: '1 1 420px', padding: '1.25rem' }}>
        <div style={{ fontSize: '0.8125rem', fontWeight: 600, color: colors.textSecondary, marginBottom: '0.25rem' }}>
          Spend by Category &amp; Model
        </div>
        <div style={{ fontSize: '0.6875rem', color: colors.textMuted, marginBottom: '0.75rem' }}>
          Inner ring: task category &middot; Outer ring: models used within it
        </div>
        {innerSlices.length === 0 ? (
          <p style={emptyStyle}>No categorized activity in this range.</p>
        ) : (
          <div style={{ display: 'flex', gap: '1rem', alignItems: 'center', flexWrap: 'wrap' }}>
            <div style={{ position: 'relative', flex: '0 0 220px', height: 220 }}>
              <ResponsiveContainer width="100%" height="100%">
                <PieChart>
                  <Pie
                    data={innerSlices}
                    dataKey="value"
                    nameKey="name"
                    cx="50%"
                    cy="50%"
                    innerRadius="38%"
                    outerRadius="62%"
                    paddingAngle={1.5}
                    activeIndex={activeCategoryIndex}
                    activeShape={renderActiveShape}
                    onMouseEnter={onCategoryEnter}
                    onMouseLeave={onCategoryLeave}
                    isAnimationActive
                  >
                    {innerSlices.map((s, i) => (
                      <Cell key={`${s.name}-${i}`} fill={s.color} stroke="rgba(0,0,0,0.2)" />
                    ))}
                  </Pie>
                  <Pie
                    data={outerSlices}
                    dataKey="value"
                    nameKey="name"
                    cx="50%"
                    cy="50%"
                    innerRadius="68%"
                    outerRadius="92%"
                    paddingAngle={1}
                    isAnimationActive
                  >
                    {outerSlices.map((s, i) => (
                      <Cell key={`${s.name}-${s.categoryName}-${i}`} fill={s.color} stroke="rgba(0,0,0,0.25)" strokeWidth={0.5} />
                    ))}
                  </Pie>
                  <Tooltip
                    contentStyle={chartTooltipStyle}
                    itemStyle={{ color: colors.textPrimary }}
                    formatter={(value: number, name: string) => [formatCost(value, 2), name]}
                  />
                </PieChart>
              </ResponsiveContainer>
              <CenterLabel total={categoryTotal} caption="Total Spend" />
            </div>
            <div style={{ display: 'flex', gap: '1.25rem', flex: '1 1 160px' }}>
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: '0.625rem', color: colors.textMuted, textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '0.4rem' }}>
                  Category
                </div>
                <Legend items={innerSlices} total={categoryTotal} twoColumn />
              </div>
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: '0.625rem', color: colors.textMuted, textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '0.4rem' }}>
                  Model
                </div>
                <Legend
                  items={outerSlices.map((s) => ({ ...s, sub: s.categoryName }))}
                  total={categoryTotal}
                  twoColumn
                />
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

export default SpendBreakdownDonuts;
