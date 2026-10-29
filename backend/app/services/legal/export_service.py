"""Legal Export Service — PDF, CSV, JSON export and JSON import.

Generates export files from ``ReviewWithFindingsRead`` data and supports
round-trip JSON serialisation.
"""

from __future__ import annotations

import csv
import io
import logging
from datetime import datetime

from fpdf import FPDF

from app.schemas_legal import (
    FindingRead,
    FindingsExport,
    ReviewRequestRead,
    ReviewWithFindingsRead,
)

logger = logging.getLogger(__name__)

# Risk-level ordering used for grouping in the PDF report.
RISK_LEVEL_ORDER = ["high", "medium", "low", "informational"]

RISK_LEVEL_LABELS = {
    "high": "High",
    "medium": "Medium",
    "low": "Low",
    "informational": "Informational",
}


class LegalExportService:
    """Generates PDF, CSV, and JSON exports for legal review findings."""

    # ------------------------------------------------------------------
    # PDF export
    # ------------------------------------------------------------------

    def export_pdf(self, review: ReviewWithFindingsRead) -> bytes:
        """Generate a PDF report with findings grouped by risk level.

        Returns the raw PDF bytes.
        """
        pdf = FPDF()
        pdf.set_auto_page_break(auto=True, margin=15)
        pdf.add_page()

        # Title
        pdf.set_font("Helvetica", "B", 16)
        pdf.cell(0, 10, "Legal Review Report", new_x="LMARGIN", new_y="NEXT", align="C")
        pdf.ln(4)

        # Metadata
        pdf.set_font("Helvetica", "", 10)
        pdf.cell(0, 6, f"Document: {review.review.document_name}", new_x="LMARGIN", new_y="NEXT")
        pdf.cell(0, 6, f"Review Command: {review.review.review_command}", new_x="LMARGIN", new_y="NEXT")
        pdf.cell(0, 6, f"Status: {review.review.status}", new_x="LMARGIN", new_y="NEXT")
        created = review.review.created_at.strftime("%Y-%m-%d %H:%M:%S UTC")
        pdf.cell(0, 6, f"Created: {created}", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(4)

        # Summary
        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(0, 8, "Summary", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 10)
        for level in RISK_LEVEL_ORDER:
            count = review.summary.get(level, 0)
            label = RISK_LEVEL_LABELS.get(level, level)
            pdf.cell(0, 6, f"{label}: {count}", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(4)

        # Findings grouped by risk level
        grouped: dict[str, list[FindingRead]] = {lvl: [] for lvl in RISK_LEVEL_ORDER}
        for finding in review.findings:
            key = finding.risk_level if finding.risk_level in grouped else "informational"
            grouped[key].append(finding)

        for level in RISK_LEVEL_ORDER:
            findings_for_level = grouped[level]
            if not findings_for_level:
                continue

            label = RISK_LEVEL_LABELS.get(level, level)
            pdf.set_font("Helvetica", "B", 12)
            pdf.cell(0, 8, f"{label} Risk Findings ({len(findings_for_level)})", new_x="LMARGIN", new_y="NEXT")

            for idx, finding in enumerate(findings_for_level, 1):
                pdf.set_font("Helvetica", "B", 10)
                pdf.cell(0, 6, f"Finding {idx}", new_x="LMARGIN", new_y="NEXT")
                pdf.set_font("Helvetica", "", 10)
                pdf.multi_cell(0, 5, f"Description: {finding.description}", new_x="LMARGIN", new_y="NEXT")
                if finding.affected_clause:
                    pdf.multi_cell(0, 5, f"Affected Clause: {finding.affected_clause}", new_x="LMARGIN", new_y="NEXT")
                if finding.recommended_action:
                    pdf.multi_cell(0, 5, f"Recommended Action: {finding.recommended_action}", new_x="LMARGIN", new_y="NEXT")
                pdf.ln(2)

        return bytes(pdf.output())

    # ------------------------------------------------------------------
    # CSV export
    # ------------------------------------------------------------------

    def export_csv(self, review: ReviewWithFindingsRead) -> str:
        """Generate a CSV string with one row per finding.

        Columns: ``Risk_Level``, ``Description``, ``Affected_Clause``,
        ``Recommended_Action``.
        """
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Risk_Level", "Description", "Affected_Clause", "Recommended_Action"])

        for finding in review.findings:
            writer.writerow([
                finding.risk_level,
                finding.description,
                finding.affected_clause or "",
                finding.recommended_action or "",
            ])

        return output.getvalue()

    # ------------------------------------------------------------------
    # JSON export
    # ------------------------------------------------------------------

    def export_json(self, review: ReviewWithFindingsRead) -> str:
        """Serialize the complete ``ReviewWithFindingsRead`` to JSON.

        Wraps the data in a ``FindingsExport`` envelope that includes
        review metadata alongside the findings list.
        """
        export = FindingsExport(
            review_id=review.review.id,
            document_name=review.review.document_name,
            review_command=review.review.review_command,
            reviewed_at=review.review.completed_at or review.review.created_at,
            findings=review.findings,
            metadata={
                "status": review.review.status,
                "document_id": str(review.review.document_id),
                "finding_count": review.review.finding_count,
                "summary": review.summary,
                "created_at": review.review.created_at.isoformat(),
                "completed_at": (
                    review.review.completed_at.isoformat()
                    if review.review.completed_at
                    else None
                ),
                "error_message": review.review.error_message,
            },
        )
        return export.model_dump_json(indent=2)

    # ------------------------------------------------------------------
    # JSON import
    # ------------------------------------------------------------------

    def import_json(self, json_str: str) -> ReviewWithFindingsRead:
        """Deserialize a JSON string back into ``ReviewWithFindingsRead``.

        Expects the JSON produced by ``export_json`` (a ``FindingsExport``
        envelope).
        """
        export = FindingsExport.model_validate_json(json_str)

        # Reconstruct summary from findings
        summary: dict[str, int] = {}
        for f in export.findings:
            summary[f.risk_level] = summary.get(f.risk_level, 0) + 1

        # Parse metadata fields back
        meta = export.metadata
        completed_at_str = meta.get("completed_at")
        completed_at = (
            datetime.fromisoformat(completed_at_str)
            if completed_at_str
            else None
        )
        created_at_str = meta.get("created_at")
        created_at = (
            datetime.fromisoformat(created_at_str)
            if created_at_str
            else export.reviewed_at
        )

        review_read = ReviewRequestRead(
            id=export.review_id,
            document_id=meta.get("document_id", str(export.review_id)),
            review_command=export.review_command,
            status=meta.get("status", "completed"),
            error_message=meta.get("error_message"),
            created_at=created_at,
            completed_at=completed_at,
            document_name=export.document_name,
            finding_count=meta.get("finding_count", len(export.findings)),
        )

        return ReviewWithFindingsRead(
            review=review_read,
            findings=export.findings,
            summary=summary,
        )
