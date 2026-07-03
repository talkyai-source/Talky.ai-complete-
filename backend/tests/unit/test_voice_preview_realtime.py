"""Unit tests for the realtime (gpt-realtime-2) voice-preview branch of
`ai_options/preview.py`.

Covers:
  - realtime branch returns float32 audio_base64 for a valid voice (OpenAI
    speech HTTP call is mocked — no network),
  - the result is cached under the `realtime-` namespace and served from disk
    on the second call (no second HTTP call),
  - an unsupported realtime voice and an OpenAI API error each raise a clean
    HTTPException without touching the cascaded providers,
  - the cascaded path (provider is None) never invokes the realtime synthesis.
"""
from __future__ import annotations

import base64
import struct

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints.ai_options import preview as preview_mod
from app.api.v1.endpoints.ai_options.preview import (
    VoicePreviewRequest,
    _realtime_preview_cache_key,
    _synthesize_realtime_preview,
    preview_voice,
)


# ---------------------------------------------------------------------------
# Fake aiohttp session so no real HTTP is performed.
# ---------------------------------------------------------------------------


class _FakeResp:
    def __init__(self, status: int, body: bytes):
        self.status = status
        self._body = body

    async def read(self) -> bytes:
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeSession:
    """Records how many POSTs happen and returns a canned response."""

    calls: list[dict] = []

    def __init__(self, status: int, body: bytes):
        self._status = status
        self._body = body

    def __call__(self, *args, **kwargs):
        # aiohttp.ClientSession(timeout=...) is called to construct the session.
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def post(self, url, headers=None, json=None):
        _FakeSession.calls.append({"url": url, "headers": headers, "json": json})
        return _FakeResp(self._status, self._body)


def _install_fake_aiohttp(monkeypatch, *, status: int, body: bytes):
    import aiohttp

    _FakeSession.calls = []
    fake = _FakeSession(status, body)
    monkeypatch.setattr(aiohttp, "ClientSession", fake)
    return fake


def _pcm16_bytes(samples: list[int]) -> bytes:
    return struct.pack(f"<{len(samples)}h", *samples)


def _decode_float32(audio_base64: str) -> list[float]:
    raw = base64.b64decode(audio_base64)
    count = len(raw) // 4
    return list(struct.unpack(f"<{count}f", raw))


@pytest.fixture(autouse=True)
def _isolate_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(preview_mod, "_VOICE_PREVIEW_CACHE_DIR", tmp_path / "cache")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key")


# ---------------------------------------------------------------------------
# Happy path + caching
# ---------------------------------------------------------------------------


async def test_realtime_valid_voice_returns_float32_audio(monkeypatch):
    pcm = _pcm16_bytes([0, 16384, -16384, 32767, -32768])
    _install_fake_aiohttp(monkeypatch, status=200, body=pcm)

    resp = await preview_voice(
        VoicePreviewRequest(voice_id="alloy", text="hi", provider="realtime")
    )

    assert resp.voice_id == "alloy"
    floats = _decode_float32(resp.audio_base64)
    assert len(floats) == 5
    assert floats[0] == pytest.approx(0.0)
    assert floats[1] == pytest.approx(0.5, abs=1e-4)
    assert -1.0 <= min(floats) and max(floats) <= 1.0
    assert resp.duration_seconds > 0
    assert len(_FakeSession.calls) == 1
    # It hit the speech endpoint with the requested voice + pcm format.
    call = _FakeSession.calls[0]
    assert call["json"]["voice"] == "alloy"
    assert call["json"]["response_format"] == "pcm"
    assert call["json"]["model"] == "gpt-4o-mini-tts"


async def test_realtime_marin_previews_as_itself(monkeypatch):
    _install_fake_aiohttp(monkeypatch, status=200, body=_pcm16_bytes([100, 200]))

    await preview_voice(
        VoicePreviewRequest(voice_id="marin", text="hi", provider="realtime")
    )

    # marin is a valid gpt-4o-mini-tts voice (live-verified) → previews as itself,
    # no substitution.
    assert _FakeSession.calls[0]["json"]["voice"] == "marin"


async def test_realtime_result_is_cached(monkeypatch):
    _install_fake_aiohttp(monkeypatch, status=200, body=_pcm16_bytes([1, 2, 3, 4]))

    req = VoicePreviewRequest(voice_id="verse", text="hi", provider="realtime")
    first = await preview_voice(req)
    assert len(_FakeSession.calls) == 1

    # Second call must be served from disk cache — no new HTTP POST.
    second = await preview_voice(req)
    assert len(_FakeSession.calls) == 1
    assert second.audio_base64 == first.audio_base64
    assert second.latency_ms == 0.0

    # Cache file lives under the realtime- namespace.
    key = _realtime_preview_cache_key("verse")
    assert preview_mod._load_preview_cache(key) is not None


# ---------------------------------------------------------------------------
# Error handling — clean HTTPException, cascaded path untouched
# ---------------------------------------------------------------------------


async def test_realtime_unsupported_voice_raises_400(monkeypatch):
    fake = _install_fake_aiohttp(monkeypatch, status=200, body=b"")

    with pytest.raises(HTTPException) as exc:
        await _synthesize_realtime_preview("not-a-voice", "hi")
    assert exc.value.status_code == 400
    # No HTTP call was attempted for an unsupported voice.
    assert _FakeSession.calls == []


async def test_realtime_api_error_raises_502(monkeypatch):
    _install_fake_aiohttp(monkeypatch, status=401, body=b'{"error":"bad key"}')

    with pytest.raises(HTTPException) as exc:
        await preview_voice(
            VoicePreviewRequest(voice_id="alloy", text="hi", provider="realtime")
        )
    assert exc.value.status_code == 502
    assert "OpenAI speech API error" in exc.value.detail


async def test_realtime_missing_api_key_raises_503(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    fake = _install_fake_aiohttp(monkeypatch, status=200, body=b"")

    with pytest.raises(HTTPException) as exc:
        await _synthesize_realtime_preview("alloy", "hi")
    assert exc.value.status_code == 503
    assert _FakeSession.calls == []


async def test_cascaded_path_does_not_invoke_realtime(monkeypatch):
    """provider=None must never route to the realtime synth."""
    def _boom(*a, **k):
        raise AssertionError("realtime synth called for a cascaded request")

    monkeypatch.setattr(preview_mod, "_synthesize_realtime_preview", _boom)

    # Force the cascaded catalogs to be empty so detection resolves to the
    # "Unknown voice_id" 400 without any network — proving we took the
    # cascaded branch, not the realtime one.
    async def _empty():
        return []

    monkeypatch.setattr(preview_mod, "_get_live_cartesia_voices", _empty)
    monkeypatch.setattr(preview_mod, "_get_deepgram_voices_for_current_key", _empty)
    monkeypatch.setattr(preview_mod, "_is_cartesia_voice", lambda v: False)
    monkeypatch.setattr(preview_mod, "_is_google_voice", lambda v: False)
    monkeypatch.setattr(preview_mod, "_english_deepgram_static_voices", lambda: [])

    async def _no_el(_v):
        return None

    monkeypatch.setattr(preview_mod, "_find_elevenlabs_voice", _no_el)
    monkeypatch.setattr(preview_mod, "_find_cartesia_voice", lambda v: None)
    monkeypatch.setattr(preview_mod, "_find_google_voice", lambda v: None)

    with pytest.raises(HTTPException) as exc:
        await preview_voice(VoicePreviewRequest(voice_id="ghost", text="hi"))
    assert exc.value.status_code == 400
    assert "Unknown voice_id" in exc.value.detail
