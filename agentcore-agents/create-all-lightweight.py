"""Create lightweight main.py files for all 13 agents.

Each agent uses a pure HTTP server pattern (no Strands SDK) to stay
within AgentCore's 30s initialization timeout.
"""

import os
import textwrap

AGENTS = {
    "cost-anomaly-alerter": {
        "description": "Detects spending spikes against rolling averages",
        "code": textwrap.dedent('''
            DEFAULT_WINDOW_DAYS = 7
            DEFAULT_THRESHOLD_MULTIPLIER = 2.0
            MIN_HISTORY_DAYS = 3

            def compute_rolling_average(daily_spends, window_days=DEFAULT_WINDOW_DAYS):
                if not daily_spends:
                    return {"rolling_average": 0, "days_available": 0}
                recent = daily_spends[-window_days:] if len(daily_spends) > window_days else daily_spends
                total = sum(e.get("spend", 0) for e in recent)
                avg = total / len(recent) if recent else 0
                return {"rolling_average": round(avg, 4), "days_available": len(daily_spends)}

            def detect_anomalies(daily_spends, threshold_multiplier=DEFAULT_THRESHOLD_MULTIPLIER):
                data = compute_rolling_average(daily_spends)
                if data["days_available"] < MIN_HISTORY_DAYS:
                    return []
                avg = data["rolling_average"]
                if avg <= 0:
                    return []
                anomalies = []
                for entry in daily_spends:
                    spend = entry.get("spend", 0)
                    if spend > avg * threshold_multiplier:
                        anomalies.append({
                            "anomaly_date": entry.get("date", ""),
                            "current_spend": spend,
                            "rolling_average": avg,
                            "threshold_multiplier": threshold_multiplier,
                            "severity": "critical" if spend > avg * 3 else "high",
                        })
                return anomalies

            def handle_invocation(payload):
                data = payload.get("data", {})
                daily_spends = data.get("daily_spends", [])
                if not daily_spends:
                    return {"result": "No spend data provided", "findings": [], "finding_count": 0}
                findings = detect_anomalies(daily_spends)
                if findings:
                    result = f"Cost anomaly scan complete. {len(findings)} anomaly(ies) detected."
                else:
                    result = "Cost anomaly scan complete. No anomalies detected."
                return {"result": result, "findings": findings, "finding_count": len(findings)}
        '''),
    },
    "guardrail-recommender": {
        "description": "Generates Bedrock Guardrail configuration suggestions based on PII findings",
        "code": textwrap.dedent('''
            GUARDRAIL_TEMPLATES = {
                "email": {"type": "PII", "action": "ANONYMIZE", "description": "Email addresses detected"},
                "phone": {"type": "PII", "action": "ANONYMIZE", "description": "Phone numbers detected"},
                "ssn": {"type": "PII", "action": "BLOCK", "description": "Social Security Numbers detected"},
                "credit_card": {"type": "PII", "action": "BLOCK", "description": "Credit card numbers detected"},
                "aws_access_key": {"type": "PII", "action": "BLOCK", "description": "AWS access keys detected"},
            }

            def generate_guardrail_config(pii_types):
                configs = []
                for pii_type in pii_types:
                    template = GUARDRAIL_TEMPLATES.get(pii_type, {"type": "PII", "action": "ANONYMIZE", "description": f"{pii_type} detected"})
                    configs.append({"pii_type": pii_type, **template})
                return configs

            def handle_invocation(payload):
                data = payload.get("data", {})
                pii_findings = data.get("pii_findings", [])
                if not pii_findings:
                    return {"result": "No PII findings to process", "findings": [], "finding_count": 0}
                pii_types = list(set(f.get("pii_type", "unknown") for f in pii_findings))
                configs = generate_guardrail_config(pii_types)
                findings = [{"recommendation": c, "based_on_findings": pii_types, "severity": "medium"} for c in configs]
                result = f"Generated {len(configs)} guardrail recommendation(s) for PII types: {', '.join(pii_types)}"
                return {"result": result, "findings": findings, "finding_count": len(findings)}
        '''),
    },
    "model-cost-optimizer": {
        "description": "Analyzes usage patterns to find expensive models used for simple tasks",
        "code": textwrap.dedent('''
            MODEL_COST_MAP = {
                "us.anthropic.claude-sonnet-4-6": {"input_per_1k": 0.003, "output_per_1k": 0.015},
                "us.anthropic.claude-haiku-4-5-20251001-v1:0": {"input_per_1k": 0.0008, "output_per_1k": 0.004},
                "us.amazon.nova-pro-v1:0": {"input_per_1k": 0.0008, "output_per_1k": 0.0032},
                "us.amazon.nova-lite-v1:0": {"input_per_1k": 0.00006, "output_per_1k": 0.00024},
                "us.amazon.nova-micro-v1:0": {"input_per_1k": 0.000035, "output_per_1k": 0.00014},
            }
            CHEAPER_ALTERNATIVES = {
                "us.anthropic.claude-sonnet-4-6": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
                "us.amazon.nova-pro-v1:0": "us.amazon.nova-lite-v1:0",
            }

            def analyze_usage(usage_data):
                findings = []
                for model_id, stats in usage_data.items():
                    alt = CHEAPER_ALTERNATIVES.get(model_id)
                    if alt and model_id in MODEL_COST_MAP and alt in MODEL_COST_MAP:
                        current_cost = MODEL_COST_MAP[model_id]
                        alt_cost = MODEL_COST_MAP[alt]
                        monthly_tokens = stats.get("monthly_input_tokens", 0)
                        savings = (current_cost["input_per_1k"] - alt_cost["input_per_1k"]) * monthly_tokens / 1000
                        if savings > 0:
                            findings.append({
                                "current_model": model_id,
                                "suggested_model": alt,
                                "estimated_monthly_savings": round(savings, 2),
                                "affected_user_count": stats.get("user_count", 1),
                                "usage_pattern_summary": f"Consider switching from {model_id.split('.')[-1]} to {alt.split('.')[-1]} for cost savings of ${round(savings, 2)}/mo",
                                "severity": "high" if savings > 50 else "medium",
                            })
                return findings

            def handle_invocation(payload):
                data = payload.get("data", {})
                usage_data = data.get("usage_data", {})
                if not usage_data:
                    return {"result": "No usage data provided", "findings": [], "finding_count": 0}
                findings = analyze_usage(usage_data)
                if findings:
                    total_savings = sum(f["estimated_monthly_savings"] for f in findings)
                    result = f"Cost optimization analysis complete. {len(findings)} recommendation(s), potential savings: ${total_savings:.2f}/mo"
                else:
                    result = "Cost optimization analysis complete. No cost-saving opportunities found."
                return {"result": result, "findings": findings, "finding_count": len(findings)}
        '''),
    },
    "prompt-quality-analyzer": {
        "description": "Scans prompts for anti-patterns like vague instructions",
        "code": textwrap.dedent('''
            import re
            VAGUE_INDICATORS = [
                (re.compile(r"\\b(do something|help me|fix it|make it work)\\b", re.I), "vague_instruction"),
                (re.compile(r"\\b(etc|and so on|stuff like that)\\b", re.I), "incomplete_context"),
                (re.compile(r"^.{0,20}$"), "too_short"),
            ]

            def scan_prompts(prompts):
                findings = []
                for p in prompts:
                    text = p.get("prompt_text", p.get("text", ""))
                    user = p.get("user_identity", "unknown")
                    for pattern, anti_type in VAGUE_INDICATORS:
                        if pattern.search(text):
                            findings.append({
                                "user_identity": user,
                                "anti_pattern_type": anti_type,
                                "prompt_excerpt": text[:200],
                                "improvement_suggestion": f"Detected '{anti_type}' pattern. Be more specific in your prompts.",
                                "quality_score": 40,
                                "severity": "low",
                            })
                return findings

            def handle_invocation(payload):
                data = payload.get("data", {})
                prompts = data.get("prompts", [])
                if not prompts:
                    return {"result": "No prompts to analyze", "findings": [], "finding_count": 0}
                findings = scan_prompts(prompts)
                result = f"Prompt quality scan complete. {len(findings)} anti-pattern(s) found across {len(prompts)} prompts."
                return {"result": result, "findings": findings, "finding_count": len(findings)}
        '''),
    },
    "model-adoption-tracker": {
        "description": "Lists available Bedrock models and compares capabilities",
        "code": textwrap.dedent('''
            MODEL_METADATA = {
                "us.anthropic.claude-sonnet-4-6": {"family": "Claude", "tier": "premium", "context_window": 200000, "cost_tier": "high"},
                "us.anthropic.claude-haiku-4-5-20251001-v1:0": {"family": "Claude", "tier": "standard", "context_window": 200000, "cost_tier": "low"},
                "us.amazon.nova-pro-v1:0": {"family": "Nova", "tier": "premium", "context_window": 300000, "cost_tier": "medium"},
                "us.amazon.nova-lite-v1:0": {"family": "Nova", "tier": "standard", "context_window": 300000, "cost_tier": "low"},
                "us.amazon.nova-micro-v1:0": {"family": "Nova", "tier": "basic", "context_window": 128000, "cost_tier": "very_low"},
            }

            def compare_models(source, target):
                s = MODEL_METADATA.get(source, {})
                t = MODEL_METADATA.get(target, {})
                return {
                    "source_model": source, "target_model": target,
                    "source_tier": s.get("tier", "unknown"), "target_tier": t.get("tier", "unknown"),
                    "source_cost": s.get("cost_tier", "unknown"), "target_cost": t.get("cost_tier", "unknown"),
                    "recommendation_rationale": f"Compare {source.split('.')[-1]} ({s.get('tier','?')}) vs {target.split('.')[-1]} ({t.get('tier','?')})",
                }

            def handle_invocation(payload):
                data = payload.get("data", {})
                current_models = data.get("current_models", [])
                findings = []
                for model_id in current_models:
                    meta = MODEL_METADATA.get(model_id)
                    if meta and meta["cost_tier"] in ("high", "medium"):
                        for alt_id, alt_meta in MODEL_METADATA.items():
                            if alt_id != model_id and alt_meta["cost_tier"] in ("low", "very_low"):
                                findings.append({
                                    "source_model": model_id, "target_model": alt_id,
                                    "capability_comparison": compare_models(model_id, alt_id),
                                    "recommendation_rationale": f"Consider {alt_id.split('.')[-1]} as a lower-cost alternative",
                                    "severity": "low",
                                })
                result = f"Model adoption analysis complete. {len(findings)} alternative(s) identified."
                return {"result": result, "findings": findings, "finding_count": len(findings)}
        '''),
    },
    "usage-forecaster": {
        "description": "Analyzes historical trends and projects future token consumption",
        "code": textwrap.dedent('''
            def analyze_trends(daily_data):
                if len(daily_data) < 7:
                    return {"trend": "insufficient_data", "avg_daily": 0}
                totals = [d.get("total_tokens", 0) for d in daily_data]
                avg = sum(totals) / len(totals)
                recent_avg = sum(totals[-7:]) / 7
                trend = "increasing" if recent_avg > avg * 1.1 else "decreasing" if recent_avg < avg * 0.9 else "stable"
                return {"trend": trend, "avg_daily": round(avg, 2), "recent_avg": round(recent_avg, 2)}

            def generate_projections(trend_data, days=30):
                avg = trend_data.get("avg_daily", 0)
                multiplier = 1.1 if trend_data.get("trend") == "increasing" else 0.9 if trend_data.get("trend") == "decreasing" else 1.0
                projected = avg * multiplier * days
                return {
                    "forecast_period_days": days,
                    "projected_tokens": int(projected),
                    "projected_cost": round(projected * 0.000003, 2),
                    "confidence_lower": round(projected * 0.8, 2),
                    "confidence_upper": round(projected * 1.2, 2),
                    "methodology": "linear_extrapolation",
                    "severity": "medium" if trend_data.get("trend") == "increasing" else "low",
                }

            def handle_invocation(payload):
                data = payload.get("data", {})
                daily_data = data.get("daily_data", [])
                if not daily_data:
                    return {"result": "No historical data provided", "findings": [], "finding_count": 0}
                trends = analyze_trends(daily_data)
                findings = [generate_projections(trends, d) for d in [30, 60, 90]]
                result = f"Usage forecast complete. Trend: {trends['trend']}. Avg daily tokens: {trends['avg_daily']:,.0f}"
                return {"result": result, "findings": findings, "finding_count": len(findings)}
        '''),
    },
    "data-residency-auditor": {
        "description": "Checks Bedrock invocation regions against approved policies",
        "code": textwrap.dedent('''
            def check_violations(invocations, approved_regions):
                violations = []
                for inv in invocations:
                    region = inv.get("target_region", inv.get("region", ""))
                    if region and region not in approved_regions:
                        violations.append({
                            "user_identity": inv.get("user_identity", "unknown"),
                            "invocation_timestamp": inv.get("timestamp", ""),
                            "target_region": region,
                            "approved_regions": approved_regions,
                            "violation_severity": "critical" if "cn-" in region or "me-" in region else "warning",
                            "severity": "high",
                        })
                return violations

            def handle_invocation(payload):
                data = payload.get("data", {})
                invocations = data.get("invocations", [])
                approved_regions = data.get("approved_regions", ["us-west-2", "us-east-1"])
                if not invocations:
                    return {"result": "No invocations to audit", "findings": [], "finding_count": 0}
                findings = check_violations(invocations, approved_regions)
                result = f"Data residency audit complete. {len(findings)} violation(s) found across {len(invocations)} invocations."
                return {"result": result, "findings": findings, "finding_count": len(findings)}
        '''),
    },
    "latency-error-monitor": {
        "description": "Computes response time percentiles and error rates",
        "code": textwrap.dedent('''
            def compute_metrics(metrics_data):
                findings = []
                for model_id, data in metrics_data.items():
                    latencies = data.get("latencies_ms", [])
                    errors = data.get("error_count", 0)
                    total = data.get("total_count", 1)
                    if latencies:
                        sorted_lat = sorted(latencies)
                        p50 = sorted_lat[len(sorted_lat) // 2]
                        p99 = sorted_lat[int(len(sorted_lat) * 0.99)]
                        if p99 > 5000:
                            findings.append({
                                "metric_type": "latency_spike",
                                "current_value": p99,
                                "baseline_value": p50,
                                "affected_model": model_id,
                                "affected_region": data.get("region", "us-west-2"),
                                "severity": "high" if p99 > 10000 else "medium",
                            })
                    error_rate = errors / total if total > 0 else 0
                    if error_rate > 0.05:
                        findings.append({
                            "metric_type": "error_rate",
                            "current_value": round(error_rate, 4),
                            "baseline_value": 0.01,
                            "affected_model": model_id,
                            "affected_region": data.get("region", "us-west-2"),
                            "severity": "critical" if error_rate > 0.1 else "high",
                        })
                return findings

            def handle_invocation(payload):
                data = payload.get("data", {})
                metrics_data = data.get("metrics_data", {})
                if not metrics_data:
                    return {"result": "No metrics data provided", "findings": [], "finding_count": 0}
                findings = compute_metrics(metrics_data)
                result = f"Latency/error monitoring complete. {len(findings)} issue(s) detected."
                return {"result": result, "findings": findings, "finding_count": len(findings)}
        '''),
    },
    "chargeback-report-generator": {
        "description": "Calculates Bedrock costs per team for a given month",
        "code": textwrap.dedent('''
            def compute_team_costs(cost_records):
                teams = {}
                for record in cost_records:
                    team = record.get("team_name", "unassigned")
                    cost = record.get("cost", 0)
                    teams.setdefault(team, {"total_cost": 0, "invocation_count": 0, "models": set()})
                    teams[team]["total_cost"] += cost
                    teams[team]["invocation_count"] += 1
                    teams[team]["models"].add(record.get("model_id", "unknown"))
                findings = []
                for team_name, data in teams.items():
                    findings.append({
                        "team_name": team_name,
                        "total_cost": round(data["total_cost"], 4),
                        "cost_breakdown": {"invocation_count": data["invocation_count"], "models_used": list(data["models"])},
                        "severity": "medium" if data["total_cost"] > 100 else "low",
                    })
                return findings

            def handle_invocation(payload):
                data = payload.get("data", {})
                cost_records = data.get("cost_records", [])
                month = data.get("month", "unknown")
                if not cost_records:
                    return {"result": "No cost records provided", "findings": [], "finding_count": 0}
                findings = compute_team_costs(cost_records)
                total = sum(f["total_cost"] for f in findings)
                result = f"Chargeback report for {month}: {len(findings)} team(s), total cost ${total:.2f}"
                return {"result": result, "findings": findings, "finding_count": len(findings)}
        '''),
    },
    "onboarding-assistant": {
        "description": "Detects new Bedrock users and generates welcome messages",
        "code": textwrap.dedent('''
            def detect_and_welcome(user_data):
                findings = []
                for user in user_data:
                    user_id = user.get("user_identity", "unknown")
                    is_new = user.get("is_new", False)
                    if is_new:
                        team = user.get("team_assignment", "General")
                        findings.append({
                            "user_identity": user_id,
                            "team_assignment": team,
                            "guidance_content": f"Welcome to Bedrock! You are assigned to the {team} team. Start by exploring available models in the dashboard.",
                            "severity": "low",
                        })
                return findings

            def handle_invocation(payload):
                data = payload.get("data", {})
                user_data = data.get("user_data", [])
                if not user_data:
                    return {"result": "No user data provided", "findings": [], "finding_count": 0}
                findings = detect_and_welcome(user_data)
                result = f"Onboarding scan complete. {len(findings)} new user(s) detected."
                return {"result": result, "findings": findings, "finding_count": len(findings)}
        '''),
    },
    "policy-compliance-checker": {
        "description": "Evaluates Bedrock invocations against organizational policies",
        "code": textwrap.dedent('''
            def evaluate_compliance(invocations, policies):
                violations = []
                for inv in invocations:
                    model = inv.get("model_id", "")
                    user = inv.get("user_identity", "unknown")
                    for policy in policies:
                        if policy.get("policy_type") == "model_restriction":
                            blocked = policy.get("policy_rules", {}).get("blocked_models", [])
                            if model in blocked:
                                violations.append({
                                    "policy_rule_id": policy.get("id", "unknown"),
                                    "user_identity": user,
                                    "invocation_details": {"model_id": model, "timestamp": inv.get("timestamp", "")},
                                    "violation_severity": "critical",
                                    "severity": "high",
                                })
                        elif policy.get("policy_type") == "token_limit":
                            limit = policy.get("policy_rules", {}).get("max_tokens", 100000)
                            tokens = inv.get("total_tokens", 0)
                            if tokens > limit:
                                violations.append({
                                    "policy_rule_id": policy.get("id", "unknown"),
                                    "user_identity": user,
                                    "invocation_details": {"tokens_used": tokens, "limit": limit},
                                    "violation_severity": "warning",
                                    "severity": "medium",
                                })
                return violations

            def handle_invocation(payload):
                data = payload.get("data", {})
                invocations = data.get("invocations", [])
                policies = data.get("policies", [])
                if not invocations or not policies:
                    return {"result": "No invocations or policies to check", "findings": [], "finding_count": 0}
                findings = evaluate_compliance(invocations, policies)
                result = f"Policy compliance check complete. {len(findings)} violation(s) found."
                return {"result": result, "findings": findings, "finding_count": len(findings)}
        '''),
    },
    "weekly-digest-orchestrator": {
        "description": "Collects findings from all agents and compiles a weekly digest",
        "code": textwrap.dedent('''
            def compile_digest(agent_summaries):
                total_findings = sum(s.get("finding_count", 0) for s in agent_summaries)
                critical = sum(1 for s in agent_summaries for f in s.get("findings", []) if f.get("severity") == "critical")
                digest = {
                    "total_findings": total_findings,
                    "critical_count": critical,
                    "agent_summaries": agent_summaries,
                    "delivery_status": "compiled",
                }
                return digest

            def handle_invocation(payload):
                data = payload.get("data", {})
                agent_summaries = data.get("agent_summaries", [])
                if not agent_summaries:
                    return {"result": "No agent summaries to compile", "findings": [], "finding_count": 0}
                digest = compile_digest(agent_summaries)
                findings = [{
                    "digest_period_start": data.get("period_start", ""),
                    "digest_period_end": data.get("period_end", ""),
                    "included_agent_summaries": digest["agent_summaries"],
                    "delivery_status": "compiled",
                    "severity": "high" if digest["critical_count"] > 0 else "low",
                }]
                result = f"Weekly digest compiled. {digest['total_findings']} total findings, {digest['critical_count']} critical."
                return {"result": result, "findings": findings, "finding_count": len(findings)}
        '''),
    },
}

