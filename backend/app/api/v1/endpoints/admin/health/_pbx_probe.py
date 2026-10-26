"""Read configured PBX control health without creating a call-control adapter."""
from __future__ import annotations

import asyncio
import json
import os

import httpx

from app.infrastructure.telephony.adapter_factory import AdapterRegistry

_PROBE_TIMEOUT_SECONDS = 3.0
_MAX_RESPONSE_BYTES = 64 * 1024


def _configured_targets():
    # Reuse the factory's environment/config precedence without auto-detection,
    # adapter construction, connection, registry mutation or ownership claims.
    if AdapterRegistry._resolve_key(None) != "asterisk":
        return None
    names = ("ASTERISK_ARI_HOST", "ASTERISK_ARI_PORT", "ASTERISK_ARI_USER",
             "ASTERISK_ARI_PASSWORD", "ASTERISK_GATEWAY_BASE_URL")
    values = [os.getenv(name, "") for name in names]
    if any(not value.strip() for value in values):
        return None
    host, port, user, password, gateway = values
    if any(character in host for character in "/?#@\\") or any(character.isspace() for character in host):
        return None
    try:
        port_number = int(port)
        if not 1 <= port_number <= 65535:
            return None
        ari = httpx.URL(scheme="http", host=host, port=port_number,
                        path="/ari/asterisk/info")
        gateway = httpx.URL(gateway.rstrip("/") + "/health")
        if (gateway.scheme not in {"http", "https"} or not gateway.host
                or gateway.username or gateway.password or gateway.query or gateway.fragment):
            return None
    except (ValueError, httpx.InvalidURL):
        return None
    return ari, gateway, httpx.BasicAuth(user, password)


def _ari_compatible(payload) -> bool:
    if not isinstance(payload, dict):
        return False
    system, status = payload.get("system"), payload.get("status")
    return (isinstance(system, dict) and isinstance(status, dict)
            and isinstance(system.get("entity_id"), str) and bool(system["entity_id"].strip())
            and isinstance(status.get("startup_time"), str) and bool(status["startup_time"].strip()))


def _gateway_compatible(payload) -> bool:
    if not isinstance(payload, dict):
        return False
    codecs, callbacks = payload.get("codecs"), payload.get("callback_protocol_versions")
    return (payload.get("status") == "ok" and payload.get("io_loop_healthy") is True
            and type(payload.get("protocol_version")) is int and payload["protocol_version"] == 2
            and isinstance(codecs, list) and all(isinstance(codec, str) for codec in codecs)
            and "pcmu" in codecs
            and isinstance(callbacks, list) and all(type(version) is int for version in callbacks)
            and 2 in callbacks)


async def _read_json(client, url, *, auth=None):
    async with client.stream("GET", url, auth=auth) as response:
        if response.status_code != 200:
            raise ValueError("Control probe did not acknowledge the read")
        body = bytearray()
        async for chunk in response.aiter_bytes():
            if len(body) + len(chunk) > _MAX_RESPONSE_BYTES:
                raise ValueError("Control probe response exceeded its limit")
            body.extend(chunk)
        return json.loads(body)


async def configured_pbx_health() -> bool | None:
    """None: unsupported/incomplete configuration; bool: observed control result.

    Only server configuration supplies targets. Credentials are used for ARI
    alone; redirects and inherited proxies are disabled. No response content,
    endpoint, credentials or exception details are logged or returned.
    """
    try:
        targets = _configured_targets()
    except Exception:
        return None
    if targets is None:
        return None
    ari, gateway, auth = targets
    try:
        async with asyncio.timeout(_PROBE_TIMEOUT_SECONDS):
            async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT_SECONDS,
                                         follow_redirects=False, trust_env=False) as client:
                async with asyncio.TaskGroup() as group:
                    ari_read = group.create_task(_read_json(client, ari, auth=auth))
                    gateway_read = group.create_task(_read_json(client, gateway))
                return _ari_compatible(ari_read.result()) and _gateway_compatible(gateway_read.result())
    except Exception:
        return False
