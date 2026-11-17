"""PayPal REST client (Orders v2, Payments v2, Webhooks v1) over httpx.

Safe by construction: no credential, access token or Authorization header is
ever logged, raised or returned. Errors carry PayPal's ``name``, ``issue`` and
``debug_id`` only. No retries other than one re-authentication on a 401.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import time
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any
from urllib.parse import quote, urlparse

import httpx

from app.domain.services import paypal_mode

logger = logging.getLogger(__name__)

SUPPORTED_CURRENCIES = frozenset({"USD", "GBP", "EUR", "CAD", "AUD"})

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_AMOUNT_RE = re.compile(r"[0-9]+(?:\.[0-9]{1,2})?", re.ASCII)
_TOKEN_MARGIN_SECONDS = 60.0
_APPROVAL_HOSTS = {"live": "www.paypal.com", "sandbox": "www.sandbox.paypal.com"}
_WEBHOOK_HEADERS = (
    ("auth_algo", "paypal-auth-algo"),
    ("cert_url", "paypal-cert-url"),
    ("transmission_id", "paypal-transmission-id"),
    ("transmission_sig", "paypal-transmission-sig"),
    ("transmission_time", "paypal-transmission-time"),
)

# Time source, replaceable in tests.
_monotonic = time.monotonic


class PayPalNotConfigured(RuntimeError):
    """PayPal environment or credentials are missing."""


def _clip(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text[:120]


class PayPalError(Exception):
    def __init__(
        self,
        *,
        status: int | None,
        name: str | None = None,
        issue: str | None = None,
        debug_id: str | None = None,
        retryable: bool = False,
    ) -> None:
        self.status = status
        self.name = _clip(name)
        self.issue = _clip(issue)
        self.debug_id = _clip(debug_id)
        self.retryable = retryable
        super().__init__(
            f"PayPal error: status={self.status} name={self.name} "
            f"issue={self.issue} debug_id={self.debug_id}"
        )


def _check_currency(currency: str) -> str:
    code = currency.strip().upper() if isinstance(currency, str) else ""
    if code not in SUPPORTED_CURRENCIES:
        raise ValueError("unsupported currency")
    return code


def money_value(amount_minor: int, currency: str) -> str:
    _check_currency(currency)
    if isinstance(amount_minor, bool) or not isinstance(amount_minor, int) or amount_minor < 0:
        raise ValueError("amount must be a non-negative integer")
    return f"{amount_minor // 100}.{amount_minor % 100:02d}"


def minor_units(value: str, currency: str) -> int:
    _check_currency(currency)
    if not isinstance(value, str) or _AMOUNT_RE.fullmatch(value) is None:
        raise ValueError("amount is not a plain decimal with at most two places")
    return int(Decimal(value) * 100)


def approval_url(order: Mapping[str, Any], *, env: str) -> str | None:
    host = _APPROVAL_HOSTS.get(env)
    links = order.get("links") if isinstance(order, Mapping) else None
    if host is None or not isinstance(links, Sequence) or isinstance(links, (str, bytes)):
        return None
    for rel in ("payer-action", "approve"):
        for link in links:
            if not isinstance(link, Mapping) or link.get("rel") != rel:
                continue
            href = link.get("href")
            if not isinstance(href, str):
                continue
            try:
                parsed = urlparse(href)
                port = parsed.port
            except ValueError:
                continue
            if (
                parsed.scheme == "https"
                and parsed.hostname == host
                and port in (None, 443)
                and parsed.username is None
            ):
                return href
    return None


def _path_id(value: str) -> str:
    if not isinstance(value, str) or _ID_RE.fullmatch(value) is None:
        raise ValueError("invalid PayPal id")
    return quote(value, safe="")


def _is_https(url: str) -> bool:
    try:
        parsed = urlparse(url)
        return parsed.scheme == "https" and bool(parsed.hostname)
    except ValueError:
        return False


def _cert_url_ok(url: str) -> bool:
    try:
        parsed = urlparse(url)
        host = parsed.hostname
    except ValueError:
        return False
    if parsed.scheme != "https" or not host:
        return False
    return host == "paypal.com" or host.endswith(".paypal.com")


class PayPalClient:
    def __init__(
        self,
        *,
        base_url: str,
        client_id: str,
        client_secret: str,
        timeout: float = 20.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._client_id = client_id
        self._client_secret = client_secret
        self._http = httpx.AsyncClient(timeout=timeout, transport=transport)
        self._token: str | None = None
        self._token_expires_at = 0.0
        self._lock = asyncio.Lock()

    async def aclose(self) -> None:
        await self._http.aclose()

    # -- transport ---------------------------------------------------------

    async def _send(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str],
        json_body: Any = None,
        content: bytes | None = None,
        data: dict[str, str] | None = None,
    ) -> httpx.Response:
        try:
            response = await self._http.request(
                method,
                f"{self._base}{path}",
                headers=headers,
                json=json_body,
                content=content,
                data=data,
            )
        except httpx.HTTPError:
            logger.info("paypal %s %s -> network error", method, path)
            raise PayPalError(status=None, name="NETWORK_ERROR", retryable=True) from None
        logger.info("paypal %s %s -> %s", method, path, response.status_code)
        return response

    @staticmethod
    def _error_from(response: httpx.Response) -> PayPalError:
        name = issue = debug_id = None
        try:
            body = response.json()
        except ValueError:
            body = None
        if isinstance(body, dict):
            name = body.get("name") if isinstance(body.get("name"), str) else None
            debug_id = body.get("debug_id") if isinstance(body.get("debug_id"), str) else None
            details = body.get("details")
            if isinstance(details, list) and details and isinstance(details[0], dict):
                first = details[0].get("issue")
                issue = first if isinstance(first, str) else None
        status = response.status_code
        return PayPalError(
            status=status,
            name=name,
            issue=issue,
            debug_id=debug_id,
            retryable=status == 429 or status >= 500,
        )

    @staticmethod
    def _json(response: httpx.Response) -> dict[str, Any]:
        try:
            body = response.json()
        except ValueError:
            raise PayPalError(status=response.status_code, name="INVALID_RESPONSE") from None
        if not isinstance(body, dict):
            raise PayPalError(status=response.status_code, name="INVALID_RESPONSE")
        return body

    async def _access_token(self) -> str:
        async with self._lock:
            if self._token is not None and _monotonic() < self._token_expires_at:
                return self._token
            raw = f"{self._client_id}:{self._client_secret}".encode()
            response = await self._send(
                "POST",
                "/v1/oauth2/token",
                headers={
                    "Authorization": "Basic " + base64.b64encode(raw).decode("ascii"),
                    "Accept": "application/json",
                },
                data={"grant_type": "client_credentials"},
            )
            if not 200 <= response.status_code < 300:
                raise self._error_from(response)
            body = self._json(response)
            token = body.get("access_token")
            expires_in = body.get("expires_in")
            if not isinstance(token, str) or not token or isinstance(expires_in, bool) or not isinstance(expires_in, (int, float)):
                raise PayPalError(status=response.status_code, name="INVALID_TOKEN_RESPONSE")
            self._token = token
            self._token_expires_at = _monotonic() + float(expires_in) - _TOKEN_MARGIN_SECONDS
            return token

    async def _api(
        self,
        method: str,
        path: str,
        *,
        extra_headers: Mapping[str, str] | None = None,
        json_body: Any = None,
        content: bytes | None = None,
    ) -> dict[str, Any]:
        async def attempt() -> tuple[str, httpx.Response]:
            token = await self._access_token()
            headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
            if extra_headers:
                headers.update(extra_headers)
            sent = await self._send(
                method, path, headers=headers, json_body=json_body, content=content
            )
            return token, sent

        token, response = await attempt()
        if response.status_code == 401:
            # The token was refused: forget it (unless another call already
            # replaced it), sign in again and try this call once more.
            async with self._lock:
                if self._token == token:
                    self._token = None
                    self._token_expires_at = 0.0
            token, response = await attempt()
        if not 200 <= response.status_code < 300:
            raise self._error_from(response)
        return self._json(response)

    # -- orders and captures ----------------------------------------------

    async def create_order(
        self,
        *,
        request_id: str,
        reference_id: str,
        custom_id: str,
        description: str,
        currency: str,
        value: str,
        return_url: str,
        cancel_url: str,
        brand_name: str,
    ) -> dict[str, Any]:
        code = _check_currency(currency)
        if minor_units(value, code) == 0:
            raise ValueError("amount must be greater than zero")
        if len(custom_id) > 255:
            raise ValueError("custom_id too long")
        if len(reference_id) > 256:
            raise ValueError("reference_id too long")
        if not _is_https(return_url) or not _is_https(cancel_url):
            raise ValueError("return and cancel addresses must be https")
        body = {
            "intent": "CAPTURE",
            "purchase_units": [
                {
                    "reference_id": reference_id,
                    "custom_id": custom_id,
                    "description": description,
                    "amount": {"currency_code": code, "value": value},
                }
            ],
            "payment_source": {
                "paypal": {
                    "experience_context": {
                        "return_url": return_url,
                        "cancel_url": cancel_url,
                        "user_action": "PAY_NOW",
                        "shipping_preference": "NO_SHIPPING",
                        "payment_method_preference": "IMMEDIATE_PAYMENT_REQUIRED",
                        "brand_name": brand_name,
                    }
                }
            },
        }
        return await self._api(
            "POST",
            "/v2/checkout/orders",
            extra_headers={"PayPal-Request-Id": request_id, "Prefer": "return=representation"},
            json_body=body,
        )

    async def capture_order(self, paypal_order_id: str, *, request_id: str) -> dict[str, Any]:
        oid = _path_id(paypal_order_id)
        return await self._api(
            "POST",
            f"/v2/checkout/orders/{oid}/capture",
            extra_headers={
                "PayPal-Request-Id": request_id,
                "Prefer": "return=representation",
                "Content-Type": "application/json",
            },
            json_body={},
        )

    async def get_order(self, paypal_order_id: str) -> dict[str, Any]:
        return await self._api("GET", f"/v2/checkout/orders/{_path_id(paypal_order_id)}")

    async def get_capture(self, capture_id: str) -> dict[str, Any]:
        return await self._api("GET", f"/v2/payments/captures/{_path_id(capture_id)}")

    # -- webhooks -----------------------------------------------------------

    async def verify_webhook_signature(
        self, *, webhook_id: str, headers: Mapping[str, str], raw_body: bytes
    ) -> bool:
        if not webhook_id:
            return False
        lowered = {str(k).lower(): v for k, v in headers.items()}
        values: dict[str, str] = {}
        for field, header in _WEBHOOK_HEADERS:
            value = lowered.get(header)
            if not isinstance(value, str) or not value.strip():
                return False
            values[field] = value.strip()
        if not _cert_url_ok(values["cert_url"]):
            return False
        try:
            if not isinstance(json.loads(raw_body), dict):
                return False
        except (ValueError, TypeError):
            return False
        enc = json.dumps
        content = (
            b'{"auth_algo":' + enc(values["auth_algo"]).encode()
            + b',"cert_url":' + enc(values["cert_url"]).encode()
            + b',"transmission_id":' + enc(values["transmission_id"]).encode()
            + b',"transmission_sig":' + enc(values["transmission_sig"]).encode()
            + b',"transmission_time":' + enc(values["transmission_time"]).encode()
            + b',"webhook_id":' + enc(webhook_id).encode()
            + b',"webhook_event":' + bytes(raw_body)
            + b"}"
        )
        result = await self._api(
            "POST",
            "/v1/notifications/verify-webhook-signature",
            extra_headers={"Content-Type": "application/json"},
            content=content,
        )
        return result.get("verification_status") == "SUCCESS"

    async def list_webhooks(self) -> list[dict[str, Any]]:
        body = await self._api("GET", "/v1/notifications/webhooks")
        hooks = body.get("webhooks")
        return [h for h in hooks if isinstance(h, dict)] if isinstance(hooks, list) else []

    async def create_webhook(self, *, url: str, event_names: Sequence[str]) -> dict[str, Any]:
        return await self._api(
            "POST",
            "/v1/notifications/webhooks",
            json_body={"url": url, "event_types": [{"name": n} for n in event_names]},
        )

    async def update_webhook_events(
        self, webhook_id: str, *, event_names: Sequence[str]
    ) -> dict[str, Any]:
        # PayPal supports only "replace" on a webhook.
        return await self._api(
            "PATCH",
            f"/v1/notifications/webhooks/{_path_id(webhook_id)}",
            extra_headers={"Content-Type": "application/json"},
            json_body=[
                {"op": "replace", "path": "/event_types", "value": [{"name": n} for n in event_names]}
            ],
        )


_client: PayPalClient | None = None
_client_key: tuple[str, str, str] | None = None


def get_paypal_client() -> PayPalClient:
    global _client, _client_key
    base = paypal_mode.paypal_api_base()
    creds = paypal_mode.paypal_credentials()
    if base is None or creds is None:
        raise PayPalNotConfigured("PayPal is not configured")
    key = (base, creds[0], creds[1])
    if _client is None or _client_key != key:
        _client = PayPalClient(base_url=base, client_id=creds[0], client_secret=creds[1])
        _client_key = key
    return _client


def reset_paypal_client() -> None:
    global _client, _client_key
    _client = None
    _client_key = None
