/** V2 Agent Platform TypeScript interfaces. */

// Agent configuration
export interface AgentConfig {
  id: string;
  agent_name: string;
  display_name: string;
  category: string;
  model_id: string;
  schedule_cron: string | null;
  enabled: boolean;
  is_agentic: boolean;
  created_at: string;
  updated_at: string;
}

export interface AgentConfigUpdate {
  display_name?: string;
  category?: string;
  model_id?: string;
  schedule_cron?: string | null;
  enabled?: boolean;
}

// Agent execution
export interface AgentExecution {
  id: string;
  agent_name: string;
  trigger_type: 'scheduled' | 'manual';
  start_time: string;
  end_time: string | null;
  status: 'success' | 'failure' | 'timeout' | 'running' | 'skipped';
  finding_count: number;
  error_details: string | null;
  created_at: string;
}

// Finding filters
export interface FindingFilters {
  date_start?: string;
  date_end?: string;
  status?: string;
  severity?: string;
  agent_name?: string;
}

// Finding summary (per agent)
export interface AgentFindingSummary {
  [agent_name: string]: number;
}

// Approval action
export interface ApprovalAction {
  status: 'approved' | 'rejected';
  rejection_reason?: string;
}

// Response from PATCH /v2/agents/findings/{id}
export interface ApprovalResult {
  id: string;
  agent_name: string;
  status: string;
  updated_at: string;
  rejection_reason: string | null;
  /** Only present for guardrail-recommender findings. */
  guardrail_id?: string | null;
  guardrail_arn?: string | null;
  guardrail_version?: string | null;
  guardrail_published_version?: string | null;
  creation_error?: string | null;
}

// Base finding (common fields)
export interface BaseFinding {
  id: string;
  agent_name: string;
  status: string;
  severity: string;
  created_at: string;
  updated_at: string;
}

// Per-agent finding types
export interface PiiFinding extends BaseFinding {
  source_record_id: string;
  pii_type: string;
  confidence_score: number;
  detection_tier: 'regex' | 'ai';
  text_excerpt: string | null;
  user_identity: string | null;
}

export interface CostAnomaly extends BaseFinding {
  anomaly_date: string;
  current_spend: number;
  rolling_average: number;
  threshold_multiplier: number;
  contributing_users: Record<string, unknown>[] | null;
  team_id: string | null;
}

export interface GuardrailRecommendation extends BaseFinding {
  recommendation: Record<string, unknown>;
  based_on_findings: string[] | null;
  rejection_reason: string | null;
  /** Populated once approving this finding created/updated a real Bedrock Guardrail. */
  guardrail_id: string | null;
  guardrail_arn: string | null;
  guardrail_version: string | null;
  /** The stable, numbered version published after create/update (never
   * DRAFT) — what Agent Chat / Demo Data enforcement pins to. */
  guardrail_published_version: string | null;
  /** Populated instead, when the real Bedrock call failed on approval. */
  creation_error: string | null;
}

export interface ModelCostRecommendation extends BaseFinding {
  current_model: string;
  suggested_model: string;
  estimated_monthly_savings: number;
  affected_user_count: number;
  usage_pattern_summary: string | null;
  /** True when usage also looks like a good model-distillation candidate
   * (narrow, repetitive, high-volume task driven by few callers). Optional
   * since older persisted findings predate this field. */
  is_distillation_candidate?: boolean;
}

export interface PromptQualityFinding extends BaseFinding {
  user_identity: string;
  anti_pattern_type: string;
  prompt_excerpt: string | null;
  improvement_suggestion: string | null;
  quality_score: number | null;
}

export interface ModelAdoptionFinding extends BaseFinding {
  source_model: string;
  target_model: string;
  capability_comparison: Record<string, unknown> | null;
  cost_delta: number | null;
  recommendation_rationale: string | null;
  pricing_available: boolean;
}

export interface UsageForecast extends BaseFinding {
  forecast_period_days: number;
  projected_tokens: number | null;
  projected_cost: number | null;
  confidence_lower: number | null;
  confidence_upper: number | null;
  methodology: string | null;
  breakdown_by_team: Record<string, unknown> | null;
  breakdown_by_model: Record<string, unknown> | null;
}

export interface ResidencyViolation extends BaseFinding {
  user_identity: string;
  invocation_timestamp: string;
  target_region: string;
  approved_regions: string[];
  violation_severity: 'warning' | 'critical';
}

export interface LatencyErrorFinding extends BaseFinding {
  metric_type: 'latency_spike' | 'error_rate';
  current_value: number;
  baseline_value: number;
  affected_model: string | null;
  affected_region: string | null;
}

export interface ChargebackReport extends BaseFinding {
  report_period_start: string;
  report_period_end: string;
  team_name: string;
  total_cost: number;
  cost_breakdown: Record<string, unknown> | null;
}

export interface OnboardingFinding extends BaseFinding {
  user_identity: string;
  team_assignment: string | null;
  guidance_content: string | null;
}

export interface PolicyViolation extends BaseFinding {
  policy_rule_id: string;
  user_identity: string;
  invocation_details: Record<string, unknown> | null;
  violation_severity: 'warning' | 'critical';
}

export interface DigestFinding extends BaseFinding {
  digest_period_start: string;
  digest_period_end: string;
  included_agent_summaries: Record<string, unknown> | null;
  delivery_status: 'sent' | 'failed' | 'partial';
  recipient_list: string[] | null;
  failed_agents: string[] | null;
}

// Policy types
export interface Policy {
  id: string;
  policy_name: string;
  policy_type: string;
  policy_rules: Record<string, unknown>;
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

export interface ResidencyPolicy {
  id: string;
  team_id: string | null;
  approved_regions: string[];
  created_at: string;
  updated_at: string;
}

export interface GenericFinding extends BaseFinding {
  title: string;
  details: Record<string, unknown> | null;
}

// Union type for any finding
export type AgentFinding =
  | PiiFinding
  | CostAnomaly
  | GuardrailRecommendation
  | ModelCostRecommendation
  | PromptQualityFinding
  | ModelAdoptionFinding
  | UsageForecast
  | ResidencyViolation
  | LatencyErrorFinding
  | ChargebackReport
  | OnboardingFinding
  | PolicyViolation
  | DigestFinding
  | GenericFinding;


// Chargeback types
export interface UploadRecord {
  id: string;
  filename: string;
  billing_period: string | null;
  status: string;
  error_message: string | null;
  warning_message: string | null;
  uploaded_at: string;
}

export interface ChargebackReportLine {
  entity_type: string;
  entity_name: string;
  estimated_cost: number;
  allocated_actual_cost: number;
  variance: number;
  percentage_share: number;
}

export interface ChargebackReportDetail {
  id: string;
  upload_id: string;
  billing_period: string;
  total_actual_cost: number;
  total_estimated_cost: number;
  total_variance: number;
  insights: string | null;
  generated_at: string;
  lines: ChargebackReportLine[];
}
