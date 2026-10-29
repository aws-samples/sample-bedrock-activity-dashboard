"""Admin-managed runtime settings backed by the ``app_runtime_settings`` KV table.

Thin typed wrapper over the raw KV so route handlers don't deal with strings:

- ``prompt_history_enabled`` -> bool (default True)

Reads tolerate a missing row by applying a code-level default, so a freshly
seeded database boots with the expected behavior without needing a data
migration. Writes stamp ``updated_at`` and ``updated_by`` for an audit trail.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.models import AppRuntimeSetting

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Keys + defaults
# ---------------------------------------------------------------------------

PROMPT_HISTORY_ENABLED_KEY = "prompt_history_enabled"
_DEFAULT_PROMPT_HISTORY_ENABLED = True


# ---------------------------------------------------------------------------
# Raw KV helpers
# ---------------------------------------------------------------------------

def _get_raw(db: Session, key: str) -> Optional[str]:
    row = db.query(AppRuntimeSetting).filter(AppRuntimeSetting.key == key).first()
    return row.value if row is not None else None


def _set_raw(db: Session, key: str, value: str, updated_by: Optional[str]) -> None:
    row = db.query(AppRuntimeSetting).filter(AppRuntimeSetting.key == key).first()
    now = datetime.now(timezone.utc)
    if row is None:
        row = AppRuntimeSetting(
            key=key, value=value, updated_at=now, updated_by=updated_by
        )
        db.add(row)
    else:
        row.value = value
        row.updated_at = now
        row.updated_by = updated_by
    db.commit()


def _parse_bool(raw: Optional[str], default: bool) -> bool:
    """Robust bool parse — tolerant of legacy ``"1"``/``"0"`` style values."""
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"true", "1", "yes", "on", "enabled"}:
        return True
    if normalized in {"false", "0", "no", "off", "disabled"}:
        return False
    logger.warning(
        "Unparseable runtime setting value for key=%s value=%r; "
        "falling back to default=%s",
        normalized, raw, default,
    )
    return default


# ---------------------------------------------------------------------------
# Prompt-history toggle
# ---------------------------------------------------------------------------

def get_prompt_history_enabled(db: Session) -> bool:
    """Return whether Agent Chat should persist prompt/response text.

    Default is ``True`` so a fresh deploy keeps existing behavior; the admin
    must explicitly flip the toggle off from the Settings UI to opt out.
    """
    raw = _get_raw(db, PROMPT_HISTORY_ENABLED_KEY)
    return _parse_bool(raw, _DEFAULT_PROMPT_HISTORY_ENABLED)


def set_prompt_history_enabled(
    db: Session, enabled: bool, updated_by: Optional[str] = None
) -> None:
    """Persist the toggle. ``updated_by`` is stored for the admin audit trail."""
    _set_raw(
        db,
        PROMPT_HISTORY_ENABLED_KEY,
        "true" if enabled else "false",
        updated_by,
    )
    logger.info(
        "Prompt-history persistence toggled: enabled=%s updated_by=%s",
        enabled, updated_by or "<unknown>",
    )


def get_prompt_history_updated_at(db: Session) -> Optional[datetime]:
    """Return the last-change timestamp for the toggle, if any."""
    row = (
        db.query(AppRuntimeSetting)
        .filter(AppRuntimeSetting.key == PROMPT_HISTORY_ENABLED_KEY)
        .first()
    )
    return row.updated_at if row is not None else None


def get_prompt_history_updated_by(db: Session) -> Optional[str]:
    """Return the identity of whoever last toggled it, if recorded."""
    row = (
        db.query(AppRuntimeSetting)
        .filter(AppRuntimeSetting.key == PROMPT_HISTORY_ENABLED_KEY)
        .first()
    )
    return row.updated_by if row is not None else None
