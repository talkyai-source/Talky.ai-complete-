"""Only the number-request submit path gets the larger body cap."""
import httpx
import pytest
from fastapi import FastAPI, Request

from app.core import api_security_middleware as api_security
from app.domain.services.number_requests import config

SUBMIT = "/api/v1/telephony/number-requests"
OTHER = "/api/v1/telephony/other"


def make_app():
    app = FastAPI()
    for path in (SUBMIT, OTHER):
        @app.post(path)
        async def endpoint(request: Request):
            return {"size": len(await request.body())}
    app.add_middleware(api_security.APISecurityMiddleware)
    return app


async def send(path, size, content_length=True, method="POST"):
    headers = {"content-type": "text/plain"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=make_app()), base_url="http://t") as c:
        return await c.request(method, path, content=b"a" * size, headers=headers)


@pytest.fixture(autouse=True)
def small_caps(monkeypatch):
    monkeypatch.setattr(api_security, "MAX_REQUEST_BODY_SIZE", 1024)
    monkeypatch.setenv("NUMBER_REQUEST_MAX_FILE_BYTES", "1024")


def test_limit_is_two_files_plus_overhead():
    assert config.submit_body_limit_bytes() == 2 * 1024 + 262144


async def test_other_paths_keep_the_default_cap():
    assert (await send(OTHER, 1024)).status_code == 200
    r = await send(OTHER, 1025)
    assert r.status_code == 413 and r.json() == {"detail": "Request body too large (max 1024 bytes)"}


async def test_submit_path_accepts_just_under_and_refuses_just_over():
    limit = config.submit_body_limit_bytes()
    assert (await send(SUBMIT, 1025)).status_code == 200  # above the default cap
    assert (await send(SUBMIT, limit)).status_code == 200
    r = await send(SUBMIT, limit + 1)
    assert r.status_code == 413
    assert r.json() == {"detail": f"Request body too large (max {limit} bytes)"}


def test_trailing_slash_counts_as_the_submit_path():
    assert api_security._max_body_size({"method": "POST", "path": SUBMIT + "/"}) == config.submit_body_limit_bytes()


async def test_larger_cap_is_for_post_only():
    scope_get = {"method": "GET", "path": SUBMIT}
    scope_post = {"method": "POST", "path": SUBMIT}
    assert api_security._max_body_size(scope_get) == 1024
    assert api_security._max_body_size(scope_post) == config.submit_body_limit_bytes()
    assert api_security._max_body_size({"method": "POST", "path": SUBMIT + "/config"}) == 1024


def test_the_customer_post_route_resolves_to_the_path_the_middleware_widens():
    from app.api.v1.routes import api_router
    from app.api.v1.endpoints import telephony_number_requests as customer

    app = FastAPI()
    app.include_router(api_router, prefix="/api/v1")
    from fastapi.routing import iter_route_contexts
    found = [r.path for r in iter_route_contexts(app.routes)
             if getattr(r, "endpoint", None) is customer.submit_number_request and "POST" in r.methods]
    assert found == [api_security.NUMBER_REQUEST_SUBMIT_PATH]
