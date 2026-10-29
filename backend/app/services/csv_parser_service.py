"""CSV Parser Service for Cost Explorer chargeback uploads.

Handles two CSV formats:
1. Standard format: columns Service, Usage Type, Unblended Cost (one row per line item)
2. Pivot table format: first column is "Service" with date rows, other columns are
   service names like "Bedrock($)", "EC2-Instances($)" (Cost Explorer monthly view)
"""

import csv
import io
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

REQUIRED_COLUMNS_STANDARD = {"Service", "Usage Type", "Unblended Cost"}


def _strip_bom(text: str) -> str:
    """Strip UTF-8 BOM and clean up."""
    if text.startswith("\ufeff"):
        text = text[1:]
    # Also handle BOM encoded as bytes that got decoded
    if text.startswith("\xef\xbb\xbf"):
        text = text[3:]
    return text


def _detect_format(headers: list[str]) -> str:
    """Detect whether the CSV is standard or pivot table format."""
    # Clean all headers: strip whitespace, BOM, and quotes
    cleaned = [h.strip().strip('"').strip('\ufeff').strip() for h in headers]
    cleaned_set = set(cleaned)

    if REQUIRED_COLUMNS_STANDARD.issubset(cleaned_set):
        return "standard"
    # Check for pivot format: first column is "Service" and other columns have ($)
    if cleaned and cleaned[0] == "Service":
        dollar_cols = [h for h in headers[1:] if "($)" in h]
        if dollar_cols:
            return "pivot"
    return "unknown"


def _find_bedrock_columns(headers: list[str]) -> list[str]:
    """Find column names that contain 'Bedrock' in pivot format."""
    return [h.strip() for h in headers if "Bedrock" in h and "($)" in h]


def validate_headers(csv_text: str) -> tuple[bool, list[str]]:
    """Check CSV has required columns or is a recognized pivot format."""
    csv_text = _strip_bom(csv_text)
    reader = csv.reader(io.StringIO(csv_text))
    try:
        headers = next(reader)
    except StopIteration:
        return False, sorted(REQUIRED_COLUMNS_STANDARD)

    fmt = _detect_format(headers)
    if fmt == "standard":
        return True, []
    if fmt == "pivot":
        bedrock_cols = _find_bedrock_columns(headers)
        if bedrock_cols:
            return True, []
        return False, ["No Bedrock columns found in pivot table"]

    # Unknown format — report missing standard columns
    headers_stripped = {h.strip() for h in headers}
    missing = sorted(REQUIRED_COLUMNS_STANDARD - headers_stripped)
    return False, missing


def parse_bedrock_line_items(csv_text: str) -> list[dict[str, Any]]:
    """Extract Bedrock cost items from either standard or pivot format."""
    csv_text = _strip_bom(csv_text)
    reader = csv.reader(io.StringIO(csv_text))
    try:
        headers = next(reader)
    except StopIteration:
        return []

    fmt = _detect_format(headers)
    if fmt == "standard":
        return _parse_standard(csv_text)
    if fmt == "pivot":
        return _parse_pivot(headers, reader)
    return []


def _parse_standard(csv_text: str) -> list[dict[str, Any]]:
    """Parse standard format: Service, Usage Type, Unblended Cost."""
    reader = csv.DictReader(io.StringIO(csv_text))
    items: list[dict[str, Any]] = []
    for row_num, row in enumerate(reader, start=2):
        service = (row.get("Service") or "").strip()
        if "Bedrock" not in service:
            continue
        cost_str = (row.get("Unblended Cost") or "").strip()
        try:
            cost = float(cost_str)
        except (ValueError, TypeError):
            logger.warning("Skipping malformed row %d: invalid cost '%s'", row_num, cost_str)
            continue
        items.append({
            "service": service,
            "usage_type": (row.get("Usage Type") or "").strip(),
            "unblended_cost": cost,
            "raw_row": dict(row),
        })
    return items


def _parse_pivot(headers: list[str], reader) -> list[dict[str, Any]]:
    """Parse pivot table format: date rows with service columns containing ($)."""
    bedrock_cols = _find_bedrock_columns(headers)
    if not bedrock_cols:
        return []

    # Build column index mapping
    col_indices = {}
    for col_name in bedrock_cols:
        try:
            idx = [h.strip() for h in headers].index(col_name)
            col_indices[col_name] = idx
        except ValueError:
            continue

    items: list[dict[str, Any]] = []
    for row_num, row in enumerate(reader, start=2):
        if not row:
            continue
        row_label = row[0].strip().strip('"')

        # Skip summary rows like "Service total"
        if not re.match(r"\d{4}-\d{2}-\d{2}", row_label):
            continue

        for col_name, idx in col_indices.items():
            if idx >= len(row):
                continue
            cost_str = row[idx].strip().strip('"')
            if not cost_str:
                continue
            try:
                cost = float(cost_str)
            except (ValueError, TypeError):
                logger.warning("Skipping malformed cell row %d col '%s': '%s'", row_num, col_name, cost_str)
                continue
            if cost == 0:
                continue

            # Extract clean service name from column header (e.g., "Bedrock($)" -> "Bedrock")
            service_name = col_name.replace("($)", "").strip()

            items.append({
                "service": f"Amazon {service_name}" if not service_name.startswith("Amazon") else service_name,
                "usage_type": f"Monthly cost for {row_label}",
                "unblended_cost": cost,
                "raw_row": {"Service": service_name, "Period": row_label, "Cost": cost_str, "Column": col_name},
            })

    return items


def detect_billing_period(csv_text: str, filename: str) -> str | None:
    """Detect YYYY-MM billing period from CSV content or filename."""
    # Try filename first (e.g., cost-report-2025-01.csv, costexplorer-march2026.csv)
    match = re.search(r"(\d{4})-(\d{2})", filename)
    if match:
        return f"{match.group(1)}-{match.group(2)}"

    # Try month names in filename
    month_names = {
        "january": "01", "february": "02", "march": "03", "april": "04",
        "may": "05", "june": "06", "july": "07", "august": "08",
        "september": "09", "october": "10", "november": "11", "december": "12",
        "jan": "01", "feb": "02", "mar": "03", "apr": "04",
        "jun": "06", "jul": "07", "aug": "08", "sep": "09",
        "oct": "10", "nov": "11", "dec": "12",
    }
    fname_lower = filename.lower()
    for month_name, month_num in month_names.items():
        if month_name in fname_lower:
            year_match = re.search(r"(\d{4})", fname_lower)
            if year_match:
                return f"{year_match.group(1)}-{month_num}"

    # Try CSV content — look for date-like values in first few rows
    reader = csv.DictReader(io.StringIO(csv_text))
    for i, row in enumerate(reader):
        if i >= 20:
            break
        for value in row.values():
            if not value:
                continue
            m = re.search(r"(\d{4})-(\d{2})", str(value))
            if m:
                return f"{m.group(1)}-{m.group(2)}"

    return None


def format_line_items_to_csv(headers: list[str], line_items: list[dict[str, Any]]) -> str:
    """Format parsed line items back to CSV text."""
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=headers)
    writer.writeheader()
    for item in line_items:
        raw = item.get("raw_row", {})
        writer.writerow(raw)
    return output.getvalue()
