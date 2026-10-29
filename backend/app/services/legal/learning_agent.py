"""Learning Agent Service — feedback collection, metrics computation, and model recommendations.

Orchestrates the feedback loop: users rate findings, the system computes
performance metrics per model / document-type / prompt-version, and surfaces
insights for better review configuration.
"""

import logging
import time
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models_learning import (
    LearningFeedback,
    LearningPerformanceMetric,
    LearningPromptSuggestion,
    LearningPromptVersion,
)
from app.models_legal import LegalReviewFinding, LegalReviewRequest
from app.schemas_learning import (
    AnalyticsReportRead,
    ModelRecommendationRead,
    PerformanceMetricsRead,
    PromptSuggestionRead,
    WeeklyTrendRead,
)
from app.services.legal.prompt_manager import PromptVersionManager

logger = logging.getLogger(__name__)

# Low-confidence threshold: fewer than this many rated findings → low confidence
LOW_CONFIDENCE_THRESHOLD = 5

# Default model used when no recommendation can be made
DEFAULT_MODEL = "claude-opus-4.6"


class LearningAgentService:
    """Orchestrates feedback collection, metrics computation,
    model recommendations, document classification, and prompt suggestions."""

    # ------------------------------------------------------------------
    # Feedback submission (upsert)
    # ------------------------------------------------------------------

    def submit_feedback(
        self,
        db: Session,
        user_id: str,
        finding_id: uuid.UUID,
        rating: str,
        classification: str | None = None,
        comment: str | None = None,
    ) -> LearningFeedback:
        """Submit or update feedback on a finding.

        Upsert logic: if a record for (finding_id, user_id) already exists,
        update it; otherwise create a new one.  The model_id, document_type,
        and prompt_version_id are derived from the finding's review request.
        """
        # Resolve review context from the finding
        finding = (
            db.query(LegalReviewFinding)
            .filter(LegalReviewFinding.id == finding_id)
            .first()
        )
        if finding is None:
            raise ValueError(f"Finding {finding_id} not found")

        review: LegalReviewRequest = finding.review_request
        model_id = review.model_id or "default"
        prompt_version_id = review.prompt_version_id

        # Determine document_type from the review's document
        document = review.document
        document_type = getattr(document, "document_type", None) or "general"
        # If the document model doesn't have a document_type attribute,
        # fall back to "general"
        if not isinstance(document_type, str) or not document_type.strip():
            document_type = "general"

        now = datetime.now(timezone.utc)

        # Check for existing feedback (upsert)
        existing = (
            db.query(LearningFeedback)
            .filter(
                LearningFeedback.finding_id == finding_id,
                LearningFeedback.user_id == user_id,
            )
            .first()
        )

        if existing is not None:
            # Update existing record
            existing.rating = rating
            existing.classification = classification
            existing.comment = comment
            existing.updated_at = now
            db.flush()
            logger.info(
                "Updated feedback",
                extra={
                    "feedback_id": str(existing.id),
                    "finding_id": str(finding_id),
                    "user_id": user_id,
                },
            )
            return existing

        # Create new feedback record
        feedback = LearningFeedback(
            finding_id=finding_id,
            user_id=user_id,
            rating=rating,
            classification=classification,
            comment=comment,
            model_id=model_id,
            prompt_version_id=prompt_version_id,
            document_type=document_type,
            created_at=now,
            updated_at=now,
        )
        db.add(feedback)
        db.flush()
        logger.info(
            "Created feedback",
            extra={
                "feedback_id": str(feedback.id),
                "finding_id": str(finding_id),
                "user_id": user_id,
            },
        )
        return feedback

    # ------------------------------------------------------------------
    # Feedback retrieval
    # ------------------------------------------------------------------

    def get_feedback(
        self,
        db: Session,
        user_id: str,
        finding_id: uuid.UUID,
    ) -> LearningFeedback | None:
        """Return the feedback record for a (user_id, finding_id) pair, or None."""
        return (
            db.query(LearningFeedback)
            .filter(
                LearningFeedback.finding_id == finding_id,
                LearningFeedback.user_id == user_id,
            )
            .first()
        )

    # ------------------------------------------------------------------
    # Metrics computation
    # ------------------------------------------------------------------

    def compute_metrics(
        self,
        db: Session,
        model_id: str | None = None,
        document_type: str | None = None,
    ) -> LearningPerformanceMetric:
        """Compute and persist performance metrics for a given scope.

        Scope is determined by the combination of model_id and document_type:
        - Both None → global (not typically used, but supported)
        - model_id only → scope_type = "model"
        - document_type only → scope_type = "document_type"
        - Both → scope_type = "model_document_type"

        Formulas:
            accuracy_score = positive_count / total_rated
            false_positive_rate = false_positive_count / total_rated
            is_low_confidence = total_rated < LOW_CONFIDENCE_THRESHOLD
        """
        # Determine scope type
        if model_id and document_type:
            scope_type = "model_document_type"
        elif model_id:
            scope_type = "model"
        elif document_type:
            scope_type = "document_type"
        else:
            scope_type = "global"

        # Build query with scope filters
        query = db.query(LearningFeedback)
        if model_id:
            query = query.filter(LearningFeedback.model_id == model_id)
        if document_type:
            query = query.filter(LearningFeedback.document_type == document_type)

        feedback_records = query.all()
        total_rated = len(feedback_records)

        positive_count = sum(1 for f in feedback_records if f.rating == "positive")
        negative_count = sum(1 for f in feedback_records if f.rating == "negative")
        false_positive_count = sum(
            1 for f in feedback_records if f.classification == "false_positive"
        )

        if total_rated > 0:
            accuracy_score = positive_count / total_rated
            false_positive_rate = false_positive_count / total_rated
        else:
            accuracy_score = 0.0
            false_positive_rate = 0.0

        is_low_confidence = total_rated < LOW_CONFIDENCE_THRESHOLD

        now = datetime.now(timezone.utc)

        # Upsert: remove old metric for this exact scope, then insert new
        old_query = db.query(LearningPerformanceMetric).filter(
            LearningPerformanceMetric.scope_type == scope_type,
        )
        if model_id:
            old_query = old_query.filter(LearningPerformanceMetric.model_id == model_id)
        else:
            old_query = old_query.filter(LearningPerformanceMetric.model_id.is_(None))
        if document_type:
            old_query = old_query.filter(
                LearningPerformanceMetric.document_type == document_type
            )
        else:
            old_query = old_query.filter(
                LearningPerformanceMetric.document_type.is_(None)
            )
        old_query.delete(synchronize_session="fetch")

        metric = LearningPerformanceMetric(
            scope_type=scope_type,
            model_id=model_id,
            document_type=document_type,
            accuracy_score=accuracy_score,
            false_positive_rate=false_positive_rate,
            total_rated=total_rated,
            positive_count=positive_count,
            negative_count=negative_count,
            false_positive_count=false_positive_count,
            is_low_confidence=is_low_confidence,
            computed_at=now,
        )
        db.add(metric)
        db.flush()

        logger.info(
            "Computed metrics",
            extra={
                "scope_type": scope_type,
                "model_id": model_id,
                "document_type": document_type,
                "total_rated": total_rated,
                "accuracy_score": accuracy_score,
                "false_positive_rate": false_positive_rate,
                "is_low_confidence": is_low_confidence,
            },
        )
        return metric

    def recompute_all_metrics(self, db: Session) -> list[LearningPerformanceMetric]:
        """Recompute metrics for every distinct (model_id, document_type) scope.

        Produces three kinds of metrics per unique value:
        1. Per model_id (scope_type = "model")
        2. Per document_type (scope_type = "document_type")
        3. Per (model_id, document_type) combination (scope_type = "model_document_type")

        Returns the full list of newly computed metric records.
        """
        # Discover distinct model_ids and document_types from feedback
        distinct_models = [
            row[0]
            for row in db.query(LearningFeedback.model_id).distinct().all()
        ]
        distinct_doc_types = [
            row[0]
            for row in db.query(LearningFeedback.document_type).distinct().all()
        ]

        metrics: list[LearningPerformanceMetric] = []

        # Per model
        for mid in distinct_models:
            metrics.append(self.compute_metrics(db, model_id=mid, document_type=None))

        # Per document type
        for dt in distinct_doc_types:
            metrics.append(self.compute_metrics(db, model_id=None, document_type=dt))

        # Per combination
        distinct_combos = (
            db.query(LearningFeedback.model_id, LearningFeedback.document_type)
            .distinct()
            .all()
        )
        for mid, dt in distinct_combos:
            metrics.append(self.compute_metrics(db, model_id=mid, document_type=dt))

        logger.info(
            "Recomputed all metrics",
            extra={"metric_count": len(metrics)},
        )
        return metrics

    # ------------------------------------------------------------------
    # Model recommendation
    # ------------------------------------------------------------------

    def get_model_recommendation(
        self,
        db: Session,
        document_type: str,
    ) -> ModelRecommendationRead:
        """Recommend the best model for a document type based on accuracy metrics.

        Queries model_document_type-scoped metrics for the given document_type,
        filters out low-confidence entries, and ranks by accuracy_score descending.

        Returns the default model (claude-opus-4.6) with confidence="insufficient_data"
        when no metrics exist or all are low confidence.

        Includes a notification message when the recommended model's accuracy is
        10+ percentage points better than the default model's accuracy.
        """
        # Query metrics scoped to model_document_type for this document type
        metrics = (
            db.query(LearningPerformanceMetric)
            .filter(
                LearningPerformanceMetric.scope_type == "model_document_type",
                LearningPerformanceMetric.document_type == document_type,
            )
            .all()
        )

        # Filter out low-confidence metrics
        confident_metrics = [m for m in metrics if not m.is_low_confidence]

        # No metrics or all low confidence → return default
        if not confident_metrics:
            return ModelRecommendationRead(
                recommended_model=DEFAULT_MODEL,
                accuracy_score=0.0,
                false_positive_rate=0.0,
                rated_finding_count=0,
                confidence="insufficient_data",
                message="Insufficient data to recommend a model. Using default.",
            )

        # Rank by accuracy_score descending, pick the best
        confident_metrics.sort(key=lambda m: m.accuracy_score, reverse=True)
        best = confident_metrics[0]

        # Determine the default model's accuracy for this document type
        default_metric = next(
            (m for m in confident_metrics if m.model_id == DEFAULT_MODEL),
            None,
        )
        default_accuracy = default_metric.accuracy_score if default_metric else 0.0

        # Notification when recommendation is 10+ points better than default
        accuracy_diff = best.accuracy_score - default_accuracy
        message = None
        if best.model_id != DEFAULT_MODEL and accuracy_diff >= 0.10:
            message = (
                f"{best.model_id} is {accuracy_diff * 100:.0f} percentage points "
                f"more accurate than the default model for {document_type} documents."
            )

        return ModelRecommendationRead(
            recommended_model=best.model_id,
            accuracy_score=best.accuracy_score,
            false_positive_rate=best.false_positive_rate,
            rated_finding_count=best.total_rated,
            confidence="high",
            message=message,
        )

    # ------------------------------------------------------------------
    # Feedback pattern identification (Req 5.1)
    # ------------------------------------------------------------------

    def identify_feedback_patterns(
        self,
        db: Session,
    ) -> list[dict]:
        """Identify recurring false-positive feedback patterns.

        Queries LearningFeedback where classification='false_positive',
        joins with LegalReviewFinding to obtain affected_clause and risk_level,
        and groups by (affected_clause, document_type, risk_level).

        Returns a list of pattern dicts, each containing:
            - affected_clause: the clause text (or "unknown")
            - document_type: the document type from the feedback record
            - risk_level: the risk level from the finding
            - occurrence_count: how many false-positive feedback records match
        """
        # Fetch all false_positive feedback joined with their findings
        rows = (
            db.query(LearningFeedback, LegalReviewFinding)
            .join(
                LegalReviewFinding,
                LearningFeedback.finding_id == LegalReviewFinding.id,
            )
            .filter(LearningFeedback.classification == "false_positive")
            .all()
        )

        # Group by (affected_clause, document_type, risk_level)
        groups: dict[tuple[str, str, str], int] = defaultdict(int)
        for feedback, finding in rows:
            clause = finding.affected_clause or "unknown"
            doc_type = feedback.document_type or "general"
            risk = finding.risk_level or "unknown"
            key = (clause, doc_type, risk)
            groups[key] += 1

        patterns = [
            {
                "affected_clause": clause,
                "document_type": doc_type,
                "risk_level": risk,
                "occurrence_count": count,
            }
            for (clause, doc_type, risk), count in groups.items()
        ]

        # Sort by occurrence count descending for convenience
        patterns.sort(key=lambda p: p["occurrence_count"], reverse=True)

        logger.info(
            "Identified feedback patterns",
            extra={"pattern_count": len(patterns)},
        )
        return patterns

    # ------------------------------------------------------------------
    # Prompt suggestion generation (Req 5.2, 5.3)
    # ------------------------------------------------------------------

    def generate_prompt_suggestions(
        self,
        db: Session,
        review_command: str = "legal_review",
    ) -> list[LearningPromptSuggestion]:
        """Generate prompt improvement suggestions for patterns with 3+ occurrences.

        Calls identify_feedback_patterns, then for each pattern meeting the
        threshold creates a LearningPromptSuggestion with status='pending'.

        Returns the list of newly created suggestion records.
        """
        patterns = self.identify_feedback_patterns(db)
        suggestions: list[LearningPromptSuggestion] = []

        for pattern in patterns:
            if pattern["occurrence_count"] < 3:
                continue

            clause = pattern["affected_clause"]
            doc_type = pattern["document_type"]
            risk = pattern["risk_level"]
            count = pattern["occurrence_count"]

            pattern_desc = (
                f"False positive pattern: '{clause}' clauses in {doc_type} "
                f"documents at {risk} risk level ({count} occurrences)"
            )
            suggested_change = (
                f"Add an exception or refinement to the prompt for {doc_type} "
                f"documents: when encountering '{clause}' clauses at {risk} risk "
                f"level, reduce the likelihood of flagging as a finding since "
                f"users have consistently marked these as false positives."
            )

            suggestion = LearningPromptSuggestion(
                review_command=review_command,
                pattern_description=pattern_desc,
                suggested_prompt_change=suggested_change,
                supporting_feedback_count=count,
                status="pending",
            )
            db.add(suggestion)
            suggestions.append(suggestion)

        db.flush()

        logger.info(
            "Generated prompt suggestions",
            extra={"suggestion_count": len(suggestions)},
        )
        return suggestions

    # ------------------------------------------------------------------
    # Suggestion approval (Req 5.4)
    # ------------------------------------------------------------------

    def approve_suggestion(
        self,
        db: Session,
        suggestion_id: uuid.UUID,
        user_id: str,
    ) -> "LearningPromptVersion":
        """Approve a prompt suggestion, creating and activating a new prompt version.

        1. Load the suggestion (raise ValueError if not found or not pending).
        2. Mark the suggestion as approved.
        3. Get the current active prompt for the suggestion's review_command.
        4. Create a new prompt version with the suggested change appended.
        5. Activate the new version (deactivates the old one).
        6. Return the new prompt version.
        """

        suggestion = (
            db.query(LearningPromptSuggestion)
            .filter(LearningPromptSuggestion.id == suggestion_id)
            .first()
        )
        if suggestion is None:
            raise ValueError(f"Suggestion {suggestion_id} not found")
        if suggestion.status != "pending":
            raise ValueError(
                f"Suggestion {suggestion_id} is already {suggestion.status}"
            )

        # Mark suggestion as approved
        now = datetime.now(timezone.utc)
        suggestion.status = "approved"
        suggestion.resolved_at = now
        suggestion.resolved_by = user_id

        # Get current active prompt to use as base
        prompt_mgr = PromptVersionManager()
        active_prompt = prompt_mgr.get_active_prompt(db, suggestion.review_command)

        if active_prompt is not None:
            base_text = active_prompt.prompt_text
        else:
            base_text = ""

        # Build new prompt text by appending the suggested change
        new_prompt_text = (
            f"{base_text}\n\n"
            f"# Prompt refinement (from feedback pattern)\n"
            f"{suggestion.suggested_prompt_change}"
        ).strip()

        # Create and activate the new version
        new_version = prompt_mgr.create_version(
            db,
            review_command=suggestion.review_command,
            prompt_text=new_prompt_text,
            created_by=user_id,
        )
        prompt_mgr.activate_version(db, new_version.id)

        logger.info(
            "Approved suggestion and created new prompt version",
            extra={
                "suggestion_id": str(suggestion_id),
                "new_version_id": str(new_version.id),
                "user_id": user_id,
            },
        )
        return new_version

    # ------------------------------------------------------------------
    # Weekly trends (Req 9.1)
    # ------------------------------------------------------------------

    def get_weekly_trends(
        self,
        db: Session,
        model_id: str | None = None,
        document_type: str | None = None,
        weeks: int = 12,
    ) -> list[WeeklyTrendRead]:
        """Compute per-week accuracy and false-positive rate trends.

        Groups LearningFeedback by ISO week, optionally filtered by model_id
        and/or document_type.  Returns up to *weeks* most recent entries,
        ordered chronologically (oldest first).
        """
        query = db.query(LearningFeedback)
        if model_id:
            query = query.filter(LearningFeedback.model_id == model_id)
        if document_type:
            query = query.filter(LearningFeedback.document_type == document_type)

        feedback_records = query.all()

        # Bucket feedback by ISO week start (Monday)
        weekly_buckets: dict[date, list[LearningFeedback]] = defaultdict(list)
        for fb in feedback_records:
            ts = fb.created_at
            # ISO weekday: Monday=1 … Sunday=7
            week_start = (ts - timedelta(days=ts.isoweekday() - 1)).date()
            weekly_buckets[week_start].append(fb)

        # Build trend entries
        trends: list[WeeklyTrendRead] = []
        for week_start in sorted(weekly_buckets.keys()):
            bucket = weekly_buckets[week_start]
            total = len(bucket)
            positive = sum(1 for f in bucket if f.rating == "positive")
            fp = sum(1 for f in bucket if f.classification == "false_positive")

            accuracy = positive / total if total > 0 else 0.0
            fpr = fp / total if total > 0 else 0.0

            trends.append(
                WeeklyTrendRead(
                    week_start=week_start,
                    accuracy_score=accuracy,
                    false_positive_rate=fpr,
                    total_rated=total,
                    model_id=model_id,
                    document_type=document_type,
                )
            )

        # Return only the most recent *weeks* entries
        trends = trends[-weeks:]

        logger.info(
            "Computed weekly trends",
            extra={
                "model_id": model_id,
                "document_type": document_type,
                "weeks_returned": len(trends),
            },
        )
        return trends

    # ------------------------------------------------------------------
    # Analytics report (Req 9.2)
    # ------------------------------------------------------------------

    def generate_analytics_report(
        self,
        db: Session,
    ) -> AnalyticsReportRead:
        """Generate a full feedback analytics report.

        Returns an AnalyticsReportRead containing:
        - total_feedback_count: total number of feedback records
        - accuracy_trends: weekly accuracy trends (all models/doc types)
        - fpr_trends: weekly FPR trends (same data, duplicated for
          convenience — the schema separates them for charting)
        - top_patterns: prompt suggestions from generate_prompt_suggestions
        - model_comparison: per-model performance metrics
        """
        total_feedback_count = db.query(LearningFeedback).count()

        # Weekly trends (unfiltered)
        weekly = self.get_weekly_trends(db)

        # accuracy_trends and fpr_trends share the same weekly data
        accuracy_trends = weekly
        fpr_trends = weekly

        # Top patterns → prompt suggestions (pending ones)
        existing_suggestions = (
            db.query(LearningPromptSuggestion)
            .filter(LearningPromptSuggestion.status == "pending")
            .order_by(LearningPromptSuggestion.supporting_feedback_count.desc())
            .all()
        )
        top_patterns = [
            PromptSuggestionRead.model_validate(s) for s in existing_suggestions
        ]

        # Model comparison: per-model metrics
        model_metrics = (
            db.query(LearningPerformanceMetric)
            .filter(LearningPerformanceMetric.scope_type == "model")
            .all()
        )
        model_comparison = [
            PerformanceMetricsRead.model_validate(m) for m in model_metrics
        ]

        logger.info(
            "Generated analytics report",
            extra={
                "total_feedback_count": total_feedback_count,
                "trend_weeks": len(weekly),
                "top_patterns": len(top_patterns),
                "models_compared": len(model_comparison),
            },
        )
        return AnalyticsReportRead(
            total_feedback_count=total_feedback_count,
            accuracy_trends=accuracy_trends,
            fpr_trends=fpr_trends,
            top_patterns=top_patterns,
            model_comparison=model_comparison,
        )

    # ------------------------------------------------------------------
    # Accuracy change detection between prompt versions (Req 9.4)
    # ------------------------------------------------------------------

    def detect_accuracy_changes(
        self,
        db: Session,
        review_command: str = "legal_review",
    ) -> list[dict]:
        """Detect accuracy changes between consecutive prompt versions.

        Queries all prompt versions for the given review_command ordered by
        version_number, then compares each consecutive pair using the
        PromptVersionManager.compare_versions method.

        Returns a list of dicts, each containing:
        - from_version: version_number of the earlier version
        - to_version: version_number of the later version
        - accuracy_delta: change in accuracy (positive = improvement)
        - fpr_delta: change in FPR (negative = improvement)
        - significant: True if |accuracy_delta| >= 0.05 (5 percentage points)
        - direction: "improvement", "regression", or "stable"
        """
        prompt_mgr = PromptVersionManager()
        versions = (
            db.query(LearningPromptVersion)
            .filter(LearningPromptVersion.review_command == review_command)
            .order_by(LearningPromptVersion.version_number.asc())
            .all()
        )

        if len(versions) < 2:
            return []

        changes: list[dict] = []
        for i in range(len(versions) - 1):
            v_a = versions[i]
            v_b = versions[i + 1]

            comparison = prompt_mgr.compare_versions(db, v_a.id, v_b.id)

            accuracy_delta = comparison.accuracy_delta
            fpr_delta = comparison.fpr_delta

            # Determine significance and direction
            significant = (
                accuracy_delta is not None and abs(accuracy_delta) >= 0.05
            )
            if accuracy_delta is None:
                direction = "stable"
            elif accuracy_delta >= 0.05:
                direction = "improvement"
            elif accuracy_delta <= -0.05:
                direction = "regression"
            else:
                direction = "stable"

            changes.append(
                {
                    "from_version": v_a.version_number,
                    "to_version": v_b.version_number,
                    "from_version_id": v_a.id,
                    "to_version_id": v_b.id,
                    "accuracy_delta": accuracy_delta,
                    "fpr_delta": fpr_delta,
                    "significant": significant,
                    "direction": direction,
                }
            )

        logger.info(
            "Detected accuracy changes between prompt versions",
            extra={
                "review_command": review_command,
                "comparisons": len(changes),
            },
        )
        return changes


