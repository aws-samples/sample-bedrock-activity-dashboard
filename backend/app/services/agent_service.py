"""Agent service for AI-powered chat using Amazon Bedrock Converse API."""

import concurrent.futures
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from sqlalchemy.orm import Session

from app.config import settings
from app.models import ChatMessage
from app.services import settings_service
from app.services.aggregation_service import AggregationService
from app.services.cloudtrail_service import CloudTrailService
from app.services.crypto_service import get_crypto
from app.services.filter_service import ActivityFilters, FilterService
from app.v2.services.guardrail_creation_service import get_latest_published_guardrail

logger = logging.getLogger(__name__)

# Retry configuration — keep total latency well under ALB's 120s idle timeout
_MAX_RETRIES = 1
_BASE_BACKOFF_SECONDS = 1.0

# Cap how many prior messages are replayed to the model each turn. Full history
# is still persisted in the DB — this only bounds the tokens/latency of the
# prompt (~10 turns of user+assistant).
_MAX_HISTORY_MESSAGES = 20


class BedrockConverseError(Exception):
    """Raised when the Bedrock Converse API fails after retries."""

    def __init__(self, error_code: str, message: str):
        self.error_code = error_code
        self.message = message
        super().__init__(f"{error_code}: {message}")


def _fmt_cost(cost: float | None) -> str:
    """Format a cost value for display in the usage context.

    Sub-cent costs are rendered with 4 decimal places so priced models are not
    misleadingly shown as ``$0.00`` (which reads as "free").

    Args:
        cost: The cost value, or None if no cost data is available.

    Returns:
        - ``"N/A"`` when cost is None.
        - ``"$0.00"`` when cost is exactly 0.
        - ``"$0.xxxx"`` (4 decimals) when 0 < cost < 0.01.
        - ``"$x.xx"`` (2 decimals) when cost >= 0.01.
    """
    if cost is None:
        return "N/A"
    if cost == 0:
        return "$0.00"
    if 0 < cost < 0.01:
        return f"${cost:.4f}"
    return f"${cost:.2f}"


def _supports_extended_thinking(model_id: str) -> bool:
    """Return True only for Anthropic Claude models that support extended thinking.

    Extended (Anthropic-style) thinking/reasoning on Bedrock is only supported by
    certain Claude model families. Everything else (Nova, Llama, Mistral, Titan,
    and older Claude 3 / 3.5) does not support the ``thinking`` request field, and
    sending it causes the request to fail.

    The match is conservative and operates on the id with any regional inference
    prefix (``us.``, ``eu.``, ``ap.``, ``global.``) stripped.

    Args:
        model_id: The Bedrock model id (may include a regional prefix).

    Returns:
        True if the model is a reasoning-capable Anthropic Claude model.
    """
    if not model_id:
        return False

    normalized = model_id.lower()
    for prefix in ("us.", "eu.", "ap.", "global."):
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix):]
            break

    if "anthropic" not in normalized and "claude" not in normalized:
        return False

    reasoning_markers = (
        "claude-3-7",
        "claude-sonnet-4",
        "claude-opus-4",
        "claude-haiku-4",
        "claude-4",
    )
    return any(marker in normalized for marker in reasoning_markers)


def _build_guardrail_config(db: Session) -> dict[str, Any] | None:
    """Return a Converse ``guardrailConfig`` dict for the dashboard's managed
    guardrail, or None when no recommendation has been approved+published yet.

    Scoped enforcement: this only affects THIS dashboard's own Converse calls
    (Agent Chat here; Demo Data separately) — it has no effect on Bedrock
    calls made by other applications/credentials in the account. Best-effort:
    any lookup failure degrades to None (no guardrail attached) rather than
    failing the chat request.
    """
    try:
        latest = get_latest_published_guardrail(db)
    except Exception:
        logger.warning("Guardrail lookup failed — proceeding without a guardrail", exc_info=True)
        return None
    if latest is None:
        return None
    guardrail_id, version = latest
    return {"guardrailIdentifier": guardrail_id, "guardrailVersion": version, "trace": "enabled"}


