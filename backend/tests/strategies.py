"""Shared Hypothesis strategies for Bedrock Activity Dashboard PBT."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from hypothesis import strategies as st
from hypothesis.strategies import composite

_MODEL_IDS = [
    "anthropic.claude-sonnet-4-20250514-v1:0",
    "anthropic.claude-3-haiku-20240307-v1:0",
    "anthropic.claude-3-opus-20240229-v1:0",
    "amazon.titan-text-express-v1",
]

_SOURCE_APPS = ["Kiro", "Q Developer", "Unknown"]


@composite
def activity_record(draw, model_id=None, user_identity=None, timestamp=None):
    """Generate a valid activity record dict."""
    inp = draw(st.integers(min_value=0, max_value=100_000))
    out = draw(st.integers(min_value=0, max_value=100_000))
    cr = draw(st.integers(min_value=0, max_value=100_000))
    cw = draw(st.integers(min_value=0, max_value=100_000))
    ts = timestamp or draw(
        st.datetimes(
            min_value=datetime(2024, 1, 1),
            max_value=datetime(2026, 12, 31),
            timezones=st.just(timezone.utc),
        )
    ).isoformat()
    return {
        "id": draw(st.uuids()).hex,
        "timestamp": ts,
        "userIdentity": user_identity or draw(st.sampled_from(["user-a", "user-b", "user-c", "user-d"])),
        "userName": draw(st.sampled_from(["Alice", "Bob", "Carol", "Dave"])),
        "modelId": model_id or draw(st.sampled_from(_MODEL_IDS)),
        "sourceApplication": draw(st.sampled_from(_SOURCE_APPS)),
        "sourceIp": "10.0.0.1",
        "inputTokens": inp,
        "outputTokens": out,
        "cacheReadTokens": cr,
        "cacheWriteTokens": cw,
        "totalTokens": inp + out + cr + cw,
        "costEstimate": None,
    }


@composite
def pricing_config(draw):
    """Generate a valid pricing config dict with Decimal values."""
    return {
        "inputTokenPrice": draw(st.decimals(min_value=Decimal("0.0001"), max_value=Decimal("0.1"), places=4, allow_nan=False, allow_infinity=False)),
        "outputTokenPrice": draw(st.decimals(min_value=Decimal("0.0001"), max_value=Decimal("0.1"), places=4, allow_nan=False, allow_infinity=False)),
        "cacheReadTokenPrice": draw(st.decimals(min_value=Decimal("0"), max_value=Decimal("0.05"), places=4, allow_nan=False, allow_infinity=False)),
        "cacheWriteTokenPrice": draw(st.decimals(min_value=Decimal("0"), max_value=Decimal("0.05"), places=4, allow_nan=False, allow_infinity=False)),
    }


@composite
def date_range(draw):
    """Generate a valid (start, end) datetime pair where start <= end."""
    start = draw(st.datetimes(
        min_value=datetime(2024, 1, 1),
        max_value=datetime(2026, 6, 1),
        timezones=st.just(timezone.utc),
    ))
    delta = draw(st.timedeltas(min_value=timedelta(0), max_value=timedelta(days=90)))
    end = start + delta
    return start, end



@composite
def chat_message_pair(draw, session_id=None):
    """Generate a valid (user_msg_dict, assistant_msg_dict) pair for a chat session."""
    sid = session_id or draw(st.uuids()).hex
    base_ts = draw(st.datetimes(
        min_value=datetime(2024, 1, 1),
        max_value=datetime(2026, 12, 31),
        timezones=st.just(timezone.utc),
    ))
    user_content = draw(st.text(min_size=1, max_size=200))
    asst_content = draw(st.text(min_size=1, max_size=500))
    return (
        {"session_id": sid, "role": "user", "content": user_content, "created_at": base_ts},
        {"session_id": sid, "role": "assistant", "content": asst_content, "created_at": base_ts + timedelta(seconds=1)},
    )


@composite
def chat_session(draw, session_id=None):
    """Generate a sequence of ChatMessage dicts for a single session.

    Produces interleaved user/assistant pairs with an optional trailing user message.
    """
    sid = session_id or draw(st.uuids()).hex
    n_pairs = draw(st.integers(min_value=1, max_value=5))
    trailing_user = draw(st.booleans())
    base_ts = draw(st.datetimes(
        min_value=datetime(2024, 1, 1),
        max_value=datetime(2026, 6, 1),
        timezones=st.just(timezone.utc),
    ))
    msgs = []
    for i in range(n_pairs):
        offset = timedelta(seconds=i * 10)
        msgs.append({"session_id": sid, "role": "user", "content": f"q{i}", "created_at": base_ts + offset})
        msgs.append({"session_id": sid, "role": "assistant", "content": f"a{i}", "created_at": base_ts + offset + timedelta(seconds=1)})
    if trailing_user:
        msgs.append({"session_id": sid, "role": "user", "content": "trailing", "created_at": base_ts + timedelta(seconds=n_pairs * 10)})
    return msgs
