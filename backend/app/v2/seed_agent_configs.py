"""Seed default agent configurations for the V2 Agent Platform."""

import logging

from sqlalchemy.orm import Session

from app.v2.models import V2AgentConfig

logger = logging.getLogger(__name__)

AGENTCORE_RUNTIME_IDS = {
    # Only the agents that are actually agentic (is_agentic=True) get an
    # AgentCore Runtime package here. The 11 deterministic (⚙️) agents
    # previously had entries too — each deployed as its own AgentCore
    # Runtime — but they always resolve via
    # AgentExecutionService._try_direct_execution() in the backend's ECS
    # task and NEVER invoke AgentCore Runtime, so those Runtimes (and the
    # CloudFormation stacks that owned them) were pure dead weight and have
    # been deleted from AWS. Do not re-add entries here for a deterministic
    # agent without also removing its bypass in _try_direct_execution() —
    # otherwise the Runtime is unreachable dead weight again.
    "guardrail-recommender": "guardrailrecommender_guardrailrecommender-qUPSxyGAsy",
    "weekly-digest-orchestrator": "weeklydigestorch_weeklydigestorch-E47C7wCD8P",
}

# Agent configs that must be removed from legacy databases on startup. Seeded
# rows for retired agents are deleted idempotently in ``seed_agent_configs``
# below so the frontend Agent Management page never surfaces them again. Do
# NOT re-add any of these names to DEFAULT_AGENTS without also removing them
# from this list.
RETIRED_AGENTS: tuple[str, ...] = (
    "pii-detector",
)

DEFAULT_AGENTS = [
    {"agent_name": "cost-anomaly-alerter", "display_name": "Cost Anomaly Alerter", "category": "Security & Guardrails", "model_id": "us.anthropic.claude-haiku-4-5-20251001-v1:0", "schedule_cron": "0 8 * * *", "is_agentic": False},
    {"agent_name": "guardrail-recommender", "display_name": "Guardrail Recommender", "category": "Security & Guardrails", "model_id": "us.anthropic.claude-haiku-4-5-20251001-v1:0", "schedule_cron": None, "is_agentic": True},
    {"agent_name": "model-cost-optimizer", "display_name": "Model Cost Optimizer", "category": "Security & Guardrails", "model_id": "us.anthropic.claude-haiku-4-5-20251001-v1:0", "schedule_cron": "0 9 * * 1", "is_agentic": False},
    {"agent_name": "prompt-quality-analyzer", "display_name": "Prompt Quality Analyzer", "category": "Usage Intelligence", "model_id": "us.anthropic.claude-haiku-4-5-20251001-v1:0", "schedule_cron": "0 7 * * *", "is_agentic": False},
    {"agent_name": "model-adoption-tracker", "display_name": "Model Adoption Tracker", "category": "Usage Intelligence", "model_id": "us.anthropic.claude-haiku-4-5-20251001-v1:0", "schedule_cron": "0 10 * * 1", "is_agentic": False},
    {"agent_name": "usage-forecaster", "display_name": "Usage Forecaster", "category": "Usage Intelligence", "model_id": "us.anthropic.claude-haiku-4-5-20251001-v1:0", "schedule_cron": "0 11 * * 1", "is_agentic": False},
    {"agent_name": "data-residency-auditor", "display_name": "Data Residency Auditor", "category": "Compliance & Operations", "model_id": "us.anthropic.claude-haiku-4-5-20251001-v1:0", "schedule_cron": "0 6 * * *", "is_agentic": False},
    {"agent_name": "latency-error-monitor", "display_name": "Latency & Error Monitor", "category": "Compliance & Operations", "model_id": "us.anthropic.claude-haiku-4-5-20251001-v1:0", "schedule_cron": "0 */4 * * *", "is_agentic": False},
    {"agent_name": "chargeback-report-generator", "display_name": "Chargeback Report Generator", "category": "Compliance & Operations", "model_id": "us.anthropic.claude-haiku-4-5-20251001-v1:0", "schedule_cron": "0 9 1 * *", "is_agentic": False},
    {"agent_name": "onboarding-assistant", "display_name": "Onboarding Assistant", "category": "Compliance & Operations", "model_id": "us.anthropic.claude-haiku-4-5-20251001-v1:0", "schedule_cron": "0 */2 * * *", "is_agentic": False},
    {"agent_name": "policy-compliance-checker", "display_name": "Policy Compliance Checker", "category": "Orchestration", "model_id": "us.anthropic.claude-haiku-4-5-20251001-v1:0", "schedule_cron": "0 5 * * *", "is_agentic": False},
    {"agent_name": "weekly-digest-orchestrator", "display_name": "Weekly Digest Orchestrator", "category": "Orchestration", "model_id": "us.anthropic.claude-haiku-4-5-20251001-v1:0", "schedule_cron": "0 8 * * 1", "is_agentic": True},
    {"agent_name": "chargeback-reconciliation", "display_name": "Chargeback Reconciliation", "category": "Compliance & Operations", "model_id": "us.anthropic.claude-haiku-4-5-20251001-v1:0", "schedule_cron": None, "is_agentic": True},
    {"agent_name": "cur-ingestion", "display_name": "CUR Ingestion", "category": "Compliance & Operations", "model_id": "us.anthropic.claude-haiku-4-5-20251001-v1:0", "schedule_cron": "0 9 * * *", "is_agentic": False},
]


