"""Permission gates added 2026-11-20 after the role-permission review.

Routes that only checked "signed in" now require the permission the role
model already defines. Wiring tests walk each route's resolved dependant tree;
behaviour tests call the handlers with restricted roles.
"""
from __future__ import annotations

import io
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, UploadFile

from app.api.v1.dependencies import CurrentUser
from app.core.security.rbac import Permission, require_permission

from app.api.v1.endpoints import alerts as alerts_mod
from app.api.v1.endpoints import call_feedback as feedback_mod
from app.api.v1.endpoints import dnc as dnc_mod
from app.api.v1.endpoints import telephony_providers as tp_mod
from app.api.v1.endpoints.alerts import router as alerts_router
from app.api.v1.endpoints.assistant_config import router as assistant_router
from app.api.v1.endpoints.campaigns import router as campaigns_router
from app.api.v1.endpoints.connectors import router as connectors_router
from app.api.v1.endpoints.contact_lists import router as contact_lists_router
from app.api.v1.endpoints.contact_permission_confirm import (
    router as contact_permission_router,
)
from app.api.v1.endpoints.dnc import router as dnc_router
from app.api.v1.endpoints.tenant_ai_credentials import router as credentials_router
from app.domain.services import call_feedback_service as fb_service

TENANT = "11111111-1111-1111-1111-111111111111"


def _user(role: str, uid: str = "22222222-2222-2222-2222-222222222222") -> CurrentUser:
    return CurrentUser(id=uid, email="u@example.com", tenant_id=TENANT, role=role)


class _Req:
    headers: dict = {}
    path_params: dict = {}
    query_params: dict = {}


async def _admitted(perm: Permission, role: str) -> bool:
    try:
        await require_permission(perm)(request=_Req(), user=_user(role))
    except HTTPException as exc:
        assert exc.status_code == 403
        return False
    return True


def _gates(router, method: str, path: str):
    perms: set = set()
    admin = False
    seen: set = set()

    def walk(dep):
        nonlocal admin
        if id(dep) in seen:
            return
        seen.add(id(dep))
        call = getattr(dep, "call", None)
        name = getattr(call, "__name__", "")
        if name == "permission_checker" and getattr(call, "__closure__", None):
            vals = dict(zip(call.__code__.co_freevars, (c.cell_contents for c in call.__closure__)))
            if isinstance(vals.get("permission"), Permission):
                perms.add(vals["permission"])
        if name in ("require_admin", "require_admin_tenant"):
            admin = True
        for d in getattr(dep, "dependencies", []) or []:
            walk(d)

    for route in router.routes:
        if route.path == path and method in route.methods:
            for d in route.dependant.dependencies:
                walk(d)
            return perms, admin
    raise AssertionError(f"route not found: {method} {path}")


# ---------------------------------------------------------------- wiring

_PERM_ROUTES = [
    (campaigns_router, "GET", "/campaigns/", {Permission.CAMPAIGNS_READ}),
    (campaigns_router, "GET", "/campaigns/{campaign_id}", {Permission.CAMPAIGNS_READ}),
    (campaigns_router, "GET", "/campaigns/{campaign_id}/calls",
     {Permission.CAMPAIGNS_READ, Permission.CALLS_READ}),
    (campaigns_router, "GET", "/campaigns/{campaign_id}/jobs", {Permission.CAMPAIGNS_READ}),
    (campaigns_router, "GET", "/campaigns/{campaign_id}/stats", {Permission.CAMPAIGNS_READ}),
    (campaigns_router, "GET", "/campaigns/{campaign_id}/contacts", {Permission.CAMPAIGNS_READ}),
    (contact_lists_router, "GET", "/campaigns/{campaign_id}/contact-lists", {Permission.CAMPAIGNS_READ}),
    (contact_permission_router, "GET", "/campaigns/{campaign_id}/contact-permission",
     {Permission.CAMPAIGNS_READ}),
    (connectors_router, "GET", "/connectors", {Permission.CONNECTORS_READ}),
    (connectors_router, "GET", "/connectors/status", {Permission.CONNECTORS_READ}),
    (connectors_router, "GET", "/connectors/{connector_id}", {Permission.CONNECTORS_READ}),
    (dnc_router, "GET", "/dnc/", {Permission.CALLS_READ}),
    (dnc_router, "GET", "/dnc/check", {Permission.CALLS_READ}),
]


