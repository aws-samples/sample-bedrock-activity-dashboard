"""Service for converting ChatMessage records into Activity_Record dicts."""

import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.config import settings
from app.models import ChatMessage
from app.services.crypto_service import get_crypto

logger = logging.getLogger(__name__)

# Bound on how far apart a chat placeholder's timestamp and its matching real
# invocation's timestamp may be. The real record's timestamp is when Bedrock
# logged the invocation (before the response finished streaming); the chat
# placeholder's timestamp is set after the full response is received and
# persisted — so the real timestamp is always slightly EARLIER, typically by
# a few seconds. 120s comfortably covers slow/streamed responses without
# risking a false match against an unrelated invocation.
_DEDUPE_TOLERANCE_SECONDS = 120


def get_chat_activity_records(
    db: Session,
    start_date: datetime,
    end_date: datetime,
) -> list[dict[str, Any]]:
    """Query ChatMessage table, pair user+assistant messages per session,
    and return Activity_Record dicts.

    Each user message is paired with the immediately following assistant
    message in the same session. Unpaired user messages are skipped.
    """
    rows = (
        db.query(ChatMessage)
        .filter(
            ChatMessage.created_at >= start_date,
            ChatMessage.created_at <= end_date,
        )
        .order_by(ChatMessage.session_id, ChatMessage.created_at)
        .all()
    )

    # Group by session
    sessions: dict[str, list[ChatMessage]] = defaultdict(list)
    for row in rows:
        sessions[str(row.session_id)].append(row)

    records: list[dict[str, Any]] = []
    for msgs in sessions.values():
        i = 0
        while i < len(msgs) - 1:
            if msgs[i].role == "user" and msgs[i + 1].role == "assistant":
                user_msg = msgs[i]
                asst_msg = msgs[i + 1]
                records.append(_pair_to_record(user_msg, asst_msg))
                i += 2
            else:
                i += 1

    return records


def _normalized_bedrock_model_id() -> str:
    """The configured Agent Chat model id, normalized the same way real
    ingested records are (cross-region prefix stripped), so the two sides of
    the dedup comparison use the same model-id form.

    Imported at call time to avoid a module-level circular import between
    ``chat_message_service`` and ``cloudtrail_service``.
    """
    from app.services.cloudtrail_service import _strip_region_prefix

    return _strip_region_prefix(settings.bedrock_model_id)


def ingestion_regions_for_chat_dedup(regions: list[str] | None) -> list[str] | None:
    """Widen an explicit region-scoped ingestion request so it always covers
    the region Agent Chat's real Bedrock calls run in.

    Every Agent Chat turn calls Bedrock in ONE fixed region
    (``settings.bedrock_region``/``settings.aws_region`` — see
    ``AgentService.__init__``), regardless of any region filter the caller has
    applied to the activity/prompt-history VIEW. ``suppress_chat_placeholders_
    with_real_invocation`` can only drop a chat placeholder once it finds that
    turn's real invocation log among the ingested records — so if the caller's
    region filter narrows ingestion to a set that excludes Agent Chat's home
    region, that real record is never fetched, the dedup match can never
    happen, and the placeholder survives showing 0 tokens/N/A cost even though
    the real, priced invocation exists in CloudWatch.

    Returns ``regions`` unchanged when it's ``None``/empty (already means
    "every configured region", which already covers the chat region) or
    already includes the chat region. Otherwise returns a NEW list with the
    chat region appended — this widens INGESTION scope only; the caller's
    region filter is still applied afterward to the records actually shown, so
    a real invocation from a region outside the filter still won't display,
    it just becomes available to suppress its placeholder.
    """
    if not regions:
        return regions
    chat_region = settings.bedrock_region or settings.aws_region
    if chat_region in regions:
        return regions
    return [*regions, chat_region]


