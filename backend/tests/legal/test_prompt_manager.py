"""Unit tests for PromptVersionManager."""

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

# Import legal models first so SQLAlchemy can resolve relationships
import app.models_legal  # noqa: F401
from app.models_learning import LearningPerformanceMetric, LearningPromptVersion
from app.services.legal.prompt_manager import PromptVersionManager, VersionComparison


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_version(
    review_command: str = "review_contract",
    version_number: int = 1,
    prompt_text: str = "Analyze this contract.",
    is_active: bool = False,
    created_by: str | None = "admin",
    version_id: uuid.UUID | None = None,
) -> MagicMock:
    """Build a mock LearningPromptVersion."""
    v = MagicMock(spec=LearningPromptVersion)
    v.id = version_id or uuid.uuid4()
    v.review_command = review_command
    v.version_number = version_number
    v.prompt_text = prompt_text
    v.is_active = is_active
    v.created_by = created_by
    v.created_at = datetime.now(timezone.utc)
    v.superseded_at = None
    return v


def _make_metric(
    prompt_version_id: uuid.UUID,
    accuracy_score: float = 0.85,
    false_positive_rate: float = 0.10,
    total_rated: int = 20,
) -> MagicMock:
    m = MagicMock(spec=LearningPerformanceMetric)
    m.prompt_version_id = prompt_version_id
    m.accuracy_score = accuracy_score
    m.false_positive_rate = false_positive_rate
    m.total_rated = total_rated
    m.computed_at = datetime.now(timezone.utc)
    return m


# ---------------------------------------------------------------------------
# Tests: get_active_prompt
# ---------------------------------------------------------------------------

class TestGetActivePrompt:
    def test_returns_active_version(self):
        active = _make_version(is_active=True)
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = active

        mgr = PromptVersionManager()
        result = mgr.get_active_prompt(db, "review_contract")

        assert result is active

    def test_returns_none_when_no_active(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None

        mgr = PromptVersionManager()
        result = mgr.get_active_prompt(db, "review_contract")

        assert result is None


# ---------------------------------------------------------------------------
# Tests: create_version
# ---------------------------------------------------------------------------

class TestCreateVersion:
    def test_creates_with_next_version_number(self):
        db = MagicMock()
        # Simulate max version = 3
        db.query.return_value.filter.return_value.scalar.return_value = 3

        mgr = PromptVersionManager()
        mgr.create_version(db, "review_contract", "New prompt text", "admin")

        db.add.assert_called_once()
        db.flush.assert_called_once()
        added = db.add.call_args[0][0]
        assert added.version_number == 4
        assert added.prompt_text == "New prompt text"
        assert added.is_active is False
        assert added.created_by == "admin"

    def test_first_version_gets_number_one(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.scalar.return_value = None

        mgr = PromptVersionManager()
        mgr.create_version(db, "review_nda", "NDA prompt")

        added = db.add.call_args[0][0]
        assert added.version_number == 1


# ---------------------------------------------------------------------------
# Tests: activate_version
# ---------------------------------------------------------------------------

class TestActivateVersion:
    def test_raises_on_missing_version(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None

        mgr = PromptVersionManager()
        with pytest.raises(ValueError, match="not found"):
            mgr.activate_version(db, uuid.uuid4())

    def test_deactivates_others_and_activates_target(self):
        target = _make_version(is_active=False, version_number=2)
        db = MagicMock()

        # First .filter().first() returns the target
        # Second .filter().update() deactivates others
        filter_calls = []

        def filter_side_effect(*args, **kwargs):
            call_obj = MagicMock()
            filter_calls.append(call_obj)
            if len(filter_calls) == 1:
                # First filter: lookup by id
                call_obj.first.return_value = target
            elif len(filter_calls) == 2:
                # Second filter: deactivate active versions
                call_obj.update.return_value = 1
            return call_obj

        db.query.return_value.filter.side_effect = filter_side_effect

        mgr = PromptVersionManager()
        mgr.activate_version(db, target.id)

        assert target.is_active is True
        assert target.superseded_at is None
        db.flush.assert_called_once()


# ---------------------------------------------------------------------------
# Tests: rollback_to_version
# ---------------------------------------------------------------------------

class TestRollbackToVersion:
    def test_delegates_to_activate(self):
        mgr = PromptVersionManager()
        mgr.activate_version = MagicMock(return_value="activated")

        vid = uuid.uuid4()
        result = mgr.rollback_to_version(MagicMock(), vid)

        mgr.activate_version.assert_called_once()
        assert result == "activated"


# ---------------------------------------------------------------------------
# Tests: list_versions
# ---------------------------------------------------------------------------

class TestListVersions:
    def test_returns_ordered_versions(self):
        v1 = _make_version(version_number=1)
        v2 = _make_version(version_number=2)
        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [v2, v1]

        mgr = PromptVersionManager()
        result = mgr.list_versions(db, "review_contract")

        assert result == [v2, v1]


# ---------------------------------------------------------------------------
# Tests: compare_versions
# ---------------------------------------------------------------------------

class TestCompareVersions:
    def test_raises_on_missing_version_a(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None

        mgr = PromptVersionManager()
        with pytest.raises(ValueError, match="not found"):
            mgr.compare_versions(db, uuid.uuid4(), uuid.uuid4())

    def test_returns_deltas_when_metrics_exist(self):
        vid_a = uuid.uuid4()
        vid_b = uuid.uuid4()
        va = _make_version(version_id=vid_a, version_number=1)
        vb = _make_version(version_id=vid_b, version_number=2)
        ma = _make_metric(vid_a, accuracy_score=0.80, false_positive_rate=0.15, total_rated=10)
        mb = _make_metric(vid_b, accuracy_score=0.90, false_positive_rate=0.05, total_rated=15)

        db = MagicMock()
        call_count = [0]

        def filter_side_effect(*args, **kwargs):
            call_count[0] += 1
            mock = MagicMock()
            if call_count[0] == 1:
                mock.first.return_value = va
            elif call_count[0] == 2:
                mock.first.return_value = vb
            elif call_count[0] == 3:
                mock.order_by.return_value.first.return_value = ma
            elif call_count[0] == 4:
                mock.order_by.return_value.first.return_value = mb
            return mock

        db.query.return_value.filter.side_effect = filter_side_effect

        mgr = PromptVersionManager()
        result = mgr.compare_versions(db, vid_a, vid_b)

        assert isinstance(result, VersionComparison)
        assert result.accuracy_delta == pytest.approx(0.10)
        assert result.fpr_delta == pytest.approx(-0.10)
        assert result.total_rated_a == 10
        assert result.total_rated_b == 15

    def test_returns_none_deltas_when_no_metrics(self):
        vid_a = uuid.uuid4()
        vid_b = uuid.uuid4()
        va = _make_version(version_id=vid_a, version_number=1)
        vb = _make_version(version_id=vid_b, version_number=2)

        db = MagicMock()
        call_count = [0]

        def filter_side_effect(*args, **kwargs):
            call_count[0] += 1
            mock = MagicMock()
            if call_count[0] == 1:
                mock.first.return_value = va
            elif call_count[0] == 2:
                mock.first.return_value = vb
            elif call_count[0] in (3, 4):
                mock.order_by.return_value.first.return_value = None
            return mock

        db.query.return_value.filter.side_effect = filter_side_effect

        mgr = PromptVersionManager()
        result = mgr.compare_versions(db, vid_a, vid_b)

        assert result.accuracy_delta is None
        assert result.fpr_delta is None
        assert result.total_rated_a == 0
        assert result.total_rated_b == 0
