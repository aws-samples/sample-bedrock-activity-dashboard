"""Property-based tests for ExportService."""
# Feature: bedrock-activity-dashboard, Property 14: CSV Export Correctness

import csv
import io

from hypothesis import given, settings
from hypothesis import strategies as st

from app.services.export_service import ExportService


class TestCSVExportCorrectness:
    """Property 14: CSV Export Correctness.

    Exported CSV must contain one row per record with matching column values.
    """

    @settings(max_examples=150, deadline=None)
    @given(
        n_records=st.integers(min_value=0, max_value=20),
    )
    def test_csv_row_count_matches_records(self, n_records):
        records = [
            {
                "userName": f"user-{i}",
                "inputTokens": i * 100,
                "outputTokens": i * 50,
                "cacheReadTokens": 0,
                "cacheWriteTokens": 0,
                "totalTokens": i * 150,
                "costEstimate": i * 0.01,
            }
            for i in range(n_records)
        ]
        filters = {"start_date": "2025-01-01", "end_date": "2025-01-31"}
        csv_str = ExportService.generate_csv("token-usage", records, filters)

        reader = csv.reader(io.StringIO(csv_str))
        rows = list(reader)
        # Find the header row (first row after blank separator)
        data_start = None
        for idx, row in enumerate(rows):
            if row == []:
                data_start = idx + 1
                break
        assert data_start is not None
        header = rows[data_start]
        data_rows = rows[data_start + 1:]
        assert len(data_rows) == n_records
        assert "userName" in header
