"""Bedrock client service for the Legal Review App.

Uses Amazon Bedrock's Converse API with Claude models to perform
legal document review via prompt engineering. Replaces the direct
Anthropic API integration with AWS-native Bedrock calls.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from functools import partial
from typing import Any

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError, ReadTimeoutError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Response data models
# ---------------------------------------------------------------------------

VALID_RISK_LEVELS = {"high", "medium", "low", "informational"}


@dataclass
class LegalFinding:
    """A single legal finding parsed from the API response."""

    risk_level: str
    description: str
    affected_clause: str | None = None
    recommended_action: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class LegalReviewResponse:
    """Structured response from a legal review API call."""

    findings: list[LegalFinding] = field(default_factory=list)
    raw_response: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class LegalReviewAPIError(Exception):
    """Raised when the Bedrock API returns a non-transient error."""


class LegalReviewTimeoutError(Exception):
    """Raised when the Bedrock API call exceeds the timeout."""


# ---------------------------------------------------------------------------
# System prompts for legal review
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT_BASE = """\
You are an expert legal reviewer. Analyze the provided document and produce \
structured legal findings. For each finding, provide:

1. risk_level: one of "high", "medium", "low", or "informational"
2. description: a clear explanation of the legal issue or observation
3. affected_clause: the specific clause, section, or text from the document
4. recommended_action: what the user should do to address this finding

Respond ONLY with a JSON array of finding objects. Do not include any text \
before or after the JSON. Example format:

[
  {
    "risk_level": "high",
    "description": "The indemnification clause is one-sided...",
    "affected_clause": "Section 8.1 - Indemnification",
    "recommended_action": "Negotiate mutual indemnification..."
  }
]

