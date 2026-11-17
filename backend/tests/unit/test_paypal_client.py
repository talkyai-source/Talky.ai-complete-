"""PayPal REST client, against httpx.MockTransport (no network)."""
from __future__ import annotations

import asyncio
import base64
import json
import logging

import httpx
import pytest

from app.domain.services import paypal_client as pc
from app.domain.services.paypal_client import (
    PayPalClient,
    PayPalError,
    PayPalNotConfigured,
    approval_url,
    minor_units,
    money_value,
)

CLIENT_ID = "client-" + "id" * 4
SECRET = "client-" + "sec" * 4
TOKEN = "access-" + "tok" * 5
TOKEN2 = "access-" + "two" * 5
BASE = "https://api-m.sandbox.paypal.com"


class Server:
    """Records requests; answers the token endpoint and delegates the rest."""

    def __init__(self, api=None, expires_in=3600, tokens=None):
        self.requests: list[httpx.Request] = []
        self.api = api or (lambda req: httpx.Response(200, json={"ok": True}))
        self.expires_in = expires_in
        self.tokens = list(tokens or [TOKEN, TOKEN2, TOKEN + "3", TOKEN + "4"])

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/v1/oauth2/token":
            return httpx.Response(
                200, json={"access_token": self.tokens.pop(0), "expires_in": self.expires_in}
            )
        return self.api(request)

    @property
    def token_calls(self):
        return [r for r in self.requests if r.url.path == "/v1/oauth2/token"]

    @property
    def api_calls(self):
        return [r for r in self.requests if r.url.path != "/v1/oauth2/token"]


def make(server) -> PayPalClient:
    return PayPalClient(
        base_url=BASE, client_id=CLIENT_ID, client_secret=SECRET,
        transport=httpx.MockTransport(server),
    )


ORDER_KW = dict(
    request_id="req-1", reference_id="ref-1", custom_id="cust-1", description="Minutes",
    currency="usd", value="12.34", return_url="https://x.example/ok",
    cancel_url="https://x.example/no", brand_name="Brand",
)


# ---- pure helpers -----------------------------------------------------------

def test_money_value():
    assert money_value(1234, "usd") == "12.34"
    assert money_value(5, "GBP") == "0.05"
    assert money_value(0, "EUR") == "0.00"
    assert money_value(100000, "CAD") == "1000.00"


@pytest.mark.parametrize("amount,cur", [(-1, "USD"), (100, "JPY"), (100, ""), (True, "USD"), (1.5, "USD")])
def test_money_value_refuses(amount, cur):
    with pytest.raises(ValueError):
        money_value(amount, cur)


def test_minor_units():
    assert minor_units("12.34", "USD") == 1234
    assert minor_units("12.3", "usd") == 1230
    assert minor_units("12", "AUD") == 1200
    assert minor_units("0.00", "USD") == 0


@pytest.mark.parametrize("v", ["12.345", "-1.00", "1e2", " 1.00", "1.00 ", "1.00\n", "", ".5", "1.", "abc", "１２"])
def test_minor_units_refuses(v):
    with pytest.raises(ValueError):
        minor_units(v, "USD")


def test_minor_units_unsupported_currency():
    with pytest.raises(ValueError):
        minor_units("1.00", "JPY")


def _links(*pairs):
    return {"links": [{"rel": r, "href": h} for r, h in pairs]}


def test_approval_url_prefers_payer_action():
    order = _links(("approve", "https://www.paypal.com/a"), ("payer-action", "https://www.paypal.com/p"))
    assert approval_url(order, env="live") == "https://www.paypal.com/p"
    assert approval_url(_links(("approve", "https://www.sandbox.paypal.com/a")), env="sandbox") == \
        "https://www.sandbox.paypal.com/a"


