import { colors, glass } from '../../theme';

// ---------------------------------------------------------------------------
// Shared diagram primitives.
//
// All five architecture diagrams are rendered inline (styled divs + inline
// SVG connectors) rather than from PNG assets. The inbound traffic path
// (CloudFront -> API Gateway -> VPC Link v2 -> internal ALB -> Fargate)
// evolves, and refreshing a raster asset for every architecture tweak is
// lossy — code is the source of truth here. The prompt-history encryption
// architecture further down follows the same pattern (hand-authored inline
// React).
// ---------------------------------------------------------------------------

type AccentColor =
  | 'blue'
  | 'purple'
  | 'teal'
  | 'orange'
  | 'yellow'
  | 'muted';

const ACCENT: Record<AccentColor, { fg: string; bg: string; border: string }> = {
  blue:   { fg: colors.accentBlue,   bg: 'rgba(96,176,255,0.08)',  border: 'rgba(96,176,255,0.35)' },
  purple: { fg: colors.accentPurple, bg: 'rgba(201,158,255,0.08)', border: 'rgba(201,158,255,0.35)' },
  teal:   { fg: colors.accentTeal,   bg: 'rgba(74,224,106,0.08)',  border: 'rgba(74,224,106,0.35)' },
  orange: { fg: colors.accentOrange, bg: 'rgba(255,138,106,0.08)', border: 'rgba(255,138,106,0.35)' },
  yellow: { fg: colors.accentYellow, bg: 'rgba(255,201,77,0.08)',  border: 'rgba(255,201,77,0.35)' },
  muted:  { fg: colors.textMuted,    bg: 'rgba(255,255,255,0.03)', border: 'rgba(255,255,255,0.1)' },
};

interface BoxProps {
  title: string;
  sub?: string | React.ReactNode;
  accent?: AccentColor;
  compact?: boolean;
  minWidth?: number;
}

function Box({ title, sub, accent = 'blue', compact, minWidth }: BoxProps) {
  const c = ACCENT[accent];
  return (
    <div
      style={{
        padding: compact ? '0.4rem 0.65rem' : '0.55rem 0.85rem',
        background: c.bg,
        border: `1px solid ${c.border}`,
        borderRadius: 6,
        minWidth,
        display: 'inline-block',
        verticalAlign: 'top',
      }}
    >
      <div
        style={{
          fontSize: compact ? '0.75rem' : '0.8125rem',
          fontWeight: 600,
          color: c.fg,
          lineHeight: 1.3,
        }}
      >
        {title}
      </div>
      {sub && (
        <div
          style={{
            fontSize: compact ? '0.65rem' : '0.6875rem',
            color: colors.textMuted,
            marginTop: '0.15rem',
            lineHeight: 1.4,
          }}
        >
          {sub}
        </div>
      )}
    </div>
  );
}

interface ArrowProps {
  label?: string;
  dashed?: boolean;
  dir?: 'down' | 'right' | 'up';
  color?: string;
}

function Arrow({ label, dashed, dir = 'down', color = colors.textMuted }: ArrowProps) {
  const stroke = dashed ? '4 4' : undefined;
  if (dir === 'down' || dir === 'up') {
    const up = dir === 'up';
    return (
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          padding: '0.4rem 0',
          gap: '0.5rem',
        }}
      >
        <svg width="12" height="40" style={{ overflow: 'visible' }}>
          <defs>
            <marker id={`ah-${up ? 'u' : 'd'}-${color.replace(/[^a-z0-9]/gi, '')}`} markerWidth="8" markerHeight="8" refX="4" refY="4" orient="auto">
              <path d="M0,0 L8,4 L0,8 Z" fill={color} />
            </marker>
          </defs>
          <line
            x1="6"
            y1={up ? 36 : 4}
            x2="6"
            y2={up ? 4 : 36}
            stroke={color}
            strokeWidth="1.5"
            strokeDasharray={stroke}
            markerEnd={`url(#ah-${up ? 'u' : 'd'}-${color.replace(/[^a-z0-9]/gi, '')})`}
          />
        </svg>
        {label && (
          <span style={{ fontSize: '0.6875rem', color: colors.textMuted, fontStyle: dashed ? 'italic' : 'normal' }}>
            {label}
          </span>
        )}
      </div>
    );
  }
  // dir === 'right'
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', padding: '0 0.4rem' }}>
      {label && (
        <span style={{ fontSize: '0.6875rem', color: colors.textMuted, fontStyle: dashed ? 'italic' : 'normal', whiteSpace: 'nowrap' }}>
          {label}
        </span>
      )}
      <svg width="48" height="12" style={{ overflow: 'visible' }}>
        <defs>
          <marker id={`ah-r-${color.replace(/[^a-z0-9]/gi, '')}`} markerWidth="8" markerHeight="8" refX="4" refY="4" orient="auto">
            <path d="M0,0 L8,4 L0,8 Z" fill={color} />
          </marker>
        </defs>
        <line
          x1="4"
          y1="6"
          x2="44"
          y2="6"
          stroke={color}
          strokeWidth="1.5"
          strokeDasharray={stroke}
          markerEnd={`url(#ah-r-${color.replace(/[^a-z0-9]/gi, '')})`}
        />
      </svg>
    </div>
  );
}

interface ZoneProps {
  title: string;
  color?: AccentColor;
  children: React.ReactNode;
  compact?: boolean;
}

