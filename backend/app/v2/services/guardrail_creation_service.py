"""Bedrock Guardrail creation/update service.

Turns an APPROVED Guardrail Recommender finding into a REAL Amazon Bedrock
Guardrail via the CreateGuardrail / UpdateGuardrail control-plane APIs
(``boto3.client("bedrock")`` — the control-plane client, distinct from
``bedrock-runtime`` used for model invocation).

Design: rather than creating a brand-new guardrail per approved finding
(which would scatter PII protections across many small guardrails), this
service maintains a SINGLE guardrail for the whole dashboard
(``GUARDRAIL_NAME``) and accumulates PII entity rules onto it as findings are
approved:

  - First approval anywhere  -> CreateGuardrail with just that PII entity rule.
  - Every later approval     -> GetGuardrail (to fetch the current DRAFT
    config), merge in the new/updated entity rule, then UpdateGuardrail.

The DRAFT version is mutated by create/update, but DRAFT is a moving target —
it changes every time a new finding is approved. So immediately after each
successful create/update, this service ALSO calls CreateGuardrailVersion to
publish a stable, numbered snapshot, and records that version number
(``guardrail_published_version``) on the finding. Other parts of this
codebase that want to actually ENFORCE the guardrail (see
``agent_service.py`` / ``demo_service.py``, which pass ``guardrailConfig`` on
their own Converse calls) look up the latest published version via
:func:`get_latest_published_guardrail` rather than referencing DRAFT — so
their enforcement doesn't shift mid-conversation as more findings get
approved.

This only wires the guardrail into THIS dashboard's own Bedrock calls
(Agent Chat, Demo Data). It does not, and cannot by itself, enforce the
guardrail on Bedrock calls made by other applications/credentials in the
account — that would require either code changes in those applications or an
account-wide IAM policy change, neither of which this service performs.

Rejecting a finding never calls this service; only an approval transition
does (see ``agent_finding_service.update_finding_status``).
"""

import logging
from typing import Any, Optional

import boto3
from botocore.exceptions import ClientError
from sqlalchemy.orm import Session

from app.config import settings
from app.v2.models import V2AgentGuardrailRecommendation

logger = logging.getLogger(__name__)

# Single guardrail this dashboard manages; accumulates PII rules over time
# rather than creating a new guardrail per approved recommendation. Must
# match Bedrock's guardrail name pattern: [0-9a-zA-Z-_]+, max 50 chars.
GUARDRAIL_NAME = "bedrock-dashboard-guardrail"
GUARDRAIL_DESCRIPTION = "Managed by the Bedrock Activity Dashboard Guardrail Recommender agent."
BLOCKED_INPUT_MESSAGE = "This prompt was blocked by the Bedrock Activity Dashboard guardrail policy."
BLOCKED_OUTPUT_MESSAGE = "This response was blocked by the Bedrock Activity Dashboard guardrail policy."

# Maps the dashboard's internal pii_type keys (as used in historical
# V2AgentPiiFinding rows and current Guardrail Recommender inputs) to
# Bedrock Guardrails' GuardrailPiiEntityType enum values. The PII Detector
# agent itself was retired, but this mapping is still used by the Guardrail
# Recommender when it has pii_type input (from legacy rows or caller-
# supplied payloads).
PII_TYPE_TO_BEDROCK_ENTITY: dict[str, str] = {
    "email": "EMAIL",
    "phone": "PHONE",
    "ssn": "US_SOCIAL_SECURITY_NUMBER",
    "credit_card": "CREDIT_DEBIT_CARD_NUMBER",
    "aws_access_key": "AWS_ACCESS_KEY",
}

_VALID_ACTIONS = {"BLOCK", "ANONYMIZE", "NONE"}


class GuardrailCreationError(Exception):
    """Raised internally when the real Bedrock create/update call fails."""


