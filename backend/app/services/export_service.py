"""CSV export service for the Bedrock Activity Dashboard."""

import csv
import io
from typing import Any


# Column definitions per section
_SECTION_COLUMNS: dict[str, list[str]] = {
    "token-usage": [
        "userName",
        "inputTokens",
        "outputTokens",
        "cacheReadTokens",
        "cacheWriteTokens",
        "totalTokens",
        "costEstimate",
    ],
    "model-usage": [
        "modelId",
        "invocationCount",
        "inputTokens",
        "outputTokens",
        "totalTokens",
        "costEstimate",
        "percentageOfTotal",
    ],
    "cost-analysis": [
        "userName",
        "modelId",
        "costEstimate",
        "percentageOfTotal",
    ],
    "prompt-history": [
        "timestamp",
        "userName",
        "modelId",
        "sourceApplication",
        "inputTokens",
        "outputTokens",
        "costEstimate",
    ],
}


class ExportService:
    """Generates CSV exports from filtered/aggregated activity data."""

    @staticmethod
    def generate_csv(
        section: str,
        records: list[dict[str, Any]],
        filters: dict[str, Any],
    ) -> str:
        """Generate a CSV string for the given section and records.

        The CSV includes metadata header rows (date range, applied filters)
        followed by column headers and one data row per record.

        Args:
            section: One of token-usage, model-usage, cost-analysis, prompt-history.
            records: List of dicts with the data to export.
            filters: Dict with start_date, end_date, user_identities, team_id,
                     source_application used for metadata header.

        Returns:
            CSV content as a string.
        """
        output = io.StringIO()
        writer = csv.writer(output)

        # Metadata header rows
        start = filters.get("start_date", "")
        end = filters.get("end_date", "")
        if hasattr(start, "isoformat"):
            start = start.isoformat()
        if hasattr(end, "isoformat"):
            end = end.isoformat()

        writer.writerow(["# Date Range", f"{start} to {end}"])

        users = filters.get("user_identities", [])
        if users:
            writer.writerow(["# Users", ", ".join(users)])

        team_id = filters.get("team_id")
        if team_id:
            writer.writerow(["# Team", team_id])

        source_app = filters.get("source_application")
        if source_app:
            writer.writerow(["# Source Application", source_app])

        writer.writerow(["# Section", section])
        writer.writerow([])  # blank separator

        # Column headers and data rows
        columns = _SECTION_COLUMNS.get(section, [])
        writer.writerow(columns)

        for record in records:
            row = [record.get(col, "") for col in columns]
            writer.writerow(row)

        return output.getvalue()