# ======================================================================
# Graceful degradation wrappers (Req 10.1–10.5)
# ======================================================================

# Retry configuration for feedback DB writes
FEEDBACK_RETRY_COUNT = 3
FEEDBACK_RETRY_DELAY_S = 0.5


def _correlation_id() -> str:
    """Generate a unique correlation ID for tracing operations."""
    return str(uuid.uuid4())


def get_recommendation_safe(
    db: Session,
    document_type: str,
    correlation_id: str | None = None,
) -> ModelRecommendationRead:
    """Return a model recommendation, falling back to defaults on any error.

    Req 10.3: If the Learning Agent fails to generate a Model_Recommendation,
    use the default model without displaying a recommendation.
    """
    cid = correlation_id or _correlation_id()
    try:
        service = LearningAgentService()
        result = service.get_model_recommendation(db, document_type)
        logger.info(
            "Recommendation generated",
            extra={"correlation_id": cid, "document_type": document_type},
        )
        return result
    except Exception as e:
        logger.error(
            "Recommendation failed, using default",
            extra={"correlation_id": cid, "document_type": document_type, "error": str(e)},
        )
        return ModelRecommendationRead(
            recommended_model=DEFAULT_MODEL,
            accuracy_score=0.0,
            false_positive_rate=0.0,
            rated_finding_count=0,
            confidence="insufficient_data",
            message="Unable to generate recommendation. Using default model.",
        )


