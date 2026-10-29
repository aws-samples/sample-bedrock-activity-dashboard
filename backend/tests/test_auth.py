"""Unit tests for Cognito JWT authentication and role-based authorization.

These tests exercise the auth logic in isolation (no network / no live JWKS).
Token verification is monkeypatched to inject decoded claims or raise, so the
dependency wiring, role mapping, bypass behavior, and per-user scoping can be
validated deterministically.
"""

import pytest
from fastapi import HTTPException

from app import auth
from app.auth import (
    CurrentUser,
    build_current_user,
    get_current_user,
    require_admin,
)


# ---------------------------------------------------------------------------
# (a) Role mapping
# ---------------------------------------------------------------------------

class TestRoleMapping:
    def test_group_named_admin_maps_to_admin(self):
        user = build_current_user({"cognito:groups": ["admin"], "email": "a@example.com"})
        assert user.is_admin is True

    def test_group_containing_admin_case_insensitive(self):
        for grp in ["Admins", "platform-ADMIN", "SuperAdmin"]:
            user = build_current_user({"cognito:groups": [grp], "email": "a@example.com"})
            assert user.is_admin is True, grp

    def test_non_admin_groups_map_to_regular_user(self):
        user = build_current_user({"cognito:groups": ["users", "payer"], "email": "u@example.com"})
        assert user.is_admin is False

    def test_no_groups_maps_to_regular_user(self):
        user = build_current_user({"email": "u@example.com"})
        assert user.is_admin is False
        assert user.groups == []

    def test_groups_as_single_string_is_normalized(self):
        user = build_current_user({"cognito:groups": "admin", "email": "a@example.com"})
        assert user.groups == ["admin"]
        assert user.is_admin is True

    def test_identity_prefers_email_then_username(self):
        with_email = build_current_user({"email": "e@example.com", "cognito:username": "bob"})
        assert with_email.identity == "e@example.com"

        no_email = build_current_user({"cognito:username": "bob"})
        assert no_email.identity == "bob"

        only_sub = build_current_user({"sub": "abc-123"})
        assert only_sub.identity == "abc-123"


# ---------------------------------------------------------------------------
# (b) get_current_user raises 401 on missing / invalid token
# ---------------------------------------------------------------------------

class TestGetCurrentUser401:
    def test_missing_authorization_header_raises_401(self, monkeypatch):
        monkeypatch.setattr(auth.settings, "auth_enabled", True)
        with pytest.raises(HTTPException) as exc:
            get_current_user(authorization=None)
        assert exc.value.status_code == 401
        assert exc.value.detail["code"] == "MISSING_TOKEN"

    def test_malformed_header_raises_401(self, monkeypatch):
        monkeypatch.setattr(auth.settings, "auth_enabled", True)
        with pytest.raises(HTTPException) as exc:
            get_current_user(authorization="Token abc")
        assert exc.value.status_code == 401
        assert exc.value.detail["code"] == "INVALID_AUTH_HEADER"

    def test_invalid_token_raises_401(self, monkeypatch):
        monkeypatch.setattr(auth.settings, "auth_enabled", True)

        def _raise(_token):
            raise auth._unauthorized("INVALID_TOKEN", "Invalid authentication token.")

        monkeypatch.setattr(auth, "verify_token", _raise)
        with pytest.raises(HTTPException) as exc:
            get_current_user(authorization="Bearer garbage")
        assert exc.value.status_code == 401
        assert exc.value.detail["code"] == "INVALID_TOKEN"

    def test_valid_token_builds_user(self, monkeypatch):
        monkeypatch.setattr(auth.settings, "auth_enabled", True)

        def _claims(_token):
            return {
                "token_use": "id",
                "email": "dev@example.com",
                "cognito:groups": ["admin"],
                "cognito:username": "dev",
            }

        monkeypatch.setattr(auth, "verify_token", _claims)
        user = get_current_user(authorization="Bearer good.token.here")
        assert user.identity == "dev@example.com"
        assert user.is_admin is True

    def test_verify_token_rejects_non_id_token(self, monkeypatch):
        # token_use != 'id' must be rejected as 401
        monkeypatch.setattr(
            auth, "_get_jwk_client", lambda: _FakeJwkClient()
        )

        import jwt as pyjwt

        monkeypatch.setattr(
            pyjwt, "decode", lambda *a, **k: {"token_use": "access"}
        )
        with pytest.raises(HTTPException) as exc:
            auth.verify_token("whatever")
        assert exc.value.status_code == 401
        assert exc.value.detail["code"] == "INVALID_TOKEN_USE"