class GuardrailCreationService:
    """Creates/updates the dashboard's managed Bedrock Guardrail from approved recommendations."""

    def __init__(self) -> None:
        self._client = None

    @property
    def client(self):
        """Lazily construct the Bedrock control-plane client (never at import time)."""
        if self._client is None:
            region = settings.bedrock_region or settings.aws_region
            session_kwargs: dict[str, Any] = {"region_name": region}
            if settings.aws_access_key_id:
                session_kwargs["aws_access_key_id"] = settings.aws_access_key_id
            if settings.aws_secret_access_key:
                session_kwargs["aws_secret_access_key"] = settings.aws_secret_access_key
            if settings.aws_session_token:
                session_kwargs["aws_session_token"] = settings.aws_session_token
            self._client = boto3.client("bedrock", **session_kwargs)
        return self._client

    def apply_recommendation(
        self, finding: V2AgentGuardrailRecommendation, db: Session
    ) -> dict[str, Any]:
        """Apply an approved recommendation to the real Bedrock Guardrail.

        Returns a dict describing the outcome:
          ``{"ok": True, "guardrail_id", "guardrail_arn", "guardrail_version",
             "guardrail_published_version"}``
        or
          ``{"ok": False, "error": "<message>"}``

        Never raises — AWS/config failures are caught and returned as
        ``{"ok": False, ...}`` so the caller can still persist the human
        approval decision regardless of whether the AWS mutation succeeded.

        On success, also publishes a numbered version (CreateGuardrailVersion)
        so callers that enforce the guardrail (Agent Chat, Demo Data) have a
        stable version to pin to instead of the ever-changing DRAFT. A
        publish failure does NOT undo the create/update — the guardrail
        config change stands; ``guardrail_published_version`` is simply left
        as whatever it was before (None on the very first approval).
        """
        rec = finding.recommendation if isinstance(finding.recommendation, dict) else {}
        pii_type = rec.get("pii_type")
        entity_type = PII_TYPE_TO_BEDROCK_ENTITY.get(pii_type or "")
        if not entity_type:
            return {
                "ok": False,
                "error": f"No Bedrock PII entity mapping for pii_type={pii_type!r}",
            }

        action = str(rec.get("action") or "ANONYMIZE").upper()
        if action not in _VALID_ACTIONS:
            action = "ANONYMIZE"

        try:
            existing_arn = self._find_existing_guardrail_arn(db)
            if existing_arn:
                outcome = self._update_guardrail(existing_arn, entity_type, action)
            else:
                outcome = self._create_guardrail(entity_type, action)
        except GuardrailCreationError as exc:
            logger.warning("Guardrail creation/update failed: %s", exc)
            return {"ok": False, "error": str(exc)}
        except Exception as exc:  # never let an AWS/network failure bubble up
            logger.exception("Unexpected error applying guardrail recommendation")
            return {"ok": False, "error": f"Unexpected error: {exc}"[:500]}

        guardrail_id = outcome.get("guardrail_id")
        if guardrail_id:
            try:
                published = self.client.create_guardrail_version(
                    guardrailIdentifier=guardrail_id,
                    description="Published after a Guardrail Recommender approval.",
                )
                outcome["guardrail_published_version"] = published.get("version")
            except ClientError as exc:
                # The config change itself already succeeded — don't fail the
                # whole approval over a publish-step failure, just log it.
                logger.warning(
                    "Guardrail config updated but publishing a version failed: %s",
                    _client_error_message(exc),
                )

        return outcome

    def _find_existing_guardrail_arn(self, db: Session) -> Optional[str]:
        """Return the guardrail ARN from a prior successful approval, if any."""
        row = (
            db.query(V2AgentGuardrailRecommendation)
            .filter(V2AgentGuardrailRecommendation.guardrail_arn.isnot(None))
            .order_by(V2AgentGuardrailRecommendation.updated_at.desc())
            .first()
        )
        return row.guardrail_arn if row else None

    def _create_guardrail(self, entity_type: str, action: str) -> dict[str, Any]:
        try:
            response = self.client.create_guardrail(
                name=GUARDRAIL_NAME,
                description=GUARDRAIL_DESCRIPTION,
                sensitiveInformationPolicyConfig={
                    "piiEntitiesConfig": [{"type": entity_type, "action": action}],
                },
                blockedInputMessaging=BLOCKED_INPUT_MESSAGE,
                blockedOutputsMessaging=BLOCKED_OUTPUT_MESSAGE,
            )
        except ClientError as exc:
            raise GuardrailCreationError(_client_error_message(exc)) from exc

        return {
            "ok": True,
            "guardrail_id": response.get("guardrailId"),
            "guardrail_arn": response.get("guardrailArn"),
            "guardrail_version": response.get("version"),
        }

    def _update_guardrail(
        self, guardrail_arn: str, entity_type: str, action: str
    ) -> dict[str, Any]:
        try:
            current = self.client.get_guardrail(guardrailIdentifier=guardrail_arn)
        except ClientError as exc:
            raise GuardrailCreationError(_client_error_message(exc)) from exc

        existing_policy = current.get("sensitiveInformationPolicy", {}) or {}
        existing_entities = existing_policy.get("piiEntities", []) or []

        # Merge: replace the entry for this entity type if already configured,
        # otherwise append it — so re-approving a finding for the same PII
        # type updates its action rather than duplicating the entry.
        merged: dict[str, dict[str, Any]] = {
            e["type"]: {"type": e["type"], "action": e.get("action", "ANONYMIZE")}
            for e in existing_entities
            if e.get("type")
        }
        merged[entity_type] = {"type": entity_type, "action": action}

        sensitive_config: dict[str, Any] = {"piiEntitiesConfig": list(merged.values())}

        existing_regexes = existing_policy.get("regexes", []) or []
        preserved_regexes = [
            {
                "name": r["name"],
                "pattern": r["pattern"],
                "action": r.get("action", "ANONYMIZE"),
                **({"description": r["description"]} if r.get("description") else {}),
            }
            for r in existing_regexes
            if r.get("name") and r.get("pattern")
        ]
        if preserved_regexes:
            sensitive_config["regexesConfig"] = preserved_regexes

        try:
            response = self.client.update_guardrail(
                guardrailIdentifier=guardrail_arn,
                name=current.get("name", GUARDRAIL_NAME),
                description=current.get("description", GUARDRAIL_DESCRIPTION),
                sensitiveInformationPolicyConfig=sensitive_config,
                blockedInputMessaging=current.get(
                    "blockedInputMessaging", BLOCKED_INPUT_MESSAGE
                ),
                blockedOutputsMessaging=current.get(
                    "blockedOutputsMessaging", BLOCKED_OUTPUT_MESSAGE
                ),
            )
        except ClientError as exc:
            raise GuardrailCreationError(_client_error_message(exc)) from exc

        return {
            "ok": True,
            "guardrail_id": response.get("guardrailId"),
            "guardrail_arn": response.get("guardrailArn"),
            "guardrail_version": response.get("version"),
        }


