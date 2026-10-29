"""Legal Review API routes.

Provides endpoints for document upload, review management, findings
retrieval, export, and retry functionality.  All endpoints require
authentication via the Cognito ALB integration and scope data per user.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.models_legal import (
    LegalReviewDocument,
    LegalReviewRequest,
)
from app.schemas_legal import (
    DocumentUploadResponse,
    ReviewListParams,
)
from app.services.legal.anthropic_client import (
    AVAILABLE_MODELS,
    LegalReviewAPIError,
    LegalReviewClient,
    LegalReviewTimeoutError,
)
from app.services.legal.document_parser import (
    DocumentParseError,
    DocumentParser,
    DocumentValidationError,
)
from app.services.legal.export_service import LegalExportService
from app.services.legal.findings_service import (
    DatabaseWriteError,
    FindingsService,
    ReviewNotFoundError,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/legal-review", tags=["legal-review"])

# ---------------------------------------------------------------------------
# Shared service instances
# ---------------------------------------------------------------------------

_document_parser = DocumentParser()
_findings_service = FindingsService()
_export_service = LegalExportService()


# ---------------------------------------------------------------------------
# Auth dependency — extracts user identity from Cognito ALB headers
# ---------------------------------------------------------------------------


def get_current_user_id(
    x_amzn_oidc_identity: Optional[str] = Header(None),
    x_amzn_oidc_data: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None),
) -> str:
    """Extract the authenticated user ID from Cognito ALB headers.

    The ALB + Cognito integration forwards the user identity via the
    ``x-amzn-oidc-identity`` header.  Falls back to ``x-amzn-oidc-data``
    or ``Authorization`` header for flexibility in dev/test environments.

    Raises 401 if no user identity can be determined.
    """
    user_id = x_amzn_oidc_identity or x_amzn_oidc_data or authorization
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required.")
    return user_id


# ---------------------------------------------------------------------------
# Helper to build the Bedrock legal review client (lazy init)
# ---------------------------------------------------------------------------

_legal_review_client: LegalReviewClient | None = None


def _get_legal_review_client() -> LegalReviewClient:
    """Return a cached ``LegalReviewClient`` instance, creating it on first use."""
    global _legal_review_client
    if _legal_review_client is None:
        _legal_review_client = LegalReviewClient()
    return _legal_review_client


# ---------------------------------------------------------------------------
# GET /models — list available Claude models for legal review
# ---------------------------------------------------------------------------


@router.get("/models")
def list_models():
    """Return the available Claude models for legal review."""
    return {
        "models": [
            {"id": key, "name": key, "model_id": model_id}
            for key, model_id in AVAILABLE_MODELS.items()
        ],
        "default": "claude-opus-4.6",
    }


# ---------------------------------------------------------------------------
# POST /upload — upload document, parse, create review, call Bedrock API
# ---------------------------------------------------------------------------


@router.post("/upload", response_model=DocumentUploadResponse)
async def upload_document(
    file: UploadFile = File(...),
    document_type: str = Form(default="general"),
    model_id: str = Form(default="claude-opus-4.6"),
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    """Upload a document for legal review.

    Validates the file, parses its content, creates a review request,
    sends the content to the Anthropic Messages API, and stores the
    resulting findings.
    """
    filename = file.filename or "unknown"
    file_bytes = await file.read()
    file_size = len(file_bytes)

    # --- Validate file format and size ---
    try:
        _document_parser.validate_file(filename, file_size)
    except DocumentValidationError as exc:
        error_msg = str(exc)
        if "size" in error_msg.lower():
            raise HTTPException(status_code=413, detail=error_msg)
        raise HTTPException(status_code=422, detail=error_msg)

    # --- Parse document ---
    try:
        parsed = _document_parser.parse(file_bytes, filename)
    except DocumentParseError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    # --- Store document record ---
    content_type = filename.rsplit(".", 1)[-1] if "." in filename else "txt"
    doc = LegalReviewDocument(
        user_id=user_id,
        filename=filename,
        file_size=file_size,
        content_type=content_type,
        extracted_text=parsed.text,
        section_structure=parsed.sections if parsed.sections else None,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)

    # --- Create review request ---
    review_command = LegalReviewClient.get_command(document_type)
    try:
        review = _findings_service.create_review_request(
            db=db,
            user_id=user_id,
            document_id=doc.id,
            review_command=review_command,
        )
    except DatabaseWriteError as exc:
        logger.error(
            "Failed to create review request",
            extra={"user_id": user_id, "error": str(exc)},
        )
        raise HTTPException(status_code=500, detail="Failed to create review request.")

    review_request_id = review.id
    logger.info(
        "Starting legal review",
        extra={
            "review_request_id": str(review_request_id),
            "filename": filename,
            "document_type": document_type,
        },
    )

    # --- Update status to in_progress ---
    review.status = "in_progress"
    db.commit()

    # --- Call Bedrock API ---
    try:
        client = _get_legal_review_client()
        api_response = await client.submit_review(
            document_text=parsed.text,
            document_type=document_type,
            review_command=review_command,
            model_id=model_id,
        )
    except LegalReviewTimeoutError as exc:
        review.status = "timeout"
        review.error_message = str(exc)
        db.commit()
        logger.error(
            "Anthropic API timeout",
            extra={"review_request_id": str(review_request_id), "error": str(exc)},
        )
        raise HTTPException(status_code=504, detail=str(exc))
    except LegalReviewAPIError as exc:
        error_msg = str(exc)
        review.status = "failed"
        review.error_message = error_msg
        db.commit()
        logger.error(
            "Anthropic API error",
            extra={"review_request_id": str(review_request_id), "error": error_msg},
        )
        # Distinguish unreachable (503) from other API errors (502)
        status_code = 503 if "unreachable" in error_msg.lower() or "Unable to reach" in error_msg else 502
        raise HTTPException(status_code=status_code, detail=error_msg)
    except Exception as exc:
        review.status = "failed"
        review.error_message = str(exc)
        db.commit()
        logger.error(
            "Unexpected error during review",
            extra={"review_request_id": str(review_request_id), "error": str(exc)},
        )
        raise HTTPException(status_code=502, detail="An unexpected error occurred during review.")

    # --- Store findings ---
    try:
        findings_dicts = [
            {
                "risk_level": f.risk_level,
                "description": f.description,
                "affected_clause": f.affected_clause,
                "recommended_action": f.recommended_action,
                "raw": f.raw,
            }
            for f in api_response.findings
        ]
        _findings_service.store_findings(db, review_request_id, findings_dicts)
    except DatabaseWriteError as exc:
        review.status = "failed"
        review.error_message = f"Failed to store findings: {exc}"
        db.commit()
        logger.error(
            "Failed to store findings",
            extra={"review_request_id": str(review_request_id), "error": str(exc)},
        )
        raise HTTPException(status_code=500, detail="Failed to store review findings.")

    # --- Mark review as completed ---
    review.status = "completed"
    review.completed_at = datetime.now(timezone.utc)
    db.commit()

    logger.info(
        "Legal review completed",
        extra={
            "review_request_id": str(review_request_id),
            "finding_count": len(api_response.findings),
        },
    )

    return DocumentUploadResponse(
        review_request_id=review_request_id,
        document_id=doc.id,
        status="completed",
        filename=filename,
    )


# ---------------------------------------------------------------------------
# GET /reviews — paginated list of user's review requests
# ---------------------------------------------------------------------------


@router.get("/reviews")
def list_reviews(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: Optional[str] = Query(None),
    date_start: Optional[str] = Query(None),
    date_end: Optional[str] = Query(None),
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    """Return a paginated list of the authenticated user's review requests."""
    params = ReviewListParams(
        page=page,
        page_size=page_size,
        search=search,
        date_start=date_start,
        date_end=date_end,
    )
    result = _findings_service.list_reviews(db, user_id, params)
    logger.info(
        "Listed reviews",
        extra={"user_id": user_id, "total": result["total"], "page": page},
    )
    return result


