"""Gateway control plane: fail closed on a missing token; log the running build.

2026-09-09 telephony edge audit. The audio-ingress side (gateway → backend) has
always refused everything without INTERNAL_SERVICE_TOKEN; the control side
(backend → gateway) silently sent no Authorization header when
VOICE_GATEWAY_AUTH_TOKEN was unset. And the health check proved protocol
compatibility but never said which gateway build was running — prod sat four
fixes behind source with no log line to show it.
"""
from __future__ import annotations

import logging

import pytest

from app.infrastructure.telephony.asterisk_adapter import AsteriskAdapter


def _adapter() -> AsteriskAdapter:
    adapter = AsteriskAdapter.__new__(AsteriskAdapter)
    adapter._session = object()  # "connected"
    adapter._gateway_session = None
    adapter._gateway_base_url = "http://127.0.0.1:18080"
    return adapter


@pytest.mark.asyncio
async def test_control_call_refuses_to_go_out_without_the_token(monkeypatch):
    monkeypatch.delenv("VOICE_GATEWAY_AUTH_TOKEN", raising=False)
    adapter = _adapter()

    class _MustNotBeUsed:
        closed = False

        def request(self, *a, **k):
            raise AssertionError("no HTTP request may be made without a token")

    adapter._gateway_session = _MustNotBeUsed()

    with pytest.raises(RuntimeError, match="VOICE_GATEWAY_AUTH_TOKEN is not set"):
        await adapter._gateway("POST", "/v1/sessions/stop", {"session_id": "s"})


@pytest.mark.asyncio
async def test_control_call_carries_the_bearer_when_set(monkeypatch):
    monkeypatch.setenv("VOICE_GATEWAY_AUTH_TOKEN", "gateway_" + "b" * 32)
    adapter = _adapter()
    seen = {}

    class _Resp:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def json(self, content_type=None):
            return {"ok": True}

    class _Session:
        closed = False

        def request(self, method, url, json=None, headers=None, timeout=None):
            seen.update({"method": method, "url": url, "headers": headers})
            return _Resp()

    adapter._gateway_session = _Session()
    assert await adapter._gateway("POST", "/v1/sessions/stop", {"session_id": "s"}) == {"ok": True}
    assert seen["headers"] == {"Authorization": "Bearer gateway_" + "b" * 32}
    assert seen["url"] == "http://127.0.0.1:18080/v1/sessions/stop"


def test_gateway_build_identity_is_logged_once_per_change(caplog):
    AsteriskAdapter._last_gateway_build_sha = None
    payload = {"status": "ok", "build_sha": "a4aa0c56" + "0" * 32, "protocol_version": 2, "codecs": ["pcmu"]}
    with caplog.at_level(logging.INFO, logger="app.infrastructure.telephony.asterisk_adapter"):
        AsteriskAdapter._note_gateway_build(payload)
        AsteriskAdapter._note_gateway_build(payload)  # same build → no second line
        AsteriskAdapter._note_gateway_build({**payload, "build_sha": "deadbeef" + "0" * 32})
    lines = [r.getMessage() for r in caplog.records if "voice_gateway build_sha=" in r.getMessage()]
    assert len(lines) == 2
    assert "a4aa0c56" in lines[0] and "deadbeef" in lines[1]
    # Garbage payloads never raise — health_check must keep working.
    AsteriskAdapter._note_gateway_build(None)
    AsteriskAdapter._note_gateway_build({"build_sha": 7})
