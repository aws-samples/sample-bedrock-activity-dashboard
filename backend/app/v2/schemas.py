"""Pydantic schemas for the V2 Agent Platform."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Shared / utility schemas
# ---------------------------------------------------------------------------

class FindingFilters(BaseModel):
    """Query filters for agent findings."""
    date_start: Optional[datetime] = None
    date_end: Optional[datetime] = None
    status: Optional[str] = None
    severity: Optional[str] = None
    agent_name: Optional[str] = None


class ApprovalAction(BaseModel):
    """Payload for approving or rejecting a finding."""
    status: str = Field(..., pattern=r"^(approved|rejected)$")
    rejection_reason: Optional[str] = None


class AgentExecutionTrigger(BaseModel):
    """Payload for manually triggering an agent execution."""
    trigger_type: str = Field(default="manual", pattern=r"^(scheduled|manual)$")


# ---------------------------------------------------------------------------
# Agent config schemas
# ---------------------------------------------------------------------------

class AgentConfigBase(BaseModel):
    display_name: str
    category: str
    model_id: str
    schedule_cron: Optional[str] = None
    enabled: bool = True


class AgentConfigCreate(AgentConfigBase):
    agent_name: str


class AgentConfigUpdate(BaseModel):
    display_name: Optional[str] = None
    category: Optional[str] = None
    model_id: Optional[str] = None
    schedule_cron: Optional[str] = None
    enabled: Optional[bool] = None


class AgentConfigRead(AgentConfigBase):
    id: UUID
    agent_name: str
    is_agentic: bool = False
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Agent execution schemas
# ---------------------------------------------------------------------------

class AgentExecutionRead(BaseModel):
    id: UUID
    agent_name: str
    trigger_type: str
    start_time: datetime
    end_time: Optional[datetime] = None
    status: str
    finding_count: int = 0
    error_details: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Policy schemas
# ---------------------------------------------------------------------------

class PolicyBase(BaseModel):
    policy_name: str
    policy_type: str
    policy_rules: Any
    enabled: bool = True


class PolicyCreate(PolicyBase):
    pass


class PolicyUpdate(BaseModel):
    policy_name: Optional[str] = None
    policy_type: Optional[str] = None
    policy_rules: Optional[Any] = None
    enabled: Optional[bool] = None


class PolicyRead(PolicyBase):
    id: UUID
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Residency policy schemas
# ---------------------------------------------------------------------------

class ResidencyPolicyBase(BaseModel):
    team_id: Optional[UUID] = None
    approved_regions: list[str]


class ResidencyPolicyCreate(ResidencyPolicyBase):
    pass


class ResidencyPolicyUpdate(BaseModel):
    team_id: Optional[UUID] = None
    approved_regions: Optional[list[str]] = None


class ResidencyPolicyRead(ResidencyPolicyBase):
    id: UUID
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Finding base (common columns shared by all agent finding tables)
# ---------------------------------------------------------------------------

class FindingBase(BaseModel):
    agent_name: str
    status: str
    severity: str


class FindingRead(FindingBase):
    id: UUID
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# 3. PII findings
# ---------------------------------------------------------------------------

class PiiFindingCreate(FindingBase):
    source_record_id: str
    pii_type: str
    confidence_score: float = Field(..., ge=0.0, le=1.0)
    detection_tier: str = Field(..., pattern=r"^(regex|ai)$")
    text_excerpt: Optional[str] = None
    user_identity: Optional[str] = None


class PiiFindingRead(FindingRead):
    source_record_id: str
    pii_type: str
    confidence_score: float
    detection_tier: str
    text_excerpt: Optional[str] = None
    user_identity: Optional[str] = None


# ---------------------------------------------------------------------------
# 4. Cost anomalies
# ---------------------------------------------------------------------------

class CostAnomalyCreate(FindingBase):
    anomaly_date: date
    current_spend: Decimal
    rolling_average: Decimal
    threshold_multiplier: float
    contributing_users: Optional[Any] = None
    team_id: Optional[UUID] = None


class CostAnomalyRead(FindingRead):
    anomaly_date: date
    current_spend: Decimal
    rolling_average: Decimal
    threshold_multiplier: float
    contributing_users: Optional[Any] = None
    team_id: Optional[UUID] = None


# ---------------------------------------------------------------------------
# 5. Guardrail recommendations
# ---------------------------------------------------------------------------

class GuardrailRecommendationCreate(FindingBase):
    recommendation: Any
    based_on_findings: Optional[Any] = None
    rejection_reason: Optional[str] = None


class GuardrailRecommendationRead(FindingRead):
    recommendation: Any
    based_on_findings: Optional[Any] = None
    rejection_reason: Optional[str] = None
    guardrail_id: Optional[str] = None
    guardrail_arn: Optional[str] = None
    guardrail_version: Optional[str] = None
    creation_error: Optional[str] = None


# ---------------------------------------------------------------------------
# 6. Model cost recommendations
# ---------------------------------------------------------------------------

class ModelCostRecommendationCreate(FindingBase):
    current_model: str
    suggested_model: str
    estimated_monthly_savings: Decimal = Field(..., gt=0)
    affected_user_count: int = Field(..., ge=1)
    usage_pattern_summary: Optional[str] = None


class ModelCostRecommendationRead(FindingRead):
    current_model: str
    suggested_model: str
    estimated_monthly_savings: Decimal
    affected_user_count: int
    usage_pattern_summary: Optional[str] = None


# ---------------------------------------------------------------------------
# 7. Prompt quality findings
# ---------------------------------------------------------------------------

class PromptQualityFindingCreate(FindingBase):
    user_identity: str
    anti_pattern_type: str
    prompt_excerpt: Optional[str] = None
    improvement_suggestion: Optional[str] = None
    quality_score: Optional[int] = Field(default=None, ge=0, le=100)


class PromptQualityFindingRead(FindingRead):
    user_identity: str
    anti_pattern_type: str
    prompt_excerpt: Optional[str] = None
    improvement_suggestion: Optional[str] = None
    quality_score: Optional[int] = None


# ---------------------------------------------------------------------------
# 8. Model adoption findings
# ---------------------------------------------------------------------------

class ModelAdoptionFindingCreate(FindingBase):
    source_model: str
    target_model: str
    capability_comparison: Optional[Any] = None
    cost_delta: Optional[Decimal] = None
    recommendation_rationale: Optional[str] = None
    pricing_available: bool = True


class ModelAdoptionFindingRead(FindingRead):
    source_model: str
    target_model: str
    capability_comparison: Optional[Any] = None
    cost_delta: Optional[Decimal] = None
    recommendation_rationale: Optional[str] = None
    pricing_available: bool


# ---------------------------------------------------------------------------
# 9. Usage forecasts
# ---------------------------------------------------------------------------

class UsageForecastCreate(FindingBase):
    forecast_period_days: int
    projected_tokens: Optional[int] = None
    projected_cost: Optional[Decimal] = None
    confidence_lower: Optional[Decimal] = None
    confidence_upper: Optional[Decimal] = None
    methodology: Optional[str] = None
    breakdown_by_team: Optional[Any] = None
    breakdown_by_model: Optional[Any] = None


class UsageForecastRead(FindingRead):
    forecast_period_days: int
    projected_tokens: Optional[int] = None
    projected_cost: Optional[Decimal] = None
    confidence_lower: Optional[Decimal] = None
    confidence_upper: Optional[Decimal] = None
    methodology: Optional[str] = None
    breakdown_by_team: Optional[Any] = None
    breakdown_by_model: Optional[Any] = None


# ---------------------------------------------------------------------------
# 10. Residency violations
# ---------------------------------------------------------------------------

class ResidencyViolationCreate(FindingBase):
    user_identity: str
    invocation_timestamp: datetime
    target_region: str
    approved_regions: list[str]
    violation_severity: str = Field(..., pattern=r"^(warning|critical)$")


class ResidencyViolationRead(FindingRead):
    user_identity: str
    invocation_timestamp: datetime
    target_region: str
    approved_regions: list[str]
    violation_severity: str


# ---------------------------------------------------------------------------
# 11. Latency / error findings
# ---------------------------------------------------------------------------

class LatencyErrorFindingCreate(FindingBase):
    metric_type: str = Field(..., pattern=r"^(latency_spike|error_rate)$")
    current_value: float
    baseline_value: float
    affected_model: Optional[str] = None
    affected_region: Optional[str] = None


class LatencyErrorFindingRead(FindingRead):
    metric_type: str
    current_value: float
    baseline_value: float
    affected_model: Optional[str] = None
    affected_region: Optional[str] = None


# ---------------------------------------------------------------------------
# 12. Chargeback reports
# ---------------------------------------------------------------------------

class ChargebackReportCreate(FindingBase):
    report_period_start: date
    report_period_end: date
    team_name: str
    total_cost: Decimal
    cost_breakdown: Optional[Any] = None


# NOTE: ChargebackReportRead is defined below under section 19 (Chargeback
# report schemas); the finding-style variant that previously lived here was an
# unused shadowed duplicate and has been removed.


# ---------------------------------------------------------------------------
# 13. Onboarding findings
# ---------------------------------------------------------------------------

class OnboardingFindingCreate(FindingBase):
    user_identity: str
    team_assignment: Optional[str] = None
    guidance_content: Optional[str] = None


class OnboardingFindingRead(FindingRead):
    user_identity: str
    team_assignment: Optional[str] = None
    guidance_content: Optional[str] = None


# ---------------------------------------------------------------------------
# 14. Policy violations
# ---------------------------------------------------------------------------

class PolicyViolationCreate(FindingBase):
    policy_rule_id: UUID
    user_identity: str
    invocation_details: Optional[Any] = None
    violation_severity: str = Field(..., pattern=r"^(warning|critical)$")


class PolicyViolationRead(FindingRead):
    policy_rule_id: UUID
    user_identity: str
    invocation_details: Optional[Any] = None
    violation_severity: str


# ---------------------------------------------------------------------------
# 17. Digest findings
# ---------------------------------------------------------------------------

class DigestFindingCreate(FindingBase):
    digest_period_start: date
    digest_period_end: date
    included_agent_summaries: Optional[Any] = None
    delivery_status: str = Field(..., pattern=r"^(sent|failed|partial)$")
    recipient_list: Optional[Any] = None
    failed_agents: Optional[Any] = None


class DigestFindingRead(FindingRead):
    digest_period_start: date
    digest_period_end: date
    included_agent_summaries: Optional[Any] = None
    delivery_status: str
    recipient_list: Optional[Any] = None
    failed_agents: Optional[Any] = None


# ---------------------------------------------------------------------------
# 18. CSV Upload schemas (chargeback)
# ---------------------------------------------------------------------------

class CsvUploadRead(BaseModel):
    id: UUID
    filename: str
    billing_period: Optional[str] = None
    status: str
    error_message: Optional[str] = None
    warning_message: Optional[str] = None
    uploaded_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# 19. Chargeback report schemas
# ---------------------------------------------------------------------------

class ChargebackReportLineRead(BaseModel):
    entity_type: str
    entity_name: str
    estimated_cost: Decimal
    allocated_actual_cost: Decimal
    variance: Decimal
    percentage_share: Decimal


class ChargebackReportRead(BaseModel):
    id: UUID
    upload_id: UUID
    billing_period: str
    total_actual_cost: Decimal
    total_estimated_cost: Decimal
    total_variance: Decimal
    insights: Optional[str] = None
    generated_at: datetime
    lines: list[ChargebackReportLineRead] = []

    model_config = {"from_attributes": True}