def test_approval_url_refusals():
    assert approval_url(_links(("payer-action", "https://evil.example/p")), env="live") is None
    assert approval_url(_links(("payer-action", "https://www.paypal.com.evil.example/p")), env="live") is None
    assert approval_url(_links(("payer-action", "https://www.paypal.com@evil.example/p")), env="live") is None
    assert approval_url(_links(("payer-action", "http://www.paypal.com/p")), env="live") is None
    assert approval_url(_links(("payer-action", "https://www.sandbox.paypal.com/p")), env="live") is None
    assert approval_url(_links(("self", "https://www.paypal.com/p")), env="live") is None
    assert approval_url({}, env="live") is None
    assert approval_url({"links": "x"}, env="live") is None
    assert approval_url(_links(("payer-action", "https://www.paypal.com/p")), env="other") is None
    # a bad payer-action does not stop a good approve link
    order = _links(("payer-action", "https://evil.example/p"), ("approve", "https://www.paypal.com/a"))
    assert approval_url(order, env="live") == "https://www.paypal.com/a"


# ---- create order -----------------------------------------------------------

async def test_create_order_exact_request():
    srv = Server(api=lambda r: httpx.Response(201, json={"id": "ORD1", "status": "PAYER_ACTION_REQUIRED"}))
    client = make(srv)
    result = await client.create_order(**ORDER_KW)
    assert result["id"] == "ORD1"
    (req,) = srv.api_calls
    assert req.method == "POST"
    assert req.url.path == "/v2/checkout/orders"
    assert req.headers["PayPal-Request-Id"] == "req-1"
    assert req.headers["Prefer"] == "return=representation"
    assert req.headers["Authorization"] == f"Bearer {TOKEN}"
    assert json.loads(req.content) == {
        "intent": "CAPTURE",
        "purchase_units": [{
            "reference_id": "ref-1", "custom_id": "cust-1", "description": "Minutes",
            "amount": {"currency_code": "USD", "value": "12.34"},
        }],
        "payment_source": {"paypal": {"experience_context": {
            "return_url": "https://x.example/ok", "cancel_url": "https://x.example/no",
            "user_action": "PAY_NOW", "shipping_preference": "NO_SHIPPING",
            "payment_method_preference": "IMMEDIATE_PAYMENT_REQUIRED", "brand_name": "Brand",
        }}},
    }
    # token request: Basic auth with id and secret, form body
    (tok,) = srv.token_calls
    expected = "Basic " + base64.b64encode(f"{CLIENT_ID}:{SECRET}".encode()).decode()
    assert tok.headers["Authorization"] == expected
    assert tok.content == b"grant_type=client_credentials"
    await client.aclose()


@pytest.mark.parametrize(
    "override",
    [
        {"currency": "JPY"},
        {"value": "0.00"},
        {"value": "12.345"},
        {"value": "-1.00"},
        {"value": "abc"},
        {"custom_id": "c" * 256},
        {"reference_id": "r" * 257},
        {"return_url": "http://x.example/ok"},
        {"cancel_url": "http://x.example/no"},
        {"return_url": "javascript:alert(1)"},
    ],
)
async def test_create_order_refusals_make_no_call(override):
    srv = Server()
    client = make(srv)
    with pytest.raises(ValueError):
        await client.create_order(**{**ORDER_KW, **override})
    assert srv.requests == []
    await client.aclose()


async def test_create_order_limits_accepted():
    srv = Server(api=lambda r: httpx.Response(200, json={"id": "O"}))
    client = make(srv)
    await client.create_order(**{**ORDER_KW, "custom_id": "c" * 255, "reference_id": "r" * 256})
    assert len(srv.api_calls) == 1
    await client.aclose()


# ---- token handling ---------------------------------------------------------

async def test_token_fetched_once_for_two_calls():
    srv = Server()
    client = make(srv)
    await client.get_order("ORD1")
    await client.get_order("ORD2")
    assert len(srv.token_calls) == 1
    assert len(srv.api_calls) == 2
    await client.aclose()


async def test_token_fetched_once_under_gather():
    srv = Server()
    client = make(srv)
    await asyncio.gather(*(client.get_order(f"ORD{i}") for i in range(5)))
    assert len(srv.token_calls) == 1
    assert len(srv.api_calls) == 5
    await client.aclose()


