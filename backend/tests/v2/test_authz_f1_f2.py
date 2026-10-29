"""AppSec F-1 (function-level authz) and F-2 (finding scoping/redaction) tests."""
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import StaticPool, create_engine
from sqlalchemy.orm import sessionmaker

from app.auth import CurrentUser, get_current_user
from app.database import Base, get_db
from app.models import ChatMessage, ModelPricing
from app.routes.chargeback import router as chargeback_router
from app.services.agent_service import AgentService
from app.services.audit_log import audit
from app.v2.models import V2AgentConfig, V2AgentPromptQualityFinding, V2AuditEvent
from app.v2.routes.agents import router as agents_router
from app.v2.schemas import FindingFilters
from app.v2.services.agent_execution_service import AgentExecutionService
from app.v2.services.agent_finding_service import (
    AgentFindingService,
    _redact_prompt_content,
)

USER = CurrentUser(username="u", email="u@x.com", groups=[], identity="u@x.com")
PAYER = CurrentUser(
    username="p", email="p@x.com", groups=["payer"], can_view_all=True, identity="p@x.com"
)
ADMIN = CurrentUser(
    username="a", email="a@x.com", groups=["admin"], is_admin=True, can_view_all=True, identity="a"
)

ADMIN_ONLY = [
    ("post", "/api/v2/agents/cost-anomaly-alerter/execute", None),
    ("patch", "/api/v2/agents/cost-anomaly-alerter/config", {"enabled": False}),
    ("patch", "/api/v2/agents/findings/abc", {"status": "approved"}),
    ("post", "/api/v2/chargeback/uploads/00000000-0000-0000-0000-000000000001/parse", None),
    ("post", "/api/v2/chargeback/uploads/00000000-0000-0000-0000-000000000001/report", None),
    ("delete", "/api/v2/chargeback/uploads/00000000-0000-0000-0000-000000000001", None),
]
VIEW_ALL = [
    ("get", "/api/v2/chargeback/uploads"),
    ("get", "/api/v2/chargeback/reports/00000000-0000-0000-0000-000000000001"),
]


def _client(user):
    app = FastAPI()
    app.include_router(agents_router)
    app.include_router(chargeback_router)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = lambda: MagicMock()
    return TestClient(app, raise_server_exceptions=False)


def _call(client, method, url, body):
    if method == "delete":
        return client.delete(url)
    return getattr(client, method)(url, json=body)


def _non_get_routes():
    out = []
    for router in (agents_router, chargeback_router):
        for r in router.routes:
            for m in r.methods - {"GET", "HEAD", "OPTIONS"}:
                out.append((m.lower(), r.path))
    assert out, "route discovery returned nothing"
    return out


@pytest.mark.parametrize("method,path", _non_get_routes())
@pytest.mark.parametrize("user", [USER, PAYER])
def test_every_non_get_route_forbidden_for_non_admin(method, path, user):
    """Walks the routers so any NEW write route must also be admin-gated."""
    url = path.replace("{upload_id}", "00000000-0000-0000-0000-000000000001").replace(
        "{report_id}", "00000000-0000-0000-0000-000000000001"
    ).replace("{agent_name}", "cost-anomaly-alerter").replace("{finding_id}", "abc")
    client = _client(user)
    if method == "post" and path.endswith("/uploads"):
        r = client.post(url, files={"file": ("a.csv", b"x")})
    elif method == "delete":
        r = client.delete(url)
    else:
        r = getattr(client, method)(url, json={"status": "approved", "enabled": False})
    assert r.status_code == 403, (method, path, r.status_code)


@pytest.mark.parametrize("method,url,body", ADMIN_ONLY)
@pytest.mark.parametrize("user", [USER, PAYER])
def test_write_endpoints_forbidden_for_non_admin(method, url, body, user):
    assert _call(_client(user), method, url, body).status_code == 403


@pytest.mark.parametrize("method,url,body", ADMIN_ONLY)
def test_write_endpoints_pass_authz_for_admin(method, url, body):
    assert _call(_client(ADMIN), method, url, body).status_code != 403


def test_chargeback_upload_forbidden_for_non_admin():
    r = _client(USER).post("/api/v2/chargeback/uploads", files={"file": ("a.csv", b"x")})
    assert r.status_code == 403


