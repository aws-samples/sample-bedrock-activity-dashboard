"""Unit tests for Model Pricing CRUD API routes."""

import uuid
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, StaticPool
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.models import ModelPricing
from app.routes.pricing import DEFAULT_PRICING, seed_default_pricing


# ---------------------------------------------------------------------------
# In-memory SQLite database for testing.
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
# Tests: seed_default_pricing
# ---------------------------------------------------------------------------

class TestSeedDefaultPricing:
    def test_seeds_default_models(self, db_session):
        seed_default_pricing(db_session)
        rows = db_session.query(ModelPricing).order_by(ModelPricing.model_id).all()
        # The default catalog seeds the full set of known models; assert every
        # entry landed and that the two anchor models are present, rather than
        # pinning a brittle exact count that changes as the catalog grows.
        assert len(rows) == len(DEFAULT_PRICING)
        model_ids = {r.model_id for r in rows}
        assert "anthropic.claude-sonnet-4-20250514-v1:0" in model_ids
        assert "anthropic.claude-3-haiku-20240307-v1:0" in model_ids
        for r in rows:
            assert r.is_default is True

    def test_seed_is_idempotent(self, db_session):
        seed_default_pricing(db_session)
        first_count = db_session.query(ModelPricing).count()
        seed_default_pricing(db_session)
        second_count = db_session.query(ModelPricing).count()
        # Re-seeding must not duplicate rows.
        assert first_count == len(DEFAULT_PRICING)
        assert second_count == first_count

    def test_seed_does_not_overwrite_existing(self, db_session):
        # Manually insert a pricing row for one of the default models
        db_session.add(ModelPricing(
            id=uuid.uuid4(),
            model_id="anthropic.claude-sonnet-4-20250514-v1:0",
            input_token_price=0.999,
            output_token_price=0.999,
            cache_read_token_price=0,
            cache_write_token_price=0,
            is_default=False,
        ))
        db_session.commit()

        seed_default_pricing(db_session)

        row = db_session.query(ModelPricing).filter(
            ModelPricing.model_id == "anthropic.claude-sonnet-4-20250514-v1:0"
        ).first()
        # Should keep the manually set price, not overwrite
        assert float(row.input_token_price) == 0.999

    def test_seed_pricing_values_correct(self, db_session):
        seed_default_pricing(db_session)
        sonnet = db_session.query(ModelPricing).filter(
            ModelPricing.model_id == "anthropic.claude-sonnet-4-20250514-v1:0"
        ).first()
        assert float(sonnet.input_token_price) == 0.003
        assert float(sonnet.output_token_price) == 0.015
        assert float(sonnet.cache_read_token_price) == 0.0003
        assert float(sonnet.cache_write_token_price) == 0.00375

        haiku = db_session.query(ModelPricing).filter(
            ModelPricing.model_id == "anthropic.claude-3-haiku-20240307-v1:0"
        ).first()
        assert float(haiku.input_token_price) == 0.00025
        assert float(haiku.output_token_price) == 0.00125
        assert float(haiku.cache_read_token_price) == 0.00003
        assert float(haiku.cache_write_token_price) == 0.0003


# ---------------------------------------------------------------------------
# Tests: GET /api/pricing
# ---------------------------------------------------------------------------

class TestListPricing:
    def test_empty_list(self, client):
        resp = client.get("/api/pricing")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_returns_seeded_pricing(self, client, db_session):
        seed_default_pricing(db_session)
        resp = client.get("/api/pricing")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == len(DEFAULT_PRICING)
        model_ids = {d["modelId"] for d in data}
        assert "anthropic.claude-sonnet-4-20250514-v1:0" in model_ids
        assert "anthropic.claude-3-haiku-20240307-v1:0" in model_ids

    def test_response_shape(self, client, db_session):
        seed_default_pricing(db_session)
        resp = client.get("/api/pricing")
        item = resp.json()[0]
        assert "modelId" in item
        assert "inputTokenPrice" in item
        assert "outputTokenPrice" in item
        assert "cacheReadTokenPrice" in item
        assert "cacheWriteTokenPrice" in item
        assert "isDefault" in item
        assert "batchInputTokenPrice" in item
        assert "batchOutputTokenPrice" in item

    def test_sorted_by_model_id(self, client, db_session):
        seed_default_pricing(db_session)
        resp = client.get("/api/pricing")
        ids = [d["modelId"] for d in resp.json()]
        assert ids == sorted(ids)


# ---------------------------------------------------------------------------
# Tests: GET /api/pricing/usage-gaps (usage-driven pricing gap detection)
# ---------------------------------------------------------------------------

