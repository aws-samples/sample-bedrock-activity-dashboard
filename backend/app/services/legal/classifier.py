"""Document type classifier using keyword frequency analysis and structural patterns."""

import logging
import re
from collections import defaultdict

from sqlalchemy.orm import Session

from app.models_learning import LearningClassificationLog
from app.schemas_learning import ClassificationResultRead

logger = logging.getLogger(__name__)

# Valid document types produced by the classifier
DOCUMENT_TYPES = ("contract", "nda", "vendor", "general")


class DocumentTypeClassifier:
    """Classifies documents as contract, nda, vendor, or general
    using keyword frequency analysis and structural patterns."""

    # Default keyword weights: doc_type -> {keyword: weight}
    KEYWORD_WEIGHTS: dict[str, dict[str, float]] = {
        "contract": {
            "agreement": 1.0,
            "contract": 1.5,
            "parties": 0.8,
            "obligations": 1.0,
            "termination": 0.9,
            "breach": 1.0,
            "indemnification": 1.2,
            "liability": 0.9,
            "governing law": 1.0,
            "jurisdiction": 0.8,
            "effective date": 0.7,
            "consideration": 0.8,
            "representations": 0.9,
            "warranties": 0.9,
            "covenants": 1.0,
            "amendment": 0.7,
            "execution": 0.6,
        },
        "nda": {
            "confidential": 1.5,
            "non-disclosure": 2.0,
            "nda": 2.0,
            "confidentiality": 1.5,
            "proprietary information": 1.3,
            "trade secret": 1.2,
            "disclosing party": 1.4,
            "receiving party": 1.4,
            "permitted disclosure": 1.0,
            "return of materials": 0.9,
            "non-compete": 0.8,
            "non-solicitation": 0.7,
        },
        "vendor": {
            "vendor": 1.5,
            "supplier": 1.3,
            "procurement": 1.2,
            "purchase order": 1.3,
            "service level": 1.2,
            "sla": 1.2,
            "deliverables": 1.0,
            "payment terms": 0.9,
            "invoice": 0.8,
            "subcontractor": 0.9,
            "scope of work": 1.1,
            "acceptance criteria": 0.8,
            "master service agreement": 1.4,
            "msa": 1.2,
            "statement of work": 1.1,
        },
        "general": {
            "policy": 0.8,
            "procedure": 0.7,
            "guideline": 0.7,
            "memorandum": 0.8,
            "memo": 0.7,
            "notice": 0.6,
            "resolution": 0.6,
            "minutes": 0.6,
            "report": 0.5,
            "summary": 0.5,
        },
    }

    def __init__(self) -> None:
        # Instance-level copy so updates don't mutate the class default
        self._weights: dict[str, dict[str, float]] = {
            doc_type: dict(keywords)
            for doc_type, keywords in self.KEYWORD_WEIGHTS.items()
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def classify(self, text: str) -> ClassificationResultRead:
        """Classify a document's type from its text content.

        Returns a ClassificationResultRead with document_type, confidence
        (0.0–1.0), and per-type scores.
        """
        if not text or not text.strip():
            return ClassificationResultRead(
                document_type="general",
                confidence=0.0,
                scores={dt: 0.0 for dt in DOCUMENT_TYPES},
            )

        raw_scores = self._compute_scores(text)
        scores = self._normalize_scores(raw_scores)

        # Pick the type with the highest normalised score
        best_type = max(scores, key=scores.get)  # type: ignore[arg-type]
        confidence = scores[best_type]

        return ClassificationResultRead(
            document_type=best_type,
            confidence=round(confidence, 4),
            scores={k: round(v, 4) for k, v in scores.items()},
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _compute_scores(self, text: str) -> dict[str, float]:
        """Compute raw weighted keyword frequency scores for each doc type."""
        lower_text = text.lower()
        scores: dict[str, float] = {}

        for doc_type, keywords in self._weights.items():
            score = 0.0
            for keyword, weight in keywords.items():
                # Count non-overlapping occurrences of the keyword
                count = len(re.findall(re.escape(keyword), lower_text))
                score += count * weight
            scores[doc_type] = score

        return scores

    def _normalize_scores(self, scores: dict[str, float]) -> dict[str, float]:
        """Normalize raw scores to the 0.0–1.0 range.

        Uses sum-based normalization. If all scores are zero every type
        gets an equal share (0.25 for 4 types).
        """
        total = sum(scores.values())
        if total == 0:
            equal_share = 1.0 / len(DOCUMENT_TYPES)
            return {dt: equal_share for dt in DOCUMENT_TYPES}

        return {dt: scores.get(dt, 0.0) / total for dt in DOCUMENT_TYPES}

    # ------------------------------------------------------------------
    # Learning from overrides
    # ------------------------------------------------------------------

    def update_weights_from_overrides(self, db: Session) -> None:
        """Query the learning_classification_logs table for cases where
        users overrode the classification and adjust keyword weights.

        Strategy: for each override, slightly boost keywords of the
        user-chosen type and slightly reduce keywords of the originally
        classified type.
        """
        try:
            overrides = (
                db.query(LearningClassificationLog)
                .filter(LearningClassificationLog.user_override_type.isnot(None))
                .all()
            )

            if not overrides:
                return

            # Collect adjustment counts per doc type
            boost_counts: dict[str, int] = defaultdict(int)
            reduce_counts: dict[str, int] = defaultdict(int)

            for log in overrides:
                override_type = log.user_override_type
                classified_type = log.classified_type

                if override_type == classified_type:
                    continue  # no adjustment needed

                if override_type in self._weights:
                    boost_counts[override_type] += 1
                if classified_type in self._weights:
                    reduce_counts[classified_type] += 1

            # Apply adjustments: 5% boost / 3% reduction per override instance
            boost_factor = 0.05
            reduce_factor = 0.03

            for doc_type, count in boost_counts.items():
                multiplier = 1.0 + (boost_factor * count)
                self._weights[doc_type] = {
                    kw: w * multiplier
                    for kw, w in self._weights[doc_type].items()
                }

            for doc_type, count in reduce_counts.items():
                multiplier = max(0.1, 1.0 - (reduce_factor * count))
                self._weights[doc_type] = {
                    kw: w * multiplier
                    for kw, w in self._weights[doc_type].items()
                }

            logger.info(
                "Updated classifier weights from overrides",
                extra={
                    "override_count": len(overrides),
                    "boosted_types": dict(boost_counts),
                    "reduced_types": dict(reduce_counts),
                },
            )

        except Exception as e:
            logger.error(
                "Failed to update classifier weights from overrides",
                extra={"error": str(e)},
            )
