import { useState } from 'react';
import { colors, glass } from '../../theme';

interface FAQItem {
  q: string;
  a: string;
}

interface FAQCategory {
  title: string;
  icon: string;
  items: FAQItem[];
}

const FAQ_DATA: FAQCategory[] = [
  {
    title: 'Getting Started',
    icon: '🚀',
    items: [
      {
        q: 'What is the Bedrock Activity Dashboard?',
        a: 'A centralized monitoring platform for AWS Bedrock usage across your organization. It tracks token consumption, model usage, costs, and provides AI-powered insights through 15 specialized agents.',
      },
      {
        q: 'How do I log in for the first time?',
        a: 'Authentication is handled via AWS Cognito. Click "Sign in" on the login page and use your organization credentials. Your role (admin, payer, superuser, user) determines which features you can access.',
      },
      {
        q: 'What are the different user roles?',
        a: 'Admin: full access including agent management. Payer: cost and billing views. Superuser: all analytics plus configuration. User: personal usage data only.',
      },
    ],
  },
  {
    title: 'Analytics',
    icon: '📊',
    items: [
      {
        q: 'How is token usage calculated?',
        a: 'Token usage is aggregated from CloudTrail events for all Bedrock InvokeModel calls. Input and output tokens are tracked separately per model, user, and team.',
      },
      {
        q: 'What does the Cost Analysis tab show?',
        a: 'Daily and cumulative cost breakdowns by model, team, and user. Costs are computed using the pricing configuration you set in the Pricing tab.',
      },
      {
        q: 'How often is data refreshed?',
        a: 'CloudTrail data is ingested every 5 minutes. Dashboard analytics refresh on page load. The Insights section auto-refreshes every 60 seconds.',
      },
    ],
  },
  {
    title: 'AI Agents',
    icon: '✦',
    items: [
      {
        q: 'What are AI agents and how do they work?',
        a: 'AI agents are specialized Strands Agents deployed on Bedrock AgentCore Runtime. Each agent analyzes specific aspects of your Bedrock usage (security, cost, compliance) and produces structured findings.',
      },
      {
        q: 'How do I trigger an agent manually?',
        a: 'Go to the Agent Management panel, find the agent you want to run, and click the "Run" button. Manual triggers work even if the agent\'s scheduled execution is disabled.',
      },
      {
        q: 'What happens when agents are offline?',
        a: 'The dashboard gracefully degrades — it shows cached findings from the database with an "agents offline" banner. All v1 analytics continue to work normally.',
      },
      {
        q: 'How do I approve or reject a finding?',
        a: 'Findings with "pending_review" status show a Review button. Click it to open the approval dialog where you can approve or reject (with a reason) the finding.',
      },
    ],
  },
  {
    title: 'Configuration',
    icon: '⚙',
    items: [
      {
        q: 'How do I set up teams?',
        a: 'Navigate to the Teams tab under Configuration. Create teams and assign user identities to them. Team assignments drive cost attribution in chargeback reports.',
      },
      {
        q: 'How do I configure model pricing?',
        a: 'Go to the Pricing tab and set per-model input/output token prices. These prices are used for cost calculations across all analytics views.',
      },
    ],
  },
  {
    title: 'Cost Governance',
    icon: '⚖',
    items: [
      {
        q: 'What is the two-tier billed-vs-estimated cost model?',
        a: 'Tier A is authoritative billed cost, ingested from an AWS Cost and Usage Report (CUR 2.0) export — the real dollars AWS charged. Tier B is the token-based estimate the dashboard computes from invocation logs and your pricing configuration. When Tier A is available, the dashboard reconciles the two so drill-downs sum to the actual bill; when it is not, everything is labeled estimated.',
      },
      {
        q: 'Why is per-prompt cost always an estimate?',
        a: 'AWS bills at the account/resource level, not per individual invocation, so there is no authoritative per-prompt charge to read. The dashboard allocates the billed total across prompts by scaling each token-based estimate by a reconciliation factor (billed ÷ estimated). The slice sums to the real bill, but any single prompt\'s figure remains an estimate.',
      },
      {
        q: 'Why doesn\'t the dashboard enforce budgets or thresholds?',
        a: 'The dashboard is a passive, out-of-band observer: nothing it runs sits in the Bedrock invocation path. It reads logs and billing data after the fact, so it cannot block, throttle, or reject a request. Threshold enforcement would require intercepting invocations in real time, which is out of scope by design. The dashboard provides visibility — labels, variance, and the unattributed-spend canary — not control.',
      },
      {
        q: 'What external prerequisites does cost governance need?',
        a: 'Two, both configured in your AWS account outside this app: (1) a CUR 2.0 export with IAM-principal cost allocation, overlaid by a Glue table so it can be queried via Athena — this pipeline is now live in this deployment, so the dashboard ingests real billed data; and (2) adoption of tagged application inference profiles carrying Application, Environment, and Team tags. Cost-allocation tag activation can lag — until tags are active in CUR, team/application splits on billed rows fall back to an "unknown" bucket while totals stay authoritative.',
      },
      {
        q: 'How does billed cost data get into the dashboard?',
        a: 'The CUR Ingestion agent (Agent Management → Compliance & Operations) runs the pipeline: it queries the CUR 2.0 export in S3 through Athena (via the Glue table) and idempotently upserts billed rows into the dashboard database. It runs automatically every night on a schedule, and you can also trigger it on demand with the Run button. Each run records one informational finding with the outcome — rows upserted and the date billed data is covered through.',
      },
      {
        q: 'What does a "skipped" CUR ingestion mean?',
        a: 'Skipped is benign, not an error. It means the run exited early by design — for example the Athena data source is not configured in that environment, or ingestion is disabled. The run still succeeds and records a finding explaining why nothing was ingested. A genuine problem (such as a failed Athena query) is recorded as a failed execution with error details instead.',
      },
      {
        q: 'How fresh is the billed cost data?',
        a: 'CUR 2.0 exports are delivered roughly daily, and AWS finalizes billed line items up to about 24 hours after usage occurs. Combined with the nightly ingestion run, billed figures typically trail live usage by one to two days. Token-based estimates remain near-real-time, so recent activity shows as estimated first and flips to billed once CUR catches up. The "covered through" date in the CUR Ingestion agent\'s latest finding tells you exactly how far billed data extends.',
      },
      {
        q: 'How do I read Billed, Estimated, Reconciliation Factor, and Variance?',
        a: 'Billed is the authoritative Tier A dollar amount from CUR. Estimated is the Tier B token-based figure. The Reconciliation Factor is billed ÷ estimated for a slice (1.0 means no billed data, so the estimate is used unchanged). Variance is billed − estimated: positive means AWS billed more than estimated, negative means less. A basis pill on every figure tells you which tier it came from.',
      },
      {
        q: 'What is the unattributed-spend canary?',
        a: 'It tracks the share of spend (and records) that could not be matched to any team membership — for example usage through untagged profiles or base model IDs. You set a target (e.g. under 5%); the canary shows the current share against it with a meets/does-not-meet indicator and a daily trend. A rising share signals attribution gaps to close by tagging profiles and assigning identities to teams. Its basis label shows whether the shares are computed on billed or estimated cost.',
      },
    ],
  },
  {
    title: 'Troubleshooting',
    icon: '🔧',
    items: [
      {
        q: 'Why do I see "No data available"?',
        a: 'This usually means no Bedrock invocations have been recorded in the selected date range. Try expanding the date filter or check that CloudTrail is properly configured.',
      },
      {
        q: 'Why are agent findings not updating?',
        a: 'Check the Agent Management panel to see if agents are enabled and their last execution status. If agents show as offline, verify that Bedrock AgentCore Runtime is accessible.',
      },
      {
        q: 'How do I report a bug or request a feature?',
        a: 'Contact the platform team via your internal support channel or file an issue in the project repository.',
      },
    ],
  },
];

