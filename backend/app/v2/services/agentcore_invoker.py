"""AgentCore Invoker Service for the V2 Agent Platform.

Dual invocation path: tries InvokeAgentRuntime (for deployed agents) first,
falls back to invoke_inline_agent (for agents without AgentCore deployment).
"""

import json
import logging
import uuid
from typing import Any

import boto3
from botocore.exceptions import ClientError, EndpointConnectionError, ConnectTimeoutError, ReadTimeoutError
from sqlalchemy.orm import Session

from app.config import settings

logger = logging.getLogger(__name__)


class AgentUnavailableError(Exception):
    """Raised when an agent invocation fails."""

    def __init__(self, agent_name: str, message: str) -> None:
        self.agent_name = agent_name
        self.message = message
        super().__init__(f"Agent '{agent_name}' unavailable: {message}")


# ---------------------------------------------------------------------------
# Agent definitions: system prompts + tool schemas for invoke_inline_agent
# ---------------------------------------------------------------------------

AGENT_DEFINITIONS: dict[str, dict[str, Any]] = {
    # NOTE: The ``pii-detector`` entry was removed when the PII Detector
    # agent was retired from the platform. Do not re-add it without also
    # restoring the seed row, direct-execution branch, finding model map,
    # frontend tile, and docs — see commit history for the removal.
    "cost-anomaly-alerter": {
        "instruction": (
            "You are a cost anomaly detection agent. Analyze daily Bedrock "
            "spend data and flag any days where spend exceeds 2x the rolling "
            "7-day average. Use detect_anomalies to find spending spikes."
        ),
        "tools": [
            {
                "name": "detect_anomalies",
                "description": "Compare current daily spend against rolling average and return anomalies",
                "parameters": {
                    "threshold_multiplier": {"type": "number", "description": "Multiplier for anomaly threshold (default 2.0)", "required": False}
                },
            }
        ],
    },
    "guardrail-recommender": {
        "instruction": "You are a guardrail recommendation agent. Read PII findings and generate Bedrock Guardrail configuration suggestions.",
        "tools": [
            {"name": "read_pii_findings", "description": "Query active PII findings from the database", "parameters": {}},
            {"name": "generate_guardrail_config", "description": "Generate a Bedrock Guardrail config based on PII types", "parameters": {"pii_types": {"type": "array", "description": "List of PII types to protect against", "required": True}}},
        ],
    },
    "model-cost-optimizer": {
        "instruction": (
            "You are a model cost optimization agent. Analyze usage patterns to find expensive "
            "models used for simple tasks and recommend cheaper alternatives. Also flag models "
            "that look like good candidates for model distillation — narrow, repetitive, "
            "high-volume tasks driven by a small number of callers — since distilling a smaller "
            "student model can cut cost by up to ~75% with minimal accuracy loss."
        ),
        "tools": [
            {"name": "analyze_usage_patterns", "description": "Analyze recent Bedrock usage to find expensive models handling simple tasks, flagging model-switch and distillation-candidate recommendations", "parameters": {}},
            {"name": "calculate_savings", "description": "Calculate estimated savings from switching models", "parameters": {"current_model": {"type": "string", "required": True}, "suggested_model": {"type": "string", "required": True}}},
        ],
    },
    "prompt-quality-analyzer": {
        "instruction": "You are a prompt quality analysis agent. Scan prompts for anti-patterns like vague instructions, missing context, and overly long system prompts.",
        "tools": [
            {"name": "scan_for_anti_patterns", "description": "Scan prompts for quality anti-patterns", "parameters": {"prompts": {"type": "array", "description": "List of prompt dicts to analyze", "required": True}}},
        ],
    },
    "model-adoption-tracker": {
        "instruction": "You are a model adoption tracking agent. List available Bedrock models and compare capabilities and costs.",
        "tools": [
            {"name": "query_available_models", "description": "List Bedrock foundation models available in the region", "parameters": {}},
            {"name": "compare_models", "description": "Compare two models on capabilities and cost", "parameters": {"source": {"type": "string", "required": True}, "target": {"type": "string", "required": True}}},
        ],
    },
    "usage-forecaster": {
        "instruction": "You are a usage forecasting agent. Analyze historical trends and project future token consumption and costs.",
        "tools": [
            {"name": "analyze_historical_trends", "description": "Analyze historical usage over trailing days", "parameters": {"days": {"type": "integer", "description": "Number of trailing days (default 90)", "required": False}}},
            {"name": "generate_projections", "description": "Generate 30/60/90-day forecasts with confidence intervals", "parameters": {}},
        ],
    },
    "data-residency-auditor": {
        "instruction": "You are a data residency auditing agent. Check Bedrock invocation regions against approved policies.",
        "tools": [
            {"name": "check_invocation_regions", "description": "Query recent invocations and extract their target regions", "parameters": {}},
            {"name": "flag_violations", "description": "Compare invocation regions against approved-regions policy", "parameters": {"invocations": {"type": "array", "required": True}, "policy": {"type": "object", "required": True}}},
        ],
    },
    "latency-error-monitor": {
        "instruction": "You are a latency and error monitoring agent. Compute response time percentiles and error rates for Bedrock invocations.",
        "tools": [
            {"name": "compute_latency_metrics", "description": "Compute latency percentiles per model and region", "parameters": {}},
            {"name": "compute_error_rates", "description": "Compute error rates per model and region", "parameters": {}},
        ],
    },
    "chargeback-report-generator": {
        "instruction": "You are a chargeback report generation agent. Calculate Bedrock costs per team for a given month.",
        "tools": [
            {"name": "compute_team_costs", "description": "Compute total costs per team for a month", "parameters": {"month": {"type": "string", "description": "Month in YYYY-MM format", "required": True}}},
        ],
    },
    "onboarding-assistant": {
        "instruction": "You are an onboarding assistant agent. Detect new Bedrock users and generate personalized welcome messages.",
        "tools": [
            {"name": "detect_new_users", "description": "Detect users who haven't been onboarded yet", "parameters": {}},
            {"name": "generate_welcome_message", "description": "Generate a personalized onboarding message", "parameters": {"user_identity": {"type": "string", "required": True}}},
        ],
    },
    "policy-compliance-checker": {
        "instruction": "You are a policy compliance checking agent. Evaluate Bedrock invocations against organizational policies.",
        "tools": [
            {"name": "evaluate_invocations", "description": "Evaluate recent invocations against policies", "parameters": {"policies": {"type": "array", "description": "List of policy dicts", "required": True}}},
        ],
    },
    "weekly-digest-orchestrator": {
        "instruction": "You are the weekly digest orchestrator. Collect findings from all agents and compile a weekly digest.",
        "tools": [
            {"name": "collect_agent_findings", "description": "Collect findings from all agents over past N days", "parameters": {"days": {"type": "integer", "required": False}}},
            {"name": "compile_digest", "description": "Compile findings into a structured digest", "parameters": {"findings": {"type": "object", "required": True}}},
            {"name": "send_digest", "description": "Send the compiled digest via SNS", "parameters": {"digest": {"type": "object", "required": True}}},
        ],
    },
    "chargeback-reconciliation": {
        "instruction": (
            "You are a chargeback reconciliation agent. Compare actual AWS Bedrock billing "
            "from Cost Explorer against per-user token usage estimates from CloudWatch Logs. "
            "Allocate actual costs proportionally, identify variances, and provide insights. "
            "Note: This is a best-effort reconciliation — CloudWatch Logs may not capture all "
            "billing items (e.g., provisioned throughput, batch inference)."
        ),
        "tools": [
            {"name": "compare_billing_vs_usage", "description": "Compare actual billing line items against per-user estimated costs", "parameters": {"billing_data": {"type": "object", "required": True}, "usage_data": {"type": "object", "required": True}}},
            {"name": "allocate_costs", "description": "Perform proportional allocation and generate per-user breakdown", "parameters": {"user_estimated": {"type": "object", "required": True}, "total_actual_cost": {"type": "number", "required": True}}},
            {"name": "generate_insights", "description": "Produce natural-language analysis explaining variances", "parameters": {"billing_data": {"type": "object", "required": True}, "usage_data": {"type": "object", "required": True}}},
        ],
    },
}


