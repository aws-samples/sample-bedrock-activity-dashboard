import { useState, useEffect } from 'react';
import { colors } from '../../theme';
import { getAgentConfigs, updateAgentConfig, executeAgent, getExecutionHistory } from '../../services/v2Api';
import type { AgentConfig, AgentExecution } from '../../types/v2';

const CATEGORY_COLOR: Record<string, string> = {
  'Security & Guardrails': '#f78166',
  'Usage Intelligence': '#58a6ff',
  'Compliance & Operations': '#3fb9a0',
  'Orchestration': '#bc8cff',
};

const CATEGORY_ICON: Record<string, string> = {
  'Security & Guardrails': '🛡',
  'Usage Intelligence': '📊',
  'Compliance & Operations': '📋',
  'Orchestration': '⚙',
};

const CATEGORY_DESC: Record<string, string> = {
  'Security & Guardrails': 'Detects cost anomalies and recommends guardrail configurations.',
  'Usage Intelligence': 'Analyzes prompt quality, tracks model adoption, and forecasts usage.',
  'Compliance & Operations': 'Audits data residency, monitors latency, generates chargeback reports.',
  'Orchestration': 'Checks policy compliance and compiles weekly digest summaries.',
};

const CATEGORY_ORDER = ['Security & Guardrails', 'Usage Intelligence', 'Compliance & Operations', 'Orchestration'];

function explainCron(cron: string | null): string {
  if (!cron) return 'Manual only';
  if (cron.match(/^0 \*\/(\d+) \* \* \*$/)) return `Every ${cron.match(/^0 \*\/(\d+)/)?.[1]}h`;
  if (cron.match(/^0 \d+ \* \* \*$/)) return `Daily ${cron.match(/^0 (\d+)/)?.[1]}:00`;
  if (cron.match(/^0 \d+ \* \* 1$/)) return `Mon ${cron.match(/^0 (\d+)/)?.[1]}:00`;
  if (cron.match(/^0 \d+ 1 \* \*$/)) return `1st ${cron.match(/^0 (\d+)/)?.[1]}:00`;
  return cron;
}

