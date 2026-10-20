"""Noise suppression on the caller audio that goes to speech recognition.

Contract:
- off unless CALLER_NOISE_SUPPRESSION=webrtc; off means byte-identical audio
- only 16 kHz mono PCM16; anything else stays off
- 10 ms frames; no audio is ever lost: a remainder waits for the next chunk,
  and an engine failure passes the raw audio through for the rest of the call
- only the STT feed is cleaned; the raw-level signals (barge-in onset, silence
  monitor) keep reading the original frames
"""
from __future__ import annotations

import asyncio
import math
import struct
from unittest.mock import MagicMock

import pytest

from app.domain.services.voice_pipeline import caller_denoise
from app.domain.services.voice_pipeline.caller_denoise import FRAME_BYTES, CallerDenoiser


class _Engine:
    """Records frames; 'cleans' by halving every sample."""

    def __init__(self, fail_after=None):
        self.frames = []
        self.fail_after = fail_after

    def __call__(self, frame: bytes) -> bytes:
        if self.fail_after is not None and len(self.frames) >= self.fail_after:
            raise RuntimeError("engine broke")
        self.frames.append(frame)
        samples = struct.unpack(f"<{len(frame) // 2}h", frame)
        return struct.pack(f"<{len(samples)}h", *(s // 2 for s in samples))


def _pcm(n_samples: int, value: int = 1000) -> bytes:
    return struct.pack(f"<{n_samples}h", *([value] * n_samples))


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setenv("CALLER_NOISE_SUPPRESSION", "webrtc")
    monkeypatch.delenv("CALLER_NOISE_SUPPRESSION_LEVEL", raising=False)


def test_off_by_default_is_byte_identical(monkeypatch):
    monkeypatch.delenv("CALLER_NOISE_SUPPRESSION", raising=False)
    made = []
    d = CallerDenoiser("c1", sample_rate=16000, engine_factory=lambda level: made.append(level))
    chunk = _pcm(320)
    assert d.process(chunk) is chunk
    assert d.enabled is False and made == []


def test_20ms_chunk_is_two_10ms_frames_with_no_added_delay(on):
    eng = _Engine()
    d = CallerDenoiser("c1", sample_rate=16000, engine_factory=lambda level: eng)
    out = d.process(_pcm(320))  # 20 ms
    assert [len(f) for f in eng.frames] == [FRAME_BYTES, FRAME_BYTES]
    assert out == _pcm(320, 500)


def test_a_remainder_waits_for_the_next_chunk_and_nothing_is_lost(on):
    eng = _Engine()
    d = CallerDenoiser("c1", sample_rate=16000, engine_factory=lambda level: eng)
    samples = list(range(2, 642, 2))  # 320 distinct, even samples
    raw = struct.pack("<320h", *samples)
    first = d.process(raw[:500])   # 1 frame + 90-sample remainder
    second = d.process(raw[500:])  # remainder + 70 = 1 frame
    assert len(first) == FRAME_BYTES and len(second) == FRAME_BYTES
    # content and order survive the split: every sample halved, none moved
    assert struct.unpack("<320h", first + second) == tuple(s // 2 for s in samples)


def test_an_engine_failure_passes_raw_audio_for_the_rest_of_the_call(on, caplog):
    eng = _Engine(fail_after=1)
    d = CallerDenoiser("c1", sample_rate=16000, engine_factory=lambda level: eng)
    out = d.process(_pcm(320))  # frame 1 cleaned, frame 2 fails -> raw
    assert out == _pcm(160, 500) + _pcm(160)
    assert d.enabled is False
    later = _pcm(160, 7)
    assert d.process(later) is later
    assert sum("caller_denoise_disabled" in r.getMessage() for r in caplog.records) == 1


def test_a_missing_library_leaves_it_off(on):
    def factory(level):
        raise ImportError("no webrtc_noise_gain on this platform")

    d = CallerDenoiser("c1", sample_rate=16000, engine_factory=factory)
    chunk = _pcm(320)
    assert d.enabled is False and d.reason == "unavailable"
    assert d.process(chunk) is chunk


@pytest.mark.parametrize("rate", [8000, 24000, 48000, None, "16000"])
def test_only_16khz_is_cleaned(on, rate):
    d = CallerDenoiser("c1", sample_rate=rate, engine_factory=lambda level: _Engine())
    assert d.enabled is False and d.reason == "unsupported_sample_rate"


@pytest.mark.parametrize("feed", [24000, 8000, None])
def test_the_gateway_feed_rate_must_also_be_16khz(on, feed):
    """VoiceSessionConfig defaults gateway_sample_rate to 24000 while STT is
    16000; the queued audio is at the gateway's rate, not the STT rate."""
    d = CallerDenoiser("c1", sample_rate=16000, feed_sample_rate=feed,
                       engine_factory=lambda level: _Engine())
    assert d.enabled is False and d.reason == "unsupported_sample_rate"


@pytest.mark.parametrize("raw,level", [(None, 2), ("3", 3), ("9", 4), ("0", 1), ("loud", 2)])
def test_level_is_read_and_clamped(on, monkeypatch, raw, level):
    if raw is None:
        monkeypatch.delenv("CALLER_NOISE_SUPPRESSION_LEVEL", raising=False)
    else:
        monkeypatch.setenv("CALLER_NOISE_SUPPRESSION_LEVEL", raw)
    seen = []
    CallerDenoiser("c1", sample_rate=16000, engine_factory=lambda lv: seen.append(lv) or _Engine())
    assert seen == [level]


def test_the_wiring_is_logged_once_per_stream(on, caplog):
    caplog.set_level("INFO", logger=caller_denoise.logger.name)
    CallerDenoiser("c-log", sample_rate=16000, engine_factory=lambda level: _Engine())
    lines = [r.getMessage() for r in caplog.records if "caller_denoise " in r.getMessage()]
    assert len(lines) == 1 and "enabled=True" in lines[0] and "engine=webrtc" in lines[0]


async def _run_ingest(monkeypatch, chunks, *, feed_rate=16000, stop_after=None):
    """Drive AudioIngest with queued chunks; return what STT heard and the raw
    levels the barge-in onset signal saw."""
    from tests.unit.test_audio_ingest_terminal_stt_failure import _make_session
    from app.domain.services.voice_pipeline.audio_ingest import AudioIngest

    monkeypatch.setattr(caller_denoise, "_webrtc_engine", lambda level: _Engine())
    session = _make_session()
    session.stt_active = True
    queue: asyncio.Queue = asyncio.Queue()
    for c in chunks:
        await queue.put(c)

    pipeline = MagicMock()
    pipeline.stt_sample_rate = 16000
    pipeline.media_gateway._input_sample_rate = None
    pipeline.media_gateway._sample_rate = feed_rate
    pipeline.media_gateway.get_audio_queue.return_value = queue
    pipeline._barge_in_events = {}
    pipeline._barge_in_epoch = {}
    heard = []
    want = stop_after if stop_after is not None else len(chunks)

    class _STT:
        async def stream_transcribe(self, audio_stream, call_id=None, on_barge_in=None, **kwargs):
            async for chunk in audio_stream:
                heard.append(chunk)
                if len(heard) >= want:
                    session.stt_active = False
            return
            yield  # pragma: no cover

    pipeline.stt_provider = _STT()
    seen_levels = []
    monkeypatch.setattr(
        "app.domain.services.voice_pipeline.audio_ingest.note_voice_activity",
        lambda s, sum_sq, n: seen_levels.append(math.sqrt(sum_sq / n)),
    )
    await AudioIngest(pipeline).process(session)
    return heard, seen_levels


@pytest.mark.asyncio
async def test_audio_ingest_cleans_only_what_goes_to_stt(on, monkeypatch):
    heard, seen_levels = await _run_ingest(monkeypatch, [_pcm(320, 4000)])
    assert [c.data for c in heard] == [_pcm(320, 2000)]  # STT got the cleaned audio
    assert seen_levels == [pytest.approx(4000)]          # barge-in onset saw the raw level


@pytest.mark.asyncio
async def test_audio_ingest_holds_a_part_frame_until_the_next_chunk(on, monkeypatch):
    # 100 samples (part frame) -> nothing to STT yet; +220 -> two whole frames
    heard, _ = await _run_ingest(monkeypatch, [_pcm(100, 800), _pcm(220, 800)], stop_after=1)
    assert [c.data for c in heard] == [_pcm(320, 400)]


@pytest.mark.asyncio
async def test_audio_ingest_off_is_byte_identical(monkeypatch):
    monkeypatch.delenv("CALLER_NOISE_SUPPRESSION", raising=False)
    raw = _pcm(320, 4000)
    heard, _ = await _run_ingest(monkeypatch, [raw])
    assert [c.data for c in heard] == [raw]


@pytest.mark.asyncio
async def test_audio_ingest_leaves_a_24khz_gateway_feed_alone(on, monkeypatch):
    raw = _pcm(480, 4000)
    heard, _ = await _run_ingest(monkeypatch, [raw], feed_rate=24000)
    assert [c.data for c in heard] == [raw]


@pytest.mark.asyncio
async def test_audio_ingest_passes_a_non_16khz_chunk_raw(on, monkeypatch):
    from app.domain.models.conversation import AudioChunk

    chunk = AudioChunk(data=_pcm(160, 4000), sample_rate=8000)
    heard, _ = await _run_ingest(monkeypatch, [chunk])
    assert heard[0] is chunk


def test_the_real_webrtc_engine_reduces_steady_noise():
    """Runs where the library is installed (Linux CI and production)."""
    pytest.importorskip("webrtc_noise_gain")
    import random

    rng = random.Random(7)
    level = 2
    engine = caller_denoise._webrtc_engine(level)
    noise = [int(rng.gauss(0, 1500)) for _ in range(16000 * 2)]  # 2 s of hiss

    def rms(samples):
        return math.sqrt(sum(s * s for s in samples) / len(samples))

    out = b""
    raw = struct.pack(f"<{len(noise)}h", *noise)
    for i in range(0, len(raw), FRAME_BYTES):
        cleaned = engine(raw[i:i + FRAME_BYTES])
        assert len(cleaned) == FRAME_BYTES
        out += cleaned
    tail_in = noise[16000:]  # after the suppressor has settled
    tail_out = struct.unpack(f"<{len(tail_in)}h", out[32000:])
    assert rms(tail_out) < 0.5 * rms(tail_in)
