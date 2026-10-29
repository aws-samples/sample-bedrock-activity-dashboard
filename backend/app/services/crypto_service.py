"""Symmetric at-rest encryption for sensitive user-generated content.

Wraps the ``cryptography`` library's Fernet implementation (AES-128-CBC with
an HMAC-SHA256 authentication tag) with:

- A deployment-friendly key source — a 32-byte URL-safe-base64 Fernet key
  pulled from ``settings.prompt_history_encryption_key``, which in turn is
  populated from AWS Secrets Manager by the Backend CDK stack. The service
  degrades gracefully when the key is missing: writes fall back to plaintext
  and the admin UI surfaces an explicit warning.

- A versioned, self-describing ciphertext envelope (``enc:v1:<token>``)
  so legacy plaintext rows in the DB keep rendering correctly after the
  encryption toggle is flipped on, and so a future key-wrap / algorithm
  change can be rolled out without a data migration.

This module is deliberately standalone — no SQLAlchemy, no FastAPI — so it
can be exercised from unit tests without any infrastructure.
"""

from __future__ import annotations

import base64
import hashlib
import logging
from typing import Final

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings

logger = logging.getLogger(__name__)

# Envelope prefix identifies encrypted payloads so a reader can distinguish
# them from legacy plaintext without reaching for a separate "is_encrypted"
# column. ``v1`` reserves room for a future rotation / algorithm swap.
_ENCRYPTED_PREFIX: Final[str] = "enc:v1:"


