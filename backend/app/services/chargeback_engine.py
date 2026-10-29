"""Chargeback Engine — reconciles AWS billing CSV against dashboard cost estimates."""

import csv
import io
import logging
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.models import Team, TeamMembership
from app.services.cloudtrail_service import CloudTrailService
from app.services.cost_service import CostService
from app.v2.models import (
    ChargebackReport,
    ChargebackReportLine,
    CsvBedrockLineItem,
    CsvUpload,
)

logger = logging.getLogger(__name__)


def get_estimated_costs_by_user(db: Session, billing_period: str) -> dict[str, float]:
    """Retrieve activity records for the billing period and aggregate estimated costs by user_identity."""
    try:
        year, month = billing_period.split("-")
        start = datetime(int(year), int(month), 1, tzinfo=timezone.utc)
        if int(month) == 12:
            end = datetime(int(year) + 1, 1, 1, tzinfo=timezone.utc)
        else:
            end = datetime(int(year), int(month) + 1, 1, tzinfo=timezone.utc)
    except (ValueError, IndexError):
        return {}

    ct = CloudTrailService()
    records = ct.get_activity_records(start, end)
    CostService.apply_costs(records, db)

    user_costs: dict[str, float] = defaultdict(float)
    for r in records:
        user = r.get("userName", r.get("userIdentity", "unknown"))
        cost = r.get("costEstimate") or 0.0
        user_costs[user] += cost

    return dict(user_costs)


def get_team_assignments(db: Session) -> dict[str, str]:
    """Build user_identity → team_name mapping from TeamMembership."""
    memberships = db.query(TeamMembership).all()
    user_team: dict[str, str] = {}
    for m in memberships:
        team = db.query(Team).filter(Team.id == m.team_id).first()
        if team:
            user_team[m.user_identity] = team.name
    return user_team


def calculate_proportional_allocation(
    user_estimated: dict[str, float],
    total_actual_cost: float,
) -> dict[str, dict[str, Any]]:
    """Allocate actual cost proportionally based on each user's share of estimated cost.

    Returns dict[user, {estimated, allocated, variance, percentage}].
    """
    total_estimated = sum(user_estimated.values())
    result: dict[str, dict[str, Any]] = {}

    if total_estimated > 0:
        for user, est in user_estimated.items():
            proportion = est / total_estimated
            allocated = total_actual_cost * proportion
            result[user] = {
                "estimated_cost": est,
                "allocated_actual_cost": allocated,
                "variance": allocated - est,
                "percentage_share": proportion * 100,
            }
    elif user_estimated:
        # Zero total estimated but users exist — distribute equally
        equal_share = total_actual_cost / len(user_estimated)
        pct = 100.0 / len(user_estimated)
        for user, est in user_estimated.items():
            result[user] = {
                "estimated_cost": est,
                "allocated_actual_cost": equal_share,
                "variance": equal_share - est,
                "percentage_share": pct,
            }
    else:
        # No users at all — assign to Unassigned
        result["Unassigned"] = {
            "estimated_cost": 0.0,
            "allocated_actual_cost": total_actual_cost,
            "variance": total_actual_cost,
            "percentage_share": 100.0,
        }

    return result


