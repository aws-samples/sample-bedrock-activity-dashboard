import { useState, useEffect, useCallback } from 'react';
import { colors, glass } from '../../theme';
import { generateDemoData, clearDemoData, getDemoStatus } from '../../api/demo';
import type { DemoStatus, GenerateDemoSummary } from '../../types';

const DEFAULT_COUNT = 30;
const MAX_COUNT = 100;

function formatWhen(iso: string | null): string {
  if (!iso) return 'never';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString();
}

export default function DemoData() {
  const [status, setStatus] = useState<DemoStatus | null>(null);
  const [statusLoading, setStatusLoading] = useState(true);
  const [statusError, setStatusError] = useState<string | null>(null);

  const [count, setCount] = useState(DEFAULT_COUNT);
  const [withApplications, setWithApplications] = useState(false);

  const [generating, setGenerating] = useState(false);
  const [clearing, setClearing] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [summary, setSummary] = useState<GenerateDemoSummary | null>(null);
  const [clearedCount, setClearedCount] = useState<number | null>(null);

  const fetchStatus = useCallback(async (signal?: AbortSignal) => {
    setStatusLoading(true);
    setStatusError(null);
    try {
      setStatus(await getDemoStatus(signal));
    } catch (err: unknown) {
      if (signal?.aborted) return;
      setStatusError(err instanceof Error ? err.message : 'Failed to load status');
    } finally {
      if (!signal?.aborted) setStatusLoading(false);
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void fetchStatus(controller.signal);
    return () => controller.abort();
  }, [fetchStatus]);

  const handleGenerate = async () => {
    setGenerating(true);
    setActionError(null);
    setSummary(null);
    setClearedCount(null);
    try {
      const result = await generateDemoData({ count, withApplications });
      setSummary(result);
      await fetchStatus();
    } catch (err: unknown) {
      setActionError(err instanceof Error ? err.message : 'Generation failed');
    } finally {
      setGenerating(false);
    }
  };

  const handleClear = async () => {
    setClearing(true);
    setActionError(null);
    setSummary(null);
    setClearedCount(null);
    try {
      const result = await clearDemoData();
      setClearedCount(result.deleted);
      await fetchStatus();
    } catch (err: unknown) {
      setActionError(err instanceof Error ? err.message : 'Clear failed');
    } finally {
      setClearing(false);
    }
  };

  const busy = generating || clearing;

  return (
    <div style={{ maxWidth: 720 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.75rem' }}>
        <span style={{ fontSize: '1.1rem' }}>🧪</span>
        <h2 style={{ margin: 0, fontSize: '1.05rem', fontWeight: 700, color: colors.textPrimary }}>
          Demo Data
        </h2>
      </div>

      <p style={{ fontSize: '0.8125rem', color: colors.textSecondary, lineHeight: 1.6, marginTop: 0 }}>
        Generate an on-demand batch of demo activity. Every record is a genuine Bedrock model
        invocation — a real prompt sent to a real model, with the real response text, real token
        counts, and real (small) AWS cost. Prompts embed clearly-fake sample values (standard
        test strings such as <code>123-45-6789</code> and <code>4111 1111 1111 1111</code>) so
        downstream Guardrail/Compliance tooling has realistic content to analyze. To give
        trend-oriented agents (Usage Forecaster, Cost Anomaly Alerter) a realistic multi-day
        pattern to analyze, each record's displayed timestamp is spread across the last 14 days
        rather than all landing at the moment of generation — the content, response, and cost
        stay real either way. Data persists until you clear it.
        {' '}
        Check <strong>Tag with applications</strong> to also route a portion of the batch
        through a few tagged application inference profiles (get-or-created automatically),
        so Cost Governance&apos;s Spend by Application view has real data to show instead of
        everything landing in &quot;unknown&quot;.
      </p>

      {/* Status panel */}
      <div style={{ ...glass.panel, padding: '1rem', marginBottom: '1rem' }}>
        <div style={{ fontSize: '0.6875rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em', color: colors.textMuted, marginBottom: '0.6rem' }}>
          Current Status
        </div>
        {statusLoading && <div style={{ fontSize: '0.8125rem', color: colors.textSecondary }}>Loading status…</div>}
        {statusError && !statusLoading && (
          <div style={{ fontSize: '0.8125rem', color: colors.accentOrange }}>{statusError}</div>
        )}
        {status && !statusLoading && !statusError && (
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '1.5rem' }}>
            <Stat label="Total records" value={String(status.recordCount)} />
            <Stat label="Real invocations" value={String(status.realCount)} />
            <Stat label="Last generated" value={formatWhen(status.lastGeneratedAt)} />
          </div>
        )}
      </div>

      {/* Controls */}
      <div style={{ ...glass.panel, padding: '1rem', marginBottom: '1rem' }}>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '1rem', alignItems: 'flex-end' }}>
          <NumberField
            label="Records to generate"
            value={count}
            min={0}
            max={MAX_COUNT}
            disabled={busy}
            onChange={setCount}
          />
          <label
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '0.4rem',
              fontSize: '0.8125rem',
              color: colors.textSecondary,
              cursor: busy ? 'not-allowed' : 'pointer',
              paddingBottom: '0.4rem',
            }}
          >
            <input
              type="checkbox"
              checked={withApplications}
              disabled={busy}
              onChange={(e) => setWithApplications(e.target.checked)}
            />
            Tag with applications
          </label>
          <button
            onClick={handleGenerate}
            disabled={busy}
            style={{
              padding: '0.5rem 1.1rem',
              fontSize: '0.8125rem',
              fontWeight: 600,
              background: colors.btnPrimary,
              border: 'none',
              borderRadius: 8,
              color: '#fff',
              cursor: busy ? 'wait' : 'pointer',
              opacity: busy ? 0.6 : 1,
            }}
          >
            {generating ? 'Generating…' : 'Generate Demo Data'}
          </button>
          <button
            onClick={handleClear}
            disabled={busy}
            style={{
              padding: '0.5rem 1.1rem',
              fontSize: '0.8125rem',
              fontWeight: 600,
              background: 'rgba(255,138,106,0.1)',
              border: `1px solid ${colors.accentOrange}66`,
              borderRadius: 8,
              color: colors.accentOrange,
              cursor: busy ? 'wait' : 'pointer',
              opacity: busy ? 0.6 : 1,
            }}
          >
            {clearing ? 'Clearing…' : 'Clear Demo Data'}
          </button>
        </div>
        {generating && (
          <div style={{ fontSize: '0.75rem', color: colors.textMuted, marginTop: '0.6rem' }}>
            Every record is a real Bedrock call — this may take up to a minute and will incur small real AWS charges…
          </div>
        )}
      </div>

      {/* Errors */}
      {actionError && (
        <div style={{
          padding: '0.6rem 0.85rem', marginBottom: '0.75rem', borderRadius: 8,
          background: 'rgba(248,81,73,0.08)', border: '1px solid rgba(248,81,73,0.3)',
          color: '#f85149', fontSize: '0.8125rem',
        }}>
          {actionError}
        </div>
      )}

      {/* Clear result */}
      {clearedCount !== null && (
        <div style={{
          padding: '0.6rem 0.85rem', marginBottom: '0.75rem', borderRadius: 8,
          background: 'rgba(74,224,106,0.08)', border: '1px solid rgba(74,224,106,0.3)',
          color: colors.accentTeal, fontSize: '0.8125rem',
        }}>
          Cleared {clearedCount} demo record{clearedCount === 1 ? '' : 's'}.
        </div>
      )}

      {/* Generate summary */}
      {summary && (
        <div style={{ ...glass.card, padding: '1rem' }}>
          <div style={{ fontSize: '0.6875rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em', color: colors.textMuted, marginBottom: '0.6rem' }}>
            Generation Summary
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '1.5rem', marginBottom: '0.75rem' }}>
            <Stat label="Requested" value={String(summary.requested)} />
            <Stat label="Succeeded" value={String(summary.succeeded)} accent={colors.accentTeal} />
            <Stat label="Failed" value={String(summary.failed.length)} accent={summary.failed.length ? colors.accentOrange : undefined} />
            {summary.guardrailIntervenedCount > 0 && (
              <Stat label="Guardrail intervened" value={String(summary.guardrailIntervenedCount)} accent={colors.accentOrange} />
            )}
          </div>
          <div style={{ fontSize: '0.6875rem', color: colors.textMuted, marginBottom: '0.5rem' }}>
            Batch: <code>{summary.batchId}</code>
          </div>
          {summary.applicationProfilesUsed.length > 0 && (
            <div style={{ fontSize: '0.75rem', color: colors.textSecondary, marginBottom: summary.failed.length ? '0.5rem' : 0 }}>
              Tagged applications used:{' '}
              {summary.applicationProfilesUsed.map((app, i) => (
                <code key={app} style={{ marginRight: i < summary.applicationProfilesUsed.length - 1 ? '0.35rem' : 0 }}>
                  {app}
                </code>
              ))}
            </div>
          )}
          {summary.failed.length > 0 && (
            <div style={{ marginTop: '0.5rem' }}>
              <div style={{ fontSize: '0.75rem', color: colors.accentOrange, marginBottom: '0.35rem' }}>
                Invocations skipped (not enabled / access denied / throttled):
              </div>
              <ul style={{ margin: 0, paddingLeft: '1.1rem' }}>
                {summary.failed.map((f, i) => (
                  <li key={`${f.model}-${i}`} style={{ fontSize: '0.75rem', color: colors.textSecondary, marginBottom: '0.2rem' }}>
                    <code>{f.model}</code> — {f.error}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function Stat({ label, value, accent }: { label: string; value: string; accent?: string }) {
  return (
    <div>
      <div style={{ fontSize: '1.25rem', fontWeight: 700, color: accent ?? colors.textPrimary }}>{value}</div>
      <div style={{ fontSize: '0.6875rem', color: colors.textMuted }}>{label}</div>
    </div>
  );
}

function NumberField({
  label, value, min, max, disabled, onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  disabled?: boolean;
  onChange: (v: number) => void;
}) {
  return (
    <label style={{ display: 'flex', flexDirection: 'column', gap: '0.25rem' }}>
      <span style={{ fontSize: '0.6875rem', color: colors.textMuted }}>{label}</span>
      <input
        type="number"
        min={min}
        max={max}
        value={value}
        disabled={disabled}
        onChange={(e) => {
          const n = Number(e.target.value);
          if (Number.isNaN(n)) return;
          onChange(Math.max(min, Math.min(max, Math.trunc(n))));
        }}
        style={{
          width: 90,
          padding: '0.4rem 0.5rem',
          fontSize: '0.8125rem',
          background: colors.inputBg,
          border: `1px solid ${colors.inputBorder}`,
          borderRadius: 6,
          color: colors.textPrimary,
        }}
      />
    </label>
  );
}