def classify_safe(
    text: str,
    correlation_id: str | None = None,
) -> dict:
    """Classify a document type, falling back to general on any error.

    Req 10.2: If the Learning Agent fails to classify a Document_Type,
    fall back to manual document type selection (general, confidence 0.0).
    """
    cid = correlation_id or _correlation_id()
    try:
        from app.services.legal.classifier import DocumentTypeClassifier

        classifier = DocumentTypeClassifier()
        result = classifier.classify(text)
        logger.info(
            "Classification completed",
            extra={"correlation_id": cid, "document_type": result.document_type},
        )
        return {
            "document_type": result.document_type,
            "confidence": result.confidence,
            "scores": result.scores,
        }
    except Exception as e:
        logger.error(
            "Classification failed, using fallback",
            extra={"correlation_id": cid, "error": str(e)},
        )
        return {"document_type": "general", "confidence": 0.0, "scores": {}}


def compute_metrics_safe(
    db: Session,
    model_id: str | None = None,
    document_type: str | None = None,
    correlation_id: str | None = None,
) -> dict:
    """Compute metrics with fallback to empty metrics on error.

    Req 10.1: If the Learning Agent fails to compute Performance_Metrics,
    continue with the default model and prompt configuration.
    """
    cid = correlation_id or _correlation_id()
    try:
        service = LearningAgentService()
        metric = service.compute_metrics(db, model_id=model_id, document_type=document_type)
        logger.info(
            "Metrics computed",
            extra={
                "correlation_id": cid,
                "model_id": model_id,
                "document_type": document_type,
            },
        )
        return {
            "accuracy_score": metric.accuracy_score,
            "false_positive_rate": metric.false_positive_rate,
            "total_rated": metric.total_rated,
            "is_low_confidence": metric.is_low_confidence,
            "error": False,
        }
    except Exception as e:
        logger.error(
            "Metrics computation failed",
            extra={
                "correlation_id": cid,
                "model_id": model_id,
                "document_type": document_type,
                "error": str(e),
            },
        )
        return {
            "accuracy_score": 0.0,
            "false_positive_rate": 0.0,
            "total_rated": 0,
            "is_low_confidence": True,
            "error": True,
        }