export default function FAQ() {
  const [search, setSearch] = useState('');
  const [expanded, setExpanded] = useState<string | null>(null);

  const toggle = (key: string) => setExpanded((prev) => (prev === key ? null : key));

  const lowerSearch = search.toLowerCase();
  const filtered = FAQ_DATA.map((cat) => ({
    ...cat,
    items: cat.items.filter(
      (item) =>
        !lowerSearch ||
        item.q.toLowerCase().includes(lowerSearch) ||
        item.a.toLowerCase().includes(lowerSearch),
    ),
  })).filter((cat) => cat.items.length > 0);

  const categoryColors: Record<string, string> = {
    'Getting Started': '#58a6ff',
    'Analytics': '#3fb9a0',
    'AI Agents': '#bc8cff',
    'Configuration': '#f78166',
    'Cost Governance': '#4ae06a',
    'Troubleshooting': '#ffc94d',
  };

  return (
    <div style={{ maxWidth: 800 }}>
      {/* Info banner */}
      <div style={{ marginBottom: '1.25rem', padding: '1rem 1.25rem', background: 'rgba(88,166,255,0.04)', border: '1px solid rgba(88,166,255,0.12)', borderRadius: 10 }}>
        <div style={{ fontSize: '0.8125rem', color: colors.textSecondary, lineHeight: 1.6 }}>
          Common questions about the Bedrock Activity Dashboard, AI agents, analytics, and configuration.
        </div>
      </div>

      {/* Search */}
      <input
        type="text"
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        placeholder="Search FAQ…"
        style={{
          width: '100%',
          padding: '0.5rem 0.75rem',
          background: colors.inputBg,
          border: `1px solid ${colors.inputBorder}`,
          borderRadius: 8,
          color: colors.textPrimary,
          fontSize: '0.875rem',
          marginBottom: '1.25rem',
          boxSizing: 'border-box',
        }}
      />

      {filtered.length === 0 && (
        <div style={{ color: colors.textMuted, fontSize: '0.8125rem', textAlign: 'center', padding: '2rem 0' }}>
          No matching questions found.
        </div>
      )}

      {filtered.map((cat) => (
        <div key={cat.title} style={{
          background: 'rgba(255,255,255,0.03)',
          border: '1px solid rgba(255,255,255,0.06)',
          borderLeft: `4px solid ${categoryColors[cat.title] || colors.accentBlue}`,
          borderRadius: '0 10px 10px 0',
          padding: '1.25rem',
          marginBottom: '1.25rem',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.75rem' }}>
            <span style={{ fontSize: '1.125rem' }}>{cat.icon}</span>
            <span style={{ fontWeight: 700, fontSize: '0.9375rem', color: colors.textPrimary }}>{cat.title}</span>
            <span style={{ marginLeft: 'auto', fontSize: '0.6875rem', fontWeight: 600, color: categoryColors[cat.title] || colors.accentBlue, background: `${categoryColors[cat.title] || colors.accentBlue}15`, padding: '0.15rem 0.5rem', borderRadius: 10 }}>
              {cat.items.length} question{cat.items.length !== 1 ? 's' : ''}
            </span>
          </div>
          {cat.items.map((item, idx) => {
            const key = `${cat.title}-${idx}`;
            const isOpen = expanded === key;
            return (
              <div key={key} style={{ ...glass.panel, marginBottom: '0.375rem', overflow: 'hidden' }}>
                <button
                  onClick={() => toggle(key)}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    width: '100%',
                    background: 'none',
                    border: 'none',
                    padding: '0.625rem 0.75rem',
                    cursor: 'pointer',
                    color: colors.textPrimary,
                    fontSize: '0.8125rem',
                    textAlign: 'left',
                    gap: '0.5rem',
                  }}
                >
                  <span style={{ fontSize: '0.625rem', color: colors.textMuted, flexShrink: 0 }}>
                    {isOpen ? '▾' : '▸'}
                  </span>
                  <span style={{ flex: 1 }}>{item.q}</span>
                </button>
                {isOpen && (
                  <div
                    style={{
                      padding: '0 0.75rem 0.75rem 1.75rem',
                      fontSize: '0.8125rem',
                      color: colors.textSecondary,
                      lineHeight: 1.6,
                    }}
                  >
                    {item.a}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      ))}
    </div>
  );
}