class _FakeJwkClient:
    def get_signing_key_from_jwt(self, _token):
        class _K:
            key = "fake"

        return _K()


# ---------------------------------------------------------------------------
# (c) auth_enabled=False bypass returns an admin-equivalent user
# ---------------------------------------------------------------------------

class TestAuthDisabledBypass:
    def test_bypass_returns_system_admin(self, monkeypatch):
        monkeypatch.setattr(auth.settings, "auth_enabled", False)
        user = get_current_user(authorization=None)
        assert user.identity == "system"
        assert user.is_admin is True

    def test_bypass_ignores_missing_and_bad_headers(self, monkeypatch):
        monkeypatch.setattr(auth.settings, "auth_enabled", False)
        user = get_current_user(authorization="totally-not-a-bearer-token")
        assert user.is_admin is True
        assert user.username == "system"

    def test_require_admin_allows_bypass_user(self, monkeypatch):
        monkeypatch.setattr(auth.settings, "auth_enabled", False)
        user = get_current_user(authorization=None)
        # require_admin should pass through the admin-equivalent user
        assert require_admin(user=user) is user


# ---------------------------------------------------------------------------
# require_admin authorization
# ---------------------------------------------------------------------------

class TestRequireAdmin:
    def test_admin_allowed(self):
        admin = CurrentUser("a", "a@example.com", ["admin"], True, True, "a@example.com")
        assert require_admin(user=admin) is admin

    def test_non_admin_forbidden(self):
        regular = CurrentUser("u", "u@example.com", ["users"], False, False, "u@example.com")
        with pytest.raises(HTTPException) as exc:
            require_admin(user=regular)
        assert exc.value.status_code == 403
        assert exc.value.detail["code"] == "FORBIDDEN"


# ---------------------------------------------------------------------------
# (d) Best-effort per-user scoping in activity + prompts
# ---------------------------------------------------------------------------

class TestUserScoping:
    def test_activity_non_admin_forced_to_identity(self):
        from app.routes.activity import _scope_to_user

        regular = CurrentUser("u", "u@example.com", ["users"], False, False, "u@example.com")
        params = {"user_identities": ["someone-else@example.com"]}
        scoped = _scope_to_user(params, regular)
        assert scoped["user_identities"] == ["u@example.com"]

    def test_activity_admin_keeps_client_filters(self):
        from app.routes.activity import _scope_to_user

        admin = CurrentUser("a", "a@example.com", ["admin"], True, True, "a@example.com")
        params = {"user_identities": ["someone-else@example.com"]}
        scoped = _scope_to_user(params, admin)
        assert scoped["user_identities"] == ["someone-else@example.com"]

    def test_prompts_non_admin_forced_to_identity(self):
        from app.routes.prompts import _scope_to_user

        regular = CurrentUser("bob", "", ["users"], False, False, "bob")
        params = {"user_identities": []}
        scoped = _scope_to_user(params, regular)
        assert scoped["user_identities"] == ["bob"]

    def test_prompts_admin_unfiltered(self):
        from app.routes.prompts import _scope_to_user

        admin = CurrentUser("a", "a@example.com", ["Admins"], True, True, "a@example.com")
        params = {"user_identities": []}
        scoped = _scope_to_user(params, admin)
        assert scoped["user_identities"] == []
