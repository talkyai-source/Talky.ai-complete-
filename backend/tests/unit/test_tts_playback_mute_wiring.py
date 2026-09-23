"""Regression test for test-agent-mute-only-on-greeting (2026-09-23).

PRODUCTION EVIDENCE
--------------------
Calls 4a9dd845 and 7dbf415f: a browser Test-agent session started with
``allow_barge_in=false`` (the default) correctly set ``mute_during_tts=True``
on the resolved config, but ``voice_orchestrator.send_greeting``'s STT
mute/unmute pair only ever ran for the ONE-TIME opening greeting. Every later
reply and every SilenceMonitor re-greet nudge went through
``tts_playback.synthesize_and_send``, which never muted STT — so the
browser mic stayed hot while the agent's own voice played, and its own
"Hello?"/"Hello??" nudges (and later replies) were re-transcribed as caller
speech (18+ identical-text interims on 4a9dd845).

Telephony calls never set ``mute_during_tts`` on the CallSession at all
(agent_first.py / lifecycle.py, out of this fix's fence) — this test proves
that path stays a no-op (mute_during_tts is False by design there, per
telephony_session_config.py's own comment).
"""
from __future__ import annotations

import types

import pytest

from app.domain.services.voice_pipeline.tts_playback import TtsPlayback


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


class _Gateway:
    def __init__(self):
        self.sent: list[bytes] = []

    async def send_audio(self, call_id, raw):
        self.sent.append(raw)

    async def clear_output_buffer(self, call_id):
        return {"ok": True}

    async def flush_tts_buffer(self, call_id):
        return None


class _SttProvider:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []  # ("mute"|"unmute", call_id)

    async def mute(self, call_id):
        self.calls.append(("mute", call_id))

    async def unmute(self, call_id):
        self.calls.append(("unmute", call_id))


class _Latency:
    def mark_tts_first_chunk(self, *a, **k): pass
    def mark_response_start(self, *a, **k): pass
    def mark_audio_start(self, *a, **k): pass
    def mark_tts_end(self, *a, **k): pass
    def mark_completed(self, *a, **k): pass
    def mark_interrupted(self, *a, **k): pass


class _Pipeline:
    def __init__(self, stt_provider):
        self.tts_provider = _Provider([b"\x01\x02" * 80])
        self.media_gateway = _Gateway()
        self.latency_tracker = _Latency()
        self.tts_sample_rate = 16000
        self.stt_provider = stt_provider

    def _record_silent_turn(self, call_id, reason):
        pass


def _session(*, mute_during_tts):
    return types.SimpleNamespace(
        call_id="4a9dd845-0000-0000-0000-000000000000",
        tts_active=True,
        voice_id="v1",
        turn_id=1,
        _mute_during_tts=mute_during_tts,
    )


@pytest.mark.asyncio
async def test_browser_test_agent_reply_mutes_and_unmutes_stt(monkeypatch):
    """THE FIX. A Test-agent session with allow_barge_in=false (mute_during_tts
    True) must mute STT for EVERY reply, not just the greeting — this call is
    an ordinary turn reply, not a greeting."""
    monkeypatch.setattr(
        "app.domain.services.voice_pipeline.tts_playback._STT_UNMUTE_TAIL_S", 0.0
    )
    stt = _SttProvider()
    pipe = _Pipeline(stt)
    pb = TtsPlayback(pipe)

    await pb.synthesize_and_send(
        _session(mute_during_tts=True),
        "Zoe here from Dojo, is now a good time?",
        None,
        track_latency=False,
    )

    assert stt.calls, (
        "STT was never muted for an ordinary reply on a mute_during_tts=True "
        "session — the agent's own voice echoes into the mic and gets "
        "transcribed as caller speech (4a9dd845, 7dbf415f)"
    )
    kinds = [k for k, _ in stt.calls]
    assert kinds == ["mute", "unmute"], f"expected exactly one mute then one unmute; got {kinds}"
    assert all(cid == "4a9dd845-0000-0000-0000-000000000000" for _, cid in stt.calls)


@pytest.mark.asyncio
async def test_telephony_configured_session_is_unaffected():
    """Verify: telephony keeps mute_during_tts False by design — a session
    that never sets `_mute_during_tts` (real phone calls; agent_first.py and
    lifecycle.py never set this attribute) must never mute STT here."""
    stt = _SttProvider()
    pipe = _Pipeline(stt)
    pb = TtsPlayback(pipe)

    # No `_mute_during_tts` attribute at all — the real shape of a
    # telephony CallSession, which this fix's fence never touches.
    session = types.SimpleNamespace(
        call_id="6aaeb4dd-0000-0000-0000-000000000000",
        tts_active=True,
        voice_id="v1",
        turn_id=1,
    )

    await pb.synthesize_and_send(session, "How can I help?", None, track_latency=False)

    assert stt.calls == [], (
        f"telephony calls must never mute STT during ordinary playback "
        f"(mute_during_tts is False by design there); got {stt.calls!r}"
    )


@pytest.mark.asyncio
async def test_explicit_mute_during_tts_false_is_also_unaffected():
    """Same as above but with the attribute explicitly False (e.g. a Test-agent
    session started with allow_barge_in=true)."""
    stt = _SttProvider()
    pipe = _Pipeline(stt)
    pb = TtsPlayback(pipe)

    await pb.synthesize_and_send(
        _session(mute_during_tts=False), "How can I help?", None, track_latency=False
    )

    assert stt.calls == []