@pytest.mark.parametrize("method,url", VIEW_ALL)
def test_chargeback_reads_forbidden_for_regular_user(method, url):
    assert getattr(_client(USER), method)(url).status_code == 403


def test_redact_prompt_content():
    item = {
        "prompt_excerpt": "secret",
        "usage_pattern_summary": 'a\n  \u2022 bob: 3 calls (e.g. "my ssn is 1") \nend',
    }
    out = _redact_prompt_content(item)
    assert out["prompt_excerpt"] is None
    assert "ssn" not in out["usage_pattern_summary"]


@pytest.fixture
def real_db():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _finding(identity, excerpt):
    return V2AgentPromptQualityFinding(
        agent_name="prompt-quality-analyzer", status="active", severity="low",
        user_identity=identity, anti_pattern_type="vague", prompt_excerpt=excerpt,
    )


def test_regular_user_cannot_see_other_users_excerpt(real_db):
    real_db.add_all([_finding("alice@x.com", "ALICE-SECRET"), _finding("bob@x.com", "BOB-OWN")])
    real_db.commit()
    f = FindingFilters(agent_name="prompt-quality-analyzer")
    rows = AgentFindingService().get_findings(
        "prompt-quality-analyzer", f, real_db, viewer_identity="bob@x.com", can_view_all=False
    )
    assert [r["user_identity"] for r in rows] == ["bob@x.com"]
    assert "ALICE-SECRET" not in str(rows)


def test_excerpt_blanked_when_prompt_history_off_even_for_viewer(real_db):
    real_db.add(_finding("alice@x.com", "ALICE-SECRET"))
    real_db.commit()
    f = FindingFilters(agent_name="prompt-quality-analyzer")
    rows = AgentFindingService().get_findings(
        "prompt-quality-analyzer", f, real_db, can_view_all=True, include_prompt_text=False
    )
    assert rows[0]["prompt_excerpt"] is None


def test_summary_scoped_for_regular_user(real_db):
    real_db.add_all([_finding("alice@x.com", "a"), _finding("bob@x.com", "b")])
    real_db.commit()
    svc = AgentFindingService()
    assert svc.get_findings_summary(real_db, "bob@x.com", False)["prompt-quality-analyzer"] == 1
    assert svc.get_findings_summary(real_db)["prompt-quality-analyzer"] == 2


def test_model_id_allowlist(real_db):
    real_db.add(V2AgentConfig(
        agent_name="a1", display_name="A", category="c",
        model_id="us.anthropic.claude-haiku-4-5-20251001-v1:0",
    ))
    real_db.add(ModelPricing(
        model_id="us.amazon.nova-pro-v1:0", input_token_price=1, output_token_price=1,
    ))
    real_db.commit()
    svc = AgentExecutionService()
    bad = svc.update_agent_config("a1", {"model_id": "evil.expensive-model", "enabled": False}, real_db)
    assert bad["error"] == "invalid_model"
    assert real_db.query(V2AgentConfig).one().enabled is True  # rejected request is a no-op
    ok = svc.update_agent_config("a1", {"model_id": "us.amazon.nova-pro-v1:0"}, real_db)
    assert ok["model_id"] == "us.amazon.nova-pro-v1:0"


def test_chat_history_not_loaded_from_other_users_session(real_db):
    sid = uuid.uuid4()
    now = datetime.now(timezone.utc)
    real_db.add(ChatMessage(session_id=sid, role="user", content="alice secret",
                            user_identity="alice@x.com", created_at=now))
    real_db.add(ChatMessage(session_id=sid, role="assistant", content="reply", created_at=now))
    real_db.commit()
    assert AgentService._load_history(sid, real_db, "bob@x.com") == []
    assert len(AgentService._load_history(sid, real_db, "alice@x.com")) == 2


def test_audit_event_is_persisted_with_actor_and_timestamp(real_db):
    audit(ADMIN, "finding.status", db=real_db, finding="f1", status="approved")
    ev = real_db.query(V2AuditEvent).one()
    assert ev.actor_identity == "a"
    assert ev.action == "finding.status"
    assert ev.details == {"finding": "f1", "status": "approved"}
    assert ev.created_at is not None


def test_audit_failure_does_not_raise():
    broken = MagicMock()
    broken.commit.side_effect = RuntimeError("db down")
    audit(ADMIN, "agent.execute", db=broken, agent="x")  # must not raise
    broken.rollback.assert_called_once()
