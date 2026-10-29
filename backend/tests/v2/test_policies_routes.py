"""Unit tests for V2 policy CRUD routes."""

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth import CurrentUser, get_current_user, require_admin
from app.database import get_db
from app.v2.routes.policies import router


def _admin_user() -> CurrentUser:
    """Authenticated admin caller satisfying the routes' ``require_admin`` dep."""
    return CurrentUser(
        username="test-admin",
        email="test-admin@example.com",
        groups=["admin"],
        is_admin=True,
        can_view_all=True,
        identity="test-admin@example.com",
    )


@pytest.fixture
def db():
    """Create a mock database session."""
    return MagicMock()


@pytest.fixture
def client(db):
    """Create a test client with the policies router and overridden DB + auth deps.

    The create/update/delete routes are guarded by ``require_admin`` (which
    depends on ``get_current_user``), so we override both to authenticate the
    test client as an admin — mirroring how the shared app is authenticated in
    conftest.
    """
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = _admin_user
    app.dependency_overrides[require_admin] = _admin_user
    return TestClient(app)


# ---------------------------------------------------------------------------
# Organizational policies
# ---------------------------------------------------------------------------

class TestListPolicies:
    def test_returns_empty_list(self, client, db):
        db.query.return_value.order_by.return_value.all.return_value = []
        resp = client.get("/api/v2/agents/policies")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_returns_policies(self, client, db):
        policy_id = uuid.uuid4()
        now = datetime.now(timezone.utc)
        mock_policy = MagicMock()
        mock_policy.id = policy_id
        mock_policy.policy_name = "No Opus in dev"
        mock_policy.policy_type = "model_restriction"
        mock_policy.policy_rules = {"blocked_models": ["opus"]}
        mock_policy.enabled = True
        mock_policy.created_at = now
        mock_policy.updated_at = now

        db.query.return_value.order_by.return_value.all.return_value = [mock_policy]
        resp = client.get("/api/v2/agents/policies")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["policy_name"] == "No Opus in dev"


class TestCreatePolicy:
    def test_creates_policy(self, client, db):
        policy_id = uuid.uuid4()
        now = datetime.now(timezone.utc)

        def fake_refresh(obj):
            obj.id = policy_id
            obj.created_at = now
            obj.updated_at = now

        db.refresh.side_effect = fake_refresh

        resp = client.post(
            "/api/v2/agents/policies",
            json={
                "policy_name": "Token limit",
                "policy_type": "token_limit",
                "policy_rules": {"max_tokens": 5000},
                "enabled": True,
            },
        )
        assert resp.status_code == 201
        db.add.assert_called_once()
        db.commit.assert_called_once()

    def test_rejects_missing_required_fields(self, client, db):
        resp = client.post("/api/v2/agents/policies", json={"policy_name": "Incomplete"})
        assert resp.status_code == 422


class TestUpdatePolicy:
    def test_updates_policy(self, client, db):
        policy_id = uuid.uuid4()
        now = datetime.now(timezone.utc)
        mock_policy = MagicMock()
        mock_policy.id = policy_id
        mock_policy.policy_name = "Old name"
        mock_policy.policy_type = "model_restriction"
        mock_policy.policy_rules = {}
        mock_policy.enabled = True
        mock_policy.created_at = now
        mock_policy.updated_at = now

        db.query.return_value.filter.return_value.first.return_value = mock_policy

        resp = client.put(
            f"/api/v2/agents/policies/{policy_id}",
            json={"policy_name": "New name"},
        )
        assert resp.status_code == 200
        assert mock_policy.policy_name == "New name"
        db.commit.assert_called_once()

    def test_returns_404_for_missing_policy(self, client, db):
        db.query.return_value.filter.return_value.first.return_value = None
        resp = client.put(
            f"/api/v2/agents/policies/{uuid.uuid4()}",
            json={"policy_name": "X"},
        )
        assert resp.status_code == 404

    def test_returns_400_for_empty_update(self, client, db):
        policy_id = uuid.uuid4()
        mock_policy = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = mock_policy

        resp = client.put(f"/api/v2/agents/policies/{policy_id}", json={})
        assert resp.status_code == 400


