"""Prompt version manager with single-active-per-command invariant."""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models_learning import LearningPerformanceMetric, LearningPromptVersion

logger = logging.getLogger(__name__)


@dataclass
class VersionComparison:
    """Result of comparing two prompt versions' performance metrics."""

    version_a_id: UUID
    version_b_id: UUID
    version_a_number: int
    version_b_number: int
    accuracy_delta: float | None
    fpr_delta: float | None
    total_rated_a: int
    total_rated_b: int


class PromptVersionManager:
    """Manages versioned system prompts with single-active-per-command invariant."""

    def get_active_prompt(self, db: Session, review_command: str) -> LearningPromptVersion | None:
        """Return the currently active prompt version for a review command, or None."""
        return (
            db.query(LearningPromptVersion)
            .filter(
                LearningPromptVersion.review_command == review_command,
                LearningPromptVersion.is_active.is_(True),
            )
            .first()
        )

    def create_version(
        self,
        db: Session,
        review_command: str,
        prompt_text: str,
        created_by: str | None = None,
    ) -> LearningPromptVersion:
        """Create a new prompt version (inactive by default).

        The version_number is auto-incremented based on the highest existing
        version for the given review_command.
        """
        max_version = (
            db.query(func.max(LearningPromptVersion.version_number))
            .filter(LearningPromptVersion.review_command == review_command)
            .scalar()
        )
        next_version = (max_version or 0) + 1

        version = LearningPromptVersion(
            review_command=review_command,
            version_number=next_version,
            prompt_text=prompt_text,
            is_active=False,
            created_by=created_by,
        )
        db.add(version)
        db.flush()
        return version

    def activate_version(self, db: Session, version_id: UUID) -> LearningPromptVersion:
        """Activate a prompt version, enforcing single-active-per-command.

        1. Look up the target version (raise ValueError if not found).
        2. Deactivate all versions for the same review_command.
        3. Set the target version as active.
        """
        target = db.query(LearningPromptVersion).filter(
            LearningPromptVersion.id == version_id,
        ).first()
        if target is None:
            raise ValueError(f"Prompt version {version_id} not found")

        now = datetime.now(timezone.utc)

        # Deactivate all versions for this command
        db.query(LearningPromptVersion).filter(
            LearningPromptVersion.review_command == target.review_command,
            LearningPromptVersion.is_active.is_(True),
        ).update(
            {"is_active": False, "superseded_at": now},
            synchronize_session="fetch",
        )

        # Activate the target
        target.is_active = True
        target.superseded_at = None
        db.flush()
        return target

    def rollback_to_version(self, db: Session, version_id: UUID) -> LearningPromptVersion:
        """Rollback to a previous prompt version.

        Semantically identical to activate_version — deactivate all versions
        for the same command, then activate the specified one.
        """
        return self.activate_version(db, version_id)

    def list_versions(
        self,
        db: Session,
        review_command: str,
    ) -> list[LearningPromptVersion]:
        """List all prompt versions for a review command, ordered by version number descending."""
        return (
            db.query(LearningPromptVersion)
            .filter(LearningPromptVersion.review_command == review_command)
            .order_by(LearningPromptVersion.version_number.desc())
            .all()
        )

    def compare_versions(
        self,
        db: Session,
        version_a_id: UUID,
        version_b_id: UUID,
    ) -> VersionComparison:
        """Compare performance metrics between two prompt versions.

        Queries the latest LearningPerformanceMetric for each version and
        returns the delta in accuracy and false-positive rate.
        """
        version_a = db.query(LearningPromptVersion).filter(
            LearningPromptVersion.id == version_a_id,
        ).first()
        version_b = db.query(LearningPromptVersion).filter(
            LearningPromptVersion.id == version_b_id,
        ).first()

        if version_a is None:
            raise ValueError(f"Prompt version {version_a_id} not found")
        if version_b is None:
            raise ValueError(f"Prompt version {version_b_id} not found")

        metric_a = (
            db.query(LearningPerformanceMetric)
            .filter(LearningPerformanceMetric.prompt_version_id == version_a_id)
            .order_by(LearningPerformanceMetric.computed_at.desc())
            .first()
        )
        metric_b = (
            db.query(LearningPerformanceMetric)
            .filter(LearningPerformanceMetric.prompt_version_id == version_b_id)
            .order_by(LearningPerformanceMetric.computed_at.desc())
            .first()
        )

        accuracy_delta: float | None = None
        fpr_delta: float | None = None

        if metric_a is not None and metric_b is not None:
            accuracy_delta = metric_b.accuracy_score - metric_a.accuracy_score
            fpr_delta = metric_b.false_positive_rate - metric_a.false_positive_rate

        return VersionComparison(
            version_a_id=version_a_id,
            version_b_id=version_b_id,
            version_a_number=version_a.version_number,
            version_b_number=version_b.version_number,
            accuracy_delta=accuracy_delta,
            fpr_delta=fpr_delta,
            total_rated_a=metric_a.total_rated if metric_a else 0,
            total_rated_b=metric_b.total_rated if metric_b else 0,
        )