@pytest.mark.parametrize("router,method,path,expected", _PERM_ROUTES)
def test_route_requires_permission(router, method, path, expected):
    perms, _ = _gates(router, method, path)
    assert expected <= perms, f"{method} {path} enforces {perms}, wanted {expected}"


@pytest.mark.parametrize("router,method,path", [
    (alerts_router, "POST", "/alerts/{alert_id}/ack"),
    (alerts_router, "POST", "/alerts/{alert_id}/resolve"),
    (assistant_router, "PUT", "/assistant/model"),
    (credentials_router, "GET", "/tenant-ai-credentials/"),
])
def test_route_requires_admin(router, method, path):
    candidates = [r.path for r in router.routes if method in getattr(r, "methods", ())]
    if path not in candidates:
        path = next(p for p in candidates if p.rstrip("/").endswith(path.rstrip("/").split("/")[-1]))
    _, admin = _gates(router, method, path)
    assert admin, f"{method} {path} lacks the admin dependency"


@pytest.mark.parametrize("perm,role,expected", [
    (Permission.CAMPAIGNS_READ, "billing_user", False),
    (Permission.CAMPAIGNS_READ, "readonly", True),
    (Permission.CAMPAIGNS_READ, "agent", True),
    (Permission.CALLS_READ, "billing_user", False),
    (Permission.CONNECTORS_READ, "billing_user", False),
    (Permission.CONNECTORS_READ, "readonly", True),
])
async def test_role_defaults_for_new_gates(perm, role, expected):
    assert await _admitted(perm, role) is expected


# ---------------------------------------------------------------- alerts

class _Conn:
    def __init__(self, rows):
        self.rows = list(rows)
        self.sql: list[str] = []

    async def fetchrow(self, sql, *a):
        self.sql.append(sql)
        return self.rows.pop(0) if self.rows else None

    async def execute(self, *a):
        return None


class _NoAudit:
    async def lock_transaction(self, conn):
        return None

    async def log_in_transaction(self, conn, **kw):
        raise AssertionError("no audit record expected on a refused call")


def _patch_alerts(monkeypatch, conn):
    @asynccontextmanager
    async def fake(pool, tenant):
        yield conn

    monkeypatch.setattr(alerts_mod, "acquire_with_tenant", fake)
    return SimpleNamespace(pool=object())


@pytest.mark.parametrize("which", ["ack", "resolve"])
async def test_platform_raised_alert_refused_for_tenant(monkeypatch, which):
    audit = _NoAudit()
    conn = _Conn([None, {"detection_source": "telephony_rate_limiter"}])
    client = _patch_alerts(monkeypatch, conn)
    with pytest.raises(HTTPException) as ei:
        if which == "ack":
            await alerts_mod.ack_alert("a1", alerts_mod.AckRequest(), _user("tenant_admin"), client, audit)
        else:
            await alerts_mod.resolve_alert(
                "a1", alerts_mod.ResolveRequest(resolution_notes="n"), _user("tenant_admin"), client, audit
            )
    assert ei.value.status_code == 403
    assert "tenant_id = $2" in conn.sql[0]
    assert "telephony_rate_limiter" in conn.sql[0]


async def test_missing_alert_still_404(monkeypatch):
    conn = _Conn([None, None])
    client = _patch_alerts(monkeypatch, conn)
    audit = _NoAudit()
    with pytest.raises(HTTPException) as ei:
        await alerts_mod.ack_alert("a1", alerts_mod.AckRequest(), _user("tenant_admin"), client, audit)
    assert ei.value.status_code == 404


# ------------------------------------------------------------ call feedback

_MSG = "Only the person who left this voice note, or an administrator, can replace it."
OWNER = "33333333-3333-3333-3333-333333333333"
OTHER = "44444444-4444-4444-4444-444444444444"


