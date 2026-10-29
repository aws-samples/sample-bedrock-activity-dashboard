// Shared TypeScript DTOs and API types
// Bedrock Activity Dashboard

import type { CostAccountabilityBlock, CostBasis } from './governance';

/** Activity record from CloudTrail */
export interface ActivityRecord {
  id: string;
  timestamp: string;
  userIdentity: string;
  userName: string;
  modelId: string;
  sourceApplication: string;
  sourceIp: string;
  inputTokens: number;
  outputTokens: number;
  cacheReadTokens: number;
  cacheWriteTokens: number;
  totalTokens: number;
  costEstimate: number | null;
  // --- Additive multi-region ingestion fields (F1) ---
  // The region this record was fetched FROM (which region's log group/S3
  // prefix produced it — the dashboard's ingestion scope). Absent on Agent
  // Chat records and on records from backends that predate multi-region
  // ingestion.
  region?: string;
  // True when the raw model reference carried a cross-region inference
  // prefix (us./eu./ap./global.) — Bedrock may have routed the actual
  // inference to a different specific region within that geography than
  // `region`. Best-effort signal, not an authoritative resolved region.
  usedCrossRegionInference?: boolean;
  // --- Additive cost-governance attribution dimensions ---
  // Resolved from a tagged inference-profile ARN; "unknown" when unresolved,
  // absent from the record on backends that predate the governance feature.
  environment?: string;
  application?: string;
  attributedTeam?: string;
  // Extended tag taxonomy (additive; same "unknown" convention as above).
  agentName?: string;
  costCenter?: string;
  useCase?: string;
  owner?: string;
  project?: string;
}

/** Overview summary */
export interface OverviewSummary {
  totalInvocations: number;
  totalTokens: number;
  totalInputTokens: number;
  totalOutputTokens: number;
  totalCacheReadTokens: number;
  totalCacheWriteTokens: number;
  totalCostEstimate: number | null;
  activeUsers: number;
  activeModels: number;
  dateRange: { start: string; end: string };
  /**
   * Additive cost-accountability block (Req 2.4). Always present on newer
   * backends; optional here so the type stays compatible with responses that
   * predate the governance feature.
   */
  costAccountability?: CostAccountabilityBlock;
}

/**
 * A single model's cost/invocation totals within a breakdown (either the
 * per-category outer-ring rows, or the standalone top-level model totals).
 */
export interface CategoryModelRow {
  modelId: string;
  cost: number;
  invocationCount: number;
}

/**
 * One category's spend + its own model breakdown — powers the inner ring
 * (category) and outer ring (model) of the nested donut on the Overview tab.
 * `category` is either a content-type bucket (e.g. "Coding", "SQL / Database")
 * or, when no content match was found, the record's resolved Application tag
 * value, or "Uncategorized" as the final fallback — see
 * `prompt_category_service.resolve_category` on the backend.
 */
export interface CategorySpendRow {
  category: string;
  cost: number;
  invocationCount: number;
  byModel: CategoryModelRow[];
  /** Models beyond the per-category top-N, collapsed into one bucket. */
  otherModelCost: number;
  otherModelInvocationCount: number;
}

/** Response shape for GET /api/activity/categories. */
export interface SpendBreakdownResponse {
  /** Always "estimated" (Tier B) — matches every other Overview-tab figure. */
  basis: string;
  byCategory: CategorySpendRow[];
  /** Categories beyond the top-N, collapsed into one bucket. */
  otherCategoryCost: number;
  otherCategoryInvocationCount: number;
  /** Independent of byCategory — totals across ALL records, not derived by
   * summing byCategory[].byModel (each category's byModel is already
   * truncated to its own top-N). */
  byModel: CategoryModelRow[];
  /** Models beyond the top-level top-N, collapsed into one bucket. */
  otherModelCost: number;
  otherModelInvocationCount: number;
}

/** Token usage per user */
export interface UserTokenUsage {
  userIdentity: string;
  userName: string;
  inputTokens: number;
  outputTokens: number;
  cacheReadTokens: number;
  cacheWriteTokens: number;
  totalTokens: number;
  costEstimate: number | null;
}

