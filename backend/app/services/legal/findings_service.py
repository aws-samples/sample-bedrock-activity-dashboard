"""Findings service for the Legal Review App.

Handles CRUD operations for review requests and findings, including
risk-level sorting, summary computation, pagination, search, and
database write retry logic.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.models_legal import (
    LegalReviewDocument,
    LegalReviewFinding,
    LegalReviewRequest,
)
from app.schemas_legal import (
    FindingRead,
    ReviewListParams,
    ReviewRequestRead,
    ReviewWithFindingsRead,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

RISK_LEVEL_ORDER: dict[str, int] = {
    "high": 0,
    "medium": 1,
    "low": 2,
    "informational": 3,
}

DB_MAX_RETRIES = 3
DB_RETRY_DELAY_SECONDS = 0.5


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class FindingsServiceError(Exception):
    """Base exception for findings service errors."""


class ReviewNotFoundError(FindingsServiceError):
    """Raised when a review request is not found or not accessible."""


class DatabaseWriteError(FindingsServiceError):
    """Raised when a database write fails after all retries."""


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class FindingsService:
    """Manages review requests and findings persistence.

    Features:
    - Risk-level-based sort ordering for findings
    - Summary counts grouped by risk level
    - User-scoped data access
    - Paginated listing with search and date range filtering
    - Database write retry logic (3 retries, 500 ms delay)
    """

    # ------------------------------------------------------------------
    # Create review request
    # ------------------------------------------------------------------

    def create_review_request(
        self,
        db: Session,
        user_id: str,
        document_id: UUID,
        review_command: str,
    ) -> LegalReviewRequest:
        """Create a new review request in *pending* status.

        Uses retry logic for the database write.
        """
        review = LegalReviewRequest(
            user_id=user_id,
            document_id=document_id,
            review_command=review_command,
            status="pending",
        )
        self._commit_with_retry(db, review, operation="create_review_request")
        db.refresh(review)
        logger.info(
            "Created review request",
            extra={"review_request_id": str(review.id), "user_id": user_id},
        )
        return review

    # ------------------------------------------------------------------
    # Store findings
    # ------------------------------------------------------------------

    def store_findings(
        self,
        db: Session,
        review_request_id: UUID,
        findings: list[dict[str, Any]],
    ) -> list[LegalReviewFinding]:
        """Persist parsed findings for a review request.

        Each finding is assigned a ``sort_order`` based on its risk level
        severity: high=0, medium=1, low=2, informational=3.

        Uses retry logic for the database write.
        """
        db_findings: list[LegalReviewFinding] = []

        for finding_data in findings:
            risk_level = (finding_data.get("risk_level") or "informational").lower()
            sort_order = RISK_LEVEL_ORDER.get(risk_level, 3)

            db_finding = LegalReviewFinding(
                review_request_id=review_request_id,
                risk_level=risk_level,
                description=finding_data.get("description", ""),
                affected_clause=finding_data.get("affected_clause"),
                recommended_action=finding_data.get("recommended_action"),
                raw_finding=finding_data.get("raw", finding_data),
                sort_order=sort_order,
            )
            db.add(db_finding)
            db_findings.append(db_finding)

        self._flush_and_commit_with_retry(
            db, operation="store_findings", review_request_id=review_request_id
        )

        for f in db_findings:
            db.refresh(f)

        logger.info(
            "Stored %d findings",
            len(db_findings),
            extra={"review_request_id": str(review_request_id)},
        )
        return db_findings

    # ------------------------------------------------------------------
    # Get review with findings
    # ------------------------------------------------------------------

    def get_review_with_findings(
        self,
        db: Session,
        review_id: UUID,
        user_id: str,
    ) -> ReviewWithFindingsRead:
        """Load a review request with its findings, enforcing user_id scoping.

        Computes summary counts grouped by risk level.

        Raises ``ReviewNotFoundError`` if the review does not exist or
        belongs to a different user.
        """
        review: LegalReviewRequest | None = (
            db.query(LegalReviewRequest)
            .filter(
                LegalReviewRequest.id == review_id,
                LegalReviewRequest.user_id == user_id,
            )
            .first()
        )

        if review is None:
            raise ReviewNotFoundError(
                f"Review {review_id} not found or not accessible."
            )

        # Load findings sorted by sort_order
        findings: list[LegalReviewFinding] = (
            db.query(LegalReviewFinding)
            .filter(LegalReviewFinding.review_request_id == review_id)
            .order_by(LegalReviewFinding.sort_order)
            .all()
        )

        # Compute summary counts
        summary: dict[str, int] = {}
        for f in findings:
            summary[f.risk_level] = summary.get(f.risk_level, 0) + 1

        # Load document for the document name
        document: LegalReviewDocument | None = (
            db.query(LegalReviewDocument)
            .filter(LegalReviewDocument.id == review.document_id)
            .first()
        )
        document_name = document.filename if document else "Unknown"

        review_read = ReviewRequestRead(
            id=review.id,
            document_id=review.document_id,
            review_command=review.review_command,
            status=review.status,
            error_message=review.error_message,
            created_at=review.created_at,
            completed_at=review.completed_at,
            document_name=document_name,
            finding_count=len(findings),
        )

        findings_read = [
            FindingRead(
                id=f.id,
                risk_level=f.risk_level,
                description=f.description,
                affected_clause=f.affected_clause,
                recommended_action=f.recommended_action,
                sort_order=f.sort_order,
            )
            for f in findings
        ]

        return ReviewWithFindingsRead(
            review=review_read,
            findings=findings_read,
            summary=summary,
        )

    # ------------------------------------------------------------------
    # List reviews (paginated, searchable, date-filterable)
    # ------------------------------------------------------------------

    def list_reviews(
        self,
        db: Session,
        user_id: str,
        params: ReviewListParams,
    ) -> dict[str, Any]:
        """Return a paginated list of review requests for *user_id*.

        Supports:
        - Case-insensitive search by document name
        - Date range filtering (``date_start``, ``date_end``)
        - Pagination via ``page`` and ``page_size``

        Returns a dict with ``items``, ``total``, ``page``, and ``page_size``.
        """
        # Base query: join with documents for document name access
        query = (
            db.query(LegalReviewRequest, LegalReviewDocument.filename)
            .join(
                LegalReviewDocument,
                LegalReviewRequest.document_id == LegalReviewDocument.id,
            )
            .filter(LegalReviewRequest.user_id == user_id)
        )

        # Search filter (case-insensitive document name)
        if params.search:
            query = query.filter(
                func.lower(LegalReviewDocument.filename).contains(
                    params.search.lower()
                )
            )

        # Date range filters
        if params.date_start:
            try:
                start_dt = datetime.fromisoformat(params.date_start)
                if start_dt.tzinfo is None:
                    start_dt = start_dt.replace(tzinfo=timezone.utc)
                query = query.filter(LegalReviewRequest.created_at >= start_dt)
            except ValueError:
                logger.warning("Invalid date_start format: %s", params.date_start)

        if params.date_end:
            try:
                end_dt = datetime.fromisoformat(params.date_end)
                if end_dt.tzinfo is None:
                    end_dt = end_dt.replace(tzinfo=timezone.utc)
                query = query.filter(LegalReviewRequest.created_at <= end_dt)
            except ValueError:
                logger.warning("Invalid date_end format: %s", params.date_end)

        # Total count before pagination
        total = query.count()

        # Order by most recent first, then paginate
        rows = (
            query.order_by(LegalReviewRequest.created_at.desc())
            .offset((params.page - 1) * params.page_size)
            .limit(params.page_size)
            .all()
        )

        # Build response items with finding counts
        items: list[ReviewRequestRead] = []
        for review, doc_filename in rows:
            finding_count = (
                db.query(func.count(LegalReviewFinding.id))
                .filter(LegalReviewFinding.review_request_id == review.id)
                .scalar()
            ) or 0

            items.append(
                ReviewRequestRead(
                    id=review.id,
                    document_id=review.document_id,
                    review_command=review.review_command,
                    status=review.status,
                    error_message=review.error_message,
                    created_at=review.created_at,
                    completed_at=review.completed_at,
                    document_name=doc_filename,
                    finding_count=finding_count,
                )
            )

        return {
            "items": items,
            "total": total,
            "page": params.page,
            "page_size": params.page_size,
        }

    # ------------------------------------------------------------------
    # Database retry helpers
    # ------------------------------------------------------------------

    def _commit_with_retry(
        self,
        db: Session,
        obj: Any,
        operation: str,
    ) -> None:
        """Add *obj* and commit with retry logic for transient DB errors."""
        last_error: Exception | None = None

        for attempt in range(1, DB_MAX_RETRIES + 1):
            try:
                db.add(obj)
                db.commit()
                return
            except OperationalError as exc:
                last_error = exc
                db.rollback()
                logger.warning(
                    "DB write failed for %s (attempt %d/%d): %s",
                    operation,
                    attempt,
                    DB_MAX_RETRIES,
                    str(exc),
                )
                if attempt < DB_MAX_RETRIES:
                    time.sleep(DB_RETRY_DELAY_SECONDS)

        raise DatabaseWriteError(
            f"Database write failed for {operation} after {DB_MAX_RETRIES} retries."
        ) from last_error

    def _flush_and_commit_with_retry(
        self,
        db: Session,
        operation: str,
        review_request_id: UUID | None = None,
    ) -> None:
        """Flush and commit pending changes with retry logic."""
        last_error: Exception | None = None
        extra = {}
        if review_request_id:
            extra["review_request_id"] = str(review_request_id)

        for attempt in range(1, DB_MAX_RETRIES + 1):
            try:
                db.flush()
                db.commit()
                return
            except OperationalError as exc:
                last_error = exc
                db.rollback()
                logger.warning(
                    "DB write failed for %s (attempt %d/%d): %s",
                    operation,
                    attempt,
                    DB_MAX_RETRIES,
                    str(exc),
                    extra=extra,
                )
                if attempt < DB_MAX_RETRIES:
                    time.sleep(DB_RETRY_DELAY_SECONDS)

        raise DatabaseWriteError(
            f"Database write failed for {operation} after {DB_MAX_RETRIES} retries."
        ) from last_error
