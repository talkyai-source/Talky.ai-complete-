"""Regression test for test-agent-mute-only-on-greeting, review round 2
(2026-09-24).

PRODUCTION EVIDENCE (review of 91b61694)
-----------------------------------------
91b61694 muted STT before the send loop and unmuted a fixed
``STT_UNMUTE_TAIL_S`` (0.25s) after the LAST byte was PUSHED to the gateway.
But ``BrowserMediaGateway.send_audio`` has no real-time pacing, and the
Test-agent BROWSER client queues chunks and paces PLAYBACK itself
(test-agent-button.tsx's ``nextPlayTimeRef``) -- so most of a multi-second
reply was still audibly PLAYING through a live mic well after the server had
already finished pushing it and the fixed tail had already unmuted STT. On
4a9dd845 / 7dbf415f the self-echo barge-in landed AFTER every chunk had been
pushed -- exactly the window this fixed tail does not cover.

``voice_orchestrator.send_greeting`` never had this bug: it calls
``media_gateway.start_playback_tracking`` before streaming and, after the
flush, sends ``{"type": "tts_audio_complete"}`` and awaits
``media_gateway.wait_for_playback_complete`` -- which only resolves once the
BROWSER reports back (test-agent-button.tsx sends
``{"type": "playback_complete"}`` from its ``AudioBufferSourceNode.onended``
callback, i.e. real playback, not a guess). ``synthesize_and_send`` now does
the same whenever ``session._mute_during_tts`` is set and the gateway
supports it.

This test drives the REAL ``BrowserMediaGateway`` (not a mock of the
collaborator under test) end-to-end through ``TtsPlayback.synthesize_and_send``
and asserts that STT is not unmuted until AFTER the browser's own
``playback_complete`` confirmation arrives -- not merely after the send loop
finishes.
"""
from __future__ import annotations

import asyncio

import pytest

from app.domain.services.voice_pipeline.tts_playback import TtsPlayback
from app.infrastructure.telephony.browser_media_gateway import BrowserMediaGateway

CALL_ID = "4a9dd845-0000-0000-0000-000000000000"


class _Chunk:
    def __init__(self, data: bytes):
        self.data = data


class _Provider:
    name = "fake-tts"

    def __init__(self, chunks):
        self._chunks = list(chunks)

    def stream_synthesize(self, text, **kwargs):
        chunks = self._chunks

        async def _gen():
            for c in chunks:
                yield _Chunk(c)

        return _gen()


class _Websocket:
    """Fake FastAPI WebSocket. ``send_bytes`` is the gateway's real audio
    path (BrowserMediaGateway._send_payload); ``send_json`` is
    synthesize_and_send's own ``tts_audio_complete`` control message -- the
    SAME single websocket instance carries both in production."""

    def __init__(self):
        self.sent_bytes: list[bytes] = []
        self.sent_json: list[dict] = []

    async def send_bytes(self, payload: bytes):
        self.sent_bytes.append(payload)

    async def send_json(self, payload: dict):
        self.sent_json.append(payload)


class _SttProvider:
    def __init__(self):
        self.calls: list[str] = []

    async def mute(self, call_id):
        self.calls.append("mute")

    async def unmute(self, call_id):
        self.calls.append("unmute")


class _Latency:
    def mark_tts_first_chunk(self, *a, **k): pass
    def mark_response_start(self, *a, **k): pass
    def mark_audio_start(self, *a, **k): pass
    def mark_tts_end(self, *a, **k): pass
    def mark_completed(self, *a, **k): pass
    def mark_interrupted(self, *a, **k): pass


class _Pipeline:
    def __init__(self, stt_provider, gateway, chunk_bytes):
        self.tts_provider = _Provider([chunk_bytes])
        self.media_gateway = gateway
        self.latency_tracker = _Latency()
        self.tts_sample_rate = 16000
        self.stt_provider = stt_provider

    def _record_silent_turn(self, call_id, reason):
        pass


class _Session:
    def __init__(self, call_id):
        self.call_id = call_id
        self.tts_active = True
        self.voice_id = "v1"
        self.turn_id = 1
        self._mute_during_tts = True


@pytest.mark.asyncio
async def test_unmute_waits_for_the_browsers_real_playback_complete_signal(monkeypatch):
    """THE FIX. Unmute must not fire on a fixed post-send tail alone -- it
    must wait for the browser's own playback_complete confirmation when the
    gateway supports it (real BrowserMediaGateway does)."""
    # If the fixed-tail path were still taken, this would let it fire almost
    # instantly -- proving the assertion below is non-vacuous either way.
    monkeypatch.setattr(
        "app.domain.services.voice_pipeline.tts_playback._STT_UNMUTE_TAIL_S", 0.0
    )

    gateway = BrowserMediaGateway()
    ws = _Websocket()
    await gateway.on_call_started(CALL_ID, {"websocket": ws})

    stt = _SttProvider()
    # Bigger than the gateway's ~1920-byte send threshold at 16kHz/16-bit, so
    # send_audio really pushes bytes through _send_payload (playback_bytes_sent
    # advances) with a small remainder left for flush_audio_buffer.
    chunk_bytes = b"\x01\x00" * 2000
    pipe = _Pipeline(stt, gateway, chunk_bytes)
    pb = TtsPlayback(pipe)

    unmuted_before_browser_confirmed = None

    async def _delayed_browser_confirmation():
        # The real browser only reports playback_complete once it has
        # actually finished PLAYING the queued audio -- not the instant the
        # server finishes pushing it (test-agent-button.tsx onended).
        await asyncio.sleep(0.05)
        nonlocal unmuted_before_browser_confirmed
        unmuted_before_browser_confirmed = "unmute" in stt.calls
        gateway.mark_playback_complete(CALL_ID)

    confirm_task = asyncio.create_task(_delayed_browser_confirmation())

    await pb.synthesize_and_send(
        _Session(CALL_ID), "Zoe here, is now a good time?", ws, track_latency=False
    )
    await confirm_task

    assert stt.calls == ["mute", "unmute"], f"expected exactly one mute/unmute pair; got {stt.calls!r}"
    assert unmuted_before_browser_confirmed is False, (
        "STT was unmuted BEFORE the browser confirmed real playback had "
        "finished -- a fixed post-send tail, not the real signal, was used "
        "(4a9dd845, 7dbf415f)"
    )
    assert {"type": "tts_audio_complete"} in ws.sent_json, (
        "synthesize_and_send never told the browser to expect a playback-"
        "complete confirmation"
    )


@pytest.mark.asyncio
async def test_telephony_style_gateway_without_playback_tracking_is_unaffected():
    """A gateway that does not implement start_playback_tracking /
    wait_for_playback_complete (the telephony shape) must fall back to the
    old fixed-tail unmute -- no new await, no new message."""

    class _PlainGateway:
        def __init__(self):
            self.sent: list[bytes] = []

        async def send_audio(self, call_id, raw):
            self.sent.append(raw)

        async def clear_output_buffer(self, call_id):
            return {"ok": True}

        async def flush_tts_buffer(self, call_id):
            return None

    stt = _SttProvider()
    gateway = _PlainGateway()
    pipe = _Pipeline(stt, gateway, b"\x01\x02" * 80)
    pb = TtsPlayback(pipe)

    ws = _Websocket()
    await pb.synthesize_and_send(
        _Session(CALL_ID), "How can I help?", ws, track_latency=False
    )

    assert stt.calls == ["mute", "unmute"]
    assert ws.sent_json == [], (
        "a gateway with no playback-tracking support must never get a "
        "tts_audio_complete message from this path"
    )