def submit_feedback_with_retry(
    db: Session,
    user_id: str,
    finding_id: uuid.UUID,
    rating: str,
    classification: str | None = None,
    comment: str | None = None,
    correlation_id: str | None = None,
) -> LearningFeedback:
    """Submit feedback with retry logic (3 retries, 500ms delay).

    Req 10.5: If the Feedback submission fails due to a database error,
    retry the write up to 3 times before raising.
    """
    cid = correlation_id or _correlation_id()
    service = LearningAgentService()
    last_error: Exception | None = None

    for attempt in range(1, FEEDBACK_RETRY_COUNT + 1):
        try:
            result = service.submit_feedback(
                db=db,
                user_id=user_id,
                finding_id=finding_id,
                rating=rating,
                classification=classification,
                comment=comment,
            )
            logger.info(
                "Feedback submitted",
                extra={
                    "correlation_id": cid,
                    "finding_id": str(finding_id),
                    "attempt": attempt,
                },
            )
            return result
        except Exception as e:
            last_error = e
            logger.warning(
                "Feedback submission attempt failed",
                extra={
                    "correlation_id": cid,
                    "finding_id": str(finding_id),
                    "attempt": attempt,
                    "error": str(e),
                },
            )
            if attempt < FEEDBACK_RETRY_COUNT:
                time.sleep(FEEDBACK_RETRY_DELAY_S)
                db.rollback()

    logger.error(
        "Feedback submission failed after retries",
        extra={
            "correlation_id": cid,
            "finding_id": str(finding_id),
            "retries": FEEDBACK_RETRY_COUNT,
            "error": str(last_error),
        },
    )
    raise last_error  # type: ignore[misc]
