"""Report service for generating summary reports with usage trend deltas."""

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models import SummaryReport
from app.services.aggregation_service import AggregationService
from app.services.cloudtrail_service import CloudTrailService
from app.services.cost_service import CostService


class ReportService:
    """Generates, persists, and retrieves summary reports."""

    def generate_report(
        self,
        period_start: datetime,
        period_end: datetime,
        report_type: str,
        db: Session,
    ) -> dict[str, Any]:
        """Generate a SummaryReport with usage trend deltas, recommendations, and anomalies.

        Args:
            period_start: Start of the reporting period.
            period_end: End of the reporting period.
            report_type: "daily" or "weekly".
            db: SQLAlchemy database session.

        Returns:
            Dict matching the SummaryReport TypeScript interface.
        """
        duration = period_end - period_start
        prev_end = period_start
        prev_start = prev_end - duration

        # Fetch activity records for both periods
        ct_service = CloudTrailService()
        try:
            current_records = ct_service.get_activity_records(period_start, period_end)
        except Exception:
            current_records = []

        try:
            previous_records = ct_service.get_activity_records(prev_start, prev_end)
        except Exception:
            previous_records = []

        # Apply costs
        current_records = CostService.apply_costs(current_records, db)
        previous_records = CostService.apply_costs(previous_records, db)

        # Compute overviews
        agg = AggregationService()
        current_overview = agg.compute_overview(current_records) if current_records else self._empty_overview()
        previous_overview = agg.compute_overview(previous_records) if previous_records else self._empty_overview()

        # Calculate trend deltas (percentage change)
        total_tokens_delta = self._pct_change(
            current_overview["totalTokens"], previous_overview["totalTokens"]
        )
        total_cost_delta = self._pct_change(
            current_overview["totalCostEstimate"] or 0,
            previous_overview["totalCostEstimate"] or 0,
        )
        active_users_delta = self._pct_change(
            current_overview["activeUsers"], previous_overview["activeUsers"]
        )

        rec_dicts = []
        anomaly_dicts = []

        content = {
            "usageTrends": {
                "totalTokensDelta": total_tokens_delta,
                "totalCostDelta": total_cost_delta,
                "activeUsersDelta": active_users_delta,
            },
            "currentPeriod": {
                "totalTokens": current_overview["totalTokens"],
                "totalCost": current_overview["totalCostEstimate"] or 0,
                "activeUsers": current_overview["activeUsers"],
                "totalInvocations": current_overview["totalInvocations"],
            },
            "previousPeriod": {
                "totalTokens": previous_overview["totalTokens"],
                "totalCost": previous_overview["totalCostEstimate"] or 0,
                "activeUsers": previous_overview["activeUsers"],
                "totalInvocations": previous_overview["totalInvocations"],
            },
            "recommendations": rec_dicts,
            "anomalies": anomaly_dicts,
        }

        # Persist to DB
        report = SummaryReport(
            period_start=period_start,
            period_end=period_end,
            report_type=report_type,
            content=content,
        )
        db.add(report)
        db.commit()
        db.refresh(report)

        return {
            "id": str(report.id),
            "periodStart": report.period_start.isoformat(),
            "periodEnd": report.period_end.isoformat(),
            "reportType": report.report_type,
            "usageTrends": content["usageTrends"],
            "currentPeriod": content["currentPeriod"],
            "previousPeriod": content["previousPeriod"],
            "recommendations": content["recommendations"],
            "anomalies": content["anomalies"],
            "createdAt": report.created_at.isoformat(),
        }

    def list_reports(self, db: Session) -> list[dict[str, Any]]:
        """Return all reports ordered by created_at descending."""
        reports = (
            db.query(SummaryReport)
            .order_by(SummaryReport.created_at.desc())
            .all()
        )
        return [self._report_to_dict(r) for r in reports]

    def get_report(self, report_id: str, db: Session) -> dict[str, Any] | None:
        """Return a single report by ID, or None if not found."""
        report = db.query(SummaryReport).filter(SummaryReport.id == report_id).first()
        if report is None:
            return None
        return self._report_to_dict(report)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _pct_change(current: float, previous: float) -> float:
        """Calculate percentage change: ((current - previous) / previous * 100).

        Returns 0 if previous is 0.
        """
        if previous == 0:
            return 0.0
        return round((current - previous) / previous * 100, 2)

    @staticmethod
    def _empty_overview() -> dict[str, Any]:
        return {
            "totalInvocations": 0,
            "totalTokens": 0,
            "totalCostEstimate": 0,
            "activeUsers": 0,
            "activeModels": 0,
        }

    @staticmethod
    def _report_to_dict(report: SummaryReport) -> dict[str, Any]:
        content = report.content or {}
        return {
            "id": str(report.id),
            "periodStart": report.period_start.isoformat(),
            "periodEnd": report.period_end.isoformat(),
            "reportType": report.report_type,
            "usageTrends": content.get("usageTrends", {}),
            "currentPeriod": content.get("currentPeriod", {}),
            "previousPeriod": content.get("previousPeriod", {}),
            "recommendations": content.get("recommendations", []),
            "anomalies": content.get("anomalies", []),
            "createdAt": report.created_at.isoformat(),
        }
