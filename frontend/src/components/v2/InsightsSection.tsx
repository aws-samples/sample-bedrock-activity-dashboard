import { useState, useEffect, useRef, useCallback } from 'react';
import { colors } from '../../theme';
import { getFindingsSummary } from '../../services/v2Api';
import FindingsTable from './FindingsTable';

interface CategoryDef {
  title: string;
  icon: string;
  color: string;
  description: string;
  agents: string[];
}

const CATEGORIES: CategoryDef[] = [
  {
    title: 'Security & Guardrails',
    icon: '🛡',
    color: '#f78166',
    description: 'Detects cost anomalies and recommends guardrail configurations to protect your Bedrock usage.',
    agents: ['cost-anomaly-alerter', 'guardrail-recommender', 'model-cost-optimizer'],
  },
  {
    title: 'Usage Intelligence',
    icon: '📊',
    color: '#58a6ff',
    description: 'Analyzes prompt quality, tracks model adoption trends, and forecasts future token consumption.',
    agents: ['prompt-quality-analyzer', 'model-adoption-tracker', 'usage-forecaster'],
  },
  {
    title: 'Compliance & Operations',
    icon: '📋',
    color: '#3fb9a0',
    description: 'Audits data residency, monitors latency/errors, generates chargeback reports, and onboards new users.',
    agents: ['data-residency-auditor', 'latency-error-monitor', 'chargeback-report-generator', 'onboarding-assistant', 'cur-ingestion'],
  },
  {
    title: 'Orchestration',
    icon: '⚙',
    color: '#bc8cff',
    description: 'Checks policy compliance across invocations and compiles weekly digest summaries from all agents.',
    agents: ['policy-compliance-checker', 'weekly-digest-orchestrator'],
  },
];

const AGENT_DESCRIPTIONS: Record<string, string> = {
  'cost-anomaly-alerter': 'Flags daily spend spikes exceeding 2x the rolling 7-day average',
  'guardrail-recommender': 'Generates Bedrock Guardrail configs from caller-supplied or legacy PII type inputs',
  'model-cost-optimizer': 'Identifies expensive models used for simple tasks and recommends cheaper alternatives',
  'prompt-quality-analyzer': 'Detects anti-patterns like vague instructions and missing context in prompts',
  'model-adoption-tracker': 'Compares available models on capabilities and cost for migration planning',
  'usage-forecaster': 'Projects 30/60/90-day token consumption and cost trends',
  'data-residency-auditor': 'Checks invocation regions against approved data residency policies',
  'latency-error-monitor': 'Computes p95 latency and error rates per model and region',
  'chargeback-report-generator': 'Calculates per-team Bedrock costs for monthly chargeback',
  'onboarding-assistant': 'Detects new Bedrock users and generates personalized welcome guidance',
  'policy-compliance-checker': 'Evaluates invocations against organizational model and token policies',
  'weekly-digest-orchestrator': 'Compiles findings from all agents into a weekly summary digest',
  'cur-ingestion': 'Ingests authoritative CUR 2.0 billed costs via Athena into the billed-cost store',
};

