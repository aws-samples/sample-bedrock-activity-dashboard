"""Shared pytest fixtures for the backend test suite."""

import os

# Tests never verify real Cognito tokens (route tests override
# `get_current_user` below), and the suite runs with no COGNITO_USER_POOL_ID /
# COGNITO_CLIENT_ID configured. Opt into the documented AUTH_ENABLED=false
# bypass (app.auth) before any app module is imported, so app.config's
# fail-fast check doesn't reject a Cognito-less test environment. Uses
# setdefault so a developer can still override it to exercise the real
# verification path locally.
os.environ.setdefault("AUTH_ENABLED", "false")

import pytest

from app.auth import CurrentUser, get_current_user
from app.services.cloudtrail_service import clear_activity_cache
from app.services.cost_service import clear_pricing_cache


@pytest.fixture(autouse=True)
def _clear_caches():
    """Reset process-wide TTL caches around every test.

    The raw-ingestion and pricing caches are module-level singletons shared
    across the process. Clearing them before and after each test keeps tests
    hermetic (no stale cross-test cache hits) without weakening the caches in
    production.
    """
    clear_activity_cache()
    clear_pricing_cache()
    yield
    clear_activity_cache()
    clear_pricing_cache()


def _test_admin_user() -> CurrentUser:
    """An admin-equivalent authenticated caller used to satisfy the router-level
    ``get_current_user`` dependency (and the ``require_admin`` check that builds
    on it) in route tests."""
    return CurrentUser(
        username="test-admin",
        email="test-admin@example.com",
        groups=["admin"],
        is_admin=True,
        can_view_all=True,
        identity="test-admin@example.com",
    )


@pytest.fixture(autouse=True)
def _override_auth():
    """Authenticate the shared FastAPI app for route tests.

    All API routers are registered in ``main.py`` behind a router-level
    ``Depends(get_current_user)`` (Cognito ID-token verification), so a bare
    ``TestClient`` request returns 401. Route tests use the real ``main.app``
    singleton, so we override the auth dependency here — the standard FastAPI
    testing pattern — to inject an authenticated admin user. Tests that build
    their own app (e.g. the v2 policies routes) override auth locally instead.
    """
    from main import app

    app.dependency_overrides[get_current_user] = _test_admin_user
    yield
    app.dependency_overrides.pop(get_current_user, None)
