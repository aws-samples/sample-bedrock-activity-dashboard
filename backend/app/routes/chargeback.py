"""Chargeback API routes — CSV upload, parsing, report generation, and export."""

import logging
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.auth import CurrentUser, require_admin, require_view_all
from app.services.audit_log import audit
from app.database import get_db
from app.services import csv_parser_service
from app.services.chargeback_engine import export_report_csv, generate_report
from app.v2.models import (
    ChargebackReport,
    ChargebackReportLine,
    CsvBedrockLineItem,
    CsvUpload,
)
from app.v2.schemas import ChargebackReportRead, CsvUploadRead

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v2/chargeback", tags=["chargeback"])

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB


@router.post("/uploads", response_model=CsvUploadRead)
async def upload_csv(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    admin: CurrentUser = Depends(require_admin),
):
    """Upload a Cost Explorer CSV file."""
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Only .csv files are accepted")

    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="File exceeds maximum allowed size of 50 MB")

    csv_text = content.decode("utf-8", errors="replace")
    billing_period = csv_parser_service.detect_billing_period(csv_text, file.filename)

    upload = CsvUpload(
        filename=file.filename,
        billing_period=billing_period,
        csv_content=csv_text,
        status="pending",
    )
    db.add(upload)
    db.commit()
    audit(admin, "chargeback.upload", db=db, upload=upload.id, filename=file.filename)
    db.refresh(upload)
    return upload


@router.get("/uploads", response_model=list[CsvUploadRead], dependencies=[Depends(require_view_all)])
def list_uploads(db: Session = Depends(get_db)):
    """List all upload records ordered by uploaded_at desc."""
    return (
        db.query(CsvUpload)
        .order_by(CsvUpload.uploaded_at.desc())
        .all()
    )


@router.get("/uploads/{upload_id}", response_model=CsvUploadRead, dependencies=[Depends(require_view_all)])
def get_upload(upload_id: uuid.UUID, db: Session = Depends(get_db)):
    """Get a single upload record."""
    upload = db.query(CsvUpload).filter(CsvUpload.id == upload_id).first()
    if not upload:
        raise HTTPException(status_code=404, detail="Upload not found")
    return upload


@router.post("/uploads/{upload_id}/parse", response_model=CsvUploadRead)
def parse_upload(
    upload_id: uuid.UUID,
    db: Session = Depends(get_db),
    admin: CurrentUser = Depends(require_admin),
):
    audit(admin, "chargeback.parse", db=db, upload=upload_id)
    """Trigger CSV parsing via CSVParserService."""
    upload = db.query(CsvUpload).filter(CsvUpload.id == upload_id).first()
    if not upload:
        raise HTTPException(status_code=404, detail="Upload not found")

    # Validate headers
    valid, missing = csv_parser_service.validate_headers(upload.csv_content)
    if not valid:
        upload.status = "error"
        upload.error_message = f"Missing required columns: {', '.join(missing)}"
        db.commit()
        db.refresh(upload)
        return upload

    # Parse Bedrock line items
    items = csv_parser_service.parse_bedrock_line_items(upload.csv_content)

    # Delete old line items if re-parsing
    db.query(CsvBedrockLineItem).filter(CsvBedrockLineItem.upload_id == upload_id).delete()

    for item in items:
        db.add(CsvBedrockLineItem(
            upload_id=upload_id,
            service=item["service"],
            usage_type=item["usage_type"],
            unblended_cost=item["unblended_cost"],
            raw_row=item["raw_row"],
        ))

    upload.status = "parsed"
    upload.error_message = None  # Clear any previous error
    if not items:
        upload.warning_message = "No Bedrock charges found in the CSV"
    else:
        upload.warning_message = None

    # Detect billing period if not already set
    if not upload.billing_period:
        upload.billing_period = csv_parser_service.detect_billing_period(
            upload.csv_content, upload.filename
        )

    db.commit()
    db.refresh(upload)
    return upload


@router.post("/uploads/{upload_id}/report")
def generate_chargeback_report(
    upload_id: uuid.UUID,
    db: Session = Depends(get_db),
    admin: CurrentUser = Depends(require_admin),
):
    audit(admin, "chargeback.report", db=db, upload=upload_id)
    """Trigger report generation via ChargebackEngine."""
    upload = db.query(CsvUpload).filter(CsvUpload.id == upload_id).first()
    if not upload:
        raise HTTPException(status_code=404, detail="Upload not found")

    result = generate_report(db, upload_id)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.get("/reports/{report_id}", response_model=ChargebackReportRead, dependencies=[Depends(require_view_all)])
def get_report(report_id: uuid.UUID, db: Session = Depends(get_db)):
    """Get report with line details."""
    report = db.query(ChargebackReport).filter(ChargebackReport.id == report_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    lines = (
        db.query(ChargebackReportLine)
        .filter(ChargebackReportLine.report_id == report_id)
        .all()
    )

    return ChargebackReportRead(
        id=report.id,
        upload_id=report.upload_id,
        billing_period=report.billing_period,
        total_actual_cost=report.total_actual_cost,
        total_estimated_cost=report.total_estimated_cost,
        total_variance=report.total_variance,
        insights=report.insights,
        generated_at=report.generated_at,
        lines=[
            {
                "entity_type": line.entity_type,
                "entity_name": line.entity_name,
                "estimated_cost": line.estimated_cost,
                "allocated_actual_cost": line.allocated_actual_cost,
                "variance": line.variance,
                "percentage_share": line.percentage_share,
            }
            for line in lines
        ],
    )


@router.get("/reports/{report_id}/export", dependencies=[Depends(require_view_all)])
def export_report(report_id: uuid.UUID, db: Session = Depends(get_db)):
    """Download report as CSV."""
    report = db.query(ChargebackReport).filter(ChargebackReport.id == report_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    lines = (
        db.query(ChargebackReportLine)
        .filter(ChargebackReportLine.report_id == report_id)
        .all()
    )

    csv_content = export_report_csv(report, lines)
    return StreamingResponse(
        iter([csv_content]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=chargeback-{report.billing_period}.csv"},
    )


@router.delete("/uploads/{upload_id}")
def delete_upload(
    upload_id: uuid.UUID,
    db: Session = Depends(get_db),
    admin: CurrentUser = Depends(require_admin),
):
    audit(admin, "chargeback.delete", db=db, upload=upload_id)
    """Delete upload and cascade associated data."""
    upload = db.query(CsvUpload).filter(CsvUpload.id == upload_id).first()
    if not upload:
        raise HTTPException(status_code=404, detail="Upload not found")

    db.delete(upload)
    db.commit()
    return {"status": "deleted", "id": str(upload_id)}
