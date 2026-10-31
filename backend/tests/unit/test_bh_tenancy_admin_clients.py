"""tenancy-rbac-admin-10: /clients enforces RBAC and a mandatory tenant predicate."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.dependencies import CurrentUser, get_current_user, get_db_client
from app.api.v1.endpoints.clients import router as clients_router

CID = "55555555-5555-5555-5555-555555555555"
ROW = {"id": CID, "name": "Acme", "company": None, "phone": None, "email": None, "tags": []}

CALLS = [
    ("GET", "/clients/", None),
    ("GET", f"/clients/{CID}", None),
    ("POST", "/clients/", {"name": "Acme"}),
    ("DELETE", f"/clients/{CID}", None),
]
MUTATIONS = [c for c in CALLS if c[0] in ("POST", "DELETE")]


def _db(path: str = ""):
    """Chainable fake: every builder method returns itself; execute() yields the row(s)."""
    db = MagicMock()
    for name in ("table", "select", "eq", "order", "single", "insert", "delete"):
        getattr(db, name).return_value = db
    is_single_get = path.rstrip("/").endswith(CID)
    db.execute.return_value = SimpleNamespace(data=ROW if is_single_get else [ROW])
    return db


def _client(role: str, tenant_id: str | None, db=None) -> TestClient:
    app = FastAPI()
    app.include_router(clients_router)
    user = CurrentUser(id="u-1", email="u@example.com", tenant_id=tenant_id, role=role)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db_client] = lambda: db or _db()
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize("role", ["readonly", "agent", "billing_user"])
@pytest.mark.parametrize("method,path,body", MUTATIONS)
def test_roles_without_campaign_update_cannot_create_or_delete(role, method, path, body):
    db = _db()
    resp = _client(role, "t-1", db).request(method, path, json=body)
    assert resp.status_code == 403, (role, method, resp.status_code)
    db.insert.assert_not_called()
    db.delete.assert_not_called()


@pytest.mark.parametrize("method,path,body", CALLS)
def test_tenantless_caller_is_refused_instead_of_querying_every_tenant(method, path, body):
    db = _db()
    resp = _client("platform_admin", None, db).request(method, path, json=body)
    assert resp.status_code == 403, (method, resp.status_code)
    db.execute.assert_not_called()


@pytest.mark.parametrize("method,path,body", CALLS)
def test_tenant_admin_still_works_and_every_query_is_tenant_scoped(method, path, body):
    db = _db(path if method == "GET" else "")
    resp = _client("tenant_admin", "t-1", db).request(method, path, json=body)
    assert resp.status_code != 403, (method, resp.status_code, resp.text[:200])
    if method in ("GET", "DELETE"):
        db.eq.assert_any_call("tenant_id", "t-1")
    else:
        assert db.insert.call_args.args[0]["tenant_id"] == "t-1"