/** Daily token breakdown */
export interface DailyTokenUsage {
  date: string;
  inputTokens: number;
  outputTokens: number;
  cacheReadTokens: number;
  cacheWriteTokens: number;
}

/** Model usage summary */
export interface ModelUsageSummary {
  modelId: string;
  invocationCount: number;
  inputTokens: number;
  outputTokens: number;
  cacheReadTokens: number;
  cacheWriteTokens: number;
  totalTokens: number;
  costEstimate: number | null;
  percentageOfTotal: number;
}

/** User-to-model matrix cell */
export interface UserModelCell {
  userIdentity: string;
  userName: string;
  modelId: string;
  invocationCount: number;
  costEstimate: number | null;
}

/** Cost analysis */
export interface CostAnalysis {
  dailyCosts: { date: string; costEstimate: number }[];
  perUserCosts: { userName: string; costEstimate: number; percentageOfTotal: number }[];
  perModelCosts: { modelId: string; costEstimate: number; percentageOfTotal: number }[];
  totalCost: number;
  accountCost: number;
  // --- Additive cost-accountability fields (Req 2.3/2.4/2.5) ---
  // Parallel to the estimated total; present only on governance-aware backends,
  // optional here so the existing estimated-only view is unchanged when absent.
  /** Tier label for the surfaced total (Req 2.4). */
  costBasis?: CostBasis;
  /** Tier A billed total (scoped), or null when CUR absent. */
  billedCost?: number | null;
  /** Reconciled total (Tier B scaled to billed), or null when not applied. */
  reconciledCost?: number | null;
  /** billed - estimated when billed is present (Req 2.3). */
  variance?: number | null;
}

/** Prompt history entry (list view) */
export interface PromptHistoryEntry {
  id: string;
  timestamp: string;
  userName: string;
  modelId: string;
  sourceApplication: string;
  inputTokens: number;
  outputTokens: number;
  cacheReadTokens: number;
  cacheWriteTokens: number;
  costEstimate: number | null;
}

/** Prompt detail (full view).
 *
 * ``promptBodyHidden`` is set by the backend when the admin Prompt History
 * toggle is OFF — the metadata (timestamp, user, model, cost, tokens) is
 * still returned but ``promptText`` and ``responseText`` come back as
 * empty strings. The UI uses this flag to render a "content hidden" state
 * explicitly instead of showing an empty bubble.
 */
export interface PromptDetail extends PromptHistoryEntry {
  promptText: string;
  responseText: string;
  promptBodyHidden?: boolean;
}

/** Paginated response */
export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  page: number;
  pageSize: number;
  totalPages: number;
}

/** Team */
export interface Team {
  id: string;
  name: string;
  description: string | null;
  memberCount: number;
  members: string[];
  totalTokens: number;
  totalCostEstimate: number | null;
}

/** Model pricing configuration */
export interface ModelPricingConfig {
  modelId: string;
  inputTokenPrice: number;
  outputTokenPrice: number;
  cacheReadTokenPrice: number;
  cacheWriteTokenPrice: number;
  isDefault: boolean;
  // Derived at read time (flat ~50% discount off on-demand) — see
  // backend/app/services/cost_service.py calculate_batch_price. Optional so
  // this type stays compatible with backends that predate batch pricing.
  batchInputTokenPrice?: number;
  batchOutputTokenPrice?: number;
}

/** Old/new value for a single changed price dimension (per-1K prices) */
export interface PricingChange {
  old: number;
  new: number;
}

/** A model whose pricing changed during a sync */
export interface UpdatedModel {
  modelId: string;
  servicename: string;
  changes: Record<string, PricingChange>;
}

/**
 * Response from GET /api/pricing/usage-gaps: distinct models actually invoked
 * in the window, and which of those have no resolvable pricing row today
 * (would show cost as "N/A" right now).
 */
export interface PricingGapsResponse {
  usedModelIds: string[];
  modelsMissingPricing: string[];
  windowStart: string;
  windowEnd: string;
}

