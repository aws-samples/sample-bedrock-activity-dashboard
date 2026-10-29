"""Agent Finding Service for the V2 Agent Platform.

Unified service for querying and managing findings across all agent tables.
"""

import logging
import re
from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.v2.models import (
    V2AgentChargebackReport,
    V2AgentCostAnomaly,
    V2AgentDigestFinding,
    V2AgentGenericFinding,
    V2AgentGuardrailRecommendation,
    V2AgentLatencyErrorFinding,
    V2AgentModelAdoptionFinding,
    V2AgentModelCostRecommendation,
    V2AgentOnboardingFinding,
    # NOTE: V2AgentPiiFinding is intentionally not imported here. The PII
    # Detector agent was removed from the platform; the table may still
    # exist on legacy databases but no agent writes to it or reads from it.
    V2AgentPolicyViolation,
    V2AgentPromptQualityFinding,
    V2AgentResidencyViolation,
    V2AgentUsageForecast,
)
from app.v2.schemas import FindingFilters
from app.v2.services.guardrail_creation_service import GuardrailCreationService

logger = logging.getLogger(__name__)

# Mapping from agent_name to the SQLAlchemy model class for that agent's findings.
AGENT_MODEL_MAP: dict[str, type] = {
    # ``pii-detector`` entry removed: agent retired from the platform.
    "cost-anomaly-alerter": V2AgentCostAnomaly,
    "guardrail-recommender": V2AgentGuardrailRecommendation,
    "model-cost-optimizer": V2AgentModelCostRecommendation,
    "prompt-quality-analyzer": V2AgentPromptQualityFinding,
    "model-adoption-tracker": V2AgentModelAdoptionFinding,
    "usage-forecaster": V2AgentUsageForecast,
    "data-residency-auditor": V2AgentResidencyViolation,
    "latency-error-monitor": V2AgentLatencyErrorFinding,
    "chargeback-report-generator": V2AgentChargebackReport,
    "onboarding-assistant": V2AgentOnboardingFinding,
    "policy-compliance-checker": V2AgentPolicyViolation,
    "weekly-digest-orchestrator": V2AgentDigestFinding,
    "cur-ingestion": V2AgentGenericFinding,
}