function Zone({ title, color = 'muted', children, compact }: ZoneProps) {
  const c = ACCENT[color];
  return (
    <div
      style={{
        border: `1.5px dashed ${c.border}`,
        borderRadius: 10,
        padding: compact ? '0.5rem 0.75rem 0.75rem' : '0.75rem 1rem 1rem',
        background: c.bg,
        position: 'relative',
        marginTop: '0.5rem',
      }}
    >
      <div
        style={{
          position: 'absolute',
          top: -9,
          left: 12,
          padding: '0 0.5rem',
          background: colors.pageBg,
          fontSize: '0.6875rem',
          fontWeight: 700,
          textTransform: 'uppercase',
          letterSpacing: '0.05em',
          color: c.fg,
        }}
      >
        {title}
      </div>
      {children}
    </div>
  );
}

interface DiagramSectionProps {
  title: string;
  description: string;
  children: React.ReactNode;
}

function DiagramSection({ title, description, children }: DiagramSectionProps) {
  return (
    <div style={{ marginBottom: '2rem' }}>
      <h3 style={{ fontSize: '0.9375rem', color: colors.textPrimary, marginBottom: '0.4rem' }}>
        {title}
      </h3>
      <p
        style={{
          fontSize: '0.8125rem',
          color: colors.textMuted,
          marginBottom: '1rem',
          lineHeight: 1.55,
        }}
      >
        {description}
      </p>
      <div style={{ ...glass.card, padding: '1.25rem' }}>{children}</div>
    </div>
  );
}

const Row: React.FC<{ children: React.ReactNode; gap?: string; wrap?: boolean; justify?: string }> = ({
  children,
  gap = '0.5rem',
  wrap,
  justify = 'center',
}) => (
  <div
    style={{
      display: 'flex',
      alignItems: 'center',
      justifyContent: justify,
      gap,
      flexWrap: wrap ? 'wrap' : 'nowrap',
    }}
  >
    {children}
  </div>
);

const Col: React.FC<{ children: React.ReactNode; gap?: string; align?: string }> = ({
  children,
  gap = '0',
  align = 'center',
}) => (
  <div style={{ display: 'flex', flexDirection: 'column', alignItems: align, gap }}>{children}</div>
);

// ===========================================================================
// Diagram 1 — System Architecture (inbound flow)
// ===========================================================================
function SystemArchitectureDiagram() {
  return (
    <DiagramSection
      title="System Architecture"
      description="High-level view of all AWS services. Browser → CloudFront → API Gateway (REST, with Cognito authorizer + response streaming for SSE) → VPC Link v2 → internal ALB → ECS Fargate. The ALB has no public IP and lives in private subnets; direct curl against the ALB hostname from the internet times out. CloudFront handles the SPA default behavior from S3 and the /api/* behavior from API Gateway over HTTPS/TLSv1.2 end-to-end."
    >
      {/* Top row: User, CloudFront, API Gateway, Cognito */}
      <Row gap="0" wrap>
        <Col>
          <Box title="User / Browser" sub="OAuth2 PKCE sign-in" accent="muted" />
        </Col>
        <Arrow label="HTTPS" dir="right" />
        <Col gap="0.5rem">
          <Box title="CloudFront" sub="default → S3 SPA (OAI)" accent="purple" />
          <Box title="/api/* behavior" sub="HTTPS_ONLY, TLSv1.2, ALL_VIEWER_EXCEPT_HOST_HEADER" accent="purple" compact />
        </Col>
        <Arrow label="HTTPS" dir="right" />
        <Col>
          <Box
            title="API Gateway REST API"
            sub={
              <>
                regional, <code style={{ color: colors.accentPurple }}>execute-api</code> default cert<br />
                Cognito authorizer on <code style={{ color: colors.accentPurple }}>/{'{proxy+}'}</code><br />
                <code style={{ color: colors.accentPurple }}>GET /api/health/config</code> unauth<br />
                <code style={{ color: colors.accentPurple }}>ResponseTransferMode: STREAM</code> (SSE)
              </>
            }
            accent="blue"
            minWidth={240}
          />
        </Col>
      </Row>

      {/* Cognito authorizer side-pod with dashed line */}
      <div style={{ display: 'flex', justifyContent: 'center', marginTop: '0.3rem', marginBottom: '0.3rem' }}>
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '0.4rem',
            fontSize: '0.6875rem',
            color: colors.textMuted,
            fontStyle: 'italic',
          }}
        >
          <Box title="Cognito User Pool" sub="RS256 JWKS, admin / payer / user groups" accent="orange" compact />
          <span>— validates ID token (defense in depth; FastAPI validates too)</span>
        </div>
      </div>

      <Arrow label="VPC Link v2 (private integration, STREAM pass-through)" />

      {/* VPC zone */}
      <Zone title="VPC (private-with-egress subnets, 2 AZs)" color="teal">
        <Row gap="0">
          <Box title="VPC Link v2 ENIs" sub="egress to albSg :80 only" accent="teal" />
          <Arrow dir="right" />
          <Box title="Internal ALB :80" sub={<>publicLoadBalancer: false<br />ingress from vpcLinkSg only</>} accent="teal" />
          <Arrow dir="right" />
          <Box title="ECS Fargate :8000" sub="FastAPI (v1 analytics + v2 agents)" accent="teal" />
        </Row>

        {/* Fan-out from Fargate */}
        <Arrow label="service calls" />
        <Row gap="0.4rem" wrap>
          <Box title="RDS PostgreSQL" sub="private-isolated, :5432, 21 tables" accent="muted" compact />
          <Box title="Bedrock Runtime" sub="Converse / InvokeModel + requestMetadata attribution" accent="muted" compact />
          <Box title="AgentCore Runtime" sub="13 governed agents" accent="muted" compact />
          <Box title="Secrets Manager" sub="DB creds + Fernet key" accent="muted" compact />
          <Box title="CloudWatch / S3 / CloudTrail" sub="hybrid ingestion sources" accent="muted" compact />
        </Row>
      </Zone>

      {/* Trust-boundary note */}
      <div
        style={{
          marginTop: '0.9rem',
          padding: '0.6rem 0.85rem',
          background: 'rgba(74,224,106,0.04)',
          border: '1px solid rgba(74,224,106,0.15)',
          borderRadius: 6,
          fontSize: '0.7rem',
          color: colors.textSecondary,
          lineHeight: 1.6,
        }}
      >
        <strong style={{ color: colors.accentTeal }}>Trust boundary.</strong> The ALB is not
        routable from the internet — DNS resolves to RFC1918 private IPs that the viewer cannot reach, and
        the SG only accepts from the VPC Link SG. A direct <code>curl http://&lt;alb-dns&gt;/health</code>
        from any host outside the VPC times out.
      </div>
    </DiagramSection>
  );
}

