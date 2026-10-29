"""Unit tests for ReportService."""

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch


from app.services.report_service import ReportService


def _make_record(user_id, user_name, model_id, timestamp_str, input_tokens, output_tokens, cost=None):
    """Helper to build an activity record dict."""
    return {
        "id": str(uuid.uuid4()),
        "timestamp": timestamp_str,
        "userIdentity": user_id,
        "userName": user_name,
        "modelId": model_id,
        "sourceApplication": "Kiro",
        "sourceIp": "10.0.0.1",
        "inputTokens": input_tokens,
        "outputTokens": output_tokens,
        "cacheReadTokens": 0,
        "cacheWriteTokens": 0,
        "totalTokens": input_tokens + output_tokens,
        "costEstimate": cost,
    }


def _make_summary_report(report_id=None, period_start=None, period_end=None,
                          report_type="daily", content=None, created_at=None):
    """Helper to build a mock SummaryReport ORM object."""
    report = MagicMock()
    report.id = report_id or uuid.uuid4()
    report.period_start = period_start or datetime(2024, 1, 8, tzinfo=timezone.utc)
    report.period_end = period_end or datetime(2024, 1, 15, tzinfo=timezone.utc)
    report.report_type = report_type
    report.content = content or {
        "usageTrends": {"totalTokensDelta": 25.0, "totalCostDelta": 10.0, "activeUsersDelta": 50.0},
        "currentPeriod": {"totalTokens": 5000, "totalCost": 1.10, "activeUsers": 3, "totalInvocations": 10},
        "previousPeriod": {"totalTokens": 4000, "totalCost": 1.00, "activeUsers": 2, "totalInvocations": 8},
        "recommendations": [],
        "anomalies": [],
    }
    report.created_at = created_at or datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
    return report


class TestGenerateReport:
    """Tests for generate_report method."""

    @patch("app.services.report_service.CloudTrailService")
    @patch("app.services.report_service.CostService")
    def test_correct_trend_deltas_with_current_and_previous_data(self, mock_cost_cls, mock_ct_cls):
        """Generate report with current and previous period data → correct trend deltas."""
        period_start = datetime(2024, 1, 8, tzinfo=timezone.utc)
        period_end = datetime(2024, 1, 15, tzinfo=timezone.utc)

        current_records = [
            _make_record("u1", "alice", "model-a", "2024-01-10T10:00:00+00:00", 1000, 500, cost=0.50),
            _make_record("u2", "bob", "model-a", "2024-01-12T10:00:00+00:00", 800, 400, cost=0.40),
        ]
        previous_records = [
            _make_record("u1", "alice", "model-a", "2024-01-03T10:00:00+00:00", 600, 300, cost=0.30),
        ]

        mock_ct = MagicMock()
        mock_ct.get_activity_records.side_effect = [current_records, previous_records]
        mock_ct_cls.return_value = mock_ct

        mock_cost_cls.apply_costs.side_effect = lambda records, db: records

        db = MagicMock()
        # Recommendations query returns empty
        db.query.return_value.filter.return_value.all.return_value = []

        # Mock refresh to set id and created_at
        def mock_refresh(obj):
            obj.id = uuid.uuid4()
            obj.created_at = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        db.refresh = mock_refresh

        svc = ReportService()
        result = svc.generate_report(period_start, period_end, "weekly", db)

        assert "id" in result
        assert result["reportType"] == "weekly"
        assert result["periodStart"] == period_start.isoformat()
        assert result["periodEnd"] == period_end.isoformat()

        # Current: 2700 tokens, 2 users; Previous: 900 tokens, 1 user
        trends = result["usageTrends"]
        assert trends["totalTokensDelta"] == 200.0  # (2700-900)/900*100
        assert trends["activeUsersDelta"] == 100.0   # (2-1)/1*100

        assert result["currentPeriod"]["totalTokens"] == 2700
        assert result["currentPeriod"]["activeUsers"] == 2
        assert result["previousPeriod"]["totalTokens"] == 900
        assert result["previousPeriod"]["activeUsers"] == 1

        # Report persisted
        db.add.assert_called_once()
        db.commit.assert_called_once()

    @patch("app.services.report_service.CloudTrailService")
    @patch("app.services.report_service.CostService")
    def test_deltas_zero_when_previous_period_has_zero_values(self, mock_cost_cls, mock_ct_cls):
        """Generate report when previous period has zero values → deltas are 0."""
        period_start = datetime(2024, 1, 8, tzinfo=timezone.utc)
        period_end = datetime(2024, 1, 15, tzinfo=timezone.utc)

        current_records = [
            _make_record("u1", "alice", "model-a", "2024-01-10T10:00:00+00:00", 1000, 500, cost=0.50),
        ]

        mock_ct = MagicMock()
        mock_ct.get_activity_records.side_effect = [current_records, []]
        mock_ct_cls.return_value = mock_ct
        mock_cost_cls.apply_costs.side_effect = lambda records, db: records

        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = []

        def mock_refresh(obj):
            obj.id = uuid.uuid4()
            obj.created_at = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        db.refresh = mock_refresh

        svc = ReportService()
        result = svc.generate_report(period_start, period_end, "daily", db)

        trends = result["usageTrends"]
        assert trends["totalTokensDelta"] == 0.0
        assert trends["totalCostDelta"] == 0.0
        assert trends["activeUsersDelta"] == 0.0

    @patch("app.services.report_service.CloudTrailService")
    @patch("app.services.report_service.CostService")
    def test_report_has_empty_recommendations_and_anomalies(self, mock_cost_cls, mock_ct_cls):
        """Generate report → recommendations and anomalies are always empty lists."""
        period_start = datetime(2024, 1, 8, tzinfo=timezone.utc)
        period_end = datetime(2024, 1, 15, tzinfo=timezone.utc)

        mock_ct = MagicMock()
        mock_ct.get_activity_records.return_value = []
        mock_ct_cls.return_value = mock_ct
        mock_cost_cls.apply_costs.side_effect = lambda records, db: records

        db = MagicMock()

        def mock_refresh(obj):
            obj.id = uuid.uuid4()
            obj.created_at = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        db.refresh = mock_refresh

        svc = ReportService()
        result = svc.generate_report(period_start, period_end, "weekly", db)

        assert result["recommendations"] == []
        assert result["anomalies"] == []

    @patch("app.services.report_service.CloudTrailService")
    @patch("app.services.report_service.CostService")
    def test_report_with_no_activity_data_still_created(self, mock_cost_cls, mock_ct_cls):
        """Generate report with no activity data → report still created with zero values."""
        period_start = datetime(2024, 1, 8, tzinfo=timezone.utc)
        period_end = datetime(2024, 1, 15, tzinfo=timezone.utc)

        mock_ct = MagicMock()
        mock_ct.get_activity_records.return_value = []
        mock_ct_cls.return_value = mock_ct
        mock_cost_cls.apply_costs.side_effect = lambda records, db: records

        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = []

        def mock_refresh(obj):
            obj.id = uuid.uuid4()
            obj.created_at = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        db.refresh = mock_refresh

        svc = ReportService()
        result = svc.generate_report(period_start, period_end, "daily", db)

        assert result["currentPeriod"]["totalTokens"] == 0
        assert result["currentPeriod"]["totalCost"] == 0
        assert result["currentPeriod"]["activeUsers"] == 0
        assert result["currentPeriod"]["totalInvocations"] == 0
        assert result["previousPeriod"]["totalTokens"] == 0
        assert result["usageTrends"]["totalTokensDelta"] == 0.0
        db.add.assert_called_once()
        db.commit.assert_called_once()