HTTP_SERVER_TEMPLATE = '''"""{{description}} — deployed to Bedrock AgentCore Runtime.

Lightweight HTTP server pattern for fast cold start (<30s).
"""

import json
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any

{{agent_code}}

class AgentHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path == "/invocations":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(body)
            except json.JSONDecodeError:
                payload = {"prompt": body.decode("utf-8", errors="replace")}
            result = handle_invocation(payload)
            resp = json.dumps(result).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp)))
            self.end_headers()
            self.wfile.write(resp)
        else:
            self.send_error(404)

    def do_GET(self):
        if self.path == "/ping":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b\'{"status":"healthy"}\')
        else:
            self.send_error(404)

    def log_message(self, format, *args):
        pass

if __name__ == "__main__":
    # AgentCore Runtime routes traffic into the container on 8080; binding
    # to 0.0.0.0 is required for the control plane to reach the handler.
    # Network isolation is enforced by the container's security group, not
    # the bind address.
    server = HTTPServer(("0.0.0.0", 8080), AgentHandler)
    print("Agent listening on port 8080")
    server.serve_forever()
'''

def main():
    for agent_name, config in AGENTS.items():
        agent_dir = os.path.join("agentcore-agents", agent_name)
        main_py_path = os.path.join(agent_dir, "main_lightweight.py")
        
        code = HTTP_SERVER_TEMPLATE.replace("{{description}}", config["description"])
        code = code.replace("{{agent_code}}", config["code"].strip())
        
        with open(main_py_path, "w") as f:
            f.write(code)
        
        print(f"Created: {main_py_path}")

if __name__ == "__main__":
    main()
