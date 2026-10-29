import { useState, useEffect, useCallback } from 'react';
import { getSystemHealth } from '../../api/client';
import type { SystemHealth as SystemHealthData, SystemCheck } from '../../api/client';

/* ── status config ── */
const CFG: Record<string, { bg: string; border: string; color: string; icon: string }> = {
  healthy:   { bg: 'rgba(46,160,67,0.08)', border: 'rgba(46,160,67,0.35)', color: '#2ea043', icon: '✔' },
  warning:   { bg: 'rgba(227,179,65,0.08)', border: 'rgba(227,179,65,0.35)', color: '#e3b341', icon: '!' },
  unhealthy: { bg: 'rgba(248,81,73,0.08)', border: 'rgba(248,81,73,0.35)', color: '#f85149', icon: '✘' },
  degraded:  { bg: 'rgba(227,179,65,0.08)', border: 'rgba(227,179,65,0.35)', color: '#e3b341', icon: '!' },
};

/* ── hide sensitive details ── */
function safeDetail(name: string, status: string): string {
  if (['AWS Credentials', 'S3 Log Bucket', 'PostgreSQL'].includes(name)) {
    return status === 'healthy' ? 'Connected' : status === 'warning' ? 'Needs attention' : 'Unavailable';
  }
  return '';
}

/* ── big status icon (SVG circle + icon) ── */
function BigIcon({ status, size = 48 }: { status: string; size?: number }) {
  const c = CFG[status] ?? CFG.unhealthy;
  return (
    <svg width={size} height={size} viewBox="0 0 48 48">
      <circle cx="24" cy="24" r="22" fill={c.bg} stroke={c.border} strokeWidth="2" />
      <text x="24" y="26" textAnchor="middle" dominantBaseline="central"
        fill={c.color} fontSize={status === 'healthy' ? '22' : '24'} fontWeight="bold"
        fontFamily="system-ui, sans-serif">
        {c.icon}
      </text>
    </svg>
  );
}

/* ── system row ── */
function Row({ check }: { check: SystemCheck }) {
  const c = CFG[check.status] ?? CFG.unhealthy;
  const detail = safeDetail(check.name, check.status);
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: '0.85rem',
      padding: '0.6rem 0.75rem', borderRadius: '10px',
      background: c.bg, border: `1px solid ${c.border}`,
    }}>
      <BigIcon status={check.status} size={40} />
      <div>
        <div style={{ fontWeight: 600, fontSize: '0.8125rem', color: '#e6edf3' }}>{check.name}</div>
        {detail && <div style={{ fontSize: '0.6875rem', color: 'rgba(230,237,243,0.5)', marginTop: '0.1rem' }}>{detail}</div>}
      </div>
    </div>
  );
}

/* ── main component ── */
export default function SystemHealth() {
  const [health, setHealth] = useState<SystemHealthData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchHealth = useCallback(async () => {
    setLoading(true);
    setError(null);
    try { setHealth(await getSystemHealth()); }
    catch (err: unknown) { setError(err instanceof Error ? err.message : 'Failed to load'); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { fetchHealth(); }, [fetchHealth]);
  useEffect(() => { const id = setInterval(fetchHealth, 30000); return () => clearInterval(id); }, [fetchHealth]);

  const overall = health?.overall ?? 'healthy';
  const oc = CFG[overall] ?? CFG.unhealthy;
  const healthyCount = health?.systems.filter(s => s.status === 'healthy').length ?? 0;
  const totalCount = health?.systems.length ?? 0;

  return (
    <div style={{ maxWidth: 480 }}>
      {/* header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '1rem' }}>
        <h2 style={{ margin: 0, fontSize: '1rem', fontWeight: 700, color: '#e6edf3' }}>System Health</h2>
        <button onClick={fetchHealth} disabled={loading} style={{
          padding: '0.3rem 0.7rem', fontSize: '0.6875rem', fontWeight: 600,
          background: 'rgba(88,166,255,0.08)', border: '1px solid rgba(88,166,255,0.25)',
          borderRadius: '6px', color: '#58a6ff', cursor: loading ? 'wait' : 'pointer',
          opacity: loading ? 0.5 : 1,
        }}>
          {loading ? 'Checking…' : '↻ Refresh'}
        </button>
      </div>

      {error && (
        <div style={{
          padding: '0.6rem 0.85rem', marginBottom: '0.75rem', borderRadius: '8px',
          background: 'rgba(248,81,73,0.08)', border: '1px solid rgba(248,81,73,0.25)',
          color: '#f85149', fontSize: '0.75rem',
        }}>{error}</div>
      )}

      {health && (
        <>
          {/* overall banner */}
          <div style={{
            display: 'flex', alignItems: 'center', gap: '0.85rem',
            padding: '0.85rem 1rem', marginBottom: '0.75rem', borderRadius: '12px',
            background: oc.bg, border: `1px solid ${oc.border}`,
          }}>
            <BigIcon status={overall} size={52} />
            <div>
              <div style={{ fontWeight: 700, fontSize: '0.875rem', color: oc.color }}>
                {overall === 'healthy' ? 'All Systems Operational' : overall === 'degraded' ? 'Partial Degradation' : 'Systems Down'}
              </div>
              <div style={{ fontSize: '0.6875rem', color: 'rgba(230,237,243,0.45)', marginTop: '0.15rem' }}>
                {healthyCount}/{totalCount} healthy · {health.region} · {new Date(health.timestamp).toLocaleTimeString()}
              </div>
            </div>
          </div>

          {/* system rows */}
          <div style={{ display: 'grid', gap: '0.4rem' }}>
            {health.systems.map(check => <Row key={check.name} check={check} />)}
          </div>
        </>
      )}
    </div>
  );
}