If the document has no legal issues, return an empty array: []
"""

_COMMAND_PROMPTS: dict[str, str] = {
    "/review-contract": (
        "Review this contract clause-by-clause. Flag risk levels using "
        "GREEN (informational), YELLOW (medium), RED (high) criteria. "
        "Identify unfavorable terms, missing protections, ambiguous language, "
        "and one-sided obligations. Suggest specific redline edits."
    ),
    "/triage-nda": (
        "Perform rapid NDA pre-screening. Categorize this NDA as: "
        "standard-approval (low risk, standard terms), counsel-review "
        "(medium risk, non-standard terms), or full-review (high risk, "
        "unusual obligations). Flag scope of confidentiality, term/duration, "
        "non-compete clauses, and carve-outs."
    ),
    "/vendor-check": (
        "Review this vendor agreement for compliance risks. Check liability "
        "caps, data handling obligations, SLA terms, termination rights, "
        "IP ownership, and regulatory compliance requirements."
    ),
}

# ---------------------------------------------------------------------------
# Transient error codes that warrant a retry
# ---------------------------------------------------------------------------

_TRANSIENT_ERROR_CODES = {
    "ThrottlingException",
    "ServiceUnavailableException",
    "InternalServerException",
    "ModelTimeoutException",
}

# Default Bedrock model ID (Claude Opus 4.6 via cross-region inference)
_DEFAULT_MODEL_ID = "us.anthropic.claude-opus-4-20250514-v1:0"

# Available models for user selection
AVAILABLE_MODELS: dict[str, str] = {
    "claude-opus-4.6": "us.anthropic.claude-opus-4-20250514-v1:0",
    "claude-sonnet-4.6": "us.anthropic.claude-sonnet-4-20250514-v1:0",
    "claude-haiku-4.5": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
}


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class LegalReviewClient:
    """Uses Amazon Bedrock Converse API with Claude for legal document review.

    Features:
    - Maps document types to review-specific prompts
    - 120-second read timeout on Bedrock calls
    - Exponential backoff retry (1 s, 2 s, 4 s) for transient errors, max 3
    - Parses structured JSON findings from Claude's response
    """

    TIMEOUT_SECONDS = 120
    MAX_RETRIES = 3
    BASE_DELAY_SECONDS = 1

    COMMAND_MAP: dict[str, str] = {
        "contract": "/review-contract",
        "nda": "/triage-nda",
        "vendor": "/vendor-check",
        "general": "/review-contract",
    }

    def __init__(
        self,
        region: str | None = None,
        model_id: str | None = None,
    ) -> None:
        resolved_region = (
            region
            or os.environ.get("BEDROCK_REGION")
            or os.environ.get("AWS_REGION", "us-east-1")
        )
        boto_config = BotoConfig(
            read_timeout=self.TIMEOUT_SECONDS,
            retries={"max_attempts": 0},  # we handle retries ourselves
        )
        self._client = boto3.client(
            "bedrock-runtime",
            region_name=resolved_region,
            config=boto_config,
        )
        self._model_id = model_id or os.environ.get(
            "BEDROCK_MODEL_ID", _DEFAULT_MODEL_ID
        )

    # ------------------------------------------------------------------
    # Command mapping helper (exposed for testing)
    # ------------------------------------------------------------------

    @classmethod
    def get_command(cls, document_type: str) -> str:
        """Return the review command for *document_type*."""
        return cls.COMMAND_MAP.get(document_type, "/review-contract")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def submit_review(
        self,
        document_text: str,
        document_type: str,
        review_command: str | None = None,
        model_id: str | None = None,
    ) -> LegalReviewResponse:
        """Submit a document for legal review via Bedrock Converse API.

        Parameters
        ----------
        model_id:
            Optional Bedrock model ID override. Can be a short key from
            AVAILABLE_MODELS (e.g. "claude-opus-4.6") or a full model ID.

        Raises
        ------
        LegalReviewTimeoutError
            If Bedrock does not respond within the timeout.
        LegalReviewAPIError
            If Bedrock returns a non-transient error or retries exhausted.
        """
        command = review_command or self.get_command(document_type)
        system_prompt = self._build_system_prompt(command)
        user_message = self._build_user_message(document_text)

        # Resolve model: short key → full ID, or use provided ID, or default
        resolved_model = self._model_id
        if model_id:
            resolved_model = AVAILABLE_MODELS.get(model_id, model_id)

        last_error: Exception | None = None
        loop = asyncio.get_event_loop()

        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                # Run synchronous boto3 call in executor to stay async
                response = await loop.run_in_executor(
                    None,
                    partial(
                        self._client.converse,
                        modelId=resolved_model,
                        system=[{"text": system_prompt}],
                        messages=[{"role": "user", "content": [{"text": user_message}]}],
                        inferenceConfig={"maxTokens": 4096},
                    ),
                )
                return self._parse_response(response)

            except ReadTimeoutError as exc:
                last_error = exc
                logger.warning(
                    "Bedrock read timeout (attempt %d/%d)",
                    attempt, self.MAX_RETRIES,
                )
                if attempt < self.MAX_RETRIES:
                    await asyncio.sleep(self.BASE_DELAY_SECONDS * (2 ** (attempt - 1)))
                    continue
                raise LegalReviewTimeoutError(
                    f"Bedrock did not respond within {self.TIMEOUT_SECONDS}s "
                    f"after {self.MAX_RETRIES} attempts."
                ) from exc

            except ClientError as exc:
                last_error = exc
                error_code = exc.response.get("Error", {}).get("Code", "")
                error_msg = exc.response.get("Error", {}).get("Message", str(exc))

                if error_code in _TRANSIENT_ERROR_CODES:
                    logger.warning(
                        "Transient Bedrock error %s (attempt %d/%d): %s",
                        error_code, attempt, self.MAX_RETRIES, error_msg,
                    )
                    if attempt < self.MAX_RETRIES:
                        await asyncio.sleep(self.BASE_DELAY_SECONDS * (2 ** (attempt - 1)))
                        continue

                raise LegalReviewAPIError(
                    f"Bedrock error ({error_code}): {error_msg}"
                ) from exc

            except Exception as exc:
                last_error = exc
                logger.warning(
                    "Unexpected Bedrock error (attempt %d/%d): %s",
                    attempt, self.MAX_RETRIES, str(exc),
                )
                if attempt < self.MAX_RETRIES:
                    await asyncio.sleep(self.BASE_DELAY_SECONDS * (2 ** (attempt - 1)))
                    continue
                raise LegalReviewAPIError(
                    f"Unable to reach Bedrock after {self.MAX_RETRIES} attempts."
                ) from exc

        raise LegalReviewAPIError("Unexpected retry loop exit.") from last_error

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_system_prompt(self, command: str) -> str:
        """Combine the base legal review system prompt with command-specific instructions."""
        extra = _COMMAND_PROMPTS.get(command, "")
        if extra:
            return f"{_SYSTEM_PROMPT_BASE}\n\nAdditional instructions:\n{extra}"
        return _SYSTEM_PROMPT_BASE

    def _build_user_message(self, document_text: str) -> str:
        """Build the user message containing the document text."""
        return (
            "Please review the following document:\n\n"
            f"---\n{document_text}\n---"
        )

    def _parse_response(self, response: dict[str, Any]) -> LegalReviewResponse:
        """Parse a Bedrock Converse API response into findings."""
        # Extract text from the response
        output = response.get("output", {})
        message = output.get("message", {})
        content_blocks = message.get("content", [])

        text_parts: list[str] = []
        for block in content_blocks:
            if "text" in block:
                text_parts.append(block["text"])

        full_text = "\n".join(text_parts)

        findings = self._extract_findings_from_text(full_text)

        if not findings and full_text.strip():
            findings.append(
                LegalFinding(
                    risk_level="informational",
                    description=full_text.strip(),
                    raw={"source": "full_response"},
                )
            )

        return LegalReviewResponse(findings=findings, raw_response=response)

    def _extract_findings_from_text(self, text: str) -> list[LegalFinding]:
        """Extract structured JSON findings from response text."""
        findings: list[LegalFinding] = []

        for start_char, end_char in [("[", "]"), ("{", "}")]:
            start = text.find(start_char)
            end = text.rfind(end_char)
            if start != -1 and end > start:
                candidate = text[start : end + 1]
                try:
                    data = json.loads(candidate)
                    if isinstance(data, dict):
                        data = [data]
                    if isinstance(data, list):
                        for item in data:
                            if isinstance(item, dict):
                                finding = self._dict_to_finding(item)
                                if finding is not None:
                                    findings.append(finding)
                    if findings:
                        return findings
                except (json.JSONDecodeError, ValueError):
                    continue

        return findings

    @staticmethod
    def _dict_to_finding(d: dict[str, Any]) -> LegalFinding | None:
        """Convert a dict to a LegalFinding, or None if missing required fields."""
        description = d.get("description") or d.get("finding") or d.get("text") or ""
        if not description:
            return None

        risk_level = (
            d.get("risk_level") or d.get("severity") or d.get("risk") or "informational"
        )
        risk_level = risk_level.lower().strip()
        if risk_level not in VALID_RISK_LEVELS:
            risk_level = "informational"

        return LegalFinding(
            risk_level=risk_level,
            description=description.strip(),
            affected_clause=(d.get("affected_clause") or d.get("clause") or None),
            recommended_action=(
                d.get("recommended_action") or d.get("recommendation") or None
            ),
            raw=d,
        )