class AgentFindingService:
    """Queries and manages agent findings across all agent tables."""

    def __init__(self, guardrail_service: GuardrailCreationService | None = None) -> None:
        # Lazily-constructed AWS client inside GuardrailCreationService, so
        # instantiating it here performs no I/O and never fails without creds.
        self._guardrail_service = guardrail_service or GuardrailCreationService()

    def get_findings_summary(
        self,
        db: Session,
        viewer_identity: str | None = None,
        can_view_all: bool = True,
    ) -> dict:
        """Return aggregated finding counts per agent for the Insights summary view.

        Queries each agent finding table and counts records grouped by agent_name.
        Returns a dict keyed by agent_name with count values.
        """
        summary: dict[str, int] = {}

        for agent_name, model_cls in AGENT_MODEL_MAP.items():
            q = db.query(model_cls.agent_name, func.count(model_cls.id))
            if not can_view_all and hasattr(model_cls, "user_identity"):
                q = q.filter(model_cls.user_identity == viewer_identity)
            rows = q.group_by(model_cls.agent_name).all()
            for row_agent_name, count in rows:
                summary[row_agent_name] = summary.get(row_agent_name, 0) + count

        return summary

    def get_findings(
        self,
        agent_name: str,
        filters: FindingFilters,
        db: Session,
        viewer_identity: str | None = None,
        can_view_all: bool = True,
        include_prompt_text: bool = True,
    ) -> list:
        """Query findings for a specific agent with filtering.

        Supports filtering by date range, status, and severity.
        Returns a list of finding dicts.
        """
        model_cls = AGENT_MODEL_MAP.get(agent_name)
        if model_cls is None:
            return []

        query = db.query(model_cls)
        # Per-user findings are visible only to their owner unless the caller
        # may view everyone's data (admin/payer/superuser).
        if not can_view_all and hasattr(model_cls, "user_identity"):
            query = query.filter(model_cls.user_identity == viewer_identity)

        if filters.date_start is not None:
            query = query.filter(model_cls.created_at >= filters.date_start)
        if filters.date_end is not None:
            query = query.filter(model_cls.created_at <= filters.date_end)
        if filters.status is not None:
            query = query.filter(model_cls.status == filters.status)
        if filters.severity is not None:
            query = query.filter(model_cls.severity == filters.severity)

        rows = query.order_by(model_cls.created_at.desc()).all()

        items = [_row_to_dict(row) for row in rows]
        if not (can_view_all and include_prompt_text):
            items = [_redact_prompt_content(i) for i in items]
        return items

    def update_finding_status(
        self,
        finding_id: str,
        status: str,
        reason: str | None,
        db: Session,
    ) -> dict:
        """Update finding status (approve/reject).

        Searches across all finding tables for the given finding_id.
        For 'rejected' status, stores the rejection_reason on models that support it.

        Approving a guardrail-recommender finding additionally applies the
        recommendation to a REAL Amazon Bedrock Guardrail (create it if this
        is the first approval, otherwise update the existing one) via
        ``GuardrailCreationService``. The human approval decision is always
        persisted regardless of whether that AWS call succeeds; on failure,
        ``creation_error`` records why so the UI can surface it.
        """
        for agent_name, model_cls in AGENT_MODEL_MAP.items():
            finding = db.query(model_cls).filter(model_cls.id == finding_id).first()
            if finding is not None:
                finding.status = status
                finding.updated_at = datetime.now(timezone.utc)

                # Store rejection_reason if the model supports it and status is rejected
                if status == "rejected" and reason and hasattr(finding, "rejection_reason"):
                    finding.rejection_reason = reason

                if status == "approved" and isinstance(finding, V2AgentGuardrailRecommendation):
                    outcome = self._guardrail_service.apply_recommendation(finding, db)
                    if outcome.get("ok"):
                        finding.guardrail_id = outcome.get("guardrail_id")
                        finding.guardrail_arn = outcome.get("guardrail_arn")
                        finding.guardrail_version = outcome.get("guardrail_version")
                        finding.guardrail_published_version = outcome.get(
                            "guardrail_published_version"
                        )
                        finding.creation_error = None
                    else:
                        finding.creation_error = outcome.get("error")

                db.commit()
                db.refresh(finding)

                result = {
                    "id": str(finding.id),
                    "agent_name": finding.agent_name,
                    "status": finding.status,
                    "updated_at": finding.updated_at.isoformat(),
                    "rejection_reason": getattr(finding, "rejection_reason", None),
                }
                if isinstance(finding, V2AgentGuardrailRecommendation):
                    result["guardrail_id"] = finding.guardrail_id
                    result["guardrail_arn"] = finding.guardrail_arn
                    result["guardrail_version"] = finding.guardrail_version
                    result["guardrail_published_version"] = finding.guardrail_published_version
                    result["creation_error"] = finding.creation_error
                return result

        return {"error": "not_found", "message": f"Finding '{finding_id}' not found."}


# Legacy model-cost-optimizer rows embedded per-user sample prompts in the
# usage summary as ``(e.g. "<prompt text>")``. Strip them on read.
_SAMPLE_PROMPT_RE = re.compile(r'\s*\(e\.g\. ".*?"\)', re.DOTALL)


def _redact_prompt_content(item: dict) -> dict:
    """Remove raw prompt text from a serialized finding."""
    if "prompt_excerpt" in item:
        item["prompt_excerpt"] = None
    summary = item.get("usage_pattern_summary")
    if isinstance(summary, str):
        item["usage_pattern_summary"] = _SAMPLE_PROMPT_RE.sub("", summary)
    return item


def _row_to_dict(row) -> dict:
    """Convert a SQLAlchemy model instance to a dict of its column values."""
    result = {}
    for col in row.__table__.columns:
        value = getattr(row, col.name)
        if isinstance(value, datetime):
            value = value.isoformat()
        elif hasattr(value, "hex"):
            # UUID
            value = str(value)
        result[col.name] = value
    return result