async def test_token_refetched_after_expiry(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(pc, "_monotonic", lambda: now[0])
    srv = Server(expires_in=300)
    client = make(srv)
    await client.get_order("A")
    now[0] += 239  # still inside expires_in - 60
    await client.get_order("A")
    assert len(srv.token_calls) == 1
    now[0] += 2  # 241s: past the 60 second margin
    await client.get_order("A")
    assert len(srv.token_calls) == 2
    assert srv.api_calls[-1].headers["Authorization"] == f"Bearer {TOKEN2}"
    await client.aclose()


async def test_single_retry_on_401():
    state = {"n": 0}

    def api(req):
        state["n"] += 1
        if state["n"] == 1:
            return httpx.Response(401, json={"name": "AUTHENTICATION_FAILURE"})
        return httpx.Response(200, json={"id": "ORD1"})

    srv = Server(api=api)
    client = make(srv)
    assert (await client.get_order("ORD1"))["id"] == "ORD1"
    assert len(srv.token_calls) == 2
    assert [r.headers["Authorization"] for r in srv.api_calls] == [f"Bearer {TOKEN}", f"Bearer {TOKEN2}"]
    await client.aclose()


async def test_401_twice_raises_after_one_retry():
    srv = Server(api=lambda r: httpx.Response(401, json={"name": "AUTHENTICATION_FAILURE", "debug_id": "d1"}))
    client = make(srv)
    with pytest.raises(PayPalError) as exc:
        await client.get_order("ORD1")
    assert exc.value.status == 401
    assert len(srv.api_calls) == 2
    assert len(srv.token_calls) == 2
    await client.aclose()


async def test_token_endpoint_failure_raises():
    def handler(req):
        return httpx.Response(401, json={"name": "invalid_client"})

    client = PayPalClient(base_url=BASE, client_id=CLIENT_ID, client_secret=SECRET,
                          transport=httpx.MockTransport(handler))
    with pytest.raises(PayPalError) as exc:
        await client.get_order("ORD1")
    assert exc.value.status == 401
    assert SECRET not in str(exc.value)
    await client.aclose()


# ---- capture / get ----------------------------------------------------------

async def test_capture_order_request():
    srv = Server(api=lambda r: httpx.Response(201, json={"status": "COMPLETED"}))
    client = make(srv)
    out = await client.capture_order("ORD-1_x", request_id="cap-1")
    assert out["status"] == "COMPLETED"
    (req,) = srv.api_calls
    assert req.method == "POST"
    assert req.url.path == "/v2/checkout/orders/ORD-1_x/capture"
    assert req.headers["PayPal-Request-Id"] == "cap-1"
    assert req.headers["Prefer"] == "return=representation"
    assert req.headers["Content-Type"] == "application/json"
    assert json.loads(req.content) == {}
    await client.aclose()


async def test_get_order_and_capture_paths():
    srv = Server()
    client = make(srv)
    await client.get_order("ORD1")
    await client.get_capture("CAP1")
    assert [(r.method, r.url.path) for r in srv.api_calls] == [
        ("GET", "/v2/checkout/orders/ORD1"),
        ("GET", "/v2/payments/captures/CAP1"),
    ]
    await client.aclose()


@pytest.mark.parametrize("bad", ["", "a/b", "../x", "a b", "a?b", "x" * 65, "é", "a\n", "ORD1/capture"])
async def test_id_validation(bad):
    srv = Server()
    client = make(srv)
    for call in (
        lambda: client.get_order(bad),
        lambda: client.get_capture(bad),
        lambda: client.capture_order(bad, request_id="r"),
    ):
        with pytest.raises(ValueError):
            await call()
    assert srv.requests == []
    await client.aclose()


# ---- errors -----------------------------------------------------------------

async def test_error_parsing_422():
    body = {
        "name": "UNPROCESSABLE_ENTITY", "message": "secret-looking message", "debug_id": "dbg123",
        "details": [{"issue": "ORDER_NOT_APPROVED", "description": "nope"}],
    }
    srv = Server(api=lambda r: httpx.Response(422, json=body))
    client = make(srv)
    with pytest.raises(PayPalError) as exc:
        await client.capture_order("ORD1", request_id="r")
    err = exc.value
    assert (err.status, err.name, err.issue, err.debug_id, err.retryable) == (
        422, "UNPROCESSABLE_ENTITY", "ORDER_NOT_APPROVED", "dbg123", False)
    assert "ORDER_NOT_APPROVED" in str(err) and "dbg123" in str(err)
    assert "secret-looking" not in str(err)
    await client.aclose()


@pytest.mark.parametrize("status,retryable", [(500, True), (503, True), (429, True), (400, False), (404, False), (422, False)])
async def test_retryable_flag(status, retryable):
    srv = Server(api=lambda r: httpx.Response(status, json={"name": "X"}))
    client = make(srv)
    with pytest.raises(PayPalError) as exc:
        await client.get_order("ORD1")
    assert exc.value.retryable is retryable
    assert len(srv.api_calls) == 1  # no automatic retry
    await client.aclose()


async def test_non_json_error_body():
    srv = Server(api=lambda r: httpx.Response(502, text="<html>bad gateway</html>"))
    client = make(srv)
    with pytest.raises(PayPalError) as exc:
        await client.get_order("ORD1")
    assert exc.value.status == 502 and exc.value.retryable is True
    assert exc.value.name is None
    await client.aclose()


async def test_timeout_is_retryable_network_error():
    def api(req):
        raise httpx.ReadTimeout("took too long " + TOKEN, request=req)

    srv = Server(api=api)
    client = make(srv)
    with pytest.raises(PayPalError) as exc:
        await client.get_order("ORD1")
    assert exc.value.status is None and exc.value.retryable is True
    assert TOKEN not in str(exc.value)
    await client.aclose()


async def test_connection_error_is_retryable():
    def handler(req):
        raise httpx.ConnectError("refused", request=req)

    client = PayPalClient(base_url=BASE, client_id=CLIENT_ID, client_secret=SECRET,
                          transport=httpx.MockTransport(handler))
    with pytest.raises(PayPalError) as exc:
        await client.get_order("ORD1")
    assert exc.value.status is None and exc.value.retryable is True
    await client.aclose()


async def test_no_secret_or_token_in_errors_or_logs(caplog):
    caplog.set_level(logging.DEBUG)
    srv = Server(api=lambda r: httpx.Response(
        500, json={"name": "INTERNAL", "debug_id": "d9", "details": [{"issue": "BOOM"}]}))
    client = make(srv)
    with pytest.raises(PayPalError) as exc:
        await client.create_order(**ORDER_KW)
    text = str(exc.value) + repr(exc.value) + caplog.text
    basic = base64.b64encode(f"{CLIENT_ID}:{SECRET}".encode()).decode()
    for forbidden in (SECRET, TOKEN, basic, "Authorization", "Bearer"):
        assert forbidden not in text
    assert "paypal" in caplog.text  # the INFO line exists
    paypal_lines = [r for r in caplog.records if r.name == pc.logger.name]
    assert len(paypal_lines) == 2  # one token call, one order call
    await client.aclose()


# ---- webhook signature ------------------------------------------------------

WH_HEADERS = {
    "PAYPAL-AUTH-ALGO": "SHA256withRSA",
    "PAYPAL-CERT-URL": "https://api.sandbox.paypal.com/v1/notifications/certs/CERT-1",
    "PAYPAL-TRANSMISSION-ID": "tid-1",
    "PAYPAL-TRANSMISSION-SIG": "sig/+=",
    "PAYPAL-TRANSMISSION-TIME": "2026-11-18T10:00:00Z",
}
RAW_EVENT = '{ "resource" : {"b":1,  "a":"café €"},\n "id":"WH-1" , "event_type":"PAYMENT.CAPTURE.COMPLETED"}'.encode()


def _verify_server(status="SUCCESS"):
    return Server(api=lambda r: httpx.Response(200, json={"verification_status": status}))


async def test_verify_posts_raw_event_unchanged():
    srv = _verify_server()
    client = make(srv)
    ok = await client.verify_webhook_signature(webhook_id="WHID", headers=WH_HEADERS, raw_body=RAW_EVENT)
    assert ok is True
    (req,) = srv.api_calls
    assert req.method == "POST"
    assert req.url.path == "/v1/notifications/verify-webhook-signature"
    assert req.headers["Content-Type"] == "application/json"
    assert b'"webhook_event":' + RAW_EVENT + b"}" in req.content
    assert req.content.endswith(RAW_EVENT + b"}")
    # the rest parses and carries the right values (event spliced in as a JSON object)
    parsed = json.loads(req.content)
    assert parsed["webhook_id"] == "WHID"
    assert parsed["auth_algo"] == "SHA256withRSA"
    assert parsed["cert_url"] == WH_HEADERS["PAYPAL-CERT-URL"]
    assert parsed["transmission_id"] == "tid-1"
    assert parsed["transmission_sig"] == "sig/+="
    assert parsed["transmission_time"] == "2026-11-18T10:00:00Z"
    assert parsed["webhook_event"]["resource"]["a"] == "café €"
    await client.aclose()


async def test_verify_headers_case_insensitive():
    srv = _verify_server()
    client = make(srv)
    lower = {k.lower(): v for k, v in WH_HEADERS.items()}
    assert await client.verify_webhook_signature(webhook_id="W", headers=lower, raw_body=RAW_EVENT) is True
    await client.aclose()


async def test_verify_failure_status():
    client = make(_verify_server("FAILURE"))
    assert await client.verify_webhook_signature(webhook_id="W", headers=WH_HEADERS, raw_body=RAW_EVENT) is False
    await client.aclose()


async def test_verify_paypal_error_raises():
    srv = Server(api=lambda r: httpx.Response(500, json={"name": "INTERNAL_SERVER_ERROR"}))
    client = make(srv)
    with pytest.raises(PayPalError):
        await client.verify_webhook_signature(webhook_id="W", headers=WH_HEADERS, raw_body=RAW_EVENT)
    await client.aclose()


_REFUSALS = {
    "empty_webhook_id": dict(webhook_id=""),
    "not_an_object": dict(raw_body=b'["x"]'),
    "not_json": dict(raw_body=b"nope"),
    "not_utf8": dict(raw_body=b"\xff\xfe"),
    "empty_body": dict(raw_body=b""),
    "cert_http": dict(headers={**WH_HEADERS, "PAYPAL-CERT-URL": "http://api.paypal.com/c"}),
    "cert_other_host": dict(headers={**WH_HEADERS, "PAYPAL-CERT-URL": "https://evil.example/c"}),
    "cert_suffix_trick": dict(headers={**WH_HEADERS, "PAYPAL-CERT-URL": "https://evilpaypal.com/c"}),
    "cert_subdomain_of_evil": dict(headers={**WH_HEADERS, "PAYPAL-CERT-URL": "https://paypal.com.evil.example/c"}),
    "cert_userinfo": dict(headers={**WH_HEADERS, "PAYPAL-CERT-URL": "https://api.paypal.com@evil.example/c"}),
    **{
        f"missing_{h}": dict(headers={k: v for k, v in WH_HEADERS.items() if k != h})
        for h in WH_HEADERS
    },
}


@pytest.mark.parametrize("case", list(_REFUSALS))
async def test_verify_refusals_make_no_call(case):
    srv = _verify_server()
    client = make(srv)
    kwargs = dict(webhook_id="W", headers=WH_HEADERS, raw_body=RAW_EVENT)
    kwargs.update(_REFUSALS[case])
    assert await client.verify_webhook_signature(**kwargs) is False
    assert srv.requests == []
    await client.aclose()


async def test_verify_accepts_bare_paypal_com_cert():
    client = make(_verify_server())
    headers = {**WH_HEADERS, "PAYPAL-CERT-URL": "https://paypal.com/certs/1"}
    assert await client.verify_webhook_signature(webhook_id="W", headers=headers, raw_body=RAW_EVENT) is True
    await client.aclose()


# ---- webhooks management ----------------------------------------------------

async def test_list_and_create_webhook():
    def api(req):
        if req.method == "GET":
            return httpx.Response(200, json={"webhooks": [
                {"id": "W1", "url": "https://x.example/h", "event_types": [{"name": "PAYMENT.CAPTURE.COMPLETED"}]}]})
        return httpx.Response(201, json={"id": "W2"})

    srv = Server(api=api)
    client = make(srv)
    hooks = await client.list_webhooks()
    assert hooks[0]["id"] == "W1"
    created = await client.create_webhook(url="https://x.example/h2", event_names=["A.B", "C.D"])
    assert created["id"] == "W2"
    post = srv.api_calls[-1]
    assert post.url.path == "/v1/notifications/webhooks"
    assert json.loads(post.content) == {"url": "https://x.example/h2", "event_types": [{"name": "A.B"}, {"name": "C.D"}]}
    await client.aclose()


async def test_update_webhook_events_patch_request():
    srv = Server(api=lambda r: httpx.Response(200, json={"id": "W1"}))
    client = make(srv)
    out = await client.update_webhook_events("W1", event_names=["A.B", "C.D"])
    assert out == {"id": "W1"}
    req = srv.api_calls[-1]
    assert req.method == "PATCH"
    assert req.url.path == "/v1/notifications/webhooks/W1"
    assert req.headers["content-type"] == "application/json"
    assert json.loads(req.content) == [
        {"op": "replace", "path": "/event_types", "value": [{"name": "A.B"}, {"name": "C.D"}]}
    ]
    await client.aclose()


@pytest.mark.parametrize("bad", ["", "a/b", "../x", "a b", "x" * 65])
async def test_update_webhook_events_rejects_bad_id(bad):
    srv = Server()
    client = make(srv)
    with pytest.raises(ValueError):
        await client.update_webhook_events(bad, event_names=["A.B"])
    assert srv.api_calls == []
    await client.aclose()


async def test_list_webhooks_empty_shape():
    srv = Server(api=lambda r: httpx.Response(200, json={}))
    client = make(srv)
    assert await client.list_webhooks() == []
    await client.aclose()


# ---- process-wide client ----------------------------------------------------

_ENV = ("PAYPAL_ENV", "PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET",
        "PAYPAL_SANDBOX_CLIENT_ID", "PAYPAL_SANDBOX_CLIENT_SECRET", "PAYPAL_WEBHOOK_ID")


@pytest.fixture
def clean_env(monkeypatch):
    for name in _ENV:
        monkeypatch.delenv(name, raising=False)
    pc.reset_paypal_client()
    yield monkeypatch
    pc.reset_paypal_client()


def test_get_client_not_configured(clean_env):
    with pytest.raises(PayPalNotConfigured):
        pc.get_paypal_client()
    clean_env.setenv("PAYPAL_ENV", "live")
    with pytest.raises(PayPalNotConfigured):
        pc.get_paypal_client()
    clean_env.setenv("PAYPAL_CLIENT_ID", CLIENT_ID)
    with pytest.raises(PayPalNotConfigured):
        pc.get_paypal_client()


def test_get_client_without_webhook_id_and_rebuild(clean_env):
    clean_env.setenv("PAYPAL_ENV", "sandbox")
    clean_env.setenv("PAYPAL_CLIENT_ID", CLIENT_ID)
    clean_env.setenv("PAYPAL_CLIENT_SECRET", SECRET)
    # The live pair is never sent to the sandbox: sandbox needs its own pair.
    with pytest.raises(PayPalNotConfigured):
        pc.get_paypal_client()
    clean_env.setenv("PAYPAL_SANDBOX_CLIENT_ID", CLIENT_ID + "-sandbox")
    clean_env.setenv("PAYPAL_SANDBOX_CLIENT_SECRET", SECRET + "-sandbox")
    first = pc.get_paypal_client()
    assert first._base == "https://api-m.sandbox.paypal.com"
    assert pc.get_paypal_client() is first
    clean_env.setenv("PAYPAL_SANDBOX_CLIENT_ID", CLIENT_ID + "-other")
    second = pc.get_paypal_client()
    assert second is not first
    assert pc.get_paypal_client() is second
    clean_env.setenv("PAYPAL_ENV", "live")
    third = pc.get_paypal_client()
    assert third is not second and third._base == "https://api-m.paypal.com"
    pc.reset_paypal_client()
    assert pc.get_paypal_client() is not third


def test_error_text_is_clipped():
    err = PayPalError(status=400, name="n" * 500)
    assert len(str(err)) < 300
