"""Cognito JWT authentication and role-based authorization.

Verifies Cognito **ID tokens** (RS256) against the User Pool's JWKS and exposes
FastAPI dependencies for authentication (`get_current_user`) and admin-only
authorization (`require_admin`).

Sign-in method is deployment-configurable. Cognito's hosted UI is used by
default; when the target pool has a federated identity provider (SAML,
OIDC, or IAM Identity Center) and the deployment sets
``COGNITO_IDENTITY_PROVIDER`` to that provider's name, the SPA passes it
to the ``/oauth2/authorize`` call so users are deep-linked into that IdP
instead of the hosted-UI account chooser.

Only an ``admin`` Cognito group is provisioned by CDK on the fresh-account
path; on the BYO path the operator owns group membership. Membership in
any group whose name contains ``admin`` (case-insensitive) is treated as
ADMIN. Everyone else is a regular user.

Set ``AUTH_ENABLED=false`` to bypass verification entirely (useful for local
development or emergency rollback) — every request is then treated as an
admin-equivalent ``system`` user so enforcement can be toggled off without
breaking any endpoint.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from fastapi import Depends, Header, HTTPException

from app.config import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Issuer / JWKS configuration (derived from settings)
# ---------------------------------------------------------------------------

# Fail fast on startup rather than at first request: if auth enforcement is on
# (the default), the app has no usable identity provider without these two
# values, and there is no safe environment-agnostic default to fall back to.
if settings.auth_enabled and not (settings.cognito_user_pool_id and settings.cognito_client_id):
    raise RuntimeError(
        "AUTH_ENABLED is true but COGNITO_USER_POOL_ID / COGNITO_CLIENT_ID are "
        "not set. Set both env vars (see .env.example), or set AUTH_ENABLED=false "
        "for local development without Cognito."
    )

COGNITO_ISSUER = (
    f"https://cognito-idp.{settings.cognito_region}.amazonaws.com/"
    f"{settings.cognito_user_pool_id}"
)
JWKS_URL = f"{COGNITO_ISSUER}/.well-known/jwks.json"

# Cached PyJWKClient — created lazily on first verification and reused across
# requests so the JWKS is fetched once (PyJWKClient caches keys internally).
_jwk_client: Any = None


# ---------------------------------------------------------------------------
# CurrentUser model
# ---------------------------------------------------------------------------

@dataclass
class CurrentUser:
    """Authenticated caller derived from a verified Cognito ID token.

    ``is_admin`` and ``can_view_all`` are deliberately separate:
      - ``is_admin`` gates MUTATING/admin-only actions (require_admin) —
        true only for the ``admin`` group.
      - ``can_view_all`` gates READ-ONLY visibility scoping (whether the
        caller sees everyone's activity data or only their own) — true for
        ``admin``, ``payer``, or ``superuser`` groups, mirroring the
        frontend's ``canViewAll()`` role check (frontend/src/auth/cognito.ts).
        A payer/superuser can see all data but cannot perform any
        admin-only mutation.
    """

    username: str
    email: str
    groups: list[str] = field(default_factory=list)
    is_admin: bool = False
    can_view_all: bool = False
    identity: str = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_admin_group(groups: list[str]) -> bool:
    """Return True if any group name contains 'admin' (case-insensitive)."""
    return any("admin" in (g or "").lower() for g in groups)


# Group name substrings that grant read visibility into everyone's activity
# data (but NOT admin-only mutation rights). Mirrors the frontend's
# mapGroupsToRole()/canViewAll() in frontend/src/auth/cognito.ts — keep the
# two in sync if either changes.
_VIEW_ALL_GROUP_SUBSTRINGS = ("admin", "payer", "superuser")


def _can_view_all_group(groups: list[str]) -> bool:
    """Return True if any group name contains admin/payer/superuser (case-insensitive)."""
    lowered = [(g or "").lower() for g in groups]
    return any(
        substr in g for g in lowered for substr in _VIEW_ALL_GROUP_SUBSTRINGS
    )


def _get_jwk_client() -> Any:
    """Return a cached PyJWKClient for the User Pool JWKS endpoint."""
    global _jwk_client
    if _jwk_client is None:
        import jwt  # PyJWT

        _jwk_client = jwt.PyJWKClient(JWKS_URL)
    return _jwk_client


def _unauthorized(code: str, message: str) -> HTTPException:
    return HTTPException(status_code=401, detail={"code": code, "message": message})


def verify_token(token: str) -> dict[str, Any]:
    """Verify a Cognito ID token and return its decoded claims.

    Checks the signature against the User Pool JWKS (RS256), the issuer, the
    audience (app client id), expiry, and that ``token_use`` is ``id``.

    Raises:
        HTTPException(401): on any missing/invalid/expired token. No stack
            traces or library internals are leaked to the caller.
    """
    import jwt  # PyJWT

    try:
        signing_key = _get_jwk_client().get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=settings.cognito_client_id,
            issuer=COGNITO_ISSUER,
            options={"require": ["exp", "iss", "aud"]},
        )
    except jwt.ExpiredSignatureError:
        raise _unauthorized("TOKEN_EXPIRED", "Authentication token has expired.")
    except jwt.InvalidTokenError as exc:
        logger.warning("JWT verification failed: %s", type(exc).__name__)
        raise _unauthorized("INVALID_TOKEN", "Invalid authentication token.")
    except HTTPException:
        raise
    except Exception as exc:  # network/JWKS/key errors — never leak details
        logger.warning("JWT verification error: %s", type(exc).__name__)
        raise _unauthorized("INVALID_TOKEN", "Invalid authentication token.")

    if claims.get("token_use") != "id":
        raise _unauthorized("INVALID_TOKEN_USE", "Expected a Cognito ID token.")

    return claims


def build_current_user(claims: dict[str, Any]) -> CurrentUser:
    """Build a CurrentUser from verified ID-token claims."""
    groups = claims.get("cognito:groups") or []
    if isinstance(groups, str):
        groups = [groups]
    groups = list(groups)

    email = claims.get("email") or ""
    username = claims.get("cognito:username") or claims.get("sub") or email
    identity = email or username

    return CurrentUser(
        username=username,
        email=email,
        groups=groups,
        is_admin=_is_admin_group(groups),
        can_view_all=_can_view_all_group(groups),
        identity=identity,
    )


def _system_user() -> CurrentUser:
    """Admin-equivalent user used when auth is disabled via AUTH_ENABLED=false."""
    return CurrentUser(
        username="system",
        email="",
        groups=["admin"],
        is_admin=True,
        can_view_all=True,
        identity="system",
    )


# ---------------------------------------------------------------------------
# FastAPI dependencies
# ---------------------------------------------------------------------------

def get_current_user(authorization: Optional[str] = Header(None)) -> CurrentUser:
    """Authenticate the caller from an ``Authorization: Bearer <token>`` header.

    When ``settings.auth_enabled`` is False, verification is bypassed and an
    admin-equivalent ``system`` user is returned.
    """
    if not settings.auth_enabled:
        return _system_user()

    if not authorization:
        raise _unauthorized("MISSING_TOKEN", "Missing Authorization header.")

    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise _unauthorized(
            "INVALID_AUTH_HEADER",
            "Authorization header must be in the form 'Bearer <token>'.",
        )

    claims = verify_token(parts[1])
    return build_current_user(claims)


def require_admin(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    """Authorize admin-only access.

    Bypassed when ``auth_enabled`` is False since the system user is
    admin-equivalent.

    Raises:
        HTTPException(403): if the authenticated user is not an admin.
    """
    if not user.is_admin:
        raise HTTPException(
            status_code=403,
            detail={"code": "FORBIDDEN", "message": "Admin privileges required."},
        )
    return user


def require_view_all(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    """Authorize read access to organisation-wide data (admin/payer/superuser).

    Raises:
        HTTPException(403): if the caller may only see their own data.
    """
    if not user.can_view_all:
        raise HTTPException(
            status_code=403,
            detail={"code": "FORBIDDEN", "message": "Insufficient privileges."},
        )
    return user
