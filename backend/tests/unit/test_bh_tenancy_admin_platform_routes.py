"""tenancy-rbac-admin-5: platform-wide health/incident/alert routes are platform_admin only.

These tables/globals (incidents, worker_status, alert settings, global call
counts) are not tenant-owned, so ``require_admin`` (which admits every
self-signup tenant_admin) is the wrong gate.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.dependencies import CurrentUser, get_current_user, get_db_client
from app.api.v1.endpoints.admin.base import router as base_router
from app.api.v1.endpoints.admin.health import router as health_router

INC = "00000000-0000-0000-0000-000000000001"

ROUTES = [
    ("GET", "/alerts/settings", None),
    ("PUT", "/alerts/settings", {"email_enabled": False}),
    ("GET", "/incidents", None),
    ("GET", f"/incidents/{INC}", None),
    ("POST", f"/incidents/{INC}/acknowledge", None),
    ("POST", f"/incidents/{INC}/resolve", None),
    ("GET", "/health/workers", None),
    ("GET", "/health/detailed", None),
    ("GET", "/health/queues", None),
    ("GET", "/health/database", None),
    ("GET", "/dashboard/stats", None),
]


def _client(role: str) -> TestClient:
    app = FastAPI()
    app.include_router(base_router)
    app.include_router(health_router)
    user = CurrentUser(id="u-1", email="u@example.com", tenant_id="t-1", role=role)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db_client] = lambda: MagicMock()
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize("role", ["tenant_admin", "partner_admin"])
@pytest.mark.parametrize("method,path,body", ROUTES)
def test_customer_admins_are_refused(role, method, path, body):
    resp = _client(role).request(method, path, json=body)
    assert resp.status_code == 403, (method, path, resp.status_code, resp.text[:200])


@pytest.mark.parametrize("method,path,body", ROUTES)
def test_platform_admin_passes_the_gate(method, path, body):
    resp = _client("platform_admin").request(method, path, json=body)
    assert resp.status_code != 403, (method, path, resp.text[:200])