# ---------------------------------------------------------------------------
# Tool executor: maps tool names to actual Python functions
# ---------------------------------------------------------------------------


def _coerce_tool_args(fn_args: dict[str, Any], param_types: dict[str, str]) -> dict[str, Any]:
    """Coerce RETURN_CONTROL argument values back to their declared Python type.

    Bedrock's RETURN_CONTROL always hands back every function parameter value
    as a plain string (see ``FunctionParameter.value``: type string regardless
    of the schema's declared ``type``), so an "integer" param like
    ``days: int`` arrives as the string ``"90"`` and a dict-shaped "object"
    param (downgraded to "string" in the schema — see the comment where
    ``tool_param_types`` is built) arrives as a JSON-encoded string. Without
    this, calling the real tool function with these raw strings raises at
    the first type-sensitive operation (e.g. ``timedelta(days="90")``).
    Only coerces params present in ``fn_args`` and ``param_types``; unknown
    params or ones that already arrived as non-string values (e.g. from a
    non-Bedrock caller) pass through unchanged. A value that fails to coerce
    is left as-is so the underlying tool's own error surfaces instead of a
    confusing coercion failure.
    """
    coerced = dict(fn_args)
    for name, value in fn_args.items():
        if not isinstance(value, str):
            continue
        declared = param_types.get(name)
        if declared is None:
            continue
        try:
            if declared == "integer":
                coerced[name] = int(value)
            elif declared == "number":
                coerced[name] = float(value)
            elif declared == "boolean":
                coerced[name] = value.strip().lower() in ("true", "1", "yes")
            elif declared in ("array", "object"):
                coerced[name] = json.loads(value)
        except (ValueError, TypeError, json.JSONDecodeError):
            logger.warning(
                "Failed to coerce tool arg %s=%r to declared type %s; passing through",
                name, value, declared,
            )
    return coerced


