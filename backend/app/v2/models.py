"""SQLAlchemy models for the V2 Agent Platform."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# 1. v2_agent_configs — agent configuration
# ---------------------------------------------------------------------------
class V2AgentConfig(Base):
    __tablename__ = "v2_agent_configs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_name = Column(String(100), unique=True, nullable=False)
    display_name = Column(String(255), nullable=False)
    category = Column(String(100), nullable=False)
    model_id = Column(String(512), nullable=False)
    schedule_cron = Column(String(100), nullable=True)
    enabled = Column(Boolean, default=True, nullable=False)
    agentcore_agent_id = Column(String(255), nullable=True)
    is_agentic = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


# ---------------------------------------------------------------------------
# 2. v2_agent_executions — execution history
# ---------------------------------------------------------------------------
class V2AgentExecution(Base):
    __tablename__ = "v2_agent_executions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_name = Column(String(100), nullable=False)
    trigger_type = Column(String(50), nullable=False)  # "scheduled" or "manual"
    start_time = Column(DateTime(timezone=True), nullable=False)
    end_time = Column(DateTime(timezone=True), nullable=True)
    status = Column(String(50), nullable=False)  # success, failure, timeout
    finding_count = Column(Integer, default=0, nullable=False)
    error_details = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)


# ---------------------------------------------------------------------------
# 3. v2_agent_pii_findings — PII detection findings
# ---------------------------------------------------------------------------
class V2AgentPiiFinding(Base):
    __tablename__ = "v2_agent_pii_findings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_name = Column(String(100), nullable=False)
    status = Column(String(50), nullable=False)
    severity = Column(String(20), nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    source_record_id = Column(String(255), nullable=False)
    pii_type = Column(String(100), nullable=False)
    confidence_score = Column(Float, nullable=False)
    detection_tier = Column(String(20), nullable=False)  # "regex" or "ai"
    text_excerpt = Column(Text, nullable=True)
    user_identity = Column(String(512), nullable=True)

    __table_args__ = (
        UniqueConstraint("source_record_id", "pii_type", name="uq_pii_source_type"),
    )


# ---------------------------------------------------------------------------
# 4. v2_agent_cost_anomalies — cost anomaly findings
# ---------------------------------------------------------------------------
class V2AgentCostAnomaly(Base):
    __tablename__ = "v2_agent_cost_anomalies"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_name = Column(String(100), nullable=False)
    status = Column(String(50), nullable=False)
    severity = Column(String(20), nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    anomaly_date = Column(Date, nullable=False)
    current_spend = Column(Numeric(20, 4), nullable=False)
    rolling_average = Column(Numeric(20, 4), nullable=False)
    threshold_multiplier = Column(Float, nullable=False)
    contributing_users = Column(JSON, nullable=True)
    team_id = Column(UUID(as_uuid=True), nullable=True)


# ---------------------------------------------------------------------------
# 5. v2_agent_guardrail_recommendations — guardrail suggestions
# ---------------------------------------------------------------------------
class V2AgentGuardrailRecommendation(Base):
    __tablename__ = "v2_agent_guardrail_recommendations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_name = Column(String(100), nullable=False)
    status = Column(String(50), nullable=False, default="pending_review")
    severity = Column(String(20), nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    recommendation = Column(JSON, nullable=False)
    based_on_findings = Column(JSON, nullable=True)
    rejection_reason = Column(Text, nullable=True)
    # Populated when approving this finding results in a real Bedrock
    # Guardrail being created/updated via the CreateGuardrail/UpdateGuardrail
    # control-plane API (see GuardrailCreationService). NULL until approved.
    guardrail_id = Column(String(64), nullable=True)
    guardrail_arn = Column(String(2048), nullable=True)
    guardrail_version = Column(String(20), nullable=True)
    # Populated instead of the above when the real Bedrock call fails on
    # approval — the finding is still marked "approved" (the human decision
    # stands) but the guardrail itself was not created; this surfaces why.
    creation_error = Column(Text, nullable=True)
    # The numbered version published immediately after create/update (never
    # DRAFT — DRAFT keeps changing as more findings get approved). This is
    # what the dashboard's own Bedrock calls (Agent Chat, Demo Data) pin to
    # via guardrailConfig, so their enforcement doesn't shift underneath them
    # mid-conversation as new recommendations are approved.
    guardrail_published_version = Column(String(20), nullable=True)


# ---------------------------------------------------------------------------
# 6. v2_agent_model_cost_recommendations — model cost optimization
# ---------------------------------------------------------------------------
class V2AgentModelCostRecommendation(Base):
    __tablename__ = "v2_agent_model_cost_recommendations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_name = Column(String(100), nullable=False)
    status = Column(String(50), nullable=False)
    severity = Column(String(20), nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    current_model = Column(String(512), nullable=False)
    suggested_model = Column(String(512), nullable=False)
    estimated_monthly_savings = Column(Numeric(20, 4), nullable=False)
    affected_user_count = Column(Integer, nullable=False)
    usage_pattern_summary = Column(Text, nullable=True)


# ---------------------------------------------------------------------------
# 7. v2_agent_prompt_quality_findings — prompt quality findings
# ---------------------------------------------------------------------------
class V2AgentPromptQualityFinding(Base):
    __tablename__ = "v2_agent_prompt_quality_findings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_name = Column(String(100), nullable=False)
    status = Column(String(50), nullable=False)
    severity = Column(String(20), nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    user_identity = Column(String(512), nullable=False)
    anti_pattern_type = Column(String(100), nullable=False)
    prompt_excerpt = Column(Text, nullable=True)
    improvement_suggestion = Column(Text, nullable=True)
    quality_score = Column(Integer, nullable=True)


# ---------------------------------------------------------------------------
# 8. v2_agent_model_adoption_findings — model adoption findings
# ---------------------------------------------------------------------------
class V2AgentModelAdoptionFinding(Base):
    __tablename__ = "v2_agent_model_adoption_findings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_name = Column(String(100), nullable=False)
    status = Column(String(50), nullable=False)
    severity = Column(String(20), nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    source_model = Column(String(512), nullable=False)
    target_model = Column(String(512), nullable=False)
    capability_comparison = Column(JSON, nullable=True)
    cost_delta = Column(Numeric(20, 4), nullable=True)
    recommendation_rationale = Column(Text, nullable=True)
    pricing_available = Column(Boolean, default=True, nullable=False)


# ---------------------------------------------------------------------------
# 9. v2_agent_usage_forecasts — usage forecast findings
# ---------------------------------------------------------------------------
class V2AgentUsageForecast(Base):
    __tablename__ = "v2_agent_usage_forecasts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_name = Column(String(100), nullable=False)
    status = Column(String(50), nullable=False)
    severity = Column(String(20), nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    forecast_period_days = Column(Integer, nullable=False)
    projected_tokens = Column(BigInteger, nullable=True)
    projected_cost = Column(Numeric(20, 4), nullable=True)
    confidence_lower = Column(Numeric(20, 4), nullable=True)
    confidence_upper = Column(Numeric(20, 4), nullable=True)
    methodology = Column(String(100), nullable=True)
    breakdown_by_team = Column(JSON, nullable=True)
    breakdown_by_model = Column(JSON, nullable=True)


# ---------------------------------------------------------------------------
# 10. v2_agent_residency_violations — data residency violations
# ---------------------------------------------------------------------------
class V2AgentResidencyViolation(Base):
    __tablename__ = "v2_agent_residency_violations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_name = Column(String(100), nullable=False)
    status = Column(String(50), nullable=False)
    severity = Column(String(20), nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    user_identity = Column(String(512), nullable=False)
    invocation_timestamp = Column(DateTime(timezone=True), nullable=False)
    target_region = Column(String(50), nullable=False)
    approved_regions = Column(JSON, nullable=False)
    violation_severity = Column(String(20), nullable=False)  # warning or critical


# ---------------------------------------------------------------------------
# 11. v2_agent_latency_error_findings — latency/error findings
# ---------------------------------------------------------------------------
class V2AgentLatencyErrorFinding(Base):
    __tablename__ = "v2_agent_latency_error_findings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_name = Column(String(100), nullable=False)
    status = Column(String(50), nullable=False)
    severity = Column(String(20), nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    metric_type = Column(String(50), nullable=False)  # "latency_spike" or "error_rate"
    current_value = Column(Float, nullable=False)
    baseline_value = Column(Float, nullable=False)
    affected_model = Column(String(512), nullable=True)
    affected_region = Column(String(50), nullable=True)


# ---------------------------------------------------------------------------
# 12. v2_agent_chargeback_reports — chargeback reports
# ---------------------------------------------------------------------------
class V2AgentChargebackReport(Base):
    __tablename__ = "v2_agent_chargeback_reports"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_name = Column(String(100), nullable=False)
    status = Column(String(50), nullable=False)
    severity = Column(String(20), nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    report_period_start = Column(Date, nullable=False)
    report_period_end = Column(Date, nullable=False)
    team_name = Column(String(255), nullable=False)
    total_cost = Column(Numeric(20, 4), nullable=False)
    cost_breakdown = Column(JSON, nullable=True)


# ---------------------------------------------------------------------------
# 13. v2_agent_onboarding_findings — onboarding findings
# ---------------------------------------------------------------------------
class V2AgentOnboardingFinding(Base):
    __tablename__ = "v2_agent_onboarding_findings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_name = Column(String(100), nullable=False)
    status = Column(String(50), nullable=False)
    severity = Column(String(20), nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    user_identity = Column(String(512), nullable=False)
    team_assignment = Column(String(255), nullable=True)
    guidance_content = Column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("user_identity", name="uq_onboarding_user"),
    )


# ---------------------------------------------------------------------------
# 14. v2_agent_policy_violations — policy violations
# ---------------------------------------------------------------------------
class V2AgentPolicyViolation(Base):
    __tablename__ = "v2_agent_policy_violations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_name = Column(String(100), nullable=False)
    status = Column(String(50), nullable=False)
    severity = Column(String(20), nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    policy_rule_id = Column(UUID(as_uuid=True), ForeignKey("v2_agent_policies.id"), nullable=False)
    user_identity = Column(String(512), nullable=False)
    invocation_details = Column(JSON, nullable=True)
    violation_severity = Column(String(20), nullable=False)  # warning or critical


# ---------------------------------------------------------------------------
# 15. v2_agent_policies — organizational policies config
# ---------------------------------------------------------------------------
class V2AgentPolicy(Base):
    __tablename__ = "v2_agent_policies"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    policy_name = Column(String(255), nullable=False)
    policy_type = Column(String(100), nullable=False)  # model_restriction, proxy_required, token_limit
    policy_rules = Column(JSON, nullable=False)
    enabled = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


# ---------------------------------------------------------------------------
# 16. v2_agent_residency_policies — data residency policies config
# ---------------------------------------------------------------------------
class V2AgentResidencyPolicy(Base):
    __tablename__ = "v2_agent_residency_policies"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id = Column(UUID(as_uuid=True), nullable=True)
    approved_regions = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


# ---------------------------------------------------------------------------
# 17. v2_agent_digest_findings — weekly digest findings
# ---------------------------------------------------------------------------
class V2AgentDigestFinding(Base):
    __tablename__ = "v2_agent_digest_findings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_name = Column(String(100), nullable=False)
    status = Column(String(50), nullable=False)
    severity = Column(String(20), nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    digest_period_start = Column(Date, nullable=False)
    digest_period_end = Column(Date, nullable=False)
    included_agent_summaries = Column(JSON, nullable=True)
    delivery_status = Column(String(50), nullable=False)  # sent, failed, partial
    recipient_list = Column(JSON, nullable=True)
    failed_agents = Column(JSON, nullable=True)


# ---------------------------------------------------------------------------
# 17b. v2_agent_generic_findings — agent-agnostic informational findings
# ---------------------------------------------------------------------------
class V2AgentGenericFinding(Base):
    """Generic finding surface for agents without a dedicated finding table.

    Used by operational/informational agents (e.g. cur-ingestion) whose runs
    produce a single status summary rather than domain-shaped findings.
    ``title`` is the human-readable summary rendered in the UI; ``details``
    carries the full agent-specific payload for the raw-data view.
    """

    __tablename__ = "v2_agent_generic_findings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_name = Column(String(100), nullable=False)
    status = Column(String(50), nullable=False)
    severity = Column(String(20), nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    title = Column(String(512), nullable=False)
    details = Column(JSON, nullable=True)


# ---------------------------------------------------------------------------
# 18. csv_uploads — uploaded Cost Explorer CSV files
# ---------------------------------------------------------------------------
class CsvUpload(Base):
    __tablename__ = "csv_uploads"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    filename = Column(String(512), nullable=False)
    billing_period = Column(String(7), nullable=True)
    csv_content = Column(Text, nullable=False)
    status = Column(String(50), nullable=False, default="pending")
    error_message = Column(Text, nullable=True)
    warning_message = Column(Text, nullable=True)
    uploaded_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)


# ---------------------------------------------------------------------------
# 19. csv_bedrock_line_items — parsed Bedrock line items from CSV
# ---------------------------------------------------------------------------
class CsvBedrockLineItem(Base):
    __tablename__ = "csv_bedrock_line_items"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    upload_id = Column(UUID(as_uuid=True), ForeignKey("csv_uploads.id", ondelete="CASCADE"), nullable=False)
    service = Column(String(255), nullable=False)
    usage_type = Column(String(512), nullable=True)
    unblended_cost = Column(Numeric(20, 10), nullable=False)
    raw_row = Column(JSON, nullable=False)


# ---------------------------------------------------------------------------
# 20. chargeback_reports — reconciliation report summaries
# ---------------------------------------------------------------------------
class ChargebackReport(Base):
    __tablename__ = "chargeback_reports"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    upload_id = Column(UUID(as_uuid=True), ForeignKey("csv_uploads.id", ondelete="CASCADE"), nullable=False, unique=True)
    billing_period = Column(String(7), nullable=False)
    total_actual_cost = Column(Numeric(20, 4), nullable=False)
    total_estimated_cost = Column(Numeric(20, 4), nullable=False)
    total_variance = Column(Numeric(20, 4), nullable=False)
    insights = Column(Text, nullable=True)
    generated_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)


# ---------------------------------------------------------------------------
# 21. chargeback_report_lines — per-entity breakdown rows
# ---------------------------------------------------------------------------
class ChargebackReportLine(Base):
    __tablename__ = "chargeback_report_lines"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    report_id = Column(UUID(as_uuid=True), ForeignKey("chargeback_reports.id", ondelete="CASCADE"), nullable=False)
    entity_type = Column(String(10), nullable=False)
    entity_name = Column(String(512), nullable=False)
    estimated_cost = Column(Numeric(20, 4), nullable=False)
    allocated_actual_cost = Column(Numeric(20, 4), nullable=False)
    variance = Column(Numeric(20, 4), nullable=False)
    percentage_share = Column(Numeric(7, 4), nullable=False)


# ---------------------------------------------------------------------------
# 22. v2_discovered_agentcore_runtimes — account-wide AgentCore Runtime discovery
# ---------------------------------------------------------------------------
class V2DiscoveredAgentcoreRuntime(Base):
    """A Runtime found by AgentCoreDiscoveryService's periodic account-wide scan.

    Read-only visibility layer, deliberately separate from ``v2_agent_configs``:
    a row here means "this Runtime exists in a scanned region", not "this
    dashboard governs it". Whether it's one of the 14 governed agents is
    determined at read time by comparing ``agent_runtime_arn`` against every
    ``V2AgentConfig.agentcore_agent_id`` — never denormalized onto this row, so
    it can't go stale if the governed registry changes.

    ``first_seen_at``/``last_seen_at`` track discovery history across scans;
    a Runtime that stops appearing in a scan is left in place (not deleted) so
    its history isn't lost, but its ``last_seen_at`` will fall behind — the API
    layer flags "not seen in the most recent scan" in the response so the UI
    can surface it as possibly deleted.
    """

    __tablename__ = "v2_discovered_agentcore_runtimes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    region = Column(String(20), nullable=False)
    agent_runtime_arn = Column(String(512), nullable=False, unique=True)
    agent_runtime_id = Column(String(255), nullable=False)
    agent_runtime_name = Column(String(255), nullable=False)
    status = Column(String(50), nullable=True)
    first_seen_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    last_seen_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    last_scan_id = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


# ---------------------------------------------------------------------------
# v2_audit_events: append-only record of privileged actions (AppSec F-1)
# ---------------------------------------------------------------------------
class V2AuditEvent(Base):
    __tablename__ = "v2_audit_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)
    actor_identity = Column(String(512), nullable=False)
    actor_username = Column(String(255), nullable=True)
    action = Column(String(100), nullable=False, index=True)
    details = Column(JSON, nullable=True)