def _build_request_metadata(
    user_identity: str | None, user_name: str | None, session_id: str
) -> dict[str, str] | None:
    """Build the Bedrock ``requestMetadata`` tag map for a Converse call.

    ``requestMetadata`` is recorded verbatim on the model invocation log
    (CloudWatch/S3) that ``CloudTrailService`` ingests — it's the only place
    in the log record where the REAL end-user identity can appear, since every
    Agent Chat call is made under this backend's one shared IAM/task
    credential (the ``userIdentity``/``identity.arn`` field on the log entry
    is always that shared credential, not the Cognito user who asked).

    Returns None when there's no user identity to attach (e.g. auth disabled)
    so the ``requestMetadata`` kwarg is omitted entirely rather than sent
    with empty/placeholder values.
    """
    if not user_identity:
        return None
    metadata = {"UserIdentity": user_identity, "SessionId": session_id}
    if user_name:
        metadata["UserName"] = user_name
    return metadata


def _build_system_prompt(usage_context: str) -> str:
    """Build the system prompt shared by chat() and chat_stream().

    Combines the assistant intro, the usage context, and explicit guardrails
    that keep the model from conflating metrics, mislabeling sub-cent costs as
    free, or inventing values not present in the context.
    """
    return (
        "You are an AI assistant that helps analyze Amazon Bedrock usage data. "
        "Answer questions based on the following usage context.\n\n"
        f"{usage_context}\n\n"
        "=== Guidelines ===\n"
        "- Base every number strictly on the provided usage context. Never invent "
        "or infer values that are not present.\n"
        "- Do NOT conflate metrics: invocations, tokens, and cost are distinct. "
        "'% of invocations' and '% of tokens' are different measures — cite the "
        "correct one and never relabel one as the other.\n"
        "- When ranking 'most used', state which metric you are ranking by "
        "(invocations, tokens, or cost) and stay consistent within your answer.\n"
        "- A cost shown as '$0.00xx' (sub-cent) or '<$0.01' is NON-ZERO — do not "
        "describe such models as free or zero-cost. Only 'N/A' means no cost data.\n"
        "- Do not use contradictory superlatives (e.g., do not call two different "
        "models each 'the highest').\n"
        "- Include relevant totals for context when useful.\n"
        "Keep answers concise and accurate."
    )