// ===========================================================================
// Diagram 2 — Data Flow (hybrid ingestion priority)
// ===========================================================================
function DataFlowDiagram() {
  return (
    <DiagramSection
      title="Data Flow"
      description="End-to-end ingestion pipeline. Any Bedrock invocation in the configured region produces logs in two destinations (CloudWatch Logs + S3). CloudTrailService queries them in priority order — CloudWatch Logs (real-time, last 48h) first, S3 (historical) second, CloudTrail LookupEvents only as a fallback. Records are deduplicated by invocation ID (or content-hash fallback) and cached process-wide for 60s. Agent Chat records write straight to the DB with per-user requestMetadata attribution, bypassing the log round-trip."
    >
      <Col gap="0">
        <Box title="Bedrock API caller" sub="any InvokeModel / Converse / Converse Stream in us-west-2" accent="muted" />
        <Arrow label="model invocation logging" />

        {/* Logging destinations */}
        <Row gap="0.5rem">
          <Box title="CloudWatch Logs" sub="/aws/bedrock/model-invocations (real-time, last 48h)" accent="blue" />
          <Box title="S3 invocation logs" sub="bedrock-invocation-logs-<account> (historical, gzip JSON)" accent="blue" />
        </Row>

        <Arrow label="hybrid ingestion priority" />

        <Box
          title="CloudTrailService.get_activity_records_with_meta()"
          sub={
            <>
              1. <strong>CloudWatch Logs</strong> (near real-time, primary)<br />
              2. <strong>S3</strong> (fallback for records older than 48h)<br />
              3. <strong>CloudTrail LookupEvents</strong> (final fallback — bare metadata only)<br />
              dedupe by invocation ID / content hash · 60s process-wide cache keyed on time window + region set
            </>
          }
          accent="purple"
          minWidth={520}
        />

        <Arrow label="parsed records" />

        {/* Fan-out to UI/features */}
        <Row gap="0.5rem" wrap>
          <Box title="Overview / Token / Model / Cost tabs" sub="aggregated analytics via AggregationService + CostService" accent="teal" compact />
          <Box title="Prompt History" sub="per-record detail with the toggle + encryption gate (see below)" accent="teal" compact />
          <Box title="Agent Findings" sub="deterministic + agentic agents consume the same stream" accent="teal" compact />
        </Row>

        {/* Side-channel: direct Agent Chat / demo persistence */}
        <div
          style={{
            marginTop: '1rem',
            padding: '0.7rem 0.9rem',
            background: 'rgba(201,158,255,0.04)',
            border: '1px solid rgba(201,158,255,0.15)',
            borderRadius: 6,
            fontSize: '0.75rem',
            color: colors.textSecondary,
            lineHeight: 1.55,
          }}
        >
          <strong style={{ color: colors.accentPurple }}>Direct-write side channel.</strong>{' '}
          Agent Chat and demo-generation records are written straight to <code>chat_messages</code> /{' '}
          <code>demo_activity_records</code> with per-caller <code>requestMetadata</code>{' '}
          attribution (<code>UserIdentity</code>, <code>UserName</code>, <code>SessionId</code>). The ingestion
          parser prefers <code>requestMetadata</code> over the shared backend ARN when present, so Agent
          Chat shows up correctly attributed in the merged Prompt History view.
        </div>
      </Col>
    </DiagramSection>
  );
}

