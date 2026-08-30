from __future__ import annotations

import base64
import json
import pytest
from fastapi import HTTPException
from starlette.requests import Request

import app.api.v1.endpoints.telephony_bridge as tb


_TOKEN = "telephony-phase1-validation-token"


class _FakeStateBackend:
    def __init__(self, exact_map: dict[str, str] | None = None):
        self.exact = exact_map or {}
        self.early: dict[str, list[bytes]] = {}

    def get_call_id_for_gateway_session(self, session_id: str) -> str | None:
        return self.exact.get(session_id)

    def append_early_audio(self, session_id: str, audio: bytes) -> int:
        lst = self.early.setdefault(session_id, [])
        lst.append(audio)
        return len(lst)


def _make_request(session_id: str, payload_obj: object | None, raw_body: bytes | None = None) -> Request:
    if raw_body is None:
        raw_body = json.dumps(payload_obj).encode("utf-8") if payload_obj is not None else b""

    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "https",
        "server": ("api.talkleeai.com", 443),
        "path": f"/api/v1/sip/telephony/audio/{session_id}",
        "raw_path": f"/api/v1/sip/telephony/audio/{session_id}".encode(),
        "query_string": b"",
        "headers": [
            (b"x-internal-service-token", _TOKEN.encode()),
            (b"content-type", b"application/json"),
        ],
        "client": ("127.0.0.1", 50000),
        "state": {},
    }

    async def receive():
        return {"type": "http.request", "body": raw_body, "more_body": False}

    return Request(scope, receive=receive)


@pytest.fixture(autouse=True)
def _setup_env(monkeypatch):
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", _TOKEN)
    tb._gateway_audio_last_sequence.clear()
    yield
    tb._gateway_audio_last_sequence.clear()


@pytest.mark.asyncio
async def test_receive_gateway_audio_rejects_invalid_json(monkeypatch):
    monkeypatch.setattr(tb, "get_state_backend", lambda: _FakeStateBackend())
    req = _make_request("asterisk-call-1", None, raw_body=b"{not-valid-json")

    with pytest.raises(HTTPException) as exc:
        await tb.receive_gateway_audio("asterisk-call-1", req)

    assert exc.value.status_code == 400
    assert "Invalid JSON" in exc.value.detail


@pytest.mark.asyncio
async def test_receive_gateway_audio_rejects_non_dict_payload(monkeypatch):
    monkeypatch.setattr(tb, "get_state_backend", lambda: _FakeStateBackend())
    req = _make_request("asterisk-call-1", [1, 2, 3])

    with pytest.raises(HTTPException) as exc:
        await tb.receive_gateway_audio("asterisk-call-1", req)

    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_receive_gateway_audio_rejects_session_id_mismatch(monkeypatch):
    monkeypatch.setattr(tb, "get_state_backend", lambda: _FakeStateBackend())
    valid_b64 = base64.b64encode(b"\x00" * 160).decode()
    payload = {"session_id": "different-session-id", "pcmu_base64": valid_b64, "codec": "pcmu"}
    req = _make_request("asterisk-call-1", payload)

    with pytest.raises(HTTPException) as exc:
        await tb.receive_gateway_audio("asterisk-call-1", req)

    assert exc.value.status_code == 400
    assert "mismatch" in exc.value.detail


@pytest.mark.asyncio
async def test_receive_gateway_audio_rejects_unsupported_codec(monkeypatch):
    monkeypatch.setattr(tb, "get_state_backend", lambda: _FakeStateBackend())
    valid_b64 = base64.b64encode(b"\x00" * 160).decode()
    payload = {"session_id": "asterisk-call-1", "pcmu_base64": valid_b64, "codec": "mp3"}
    req = _make_request("asterisk-call-1", payload)

    with pytest.raises(HTTPException) as exc:
        await tb.receive_gateway_audio("asterisk-call-1", req)

    assert exc.value.status_code == 400
    assert "Unsupported codec" in exc.value.detail


@pytest.mark.asyncio
async def test_receive_gateway_audio_rejects_missing_audio_payload(monkeypatch):
    monkeypatch.setattr(tb, "get_state_backend", lambda: _FakeStateBackend())
    payload = {"session_id": "asterisk-call-1", "codec": "pcmu"}
    req = _make_request("asterisk-call-1", payload)

    with pytest.raises(HTTPException) as exc:
        await tb.receive_gateway_audio("asterisk-call-1", req)

    assert exc.value.status_code == 400
    assert "Missing audio payload" in exc.value.detail


@pytest.mark.asyncio
async def test_receive_gateway_audio_rejects_invalid_base64(monkeypatch):
    monkeypatch.setattr(tb, "get_state_backend", lambda: _FakeStateBackend())
    payload = {"session_id": "asterisk-call-1", "pcmu_base64": "!!!not_base64@@@", "codec": "pcmu"}
    req = _make_request("asterisk-call-1", payload)

    with pytest.raises(HTTPException) as exc:
        await tb.receive_gateway_audio("asterisk-call-1", req)

    assert exc.value.status_code == 400
    assert "base64" in exc.value.detail.lower()


@pytest.mark.asyncio
async def test_receive_gateway_audio_rejects_non_alphabet_base64_that_decodes_empty_without_strict_mode(monkeypatch):
    monkeypatch.setattr(tb, "get_state_backend", lambda: _FakeStateBackend())
    payload = {"session_id": "asterisk-call-1", "pcmu_base64": "@@@@", "codec": "pcmu"}
    req = _make_request("asterisk-call-1", payload)

    with pytest.raises(HTTPException) as exc:
        await tb.receive_gateway_audio("asterisk-call-1", req)

    assert exc.value.status_code == 400
    assert "base64" in exc.value.detail.lower()