/** `isAdmin` is cosmetic: the API rejects non-admin execute/config calls with 403. */
export default function AgentManagement({ isAdmin = false }: { isAdmin?: boolean }) {
  const [configs, setConfigs] = useState<AgentConfig[]>([]);
  const [lastExecs, setLastExecs] = useState<Record<string, AgentExecution | null>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [triggering, setTriggering] = useState<string | null>(null);
  const [execResult, setExecResult] = useState<Record<string, { status: string; count: number; time: string } | null>>({});

  const fetchData = async () => {
    try {
      setLoading(true);
      const cfgs = await getAgentConfigs();
      setConfigs(cfgs);
      const execs: Record<string, AgentExecution | null> = {};
      await Promise.allSettled(cfgs.map(async (c) => {
        try { const h = await getExecutionHistory(c.agent_name, 1); execs[c.agent_name] = h[0] ?? null; } catch { execs[c.agent_name] = null; }
      }));
      setLastExecs(execs);
      setError('');
    } catch { setError('Failed to load agent configurations'); }
    finally { setLoading(false); }
  };

  useEffect(() => { fetchData(); }, []);

  const handleToggle = async (agent: AgentConfig) => {
    try {
      await updateAgentConfig(agent.agent_name, { enabled: !agent.enabled });
      setConfigs((prev) => prev.map((c) => (c.agent_name === agent.agent_name ? { ...c, enabled: !c.enabled } : c)));
    } catch { /* silent */ }
  };

  const handleTrigger = async (agentName: string) => {
    setTriggering(agentName);
    setExecResult((prev) => ({ ...prev, [agentName]: null }));
    try {
      const result = await executeAgent(agentName);
      const status = result.status ?? (result.error ? 'failure' : 'success');
      setExecResult((prev) => ({ ...prev, [agentName]: { status, count: result.finding_count ?? 0, time: new Date().toLocaleTimeString() } }));
      const history = await getExecutionHistory(agentName, 1);
      setLastExecs((prev) => ({ ...prev, [agentName]: history[0] ?? null }));
    } catch {
      setExecResult((prev) => ({ ...prev, [agentName]: { status: 'failure', count: 0, time: new Date().toLocaleTimeString() } }));
    } finally { setTriggering(null); }
  };

  if (loading) return <div style={{ color: colors.textMuted, fontSize: '0.8125rem', padding: '2rem 0' }}>Loading agents…</div>;
  if (error) return <div style={{ color: '#f78166', fontSize: '0.8125rem' }}>⚠ {error}</div>;

  return (
    <div>

      {/* Info banner */}
      <div style={{ marginBottom: '1.25rem', padding: '1rem 1.25rem', background: 'rgba(88,166,255,0.04)', border: '1px solid rgba(88,166,255,0.12)', borderRadius: 10 }}>
        <div style={{ fontSize: '0.8125rem', color: colors.textSecondary, lineHeight: 1.6 }}>
          Each agent runs automatically on its configured schedule. You can also trigger any agent manually using the Run button. Agents marked with 🤖 use LLM reasoning; agents marked with ⚙️ execute deterministic tool functions directly.
        </div>
      </div>

      {/* Category cards */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: '1.25rem' }}>
        {CATEGORY_ORDER.map((category) => {
          const categoryAgents = configs.filter((c) => c.category === category);
          if (categoryAgents.length === 0) return null;
          const catColor = CATEGORY_COLOR[category] ?? colors.textSecondary;
          const catIcon = CATEGORY_ICON[category] ?? '◈';
          const catDesc = CATEGORY_DESC[category] ?? '';

          return (
            <div
              key={category}
              style={{
                background: 'rgba(255,255,255,0.03)',
                border: '1px solid rgba(255,255,255,0.06)',
                borderLeft: `4px solid ${catColor}`,
                borderRadius: '0 10px 10px 0',
                padding: '1.25rem',
              }}
            >
              {/* Category header */}
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.25rem' }}>
                <span style={{ fontSize: '1.125rem' }}>{catIcon}</span>
                <span style={{ fontWeight: 700, fontSize: '0.9375rem', color: colors.textPrimary }}>{category}</span>
                <span style={{ marginLeft: 'auto', fontSize: '0.6875rem', color: colors.textMuted }}>{categoryAgents.length} agent{categoryAgents.length !== 1 ? 's' : ''}</span>
              </div>
              <div style={{ fontSize: '0.8125rem', color: colors.textMuted, lineHeight: 1.5, marginBottom: '1rem' }}>{catDesc}</div>

              {/* Agent cards within category */}
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))', gap: '0.75rem' }}>
                {categoryAgents.map((agent) => {
                  const last = lastExecs[agent.agent_name];
                  const res = execResult[agent.agent_name];
                  const isRunning = triggering === agent.agent_name;

                  return (
                    <div
                      key={agent.agent_name}
                      style={{
                        background: 'rgba(255,255,255,0.02)',
                        border: '1px solid rgba(255,255,255,0.06)',
                        borderRadius: 8,
                        padding: '0.875rem 1rem',
                        transition: 'border-color 0.15s',
                      }}
                    >
                      {/* Agent name + type icon + toggle */}
                      <div style={{ display: 'flex', alignItems: 'center', gap: '0.375rem', marginBottom: '0.5rem' }}>
                        <span title={agent.is_agentic ? 'Agentic — LLM-powered with reasoning' : 'Deterministic — direct tool execution'} style={{ cursor: 'help', fontSize: '0.875rem' }}>
                          {agent.is_agentic ? '🤖' : '⚙️'}
                        </span>
                        <span style={{ fontWeight: 600, fontSize: '0.8125rem', color: colors.textPrimary, flex: 1 }}>{agent.display_name}</span>
                        <button
                          onClick={() => handleToggle(agent)}
                          disabled={!isAdmin}
                          title={isAdmin ? undefined : 'Admin only'}
                          style={{ width: 34, height: 18, borderRadius: 9, border: 'none', cursor: 'pointer', background: agent.enabled ? '#3fb9a0' : 'rgba(255,255,255,0.12)', position: 'relative', transition: 'background 0.2s', flexShrink: 0 }}
                        >
                          <span style={{ position: 'absolute', top: 2, left: agent.enabled ? 17 : 2, width: 14, height: 14, borderRadius: '50%', background: '#fff', transition: 'left 0.2s' }} />
                        </button>
                      </div>

                      {/* Schedule + last execution */}
                      <div style={{ display: 'flex', gap: '1rem', fontSize: '0.6875rem', color: colors.textMuted, marginBottom: '0.625rem' }}>
                        <span title={agent.schedule_cron ?? 'No schedule'}>⏱ {explainCron(agent.schedule_cron)}</span>
                        {last && (
                          <span>
                            Last: {new Date(last.start_time).toLocaleDateString()}{' '}
                            <span style={{ color: last.status === 'success' ? '#3fb9a0' : '#f78166', fontWeight: 600 }}>
                              {last.status}
                            </span>
                          </span>
                        )}
                      </div>

                      {/* Run button + result */}
                      <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                        {isAdmin && (
                        <button
                          onClick={() => handleTrigger(agent.agent_name)}
                          disabled={isRunning}
                          style={{
                            padding: '0.3rem 0.75rem',
                            border: `1px solid ${isRunning ? 'rgba(227,179,65,0.4)' : `${catColor}44`}`,
                            borderRadius: 6,
                            background: isRunning ? 'rgba(227,179,65,0.08)' : `${catColor}10`,
                            color: isRunning ? '#e3b341' : catColor,
                            cursor: isRunning ? 'wait' : 'pointer',
                            fontSize: '0.6875rem',
                            fontWeight: 600,
                          }}
                        >
                          {isRunning ? '⟳ Running…' : '▶ Run'}
                        </button>
                        )}
                        {res && (
                          <span style={{
                            fontSize: '0.6875rem',
                            padding: '0.15rem 0.5rem',
                            borderRadius: 4,
                            fontWeight: 600,
                            color: res.status === 'success' ? '#3fb9a0' : '#f78166',
                            background: res.status === 'success' ? 'rgba(63,185,160,0.1)' : 'rgba(247,129,102,0.1)',
                          }}>
                            {res.status === 'success' ? `✓ ${res.count} findings` : '✕ Failed'}
                          </span>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
