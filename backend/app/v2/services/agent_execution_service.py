"""Agent Execution Service for the V2 Agent Platform.

Manages agent execution lifecycle, history recording, and configuration updates.
"""

import logging
import traceback
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models import ModelPricing
from app.v2.models import V2AgentConfig, V2AgentExecution
from app.v2.models import (
    # NOTE: V2AgentPiiFinding is intentionally NOT imported here. The PII
    # Detector agent was removed from the platform; its table may still exist
    # on legacy databases but no runtime code path writes to it.
    V2AgentCostAnomaly,
    V2AgentGenericFinding,
    V2AgentGuardrailRecommendation,
    V2AgentModelCostRecommendation,
    V2AgentPromptQualityFinding,
    V2AgentLatencyErrorFinding,
    V2AgentChargebackReport,
    V2AgentOnboardingFinding,
)
from app.v2.services.agentcore_invoker import AgentCoreInvoker, AgentUnavailableError

logger = logging.getLogger(__name__)


class AgentExecutionService:
    """Manages agent execution, scheduling, and history."""

    def __init__(self) -> None:
        self._invoker = AgentCoreInvoker()

    def execute_agent(self, agent_name: str, trigger_type: str, db: Session) -> dict:
        """Execute an agent, record execution history, persist findings.

        For scheduled triggers, skips execution if the agent is disabled.
        Manual triggers are always allowed regardless of enabled status.
        """
        # Check if agent is disabled for scheduled triggers
        if trigger_type == "scheduled":
            config = (
                db.query(V2AgentConfig)
                .filter(V2AgentConfig.agent_name == agent_name)
                .first()
            )
            if config and not config.enabled:
                logger.info(
                    "Skipping scheduled execution for disabled agent=%s",
                    agent_name,
                )
                return {
                    "agent_name": agent_name,
                    "status": "skipped",
                    "reason": "Agent is disabled; scheduled execution skipped.",
                }

        # Record execution start
        start_time = datetime.now(timezone.utc)
        execution = V2AgentExecution(
            agent_name=agent_name,
            trigger_type=trigger_type,
            start_time=start_time,
            status="running",
            finding_count=0,
        )
        db.add(execution)
        db.flush()

        try:
            # For agents with deterministic tools, call them directly
            # instead of going through the LLM which may not invoke them
            findings = self._try_direct_execution(agent_name, db)
            if findings is None:
                # Fall back to LLM-based invocation
                result = self._invoker.invoke_agent(
                    agent_name, {"trigger_type": trigger_type}, db, trigger_type=trigger_type
                )
                findings = result.get("findings", [])

            persisted_count = self._persist_findings(agent_name, findings, db)

            # Update execution record on success
            execution.end_time = datetime.now(timezone.utc)
            execution.status = "success"
            execution.finding_count = persisted_count
            db.commit()

            logger.info(
                "Agent execution succeeded",
                extra={
                    "agent_name": agent_name,
                    "trigger_type": trigger_type,
                    "finding_count": execution.finding_count,
                },
            )

            return {
                "agent_name": agent_name,
                "execution_id": str(execution.id),
                "status": "success",
                "finding_count": execution.finding_count,
                "start_time": execution.start_time.isoformat(),
                "end_time": execution.end_time.isoformat(),
            }

        except AgentUnavailableError as exc:
            execution.end_time = datetime.now(timezone.utc)
            execution.status = "failure"
            execution.error_details = str(exc)
            db.commit()

            logger.error(
                "Agent execution failed: agent unavailable",
                extra={
                    "agent_name": agent_name,
                    "trigger_type": trigger_type,
                    "error_class": type(exc).__name__,
                    "error_message": str(exc),
                    "traceback": traceback.format_exc(),
                },
            )

            return AgentCoreInvoker.build_error_response(agent_name, str(exc))

        except Exception as exc:
            execution.end_time = datetime.now(timezone.utc)
            execution.status = "failure"
            execution.error_details = str(exc)
            db.commit()

            logger.error(
                "Agent execution failed: unexpected error",
                extra={
                    "agent_name": agent_name,
                    "trigger_type": trigger_type,
                    "error_class": type(exc).__name__,
                    "error_message": str(exc),
                    "traceback": traceback.format_exc(),
                },
            )

            return {
                "agent_name": agent_name,
                "execution_id": str(execution.id),
                "status": "failure",
                "error": str(exc),
                "start_time": execution.start_time.isoformat(),
                "end_time": execution.end_time.isoformat(),
            }

    def _try_direct_execution(self, agent_name: str, db: Session) -> list | None:
        """Try to execute agent tools directly without going through the LLM.
        
        Returns findings list if direct execution is supported, None otherwise.
        """
        if agent_name == "cur-ingestion":
            # Deterministic, no LLM fallback: a failed ingestion raises
            # CurIngestionFailedError (handled by execute_agent as a failure);
            # success/skipped return an informational finding. Kept outside the
            # broad try/except below so failures never degrade to the LLM path.
            from app.v2.agents.cur_ingestion import run_ingestion

            return run_ingestion(db)

        try:
            if agent_name == "model-cost-optimizer":
                # Import the module and call the core logic directly
                # (bypassing the @strands_tool decorator which may alter return types)
                from app.v2.agents import model_cost_optimizer as mco
                # Call the underlying function logic
                from app.services.cloudtrail_service import CloudTrailService
                from app.services.cost_service import CostService
                from collections import defaultdict

                end = datetime.now(timezone.utc)
                start = end - timedelta(days=14)
                ct = CloudTrailService()
                records = ct.get_activity_records(start, end)
                logger.info("model-cost-optimizer direct: fetched %d records", len(records))

                # Read from the shared model_pricing table (same source of truth
                # as CostService/pricing.py) instead of a duplicated cost map,
                # reusing this execution's own `db` session rather than opening
                # a second one via mco._load_input_price_map().
                input_price_map = {
                    model_id: float(fields["inputTokenPrice"])
                    for model_id, fields in CostService.load_pricing(db).items()
                }

                # Group by model
                model_usage: dict[str, list] = defaultdict(list)
                for r in records:
                    model_id = r.get("modelId", "unknown")
                    model_usage[model_id].append(r)

                findings = []
                for model_id, invocations in model_usage.items():
                    if any(cheap in model_id for cheap in mco.CHEAPER_ALTERNATIVES):
                        continue
                    total = len(invocations)
                    if total == 0:
                        continue

                    simple_invocations = [
                        inv for inv in invocations
                        if (inv.get("inputTokens") or 0) < mco.SIMPLE_TASK_TOKEN_THRESHOLD
                    ]
                    simple_count = len(simple_invocations)

                    # If no simple tasks, check if token data is unavailable
                    if simple_count == 0:
                        zero_token_count = sum(1 for inv in invocations if (inv.get("inputTokens") or 0) == 0)
                        if zero_token_count > total * 0.5:
                            simple_invocations = invocations
                            simple_count = total
                        else:
                            continue

                    # Build per-user breakdown
                    user_details: dict[str, dict] = defaultdict(lambda: {
                        "invocation_count": 0, "simple_count": 0, "total_input_tokens": 0,
                    })
                    for inv in invocations:
                        user_name = inv.get("userName", "unknown")
                        user_arn = inv.get("userIdentity", "unknown")
                        display_name = user_name if user_name != "unknown" else user_arn.split("/")[-1] if "/" in user_arn else user_arn
                        detail = user_details[display_name]
                        detail["invocation_count"] += 1
                        detail["total_input_tokens"] += inv.get("inputTokens", 0)
                        is_simple = (inv.get("inputTokens") or 0) < mco.SIMPLE_TASK_TOKEN_THRESHOLD
                        if is_simple:
                            detail["simple_count"] += 1

                    # Pick best alternative
                    current_cost = input_price_map.get(model_id, mco._FALLBACK_INPUT_TOKEN_PRICE)
                    best_alt = None
                    best_alt_cost = current_cost
                    for alt_id, alt_cost in input_price_map.items():
                        if alt_id == model_id or alt_cost >= current_cost:
                            continue
                        if not any(cheap in alt_id for cheap in mco.CHEAPER_ALTERNATIVES):
                            continue
                        if alt_cost < best_alt_cost:
                            best_alt = alt_id
                            best_alt_cost = alt_cost

                    if not best_alt:
                        continue

                    total_simple_tokens = sum(inv.get("inputTokens", 0) for inv in simple_invocations)
                    monthly_tokens_k = (total_simple_tokens / 1000) * 4.3 if total_simple_tokens > 0 else 100 * 30
                    monthly_savings = round((current_cost - best_alt_cost) * monthly_tokens_k, 2)

                    model_short = model_id.split(".")[-1].split("-v")[0] if "." in model_id else model_id
                    alt_short = best_alt.split(".")[-1].split("-v")[0] if "." in best_alt else best_alt

                    reasoning_parts = [
                        f"Over the past 14 days, {simple_count} of {total} invocations to {model_short} "
                        f"used fewer than {mco.SIMPLE_TASK_TOKEN_THRESHOLD} input tokens.",
                    ]
                    top_users = sorted(user_details.items(), key=lambda x: x[1]["simple_count"], reverse=True)[:5]
                    for uname, detail in top_users:
                        if detail["simple_count"] > 0:
                            line = f"  \u2022 {uname}: {detail['simple_count']} calls"
                            reasoning_parts.append(line)
                    reasoning_parts.append(
                        f"Switching to {alt_short} would save ~${monthly_savings:.2f}/mo "
                        f"({current_cost}/1K \u2192 {best_alt_cost}/1K tokens)."
                    )

                    affected_user_count = len([u for u in user_details.values() if u["simple_count"] > 0])
                    simple_task_ratio = round(simple_count / total, 2)

                    # Distillation candidate check — additive, mirrors
                    # mco.analyze_usage_patterns() so both execution paths (this
                    # direct-execution bypass and the LLM tool-call fallback)
                    # produce the same recommendation for identical usage.
                    distillation_candidate = mco.is_distillation_candidate(
                        total, simple_task_ratio, affected_user_count
                    )
                    if distillation_candidate:
                        reasoning_parts.append(
                            mco.build_distillation_note(
                                model_id, total, simple_task_ratio, affected_user_count
                            )
                        )

                    findings.append({
                        "current_model": model_id,
                        "suggested_model": best_alt,
                        "estimated_monthly_savings": monthly_savings,
                        "affected_user_count": affected_user_count,
                        "severity": "high" if monthly_savings > 50 else "medium",
                        "usage_pattern_summary": "\n".join(reasoning_parts),
                        "is_distillation_candidate": distillation_candidate,
                    })

                logger.info("model-cost-optimizer direct: generated %d findings", len(findings))
                return findings

            if agent_name == "cost-anomaly-alerter":
                from app.v2.agents.cost_anomaly_alerter import detect_anomalies
                return detect_anomalies()

            if agent_name == "chargeback-report-generator":
                from app.services.cloudtrail_service import CloudTrailService
                from app.services.cost_service import CostService
                from collections import defaultdict

                current_month = datetime.now(timezone.utc)
                # Cover last 2 months to capture recent data
                start = current_month.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                if start.month <= 2:
                    start = start.replace(year=start.year - 1, month=start.month + 10)
                else:
                    start = start.replace(month=start.month - 2)
                end = datetime.now(timezone.utc)

                ct = CloudTrailService()
                records = ct.get_activity_records(start, end)
                logger.info("chargeback direct: fetched %d records from %s to %s", len(records), start, end)

                CostService.apply_costs(records, db)

                # Build user→team map using the existing db session
                from app.models import Team, TeamMembership
                memberships = db.query(TeamMembership).all()
                user_team = {}
                for m in memberships:
                    team = db.query(Team).filter(Team.id == m.team_id).first()
                    if team:
                        user_team[m.user_identity] = team.name

                logger.info("chargeback direct: %d team memberships loaded: %s", len(user_team), user_team)

                # Aggregate costs by team
                # Match on userName (hashed ID) since team memberships store hashed IDs
                team_costs: dict[str, dict] = defaultdict(lambda: {"cost": 0.0, "invocations": 0, "users": set(), "models": set()})
                for r in records:
                    user_arn = r.get("userIdentity", "unknown")
                    user_name = r.get("userName", "unknown")
                    # Try matching by full ARN first, then by userName (hashed ID)
                    team_name = user_team.get(user_arn, user_team.get(user_name, "Unassigned"))
                    cost = r.get("costEstimate") or 0.0
                    tc = team_costs[team_name]
                    tc["cost"] += cost
                    tc["invocations"] += 1
                    tc["users"].add(user_name if user_name != "unknown" else user_arn.split("/")[-1])
                    tc["models"].add(r.get("modelId", "unknown"))

                findings = []
                period_start = start.strftime("%Y-%m-%d")
                period_end = end.strftime("%Y-%m-%d")
                for team_name, data in sorted(team_costs.items(), key=lambda x: x[1]["cost"], reverse=True):
                    findings.append({
                        "team_name": team_name,
                        "total_cost": round(data["cost"], 4),
                        "report_period_start": period_start,
                        "report_period_end": period_end,
                        "cost_breakdown": {
                            "invocation_count": data["invocations"],
                            "models_used": list(data["models"]),
                            "users": list(data["users"]),
                        },
                        "severity": "medium" if data["cost"] > 100 else "low",
                    })
                logger.info("chargeback direct: %d teams, total $%.2f", len(findings), sum(d["cost"] for d in team_costs.values()))
                return findings

            # NOTE: The ``pii-detector`` direct-execution branch was removed
            # when the PII Detector agent was retired from the platform. The
            # ``v2_agent_pii_findings`` table is intentionally left in place
            # on legacy databases (no destructive migration), but no agent
            # code path reads or writes it, and the agent config row is
            # purged by ``seed_agent_configs`` on startup. Do not re-add a
            # branch here without reinstating the full agent (seed row,
            # frontend tile, docs) — see commit history for the removal.

            # guardrail-recommender is agentic (is_agentic=True) and its
            # AgentCore Runtime package makes a real LLM call — it must NOT
            # have a branch here. A prior version of this method had one
            # (returning a template-based recommendation list directly, even
            # `[]` for "no findings"), which meant _try_direct_execution()
            # never returned None for this agent and execute_agent() never
            # fell through to AgentCoreInvoker.invoke_agent(). That silently
            # bypassed AgentCore Runtime entirely: no InvokeAgentRuntime
            # calls were ever made, so no AgentCore Runtime cost was ever
            # accrued for this agent regardless of how many times it ran.
        except Exception as exc:
            logger.warning("Direct execution failed for %s, falling back to LLM: %s", agent_name, exc, exc_info=True)
        return None

    def _persist_findings(self, agent_name: str, findings: list, db: Session) -> int:
        """Persist agent findings to the appropriate v2 table. Returns count persisted.
        
        Deduplicates by checking for existing similar findings to avoid duplicates
        across multiple agent executions.
        """
        if not findings:
            return 0

        now = datetime.now(timezone.utc)
        count = 0

        # Deduplicate findings within the batch to avoid unique constraint violations
        seen_keys: set[str] = set()
        deduped_findings = []
        for f in findings:
            if agent_name == "model-cost-optimizer":
                key = f.get("current_model", "")
            else:
                key = str(f)  # fallback: use full dict as key
            if key not in seen_keys:
                seen_keys.add(key)
                deduped_findings.append(f)

        for f in deduped_findings:
            try:
                if agent_name == "cost-anomaly-alerter":
                    # Deduplicate by anomaly_date
                    existing = db.query(V2AgentCostAnomaly).filter(
                        V2AgentCostAnomaly.anomaly_date == f.get("anomaly_date", now.date()),
                    ).first()
                    if existing:
                        continue
                    db.add(V2AgentCostAnomaly(
                        agent_name=agent_name, status="active", severity=f.get("severity", "high"),
                        anomaly_date=f.get("anomaly_date", now.date()),
                        current_spend=f.get("current_spend", 0),
                        rolling_average=f.get("rolling_average", 0),
                        threshold_multiplier=f.get("threshold_multiplier", 2.0),
                        contributing_users=f.get("contributing_users"),
                        created_at=now, updated_at=now,
                    ))
                elif agent_name == "model-cost-optimizer":
                    # Replace all old findings for this model with the new rich finding
                    current_model = f.get("current_model", f.get("model_id", "unknown"))
                    # Delete ALL old active findings for this model
                    try:
                        old_count = db.query(V2AgentModelCostRecommendation).filter(
                            V2AgentModelCostRecommendation.current_model == current_model,
                        ).delete(synchronize_session=False)
                        db.flush()
                        logger.info("Deleted %d old findings for model %s", old_count, current_model)
                    except Exception as del_exc:
                        logger.warning("Failed to delete old findings for %s: %s", current_model, del_exc)
                    db.add(V2AgentModelCostRecommendation(
                        agent_name=agent_name, status="active", severity=f.get("severity", "medium"),
                        current_model=current_model,
                        suggested_model=f.get("suggested_model", "unknown"),
                        estimated_monthly_savings=f.get("estimated_monthly_savings", f.get("savings", 0)),
                        affected_user_count=f.get("affected_user_count", f.get("affected_users", 1)),
                        usage_pattern_summary=f.get("usage_pattern_summary", f.get("description")),
                        created_at=now, updated_at=now,
                    ))
                elif agent_name == "latency-error-monitor":
                    # Deduplicate by metric_type + affected_model within same hour
                    from datetime import timedelta
                    one_hour_ago = now - timedelta(hours=1)
                    existing = db.query(V2AgentLatencyErrorFinding).filter(
                        V2AgentLatencyErrorFinding.metric_type == f.get("metric_type", "latency_spike"),
                        V2AgentLatencyErrorFinding.affected_model == f.get("affected_model"),
                        V2AgentLatencyErrorFinding.created_at >= one_hour_ago,
                    ).first()
                    if existing:
                        continue
                    db.add(V2AgentLatencyErrorFinding(
                        agent_name=agent_name, status="active", severity=f.get("severity", "medium"),
                        metric_type=f.get("metric_type", "latency_spike"),
                        current_value=f.get("current_value", 0),
                        baseline_value=f.get("baseline_value", 0),
                        affected_model=f.get("affected_model"),
                        affected_region=f.get("affected_region"),
                        created_at=now, updated_at=now,
                    ))
                elif agent_name == "onboarding-assistant":
                    # Deduplicate by user_identity (one onboarding per user)
                    user_id = f.get("user_identity", "unknown")
                    existing = db.query(V2AgentOnboardingFinding).filter(
                        V2AgentOnboardingFinding.user_identity == user_id,
                    ).first()
                    if existing:
                        continue
                    db.add(V2AgentOnboardingFinding(
                        agent_name=agent_name, status="active", severity="low",
                        user_identity=user_id,
                        team_assignment=f.get("team_assignment"),
                        guidance_content=f.get("guidance_content"),
                        created_at=now, updated_at=now,
                    ))
                elif agent_name == "guardrail-recommender":
                    # Delete all old recommendations for this PII type to avoid duplicates
                    rec = f.get("recommendation", {})
                    pii_type = rec.get("pii_type", "") if isinstance(rec, dict) else ""
                    if pii_type:
                        # Delete old recommendations that match this pii_type
                        old_recs = db.query(V2AgentGuardrailRecommendation).filter(
                            V2AgentGuardrailRecommendation.agent_name == agent_name,
                        ).all()
                        for old in old_recs:
                            old_rec = old.recommendation if isinstance(old.recommendation, dict) else {}
                            if old_rec.get("pii_type") == pii_type:
                                db.delete(old)
                        db.flush()
                    db.add(V2AgentGuardrailRecommendation(
                        agent_name=agent_name, status="pending_review", severity=f.get("severity", "medium"),
                        recommendation=f.get("recommendation", f),
                        based_on_findings=f.get("based_on_findings"),
                        created_at=now, updated_at=now,
                    ))
                elif agent_name == "prompt-quality-analyzer":
                    # Deduplicate by user_identity + anti_pattern_type within same day
                    existing = db.query(V2AgentPromptQualityFinding).filter(
                        V2AgentPromptQualityFinding.user_identity == f.get("user_identity", "unknown"),
                        V2AgentPromptQualityFinding.anti_pattern_type == f.get("anti_pattern_type", "unknown"),
                        V2AgentPromptQualityFinding.created_at >= now.replace(hour=0, minute=0, second=0),
                    ).first()
                    if existing:
                        continue
                    db.add(V2AgentPromptQualityFinding(
                        agent_name=agent_name, status="active", severity=f.get("severity", "low"),
                        user_identity=f.get("user_identity", "unknown"),
                        anti_pattern_type=f.get("anti_pattern_type", "unknown"),
                        prompt_excerpt=f.get("prompt_excerpt"),
                        improvement_suggestion=f.get("improvement_suggestion"),
                        quality_score=f.get("quality_score"),
                        created_at=now, updated_at=now,
                    ))
                elif agent_name == "chargeback-report-generator":
                    # Replace old reports for the same team + period
                    team_name = f.get("team_name", "Unassigned")
                    period_start = f.get("report_period_start", now.date().replace(day=1))
                    period_end = f.get("report_period_end", now.date())
                    db.query(V2AgentChargebackReport).filter(
                        V2AgentChargebackReport.team_name == team_name,
                        V2AgentChargebackReport.report_period_start == period_start,
                    ).delete(synchronize_session=False)
                    db.add(V2AgentChargebackReport(
                        agent_name=agent_name, status="active", severity=f.get("severity", "low"),
                        report_period_start=period_start,
                        report_period_end=period_end,
                        team_name=team_name,
                        total_cost=f.get("total_cost", 0),
                        cost_breakdown=f.get("cost_breakdown"),
                        created_at=now, updated_at=now,
                    ))
                elif agent_name == "cur-ingestion":
                    # Informational status finding: only the latest run matters
                    # (run history lives in v2_agent_executions), so replace any
                    # previous cur-ingestion findings with this one.
                    db.query(V2AgentGenericFinding).filter(
                        V2AgentGenericFinding.agent_name == agent_name,
                    ).delete(synchronize_session=False)
                    ingest_status = f.get("status", "unknown")
                    rows = f.get("rows_upserted", 0)
                    covered = f.get("covered_through")
                    if ingest_status == "success":
                        title = (
                            f"CUR ingestion succeeded: {rows} rows upserted, "
                            f"billed data covered through {covered or 'n/a'}"
                        )
                    else:
                        title = (
                            f"CUR ingestion {ingest_status}: "
                            f"{f.get('error_detail') or 'no billed rows ingested'}"
                        )
                    db.add(V2AgentGenericFinding(
                        agent_name=agent_name, status="active",
                        severity=f.get("severity", "low"),
                        title=title, details=f,
                        created_at=now, updated_at=now,
                    ))
                else:
                    logger.debug("No persistence handler for agent %s", agent_name)
                    continue
                count += 1
            except Exception as exc:
                logger.warning("Failed to persist finding for %s: %s", agent_name, exc)

        if count > 0:
            db.flush()
        return count

    def get_execution_history(
        self, agent_name: str, db: Session, limit: int = 50
    ) -> list:
        """Get execution history for an agent, ordered by start_time descending."""
        rows = (
            db.query(V2AgentExecution)
            .filter(V2AgentExecution.agent_name == agent_name)
            .order_by(V2AgentExecution.start_time.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "id": str(row.id),
                "agent_name": row.agent_name,
                "trigger_type": row.trigger_type,
                "start_time": row.start_time.isoformat(),
                "end_time": row.end_time.isoformat() if row.end_time else None,
                "status": row.status,
                "finding_count": row.finding_count,
                "error_details": row.error_details,
                "created_at": row.created_at.isoformat(),
            }
            for row in rows
        ]

    @staticmethod
    def _is_approved_model(model_id: str | None, db: Session) -> bool:
        """Approved = already used by an agent config or priced in model_pricing."""
        if not model_id:
            return False
        in_configs = (
            db.query(V2AgentConfig.id).filter(V2AgentConfig.model_id == model_id).first()
        )
        if in_configs is not None:
            return True
        return db.query(ModelPricing.id).filter(ModelPricing.model_id == model_id).first() is not None

    def update_agent_config(
        self, agent_name: str, config: dict, db: Session
    ) -> dict:
        """Update agent schedule, enabled/disabled status."""
        agent_config = (
            db.query(V2AgentConfig)
            .filter(V2AgentConfig.agent_name == agent_name)
            .first()
        )
        if not agent_config:
            return {"error": "not_found", "message": f"Agent '{agent_name}' not found."}

        # Validate before mutating anything so a rejected request is a no-op.
        if "model_id" in config and not self._is_approved_model(config["model_id"], db):
            return {
                "error": "invalid_model",
                "message": "model_id is not an approved model.",
            }

        if "schedule_cron" in config:
            agent_config.schedule_cron = config["schedule_cron"]
        if "enabled" in config:
            agent_config.enabled = config["enabled"]
        if "display_name" in config:
            agent_config.display_name = config["display_name"]
        if "category" in config:
            agent_config.category = config["category"]
        if "model_id" in config:
            agent_config.model_id = config["model_id"]

        db.commit()
        db.refresh(agent_config)

        return {
            "id": str(agent_config.id),
            "agent_name": agent_config.agent_name,
            "display_name": agent_config.display_name,
            "category": agent_config.category,
            "model_id": agent_config.model_id,
            "schedule_cron": agent_config.schedule_cron,
            "enabled": agent_config.enabled,
            "updated_at": agent_config.updated_at.isoformat(),
        }