def _client_error_message(exc: ClientError) -> str:
    """Format a boto3 ClientError as a short, user-facing message."""
    error = exc.response.get("Error", {})
    code = error.get("Code", "UnknownError")
    message = error.get("Message", str(exc))
    return f"{code}: {message}"


def get_latest_published_guardrail(db: Session) -> Optional[tuple[str, str]]:
    """Return ``(guardrail_id, published_version)`` for enforcement, or None.

    Used by ``agent_service.py`` (Agent Chat) and ``demo_service.py`` (Demo
    Data) to look up the guardrail to pass via ``guardrailConfig`` on their
    own Converse calls. Only ever returns a PUBLISHED numbered version — never
    DRAFT — so enforcement doesn't shift mid-conversation as new findings get
    approved. Returns None when no recommendation has ever been successfully
    approved AND published (nothing to enforce yet), so callers can cleanly
    skip attaching guardrailConfig.
    """
    row = (
        db.query(V2AgentGuardrailRecommendation)
        .filter(
            V2AgentGuardrailRecommendation.guardrail_id.isnot(None),
            V2AgentGuardrailRecommendation.guardrail_published_version.isnot(None),
        )
        .order_by(V2AgentGuardrailRecommendation.updated_at.desc())
        .first()
    )
    if row is None:
        return None
    return row.guardrail_id, row.guardrail_published_version