def _ct_record(model_id: str, timestamp: str = "2024-01-15T10:00:00+00:00") -> dict:
    return {
        "id": f"rec-{model_id}",
        "timestamp": timestamp,
        "userIdentity": "arn:aws:iam::123456789012:user/alice",
        "userName": "alice",
        "modelId": model_id,
        "sourceApplication": "Kiro",
        "sourceIp": "10.0.0.1",
        "inputTokens": 100,
        "outputTokens": 50,
        "cacheReadTokens": 0,
        "cacheWriteTokens": 0,
        "totalTokens": 150,
        "costEstimate": None,
    }


@pytest.fixture
def mock_activity_sourcing():
    """Patch the shared activity-sourcing pipeline for the usage-gaps route.

    Patches ``app.routes.activity._cloudtrail_service`` (the underlying AWS
    client) rather than ``source_activity_records_with_meta`` itself, so the
    real cost-application / enrichment pipeline still runs — matching the
    pattern used by ``backend/tests/test_activity_routes.py``.
    """
    with patch("app.routes.activity._cloudtrail_service") as mock_ct:
        yield mock_ct


class TestUsagePricingGaps:
    def test_used_model_with_no_pricing_row_is_reported(
        self, client, db_session, mock_activity_sourcing
    ):
        mock_activity_sourcing.get_activity_records_with_meta.return_value = (
            [_ct_record("some.brand-new-model-v1:0")],
            False,
            False,
        )
        resp = client.get("/api/pricing/usage-gaps")
        assert resp.status_code == 200
        data = resp.json()
        assert "some.brand-new-model-v1:0" in data["usedModelIds"]
        assert data["modelsMissingPricing"] == ["some.brand-new-model-v1:0"]

    def test_used_model_with_pricing_row_is_not_reported(
        self, client, db_session, mock_activity_sourcing
    ):
        seed_default_pricing(db_session)
        mock_activity_sourcing.get_activity_records_with_meta.return_value = (
            [_ct_record("anthropic.claude-sonnet-4-20250514-v1:0")],
            False,
            False,
        )
        resp = client.get("/api/pricing/usage-gaps")
        assert resp.status_code == 200
        data = resp.json()
        assert data["usedModelIds"] == ["anthropic.claude-sonnet-4-20250514-v1:0"]
        assert data["modelsMissingPricing"] == []

    def test_arn_and_bare_model_id_collapse_to_one_used_id(
        self, client, db_session, mock_activity_sourcing
    ):
        mock_activity_sourcing.get_activity_records_with_meta.return_value = (
            [
                _ct_record("us.anthropic.claude-opus-4-6-v1"),
                _ct_record("anthropic.claude-opus-4-6-v1"),
            ],
            False,
            False,
        )
        resp = client.get("/api/pricing/usage-gaps")
        data = resp.json()
        assert data["usedModelIds"] == ["anthropic.claude-opus-4-6-v1"]

    def test_no_usage_yields_empty_response(self, client, db_session, mock_activity_sourcing):
        mock_activity_sourcing.get_activity_records_with_meta.return_value = ([], False, False)
        resp = client.get("/api/pricing/usage-gaps")
        assert resp.status_code == 200
        data = resp.json()
        assert data["usedModelIds"] == []
        assert data["modelsMissingPricing"] == []

    def test_accepts_explicit_date_range(self, client, db_session, mock_activity_sourcing):
        mock_activity_sourcing.get_activity_records_with_meta.return_value = ([], False, False)
        resp = client.get(
            "/api/pricing/usage-gaps",
            params={"start_date": "2024-01-01", "end_date": "2024-01-31"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["windowStart"].startswith("2024-01-01")
        assert data["windowEnd"].startswith("2024-01-31")

    def test_no_admin_required(self, client, db_session, mock_activity_sourcing):
        """Any authenticated user can read pricing gaps (read-only, no PII)."""
        mock_activity_sourcing.get_activity_records_with_meta.return_value = ([], False, False)
        resp = client.get("/api/pricing/usage-gaps")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Tests: batch inference pricing (derived, not stored)
# ---------------------------------------------------------------------------

class TestBatchInferencePricing:
    def test_batch_price_is_half_of_on_demand(self, client, db_session):
        seed_default_pricing(db_session)
        resp = client.get("/api/pricing")
        sonnet = next(
            d for d in resp.json()
            if d["modelId"] == "anthropic.claude-sonnet-4-20250514-v1:0"
        )
        assert sonnet["inputTokenPrice"] == 0.003
        assert sonnet["outputTokenPrice"] == 0.015
        assert sonnet["batchInputTokenPrice"] == pytest.approx(0.0015)
        assert sonnet["batchOutputTokenPrice"] == pytest.approx(0.0075)

    def test_batch_price_tracks_admin_edited_on_demand_price(self, client):
        """Batch price is derived at read time, so an admin edit to the
        on-demand price is immediately reflected with no separate write."""
        model_id = "batch-pricing-test-model"
        client.put(
            f"/api/pricing/{model_id}",
            json={"inputTokenPrice": 0.01, "outputTokenPrice": 0.02},
        )
        resp = client.get("/api/pricing")
        match = next(d for d in resp.json() if d["modelId"] == model_id)
        assert match["batchInputTokenPrice"] == pytest.approx(0.005)
        assert match["batchOutputTokenPrice"] == pytest.approx(0.01)

        # Edit the on-demand price again — the batch price must follow it.
        client.put(
            f"/api/pricing/{model_id}",
            json={"inputTokenPrice": 0.02, "outputTokenPrice": 0.04},
        )
        resp2 = client.get("/api/pricing")
        match2 = next(d for d in resp2.json() if d["modelId"] == model_id)
        assert match2["batchInputTokenPrice"] == pytest.approx(0.01)
        assert match2["batchOutputTokenPrice"] == pytest.approx(0.02)

    def test_batch_price_zero_stays_zero(self, client):
        """A $0 on-demand price (e.g. cache-write for a model with no cache
        pricing) discounts to $0, not a negative or NaN value."""
        model_id = "zero-price-model"
        client.put(
            f"/api/pricing/{model_id}",
            json={"inputTokenPrice": 0.0, "outputTokenPrice": 0.0},
        )
        resp = client.get("/api/pricing")
        match = next(d for d in resp.json() if d["modelId"] == model_id)
        assert match["batchInputTokenPrice"] == 0.0
        assert match["batchOutputTokenPrice"] == 0.0


# ---------------------------------------------------------------------------
# Tests: PUT /api/pricing/{model_id}
# ---------------------------------------------------------------------------

class TestUpsertPricing:
    def test_create_new_pricing(self, client):
        resp = client.put(
            "/api/pricing/my-custom-model-v1",
            json={"inputTokenPrice": 0.01, "outputTokenPrice": 0.02},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["modelId"] == "my-custom-model-v1"
        assert data["inputTokenPrice"] == 0.01
        assert data["outputTokenPrice"] == 0.02
        assert data["cacheReadTokenPrice"] == 0
        assert data["cacheWriteTokenPrice"] == 0
        assert data["isDefault"] is False

    def test_update_existing_pricing(self, client, db_session):
        seed_default_pricing(db_session)
        model_id = "anthropic.claude-sonnet-4-20250514-v1:0"
        resp = client.put(
            f"/api/pricing/{model_id}",
            json={"inputTokenPrice": 0.005, "outputTokenPrice": 0.025},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["inputTokenPrice"] == 0.005
        assert data["outputTokenPrice"] == 0.025
        # Updated default becomes non-default
        assert data["isDefault"] is False

    def test_upsert_with_cache_prices(self, client):
        resp = client.put(
            "/api/pricing/model-with-cache",
            json={
                "inputTokenPrice": 0.001,
                "outputTokenPrice": 0.002,
                "cacheReadTokenPrice": 0.0005,
                "cacheWriteTokenPrice": 0.001,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["cacheReadTokenPrice"] == 0.0005
        assert data["cacheWriteTokenPrice"] == 0.001

    def test_upsert_persists(self, client):
        client.put(
            "/api/pricing/persist-test",
            json={"inputTokenPrice": 0.1, "outputTokenPrice": 0.2},
        )
        resp = client.get("/api/pricing")
        ids = [d["modelId"] for d in resp.json()]
        assert "persist-test" in ids

    def test_missing_required_field_returns_422(self, client):
        resp = client.put(
            "/api/pricing/bad-request",
            json={"inputTokenPrice": 0.01},
        )
        assert resp.status_code == 422

    def test_model_id_with_colon(self, client):
        """Model IDs like 'anthropic.claude-sonnet-4-20250514-v1:0' contain colons."""
        model_id = "anthropic.claude-sonnet-4-20250514-v1:0"
        resp = client.put(
            f"/api/pricing/{model_id}",
            json={"inputTokenPrice": 0.003, "outputTokenPrice": 0.015},
        )
        assert resp.status_code == 200
        assert resp.json()["modelId"] == model_id

    def test_update_then_retrieve_round_trip(self, client):
        model_id = "round-trip-model"
        client.put(
            f"/api/pricing/{model_id}",
            json={
                "inputTokenPrice": 0.007,
                "outputTokenPrice": 0.014,
                "cacheReadTokenPrice": 0.001,
                "cacheWriteTokenPrice": 0.002,
            },
        )
        resp = client.get("/api/pricing")
        match = [d for d in resp.json() if d["modelId"] == model_id]
        assert len(match) == 1
        assert match[0]["inputTokenPrice"] == 0.007
        assert match[0]["outputTokenPrice"] == 0.014
        assert match[0]["cacheReadTokenPrice"] == 0.001
        assert match[0]["cacheWriteTokenPrice"] == 0.002