@pytest.mark.asyncio
async def test_receive_gateway_audio_requires_body_session_id(monkeypatch):
    monkeypatch.setattr(tb, "get_state_backend", lambda: _FakeStateBackend())
    payload = {
        "pcmu_base64": base64.b64encode(b"\x00" * 160).decode(),
        "codec": "pcmu",
    }
    req = _make_request("asterisk-call-1", payload)

    with pytest.raises(HTTPException) as exc:
        await tb.receive_gateway_audio("asterisk-call-1", req)

    assert exc.value.status_code == 400
    assert "session_id" in exc.value.detail


@pytest.mark.asyncio
async def test_receive_gateway_audio_rejects_linear_pcm_on_pcmu_route(monkeypatch):
    monkeypatch.setattr(tb, "get_state_backend", lambda: _FakeStateBackend())
    payload = {
        "session_id": "asterisk-call-1",
        "pcmu_base64": base64.b64encode(b"\x00" * 320).decode(),
        "codec": "pcm16",
    }
    req = _make_request("asterisk-call-1", payload)

    with pytest.raises(HTTPException) as exc:
        await tb.receive_gateway_audio("asterisk-call-1", req)

    assert exc.value.status_code == 400
    assert "Unsupported codec" in exc.value.detail


@pytest.mark.asyncio
async def test_protocol_v2_duplicate_sequence_is_acknowledged_but_not_routed_twice(monkeypatch):
    fake_sb = _FakeStateBackend({"asterisk-call-1": "call-uuid-123"})
    routed = []

    async def fake_on_audio(call_id, audio):
        routed.append((call_id, audio))

    monkeypatch.setattr(tb, "get_state_backend", lambda: fake_sb)
    monkeypatch.setattr(tb, "_on_audio_received", fake_on_audio)
    audio = b"\x55" * 320
    payload = {
        "protocol_version": 2,
        "sequence": 7,
        "frame_count": 2,
        "ptime_ms": 20,
        "payload_bytes": len(audio),
        "session_id": "asterisk-call-1",
        "pcmu_base64": base64.b64encode(audio).decode(),
        "codec": "pcmu",
    }

    first = await tb.receive_gateway_audio("asterisk-call-1", _make_request("asterisk-call-1", payload))
    duplicate = await tb.receive_gateway_audio("asterisk-call-1", _make_request("asterisk-call-1", payload))

    assert first.status_code == 200
    assert duplicate.status_code == 200
    assert routed == [("call-uuid-123", audio)]
    assert json.loads(duplicate.body)["status"] == "duplicate"


@pytest.mark.asyncio
async def test_protocol_v2_metadata_must_match_decoded_audio(monkeypatch):
    monkeypatch.setattr(tb, "get_state_backend", lambda: _FakeStateBackend())
    audio = b"\x55" * 320
    payload = {
        "protocol_version": 2,
        "sequence": 1,
        "frame_count": 1,
        "ptime_ms": 20,
        "payload_bytes": len(audio),
        "session_id": "asterisk-call-1",
        "pcmu_base64": base64.b64encode(audio).decode(),
        "codec": "pcmu",
    }

    with pytest.raises(HTTPException) as exc:
        await tb.receive_gateway_audio("asterisk-call-1", _make_request("asterisk-call-1", payload))

    assert exc.value.status_code == 400
    assert "frame_count" in exc.value.detail


@pytest.mark.asyncio
async def test_receive_gateway_audio_rejects_empty_audio_bytes(monkeypatch):
    monkeypatch.setattr(tb, "get_state_backend", lambda: _FakeStateBackend())
    payload = {"session_id": "asterisk-call-1", "pcmu_base64": "", "codec": "pcmu"}
    req = _make_request("asterisk-call-1", payload)

    with pytest.raises(HTTPException) as exc:
        await tb.receive_gateway_audio("asterisk-call-1", req)

    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_receive_gateway_audio_rejects_invalid_frame_length(monkeypatch):
    monkeypatch.setattr(tb, "get_state_backend", lambda: _FakeStateBackend())
    # 100 bytes is not a multiple of 160 (20ms PCMU)
    bad_length_b64 = base64.b64encode(b"\x00" * 100).decode()
    payload = {"session_id": "asterisk-call-1", "pcmu_base64": bad_length_b64, "codec": "pcmu"}
    req = _make_request("asterisk-call-1", payload)

    with pytest.raises(HTTPException) as exc:
        await tb.receive_gateway_audio("asterisk-call-1", req)

    assert exc.value.status_code == 400
    assert "Invalid audio frame length" in exc.value.detail


@pytest.mark.asyncio
async def test_receive_gateway_audio_accepts_valid_payload_and_routes(monkeypatch):
    fake_sb = _FakeStateBackend({"asterisk-call-1": "call-uuid-123"})
    routed = []

    async def fake_on_audio(call_id, audio):
        routed.append((call_id, audio))

    monkeypatch.setattr(tb, "get_state_backend", lambda: fake_sb)
    monkeypatch.setattr(tb, "_on_audio_received", fake_on_audio)

    audio_bytes = b"\x55" * 320  # 2 frames (40ms)
    payload = {
        "session_id": "asterisk-call-1",
        "pcmu_base64": base64.b64encode(audio_bytes).decode(),
        "codec": "pcmu",
    }
    req = _make_request("asterisk-call-1", payload)

    resp = await tb.receive_gateway_audio("asterisk-call-1", req)

    assert resp.status_code == 200
    assert routed == [("call-uuid-123", audio_bytes)]
    assert fake_sb.early == {}
