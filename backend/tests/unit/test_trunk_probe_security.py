from __future__ import annotations

import asyncio
import socket
from types import SimpleNamespace

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


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", ["404", "200", "timeout", "unrelated"])
async def test_udp_reports_real_response_or_local_timeout(monkeypatch, reply):
    from app.api.v1.endpoints.telephony_sip import trunk_probe

    _patch_resolution(monkeypatch, ["8.8.8.8"])

    class Socket:
        def settimeout(self, _timeout):
            pass

        def sendto(self, data, peer):
            self.data, self.peer = data, peer

        def recvfrom(self, _size):
            if reply == "timeout" or getattr(self, "replied", False):
                raise socket.timeout()
            self.replied = True
            body = self.data.decode().split("\r\n", 1)[1]
            if reply == "unrelated":
                body = body.replace("Call-ID:", "Unrelated-ID:")
            return (f"SIP/2.0 {reply if reply != 'unrelated' else '200'} Reply\r\n" + body).encode(), self.peer

        def close(self):
            pass

    monkeypatch.setattr(trunk_probe, "socket", SimpleNamespace(**{**vars(socket), "socket": lambda *_args: Socket()}))
    result = await probe_sip_endpoint(host="sip.example.com", port=5060, transport="udp", timeout=.01)
    if reply in {"timeout", "unrelated"}:
        assert result["ok"] is False
        assert result["error"] == "timeout"
        assert result["timeout_code"] == 408
        assert result.get("sip_code") is None
    else:
        assert result["ok"] is True
        assert result["sip_code"] == reply
        assert result.get("timeout_code") is None
