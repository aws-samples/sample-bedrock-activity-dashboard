"""Unit tests for Team CRUD API routes."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, StaticPool
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.models import Team, TeamMembership


# ---------------------------------------------------------------------------
# In-memory SQLite database for testing.
# Using StaticPool so all connections share the same in-memory database.
# ---------------------------------------------------------------------------

engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(autouse=True)
def setup_db():
    """Create tables before each test and drop after."""
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db_session(setup_db):
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(db_session):
    from main import app

    def _override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Tests: GET /api/teams
# ---------------------------------------------------------------------------

class TestListTeams:
    def test_empty_list(self, client):
        resp = client.get("/api/teams")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_returns_teams_with_members(self, client, db_session):
        team = Team(id=uuid.uuid4(), name="Alpha", description="First team")
        db_session.add(team)
        db_session.flush()
        db_session.add(TeamMembership(id=uuid.uuid4(), team_id=team.id, user_identity="user-a"))
        db_session.add(TeamMembership(id=uuid.uuid4(), team_id=team.id, user_identity="user-b"))
        db_session.commit()

        resp = client.get("/api/teams")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["name"] == "Alpha"
        assert data[0]["memberCount"] == 2
        assert set(data[0]["members"]) == {"user-a", "user-b"}
        assert data[0]["totalTokens"] == 0
        assert data[0]["totalCostEstimate"] is None

    def test_teams_sorted_by_name(self, client, db_session):
        db_session.add(Team(id=uuid.uuid4(), name="Zulu"))
        db_session.add(Team(id=uuid.uuid4(), name="Alpha"))
        db_session.commit()

        resp = client.get("/api/teams")
        names = [t["name"] for t in resp.json()]
        assert names == ["Alpha", "Zulu"]


# ---------------------------------------------------------------------------
# Tests: POST /api/teams
# ---------------------------------------------------------------------------

class TestCreateTeam:
    def test_create_team_no_members(self, client):
        resp = client.post("/api/teams", json={"name": "Backend"})
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "Backend"
        assert data["description"] is None
        assert data["memberCount"] == 0
        assert data["members"] == []

    def test_create_team_with_members(self, client):
        resp = client.post("/api/teams", json={
            "name": "Frontend",
            "description": "UI team",
            "members": ["user-x", "user-y"],
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "Frontend"
        assert data["description"] == "UI team"
        assert data["memberCount"] == 2
        assert set(data["members"]) == {"user-x", "user-y"}

    def test_create_duplicate_name_returns_409(self, client):
        client.post("/api/teams", json={"name": "Dup"})
        resp = client.post("/api/teams", json={"name": "Dup"})
        assert resp.status_code == 409

    def test_create_team_returns_valid_uuid(self, client):
        resp = client.post("/api/teams", json={"name": "Test"})
        data = resp.json()
        uuid.UUID(data["id"])  # should not raise


# ---------------------------------------------------------------------------
# Tests: PUT /api/teams/{id}
# ---------------------------------------------------------------------------

class TestUpdateTeam:
    def _create_team(self, client, name="Original", members=None):
        payload = {"name": name}
        if members:
            payload["members"] = members
        resp = client.post("/api/teams", json=payload)
        return resp.json()

    def test_update_name(self, client):
        team = self._create_team(client)
        resp = client.put(f"/api/teams/{team['id']}", json={"name": "Renamed"})
        assert resp.status_code == 200
        assert resp.json()["name"] == "Renamed"

    def test_update_description(self, client):
        team = self._create_team(client)
        resp = client.put(f"/api/teams/{team['id']}", json={"description": "New desc"})
        assert resp.status_code == 200
        assert resp.json()["description"] == "New desc"

    def test_update_members_replaces_list(self, client):
        team = self._create_team(client, members=["user-a", "user-b"])
        resp = client.put(f"/api/teams/{team['id']}", json={"members": ["user-c"]})
        assert resp.status_code == 200
        data = resp.json()
        assert data["memberCount"] == 1
        assert data["members"] == ["user-c"]

    def test_update_nonexistent_returns_404(self, client):
        fake_id = str(uuid.uuid4())
        resp = client.put(f"/api/teams/{fake_id}", json={"name": "X"})
        assert resp.status_code == 404

    def test_update_invalid_id_returns_422(self, client):
        resp = client.put("/api/teams/not-a-uuid", json={"name": "X"})
        assert resp.status_code == 422

    def test_update_duplicate_name_returns_409(self, client):
        self._create_team(client, name="TeamA")
        team_b = self._create_team(client, name="TeamB")
        resp = client.put(f"/api/teams/{team_b['id']}", json={"name": "TeamA"})
        assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Tests: DELETE /api/teams/{id}
# ---------------------------------------------------------------------------

class TestDeleteTeam:
    def test_delete_team(self, client):
        resp = client.post("/api/teams", json={"name": "ToDelete", "members": ["u1"]})
        team_id = resp.json()["id"]

        resp = client.delete(f"/api/teams/{team_id}")
        assert resp.status_code == 204

        # Verify gone
        resp = client.get("/api/teams")
        assert all(t["id"] != team_id for t in resp.json())

    def test_delete_nonexistent_returns_404(self, client):
        fake_id = str(uuid.uuid4())
        resp = client.delete(f"/api/teams/{fake_id}")
        assert resp.status_code == 404

    def test_delete_invalid_id_returns_422(self, client):
        resp = client.delete("/api/teams/bad-id")
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Tests: Member management
# ---------------------------------------------------------------------------

class TestMemberManagement:
    def test_user_in_multiple_teams(self, client):
        r1 = client.post("/api/teams", json={"name": "T1", "members": ["shared-user"]})
        r2 = client.post("/api/teams", json={"name": "T2", "members": ["shared-user"]})
        assert r1.status_code == 201
        assert r2.status_code == 201

        teams = client.get("/api/teams").json()
        for t in teams:
            assert "shared-user" in t["members"]

    def test_remove_all_members(self, client):
        resp = client.post("/api/teams", json={"name": "Full", "members": ["a", "b"]})
        team_id = resp.json()["id"]

        resp = client.put(f"/api/teams/{team_id}", json={"members": []})
        assert resp.status_code == 200
        assert resp.json()["memberCount"] == 0
        assert resp.json()["members"] == []

    def test_response_includes_summary_fields(self, client):
        resp = client.post("/api/teams", json={"name": "Summary"})
        data = resp.json()
        assert "totalTokens" in data
        assert "totalCostEstimate" in data
        assert data["totalTokens"] == 0
        assert data["totalCostEstimate"] is None