def generate_report(db: Session, upload_id: uuid.UUID) -> dict[str, Any]:
    """Full reconciliation: get line items, get estimates, allocate, store report."""
    upload = db.query(CsvUpload).filter(CsvUpload.id == upload_id).first()
    if not upload:
        return {"error": "Upload not found"}

    if not upload.billing_period:
        return {"error": "No billing period detected for this upload"}

    # Get actual cost from parsed line items
    line_items = db.query(CsvBedrockLineItem).filter(
        CsvBedrockLineItem.upload_id == upload_id
    ).all()
    total_actual = float(sum(item.unblended_cost for item in line_items))

    # Get estimated costs
    user_estimated = get_estimated_costs_by_user(db, upload.billing_period)
    if not user_estimated and not line_items:
        return {"error": f"No activity data available for billing period {upload.billing_period}"}

    total_estimated = sum(user_estimated.values())

    # Proportional allocation
    allocation = calculate_proportional_allocation(user_estimated, total_actual)

    # Team assignments
    team_map = get_team_assignments(db)

    # Build team aggregation
    team_agg: dict[str, dict[str, float]] = defaultdict(
        lambda: {"estimated_cost": 0.0, "allocated_actual_cost": 0.0, "variance": 0.0}
    )
    for user, data in allocation.items():
        team_name = team_map.get(user, "Unassigned")
        team_agg[team_name]["estimated_cost"] += data["estimated_cost"]
        team_agg[team_name]["allocated_actual_cost"] += data["allocated_actual_cost"]
        team_agg[team_name]["variance"] += data["variance"]

    # Generate insights via LLM agent (best-effort)
    insights = _generate_insights(allocation, team_agg, total_actual, total_estimated, line_items)

    # Check for existing report (idempotent)
    existing = db.query(ChargebackReport).filter(
        ChargebackReport.upload_id == upload_id
    ).first()
    if existing:
        # Delete old report lines and update
        db.query(ChargebackReportLine).filter(
            ChargebackReportLine.report_id == existing.id
        ).delete()
        report = existing
        report.total_actual_cost = Decimal(str(round(total_actual, 4)))
        report.total_estimated_cost = Decimal(str(round(total_estimated, 4)))
        report.total_variance = Decimal(str(round(total_actual - total_estimated, 4)))
        report.insights = insights
        report.generated_at = datetime.now(timezone.utc)
    else:
        report = ChargebackReport(
            upload_id=upload_id,
            billing_period=upload.billing_period,
            total_actual_cost=Decimal(str(round(total_actual, 4))),
            total_estimated_cost=Decimal(str(round(total_estimated, 4))),
            total_variance=Decimal(str(round(total_actual - total_estimated, 4))),
            insights=insights,
        )
        db.add(report)
        db.flush()

    # Store report lines — users
    for user, data in allocation.items():
        db.add(ChargebackReportLine(
            report_id=report.id,
            entity_type="user",
            entity_name=user,
            estimated_cost=Decimal(str(round(data["estimated_cost"], 4))),
            allocated_actual_cost=Decimal(str(round(data["allocated_actual_cost"], 4))),
            variance=Decimal(str(round(data["variance"], 4))),
            percentage_share=Decimal(str(round(data["percentage_share"], 4))),
        ))

    # Store report lines — teams
    total_alloc_for_pct = sum(t["allocated_actual_cost"] for t in team_agg.values()) or 1.0
    for team_name, data in team_agg.items():
        pct = (data["allocated_actual_cost"] / total_alloc_for_pct) * 100
        db.add(ChargebackReportLine(
            report_id=report.id,
            entity_type="team",
            entity_name=team_name,
            estimated_cost=Decimal(str(round(data["estimated_cost"], 4))),
            allocated_actual_cost=Decimal(str(round(data["allocated_actual_cost"], 4))),
            variance=Decimal(str(round(data["variance"], 4))),
            percentage_share=Decimal(str(round(pct, 4))),
        ))

    db.commit()

    return {
        "report_id": str(report.id),
        "billing_period": upload.billing_period,
        "total_actual_cost": total_actual,
        "total_estimated_cost": total_estimated,
        "total_variance": total_actual - total_estimated,
        "insights": insights,
        "user_count": len(allocation),
        "team_count": len(team_agg),
    }


