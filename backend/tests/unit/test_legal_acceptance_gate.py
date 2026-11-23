"""The API refuses un-accepted tenant users when LEGAL_ACCEPTANCE_REQUIRED is on.

Everything runs through the real ``get_current_user`` with synthetic storage.
"""
import logging
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi import Depends, FastAPI
from fastapi.routing import iter_route_contexts

from app.api.v1 import dependencies as dep
from app.api.v1.routes import api_router
from app.core import config, jwt_security
from app.core.security import legal_gate, principal
from app.domain.services import legal_acceptance

TENANT = "aabbccdd-1234-4abc-8def-0123456789ab"
USER = "bbccddee-1234-4abc-8def-0123456789ab"
START = ("POST", "/api/v1/campaigns/{campaign_id}/start")
DB_FAILURE_DETAIL = {"detail": "Account verification is temporarily unavailable"}


def _routes_for_prefixes():
    app = FastAPI()
    app.include_router(api_router, prefix="/api/v1")
    found = set()
    for route in iter_route_contexts(app.routes):
        for method in (getattr(route, "methods", None) or ()):
            if method not in ("HEAD", "OPTIONS"):
                found.add((method, route.path))
    return found


REAL_ROUTES = _routes_for_prefixes()
PREFIX_ROUTES = sorted(r for r in REAL_ROUTES if r[1].startswith(legal_gate.OPEN_PREFIXES))
OPEN_CASES = [(m, p) for m, p, _ in legal_gate.OPEN_ROUTES] + PREFIX_ROUTES


@pytest.fixture
def gate(monkeypatch):
    legal_gate._accepted.clear()
    monkeypatch.setattr(legal_gate, "_unset_versions_logged", False)
    monkeypatch.setattr(jwt_security, "_require_secret", lambda: "synthetic-test-secret")
    monkeypatch.setattr(dep, "decode_and_validate_token", lambda token: {"sub": USER, "sid": "s1"})
    state = SimpleNamespace(
        required=True, terms="t1", privacy="p1", owed=True, fail=False,
        reads=0, db_opens=0, role="tenant_admin", tenant=TENANT,
        settings_reads=0,
    )

    def settings():
        state.settings_reads += 1
        return SimpleNamespace(
            legal_acceptance_required=state.required,
            legal_terms_version=state.terms, legal_privacy_version=state.privacy)

    monkeypatch.setattr(config, "get_settings", settings)

    @asynccontextmanager
    async def connection(pool, tenant_id):
        state.db_opens += 1
        yield object()

    async def acceptance_status(conn, *, tenant_id, user_id):
        state.reads += 1
        assert (tenant_id, user_id) == (TENANT, USER)
        if state.fail:
            raise ConnectionError("synthetic acceptance read failure")
        return {"legal_acceptance_required": state.owed,
                "terms_version": state.terms, "privacy_version": state.privacy}

    # get_current_user's own reads use dep.acquire_with_tenant; the gate has its own.
    @asynccontextmanager
    async def dep_connection(pool, tenant_id):
        yield object()

    monkeypatch.setattr(dep, "acquire_with_tenant", dep_connection)
    monkeypatch.setattr(legal_gate, "acquire_with_tenant", connection)
    monkeypatch.setattr(legal_acceptance, "acceptance_status", acceptance_status)

    session = {"id": "s1", "user_id": USER}
    monkeypatch.setattr(dep, "get_db_client", Mock(return_value=SimpleNamespace(pool=object())))
    monkeypatch.setattr(dep, "get_session_by_id", AsyncMock(return_value=session))

    async def load(conn, user_id, tenant_id=None):
        return {"id": USER, "email": "u@example.com", "tenant_id": state.tenant,
                "role": state.role, "name": "Synthetic", "business_name": None}

    monkeypatch.setattr(principal, "load_current_principal", load)
    monkeypatch.setattr("app.services.scripts.tenant_minutes.compute_tenant_minutes_status",
                        AsyncMock(return_value=SimpleNamespace(allowance=lambda: {})))
    state.make_app = lambda routes=(START,): _app(routes)
    return state


def _app(routes):
    application = FastAPI()

    async def handler(user=Depends(dep.get_current_user)):
        return {"id": user.id}

    for method, path in routes:
        application.add_api_route(path, handler, methods=[method])
    return application