// ===========================================================================
// Diagram 3 — Agent Invocation
// ===========================================================================
function AgentInvocationDiagram() {
  return (
    <DiagramSection
      title="Agent Invocation"
      description="Agents fire either on a schedule (EventBridge Scheduler per cron) or on demand (admin UI 'Execute' button). AgentExecutionService dispatches to the correct code path based on the agent's registry entry: deterministic agents (⚙️) run their tool chain directly; agentic agents (🤖) go through AgentCoreInvoker, which calls AgentCore Runtime first and falls back to Bedrock's invoke_inline_agent on failure. Findings land in per-agent PostgreSQL tables and surface in the Governance / Insights tabs."
    >
      <Col gap="0">
        {/* Triggers */}
        <Row gap="0.5rem">
          <Box title="EventBridge Scheduler" sub="cron per agent (CUR: 09:00 UTC, Discovery: 10:00 UTC, etc.)" accent="yellow" />
          <Box title="Manual trigger" sub="admin UI → POST /api/v2/agents/{name}/execute" accent="yellow" />
        </Row>

        <Arrow label="trigger_type: scheduled | manual" />

        <Box title="AgentExecutionService.execute()" sub="loads registry entry · resolves deterministic vs agentic path" accent="purple" minWidth={360} />

        <Arrow />

        {/* Two paths */}
        <Row gap="1rem" justify="center">
          {/* Deterministic path */}
          <Col gap="0.4rem">
            <Box title="Deterministic ⚙️" sub="_try_direct_execution() — direct tool chain, no LLM" accent="teal" />
            <div style={{ fontSize: '0.65rem', color: colors.textMuted, fontStyle: 'italic', textAlign: 'center' }}>
              11 of 13 agents today
            </div>
          </Col>

          {/* Agentic path */}
          <Col gap="0.4rem">
            <Box title="Agentic 🤖" sub="AgentCoreInvoker — LLM-powered" accent="blue" />
            <div style={{ fontSize: '0.65rem', color: colors.textMuted, textAlign: 'center' }}>
              1. <code>bedrock-agentcore:InvokeAgentRuntime</code>
              <br />
              2. Fallback: <code>bedrock:InvokeInlineAgent</code>
            </div>
            <div style={{ fontSize: '0.65rem', color: colors.textMuted, fontStyle: 'italic', textAlign: 'center' }}>
              Guardrail Recommender, Chargeback Reconciliation, Weekly Digest Orchestrator
            </div>
          </Col>
        </Row>

        <Arrow label="findings" />

        <Box
          title="PostgreSQL — per-agent tables"
          sub="agent_findings, agent_executions, agent_configs (14-table family under app/v2/models.py)"
          accent="purple"
          minWidth={420}
        />

        <Arrow />

        <Row gap="0.5rem" wrap>
          <Box title="Governance / Insights tabs" sub="per-agent findings UI with approval/dismiss actions" accent="muted" compact />
          <Box title="Weekly Digest email" sub="SNS → operator inbox, orchestrated by Weekly Digest agent" accent="muted" compact />
        </Row>
      </Col>
    </DiagramSection>
  );
}

// ===========================================================================
// Diagram 4 — Billed Cost Pipeline
// ===========================================================================
function BilledCostPipelineDiagram() {
  return (
    <DiagramSection
      title="Billed Cost Pipeline (Cost Governance)"
      description="The Tier A billed-cost path. AWS CUR 2.0 delivers daily exports to S3 at IAM-principal granularity, a Glue table makes the data queryable via Athena, and CurIngestionService runs nightly as an ECS task (EventBridge Scheduler, 09:00 UTC) to UPSERT billed rows into cur_billed_cost. The service degrades gracefully: if no Athena/CUR config is set in the environment, it logs a 'skipped' message and returns success — the dashboard just falls back to token-based estimates (Tier B). Billed figures are reconciled against estimates with profile-tag enrichment and surface in Cost Governance with Billed/Estimated basis pills."
    >
      <Col gap="0">
        {/* CUR provisioning (operator) */}
        <Box title="AWS CUR 2.0 export" sub="operator-provisioned, out-of-band · IAM-principal granularity, Parquet, delivered ~daily" accent="muted" minWidth={420} />

        <Arrow label="deliver" />

        <Box title="S3 CUR bucket" sub="customer-owned, read-only to the dashboard task role" accent="orange" />

        <Arrow label="08:00 UTC Glue crawler refresh" />

        <Box title="Glue table: cur_db.cur_table" sub="partition keys picked up by Athena at query time" accent="orange" />

        <Arrow label="SELECT via Athena workgroup (default: primary)" />

        {/* Nightly ingestion loop */}
        <Zone title="Nightly ingestion (EventBridge Scheduler → ECS RunTask)" color="purple" compact>
          <Row gap="0.4rem">
            <Box title="EventBridge Scheduler" sub="daily cron: 09:00 UTC" accent="purple" compact />
            <Arrow dir="right" />
            <Box title="ECS RunTask" sub="reuses Backend task def with command override: python -m app.services.cur_ingestion_service" accent="purple" compact />
            <Arrow dir="right" />
            <Box title="CurIngestionService" sub="validate SQL identifiers · StartQueryExecution · GetQueryResults · UPSERT into Postgres" accent="purple" compact />
          </Row>
        </Zone>

        <Arrow label="billed rows" />

        <Box title="cur_billed_cost (PostgreSQL)" sub="joined against token-based estimates for Tier A/B reconciliation + profile-tag enrichment" accent="blue" minWidth={420} />

        <Arrow />

        <Box
          title="Cost Governance tab"
          sub="Billed / Estimated basis pills · per-application, per-team, per-model breakdowns"
          accent="teal"
        />

        {/* Fallback note */}
        <div
          style={{
            marginTop: '0.9rem',
            padding: '0.6rem 0.85rem',
            background: 'rgba(255,201,77,0.04)',
            border: '1px solid rgba(255,201,77,0.15)',
            borderRadius: 6,
            fontSize: '0.7rem',
            color: colors.textSecondary,
            lineHeight: 1.6,
          }}
        >
          <strong style={{ color: colors.accentYellow }}>Graceful degradation.</strong> If the
          deploy environment has no <code>ATHENA_DATABASE</code> / <code>ATHENA_TABLE</code> set,
          the IAM policy carve-out drops the Athena/Glue/S3 permissions entirely and
          CurIngestionService logs <code>"CUR unconfigured — skipping"</code> on each run. The
          dashboard still works end-to-end on token-estimated figures alone.
        </div>
      </Col>
    </DiagramSection>
  );
}

