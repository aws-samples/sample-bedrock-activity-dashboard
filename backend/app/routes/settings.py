"""Admin API for runtime toggles — currently the Prompt History on/off switch.

The toggle controls whether Agent Chat persists user/assistant message
content to the ``chat_messages`` table and whether /api/activity/prompts
surfaces anything beyond the CloudTrail + S3 ingestion feed. Encryption of
persisted content is independent of the toggle: when a Fernet key is
configured, Agent Chat writes are always encrypted at rest.

Public read (``GET /api/settings/prompt-history``) is intentionally not
admin-gated so non-admins can at least see whether their chats will be
retained; writes are admin-only via ``require_admin``.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import CurrentUser, get_current_user, require_admin
from app.database import get_db
from app.services import settings_service
from app.services.crypto_service import get_crypto

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/settings", tags=["settings"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class PromptHistorySettingsResponse(BaseModel):
    """Current state of the prompt-history toggle + encryption posture."""

    enabled: bool = Field(
        description=(
            "True when Agent Chat persists user and assistant message text "
            "to the chat_messages table and the Prompt History tab shows "
            "Agent Chat records. False means Agent Chat runs in "
            "ephemeral mode."
        )
    )
    encryption_available: bool = Field(
        description=(
            "True when a valid Fernet key is loaded into the backend "
            "process, which means persisted prompt content will be "
            "encrypted at rest."
        )
    )
    encryption_error: Optional[str] = Field(
        default=None,
        description=(
            "Human-readable reason encryption is unavailable, when "
            "``encryption_available`` is false. Shown verbatim to the admin "
            "in the Settings UI so misconfiguration is obvious."
        ),
    )
    updated_at: Optional[datetime] = Field(
        default=None,
        description="When the toggle was last changed, in UTC. Null before first change.",
    )
    updated_by: Optional[str] = Field(
        default=None,
        description="Identity of the admin who last changed the toggle, if recorded.",
    )


class PromptHistoryToggleRequest(BaseModel):
    """Payload for flipping the toggle."""

    enabled: bool


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/prompt-history", response_model=PromptHistorySettingsResponse)
def get_prompt_history_settings(
    db: Session = Depends(get_db),
    _user: CurrentUser = Depends(get_current_user),  # any authenticated user
) -> PromptHistorySettingsResponse:
    """Read the current prompt-history toggle + encryption posture."""
    crypto = get_crypto()
    return PromptHistorySettingsResponse(
        enabled=settings_service.get_prompt_history_enabled(db),
        encryption_available=crypto.is_available,
        encryption_error=crypto.init_error,
        updated_at=settings_service.get_prompt_history_updated_at(db),
        updated_by=settings_service.get_prompt_history_updated_by(db),
    )


@router.put("/prompt-history", response_model=PromptHistorySettingsResponse)
def update_prompt_history_settings(
    body: PromptHistoryToggleRequest,
    db: Session = Depends(get_db),
    admin: CurrentUser = Depends(require_admin),
) -> PromptHistorySettingsResponse:
    """Flip the toggle. Admin-only.

    Gatekeeper: when ``enabled=True``, this call is REJECTED unless the
    backend has a usable at-rest encryption key loaded. The toggle controls
    whether prompt/response text is persisted to our DB (Agent Chat +
    demo records) AND whether such text is included in any API payload the
    UI consumes, so enabling it without an encryption key would mean the
    dashboard is persisting and surfacing prompt content without the
    advertised at-rest protection. We refuse rather than silently falling
    back to plaintext — the operator must configure
    PROMPT_HISTORY_ENCRYPTION_KEY (via Secrets Manager / env var) first.

    Flipping the toggle takes effect immediately for all subsequent requests
    across every ECS task (the setting is read on every call, never
    cached), so no redeploy or restart is required.
    """
    crypto = get_crypto()
    if body.enabled and not crypto.is_available:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "encryption_key_unavailable",
                "message": (
                    "Prompt History cannot be enabled because the backend has "
                    "no usable at-rest encryption key. Configure "
                    "PROMPT_HISTORY_ENCRYPTION_KEY (via Secrets Manager on a "
                    "CDK deploy, or the env var for local dev) and try again. "
                    "This dashboard refuses to persist or surface prompt "
                    "content without the advertised encryption-at-rest guarantee."
                ),
                "encryption_error": crypto.init_error,
            },
        )

    settings_service.set_prompt_history_enabled(
        db, body.enabled, updated_by=admin.identity or admin.email or admin.username
    )
    return PromptHistorySettingsResponse(
        enabled=body.enabled,
        encryption_available=crypto.is_available,
        encryption_error=crypto.init_error,
        updated_at=settings_service.get_prompt_history_updated_at(db),
        updated_by=settings_service.get_prompt_history_updated_by(db),
    )
