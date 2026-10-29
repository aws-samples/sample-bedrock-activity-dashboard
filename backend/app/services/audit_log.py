"""Audit trail for privileged actions (AppSec F-1: actor identity + timestamp).

Each event is written to the ``audit`` logger (greppable in CloudWatch Logs)
and, when a DB session is supplied, persisted to ``v2_audit_events`` so the
record survives log retention and deletes of the target row (e.g. an upload).
"""
import logging
from typing import Any

from sqlalchemy.orm import Session

from app.auth import CurrentUser
from app.v2.models import V2AuditEvent

_audit_logger = logging.getLogger("audit")


def audit(actor: CurrentUser, action: str, db: Session | None = None, **fields: Any) -> None:
    """Record who did what. Never raises: an audit failure must not undo the action."""
    identity = actor.identity or actor.email or actor.username
    details = {k: str(v) for k, v in fields.items()}
    _audit_logger.info(
        "AUDIT action=%s actor=%s username=%s %s",
        action,
        identity,
        actor.username,
        " ".join(f"{k}={v}" for k, v in details.items()),
    )
    if db is None:
        return
    try:
        db.add(
            V2AuditEvent(
                actor_identity=identity,
                actor_username=actor.username,
                action=action,
                details=details,
            )
        )
        db.commit()
    except Exception:
        db.rollback()
        _audit_logger.error(
            "AUDIT persist failed action=%s actor=%s", action, identity, exc_info=True
        )