def _get_tool_executor(agent_name: str) -> dict[str, Any]:
    """Import and return the tool functions for a given agent."""
    executors: dict[str, Any] = {}
    try:
        # ``pii-detector`` branch removed: agent retired from the platform.
        if agent_name == "cost-anomaly-alerter":
            from app.v2.agents.cost_anomaly_alerter import detect_anomalies
            executors["detect_anomalies"] = detect_anomalies
        elif agent_name == "guardrail-recommender":
            from app.v2.agents.guardrail_recommender import read_pii_findings, generate_guardrail_config
            executors["read_pii_findings"] = read_pii_findings
            executors["generate_guardrail_config"] = generate_guardrail_config
        elif agent_name == "model-cost-optimizer":
            from app.v2.agents.model_cost_optimizer import analyze_usage_patterns, calculate_savings
            executors["analyze_usage_patterns"] = analyze_usage_patterns
            executors["calculate_savings"] = calculate_savings
        elif agent_name == "prompt-quality-analyzer":
            from app.v2.agents.prompt_quality_analyzer import scan_for_anti_patterns
            executors["scan_for_anti_patterns"] = scan_for_anti_patterns
        elif agent_name == "model-adoption-tracker":
            from app.v2.agents.model_adoption_tracker import query_available_models, compare_models
            executors["query_available_models"] = query_available_models
            executors["compare_models"] = compare_models
        elif agent_name == "usage-forecaster":
            from app.v2.agents.usage_forecaster import analyze_historical_trends, generate_projections
            executors["analyze_historical_trends"] = analyze_historical_trends
            executors["generate_projections"] = generate_projections
        elif agent_name == "data-residency-auditor":
            from app.v2.agents.data_residency_auditor import check_invocation_regions, flag_violations
            executors["check_invocation_regions"] = check_invocation_regions
            executors["flag_violations"] = flag_violations
        elif agent_name == "latency-error-monitor":
            from app.v2.agents.latency_error_monitor import compute_latency_metrics, compute_error_rates
            executors["compute_latency_metrics"] = compute_latency_metrics
            executors["compute_error_rates"] = compute_error_rates
        elif agent_name == "chargeback-report-generator":
            from app.v2.agents.chargeback_report_generator import compute_team_costs
            executors["compute_team_costs"] = compute_team_costs
        elif agent_name == "onboarding-assistant":
            from app.v2.agents.onboarding_assistant import detect_new_users, generate_welcome_message
            executors["detect_new_users"] = detect_new_users
            executors["generate_welcome_message"] = generate_welcome_message
        elif agent_name == "policy-compliance-checker":
            from app.v2.agents.policy_compliance_checker import evaluate_invocations
            executors["evaluate_invocations"] = evaluate_invocations
        elif agent_name == "weekly-digest-orchestrator":
            from app.v2.agents.weekly_digest_orchestrator import collect_agent_findings, compile_digest, send_digest
            executors["collect_agent_findings"] = collect_agent_findings
            executors["compile_digest"] = compile_digest
            executors["send_digest"] = send_digest
        elif agent_name == "chargeback-reconciliation":
            from app.v2.agents.chargeback_reconciliation_agent import compare_billing_vs_usage, allocate_costs, generate_insights
            executors["compare_billing_vs_usage"] = compare_billing_vs_usage
            executors["allocate_costs"] = allocate_costs
            executors["generate_insights"] = generate_insights
    except ImportError as exc:
        logger.warning("Failed to import tools for agent %s: %s", agent_name, exc)
    return executors