class TestDeletePolicy:
    def test_deletes_policy(self, client, db):
        policy_id = uuid.uuid4()
        mock_policy = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = mock_policy

        resp = client.delete(f"/api/v2/agents/policies/{policy_id}")
        assert resp.status_code == 204
        db.delete.assert_called_once_with(mock_policy)
        db.commit.assert_called_once()

    def test_returns_404_for_missing_policy(self, client, db):
        db.query.return_value.filter.return_value.first.return_value = None
        resp = client.delete(f"/api/v2/agents/policies/{uuid.uuid4()}")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Data residency policies
# ---------------------------------------------------------------------------

class TestListResidencyPolicies:
    def test_returns_empty_list(self, client, db):
        db.query.return_value.order_by.return_value.all.return_value = []
        resp = client.get("/api/v2/agents/residency-policies")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_returns_residency_policies(self, client, db):
        policy_id = uuid.uuid4()
        team_id = uuid.uuid4()
        now = datetime.now(timezone.utc)
        mock_policy = MagicMock()
        mock_policy.id = policy_id
        mock_policy.team_id = team_id
        mock_policy.approved_regions = ["us-east-1", "eu-west-1"]
        mock_policy.created_at = now
        mock_policy.updated_at = now

        db.query.return_value.order_by.return_value.all.return_value = [mock_policy]
        resp = client.get("/api/v2/agents/residency-policies")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["approved_regions"] == ["us-east-1", "eu-west-1"]


class TestCreateResidencyPolicy:
    def test_creates_residency_policy(self, client, db):
        policy_id = uuid.uuid4()
        now = datetime.now(timezone.utc)

        def fake_refresh(obj):
            obj.id = policy_id
            obj.team_id = None
            obj.created_at = now
            obj.updated_at = now

        db.refresh.side_effect = fake_refresh

        resp = client.post(
            "/api/v2/agents/residency-policies",
            json={"approved_regions": ["us-east-1"]},
        )
        assert resp.status_code == 201
        db.add.assert_called_once()
        db.commit.assert_called_once()

    def test_rejects_missing_approved_regions(self, client, db):
        resp = client.post("/api/v2/agents/residency-policies", json={})
        assert resp.status_code == 422


class TestUpdateResidencyPolicy:
    def test_updates_residency_policy(self, client, db):
        policy_id = uuid.uuid4()
        now = datetime.now(timezone.utc)
        mock_policy = MagicMock()
        mock_policy.id = policy_id
        mock_policy.team_id = None
        mock_policy.approved_regions = ["us-east-1"]
        mock_policy.created_at = now
        mock_policy.updated_at = now

        db.query.return_value.filter.return_value.first.return_value = mock_policy

        resp = client.put(
            f"/api/v2/agents/residency-policies/{policy_id}",
            json={"approved_regions": ["us-east-1", "eu-west-1"]},
        )
        assert resp.status_code == 200
        assert mock_policy.approved_regions == ["us-east-1", "eu-west-1"]
        db.commit.assert_called_once()

    def test_returns_404_for_missing_policy(self, client, db):
        db.query.return_value.filter.return_value.first.return_value = None
        resp = client.put(
            f"/api/v2/agents/residency-policies/{uuid.uuid4()}",
            json={"approved_regions": ["us-west-2"]},
        )
        assert resp.status_code == 404

    def test_returns_400_for_empty_update(self, client, db):
        policy_id = uuid.uuid4()
        mock_policy = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = mock_policy

        resp = client.put(
            f"/api/v2/agents/residency-policies/{policy_id}", json={},
        )
        assert resp.status_code == 400


class TestDeleteResidencyPolicy:
    def test_deletes_residency_policy(self, client, db):
        policy_id = uuid.uuid4()
        mock_policy = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = mock_policy

        resp = client.delete(f"/api/v2/agents/residency-policies/{policy_id}")
        assert resp.status_code == 204
        db.delete.assert_called_once_with(mock_policy)
        db.commit.assert_called_once()

    def test_returns_404_for_missing_policy(self, client, db):
        db.query.return_value.filter.return_value.first.return_value = None
        resp = client.delete(f"/api/v2/agents/residency-policies/{uuid.uuid4()}")
        assert resp.status_code == 404
