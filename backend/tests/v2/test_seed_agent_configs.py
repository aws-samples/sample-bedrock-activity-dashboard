"""Unit tests for seed_agent_configs — agent config seeding + AgentCore Runtime bookkeeping."""

import pytest
from sqlalchemy import StaticPool, create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.v2.models import V2AgentConfig
from app.v2.seed_agent_configs import (
    AGENTCORE_RUNTIME_IDS,
    DEFAULT_AGENTS,
    RETIRED_AGENTS,
    seed_agent_configs,
)


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine, tables=[V2AgentConfig.__table__])
    session_local = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = session_local()
    try:
        yield session
    finally:
        session.close()


def _get(db, agent_name: str) -> V2AgentConfig:
    return db.query(V2AgentConfig).filter(V2AgentConfig.agent_name == agent_name).first()


class TestAgentcoreRuntimeIds:
    def test_only_agentic_agents_have_runtime_ids(self):
        # Regression guard: every key in AGENTCORE_RUNTIME_IDS must belong to
        # an agent flagged is_agentic=True in DEFAULT_AGENTS. If a
        # deterministic agent's id ever ends up back in this map without
        # also removing its bypass in
        # AgentExecutionService._try_direct_execution(), the Runtime is
        # unreachable dead weight again (the bug this cleanup fixed).
        agentic_names = {a["agent_name"] for a in DEFAULT_AGENTS if a["is_agentic"]}
        assert set(AGENTCORE_RUNTIME_IDS.keys()) <= agentic_names

    def test_expected_two_agentic_runtimes_present(self):
        assert set(AGENTCORE_RUNTIME_IDS.keys()) == {
            "guardrail-recommender",
            "weekly-digest-orchestrator",
        }


class TestSeedAgentConfigs:
    def test_seeds_all_default_agents_when_none_exist(self, db):
        seed_agent_configs(db)
        assert db.query(V2AgentConfig).count() == len(DEFAULT_AGENTS)

    def test_seeded_agentic_agent_gets_runtime_id(self, db):
        seed_agent_configs(db)
        cfg = _get(db, "guardrail-recommender")
        assert cfg.agentcore_agent_id == AGENTCORE_RUNTIME_IDS["guardrail-recommender"]
        assert cfg.is_agentic is True

    def test_seeded_deterministic_agent_gets_no_runtime_id(self, db):
        seed_agent_configs(db)
        cfg = _get(db, "cost-anomaly-alerter")
        assert cfg.agentcore_agent_id is None
        assert cfg.is_agentic is False

    def test_is_idempotent_on_second_run(self, db):
        seed_agent_configs(db)
        seed_agent_configs(db)
        assert db.query(V2AgentConfig).count() == len(DEFAULT_AGENTS)

    def test_clears_stale_agentcore_agent_id_for_deleted_runtime(self, db):
        # Simulate a row seeded before the AgentCore Runtime cleanup: it
        # still carries the id of a Runtime that has since been deleted from
        # AWS along with its CloudFormation stack. Uses model-cost-optimizer
        # here as the stand-in deterministic agent (pii-detector, the
        # historic example, has been removed from the platform entirely).
        db.add(
            V2AgentConfig(
                agent_name="model-cost-optimizer",
                display_name="Model Cost Optimizer",
                category="Security & Guardrails",
                model_id="us.anthropic.claude-haiku-4-5-20251001-v1:0",
                agentcore_agent_id="modelcostoptimizer_modelcostoptimizer-CijNhH6JHL",
                is_agentic=False,
            )
        )
        db.commit()

        seed_agent_configs(db)

        cfg = _get(db, "model-cost-optimizer")
        assert cfg.agentcore_agent_id is None

    def test_retired_agents_are_purged_on_seed(self, db):
        # Simulate a legacy database that still has a config row for a
        # retired agent. seed_agent_configs() must drop it so the frontend
        # Agent Management page no longer surfaces a tile for it.
        assert "pii-detector" in RETIRED_AGENTS
        db.add(
            V2AgentConfig(
                agent_name="pii-detector",
                display_name="PII Detector",
                category="Security & Guardrails",
                model_id="us.anthropic.claude-haiku-4-5-20251001-v1:0",
                agentcore_agent_id=None,
                is_agentic=False,
            )
        )
        db.commit()

        seed_agent_configs(db)

        assert _get(db, "pii-detector") is None

    def test_does_not_clear_agentcore_agent_id_for_still_agentic_runtime(self, db):
        db.add(
            V2AgentConfig(
                agent_name="guardrail-recommender",
                display_name="Guardrail Recommender",
                category="Security & Guardrails",
                model_id="us.anthropic.claude-haiku-4-5-20251001-v1:0",
                agentcore_agent_id=AGENTCORE_RUNTIME_IDS["guardrail-recommender"],
                is_agentic=True,
            )
        )
        db.commit()

        seed_agent_configs(db)

        cfg = _get(db, "guardrail-recommender")
        assert cfg.agentcore_agent_id == AGENTCORE_RUNTIME_IDS["guardrail-recommender"]

    def test_seeding_twice_does_not_corrupt_is_agentic_via_dict_mutation(self, db):
        # Regression guard: the original seed-branch used
        # agent_data.pop("is_agentic", False), which mutated the
        # module-level DEFAULT_AGENTS dicts in place. A second call to
        # seed_agent_configs() in the same process (e.g. two test runs, or
        # a re-seed endpoint) would then hit the update branch's
        # agent_data.get("is_agentic", False) with the key already gone,
        # silently flipping every agent's is_agentic back to False.
        seed_agent_configs(db)
        seed_agent_configs(db)

        assert _get(db, "guardrail-recommender").is_agentic is True
        assert _get(db, "weekly-digest-orchestrator").is_agentic is True
        assert _get(db, "chargeback-reconciliation").is_agentic is True

    def test_updates_is_agentic_when_it_changed(self, db):
        db.add(
            V2AgentConfig(
                agent_name="guardrail-recommender",
                display_name="Guardrail Recommender",
                category="Security & Guardrails",
                model_id="us.anthropic.claude-haiku-4-5-20251001-v1:0",
                agentcore_agent_id=None,
                is_agentic=False,
            )
        )
        db.commit()

        seed_agent_configs(db)

        cfg = _get(db, "guardrail-recommender")
        assert cfg.is_agentic is True