# ---------------------------------------------------------------------------
# GET /reviews/{id} — single review with findings
# ---------------------------------------------------------------------------


@router.get("/reviews/{review_id}")
def get_review(
    review_id: UUID,
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    """Return a single review request with its findings and summary."""
    try:
        result = _findings_service.get_review_with_findings(db, review_id, user_id)
    except ReviewNotFoundError:
        raise HTTPException(status_code=404, detail="Review not found.")
    logger.info(
        "Retrieved review",
        extra={"review_request_id": str(review_id)},
    )
    return result


# ---------------------------------------------------------------------------
# GET /reviews/{id}/findings — findings only
# ---------------------------------------------------------------------------


@router.get("/reviews/{review_id}/findings")
def get_review_findings(
    review_id: UUID,
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    """Return the findings for a review request."""
    try:
        result = _findings_service.get_review_with_findings(db, review_id, user_id)
    except ReviewNotFoundError:
        raise HTTPException(status_code=404, detail="Review not found.")
    logger.info(
        "Retrieved findings",
        extra={"review_request_id": str(review_id), "finding_count": len(result.findings)},
    )
    return {"findings": result.findings, "summary": result.summary}


# ---------------------------------------------------------------------------
# GET /reviews/{id}/export/{format} — export findings
# ---------------------------------------------------------------------------


@router.get("/reviews/{review_id}/export/{export_format}")
def export_findings(
    review_id: UUID,
    export_format: str,
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    """Export findings in PDF, CSV, or JSON format."""
    if export_format not in ("pdf", "csv", "json"):
        raise HTTPException(status_code=422, detail="Unsupported export format. Use pdf, csv, or json.")

    try:
        review_data = _findings_service.get_review_with_findings(db, review_id, user_id)
    except ReviewNotFoundError:
        raise HTTPException(status_code=404, detail="Review not found.")

    logger.info(
        "Exporting findings",
        extra={"review_request_id": str(review_id), "format": export_format},
    )

    if export_format == "pdf":
        pdf_bytes = _export_service.export_pdf(review_data)
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="legal-review-{review_id}.pdf"'},
        )

    if export_format == "csv":
        csv_content = _export_service.export_csv(review_data)
        return Response(
            content=csv_content,
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="legal-review-{review_id}.csv"'},
        )

    # JSON
    json_content = _export_service.export_json(review_data)
    return Response(
        content=json_content,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="legal-review-{review_id}.json"'},
    )


# ---------------------------------------------------------------------------
# POST /reviews/{id}/retry — retry a failed/timed-out review
# ---------------------------------------------------------------------------


@router.post("/reviews/{review_id}/retry")
async def retry_review(
    review_id: UUID,
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    """Retry a failed or timed-out review by resubmitting to the Anthropic API."""
    # Load the review request (user-scoped)
    review: LegalReviewRequest | None = (
        db.query(LegalReviewRequest)
        .filter(
            LegalReviewRequest.id == review_id,
            LegalReviewRequest.user_id == user_id,
        )
        .first()
    )
    if review is None:
        raise HTTPException(status_code=404, detail="Review not found.")

    if review.status not in ("failed", "timeout"):
        raise HTTPException(
            status_code=422,
            detail=f"Only failed or timed-out reviews can be retried. Current status: {review.status}",
        )

    logger.info(
        "Retrying review",
        extra={"review_request_id": str(review_id), "previous_status": review.status},
    )

    # Load the associated document
    doc: LegalReviewDocument | None = (
        db.query(LegalReviewDocument)
        .filter(LegalReviewDocument.id == review.document_id)
        .first()
    )
    if doc is None:
        raise HTTPException(status_code=404, detail="Associated document not found.")

    # Update status and increment retry count
    review.status = "in_progress"
    review.error_message = None
    review.retry_count = (review.retry_count or 0) + 1
    db.commit()

    # Call Bedrock API
    try:
        client = _get_legal_review_client()
        api_response = await client.submit_review(
            document_text=doc.extracted_text,
            document_type="general",
            review_command=review.review_command,
        )
    except LegalReviewTimeoutError as exc:
        review.status = "timeout"
        review.error_message = str(exc)
        db.commit()
        logger.error(
            "Anthropic API timeout on retry",
            extra={"review_request_id": str(review_id), "error": str(exc)},
        )
        raise HTTPException(status_code=504, detail=str(exc))
    except LegalReviewAPIError as exc:
        error_msg = str(exc)
        review.status = "failed"
        review.error_message = error_msg
        db.commit()
        logger.error(
            "Anthropic API error on retry",
            extra={"review_request_id": str(review_id), "error": error_msg},
        )
        status_code = 503 if "unreachable" in error_msg.lower() or "Unable to reach" in error_msg else 502
        raise HTTPException(status_code=status_code, detail=error_msg)
    except Exception as exc:
        review.status = "failed"
        review.error_message = str(exc)
        db.commit()
        logger.error(
            "Unexpected error during retry",
            extra={"review_request_id": str(review_id), "error": str(exc)},
        )
        raise HTTPException(status_code=502, detail="An unexpected error occurred during retry.")

    # Store findings
    try:
        findings_dicts = [
            {
                "risk_level": f.risk_level,
                "description": f.description,
                "affected_clause": f.affected_clause,
                "recommended_action": f.recommended_action,
                "raw": f.raw,
            }
            for f in api_response.findings
        ]
        _findings_service.store_findings(db, review_id, findings_dicts)
    except DatabaseWriteError as exc:
        review.status = "failed"
        review.error_message = f"Failed to store findings: {exc}"
        db.commit()
        logger.error(
            "Failed to store findings on retry",
            extra={"review_request_id": str(review_id), "error": str(exc)},
        )
        raise HTTPException(status_code=500, detail="Failed to store review findings.")

    # Mark completed
    review.status = "completed"
    review.completed_at = datetime.now(timezone.utc)
    db.commit()

    logger.info(
        "Retry completed successfully",
        extra={"review_request_id": str(review_id), "finding_count": len(api_response.findings)},
    )

    return {
        "review_request_id": review_id,
        "status": "completed",
        "retry_count": review.retry_count,
    }