export default function InsightsSection({ isAdmin = false }: { isAdmin?: boolean }) {
  const [summary, setSummary] = useState<Record<string, number>>({});
  const [offline, setOffline] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [selectedAgent, setSelectedAgent] = useState<string | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval>>();

  const fetchSummary = useCallback(async () => {
    try {
      const data = await getFindingsSummary();
      setSummary(data);
      setOffline(false);
      setLastUpdated(new Date());
    } catch {
      setOffline(true);
    }
  }, []);

  useEffect(() => {
    fetchSummary();
    timerRef.current = setInterval(fetchSummary, 60_000);
    return () => clearInterval(timerRef.current);
  }, [fetchSummary]);

  const agentLabel = (name: string) => name.replace(/[-_]/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
  const totalFindings = Object.values(summary).reduce((a, b) => a + b, 0);

  return (
    <div>
      {offline && (
        <div style={{ background: 'rgba(247,129,102,0.08)', border: '1px solid rgba(247,129,102,0.25)', borderRadius: 8, padding: '0.625rem 1rem', marginBottom: '1rem', fontSize: '0.8125rem', color: '#f78166', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <span>⚠</span> Agents offline — showing cached data
          {lastUpdated && <span style={{ marginLeft: 'auto', fontSize: '0.6875rem', color: colors.textMuted }}>Last updated {lastUpdated.toLocaleTimeString()}</span>}
        </div>
      )}

      {/* Summary header */}
      <div style={{ marginBottom: '1.25rem', padding: '1rem 1.25rem', background: 'rgba(88,166,255,0.04)', border: '1px solid rgba(88,166,255,0.12)', borderRadius: 10 }}>
        <div style={{ fontSize: '0.8125rem', color: colors.textSecondary, lineHeight: 1.6 }}>
          Each agent scans your Bedrock activity data independently and produces findings. Click any agent card below to view its detailed findings.
        </div>
        <div style={{ display: 'flex', gap: '1.5rem', marginTop: '0.75rem' }}>
          <div><span style={{ fontWeight: 700, fontSize: '1.25rem', color: colors.textPrimary }}>{totalFindings}</span> <span style={{ fontSize: '0.75rem', color: colors.textMuted }}>total findings</span></div>
          <div><span style={{ fontWeight: 700, fontSize: '1.25rem', color: colors.textPrimary }}>{Object.keys(summary).length}</span> <span style={{ fontSize: '0.75rem', color: colors.textMuted }}>active agents</span></div>
        </div>
      </div>

      {/* Category cards — reference design: colored left border, icon + title, description, agent list */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(380px, 1fr))', gap: '1rem' }}>
        {CATEGORIES.map((cat) => {
          const catFindings = cat.agents.reduce((sum, a) => sum + (summary[a] ?? 0), 0);
          return (
            <div
              key={cat.title}
              style={{
                background: 'rgba(255,255,255,0.03)',
                border: '1px solid rgba(255,255,255,0.06)',
                borderLeft: `4px solid ${cat.color}`,
                borderRadius: '0 10px 10px 0',
                padding: '1.25rem',
                transition: 'box-shadow 0.2s',
              }}
            >
              {/* Category header */}
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.375rem' }}>
                <span style={{ fontSize: '1.125rem' }}>{cat.icon}</span>
                <span style={{ fontWeight: 700, fontSize: '0.9375rem', color: colors.textPrimary }}>{cat.title}</span>
                {catFindings > 0 && (
                  <span style={{ marginLeft: 'auto', fontSize: '0.6875rem', fontWeight: 600, color: cat.color, background: `${cat.color}15`, padding: '0.15rem 0.5rem', borderRadius: 10 }}>
                    {catFindings} finding{catFindings !== 1 ? 's' : ''}
                  </span>
                )}
              </div>
              <div style={{ fontSize: '0.8125rem', color: colors.textMuted, lineHeight: 1.5, marginBottom: '0.875rem' }}>
                {cat.description}
              </div>

              {/* Agent list */}
              <div style={{ display: 'flex', flexDirection: 'column', gap: '0.375rem' }}>
                {cat.agents.map((agent) => {
                  const count = summary[agent] ?? 0;
                  const isSelected = selectedAgent === agent;
                  return (
                    <button
                      key={agent}
                      onClick={() => setSelectedAgent(isSelected ? null : agent)}
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: '0.625rem',
                        width: '100%',
                        background: isSelected ? `${cat.color}12` : 'rgba(255,255,255,0.02)',
                        border: isSelected ? `1px solid ${cat.color}44` : '1px solid rgba(255,255,255,0.04)',
                        borderRadius: 8,
                        padding: '0.625rem 0.75rem',
                        cursor: 'pointer',
                        transition: 'all 0.15s',
                        textAlign: 'left',
                      }}
                    >
                      {/* Status dot */}
                      <span style={{ width: 8, height: 8, borderRadius: '50%', background: offline ? '#f78166' : count > 0 ? cat.color : 'rgba(255,255,255,0.15)', flexShrink: 0 }} />
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ fontSize: '0.8125rem', fontWeight: 600, color: isSelected ? cat.color : colors.textSecondary }}>{agentLabel(agent)}</div>
                        <div style={{ fontSize: '0.6875rem', color: colors.textMuted, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {AGENT_DESCRIPTIONS[agent] ?? ''}
                        </div>
                      </div>
                      <span style={{ fontSize: '0.8125rem', fontWeight: 700, color: count > 0 ? cat.color : 'rgba(255,255,255,0.2)', minWidth: 24, textAlign: 'right' }}>
                        {count}
                      </span>
                    </button>
                  );
                })}
              </div>
            </div>
          );
        })}
      </div>

      {/* Expanded findings table */}
      {selectedAgent && (
        <div style={{ marginTop: '1.5rem' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.75rem' }}>
            <h3 style={{ margin: 0, fontSize: '0.9375rem', color: colors.textPrimary }}>{agentLabel(selectedAgent)}</h3>
            <span style={{ fontSize: '0.75rem', color: colors.textMuted }}>— Findings</span>
            <button onClick={() => setSelectedAgent(null)} style={{ marginLeft: 'auto', background: 'none', border: 'none', color: colors.textMuted, cursor: 'pointer', fontSize: '0.8125rem' }}>✕ Close</button>
          </div>
          <FindingsTable agentName={selectedAgent} isAdmin={isAdmin} />
        </div>
      )}
    </div>
  );
}