async def call(application, method, template):
    url = template.replace("{", "").replace("}", "")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application),
                                 base_url="https://synthetic.example") as client:
        return await client.request(method, url, headers={"Authorization": "Bearer valid"})


async def start(gate_state):
    return await call(gate_state.make_app(), *START)


async def test_switch_off_makes_no_read_and_changes_nothing(gate):
    gate.required = False
    response = await start(gate)
    assert response.status_code == 200 and response.json() == {"id": USER}
    assert gate.reads == 0 and gate.db_opens == 0


async def test_switch_on_unaccepted_user_gets_403_on_campaign_start(gate):
    response = await start(gate)
    assert response.status_code == 403
    assert response.json() == {"detail": {
        "code": "legal_acceptance_required", "message": legal_gate.LEGAL_MESSAGE,
        "terms_version": "t1", "privacy_version": "p1"}}


async def test_accepted_user_passes(gate):
    gate.owed = False
    assert (await start(gate)).status_code == 200
    assert gate.reads == 1


@pytest.mark.parametrize("method,template", OPEN_CASES, ids=[f"{m} {p}" for m, p in OPEN_CASES])
async def test_open_routes_stay_open(gate, method, template):
    response = await call(gate.make_app(((method, template),)), method, template)
    assert response.status_code == 200, response.text
    assert gate.reads == 0


async def test_open_route_with_other_method_is_gated(gate):
    response = await call(gate.make_app((("DELETE", "/api/v1/billing/plans"),)),
                          "DELETE", "/api/v1/billing/plans")
    assert response.status_code == 403


async def test_platform_admin_not_gated(gate):
    gate.role = "platform_admin"
    assert (await start(gate)).status_code == 200
    assert gate.reads == 0


async def test_user_without_tenant_not_gated(gate):
    gate.tenant = None
    assert (await start(gate)).status_code == 200
    assert gate.reads == 0


async def test_unset_versions_do_not_lock_out_and_log_one_error(gate, caplog):
    gate.terms = None
    with caplog.at_level(logging.ERROR, logger=legal_gate.logger.name):
        for _ in range(3):
            assert (await start(gate)).status_code == 200
    errors = [r for r in caplog.records if r.name == legal_gate.logger.name and r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert gate.reads == 0


async def test_read_failure_is_the_existing_503_and_not_accepted(gate):
    gate.fail = True
    response = await start(gate)
    assert response.status_code == 503 and response.json() == DB_FAILURE_DETAIL
    assert not legal_gate._accepted
    gate.fail = False
    assert (await start(gate)).status_code == 403  # a failed read never cached "accepted"


async def test_acceptance_works_on_next_request(gate):
    assert (await start(gate)).status_code == 403
    assert (await start(gate)).status_code == 403  # "not accepted" is never cached
    gate.owed = False  # the receipt is recorded
    assert (await start(gate)).status_code == 200


async def test_accepted_answer_is_cached_briefly(gate):
    gate.owed = False
    await start(gate)
    await start(gate)
    assert gate.reads == 1


async def test_cache_expires(gate, monkeypatch):
    gate.owed = False
    await start(gate)
    clock = legal_gate.time.monotonic() + legal_gate.ACCEPTED_CACHE_TTL_SECONDS + 1
    monkeypatch.setattr(legal_gate.time, "monotonic", lambda: clock)
    await start(gate)
    assert gate.reads == 2


def test_cache_is_bounded(monkeypatch):
    legal_gate._accepted.clear()
    monkeypatch.setattr(legal_gate, "ACCEPTED_CACHE_MAX", 5)
    for index in range(50):
        legal_gate._remember_accepted((TENANT, str(index), "t1", "p1"))
    assert len(legal_gate._accepted) == 5
    legal_gate._accepted.clear()


def test_every_open_route_exists_in_the_real_application():
    missing = [(m, p) for m, p, _ in legal_gate.OPEN_ROUTES if (m, p) not in REAL_ROUTES]
    assert not missing, f"open-list entries that no longer exist: {missing}"
    assert PREFIX_ROUTES, "no route is mounted under the open prefixes"
    assert all(reason for _, _, reason in legal_gate.OPEN_ROUTES)