class _Repo:
    async def get_call_context(self, tenant_id, call_id):
        return {"id": call_id, "campaign_id": None}

    async def get_for_call(self, tenant_id, call_id):
        return {"id": "f1", "created_by": OWNER, "audio_sha256": "old"}


def _service(monkeypatch):
    svc = fb_service.CallFeedbackService(object(), repository=_Repo(), storage=object(), transcriber=object())
    calls = []

    async def fake_replace(**kw):
        calls.append(kw)
        return {"replaced": True}

    monkeypatch.setattr(svc, "_replace_existing", fake_replace)
    return svc, calls


async def test_replace_colleagues_note_refused(monkeypatch):
    svc, calls = _service(monkeypatch)
    with pytest.raises(fb_service.FeedbackReplaceNotAllowedError):
        await svc.submit(tenant_id=TENANT, call_id="c", created_by=OTHER, audio=b"x",
                         mime_type="audio/webm", replace=True)
    assert calls == []


async def test_replace_own_note_and_admin_override_allowed(monkeypatch):
    svc, calls = _service(monkeypatch)
    await svc.submit(tenant_id=TENANT, call_id="c", created_by=OWNER, audio=b"x",
                     mime_type="audio/webm", replace=True)
    await svc.submit(tenant_id=TENANT, call_id="c", created_by=OTHER, audio=b"x",
                     mime_type="audio/webm", replace=True, can_replace_others=True)
    assert len(calls) == 2


def test_feedback_refusal_maps_to_403_with_message():
    exc = feedback_mod._expected_error(fb_service.FeedbackReplaceNotAllowedError(_MSG))
    assert exc.status_code == 403 and exc.detail == _MSG


class _Svc:
    def __init__(self):
        self.kw = None

    async def submit(self, **kw):
        self.kw = kw
        return {}


@pytest.mark.parametrize("role,expected", [("agent", False), ("tenant_admin", True)])
async def test_endpoint_passes_delete_permission_to_service(monkeypatch, role, expected):
    svc = _Svc()
    monkeypatch.setattr(feedback_mod, "_response", lambda row: row)
    upload = UploadFile(file=io.BytesIO(b"abc"), filename="n.webm",
                        headers={"content-type": "audio/webm"})
    await feedback_mod.submit_call_feedback(
        request=_Req(), call_id="c", audio=upload, duration_seconds=1.0,
        replace=True, current_user=_user(role), service=svc,
    )
    assert svc.kw["can_replace_others"] is expected


# --------------------------------------------------------------------- dnc

class _DncSvc:
    seen = None

    def __init__(self, pool):
        pass

    async def list_for_tenant(self, tenant_id, *, include_global=False, limit=200):
        type(self).seen = include_global
        return []


@pytest.mark.parametrize("role,expected", [
    ("tenant_admin", False), ("user", False), ("readonly", False), ("platform_admin", True),
])
async def test_dnc_include_global_only_for_platform_admin(monkeypatch, role, expected):
    monkeypatch.setattr(dnc_mod, "DNCService", _DncSvc)
    await dnc_mod.list_dnc_entries(include_global=True, limit=10,
                                   current_user=_user(role), db_pool=object())
    assert _DncSvc.seen is expected


# ------------------------------------------------------ telephony providers

async def test_list_providers_filters_last_test_result(monkeypatch):
    class C:
        async def execute(self, *a):
            return None

        async def fetchrow(self, *a):
            return {"active_telephony_provider": "twilio"}

        async def fetch(self, *a):
            return [{
                "provider": "twilio", "label": "L", "from_number": "+1", "status": "active",
                "last_tested_at": None,
                "last_test_result": '{"ok": false, "status_code": 401, "error": "bad key sk-SECRET"}',
            }]

        def transaction(self):
            @asynccontextmanager
            async def t():
                yield

            return t()

    @asynccontextmanager
    async def fake_conn(pool):
        yield C()

    monkeypatch.setattr(tp_mod, "acquire_connection", fake_conn)
    out = await tp_mod.list_providers(current_user=_user("tenant_admin"), db_pool=object())
    result = out.providers[0].last_test_result
    assert result == {"ok": False, "status_code": 401}
    assert "SECRET" not in str(out.model_dump())