def _generate_insights(
    allocation: dict, team_agg: dict, total_actual: float, total_estimated: float,
    line_items: list | None = None,
) -> str:
    """Generate natural-language insights using Claude Haiku LLM.
    
    Passes the full billing context to the LLM for variance analysis,
    discrepancy reasoning, and actionable recommendations.
    Falls back to deterministic output if LLM is unavailable.
    """
    variance = total_actual - total_estimated
    pct = (abs(variance) / total_estimated * 100) if total_estimated > 0 else 0
    direction = "higher" if variance > 0 else "lower"

    # Build rich context for the LLM
    top_users = sorted(allocation.items(), key=lambda x: x[1].get("allocated_actual_cost", 0), reverse=True)[:10]
    user_summary = "\n".join(
        f"  - {u}: estimated ${d['estimated_cost']:.2f}, allocated ${d['allocated_actual_cost']:.2f}, variance ${d['variance']:.2f} ({d['percentage_share']:.1f}%)"
        for u, d in top_users
    )

    team_summary = "\n".join(
        f"  - {t}: estimated ${d['estimated_cost']:.2f}, allocated ${d['allocated_actual_cost']:.2f}, variance ${d['variance']:.2f}"
        for t, d in sorted(team_agg.items(), key=lambda x: x[1].get("allocated_actual_cost", 0), reverse=True)
    )

    line_item_summary = ""
    if line_items:
        line_item_summary = "\nBilling line items from AWS Cost Explorer:\n" + "\n".join(
            f"  - {item.service}: ${float(item.unblended_cost):.2f} ({item.usage_type or 'N/A'})"
            for item in line_items[:20]
        )

    prompt = f"""You are a cloud cost analyst for Amazon Bedrock. Analyze this chargeback reconciliation and provide insights.

BILLING DATA:
- Total actual AWS cost (from Cost Explorer): ${total_actual:,.2f}
- Total estimated cost (from CloudWatch Logs token usage): ${total_estimated:,.2f}
- Variance: ${variance:,.2f} ({pct:.1f}% {direction} than estimated)
- Number of users: {len(allocation)}
- Number of teams: {len(team_agg)}
{line_item_summary}

TOP USERS BY ALLOCATED COST:
{user_summary}

TEAM BREAKDOWN:
{team_summary}

Provide a concise analysis covering:
1. VARIANCE SUMMARY: Why the actual cost is {direction} than estimated. List specific likely reasons (e.g., provisioned throughput, model customization, batch inference, cached tokens not tracked, pricing differences, invocations before logging was enabled).
2. TOP COST DRIVERS: Which users/teams are driving the most cost and why.
3. RECOMMENDATIONS: 2-3 actionable steps to reduce the variance or optimize costs.
4. DISCLAIMER: Note this is a best-effort reconciliation based on CloudWatch Logs vs actual billing.

Keep it under 300 words. Use bullet points. Be specific with numbers."""

    try:
        import boto3
        from app.config import settings

        region = settings.bedrock_region or settings.aws_region
        client = boto3.client("bedrock-runtime", region_name=region)

        response = client.converse(
            modelId="us.anthropic.claude-haiku-4-5-20251001-v1:0",
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": 600},
        )

        output = response.get("output", {}).get("message", {}).get("content", [])
        llm_text = "".join(b.get("text", "") for b in output)
        if llm_text.strip():
            logger.info("LLM insights generated successfully (%d chars)", len(llm_text))
            return llm_text.strip()
    except Exception as exc:
        logger.warning("LLM insights generation failed, using deterministic: %s", exc)

    # Deterministic fallback
    lines = [
        "⚠️ Best-Effort Reconciliation Disclaimer: This report compares actual AWS billing "
        "against CloudWatch Logs-based cost estimates. Variance is expected.",
        "",
        f"Total actual cost: ${total_actual:,.2f}",
        f"Total estimated cost: ${total_estimated:,.2f}",
        f"Variance: ${variance:,.2f} ({pct:.1f}% {direction} than estimated)",
        "",
        "Possible reasons for variance:",
        "  • Provisioned throughput charges not captured in CloudWatch Logs",
        "  • Model customization or fine-tuning costs",
        "  • Batch inference jobs",
        "  • Invocations made before logging was enabled",
        "  • Pricing differences between estimated and actual rates",
        "",
    ]

    if team_agg:
        top_team = max(team_agg.items(), key=lambda x: x[1]["allocated_actual_cost"])
        lines.append(
            f"Largest cost center: {top_team[0]} "
            f"(${top_team[1]['allocated_actual_cost']:,.2f} allocated)"
        )

    return "\n".join(lines)


def export_report_csv(report: ChargebackReport, lines: list[ChargebackReportLine]) -> str:
    """Format report as CSV with summary row."""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Entity Type", "Entity Name", "Estimated Cost",
        "Allocated Actual Cost", "Variance", "Percentage Share",
    ])

    sum_est = Decimal("0")
    sum_alloc = Decimal("0")
    sum_var = Decimal("0")

    for line in lines:
        writer.writerow([
            line.entity_type,
            line.entity_name,
            f"{line.estimated_cost:.4f}",
            f"{line.allocated_actual_cost:.4f}",
            f"{line.variance:.4f}",
            f"{line.percentage_share:.4f}",
        ])
        sum_est += line.estimated_cost
        sum_alloc += line.allocated_actual_cost
        sum_var += line.variance

    # Summary row
    writer.writerow([
        "TOTAL", "",
        f"{sum_est:.4f}",
        f"{sum_alloc:.4f}",
        f"{sum_var:.4f}",
        "",
    ])

    return output.getvalue()
