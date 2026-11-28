"""Five raw answers of the security middleware now use the standard error shape.

Each answer keeps its status and headers, and keeps a top-level ``detail``
equal to ``error.message`` for the one old client that reads only ``detail``.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI, Request

from app.core import api_security_middleware as api_security
from app.core.request_id_middleware import RequestIdMiddleware

RID = "11111111-1111-4111-8111-111111111111"
SUBMIT = "/api/v1/telephony/number-requests"
ADMIN = "/api/v1/admin/telephony/number-requests"
OTHER = "/api/v1/example"
SIGN_IN = "/api/v1/auth/login"
INVALID = "Your request could not be read. Please try again."


def make_app():
    app = FastAPI()
    for path in (SUBMIT, ADMIN, OTHER, SIGN_IN):
        @app.post(path)
        async def endpoint(request: Request):
            return {"size": len(await request.body())}
    app.add_middleware(api_security.APISecurityMiddleware)
    app.add_middleware(RequestIdMiddleware)
    return app


async def post(path, content=b"{}", headers=None):
    transport = httpx.ASGITransport(app=make_app(), client=("203.0.113.10", 4321))
    h = [("x-request-id", RID)] + list((headers or {}).items() if isinstance(headers, dict) else headers or [])
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        return await c.post(path, content=content, headers=h)


def assert_both_keys(r, code, message=None, details=None):
    body = r.json()
    assert set(body) == {"error", "detail"}, r.text
    err = body["error"]
    assert set(err) == {"code", "message", "details", "request_id"}
    assert err["code"] == code
    assert err["request_id"] == RID
    assert body["detail"] == err["message"]
    assert err["details"] == details
    if message is not None:
        assert err["message"] == message
    return err


@pytest.fixture
def small_caps(monkeypatch):
    monkeypatch.setattr(api_security, "MAX_REQUEST_BODY_SIZE", 1024)
    monkeypatch.setenv("NUMBER_REQUEST_MAX_FILE_BYTES", str(3 * 1024 * 1024))


# --- T6 / T12 / T7: 413 ---------------------------------------------------

async def test_t6_submit_413_names_the_configured_size(monkeypatch):
    monkeypatch.setenv("NUMBER_REQUEST_MAX_FILE_BYTES", str(3 * 1024 * 1024))
    limit = api_security._max_body_size({"method": "POST", "path": SUBMIT})
    r = await post(SUBMIT, b"a" * (limit + 1), {"content-type": "text/plain"})
    assert r.status_code == 413
    assert_both_keys(r, "payload_too_large",
                     "Your files are too large to send together. Each document must be 3 MB or smaller.")


async def test_t12_changed_setting_changes_the_sentence(monkeypatch):
    monkeypatch.setenv("NUMBER_REQUEST_MAX_FILE_BYTES", str(5 * 1024 * 1024))
    limit = api_security._max_body_size({"method": "POST", "path": SUBMIT})
    r = await post(SUBMIT, b"a" * (limit + 1), {"content-type": "text/plain"})
    err = assert_both_keys(r, "payload_too_large", "Your files are too large to send together. Each document must be 5 MB or smaller.")
    assert "5 MB or smaller" in err["message"] and "3 MB" not in err["message"]


async def test_413_size_is_rounded_down_to_whole_megabytes(monkeypatch):
    monkeypatch.setenv("NUMBER_REQUEST_MAX_FILE_BYTES", "10000000")  # 9.53 MiB
    limit = api_security._max_body_size({"method": "POST", "path": SUBMIT})
    r = await post(SUBMIT, b"a" * (limit + 1), {"content-type": "text/plain"})
    assert_both_keys(r, "payload_too_large",
                     "Your files are too large to send together. Each document must be 9 MB or smaller.")


async def test_413_size_under_one_megabyte_uses_the_generic_sentence(monkeypatch):
    monkeypatch.setenv("NUMBER_REQUEST_MAX_FILE_BYTES", "1024")
    limit = api_security._max_body_size({"method": "POST", "path": SUBMIT})
    r = await post(SUBMIT, b"a" * (limit + 1), {"content-type": "text/plain"})
    assert_both_keys(r, "payload_too_large", "The request is too large.")


async def test_t7_other_path_413_is_generic(small_caps):
    r = await post(OTHER, b"a" * 1025, {"content-type": "text/plain"})
    assert r.status_code == 413
    assert_both_keys(r, "payload_too_large", "The request is too large.")


async def test_413_by_content_length_header_has_the_same_shape(small_caps):
    r = await post(OTHER, b"{}", {"content-type": "text/plain", "content-length": "5000"})
    # h11 may reject the mismatch in a real server; the ASGI transport passes it through.
    assert r.status_code == 413
    assert_both_keys(r, "payload_too_large", "The request is too large.")


# --- T8: both 400s ----------------------------------------------------------

async def test_t8_ambiguous_content_length():
    r = await post(OTHER, b"{}", [("content-type", "application/json"),
                                  ("content-length", "2"), ("content-length", "2")])
    assert r.status_code == 400
    assert_both_keys(r, "bad_request", INVALID, {"reason": "ambiguous_content_length"})


async def test_t8_invalid_content_length():
    r = await post(OTHER, b"{}", {"content-type": "application/json", "content-length": "x"})
    assert r.status_code == 400
    assert_both_keys(r, "bad_request", INVALID, {"reason": "invalid_content_length"})


# --- T9: IP-tier 429 --------------------------------------------------------

@pytest.fixture
def ip_tier(monkeypatch):
    from app.core import container
    from app.core.security import api_security as limits
    limiter = SimpleNamespace(check_all_tiers=AsyncMock(
        return_value=(False, {"Retry-After": "17", "X-RateLimit-Limit": "5"}, "Rate limit exceeded (ip tier)")))
    monkeypatch.setattr(container, "get_container",
                        lambda: SimpleNamespace(is_initialized=True, redis_enabled=True, redis=object()))
    monkeypatch.setattr(limits, "get_api_rate_limiter", lambda redis: limiter)
    return limiter


@pytest.mark.parametrize("path", [OTHER, ADMIN])
async def test_t9_ip_tier_429(ip_tier, path):
    r = await post(path, b"{}", {"content-type": "application/json"})
    assert r.status_code == 429
    assert r.headers["retry-after"] == "17" and r.headers["x-ratelimit-limit"] == "5"
    err = assert_both_keys(r, "rate_limited",
                           "Too many requests from your network. Please try again in less than a minute.",
                           {"retry_after_seconds": 17})
    assert err["details"]["retry_after_seconds"] == int(r.headers["retry-after"])


async def test_t9_wait_is_in_words_for_long_waits(ip_tier):
    ip_tier.check_all_tiers.return_value = (False, {"Retry-After": "300"}, "x")
    r = await post(OTHER, b"{}", {"content-type": "application/json"})
    err = assert_both_keys(r, "rate_limited", details={"retry_after_seconds": 300})
    assert err["message"].endswith("in about 5 minutes.")
    assert err["details"]["retry_after_seconds"] == 300


# --- T10: user agent --------------------------------------------------------

async def test_t10_blocked_user_agent():
    r = await post(OTHER, b"{}", {"content-type": "application/json", "user-agent": "sqlmap"})
    assert r.status_code == 403
    assert_both_keys(r, "forbidden", "Access denied")


# --- 415 --------------------------------------------------------------------

@pytest.mark.parametrize("path", [SUBMIT, ADMIN, SUBMIT + "/config"])
async def test_415_on_buy_now_paths_is_readable(path):
    r = await post(path, b"{}", {"content-type": "application/x-weird"})
    assert r.status_code == 415
    body = r.json()
    assert set(body) == {"error"}
    assert body["error"]["code"] == "unsupported_media_type"
    assert body["error"]["message"] == INVALID


async def test_415_on_other_paths_keeps_the_old_text():
    r = await post(OTHER, b"{}", {"content-type": "application/x-weird"})
    assert r.status_code == 415
    assert r.json()["error"]["message"] == "Content-Type 'application/x-weird' not supported"


# --- unchanged status and headers on an unrelated path ----------------------

def _names(r):
    return {k: v for k, v in r.headers.items() if k not in ("content-length", "x-request-id")}


async def test_unrelated_path_status_and_headers_are_unchanged(small_caps, ip_tier):
    """Expected values were captured by running the pre-change code."""
    json_h = {"content-type": "application/json"}
    amb = await post(OTHER, b"{}", [("content-type", "application/json"),
                                    ("content-length", "2"), ("content-length", "2")])
    inv = await post(OTHER, b"{}", {**json_h, "content-length": "x"})
    big = await post(OTHER, b"a" * 1025, {"content-type": "text/plain"})
    ua = await post(OTHER, b"{}", {**json_h, "user-agent": "sqlmap"})
    rate = await post(OTHER, b"{}", json_h)
    for r, status in ((amb, 400), (inv, 400), (big, 413), (ua, 403)):
        assert r.status_code == status
        assert _names(r) == {"content-type": "application/json"}
        assert r.headers["x-request-id"] == RID
        assert int(r.headers["content-length"]) == len(r.content)
    assert rate.status_code == 429
    assert _names(rate) == {"content-type": "application/json", "retry-after": "17", "x-ratelimit-limit": "5"}
    assert rate.headers["x-request-id"] == RID
