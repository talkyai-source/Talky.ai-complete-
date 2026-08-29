from __future__ import annotations

import asyncio
import socket

import pytest

from app.api.v1.endpoints.telephony_sip.trunk_probe import (
    probe_sip_endpoint,
    resolve_sip_target,
)


def _patch_resolution(monkeypatch, addresses: list[str]) -> None:
    loop = asyncio.get_running_loop()

    async def getaddrinfo(_host, port, *, family, type):
        assert family == socket.AF_UNSPEC
        return [(socket.AF_INET, type, 0, "", (address, port)) for address in addresses]

    monkeypatch.setattr(loop, "getaddrinfo", getaddrinfo)


@pytest.mark.asyncio
async def test_resolution_rejects_private_or_mixed_dns_answers(monkeypatch):
    monkeypatch.delenv("TELEPHONY_ALLOW_PRIVATE_SIP_TARGETS", raising=False)
    _patch_resolution(monkeypatch, ["8.8.8.8", "10.0.0.9"])
    with pytest.raises(PermissionError, match="non-public"):
        await resolve_sip_target(
            host="sip.example.com", port=5060, socktype=socket.SOCK_DGRAM
        )


@pytest.mark.asyncio
async def test_resolution_returns_a_pinned_public_peer(monkeypatch):
    monkeypatch.delenv("TELEPHONY_ALLOW_PRIVATE_SIP_TARGETS", raising=False)
    _patch_resolution(monkeypatch, ["8.8.8.8"])
    family, peer = await resolve_sip_target(
        host="sip.example.com", port=5060, socktype=socket.SOCK_DGRAM
    )
    assert family == socket.AF_INET
    assert peer == ("8.8.8.8", 5060)


@pytest.mark.asyncio
async def test_probe_reports_unsafe_target_without_opening_a_socket():
    result = await probe_sip_endpoint(
        host="127.0.0.1", port=5060, transport="tcp", timeout=0.01
    )
    assert result["ok"] is False
    assert result["error"] == "unsafe_target"


@pytest.mark.asyncio
async def test_private_target_escape_hatch_is_explicit(monkeypatch):
    monkeypatch.setenv("TELEPHONY_ALLOW_PRIVATE_SIP_TARGETS", "on")
    _patch_resolution(monkeypatch, ["10.0.0.9"])
    _family, peer = await resolve_sip_target(
        host="pbx.internal", port=5060, socktype=socket.SOCK_DGRAM
    )
    assert peer == ("10.0.0.9", 5060)
