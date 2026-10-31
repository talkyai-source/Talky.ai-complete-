"""Switched-off behaviour and permission gates, decided before any database work."""
from uuid import uuid4

import httpx
import pytest

from app.api.v1.dependencies import CurrentUser, get_current_user, get_db_pool
from tests.helpers.support_db import build_app

TENANT = str(uuid4())

CUSTOMER_ROUTES = [
    ("GET", "/support/articles/search?q=calls"),
    ("POST", "/support/conversations"),
    ("GET", f"/support/conversations/{uuid4()}"),
    ("POST", f"/support/conversations/{uuid4()}/messages"),
    ("POST", f"/support/conversations/{uuid4()}/feedback"),
    ("POST", f"/support/conversations/{uuid4()}/escalate"),
    ("POST", "/support/attachments"),
    ("GET", f"/support/attachments/{uuid4()}"),
    ("POST", "/support/tickets"),
    ("GET", "/support/tickets"),
    ("GET", f"/support/tickets/{uuid4()}"),
    ("POST", f"/support/tickets/{uuid4()}/messages"),
    ("POST", f"/support/tickets/{uuid4()}/resolve"),
    ("POST", f"/support/tickets/{uuid4()}/reopen"),
    ("POST", f"/support/tickets/{uuid4()}/csat"),
    ("GET", "/support/unread"),
]
STAFF_ROUTES = [
    ("GET", "/admin/support/tickets"),
    ("GET", f"/admin/support/tickets/{uuid4()}"),
    ("PATCH", f"/admin/support/tickets/{uuid4()}"),
    ("POST", f"/admin/support/tickets/{uuid4()}/messages"),
    ("GET", "/admin/support/articles"),
    ("POST", "/admin/support/articles"),
    ("PATCH", f"/admin/support/articles/{uuid4()}"),
    ("GET", "/admin/support/metrics"),
]


class _NoPool:
    """Any database use fails loudly: these gates must fire first."""
    def acquire(self, *args, **kwargs):
        raise AssertionError("database touched before the gate")


def _app(role: str, tenant_id: str | None = TENANT):
    app = build_app()
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        id=str(uuid4()), email="person@example.invalid", role=role, tenant_id=tenant_id)
    app.dependency_overrides[get_db_pool] = lambda: _NoPool()
    return app


async def _call(app, method, path):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        return await client.request(method, f"/api/v1{path}", json={})


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for name in ("SUPPORT_ENABLED", "SUPPORT_TENANT_ALLOWLIST"):
        monkeypatch.delenv(name, raising=False)


async def test_config_reports_disabled_by_default():
    response = await _call(_app("user"), "GET", "/support/config")
    assert response.status_code == 200
    assert response.json()["enabled"] is False and response.json()["bot_enabled"] is False


@pytest.mark.parametrize("method,path", CUSTOMER_ROUTES + STAFF_ROUTES)
async def test_every_other_route_is_404_while_switched_off(method, path):
    role = "platform_admin" if path.startswith("/admin") else "user"
    response = await _call(_app(role), method, path)
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "not_found"


@pytest.mark.parametrize("method,path", CUSTOMER_ROUTES)
async def test_tenants_outside_the_pilot_list_get_404(monkeypatch, method, path):
    monkeypatch.setenv("SUPPORT_ENABLED", "true")
    monkeypatch.setenv("SUPPORT_TENANT_ALLOWLIST", str(uuid4()))
    response = await _call(_app("user"), method, path)
    assert response.status_code == 404
    config = await _call(_app("user"), "GET", "/support/config")
    assert config.json()["enabled"] is False


@pytest.mark.parametrize("method,path", [r for r in CUSTOMER_ROUTES if "attachments" not in r[1]])
async def test_readonly_role_is_403_on_customer_routes(monkeypatch, method, path):
    monkeypatch.setenv("SUPPORT_ENABLED", "true")
    response = await _call(_app("readonly"), method, path)
    assert response.status_code == 403
    assert "support:report" in response.json()["error"]["message"]


@pytest.mark.parametrize("role", ["user", "tenant_admin", "partner_admin", "campaign_manager", "readonly"])
@pytest.mark.parametrize("method,path", STAFF_ROUTES)
async def test_non_platform_roles_are_403_on_staff_routes(monkeypatch, role, method, path):
    monkeypatch.setenv("SUPPORT_ENABLED", "true")
    response = await _call(_app(role), method, path)
    assert response.status_code == 403


async def test_config_lists_permissions_categories_and_attachment_rules(monkeypatch):
    monkeypatch.setenv("SUPPORT_ENABLED", "true")
    member = (await _call(_app("user"), "GET", "/support/config")).json()
    assert member["enabled"] is True and member["bot_enabled"] is True
    assert member["permissions"] == {"report": True, "read_team": False}
    assert {c["id"] for c in member["categories"]} >= {"billing", "privacy_data", "other"}
    assert member["attachments"] == {"max_mb": 10, "max_per_message": 5,
                                     "types": ["png", "jpg", "webp", "pdf", "txt", "log", "csv"]}
    admin = (await _call(_app("tenant_admin"), "GET", "/support/config")).json()
    assert admin["permissions"] == {"report": True, "read_team": True}