class TestListReports:
    """Tests for list_reports method."""

    def test_returns_reports_ordered_by_created_at_desc(self):
        """List reports returns ordered by created_at desc."""
        report1 = _make_summary_report(
            created_at=datetime(2024, 1, 10, tzinfo=timezone.utc)
        )
        report2 = _make_summary_report(
            created_at=datetime(2024, 1, 15, tzinfo=timezone.utc)
        )

        db = MagicMock()
        db.query.return_value.order_by.return_value.all.return_value = [report2, report1]

        svc = ReportService()
        results = svc.list_reports(db)

        assert len(results) == 2
        # First result should be the more recent one
        assert results[0]["createdAt"] == report2.created_at.isoformat()
        assert results[1]["createdAt"] == report1.created_at.isoformat()


class TestGetReport:
    """Tests for get_report method."""

    def test_returns_correct_report_by_id(self):
        """Get report by ID returns correct report."""
        report_id = uuid.uuid4()
        report = _make_summary_report(report_id=report_id, report_type="weekly")

        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = report

        svc = ReportService()
        result = svc.get_report(str(report_id), db)

        assert result is not None
        assert result["id"] == str(report_id)
        assert result["reportType"] == "weekly"
        assert "usageTrends" in result
        assert "recommendations" in result
        assert "anomalies" in result

    def test_returns_none_for_nonexistent_id(self):
        """Get report with non-existent ID returns None."""
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None

        svc = ReportService()
        result = svc.get_report(str(uuid.uuid4()), db)

        assert result is None


class TestPctChange:
    """Tests for the _pct_change helper."""

    def test_positive_change(self):
        assert ReportService._pct_change(150, 100) == 50.0

    def test_negative_change(self):
        assert ReportService._pct_change(50, 100) == -50.0

    def test_zero_previous_returns_zero(self):
        assert ReportService._pct_change(100, 0) == 0.0

    def test_no_change(self):
        assert ReportService._pct_change(100, 100) == 0.0