def suppress_chat_placeholders_with_real_invocation(
    chat_records: list[dict[str, Any]],
    real_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Drop Agent Chat placeholder records that already have a real invocation logged.

    An Agent Chat turn always makes a real Bedrock call, which independently
    shows up via CloudTrail/CloudWatch/S3 ingestion with actual token counts
    and cost — while the chat-message-derived placeholder always carries
    ``inputTokens=0``/``costEstimate=None`` (Agent Chat only logs the
    conversation text, never the invocation's own usage). Without this step
    the same underlying invocation appears twice in the activity feed: once as
    a real record with data, once as a zero-token/N/A placeholder.

    There is no shared invocation id between the two sources (the chat
    placeholder is keyed by the ``ChatMessage`` row id, the real record by its
    CloudWatch/CloudTrail request id), so records are matched heuristically:
    same (region-prefix-stripped) model id as the configured Agent Chat model,
    with a real record's timestamp within ``_DEDUPE_TOLERANCE_SECONDS`` of the
    placeholder's. Matching is greedy and one-to-one (each real record can
    suppress at most one placeholder) to avoid one real invocation silently
    absorbing multiple chat turns.

    Best-effort: a chat placeholder with no matching real record (e.g. the
    real ingestion source is temporarily unavailable, or the call happened
    outside the tolerance window) is kept as-is — never dropped without a
    concrete match — so Agent Chat usage never silently disappears from the
    dashboard.
    """
    if not chat_records or not real_records:
        return chat_records

    chat_model_id = _normalized_bedrock_model_id()
    candidates = [
        r for r in real_records
        if r.get("modelId") == chat_model_id and r.get("timestamp")
    ]
    if not candidates:
        return chat_records

    tolerance = timedelta(seconds=_DEDUPE_TOLERANCE_SECONDS)
    consumed_ids: set[Any] = set()
    kept: list[dict[str, Any]] = []

    for chat_record in chat_records:
        chat_ts = _parse_timestamp(chat_record.get("timestamp"))
        matched_id: Any = None
        if chat_ts is not None:
            for real_record in candidates:
                real_id = real_record.get("id")
                if real_id in consumed_ids:
                    continue
                real_ts = _parse_timestamp(real_record.get("timestamp"))
                if real_ts is None:
                    continue
                if abs(chat_ts - real_ts) <= tolerance:
                    matched_id = real_id
                    break

        if matched_id is not None:
            consumed_ids.add(matched_id)
            logger.debug(
                "Suppressing Agent Chat placeholder %s: real invocation %s already logged",
                chat_record.get("id"), matched_id,
            )
        else:
            kept.append(chat_record)

    return kept


def _parse_timestamp(value: Any) -> datetime | None:
    """Best-effort ISO-8601 -> datetime, returning None on any parse failure."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def get_chat_record_by_id(
    db: Session,
    record_id: str,
    viewer_identity: str | None = None,
    can_view_all: bool = False,
) -> dict[str, Any] | None:
    """Look up a single Agent Chat Activity_Record by the user message ID.

    Owner-checked: unless ``can_view_all`` is true, the record is returned only
    when it was authored by ``viewer_identity``. A non-owner gets ``None``
    (indistinguishable from "not found") so IDs cannot be probed.
    """
    user_msg = db.query(ChatMessage).filter(ChatMessage.id == record_id).first()
    if user_msg is None or user_msg.role != "user":
        return None
    if not can_view_all and (
        not viewer_identity or user_msg.user_identity != viewer_identity
    ):
        return None

    # Find the next assistant message in the same session
    asst_msg = (
        db.query(ChatMessage)
        .filter(
            ChatMessage.session_id == user_msg.session_id,
            ChatMessage.role == "assistant",
            ChatMessage.created_at >= user_msg.created_at,
            ChatMessage.id != user_msg.id,
        )
        .order_by(ChatMessage.created_at)
        .first()
    )
    if asst_msg is None:
        return None

    return _pair_to_record(user_msg, asst_msg)


def _pair_to_record(user_msg: ChatMessage, asst_msg: ChatMessage) -> dict[str, Any]:
    """Convert a user+assistant ChatMessage pair into an Activity_Record dict.

    Content is passed through the crypto layer on the way out so persisted
    rows written under the at-rest encryption envelope (``enc:v1:...``) are
    returned as plaintext to the UI, while legacy plaintext rows pass
    through unchanged. Rows written under a key the service can no longer
    load render as a placeholder string so the UI never shows raw
    ciphertext — see ``PromptHistoryCrypto.decrypt``.
    """
    ts = user_msg.created_at
    crypto = get_crypto()
    return {
        "id": str(user_msg.id),
        "timestamp": ts.isoformat() if isinstance(ts, datetime) else str(ts),
        # Real authenticated identity when available (see AgentService.chat);
        # rows written before this was wired through (or with auth disabled)
        # fall back to the old placeholder so they don't render as blank.
        "userIdentity": user_msg.user_identity or "agent-chat",
        "userName": user_msg.user_name or "Agent Chat User",
        "modelId": settings.bedrock_model_id,
        "sourceApplication": "Agent Chat",
        "sourceIp": "N/A",
        "inputTokens": 0,
        "outputTokens": 0,
        "cacheReadTokens": 0,
        "cacheWriteTokens": 0,
        "totalTokens": 0,
        "costEstimate": None,
        "promptText": crypto.decrypt(user_msg.content),
        "responseText": crypto.decrypt(asst_msg.content),
    }