// ===========================================================================
// Diagram 5 — Per-Category Agents (13-agent grid)
// ===========================================================================
function PerCategoryAgentsDiagram() {
  interface AgentDef {
    name: string;
    type: 'agentic' | 'deterministic';
  }
  interface CategoryDef {
    title: string;
    accent: AccentColor;
    agents: AgentDef[];
  }

  const CATEGORIES: CategoryDef[] = [
    {
      title: 'Security & Guardrails',
      accent: 'orange',
      agents: [
        { name: 'Cost Anomaly Alerter', type: 'deterministic' },
        { name: 'Guardrail Recommender', type: 'agentic' },
        { name: 'Model Cost Optimizer', type: 'deterministic' },
      ],
    },
    {
      title: 'Usage Intelligence',
      accent: 'blue',
      agents: [
        { name: 'Prompt Quality Analyzer', type: 'deterministic' },
        { name: 'Model Adoption Tracker', type: 'deterministic' },
        { name: 'Usage Forecaster', type: 'deterministic' },
      ],
    },
    {
      title: 'Compliance & Operations',
      accent: 'teal',
      agents: [
        { name: 'Data Residency Auditor', type: 'deterministic' },
        { name: 'Latency & Error Monitor', type: 'deterministic' },
        { name: 'Chargeback Report Generator', type: 'deterministic' },
        { name: 'Onboarding Assistant', type: 'deterministic' },
        { name: 'Chargeback Reconciliation', type: 'agentic' },
      ],
    },
    {
      title: 'Orchestration',
      accent: 'purple',
      agents: [
        { name: 'Policy Compliance Checker', type: 'deterministic' },
        { name: 'Weekly Digest Orchestrator', type: 'agentic' },
      ],
    },
  ];

  return (
    <DiagramSection
      title="Per-Category Agents (13 total)"
      description="The 13 agents are grouped into 4 categories by functional domain. Each agent is either 'agentic' (🤖 — LLM-powered via AgentCore Runtime with inline-agent fallback) or 'deterministic' (⚙️ — direct tool chain, no LLM involved). The historic PII Detector agent was retired; prompt-content protection moved to the Prompt History toggle + at-rest encryption documented below."
    >
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))',
          gap: '0.75rem',
        }}
      >
        {CATEGORIES.map((cat) => {
          const c = ACCENT[cat.accent];
          return (
            <div
              key={cat.title}
              style={{
                padding: '0.75rem 0.9rem',
                background: c.bg,
                border: `1px solid ${c.border}`,
                borderRadius: 8,
              }}
            >
              <div
                style={{
                  fontSize: '0.75rem',
                  fontWeight: 700,
                  textTransform: 'uppercase',
                  letterSpacing: '0.04em',
                  color: c.fg,
                  marginBottom: '0.5rem',
                }}
              >
                {cat.title}
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '0.3rem' }}>
                {cat.agents.map((a) => (
                  <div
                    key={a.name}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: '0.4rem',
                      fontSize: '0.75rem',
                      color: colors.textSecondary,
                    }}
                  >
                    <span style={{ fontSize: '0.75rem' }}>{a.type === 'agentic' ? '🤖' : '⚙️'}</span>
                    <span>{a.name}</span>
                  </div>
                ))}
              </div>
            </div>
          );
        })}
      </div>

      <div
        style={{
          marginTop: '0.9rem',
          display: 'flex',
          gap: '1rem',
          fontSize: '0.7rem',
          color: colors.textMuted,
          flexWrap: 'wrap',
        }}
      >
        <span>🤖 Agentic — LLM-powered (AgentCore Runtime, inline-agent fallback)</span>
        <span>⚙️ Deterministic — direct tool execution, no LLM invocation</span>
      </div>
    </DiagramSection>
  );
}

// ---------------------------------------------------------------------------
// Prompt History & Encryption architecture (hand-authored, inline React —
// same pattern as the five architecture diagrams above)
// ---------------------------------------------------------------------------

interface PromptHistoryStepProps {
  n: number;
  title: string;
  children: React.ReactNode;
  tone?: 'default' | 'warn' | 'ok';
}