/** Summary returned by POST /api/pricing/sync */
export interface PricingSyncSummary {
  modelsUpdated: UpdatedModel[];
  modelsUnchanged: string[];
  modelsCreated: string[];
  /** AWS Bedrock models that carry token prices but aren't tracked by this dashboard (expected noise). */
  unmatchedServiceNames: string[];
  /** Tracked model_ids for which AWS returned no mappable price — the actionable signal. */
  trackedModelsMissingPrice: string[];
  errors: string[];
  pricingSource: string;
  syncedAt: string;
}

/** Dashboard filter state */
export interface DashboardFilters {
  dateRange: { start: string; end: string };
  userIdentities: string[];
  teamId: string | null;
  sourceApplication: string | null;
  /**
   * Regions to filter to (matches each record's ingestion region — see
   * ActivityRecord.region). Optional and defaults to empty (no filter, all
   * ingested regions included) so existing callers/tests that construct a
   * DashboardFilters literal without this field keep compiling.
   */
  regions?: string[];
}

/** CSV export request */
export interface ExportRequest {
  section: "token-usage" | "model-usage" | "cost-analysis" | "prompt-history";
  filters: DashboardFilters;
}

/** Agent chat request */
export interface AgentChatRequest {
  sessionId: string | null;
  message: string;
  filters: DashboardFilters;
}

/** Agent chat response with optional extended thinking */
export interface AgentChatResponse {
  sessionId: string;
  message: string;
  thinking: string | null;
  thinkingUnavailable: boolean;
  /** True when the dashboard's managed Bedrock Guardrail blocked or
   * anonymized content in this turn. */
  guardrailIntervened: boolean;
  timestamp: string;
}

/** Cognito sign-in settings exposed to the SPA at runtime (no build-time bake). */
export interface CognitoRuntimeConfig {
  userPoolId: string;
  clientId: string;
  domain: string;
  region: string;
}

/** Runtime configuration exposed by GET /api/health/config */
export interface HealthConfig {
  demoMode: boolean;
  appName: string;
  /** True when persisted demo records exist (drives the demo banner). */
  demoDataPresent: boolean;
  demoRecordCount: number;
  cognito: CognitoRuntimeConfig;
  /**
   * Regions the backend fans Bedrock activity ingestion out across
   * (CLOUDTRAIL_REGIONS). Drives the region filter dropdown so the SPA
   * doesn't need a rebuild when the deployed region set changes. Optional
   * so this type stays compatible with backends that predate the region
   * filter — treat an absent/empty value as "no region filter available".
   */
  availableRegions?: string[];
}

/** Options for generating a demo data batch (POST /api/demo/generate) */
export interface GenerateDemoOptions {
  count?: number;
  /** When true, a portion of generated records are routed through tagged
   * application inference profiles (get-or-created by the backend) so Cost
   * Governance's Spend-by-Application view has real data to show. */
  withApplications?: boolean;
}

/** A per-model invocation failure from demo generation */
export interface DemoFailure {
  model: string;
  error: string;
}

/** Summary returned by POST /api/demo/generate */
export interface GenerateDemoSummary {
  requested: number;
  succeeded: number;
  failed: DemoFailure[];
  batchId: string;
  /** Number of successful invocations the dashboard's managed guardrail
   * blocked/anonymized content on (0 when no guardrail has been
   * approved+published yet). */
  guardrailIntervenedCount: number;
  /** Application names of any tagged inference profile actually used by at
   * least one succeeded invocation in this batch. Empty when
   * `withApplications` was not requested or tagged-profile setup failed. */
  applicationProfilesUsed: string[];
}

/** Result of POST /api/demo/clear */
export interface ClearDemoResult {
  deleted: number;
}

/** Status from GET /api/demo/status */
export interface DemoStatus {
  recordCount: number;
  realCount: number;
  lastGeneratedAt: string | null;
  lastBatchId: string | null;
}

/** Summary report */
export interface SummaryReport {
  id: string;
  periodStart: string;
  periodEnd: string;
  reportType: "daily" | "weekly";
  usageTrends: {
    totalTokensDelta: number;
    totalCostDelta: number;
    activeUsersDelta: number;
  };
  createdAt: string;
}


