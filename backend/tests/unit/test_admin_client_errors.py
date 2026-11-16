"""POST /admin/client-errors: untrusted admin-panel crash reports, logged once, scrubbed."""
import json
import logging
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from slowapi.errors import RateLimitExceeded

from app.api.v1.dependencies import get_current_user, get_db_client
from app.api.v1.endpoints.admin import client_errors
from app.api.v1.endpoints.auth import limiter
from app.core.error_handlers import rate_limit_exception_handler

LOGGER = "app.api.v1.endpoints.admin.client_errors"
URL = "/api/v1/admin/client-errors"
USER_ID = "12345678-aaaa-bbbb-cccc-ddddeeeeffff"


class RecordingPool:
    """Any SQL the route ran would show up here."""

    def __init__(self):
        self.statements = []

    def __getattr__(self, name):
        def record(*args, **kwargs):
            self.statements.append((name, args))
            raise AssertionError("client-errors must not touch the database")
        return record


def valid(**over):
    body = {
        "kind": "render", "name": "TypeError", "message": "x is undefined",
        "stack": ["at Foo (app.js:1:2)", "at Bar (app.js:3:4)"],
        "route": "/campaigns", "app_version": "1.2.3",
        "occurred_at": "2026-11-17T10:00:00Z",
    }
    body.update(over)
    return body


def make_app(role="tenant_admin", pool=None):
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exception_handler)
    app.include_router(client_errors.router, prefix="/api/v1/admin")
    if role is not None:
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
            id=USER_ID, email="admin@example.com", role=role, tenant_id=str(uuid4()))
    if pool is not None:
        app.dependency_overrides[get_db_client] = lambda: SimpleNamespace(pool=pool)
    return app


async def post(app, body=None, content=None, headers=None):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://synthetic.invalid"
    ) as client:
        if content is not None:
            return await client.post(URL, content=content, headers=headers)
        return await client.post(URL, json=body, headers=headers)


@pytest.fixture(autouse=True)
def fresh_limiter():
    limiter.reset()
    yield
    limiter.reset()


def error_lines(caplog):
    return [r for r in caplog.records if r.name == LOGGER and r.levelno == logging.ERROR]


async def test_accepted_report_is_204_and_logs_exactly_one_error_line(caplog):
    pool = RecordingPool()
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        response = await post(make_app(pool=pool), valid())
    assert response.status_code == 204 and response.content == b""
    lines = error_lines(caplog)
    assert len(lines) == 1
    text = lines[0].getMessage()
    assert text.startswith("admin_client_error ")
    assert "12345678" in text and USER_ID not in text and "tenant_admin" in text
    assert "admin@example.com" not in text
    assert "TypeError" in text and "/campaigns" in text
    assert pool.statements == []
    assert len([r for r in caplog.records if r.name == LOGGER]) == 1


async def test_sensitive_text_never_reaches_the_log(caplog):
    body = valid(
        message="mail bob@example.com phone +44 7700 900123 Authorization: Bearer abc.def.ghi at /x?query=zq9leak",
        stack=["at f (https://a.example/app.js?query=zq9leak:1:2) bob@example.com",
               "call +1 415 555 0132 Bearer tokentokentoken123"],
        route="/campaigns/42?query=zq9leak#frag",
    )
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        response = await post(make_app(), body)
    assert response.status_code == 204
    text = error_lines(caplog)[0].getMessage()
    for leaked in ("bob@example.com", "7700 900123", "415 555 0132", "abc.def.ghi",
                   "tokentokentoken123", "zq9leak", "frag"):
        assert leaked not in text, leaked
    assert "/campaigns/42" in text


async def test_newlines_and_control_characters_cannot_forge_a_second_line(caplog):
    body = valid(
        name="Err\r\nERROR forged line", message="a\nadmin_client_error forged\x00\x1b[31m b",
        stack=["x\ny"], app_version="1\n2", route="/a\nb",
    )
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        response = await post(make_app(), body)
    assert response.status_code == 204
    lines = error_lines(caplog)
    assert len(lines) == 1
    text = lines[0].getMessage()
    assert len(text.splitlines()) == 1
    assert not {c for c in text if ord(c) < 32 or ord(c) == 127 or c in "  \x85"}


@pytest.mark.parametrize("mutate", [
    lambda b: b.update(extra="x"),
    lambda b: b.update(kind="boom"),
    lambda b: b.update(name="n" * 101),
    lambda b: b.update(message="m" * 501),
    lambda b: b.update(stack=["s"] * 11),
    lambda b: b.update(stack=["s" * 301]),
    lambda b: b.update(route="/" + "r" * 200),
    lambda b: b.update(app_version="v" * 65),
    lambda b: b.update(occurred_at="yesterday"),
    lambda b: b.pop("message"),
    lambda b: b.update(message=5),
])
async def test_wrong_shape_is_422_and_nothing_is_logged(caplog, mutate):
    body = valid()
    mutate(body)
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        response = await post(make_app(), body)
    assert response.status_code == 422
    assert error_lines(caplog) == []


async def test_null_app_version_and_non_object_body(caplog):
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        ok = await post(make_app(), valid(app_version=None))
        listed = await post(make_app(), content=b"[1,2]")
        junk = await post(make_app(), content=b"{not json")
    assert ok.status_code == 204 and listed.status_code == 422 and junk.status_code == 422
    assert len(error_lines(caplog)) == 1


async def test_declared_oversize_body_is_413_without_logging(caplog):
    big = json.dumps(valid(message="m" * 400, stack=["s" * 300] * 10)).encode() + b" " * 9000
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        response = await post(make_app(), content=big, headers={"content-type": "application/json"})
    assert response.status_code == 413
    assert error_lines(caplog) == []


async def test_undeclared_oversize_body_is_413_and_reading_stops(caplog):
    served = []

    async def chunks():
        for _ in range(200):
            served.append(1)
            yield b" " * 1024

    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        response = await post(make_app(), content=chunks(), headers={"content-type": "application/json"})
    assert response.status_code == 413
    assert error_lines(caplog) == []
    assert len(served) < 200  # never read an unbounded body


async def test_not_signed_in_is_401(caplog):
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        response = await post(make_app(role=None), valid())
    assert response.status_code == 401
    assert error_lines(caplog) == []


@pytest.mark.parametrize("role", ["user", "viewer"])
async def test_non_admin_user_is_403(caplog, role):
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        response = await post(make_app(role=role), valid())
    assert response.status_code == 403
    assert error_lines(caplog) == []


@pytest.mark.parametrize("role", ["tenant_admin", "partner_admin", "platform_admin"])
async def test_every_admin_role_is_accepted(role):
    assert (await post(make_app(role=role), valid())).status_code == 204


async def test_ten_per_minute_per_user_then_429(caplog):
    app = make_app()
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        codes = [(await post(app, valid())).status_code for _ in range(31)]
    assert codes[:10] == [204] * 10 and codes[10] == 429
    assert len(error_lines(caplog)) == 10