class PromptHistoryCrypto:
    """Thin stateful wrapper around ``Fernet``.

    Instantiate once per process (an app-wide singleton; see
    ``get_crypto()`` below). The Fernet instance is lazily built so startup
    doesn't fail just because a key hasn't been configured yet — the service
    can still boot and serve the Settings UI for the admin to configure the
    key, we just don't persist new encrypted content until it's set.
    """

    def __init__(self, key: str | None = None) -> None:
        self._key_material: str = (
            key if key is not None else settings.prompt_history_encryption_key
        ).strip()
        self._fernet: Fernet | None = None
        self._init_error: str | None = None

        if self._key_material:
            self._fernet, self._init_error = self._build_fernet(self._key_material)
            if self._init_error:
                logger.error(
                    "Prompt history encryption key could not be loaded; "
                    "writes will fall back to plaintext. Error: %s",
                    self._init_error,
                )

    @staticmethod
    def _build_fernet(material: str) -> tuple[Fernet | None, str | None]:
        """Return a ``Fernet`` instance from ``material``.

        Accepts either (a) a valid 44-char URL-safe-base64 Fernet key (what
        ``Fernet.generate_key()`` and the local dev helper produce), or (b)
        any other string, which is deterministically derived to a 32-byte
        key via SHA-256. Case (b) is what makes the CDK Secrets Manager
        ``generateSecretString`` path work without needing a Lambda-backed
        custom resource — the service accepts whatever random string the
        secret contains and derives a stable key from it. Rotation still
        works: a different Secrets Manager value yields a different derived
        key, which is treated as a key rotation by ``decrypt()``.
        """
        # First try the material as a native Fernet key — if the operator
        # explicitly set a Fernet.generate_key() value, use it directly so
        # ciphertexts stay portable across environments that share the key.
        try:
            return Fernet(material.encode("utf-8")), None
        except Exception:  # noqa: BLE001 — fall through to derivation
            pass

        try:
            digest = hashlib.sha256(material.encode("utf-8")).digest()
            derived = base64.urlsafe_b64encode(digest)
            logger.info(
                "Prompt history encryption key material was not a native "
                "Fernet key; derived a 32-byte key via SHA-256."
            )
            return Fernet(derived), None
        except Exception as exc:  # noqa: BLE001
            return None, f"could not derive Fernet key from material: {exc!s}"

    # ------------------------------------------------------------------
    # Introspection — surfaced via the admin Settings route so the UI can
    # show the operator exactly why encryption is active or not.
    # ------------------------------------------------------------------

    @property
    def is_available(self) -> bool:
        """True when a valid key is loaded and encrypt/decrypt will work."""
        return self._fernet is not None

    @property
    def init_error(self) -> str | None:
        """A human-readable reason encryption is unavailable, if any."""
        if self._fernet is not None:
            return None
        if not self._key_material:
            return "PROMPT_HISTORY_ENCRYPTION_KEY is not set"
        return self._init_error

    # ------------------------------------------------------------------
    # Core API
    # ------------------------------------------------------------------

    def encrypt(self, plaintext: str) -> str:
        """Encrypt ``plaintext`` and return a self-describing envelope.

        Returns a string of the form ``"enc:v1:<fernet-token>"``. If
        encryption is unavailable (no key, or an invalid key), returns the
        plaintext unchanged so callers can always persist content — the
        admin is responsible for restoring the key.

        Non-string / empty inputs are returned as-is: empty strings aren't
        worth paying the ciphertext overhead for, and ``None``-like values
        shouldn't reach this helper in the first place.
        """
        if not plaintext:
            return plaintext
        if self._fernet is None:
            return plaintext
        try:
            token = self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")
            return f"{_ENCRYPTED_PREFIX}{token}"
        except Exception:  # noqa: BLE001
            logger.exception(
                "Fernet.encrypt() failed; falling back to plaintext for this record"
            )
            return plaintext

    def decrypt(self, stored: str) -> str:
        """Return the plaintext content of a value previously passed through
        ``encrypt()``, or the value unchanged if it was never encrypted.

        Three cases:
        1. Plaintext legacy rows (no envelope prefix) — returned verbatim.
        2. Envelope-prefixed rows and encryption is available — decrypted.
        3. Envelope-prefixed rows but encryption is unavailable OR the
           token can't be decrypted with the current key — a placeholder
           string is returned (``"[encrypted content — key unavailable]"``)
           so the UI never shows garbled ciphertext. This also happens
           after a key rotation if the operator has not re-encrypted older
           rows with the new key.
        """
        if not stored or not isinstance(stored, str):
            return stored
        if not stored.startswith(_ENCRYPTED_PREFIX):
            return stored
        token = stored[len(_ENCRYPTED_PREFIX):]
        if self._fernet is None:
            return "[encrypted content \u2014 encryption key unavailable]"
        try:
            return self._fernet.decrypt(token.encode("ascii")).decode("utf-8")
        except InvalidToken:
            logger.warning(
                "Prompt history ciphertext could not be decrypted with the "
                "current key (possibly rotated or wrong key loaded)"
            )
            return "[encrypted content \u2014 decryption failed]"
        except Exception:  # noqa: BLE001
            logger.exception("Unexpected failure decrypting prompt history row")
            return "[encrypted content \u2014 decryption error]"


# ---------------------------------------------------------------------------
# Module-level singleton accessor
# ---------------------------------------------------------------------------

_singleton: PromptHistoryCrypto | None = None


def get_crypto() -> PromptHistoryCrypto:
    """Return the process-wide ``PromptHistoryCrypto`` singleton.

    Lazy construction keeps module import side-effect-free so tests can
    monkey-patch ``settings.prompt_history_encryption_key`` and then call
    ``reset_crypto()`` to pick up the new value.
    """
    global _singleton
    if _singleton is None:
        _singleton = PromptHistoryCrypto()
    return _singleton


def reset_crypto() -> None:
    """Discard the cached singleton. Intended for tests and key rotation."""
    global _singleton
    _singleton = None


def is_ciphertext(stored: str | None) -> bool:
    """Return True if ``stored`` looks like a value produced by ``encrypt()``.

    Useful for migration/backfill tooling that needs to decide whether a row
    is already in the new envelope without instantiating the Fernet layer.
    """
    return isinstance(stored, str) and stored.startswith(_ENCRYPTED_PREFIX)