function PromptHistoryStep({ n, title, children, tone = 'default' }: PromptHistoryStepProps) {
  const toneColor =
    tone === 'warn' ? colors.accentOrange : tone === 'ok' ? '#3fb9a0' : colors.accentBlue;
  return (
    <div
      style={{
        display: 'flex',
        gap: '0.75rem',
        padding: '0.75rem 0.875rem',
        background: 'rgba(255,255,255,0.02)',
        border: `1px solid rgba(255,255,255,0.06)`,
        borderLeft: `3px solid ${toneColor}`,
        borderRadius: '6px',
        marginBottom: '0.5rem',
      }}
    >
      <div
        style={{
          width: 22,
          height: 22,
          borderRadius: '50%',
          background: `${toneColor}22`,
          color: toneColor,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          fontSize: '0.75rem',
          fontWeight: 700,
          flexShrink: 0,
          marginTop: 2,
        }}
      >
        {n}
      </div>
      <div style={{ fontSize: '0.8125rem', color: colors.textSecondary, lineHeight: 1.6 }}>
        <div style={{ fontWeight: 600, color: colors.textPrimary, marginBottom: '0.2rem' }}>
          {title}
        </div>
        {children}
      </div>
    </div>
  );
}

function Code({ children }: { children: React.ReactNode }) {
  return (
    <code
      style={{
        padding: '0.05rem 0.3rem',
        background: 'rgba(0,0,0,0.3)',
        border: '1px solid rgba(255,255,255,0.08)',
        borderRadius: 3,
        fontFamily: 'monospace',
        fontSize: '0.7rem',
        color: colors.accentPurple,
      }}
    >
      {children}
    </code>
  );
}