class AgentService:
    """Orchestrates AI Agent chat using Bedrock Converse API with usage context."""

    def __init__(self, cloudtrail_service: CloudTrailService | None = None):
        region = settings.bedrock_region or settings.aws_region
        session_kwargs: dict[str, Any] = {"region_name": region}
        if settings.aws_access_key_id:
            session_kwargs["aws_access_key_id"] = settings.aws_access_key_id
        if settings.aws_secret_access_key:
            session_kwargs["aws_secret_access_key"] = settings.aws_secret_access_key
        if settings.aws_session_token:
            session_kwargs["aws_session_token"] = settings.aws_session_token
        session = boto3.Session(**session_kwargs)
        self.client = session.client(
            "bedrock-runtime",
            config=Config(
                read_timeout=55,        # 1 attempt × 55s + 45s context = 100s, under ALB 120s
                connect_timeout=10,
                retries={"max_attempts": 0},  # we handle retries ourselves
            ),
        )
        self.model_id = settings.bedrock_model_id
        # Reuse a shared CloudTrailService instance to avoid re-creating boto3 clients
        # on every chat request. The activity routes pass their shared instance in.
        self._cloudtrail_service = cloudtrail_service or CloudTrailService()

    def chat(
        self,
        session_id: str | None,
        message: str,
        filters: dict[str, Any],
        db: Session,
        budget_tokens: int = 2048,
        user_identity: str | None = None,
        user_name: str | None = None,
    ) -> dict[str, Any]:
        """Send a user message and get an AI response with usage context.

        Args:
            session_id: Existing conversation session ID, or None to start new.
            message: The user's natural-language question.
            filters: Dashboard filter dict with keys: start_date, end_date,
                     user_identities, team_id, source_application.
            db: SQLAlchemy database session.
            budget_tokens: Max tokens for extended thinking (default 2048).
            user_identity: Authenticated caller's identity (Cognito email or
                username, from ``CurrentUser.identity``). Persisted on the
                ``ChatMessage`` row and attached as Bedrock ``requestMetadata``
                so both the dashboard's own DB and the real Bedrock invocation
                log can attribute this turn to the actual end user rather than
                the shared backend credential. None when auth is disabled.
            user_name: Authenticated caller's display name (``CurrentUser.username``).

        Returns:
            Dict with session_id, message (assistant response), thinking,
            thinkingUnavailable flag, and timestamp.

        Raises:
            BedrockConverseError: If the Converse API fails after retries.
        """
        if not session_id or self._session_owned_by_other(session_id, user_identity, db):
            # Unknown or foreign session id: never attach to another user's chat.
            session_id = str(uuid.uuid4())

        # Build usage context from current dashboard data (with timeout guard)
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(self.build_usage_context, filters, db)
                usage_context = future.result(timeout=45)
        except concurrent.futures.TimeoutError:
            logger.warning("build_usage_context timed out — proceeding without usage data")
            usage_context = "No usage data available (context fetch timed out)."
        except Exception as exc:
            logger.warning("build_usage_context failed: %s — proceeding without usage data", exc)
            usage_context = "No usage data available."

        # Load conversation history from DB (decrypted on read so the LLM
        # never sees ciphertext envelopes from the at-rest encryption layer)
        history = self._load_history(session_id, db, user_identity)
        crypto = get_crypto()

        # Build messages for Converse API
        messages = []
        for msg in history:
            messages.append(
                {"role": msg.role, "content": [{"text": crypto.decrypt(msg.content)}]}
            )
        messages.append({"role": "user", "content": [{"text": message}]})

        # Build system prompt with usage context and guardrails
        system_prompt = _build_system_prompt(usage_context)

        # Apply the dashboard's own managed Bedrock Guardrail (if a Guardrail
        # Recommender finding has been approved+published) — scoped to this
        # Agent Chat call only, not account-wide enforcement.
        guardrail_config = _build_guardrail_config(db)
        request_metadata = _build_request_metadata(user_identity, user_name, session_id)

        thinking_unavailable = False

        if _supports_extended_thinking(self.model_id):
            # Model supports Anthropic-style extended thinking — attempt with it,
            # falling back without on failure (a legitimate "unavailable").
            thinking_fields = {
                "thinking": {"type": "enabled", "budget_tokens": budget_tokens}
            }
            try:
                response = self._converse_with_retry(
                    system_prompt,
                    messages,
                    additional_model_request_fields=thinking_fields,
                    guardrail_config=guardrail_config,
                    request_metadata=request_metadata,
                )
            except (BedrockConverseError, ClientError):
                logger.warning("Converse with thinking failed — retrying without thinking")
                thinking_unavailable = True
                response = self._converse_with_retry(
                    system_prompt,
                    messages,
                    guardrail_config=guardrail_config,
                    request_metadata=request_metadata,
                )
        else:
            # Model does not support extended thinking — reasoning simply isn't
            # applicable, so do NOT flag it as "unavailable".
            response = self._converse_with_retry(
                system_prompt,
                messages,
                guardrail_config=guardrail_config,
                request_metadata=request_metadata,
            )

        # Extract thinking and assistant text from response content blocks
        output = response.get("output", {})
        response_message = output.get("message", {})
        content_blocks = response_message.get("content", [])
        assistant_text, thinking_text = self._extract_thinking_and_text(content_blocks)

        if not assistant_text:
            assistant_text = "I was unable to generate a response. Please try again."

        # stopReason is "guardrail_intervened" when the guardrail blocked or
        # anonymized content in this turn — surface it so the UI can show why
        # the response looks different, rather than silently swapping content.
        guardrail_intervened = response.get("stopReason") == "guardrail_intervened"

        now = datetime.now(timezone.utc)

        # Persist user message and assistant response only if the admin has
        # left the Prompt History toggle on. When it's off the chat still
        # works end-to-end for the current turn (the response was already
        # streamed/returned above) but nothing lands in chat_messages, so
        # subsequent reads from Prompt History / Agent Chat sidebar won't
        # replay the conversation. Content is encrypted at rest when a key
        # is configured; see crypto_service for the envelope.
        if settings_service.get_prompt_history_enabled(db):
            crypto = get_crypto()
            user_msg = ChatMessage(
                session_id=session_id,
                role="user",
                content=crypto.encrypt(message),
                created_at=now,
                user_identity=user_identity,
                user_name=user_name,
            )
            assistant_msg = ChatMessage(
                session_id=session_id,
                role="assistant",
                content=crypto.encrypt(assistant_text),
                created_at=now,
            )
            db.add(user_msg)
            db.add(assistant_msg)
            db.commit()
        else:
            logger.info(
                "Prompt history disabled; skipping chat_messages persistence for session=%s",
                session_id,
            )

        return {
            "sessionId": session_id,
            "message": assistant_text,
            "thinking": thinking_text if thinking_text else None,
            "thinkingUnavailable": thinking_unavailable,
            "guardrailIntervened": guardrail_intervened,
            "timestamp": now.isoformat(),
        }

    def chat_stream(
        self,
        session_id: str | None,
        message: str,
        filters: dict[str, Any],
        db: Session,
        budget_tokens: int = 2048,
        user_identity: str | None = None,
        user_name: str | None = None,
    ):
        """Stream an AI response token-by-token using Bedrock ConverseStream.

        Args:
            user_identity: Authenticated caller's identity — see ``chat()``.
            user_name: Authenticated caller's display name — see ``chat()``.

        Yields SSE-formatted strings: ``data: <text>\\n\\n``
        Emits ``data: [THINKING]<text>\\n\\n`` events for thinking content before
        response text events.
        Sends a final ``data: [DONE]\\n\\n`` event when complete.
        Persists the full response to DB after streaming finishes.
        """
        if not session_id or self._session_owned_by_other(session_id, user_identity, db):
            # Unknown or foreign session id: never attach to another user's chat.
            session_id = str(uuid.uuid4())

        # Build usage context (with timeout guard)
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(self.build_usage_context, filters, db)
                usage_context = future.result(timeout=45)
        except concurrent.futures.TimeoutError:
            logger.warning("build_usage_context timed out — proceeding without usage data")
            usage_context = "No usage data available (context fetch timed out)."
        except Exception as exc:
            logger.warning("build_usage_context failed: %s", exc)
            usage_context = "No usage data available."

        # Decrypt each prior turn on read; see chat() above for details.
        history = self._load_history(session_id, db, user_identity)
        crypto = get_crypto()
        messages = []
        for msg in history:
            messages.append(
                {"role": msg.role, "content": [{"text": crypto.decrypt(msg.content)}]}
            )
        messages.append({"role": "user", "content": [{"text": message}]})

        system_prompt = _build_system_prompt(usage_context)

        # Apply the dashboard's own managed Bedrock Guardrail (if a Guardrail
        # Recommender finding has been approved+published) — scoped to this
        # Agent Chat call only, not account-wide enforcement.
        guardrail_config = _build_guardrail_config(db)
        request_metadata = _build_request_metadata(user_identity, user_name, session_id)
        base_kwargs: dict[str, Any] = {
            "modelId": self.model_id,
            "system": [{"text": system_prompt}],
            "messages": messages,
        }
        if guardrail_config:
            base_kwargs["guardrailConfig"] = guardrail_config
        if request_metadata:
            base_kwargs["requestMetadata"] = request_metadata

        thinking_unavailable = False

        if _supports_extended_thinking(self.model_id):
            # Model supports Anthropic-style extended thinking — attempt with it,
            # falling back without on failure (a legitimate "unavailable").
            thinking_fields = {
                "thinking": {"type": "enabled", "budget_tokens": budget_tokens}
            }
            try:
                response = self.client.converse_stream(
                    **base_kwargs,
                    additionalModelRequestFields=thinking_fields,
                )
            except ClientError:
                logger.warning("ConverseStream with thinking failed — retrying without thinking")
                thinking_unavailable = True
                try:
                    response = self.client.converse_stream(**base_kwargs)
                except ClientError as exc:
                    error_response = exc.response.get("Error", {})
                    raise BedrockConverseError(
                        error_code=error_response.get("Code", "UnknownError"),
                        message=error_response.get("Message", str(exc)),
                    )
        else:
            # Model does not support extended thinking — reasoning isn't
            # applicable, so do NOT emit the "unavailable" notice.
            try:
                response = self.client.converse_stream(**base_kwargs)
            except ClientError as exc:
                error_response = exc.response.get("Error", {})
                raise BedrockConverseError(
                    error_code=error_response.get("Code", "UnknownError"),
                    message=error_response.get("Message", str(exc)),
                )

        if thinking_unavailable:
            yield "data: [THINKING_UNAVAILABLE]\n\n"

        # Stream tokens and collect full text
        full_text = ""
        full_thinking = ""
        guardrail_intervened = False
        stream = response.get("stream")
        if stream:
            for event in stream:
                # contentBlockStart / contentBlockStop events carry no delta
                # payload, so skip straight to the next event.
                if event.get("contentBlockStart", {}):
                    continue

                if "contentBlockStop" in event:
                    continue

                # messageStop carries stopReason — "guardrail_intervened" means
                # the guardrail blocked or anonymized content in this turn.
                message_stop = event.get("messageStop")
                if message_stop:
                    if message_stop.get("stopReason") == "guardrail_intervened":
                        guardrail_intervened = True
                    continue

                delta = event.get("contentBlockDelta", {}).get("delta", {})

                # Handle thinking delta
                thinking_chunk = delta.get("thinking", "")
                if thinking_chunk:
                    full_thinking += thinking_chunk
                    safe = thinking_chunk.replace("\n", "\\n")
                    yield f"data: [THINKING]{safe}\n\n"
                    continue

                # Handle text delta
                text_chunk = delta.get("text", "")
                if text_chunk:
                    full_text += text_chunk
                    # SSE format: escape newlines within the chunk
                    safe = text_chunk.replace("\n", "\\n")
                    yield f"data: {safe}\n\n"

        if not full_text:
            full_text = "I was unable to generate a response. Please try again."
            yield f"data: {full_text}\n\n"

        if guardrail_intervened:
            yield "data: [GUARDRAIL_INTERVENED]\n\n"

        # Persist conversation only when the Prompt History toggle is on.
        # Mirrors the gating in chat() above; encryption applies when a
        # Fernet key is configured.
        now = datetime.now(timezone.utc)
        if settings_service.get_prompt_history_enabled(db):
            crypto = get_crypto()
            db.add(
                ChatMessage(
                    session_id=session_id,
                    role="user",
                    content=crypto.encrypt(message),
                    created_at=now,
                    user_identity=user_identity,
                    user_name=user_name,
                )
            )
            db.add(
                ChatMessage(
                    session_id=session_id,
                    role="assistant",
                    content=crypto.encrypt(full_text),
                    created_at=now,
                )
            )
            db.commit()
        else:
            logger.info(
                "Prompt history disabled; skipping chat_messages persistence for session=%s",
                session_id,
            )

        # Final event carries session_id so the client can track the session
        yield f"data: [DONE:{session_id}]\n\n"

    def build_usage_context(self, filters: dict[str, Any], db: Session) -> str:
        """Build a text summary of usage data for the system prompt.

        Args:
            filters: Dashboard filter dict.
            db: SQLAlchemy database session.

        Returns:
            A formatted text summary of usage statistics.
        """
        # Parse filter dates
        start_date = filters.get("start_date")
        end_date = filters.get("end_date")

        if not start_date or not end_date:
            start_date, end_date = FilterService.get_default_date_range()
        elif isinstance(start_date, str):
            start_date = datetime.fromisoformat(start_date).replace(tzinfo=timezone.utc)
            end_date_str = end_date if isinstance(end_date, str) else end_date.isoformat()
            end_date = datetime.fromisoformat(end_date_str).replace(tzinfo=timezone.utc)
            # If end_date was a bare date (no time), set to end of day — same as activity routes
            if isinstance(filters.get("end_date"), str) and len(filters["end_date"]) <= 10:
                end_date = end_date.replace(hour=23, minute=59, second=59)

        # Source records the SAME way the activity/teams routes do so chat totals
        # agree with the dashboard: CloudTrail/CloudWatch/S3 + persisted demo
        # records + Agent Chat records, with costs already applied in place.
        # Imported at function level to avoid any import-order issues with the
        # routes module (agent_service is imported by the agent route).
        from app.routes.activity import source_activity_records_with_meta

        try:
            ct_records, chat_records, _truncated, _ingestion_error = (
                source_activity_records_with_meta(start_date, end_date, db)
            )
        except Exception:
            return "No usage data available."

        # Apply the SAME user/source filters the activity route applies.
        user_identities = filters.get("user_identities", [])
        activity_filters = ActivityFilters(
            start_date=start_date,
            end_date=end_date,
            user_identities=user_identities,
            source_application=filters.get("source_application"),
        )
        filtered_ct = FilterService.apply_filters(ct_records, activity_filters)

        # Mirror activity.py: Agent Chat records receive the SAME user filter.
        # When a user filter is set (e.g. the #1 route scoping forces one for
        # non-admins) chat records won't match a human identity and are excluded
        # — correct. With no user filter (admin, unscoped) they're included.
        filtered_chat = FilterService.apply_filters(chat_records, activity_filters)

        # Apply team filter to the CloudTrail records if present; chat records
        # keep their user-filtered set, mirroring the activity route's behavior.
        team_id = filters.get("team_id")
        if team_id:
            filtered_ct = FilterService.filter_by_team(filtered_ct, team_id, db)

        records = filtered_ct + filtered_chat

        if not records:
            return "No usage data available for the selected filters."

        # Aggregate
        agg = AggregationService()
        overview = agg.compute_overview(records)
        by_user = agg.aggregate_by_user(records)
        by_model = agg.aggregate_by_model(records)
        by_source = agg.aggregate_by_source(records)

        # Overall totals used to compute distinct share metrics
        overall_total_tokens = overview["totalTokens"] or 0

        # Format context
        lines = [
            "=== Bedrock Usage Summary ===",
            f"Date Range: {overview['dateRange']['start']} to {overview['dateRange']['end']}",
            f"Total Invocations: {overview['totalInvocations']}",
            f"Total Tokens: {overview['totalTokens']}",
            f"  Input Tokens: {overview['totalInputTokens']}",
            f"  Output Tokens: {overview['totalOutputTokens']}",
            f"  Cache Read Tokens: {overview['totalCacheReadTokens']}",
            f"  Cache Write Tokens: {overview['totalCacheWriteTokens']}",
            f"Total Cost Estimate: {_fmt_cost(overview['totalCostEstimate'])}",
            f"Active Users: {overview['activeUsers']}",
            f"Active Models: {overview['activeModels']}",
            "",
            "--- Top Users by Token Usage ---",
        ]
        for u in by_user[:10]:
            lines.append(
                f"  {u['userName']}: {u['totalTokens']} tokens, cost {_fmt_cost(u['costEstimate'])}"
            )

        lines.append("")
        lines.append("--- Model Usage ---")
        for m in by_model:
            # percentageOfTotal is INVOCATION share; token share is computed here.
            inv_share_pct = m["percentageOfTotal"]
            token_share_pct = (
                (m["totalTokens"] / overall_total_tokens * 100.0)
                if overall_total_tokens > 0
                else 0.0
            )
            lines.append(
                f"  {m['modelId']}: {m['invocationCount']} invocations "
                f"({inv_share_pct:.1f}% of invocations), "
                f"{m['totalTokens']} tokens ({token_share_pct:.1f}% of tokens), "
                f"cost {_fmt_cost(m['costEstimate'])}"
            )

        # Source-application breakdown — lets the assistant answer questions like
        # "which source applications drive the most usage?".
        overall_total_invocations = overview["totalInvocations"] or 0
        lines.append("")
        lines.append("--- Source Application Usage ---")
        for s in by_source:
            src_inv_share_pct = (
                (s["invocationCount"] / overall_total_invocations * 100.0)
                if overall_total_invocations > 0
                else 0.0
            )
            lines.append(
                f"  {s['sourceApplication']}: {s['invocationCount']} invocations "
                f"({src_inv_share_pct:.1f}% of invocations), "
                f"{s['totalTokens']} tokens, cost {_fmt_cost(s['costEstimate'])}"
            )

        return "\n".join(lines)

    @staticmethod
    def _extract_thinking_and_text(
        content_blocks: list[dict[str, Any]],
    ) -> tuple[str, str]:
        """Extract assistant text and thinking content from Converse API response blocks.

        The Converse API may return content blocks with a "thinking" key for
        extended thinking content and "text" key for the final response.

        Returns:
            Tuple of (assistant_text, thinking_text).
        """
        assistant_text = ""
        thinking_text = ""
        for block in content_blocks:
            if "thinking" in block:
                thinking_text += block["thinking"]
            elif "text" in block:
                assistant_text += block["text"]
        return assistant_text, thinking_text

    def _converse_with_retry(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        additional_model_request_fields: dict[str, Any] | None = None,
        guardrail_config: dict[str, Any] | None = None,
        request_metadata: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Call Bedrock Converse API with exponential backoff retry.

        Args:
            system_prompt: System prompt text.
            messages: Conversation messages.
            additional_model_request_fields: Optional dict for extended thinking
                or other model-specific fields.
            guardrail_config: Optional Converse ``guardrailConfig`` dict
                (guardrailIdentifier/guardrailVersion/trace) to enforce the
                dashboard's managed guardrail on this call.
            request_metadata: Optional Converse ``requestMetadata`` tag map
                (see ``_build_request_metadata``) for per-caller attribution in
                the Bedrock model invocation log.

        Retries up to _MAX_RETRIES times on ClientError, then raises
        BedrockConverseError with the error code and message.
        """
        last_error: ClientError | None = None
        kwargs: dict[str, Any] = {
            "modelId": self.model_id,
            "system": [{"text": system_prompt}],
            "messages": messages,
        }
        if additional_model_request_fields:
            kwargs["additionalModelRequestFields"] = additional_model_request_fields
        if guardrail_config:
            kwargs["guardrailConfig"] = guardrail_config
        if request_metadata:
            kwargs["requestMetadata"] = request_metadata

        for attempt in range(_MAX_RETRIES):
            try:
                return self.client.converse(**kwargs)
            except ClientError as exc:
                last_error = exc
                error_code = exc.response.get("Error", {}).get("Code", "Unknown")
                logger.warning(
                    "Bedrock converse attempt %d/%d failed: %s — %s",
                    attempt + 1, _MAX_RETRIES, error_code, exc,
                )
                if attempt < _MAX_RETRIES - 1:
                    backoff = _BASE_BACKOFF_SECONDS * (2**attempt)
                    time.sleep(backoff)

        # All retries exhausted
        error_response = last_error.response.get("Error", {})  # type: ignore[union-attr]
        raise BedrockConverseError(
            error_code=error_response.get("Code", "UnknownError"),
            message=error_response.get("Message", str(last_error)),
        )

    @staticmethod
    def _session_owned_by_other(
        session_id: str, user_identity: str | None, db: Session
    ) -> bool:
        """True if ``session_id`` already holds turns authored by someone else.

        Skipped (False) when no caller identity is available (auth disabled).
        Legacy rows with no recorded identity are treated as unowned.
        """
        if not user_identity:
            return False
        foreign = (
            db.query(ChatMessage.id)
            .filter(
                ChatMessage.session_id == session_id,
                ChatMessage.role == "user",
                ChatMessage.user_identity.isnot(None),
                ChatMessage.user_identity != user_identity,
            )
            .first()
        )
        return foreign is not None

    @staticmethod
    def _load_history(
        session_id: str, db: Session, user_identity: str | None = None
    ) -> list[ChatMessage]:
        """Load recent conversation history for a session, oldest→newest.

        Only the most recent ``_MAX_HISTORY_MESSAGES`` messages are returned to
        bound prompt size and latency. We fetch the latest N by ``created_at``
        DESC (with ``id`` as a stable tie-breaker) then reverse to chronological
        ascending order so the prompt still reads oldest→newest. Full history
        remains persisted in the DB — only what is replayed to the model is
        capped.
        """
        if AgentService._session_owned_by_other(session_id, user_identity, db):
            return []
        recent = (
            db.query(ChatMessage)
            .filter(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
            .limit(_MAX_HISTORY_MESSAGES)
            .all()
        )
        recent.reverse()
        return recent