def seed_agent_configs(db: Session) -> None:
    """Seed default agent configs if they don't already exist.

    Also updates existing configs with agentcore_agent_id if not set, and
    purges any config rows for retired agents listed in ``RETIRED_AGENTS``
    so the frontend never shows a tile for an agent that no longer runs.
    """
    # Purge retired agent config rows (idempotent — no-op if nothing is left).
    # The agent's finding tables are intentionally left in place to preserve
    # historical rows; only the dispatchable config row is removed.
    for retired_name in RETIRED_AGENTS:
        removed = (
            db.query(V2AgentConfig)
            .filter(V2AgentConfig.agent_name == retired_name)
            .delete(synchronize_session=False)
        )
        if removed:
            logger.info("Purged retired agent config: %s", retired_name)

    for agent_data in DEFAULT_AGENTS:
        existing = db.query(V2AgentConfig).filter(
            V2AgentConfig.agent_name == agent_data["agent_name"]
        ).first()
        if existing is None:
            runtime_id = AGENTCORE_RUNTIME_IDS.get(agent_data["agent_name"])
            # Build kwargs from a copy so this never mutates the
            # module-level DEFAULT_AGENTS dicts in place — mutating them
            # (e.g. via dict.pop) would corrupt is_agentic bookkeeping on
            # any later call to this function within the same process
            # (falls through to the update branch below, whose
            # agent_data.get("is_agentic", False) would then read a
            # already-popped/missing key and silently flip is_agentic back
            # to False).
            fields = {k: v for k, v in agent_data.items() if k != "is_agentic"}
            is_agentic = agent_data.get("is_agentic", False)
            config = V2AgentConfig(enabled=True, agentcore_agent_id=runtime_id, is_agentic=is_agentic, **fields)
            db.add(config)
            logger.info("Seeded agent config: %s (runtime: %s)", agent_data["agent_name"], runtime_id)
        else:
            runtime_id = AGENTCORE_RUNTIME_IDS.get(agent_data["agent_name"])
            if existing.agentcore_agent_id is None:
                if runtime_id:
                    existing.agentcore_agent_id = runtime_id
                    logger.info("Updated agentcore_agent_id for %s: %s", agent_data["agent_name"], runtime_id)
            elif runtime_id is None:
                # The agent no longer has an entry in AGENTCORE_RUNTIME_IDS
                # (its Runtime + CloudFormation stack were deleted from AWS
                # as dead weight — see module docstring), but an old row may
                # still carry the now-nonexistent Runtime id. Clear it so
                # AgentCoreInvoker never attempts to invoke a deleted
                # Runtime ARN; it'll fall back to inline invocation as
                # designed when agentcore_agent_id is None.
                existing.agentcore_agent_id = None
                logger.info("Cleared stale agentcore_agent_id for %s (Runtime removed)", agent_data["agent_name"])
            # Update is_agentic if it changed
            is_agentic = agent_data.get("is_agentic", False)
            if existing.is_agentic != is_agentic:
                existing.is_agentic = is_agentic
                logger.info("Updated is_agentic for %s: %s", agent_data["agent_name"], is_agentic)

    db.commit()
    logger.info("Agent config seeding complete (%d agents)", len(DEFAULT_AGENTS))