function PromptHistoryArchitecture() {
  return (
    <div style={{ marginBottom: '2rem' }}>
      <h3 style={{ fontSize: '0.9375rem', color: colors.textPrimary, marginBottom: '0.5rem' }}>
        Prompt History &amp; At-Rest Encryption
      </h3>
      <p
        style={{
          fontSize: '0.8125rem',
          color: colors.textMuted,
          marginBottom: '1rem',
          lineHeight: 1.5,
        }}
      >
        How the admin Prompt History toggle flows through the backend, which code paths encrypt and
        decrypt row bodies at rest, and the gatekeeper that blocks enabling without a usable Fernet
        key. The toggle gates visibility of prompt and response text across every record source
        (Agent Chat, demo, CloudWatch Logs, S3 invocation logs, CloudTrail); metadata always stays
        visible.
      </p>

      {/* State diagram */}
      <div
        style={{
          ...glass.card,
          padding: '1rem 1.25rem',
          marginBottom: '1rem',
        }}
      >
        <div
          style={{
            fontSize: '0.6875rem',
            fontWeight: 700,
            textTransform: 'uppercase',
            letterSpacing: '0.06em',
            color: colors.textMuted,
            marginBottom: '0.6rem',
          }}
        >
          Where the state lives
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem' }}>
          <div>
            <div
              style={{
                fontSize: '0.8125rem',
                fontWeight: 600,
                color: colors.textPrimary,
                marginBottom: '0.3rem',
              }}
            >
              Toggle value
            </div>
            <div style={{ fontSize: '0.75rem', color: colors.textSecondary, lineHeight: 1.6 }}>
              A single row in <Code>app_runtime_settings</Code> (migration{' '}
              <Code>021_app_runtime_settings</Code>) with key{' '}
              <Code>prompt_history_enabled</Code> and value <Code>"true"</Code> /{' '}
              <Code>"false"</Code>, plus <Code>updated_at</Code> and <Code>updated_by</Code> for an
              audit trail. Default (missing row) is <strong>true</strong>, applied at read time by{' '}
              <Code>settings_service.get_prompt_history_enabled</Code>.
            </div>
          </div>
          <div>
            <div
              style={{
                fontSize: '0.8125rem',
                fontWeight: 600,
                color: colors.textPrimary,
                marginBottom: '0.3rem',
              }}
            >
              Encryption key
            </div>
            <div style={{ fontSize: '0.75rem', color: colors.textSecondary, lineHeight: 1.6 }}>
              An AWS Secrets Manager entry called <Code>PromptHistoryEncryptionKey</Code>, created
              by the Backend CDK stack and injected into the ECS task as the{' '}
              <Code>PROMPT_HISTORY_ENCRYPTION_KEY</Code> env var via{' '}
              <Code>ecs.Secret.fromSecretsManager()</Code>. Never persisted locally; read into{' '}
              <Code>PromptHistoryCrypto</Code> at process start.
            </div>
          </div>
        </div>
      </div>

      {/* Write path */}
      <div style={{ marginBottom: '1rem' }}>
        <div
          style={{
            fontSize: '0.6875rem',
            fontWeight: 700,
            textTransform: 'uppercase',
            letterSpacing: '0.06em',
            color: colors.textMuted,
            marginBottom: '0.5rem',
          }}
        >
          Write path (Agent Chat / Demo persistence)
        </div>
        <PromptHistoryStep n={1} title="Request arrives">
          A user sends a message via <Code>POST /api/agent/chat</Code> (or demo generation runs via{' '}
          <Code>POST /api/demo/generate</Code>). The backend processes the Bedrock call as normal
          and gets back a response. Nothing is persisted yet.
        </PromptHistoryStep>
        <PromptHistoryStep n={2} title="Toggle check">
          Before writing to the DB, the service calls{' '}
          <Code>settings_service.get_prompt_history_enabled(db)</Code>. If <strong>false</strong>,
          the service logs <Code>"Prompt history disabled; skipping persistence"</Code> and
          returns. The chat/demo still works in-session; nothing lands in the DB.
        </PromptHistoryStep>
        <PromptHistoryStep n={3} title="Encrypt via Fernet envelope">
          If the toggle is on, each piece of text goes through{' '}
          <Code>get_crypto().encrypt(plaintext)</Code>, which wraps it as{' '}
          <Code>"enc:v1:&lt;Fernet-token&gt;"</Code> (AES-128-CBC + HMAC-SHA256, with a built-in
          timestamp). If the key isn't loaded, the helper returns plaintext unchanged — but the
          gatekeeper below means this state can't normally be reached in production.
        </PromptHistoryStep>
        <PromptHistoryStep n={4} title="Persist" tone="ok">
          The envelope string is written to <Code>chat_messages.content</Code> (Agent Chat) or{' '}
          <Code>demo_activity_records.prompt_text</Code> / <Code>response_text</Code> (demo).
          Nothing else is stored about the plaintext.
        </PromptHistoryStep>
      </div>

      {/* Read path */}
      <div style={{ marginBottom: '1rem' }}>
        <div
          style={{
            fontSize: '0.6875rem',
            fontWeight: 700,
            textTransform: 'uppercase',
            letterSpacing: '0.06em',
            color: colors.textMuted,
            marginBottom: '0.5rem',
          }}
        >
          Read path (Prompt Detail display)
        </div>
        <PromptHistoryStep n={1} title="Row fetched from DB or AWS log source">
          The record is loaded from one of five places: <Code>chat_messages</Code> (Agent Chat),{' '}
          <Code>demo_activity_records</Code> (demo), CloudWatch Logs, S3 invocation logs, or
          CloudTrail. The first two carry envelope-prefixed ciphertext this app wrote. The last
          three carry plaintext as AWS stored them (protected by AWS-managed KMS / SSE at those
          services, not by this app).
        </PromptHistoryStep>
        <PromptHistoryStep n={2} title="Decrypt — always">
          Every reader calls <Code>get_crypto().decrypt(stored)</Code>, which returns one of three
          things: (a) the value unchanged if it has no <Code>enc:v1:</Code> prefix (legacy
          plaintext or AWS log source), (b) the decrypted plaintext if the current key succeeds, or
          (c) a safe placeholder <Code>"[encrypted content — &lt;reason&gt;]"</Code> if the current
          key can't decrypt it (bad token, key rotated, key not loaded). The UI never sees raw
          ciphertext.
        </PromptHistoryStep>
        <PromptHistoryStep n={3} title="Toggle gate at the API edge" tone="warn">
          <Code>GET /api/activity/prompts/&#123;id&#125;</Code> calls{' '}
          <Code>settings_service.get_prompt_history_enabled</Code> on every request. If{' '}
          <strong>off</strong>, the response has empty <Code>promptText</Code> /{' '}
          <Code>responseText</Code> and sets <Code>promptBodyHidden: true</Code>. The frontend's{' '}
          Prompt Detail modal checks that flag and renders the yellow "content hidden" banner
          instead of the body — regardless of record source.
        </PromptHistoryStep>
      </div>

      {/* Gatekeeper */}
      <div
        style={{
          ...glass.card,
          padding: '1rem 1.25rem',
          marginBottom: '1rem',
          borderLeft: `4px solid ${colors.accentOrange}`,
        }}
      >
        <div
          style={{
            fontSize: '0.75rem',
            fontWeight: 700,
            textTransform: 'uppercase',
            letterSpacing: '0.06em',
            color: colors.accentOrange,
            marginBottom: '0.5rem',
          }}
        >
          Gatekeeper
        </div>
        <div style={{ fontSize: '0.8125rem', color: colors.textSecondary, lineHeight: 1.6 }}>
          <Code>PUT /api/settings/prompt-history &#123;enabled: true&#125;</Code> is{' '}
          <strong>rejected with HTTP 400</strong> (
          <Code>error: "encryption_key_unavailable"</Code>) unless the backend has a usable Fernet
          key loaded. The Settings UI mirrors this: when{' '}
          <Code>encryption_available</Code> is false, the toggle control is disabled, a cursor
          hint explains it, and an orange notice tells the admin to configure{' '}
          <Code>PROMPT_HISTORY_ENCRYPTION_KEY</Code>. Disabling the toggle is always allowed, even
          without a key. Net effect: this dashboard never persists or surfaces prompt bodies
          through an advertised "Prompt History is on" state without the advertised at-rest
          encryption actually being in effect.
        </div>
      </div>

      {/* Behavior matrix */}
      <div
        style={{
          ...glass.card,
          padding: '1rem 1.25rem',
          marginBottom: '1rem',
        }}
      >
        <div
          style={{
            fontSize: '0.6875rem',
            fontWeight: 700,
            textTransform: 'uppercase',
            letterSpacing: '0.06em',
            color: colors.textMuted,
            marginBottom: '0.6rem',
          }}
        >
          Behavior matrix
        </div>
        <div style={{ overflowX: 'auto' }}>
          <table
            style={{
              width: '100%',
              borderCollapse: 'collapse',
              fontSize: '0.75rem',
            }}
          >
            <thead>
              <tr>
                <th
                  style={{
                    textAlign: 'left',
                    padding: '0.4rem 0.6rem',
                    color: colors.textMuted,
                    fontWeight: 600,
                    textTransform: 'uppercase',
                    letterSpacing: '0.05em',
                    borderBottom: `1px solid rgba(255,255,255,0.1)`,
                  }}
                >
                  Toggle
                </th>
                <th
                  style={{
                    textAlign: 'left',
                    padding: '0.4rem 0.6rem',
                    color: colors.textMuted,
                    fontWeight: 600,
                    textTransform: 'uppercase',
                    letterSpacing: '0.05em',
                    borderBottom: `1px solid rgba(255,255,255,0.1)`,
                  }}
                >
                  Source
                </th>
                <th
                  style={{
                    textAlign: 'left',
                    padding: '0.4rem 0.6rem',
                    color: colors.textMuted,
                    fontWeight: 600,
                    textTransform: 'uppercase',
                    letterSpacing: '0.05em',
                    borderBottom: `1px solid rgba(255,255,255,0.1)`,
                  }}
                >
                  List metadata
                </th>
                <th
                  style={{
                    textAlign: 'left',
                    padding: '0.4rem 0.6rem',
                    color: colors.textMuted,
                    fontWeight: 600,
                    textTransform: 'uppercase',
                    letterSpacing: '0.05em',
                    borderBottom: `1px solid rgba(255,255,255,0.1)`,
                  }}
                >
                  Detail body in UI
                </th>
                <th
                  style={{
                    textAlign: 'left',
                    padding: '0.4rem 0.6rem',
                    color: colors.textMuted,
                    fontWeight: 600,
                    textTransform: 'uppercase',
                    letterSpacing: '0.05em',
                    borderBottom: `1px solid rgba(255,255,255,0.1)`,
                  }}
                >
                  Write path
                </th>
              </tr>
            </thead>
            <tbody>
              {[
                ['ON', 'Agent Chat', 'visible', 'plaintext (Fernet-decrypted)', 'encrypted write to chat_messages'],
                ['ON', 'Demo', 'visible', 'plaintext (Fernet-decrypted)', 'encrypted write to demo_activity_records'],
                ['ON', 'CloudWatch / S3 / CloudTrail', 'visible', 'plaintext (AWS-side encryption)', 'n/a (not persisted)'],
                ['OFF', 'any of the above', 'visible', 'yellow "content hidden" banner', 'Agent Chat & demo stop persisting'],
              ].map((row, i) => (
                <tr key={i}>
                  {row.map((cell, j) => {
                    const cellColor =
                      j === 0 && cell === 'ON'
                        ? '#3fb9a0'
                        : j === 0 && cell === 'OFF'
                        ? colors.accentOrange
                        : colors.textSecondary;
                    return (
                      <td
                        key={j}
                        style={{
                          padding: '0.4rem 0.6rem',
                          color: cellColor,
                          borderBottom:
                            i < 3 ? `1px solid rgba(255,255,255,0.05)` : 'none',
                          fontFamily: j === 0 ? 'monospace' : 'inherit',
                          fontWeight: j === 0 ? 600 : 400,
                        }}
                      >
                        {cell}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Key rotation note */}
      <div
        style={{
          padding: '0.75rem 1rem',
          background: 'rgba(88,166,255,0.04)',
          border: '1px solid rgba(88,166,255,0.15)',
          borderRadius: 6,
          fontSize: '0.75rem',
          color: colors.textSecondary,
          lineHeight: 1.6,
        }}
      >
        <strong style={{ color: colors.textPrimary }}>Key rotation.</strong> Replace the Secrets
        Manager value, then run{' '}
        <Code>aws ecs update-service --force-new-deployment</Code> so a new ECS task-set pulls the
        new value (Fargate caches tag→digest resolution per task-set, not per task launch — same
        reason you see the task-set digest-pinning row in the README's Troubleshooting table).
        New writes use the new key; existing rows written under the old key stop decrypting
        cleanly and render the <Code>"[encrypted content — decryption failed]"</Code> placeholder.
        There is intentionally no automatic re-encryption — a one-off backfill script that loads
        both keys and rewrites rows belongs with the operator, not with this app.
      </div>
    </div>
  );
}

export default function ArchitectureTab() {
  return (
    <div style={{ maxWidth: 1000 }}>
      {/* Info banner */}
      <div
        style={{
          marginBottom: '1.25rem',
          padding: '1rem 1.25rem',
          background: 'rgba(88,166,255,0.04)',
          border: '1px solid rgba(88,166,255,0.12)',
          borderRadius: 10,
        }}
      >
        <div style={{ fontSize: '0.8125rem', color: colors.textSecondary, lineHeight: 1.6 }}>
          Architecture reference. Every diagram on this tab is authored inline (React + inline
          SVG), so what you see here always matches the code that just deployed. The{' '}
          <strong>System Architecture</strong>, <strong>Data Flow</strong>,{' '}
          <strong>Agent Invocation</strong>, <strong>Billed Cost Pipeline</strong>, and{' '}
          <strong>Per-Category Agents</strong> sections cover the AWS service topology; the{' '}
          <strong>Prompt History &amp; At-Rest Encryption</strong> section covers the toggle flow,
          encryption envelope, gatekeeper, and read/write paths.
        </div>
      </div>

      <SystemArchitectureDiagram />
      <DataFlowDiagram />
      <AgentInvocationDiagram />
      <BilledCostPipelineDiagram />
      <PerCategoryAgentsDiagram />

      <div
        style={{
          margin: '1.5rem 0',
          padding: 0,
          borderTop: `1px solid rgba(255,255,255,0.08)`,
        }}
      />

      <PromptHistoryArchitecture />
    </div>
  );
}