# ---------------------------------------------------------------------------
# AgentCoreInvoker class
# ---------------------------------------------------------------------------

class AgentCoreInvoker:
    """Invokes agents using Bedrock invoke_inline_agent API.

    The LLM reasons about which tools to call, and we execute the tool
    functions locally, returning results back to the LLM for final response.
    """

    def __init__(self) -> None:
        region = settings.bedrock_region or settings.aws_region
        self._region = region
        self._model_id = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
        # Boto3 clients and the resolved account id are created LAZILY on first
        # use (see the properties below). Deferring creation keeps importing this
        # module — and any router that instantiates the invoker at import time —
        # resilient: a missing service model or absent credentials can never
        # break application boot, only the first real invocation.
        self.__client: Any = None
        self.__inline_client: Any = None
        self.__account_id: str | None = None

    @property
    def _client(self) -> Any:
        """Lazily created ``bedrock-agentcore`` (AgentCore Runtime) client."""
        if self.__client is None:
            self.__client = boto3.client("bedrock-agentcore", region_name=self._region)
        return self.__client

    @property
    def _inline_client(self) -> Any:
        """Lazily created ``bedrock-agent-runtime`` (inline agent) client."""
        if self.__inline_client is None:
            self.__inline_client = boto3.client(
                "bedrock-agent-runtime", region_name=self._region
            )
        return self.__inline_client

    @property
    def _account_id(self) -> str:
        """Resolve the caller's AWS account id lazily for ARN construction."""
        if self.__account_id is None:
            try:
                sts = boto3.client("sts", region_name=self._region)
                self.__account_id = sts.get_caller_identity()["Account"]
            except Exception:
                self.__account_id = "unknown"
        return self.__account_id

    def invoke_agent(
        self,
        agent_name: str,
        payload: dict,
        db: Session | None = None,
        trigger_type: str | None = None,
    ) -> dict:
        """Try InvokeAgentRuntime first, fall back to invoke_inline_agent.
        
        If db is provided and the agent has an agentcore_agent_id, uses the
        deployed AgentCore Runtime path. Otherwise falls back to inline.

        Args:
            trigger_type: How this execution was triggered (e.g. "manual",
                "scheduled"). NOT attached as Bedrock ``requestMetadata`` —
                that parameter does not exist on InvokeInlineAgent (only on
                Converse/InvokeModel; see ``_invoke_inline`` for the full
                explanation of why this was removed). Kept for logging/
                diagnostics (there is no per-end-user identity at this call
                site — agent execution is not tied to a specific
                logged-in user's request).
        """
        agent_id = self._get_agent_id(agent_name, db) if db else None

        if agent_id:
            try:
                result = self._invoke_runtime(agent_id, agent_name, payload)
                result["fallback_used"] = False
                return result
            except (ClientError, EndpointConnectionError, ConnectTimeoutError, ReadTimeoutError) as exc:
                logger.warning("AgentCore Runtime fallback for %s: %s", agent_name, exc)
                result = self._invoke_inline(agent_name, payload, trigger_type=trigger_type)
                result["fallback_used"] = True
                return result

        # No agent ID — use inline directly
        result = self._invoke_inline(agent_name, payload, trigger_type=trigger_type)
        result["fallback_used"] = True
        return result

    def _get_agent_id(self, agent_name: str, db: Session | None) -> str | None:
        """Read agentcore_agent_id from v2_agent_configs."""
        if db is None:
            return None
        from app.v2.models import V2AgentConfig
        config = db.query(V2AgentConfig).filter(
            V2AgentConfig.agent_name == agent_name
        ).first()
        if config and config.agentcore_agent_id:
            return config.agentcore_agent_id
        return None

    def _invoke_runtime(self, agent_id: str, agent_name: str, payload: dict) -> dict:
        """Invoke a deployed agent via InvokeAgentRuntime API.
        
        agent_id is the full ARN or runtime ID stored in v2_agent_configs.agentcore_agent_id.
        """
        session_id = str(uuid.uuid4()) + "-" + str(uuid.uuid4())[:4]
        input_text = payload.get("prompt", f"Execute your analysis for {agent_name}")

        # Build the ARN if only an ID was provided
        if agent_id.startswith("arn:"):
            arn = agent_id
        else:
            arn = f"arn:aws:bedrock-agentcore:{self._region}:{self._account_id}:runtime/{agent_id}"

        response = self._client.invoke_agent_runtime(
            agentRuntimeArn=arn,
            runtimeSessionId=session_id,
            payload=json.dumps({"prompt": input_text, **{k: v for k, v in payload.items() if k != "prompt"}}).encode(),
        )

        # Read the streaming response body
        resp_body = response.get("response") or response.get("body")
        completion = ""
        if resp_body:
            completion = resp_body.read().decode("utf-8")

        # Try to parse structured response
        findings = []
        try:
            parsed = json.loads(completion)
            if isinstance(parsed, dict):
                findings = parsed.get("findings", [])
                completion = parsed.get("result", completion)
        except (json.JSONDecodeError, TypeError):
            pass

        return {
            "agent": agent_name,
            "response": completion,
            "findings": findings,
            "finding_count": len(findings),
        }

    def _invoke_inline(
        self, agent_name: str, payload: dict, trigger_type: str | None = None
    ) -> dict:
        """Invoke an agent using invoke_inline_agent with RETURN_CONTROL tools."""
        defn = AGENT_DEFINITIONS.get(agent_name)
        if not defn:
            raise AgentUnavailableError(agent_name, f"No definition found for agent '{agent_name}'")

        tool_executors = _get_tool_executor(agent_name)
        session_id = str(uuid.uuid4())
        input_text = payload.get("prompt", f"Execute your analysis for {agent_name}")

        # NOTE: `requestMetadata` is NOT a valid parameter of InvokeInlineAgent
        # (it only exists on the Converse/InvokeModel runtime APIs — see
        # AgentService._build_request_metadata for the call site where it IS
        # valid). A prior change passed it here anyway, which made every
        # invoke_inline_agent call fail with "Unknown parameter in input:
        # 'requestMetadata'" (botocore ParamValidationError), breaking every
        # agentic V2 agent that falls back to this inline path. There is no
        # supported way to tag an inline-agent invocation's own log entry
        # with agent/trigger metadata today, so this attribution is dropped;
        # the invocation falls back to being attributed by the shared backend
        # IAM credential like any other unattributed call (see
        # cloudtrail_service.py's requestMetadata-preferring parser, which
        # degrades gracefully when the field is absent). ``trigger_type`` is
        # still accepted as a parameter (used by callers for logging) but no
        # longer threaded into the Bedrock request itself.

        # Build action groups with RETURN_CONTROL.
        #
        # Bedrock's InvokeInlineAgent functionSchema only accepts parameter
        # types boolean/number/string/array/integer — "object" is rejected
        # outright with a ValidationException before the call even starts
        # (not a tool-execution-time error, so there is no way to recover
        # from it once sent). Several tool defs here declare "object" for a
        # dict-shaped argument (e.g. flag_violations' `policy`,
        # compare_billing_vs_usage's `billing_data`/`usage_data`) — downgrade
        # those to "string" for the schema Bedrock sees, while remembering
        # the ORIGINAL declared type per tool so the real argument value can
        # be coerced back to the right Python type after RETURN_CONTROL hands
        # it back (see _coerce_tool_args below).
        tool_param_types: dict[str, dict[str, str]] = {}
        action_groups = []
        for tool_def in defn.get("tools", []):
            params = {}
            param_types: dict[str, str] = {}
            for pname, pdef in tool_def.get("parameters", {}).items():
                declared_type = pdef.get("type", "string")
                param_types[pname] = declared_type
                params[pname] = {
                    "description": pdef.get("description", pname),
                    "required": pdef.get("required", False),
                    "type": declared_type if declared_type != "object" else "string",
                }
            tool_param_types[tool_def["name"]] = param_types
            action_groups.append({
                "actionGroupName": tool_def["name"],
                "actionGroupExecutor": {"customControl": "RETURN_CONTROL"},
                "functionSchema": {
                    "functions": [{
                        "name": tool_def["name"],
                        "description": tool_def["description"],
                        "parameters": params,
                        "requireConfirmation": "DISABLED",
                    }]
                },
            })

        try:
            # Initial invocation
            response = self._inline_client.invoke_inline_agent(
                sessionId=session_id,
                inputText=input_text,
                foundationModel=self._model_id,
                instruction=defn["instruction"],
                actionGroups=action_groups,
                enableTrace=False,
            )

            # Process the response stream — handle RETURN_CONTROL tool calls
            all_findings = []
            final_text = ""
            max_iterations = 5

            for iteration in range(max_iterations):
                final_text, tool_calls = self._process_stream(response)

                if not tool_calls:
                    break  # No more tool calls, we're done

                # Execute tool calls locally
                tool_results = []
                for tc in tool_calls:
                    fn_name = tc["function"]
                    fn_args = tc.get("parameters", {})
                    executor = tool_executors.get(fn_name)

                    if executor:
                        try:
                            fn_args = _coerce_tool_args(fn_args, tool_param_types.get(fn_name, {}))
                            result = executor(**fn_args) if fn_args else executor()
                            if isinstance(result, list):
                                all_findings.extend(result)
                            tool_results.append({
                                "actionGroupName": fn_name,
                                "functionName": fn_name,
                                "responseBody": {"TEXT": {"body": json.dumps(result, default=str)}},
                            })
                        except Exception as exc:
                            logger.warning("Tool %s failed: %s", fn_name, exc)
                            tool_results.append({
                                "actionGroupName": fn_name,
                                "functionName": fn_name,
                                "responseBody": {"TEXT": {"body": json.dumps({"error": str(exc)})}},
                            })
                    else:
                        tool_results.append({
                            "actionGroupName": fn_name,
                            "functionName": fn_name,
                            "responseBody": {"TEXT": {"body": json.dumps({"error": f"Tool {fn_name} not found"})}},
                        })

                # Send tool results back to the agent
                response = self._inline_client.invoke_inline_agent(
                    sessionId=session_id,
                    inputText="Process the tool results and provide your analysis.",
                    foundationModel=self._model_id,
                    instruction=defn["instruction"],
                    actionGroups=action_groups,
                    enableTrace=False,
                    inlineSessionState={
                        "returnControlInvocationResults": [
                            {
                                "functionResult": {
                                    "actionGroup": tr["actionGroupName"],
                                    "function": tr["functionName"],
                                    "responseBody": tr["responseBody"],
                                }
                            }
                            for tr in tool_results
                        ]
                    },
                )

            return {
                "agent": agent_name,
                "response": final_text,
                "findings": all_findings,
                "finding_count": len(all_findings),
            }

        except (ClientError, EndpointConnectionError, ConnectTimeoutError) as exc:
            logger.error("Agent invocation failed for %s: %s", agent_name, exc)
            raise AgentUnavailableError(agent_name, str(exc)) from exc
        except Exception as exc:
            logger.error("Unexpected error invoking agent %s: %s", agent_name, exc)
            raise AgentUnavailableError(agent_name, str(exc)) from exc

    def _process_stream(self, response: dict) -> tuple[str, list[dict]]:
        """Process the invoke_inline_agent response stream.

        Returns (final_text, tool_calls) where tool_calls is a list of
        {function, parameters, invocationId} dicts if RETURN_CONTROL.
        """
        text = ""
        tool_calls = []

        completion = response.get("completion", [])
        for event in completion:
            chunk = event.get("chunk", {})
            if "bytes" in chunk:
                text += chunk["bytes"].decode("utf-8")

            # Check for returnControl events
            return_control = event.get("returnControl", {})
            invocation_inputs = return_control.get("invocationInputs", [])
            for inv_input in invocation_inputs:
                fn_inv = inv_input.get("functionInvocationInput", {})
                if fn_inv:
                    params = {}
                    for p in fn_inv.get("parameters", []):
                        params[p["name"]] = p.get("value", "")
                    tool_calls.append({
                        "function": fn_inv.get("function", ""),
                        "actionGroup": fn_inv.get("actionGroup", ""),
                        "parameters": params,
                    })

        return text, tool_calls

    def check_health(self) -> bool:
        """Check if Bedrock Agent Runtime is available."""
        try:
            self._inline_client.meta.service_model
            return True
        except Exception as exc:
            logger.warning("Health check failed: %s", exc)
            return False

    @staticmethod
    def build_error_response(agent_name: str, message: str) -> dict:
        """Return the structured error payload for agent unavailability."""
        return {
            "error": "agent_unavailable",
            "agent": agent_name,
            "message": message,
            "v1_status": "operational",
        }
