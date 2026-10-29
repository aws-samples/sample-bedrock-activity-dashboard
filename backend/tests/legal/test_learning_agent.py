"""Unit tests for LearningAgentService — feedback and metrics (task 5.1)."""

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, call, patch

import pytest

# Import legal models first so SQLAlchemy can resolve relationships
import app.models_legal  # noqa: F401
from app.models_learning import LearningFeedback, LearningPerformanceMetric
from app.models_legal import LegalReviewDocument, LegalReviewFinding, LegalReviewRequest
from app.schemas_learning import WeeklyTrendRead
from app.services.legal.learning_agent import (
    DEFAULT_MODEL,
    LearningAgentService,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_finding(
    finding_id: uuid.UUID | None = None,
    review_model_id: str = "claude-opus-4.6",
    review_prompt_version_id: uuid.UUID | None = None,
) -> MagicMock:
    """Build a mock LegalReviewFinding with its review_request chain."""
    fid = finding_id or uuid.uuid4()

    document = MagicMock(spec=LegalReviewDocument)
    # LegalReviewDocument doesn't have document_type column, so getattr fallback
    del document.document_type  # force getattr to return default

    review = MagicMock(spec=LegalReviewRequest)
    review.model_id = review_model_id
    review.prompt_version_id = review_prompt_version_id
    review.document = document

    finding = MagicMock(spec=LegalReviewFinding)
    finding.id = fid
    finding.review_request = review
    return finding


def _make_feedback(
    finding_id: uuid.UUID | None = None,
    user_id: str = "user-1",
    rating: str = "positive",
    classification: str | None = None,
    comment: str | None = None,
    model_id: str = "claude-opus-4.6",
    document_type: str = "contract",
) -> MagicMock:
    """Build a mock LearningFeedback record."""
    fb = MagicMock(spec=LearningFeedback)
    fb.id = uuid.uuid4()
    fb.finding_id = finding_id or uuid.uuid4()
    fb.user_id = user_id
    fb.rating = rating
    fb.classification = classification
    fb.comment = comment
    fb.model_id = model_id
    fb.document_type = document_type
    fb.created_at = datetime.now(timezone.utc)
    fb.updated_at = datetime.now(timezone.utc)
    return fb


# ---------------------------------------------------------------------------
# Tests: submit_feedback
# ---------------------------------------------------------------------------

class TestSubmitFeedback:
    def test_creates_new_feedback(self):
        finding = _make_finding()
        db = MagicMock()

        # query(LegalReviewFinding).filter().first() → finding
        # query(LearningFeedback).filter().first() → None (no existing)
        call_count = [0]

        def query_side_effect(model):
            call_count[0] += 1
            mock_q = MagicMock()
            if model is LegalReviewFinding:
                mock_q.filter.return_value.first.return_value = finding
            elif model is LearningFeedback:
                mock_q.filter.return_value.first.return_value = None
            return mock_q

        db.query.side_effect = query_side_effect

        svc = LearningAgentService()
        svc.submit_feedback(
            db, "user-1", finding.id, "positive", "false_positive", "looks wrong"
        )

        db.add.assert_called_once()
        db.flush.assert_called_once()
        added = db.add.call_args[0][0]
        assert added.rating == "positive"
        assert added.classification == "false_positive"
        assert added.comment == "looks wrong"
        assert added.user_id == "user-1"

    def test_updates_existing_feedback(self):
        finding = _make_finding()
        existing = _make_feedback(finding_id=finding.id, rating="positive")
        db = MagicMock()

        def query_side_effect(model):
            mock_q = MagicMock()
            if model is LegalReviewFinding:
                mock_q.filter.return_value.first.return_value = finding
            elif model is LearningFeedback:
                mock_q.filter.return_value.first.return_value = existing
            return mock_q

        db.query.side_effect = query_side_effect

        svc = LearningAgentService()
        svc.submit_feedback(db, "user-1", finding.id, "negative", None, "changed mind")

        # Should NOT call db.add (update path)
        db.add.assert_not_called()
        assert existing.rating == "negative"
        assert existing.comment == "changed mind"
        assert existing.classification is None

    def test_raises_on_missing_finding(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None

        svc = LearningAgentService()
        with pytest.raises(ValueError, match="not found"):
            svc.submit_feedback(db, "user-1", uuid.uuid4(), "positive")

    def test_uses_default_model_id_when_none(self):
        finding = _make_finding(review_model_id=None)
        db = MagicMock()

        def query_side_effect(model):
            mock_q = MagicMock()
            if model is LegalReviewFinding:
                mock_q.filter.return_value.first.return_value = finding
            elif model is LearningFeedback:
                mock_q.filter.return_value.first.return_value = None
            return mock_q

        db.query.side_effect = query_side_effect

        svc = LearningAgentService()
        svc.submit_feedback(db, "user-1", finding.id, "positive")

        added = db.add.call_args[0][0]
        assert added.model_id == "default"


# ---------------------------------------------------------------------------
# Tests: get_feedback
# ---------------------------------------------------------------------------

class TestGetFeedback:
    def test_returns_existing_feedback(self):
        fb = _make_feedback()
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = fb

        svc = LearningAgentService()
        result = svc.get_feedback(db, "user-1", fb.finding_id)
        assert result is fb

    def test_returns_none_when_not_found(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None

        svc = LearningAgentService()
        result = svc.get_feedback(db, "user-1", uuid.uuid4())
        assert result is None


# ---------------------------------------------------------------------------
# Tests: compute_metrics
# ---------------------------------------------------------------------------

class TestComputeMetrics:
    def _setup_db(self, feedback_list):
        """Set up a mock db that returns feedback_list for query().filter().all()
        and supports the delete path for old metrics."""
        db = MagicMock()

        # We need to handle multiple query() calls:
        # 1. query(LearningFeedback).filter(...).all() → feedback_list
        # 2. query(LearningPerformanceMetric).filter(...).filter(...).filter(...).delete()
        call_count = [0]

        def query_side_effect(model):
            call_count[0] += 1
            mock_q = MagicMock()
            if model is LearningFeedback:
                # Chain: .filter().all() returns feedback_list
                mock_q.filter.return_value.all.return_value = feedback_list
                # Also handle no-filter case
                mock_q.all.return_value = feedback_list
            elif model is LearningPerformanceMetric:
                # Chain: .filter().filter().filter().delete()
                chain = MagicMock()
                chain.filter.return_value = chain
                chain.delete.return_value = 0
                mock_q.filter.return_value = chain
            return mock_q

        db.query.side_effect = query_side_effect
        return db

    def test_accuracy_formula(self):
        """accuracy_score = positive_count / total_rated"""
        feedbacks = [
            _make_feedback(rating="positive"),
            _make_feedback(rating="positive"),
            _make_feedback(rating="negative"),
            _make_feedback(rating="positive"),
        ]
        db = self._setup_db(feedbacks)

        svc = LearningAgentService()
        svc.compute_metrics(db, model_id="model-a")

        added = db.add.call_args[0][0]
        assert added.accuracy_score == pytest.approx(3 / 4)
        assert added.positive_count == 3
        assert added.negative_count == 1
        assert added.total_rated == 4

    def test_false_positive_rate_formula(self):
        """false_positive_rate = false_positive_count / total_rated"""
        feedbacks = [
            _make_feedback(rating="negative", classification="false_positive"),
            _make_feedback(rating="negative", classification="false_positive"),
            _make_feedback(rating="positive"),
            _make_feedback(rating="positive"),
            _make_feedback(rating="negative"),
        ]
        db = self._setup_db(feedbacks)

        svc = LearningAgentService()
        svc.compute_metrics(db, model_id="model-a")

        added = db.add.call_args[0][0]
        assert added.false_positive_rate == pytest.approx(2 / 5)
        assert added.false_positive_count == 2

    def test_low_confidence_when_below_threshold(self):
        """is_low_confidence = True when total_rated < 5"""
        feedbacks = [
            _make_feedback(rating="positive"),
            _make_feedback(rating="negative"),
        ]
        db = self._setup_db(feedbacks)

        svc = LearningAgentService()
        svc.compute_metrics(db, model_id="model-a")

        added = db.add.call_args[0][0]
        assert added.is_low_confidence is True
        assert added.total_rated == 2

    def test_not_low_confidence_at_threshold(self):
        """is_low_confidence = False when total_rated >= 5"""
        feedbacks = [_make_feedback(rating="positive") for _ in range(5)]
        db = self._setup_db(feedbacks)

        svc = LearningAgentService()
        svc.compute_metrics(db, model_id="model-a")

        added = db.add.call_args[0][0]
        assert added.is_low_confidence is False
        assert added.total_rated == 5

    def test_scope_type_model(self):
        db = self._setup_db([])
        svc = LearningAgentService()
        svc.compute_metrics(db, model_id="model-a", document_type=None)
        added = db.add.call_args[0][0]
        assert added.scope_type == "model"

    def test_scope_type_document_type(self):
        db = self._setup_db([])
        svc = LearningAgentService()
        svc.compute_metrics(db, model_id=None, document_type="contract")
        added = db.add.call_args[0][0]
        assert added.scope_type == "document_type"

    def test_scope_type_model_document_type(self):
        db = self._setup_db([])
        svc = LearningAgentService()
        svc.compute_metrics(db, model_id="model-a", document_type="nda")
        added = db.add.call_args[0][0]
        assert added.scope_type == "model_document_type"

    def test_empty_feedback_returns_zero_scores(self):
        db = self._setup_db([])
        svc = LearningAgentService()
        svc.compute_metrics(db, model_id="model-a")
        added = db.add.call_args[0][0]
        assert added.accuracy_score == 0.0
        assert added.false_positive_rate == 0.0
        assert added.total_rated == 0
        assert added.is_low_confidence is True

    def test_complementary_invariant(self):
        """accuracy_score + (negative_count / total_rated) ≈ 1.0"""
        feedbacks = [
            _make_feedback(rating="positive"),
            _make_feedback(rating="positive"),
            _make_feedback(rating="negative"),
            _make_feedback(rating="negative"),
            _make_feedback(rating="positive"),
            _make_feedback(rating="negative"),
        ]
        db = self._setup_db(feedbacks)

        svc = LearningAgentService()
        svc.compute_metrics(db, model_id="model-a")

        added = db.add.call_args[0][0]
        negative_ratio = added.negative_count / added.total_rated
        assert added.accuracy_score + negative_ratio == pytest.approx(1.0, abs=0.001)


# ---------------------------------------------------------------------------
# Tests: recompute_all_metrics
# ---------------------------------------------------------------------------

class TestRecomputeAllMetrics:
    def test_recomputes_for_all_scopes(self):
        svc = LearningAgentService()
        svc.compute_metrics = MagicMock(return_value=MagicMock(spec=LearningPerformanceMetric))

        db = MagicMock()
        # distinct model_ids
        db.query.return_value.distinct.return_value.all.side_effect = [
            [("model-a",), ("model-b",)],   # distinct models
            [("contract",), ("nda",)],       # distinct doc types
            [("model-a", "contract"), ("model-b", "nda")],  # combos
        ]

        result = svc.recompute_all_metrics(db)

        # 2 models + 2 doc types + 2 combos = 6
        assert svc.compute_metrics.call_count == 6
        assert len(result) == 6

        # Verify scope calls
        calls = svc.compute_metrics.call_args_list
        # Per model
        assert calls[0] == call(db, model_id="model-a", document_type=None)
        assert calls[1] == call(db, model_id="model-b", document_type=None)
        # Per doc type
        assert calls[2] == call(db, model_id=None, document_type="contract")
        assert calls[3] == call(db, model_id=None, document_type="nda")
        # Per combo
        assert calls[4] == call(db, model_id="model-a", document_type="contract")
        assert calls[5] == call(db, model_id="model-b", document_type="nda")


# ---------------------------------------------------------------------------
# Helpers for model recommendation tests
# ---------------------------------------------------------------------------

def _make_metric(
    model_id: str = "claude-opus-4.6",
    document_type: str = "contract",
    accuracy_score: float = 0.8,
    false_positive_rate: float = 0.1,
    total_rated: int = 10,
    is_low_confidence: bool = False,
    scope_type: str = "model_document_type",
) -> MagicMock:
    """Build a mock LearningPerformanceMetric."""
    m = MagicMock(spec=LearningPerformanceMetric)
    m.model_id = model_id
    m.document_type = document_type
    m.accuracy_score = accuracy_score
    m.false_positive_rate = false_positive_rate
    m.total_rated = total_rated
    m.is_low_confidence = is_low_confidence
    m.scope_type = scope_type
    return m


# ---------------------------------------------------------------------------
# Tests: get_model_recommendation
# ---------------------------------------------------------------------------

class TestGetModelRecommendation:
    def _setup_db(self, metrics_list):
        """Mock db that returns metrics_list for the model_document_type query."""
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = metrics_list
        return db

    def test_returns_default_when_no_metrics(self):
        """Req 4.4: default model when no metrics exist."""
        db = self._setup_db([])
        svc = LearningAgentService()
        result = svc.get_model_recommendation(db, "contract")

        assert result.recommended_model == DEFAULT_MODEL
        assert result.confidence == "insufficient_data"
        assert result.rated_finding_count == 0

    def test_returns_default_when_all_low_confidence(self):
        """Req 4.5: default model when all metrics are low confidence."""
        metrics = [
            _make_metric(model_id="model-a", is_low_confidence=True),
            _make_metric(model_id="model-b", is_low_confidence=True),
        ]
        db = self._setup_db(metrics)
        svc = LearningAgentService()
        result = svc.get_model_recommendation(db, "contract")

        assert result.recommended_model == DEFAULT_MODEL
        assert result.confidence == "insufficient_data"

    def test_ranks_by_accuracy_descending(self):
        """Req 4.2: recommend model with highest accuracy_score."""
        metrics = [
            _make_metric(model_id="model-a", accuracy_score=0.70, false_positive_rate=0.15, total_rated=20),
            _make_metric(model_id="model-b", accuracy_score=0.90, false_positive_rate=0.05, total_rated=15),
            _make_metric(model_id="model-c", accuracy_score=0.80, false_positive_rate=0.10, total_rated=12),
        ]
        db = self._setup_db(metrics)
        svc = LearningAgentService()
        result = svc.get_model_recommendation(db, "contract")

        assert result.recommended_model == "model-b"
        assert result.accuracy_score == pytest.approx(0.90)
        assert result.false_positive_rate == pytest.approx(0.05)
        assert result.rated_finding_count == 15
        assert result.confidence == "high"

    def test_includes_all_required_fields(self):
        """Req 4.3: recommendation includes model name, accuracy, FPR, rated count."""
        metrics = [
            _make_metric(model_id="model-x", accuracy_score=0.85, false_positive_rate=0.08, total_rated=25),
        ]
        db = self._setup_db(metrics)
        svc = LearningAgentService()
        result = svc.get_model_recommendation(db, "nda")

        assert result.recommended_model == "model-x"
        assert result.accuracy_score == pytest.approx(0.85)
        assert result.false_positive_rate == pytest.approx(0.08)
        assert result.rated_finding_count == 25
        assert result.confidence == "high"

    def test_notification_when_10_plus_points_better(self):
        """Req 7.4: notification when recommendation is 10+ points better than default."""
        metrics = [
            _make_metric(model_id=DEFAULT_MODEL, accuracy_score=0.60, total_rated=10),
            _make_metric(model_id="model-better", accuracy_score=0.75, total_rated=10),
        ]
        db = self._setup_db(metrics)
        svc = LearningAgentService()
        result = svc.get_model_recommendation(db, "contract")

        assert result.recommended_model == "model-better"
        assert result.message is not None
        assert "model-better" in result.message

    def test_no_notification_when_less_than_10_points(self):
        """Req 7.4: no notification when difference is < 10 points."""
        metrics = [
            _make_metric(model_id=DEFAULT_MODEL, accuracy_score=0.80, total_rated=10),
            _make_metric(model_id="model-slightly-better", accuracy_score=0.85, total_rated=10),
        ]
        db = self._setup_db(metrics)
        svc = LearningAgentService()
        result = svc.get_model_recommendation(db, "contract")

        assert result.recommended_model == "model-slightly-better"
        assert result.message is None

    def test_no_notification_when_default_is_best(self):
        """No notification when the default model is the recommended one."""
        metrics = [
            _make_metric(model_id=DEFAULT_MODEL, accuracy_score=0.90, total_rated=10),
            _make_metric(model_id="model-worse", accuracy_score=0.60, total_rated=10),
        ]
        db = self._setup_db(metrics)
        svc = LearningAgentService()
        result = svc.get_model_recommendation(db, "contract")

        assert result.recommended_model == DEFAULT_MODEL
        assert result.message is None

    def test_filters_out_low_confidence_metrics(self):
        """Low confidence metrics should be excluded from ranking."""
        metrics = [
            _make_metric(model_id="model-a", accuracy_score=0.95, is_low_confidence=True),
            _make_metric(model_id="model-b", accuracy_score=0.70, is_low_confidence=False, total_rated=10),
        ]
        db = self._setup_db(metrics)
        svc = LearningAgentService()
        result = svc.get_model_recommendation(db, "contract")

        assert result.recommended_model == "model-b"
        assert result.confidence == "high"

    def test_notification_when_default_has_no_metric(self):
        """When default model has no metric, its accuracy is 0 → 10+ point diff triggers notification."""
        metrics = [
            _make_metric(model_id="model-new", accuracy_score=0.85, total_rated=10),
        ]
        db = self._setup_db(metrics)
        svc = LearningAgentService()
        result = svc.get_model_recommendation(db, "contract")

        assert result.recommended_model == "model-new"
        assert result.message is not None
        assert "model-new" in result.message


# ---------------------------------------------------------------------------
# Helpers for pattern / suggestion tests
# ---------------------------------------------------------------------------

def _make_finding_for_pattern(
    affected_clause: str = "indemnification",
    risk_level: str = "high",
) -> MagicMock:
    """Build a mock LegalReviewFinding for pattern tests."""
    f = MagicMock(spec=LegalReviewFinding)
    f.affected_clause = affected_clause
    f.risk_level = risk_level
    return f


def _make_feedback_finding_pair(
    document_type: str = "contract",
    affected_clause: str = "indemnification",
    risk_level: str = "high",
) -> tuple:
    """Return a (feedback, finding) tuple as returned by the joined query."""
    fb = MagicMock(spec=LearningFeedback)
    fb.classification = "false_positive"
    fb.document_type = document_type

    finding = MagicMock(spec=LegalReviewFinding)
    finding.affected_clause = affected_clause
    finding.risk_level = risk_level
    return (fb, finding)


# ---------------------------------------------------------------------------
# Tests: identify_feedback_patterns (Req 5.1)
# ---------------------------------------------------------------------------

class TestIdentifyFeedbackPatterns:
    def _setup_db(self, rows):
        """Mock db that returns rows for the joined query."""
        db = MagicMock()
        db.query.return_value.join.return_value.filter.return_value.all.return_value = rows
        return db

    def test_groups_by_clause_doctype_risk(self):
        """Patterns are grouped by (affected_clause, document_type, risk_level)."""
        rows = [
            _make_feedback_finding_pair("contract", "indemnification", "high"),
            _make_feedback_finding_pair("contract", "indemnification", "high"),
            _make_feedback_finding_pair("nda", "confidentiality", "medium"),
        ]
        db = self._setup_db(rows)
        svc = LearningAgentService()
        patterns = svc.identify_feedback_patterns(db)

        assert len(patterns) == 2
        # Sorted by occurrence_count desc
        assert patterns[0]["occurrence_count"] == 2
        assert patterns[0]["affected_clause"] == "indemnification"
        assert patterns[0]["document_type"] == "contract"
        assert patterns[0]["risk_level"] == "high"
        assert patterns[1]["occurrence_count"] == 1

    def test_returns_empty_when_no_false_positives(self):
        db = self._setup_db([])
        svc = LearningAgentService()
        patterns = svc.identify_feedback_patterns(db)
        assert patterns == []

    def test_handles_null_affected_clause(self):
        """Null affected_clause should be treated as 'unknown'."""
        rows = [
            _make_feedback_finding_pair("contract", None, "low"),
        ]
        # Fix the finding mock to have None affected_clause
        rows[0][1].affected_clause = None
        db = self._setup_db(rows)
        svc = LearningAgentService()
        patterns = svc.identify_feedback_patterns(db)

        assert len(patterns) == 1
        assert patterns[0]["affected_clause"] == "unknown"

    def test_occurrence_count_matches_actual_records(self):
        """Each pattern's count matches the number of matching feedback records."""
        rows = [
            _make_feedback_finding_pair("contract", "liability", "high"),
            _make_feedback_finding_pair("contract", "liability", "high"),
            _make_feedback_finding_pair("contract", "liability", "high"),
            _make_feedback_finding_pair("contract", "liability", "high"),
        ]
        db = self._setup_db(rows)
        svc = LearningAgentService()
        patterns = svc.identify_feedback_patterns(db)

        assert len(patterns) == 1
        assert patterns[0]["occurrence_count"] == 4


# ---------------------------------------------------------------------------
# Tests: generate_prompt_suggestions (Req 5.2, 5.3)
# ---------------------------------------------------------------------------

class TestGeneratePromptSuggestions:
    def test_creates_suggestions_for_3_plus_occurrences(self):
        """Req 5.2: suggestions generated only for patterns with 3+ occurrences."""
        svc = LearningAgentService()
        svc.identify_feedback_patterns = MagicMock(return_value=[
            {"affected_clause": "indemnification", "document_type": "contract",
             "risk_level": "high", "occurrence_count": 5},
            {"affected_clause": "confidentiality", "document_type": "nda",
             "risk_level": "medium", "occurrence_count": 2},  # below threshold
        ])
        db = MagicMock()

        suggestions = svc.generate_prompt_suggestions(db, review_command="legal_review")

        # Only 1 suggestion (the one with 5 occurrences)
        assert len(suggestions) == 1
        assert db.add.call_count == 1
        added = db.add.call_args[0][0]
        assert added.status == "pending"
        assert added.supporting_feedback_count == 5
        assert "indemnification" in added.pattern_description
        assert added.review_command == "legal_review"

    def test_no_suggestions_when_all_below_threshold(self):
        svc = LearningAgentService()
        svc.identify_feedback_patterns = MagicMock(return_value=[
            {"affected_clause": "a", "document_type": "contract",
             "risk_level": "low", "occurrence_count": 1},
            {"affected_clause": "b", "document_type": "nda",
             "risk_level": "high", "occurrence_count": 2},
        ])
        db = MagicMock()

        suggestions = svc.generate_prompt_suggestions(db)
        assert len(suggestions) == 0
        db.add.assert_not_called()

    def test_suggestion_contains_required_fields(self):
        """Req 5.3: suggestion has pattern description, suggested change, count, timestamp."""
        svc = LearningAgentService()
        svc.identify_feedback_patterns = MagicMock(return_value=[
            {"affected_clause": "termination", "document_type": "vendor",
             "risk_level": "high", "occurrence_count": 3},
        ])
        db = MagicMock()

        suggestions = svc.generate_prompt_suggestions(db, review_command="legal_review")

        assert len(suggestions) == 1
        s = suggestions[0]
        assert s.pattern_description is not None and len(s.pattern_description) > 0
        assert s.suggested_prompt_change is not None and len(s.suggested_prompt_change) > 0
        assert s.supporting_feedback_count == 3
        assert s.status == "pending"
        assert s.review_command == "legal_review"

    def test_multiple_patterns_above_threshold(self):
        """Multiple patterns above threshold each get a suggestion."""
        svc = LearningAgentService()
        svc.identify_feedback_patterns = MagicMock(return_value=[
            {"affected_clause": "a", "document_type": "contract",
             "risk_level": "high", "occurrence_count": 4},
            {"affected_clause": "b", "document_type": "nda",
             "risk_level": "medium", "occurrence_count": 3},
            {"affected_clause": "c", "document_type": "vendor",
             "risk_level": "low", "occurrence_count": 1},
        ])
        db = MagicMock()

        suggestions = svc.generate_prompt_suggestions(db)
        assert len(suggestions) == 2
        assert db.add.call_count == 2


# ---------------------------------------------------------------------------
# Tests: approve_suggestion (Req 5.4)
# ---------------------------------------------------------------------------

class TestApproveSuggestion:
    def test_approves_and_creates_new_version(self):
        """Req 5.4: approving a suggestion creates a new prompt version."""
        from app.models_learning import LearningPromptSuggestion, LearningPromptVersion

        suggestion_id = uuid.uuid4()
        suggestion = MagicMock(spec=LearningPromptSuggestion)
        suggestion.id = suggestion_id
        suggestion.status = "pending"
        suggestion.review_command = "legal_review"
        suggestion.suggested_prompt_change = "Add exception for indemnification clauses"

        active_prompt = MagicMock(spec=LearningPromptVersion)
        active_prompt.prompt_text = "You are a legal reviewer."

        new_version = MagicMock(spec=LearningPromptVersion)
        new_version.id = uuid.uuid4()

        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = suggestion

        svc = LearningAgentService()

        with patch(
            "app.services.legal.learning_agent.PromptVersionManager"
        ) as MockPVM:
            mock_mgr = MockPVM.return_value
            mock_mgr.get_active_prompt.return_value = active_prompt
            mock_mgr.create_version.return_value = new_version

            result = svc.approve_suggestion(db, suggestion_id, "admin-user")

        assert suggestion.status == "approved"
        assert suggestion.resolved_by == "admin-user"
        assert suggestion.resolved_at is not None
        mock_mgr.create_version.assert_called_once()
        mock_mgr.activate_version.assert_called_once_with(db, new_version.id)
        assert result is new_version

    def test_raises_on_missing_suggestion(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None

        svc = LearningAgentService()
        with pytest.raises(ValueError, match="not found"):
            svc.approve_suggestion(db, uuid.uuid4(), "admin-user")

    def test_raises_on_non_pending_suggestion(self):
        from app.models_learning import LearningPromptSuggestion

        suggestion = MagicMock(spec=LearningPromptSuggestion)
        suggestion.id = uuid.uuid4()
        suggestion.status = "approved"

        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = suggestion

        svc = LearningAgentService()
        with pytest.raises(ValueError, match="already approved"):
            svc.approve_suggestion(db, suggestion.id, "admin-user")

    def test_handles_no_active_prompt(self):
        """When no active prompt exists, new version is created from empty base."""
        from app.models_learning import LearningPromptSuggestion, LearningPromptVersion

        suggestion = MagicMock(spec=LearningPromptSuggestion)
        suggestion.id = uuid.uuid4()
        suggestion.status = "pending"
        suggestion.review_command = "legal_review"
        suggestion.suggested_prompt_change = "New prompt content"

        new_version = MagicMock(spec=LearningPromptVersion)
        new_version.id = uuid.uuid4()

        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = suggestion

        svc = LearningAgentService()

        with patch(
            "app.services.legal.learning_agent.PromptVersionManager"
        ) as MockPVM:
            mock_mgr = MockPVM.return_value
            mock_mgr.get_active_prompt.return_value = None
            mock_mgr.create_version.return_value = new_version

            svc.approve_suggestion(db, suggestion.id, "admin-user")

        # Verify create_version was called with prompt text containing the suggestion
        call_kwargs = mock_mgr.create_version.call_args
        prompt_text = call_kwargs.kwargs.get("prompt_text") or call_kwargs[1].get("prompt_text") or call_kwargs[0][2]
        assert "New prompt content" in prompt_text
        mock_mgr.activate_version.assert_called_once_with(db, new_version.id)


# ---------------------------------------------------------------------------
# Tests: get_weekly_trends (Req 9.1)
# ---------------------------------------------------------------------------

class TestGetWeeklyTrends:
    def _make_fb(
        self,
        created_at: datetime,
        rating: str = "positive",
        classification: str | None = None,
        model_id: str = "claude-opus-4.6",
        document_type: str = "contract",
    ) -> MagicMock:
        fb = MagicMock(spec=LearningFeedback)
        fb.created_at = created_at
        fb.rating = rating
        fb.classification = classification
        fb.model_id = model_id
        fb.document_type = document_type
        return fb

    def test_groups_by_iso_week(self):
        """Req 9.1: feedback is grouped by ISO week start (Monday)."""
        # Monday 2025-01-06 and Tuesday 2025-01-07 → same week
        # Monday 2025-01-13 → next week
        fb1 = self._make_fb(datetime(2025, 1, 6, 10, 0, tzinfo=timezone.utc), "positive")
        fb2 = self._make_fb(datetime(2025, 1, 7, 12, 0, tzinfo=timezone.utc), "negative")
        fb3 = self._make_fb(datetime(2025, 1, 13, 8, 0, tzinfo=timezone.utc), "positive")

        db = MagicMock()
        db.query.return_value.all.return_value = [fb1, fb2, fb3]

        svc = LearningAgentService()
        trends = svc.get_weekly_trends(db)

        assert len(trends) == 2
        # First week: 1 positive, 1 negative → accuracy 0.5
        from datetime import date as date_type
        assert trends[0].week_start == date_type(2025, 1, 6)
        assert trends[0].total_rated == 2
        assert trends[0].accuracy_score == pytest.approx(0.5)
        # Second week: 1 positive → accuracy 1.0
        assert trends[1].week_start == date_type(2025, 1, 13)
        assert trends[1].total_rated == 1
        assert trends[1].accuracy_score == pytest.approx(1.0)

    def test_filters_by_model_id(self):
        """Req 9.1: trends can be filtered by model_id."""
        fb1 = self._make_fb(datetime(2025, 1, 6, tzinfo=timezone.utc), model_id="model-a")

        db = MagicMock()
        query_mock = MagicMock()
        db.query.return_value = query_mock
        query_mock.filter.return_value = query_mock
        query_mock.all.return_value = [fb1]

        svc = LearningAgentService()
        trends = svc.get_weekly_trends(db, model_id="model-a")

        assert len(trends) == 1
        assert trends[0].model_id == "model-a"
        # Verify filter was called
        query_mock.filter.assert_called()

    def test_filters_by_document_type(self):
        """Req 9.1: trends can be filtered by document_type."""
        fb1 = self._make_fb(datetime(2025, 1, 6, tzinfo=timezone.utc), document_type="nda")

        db = MagicMock()
        query_mock = MagicMock()
        db.query.return_value = query_mock
        query_mock.filter.return_value = query_mock
        query_mock.all.return_value = [fb1]

        svc = LearningAgentService()
        trends = svc.get_weekly_trends(db, document_type="nda")

        assert len(trends) == 1
        assert trends[0].document_type == "nda"

    def test_computes_false_positive_rate(self):
        """Req 9.1: FPR is computed per week."""
        fb1 = self._make_fb(
            datetime(2025, 1, 6, tzinfo=timezone.utc),
            rating="negative",
            classification="false_positive",
        )
        fb2 = self._make_fb(datetime(2025, 1, 7, tzinfo=timezone.utc), rating="positive")

        db = MagicMock()
        db.query.return_value.all.return_value = [fb1, fb2]

        svc = LearningAgentService()
        trends = svc.get_weekly_trends(db)

        assert len(trends) == 1
        assert trends[0].false_positive_rate == pytest.approx(0.5)

    def test_limits_to_requested_weeks(self):
        """Trends are limited to the most recent N weeks."""
        from datetime import timedelta as td
        base = datetime(2025, 1, 6, tzinfo=timezone.utc)
        fbs = [
            self._make_fb(base + td(weeks=i))
            for i in range(20)
        ]

        db = MagicMock()
        db.query.return_value.all.return_value = fbs

        svc = LearningAgentService()
        trends = svc.get_weekly_trends(db, weeks=5)

        assert len(trends) == 5

    def test_returns_empty_for_no_feedback(self):
        db = MagicMock()
        db.query.return_value.all.return_value = []

        svc = LearningAgentService()
        trends = svc.get_weekly_trends(db)

        assert trends == []


# ---------------------------------------------------------------------------
# Tests: generate_analytics_report (Req 9.2)
# ---------------------------------------------------------------------------

class TestGenerateAnalyticsReport:
    def test_returns_complete_report(self):
        """Req 9.2: report contains all required fields."""
        from app.models_learning import LearningPromptSuggestion

        db = MagicMock()

        # total feedback count
        db.query.return_value.count.return_value = 42

        # For get_weekly_trends: query(...).all() returns feedback
        # For suggestions: query(...).filter(...).order_by(...).all()
        # For model metrics: query(...).filter(...).all()
        # We need to handle multiple db.query calls differently

        svc = LearningAgentService()

        with patch.object(svc, "get_weekly_trends") as mock_trends:
            from datetime import date as date_type
            mock_trends.return_value = [
                WeeklyTrendRead(
                    week_start=date_type(2025, 1, 6),
                    accuracy_score=0.8,
                    false_positive_rate=0.1,
                    total_rated=10,
                    model_id=None,
                    document_type=None,
                ),
            ]

            # Mock the chained queries for suggestions and model metrics
            suggestion_mock = MagicMock(spec=LearningPromptSuggestion)
            suggestion_mock.id = uuid.uuid4()
            suggestion_mock.review_command = "legal_review"
            suggestion_mock.pattern_description = "test pattern"
            suggestion_mock.suggested_prompt_change = "test change"
            suggestion_mock.supporting_feedback_count = 5
            suggestion_mock.status = "pending"
            suggestion_mock.created_at = datetime.now(timezone.utc)

            metric_mock = MagicMock(spec=LearningPerformanceMetric)
            metric_mock.scope_type = "model"
            metric_mock.model_id = "claude-opus-4.6"
            metric_mock.document_type = None
            metric_mock.accuracy_score = 0.85
            metric_mock.false_positive_rate = 0.05
            metric_mock.total_rated = 20
            metric_mock.is_low_confidence = False
            metric_mock.computed_at = datetime.now(timezone.utc)

            # Set up query chain: count, then suggestions, then metrics
            count_chain = MagicMock()
            count_chain.count.return_value = 42

            suggestion_chain = MagicMock()
            suggestion_chain.filter.return_value.order_by.return_value.all.return_value = [suggestion_mock]

            metric_chain = MagicMock()
            metric_chain.filter.return_value.all.return_value = [metric_mock]

            db.query.side_effect = [count_chain, suggestion_chain, metric_chain]

            report = svc.generate_analytics_report(db)

        assert report.total_feedback_count == 42
        assert len(report.accuracy_trends) == 1
        assert len(report.fpr_trends) == 1
        assert report.accuracy_trends[0].accuracy_score == 0.8
        assert len(report.top_patterns) == 1
        assert len(report.model_comparison) == 1

    def test_returns_empty_report_when_no_data(self):
        """Req 9.2: report works with no feedback data."""
        db = MagicMock()

        svc = LearningAgentService()

        with patch.object(svc, "get_weekly_trends") as mock_trends:
            mock_trends.return_value = []

            count_chain = MagicMock()
            count_chain.count.return_value = 0

            suggestion_chain = MagicMock()
            suggestion_chain.filter.return_value.order_by.return_value.all.return_value = []

            metric_chain = MagicMock()
            metric_chain.filter.return_value.all.return_value = []

            db.query.side_effect = [count_chain, suggestion_chain, metric_chain]

            report = svc.generate_analytics_report(db)

        assert report.total_feedback_count == 0
        assert report.accuracy_trends == []
        assert report.fpr_trends == []
        assert report.top_patterns == []
        assert report.model_comparison == []


# ---------------------------------------------------------------------------
# Tests: detect_accuracy_changes (Req 9.4)
# ---------------------------------------------------------------------------

class TestDetectAccuracyChanges:
    def test_detects_improvement(self):
        """Req 9.4: detects accuracy improvement between consecutive versions."""
        from app.models_learning import LearningPromptVersion
        from app.services.legal.prompt_manager import VersionComparison

        v1 = MagicMock(spec=LearningPromptVersion)
        v1.id = uuid.uuid4()
        v1.version_number = 1
        v1.review_command = "legal_review"

        v2 = MagicMock(spec=LearningPromptVersion)
        v2.id = uuid.uuid4()
        v2.version_number = 2
        v2.review_command = "legal_review"

        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [v1, v2]

        svc = LearningAgentService()

        with patch(
            "app.services.legal.learning_agent.PromptVersionManager"
        ) as MockPVM:
            mock_mgr = MockPVM.return_value
            mock_mgr.compare_versions.return_value = VersionComparison(
                version_a_id=v1.id,
                version_b_id=v2.id,
                version_a_number=1,
                version_b_number=2,
                accuracy_delta=0.15,
                fpr_delta=-0.05,
                total_rated_a=10,
                total_rated_b=15,
            )

            changes = svc.detect_accuracy_changes(db)

        assert len(changes) == 1
        assert changes[0]["from_version"] == 1
        assert changes[0]["to_version"] == 2
        assert changes[0]["accuracy_delta"] == 0.15
        assert changes[0]["fpr_delta"] == -0.05
        assert changes[0]["significant"] is True
        assert changes[0]["direction"] == "improvement"

    def test_detects_regression(self):
        """Req 9.4: detects accuracy regression."""
        from app.models_learning import LearningPromptVersion
        from app.services.legal.prompt_manager import VersionComparison

        v1 = MagicMock(spec=LearningPromptVersion)
        v1.id = uuid.uuid4()
        v1.version_number = 1

        v2 = MagicMock(spec=LearningPromptVersion)
        v2.id = uuid.uuid4()
        v2.version_number = 2

        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [v1, v2]

        svc = LearningAgentService()

        with patch(
            "app.services.legal.learning_agent.PromptVersionManager"
        ) as MockPVM:
            mock_mgr = MockPVM.return_value
            mock_mgr.compare_versions.return_value = VersionComparison(
                version_a_id=v1.id,
                version_b_id=v2.id,
                version_a_number=1,
                version_b_number=2,
                accuracy_delta=-0.10,
                fpr_delta=0.08,
                total_rated_a=10,
                total_rated_b=15,
            )

            changes = svc.detect_accuracy_changes(db)

        assert len(changes) == 1
        assert changes[0]["significant"] is True
        assert changes[0]["direction"] == "regression"

    def test_stable_when_small_delta(self):
        """Req 9.4: small accuracy changes are marked as stable."""
        from app.models_learning import LearningPromptVersion
        from app.services.legal.prompt_manager import VersionComparison

        v1 = MagicMock(spec=LearningPromptVersion)
        v1.id = uuid.uuid4()
        v1.version_number = 1

        v2 = MagicMock(spec=LearningPromptVersion)
        v2.id = uuid.uuid4()
        v2.version_number = 2

        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [v1, v2]

        svc = LearningAgentService()

        with patch(
            "app.services.legal.learning_agent.PromptVersionManager"
        ) as MockPVM:
            mock_mgr = MockPVM.return_value
            mock_mgr.compare_versions.return_value = VersionComparison(
                version_a_id=v1.id,
                version_b_id=v2.id,
                version_a_number=1,
                version_b_number=2,
                accuracy_delta=0.02,
                fpr_delta=-0.01,
                total_rated_a=10,
                total_rated_b=15,
            )

            changes = svc.detect_accuracy_changes(db)

        assert len(changes) == 1
        assert changes[0]["significant"] is False
        assert changes[0]["direction"] == "stable"

    def test_returns_empty_for_single_version(self):
        """Req 9.4: no changes when only one version exists."""
        from app.models_learning import LearningPromptVersion

        v1 = MagicMock(spec=LearningPromptVersion)
        v1.id = uuid.uuid4()
        v1.version_number = 1

        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [v1]

        svc = LearningAgentService()
        changes = svc.detect_accuracy_changes(db)

        assert changes == []

    def test_returns_empty_for_no_versions(self):
        """Req 9.4: no changes when no versions exist."""
        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []

        svc = LearningAgentService()
        changes = svc.detect_accuracy_changes(db)

        assert changes == []

    def test_handles_none_accuracy_delta(self):
        """Req 9.4: handles case where no metrics exist for versions."""
        from app.models_learning import LearningPromptVersion
        from app.services.legal.prompt_manager import VersionComparison

        v1 = MagicMock(spec=LearningPromptVersion)
        v1.id = uuid.uuid4()
        v1.version_number = 1

        v2 = MagicMock(spec=LearningPromptVersion)
        v2.id = uuid.uuid4()
        v2.version_number = 2

        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [v1, v2]

        svc = LearningAgentService()

        with patch(
            "app.services.legal.learning_agent.PromptVersionManager"
        ) as MockPVM:
            mock_mgr = MockPVM.return_value
            mock_mgr.compare_versions.return_value = VersionComparison(
                version_a_id=v1.id,
                version_b_id=v2.id,
                version_a_number=1,
                version_b_number=2,
                accuracy_delta=None,
                fpr_delta=None,
                total_rated_a=0,
                total_rated_b=0,
            )

            changes = svc.detect_accuracy_changes(db)

        assert len(changes) == 1
        assert changes[0]["accuracy_delta"] is None
        assert changes[0]["significant"] is False
        assert changes[0]["direction"] == "stable"

    def test_multiple_consecutive_versions(self):
        """Req 9.4: compares all consecutive pairs."""
        from app.models_learning import LearningPromptVersion
        from app.services.legal.prompt_manager import VersionComparison

        versions = []
        for i in range(1, 4):
            v = MagicMock(spec=LearningPromptVersion)
            v.id = uuid.uuid4()
            v.version_number = i
            versions.append(v)

        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = versions

        svc = LearningAgentService()

        with patch(
            "app.services.legal.learning_agent.PromptVersionManager"
        ) as MockPVM:
            mock_mgr = MockPVM.return_value
            mock_mgr.compare_versions.side_effect = [
                VersionComparison(
                    version_a_id=versions[0].id, version_b_id=versions[1].id,
                    version_a_number=1, version_b_number=2,
                    accuracy_delta=0.10, fpr_delta=-0.03,
                    total_rated_a=10, total_rated_b=15,
                ),
                VersionComparison(
                    version_a_id=versions[1].id, version_b_id=versions[2].id,
                    version_a_number=2, version_b_number=3,
                    accuracy_delta=-0.08, fpr_delta=0.04,
                    total_rated_a=15, total_rated_b=20,
                ),
            ]

            changes = svc.detect_accuracy_changes(db)

        assert len(changes) == 2
        assert changes[0]["direction"] == "improvement"
        assert changes[1]["direction"] == "regression"
