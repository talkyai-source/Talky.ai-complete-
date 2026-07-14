"""Regression tests for the 2026-07-13 recording-teardown root-cause fix.

Before the fix, `_save_call_recording` (telephony/recording.py) ran the
per-sample stereo mix loop (`mix_stereo_recording`,
`app/domain/services/recording_service.py`) and, further downstream,
`RecordingService.save_and_link` / `_save_local` ran the SYNCHRONOUS S3
`put_object` call / local `open()+write()` — all directly on the single
asyncio event loop, during call teardown. Because every live call shares
that one loop, a single multi-minute call ending would freeze the audio
pump for every OTHER in-flight call for however long the mix + upload
took (observed as "telephony_audio_gap" bursts correlated with "Saving
stereo recording" log lines).

These tests prove:
  1. The mix and the blocking writes are now dispatched via
     ``asyncio.to_thread`` (not run inline on the loop).
  2. The mix genuinely does not block the event loop — a concurrent task
     keeps making progress while a CPU-heavy mix runs.
  3. The buffers handed to the mix thread are snapshotted, so a
     concurrent ``clear_recording_buffer()`` (real teardown behaviour)
     cannot corrupt/starve the in-flight mix.
  4. The local-disk fallback path produces byte-identical output to the
     buffer's own ``get_wav_bytes()`` — only *where* it runs changed, not
     *what* it writes.
  5. The S3 upload call is also dispatched via ``asyncio.to_thread``.
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, patch

import pytest

from app.domain.services.recording_service import (
    RecordingBuffer,
    RecordingService,
    S3Client,
    mix_stereo_recording,
)
from app.domain.services.telephony.recording import _save_call_recording


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeGateway:
    """Mimics TelephonyMediaGateway's recording-buffer surface — including
    the fact that get_recording_buffer()/get_tts_recording_buffer() return
    the gateway's *live* list objects (see telephony_media_gateway.py:825-
    832), not copies.
    """

    def __init__(self, caller_chunks, agent_chunks, sample_rate=16000):
        self._caller_chunks = caller_chunks
        self._agent_chunks = agent_chunks
        self._sample_rate = sample_rate
        self.cleared = False

    def get_recording_buffer(self, call_id):
        return self._caller_chunks

    def get_tts_recording_buffer(self, call_id):
        return self._agent_chunks

    def clear_recording_buffer(self, call_id):
        # Real gateway behaviour: mutates the SAME list objects in place.
        self._caller_chunks.clear()
        self._agent_chunks.clear()
        self.cleared = True


class FakeVoiceSession:
    def __init__(self, gateway, call_id="voice-session-call-id"):
        self.media_gateway = gateway
        self.call_id = call_id
        self.config = None
        self.call_session = None


def _uninitialized_container():
    """A ServiceContainer stand-in with is_initialized=False, so
    `_save_call_recording` stops right after the mix (before any DB call)
    — exactly the segment these tests care about, with zero DB setup.
    """
    container = AsyncMock()
    container.is_initialized = False
    return container


# ---------------------------------------------------------------------------
# 1 + Q2/Q3 — mix is dispatched via asyncio.to_thread, not run inline
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mix_offloaded_to_thread_not_run_inline():
    caller_chunks = [b"\x01\x00" * 400]
    agent_chunks = [(0, b"\x02\x00" * 400)]
    gateway = FakeGateway(caller_chunks, agent_chunks)
    vs = FakeVoiceSession(gateway)

    calls = []
    real_to_thread = asyncio.to_thread

    async def spy_to_thread(func, *args, **kwargs):
        calls.append(func)
        return await real_to_thread(func, *args, **kwargs)

    with patch(
        "app.domain.services.telephony.recording.asyncio.to_thread",
        side_effect=spy_to_thread,
    ), patch(
        "app.core.container.get_container",
        return_value=_uninitialized_container(),
    ):
        await _save_call_recording(vs, "pbx-call-id")

    assert mix_stereo_recording in calls, (
        "mix_stereo_recording must be dispatched via asyncio.to_thread, "
        "not called directly on the event loop"
    )


# ---------------------------------------------------------------------------
# 2 + Q7 — offloading genuinely frees the event loop for other work
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mix_does_not_block_other_coroutines():
    # Large enough that the per-sample interleave loop in
    # mix_stereo_recording takes measurable wall-clock time (hundreds of
    # ms of pure-Python work), simulating a multi-minute call's recording.
    n_samples = 800_000
    caller_chunks = [b"\x01\x00" * n_samples]
    agent_chunks = [(0, b"\x02\x00" * n_samples)]
    gateway = FakeGateway(caller_chunks, agent_chunks)
    vs = FakeVoiceSession(gateway)

    ticks = []

    async def ticker():
        # A stand-in for another live call's audio pump / silence timer.
        # If the mix were still running inline on the loop, this would be
        # starved for the full mix duration instead of ticking every ~5ms.
        for _ in range(40):
            ticks.append(time.monotonic())
            await asyncio.sleep(0.005)

    with patch(
        "app.core.container.get_container",
        return_value=_uninitialized_container(),
    ):
        t0 = time.monotonic()
        await asyncio.gather(
            _save_call_recording(vs, "pbx-call-id"),
            ticker(),
        )
        elapsed = time.monotonic() - t0

    # The ticker must have completed all 40 ticks roughly on schedule
    # (~200ms), not been blocked until the mix finished. A blocked loop
    # would compress all the ticks into a burst right after the mix ends.
    assert len(ticks) == 40
    gaps = [b - a for a, b in zip(ticks, ticks[1:])]
    max_gap = max(gaps)
    assert max_gap < 0.15, (
        f"ticker starved for {max_gap:.3f}s — the event loop was blocked, "
        "meaning the mix ran inline instead of in a worker thread"
    )


# ---------------------------------------------------------------------------
# 3 + Q4 — buffers are snapshotted before the offload: concurrent teardown
# (clear_recording_buffer) mutating the gateway's live lists cannot starve
# or corrupt the in-flight mix.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_buffer_snapshot_immune_to_concurrent_clear():
    # >0.5s of 16kHz mono audio (duration gate in _save_call_recording) so
    # the snapshot survives all the way to the container-not-initialized
    # log this test uses as its "the mix used pre-clear data" oracle.
    n_samples = 20_000
    caller_chunks = [b"\x01\x00" * n_samples]
    agent_chunks = [(0, b"\x02\x00" * n_samples)]
    gateway = FakeGateway(caller_chunks, agent_chunks)
    vs = FakeVoiceSession(gateway)

    real_to_thread = asyncio.to_thread
    mix_started = asyncio.Event()

    async def slow_to_thread(func, *args, **kwargs):
        # Signal readiness, yield once so the "concurrent teardown" task
        # below gets a chance to run and mutate the gateway's live lists
        # BEFORE the (already-snapshotted) mix actually executes.
        mix_started.set()
        await asyncio.sleep(0.02)
        return await real_to_thread(func, *args, **kwargs)

    async def concurrent_teardown():
        await mix_started.wait()
        # Simulates end_session()/clear_recording_buffer() racing the
        # in-flight recording save — exactly what can now happen since
        # the mix yields the loop.
        gateway.clear_recording_buffer(vs.call_id)

    with patch(
        "app.domain.services.telephony.recording.asyncio.to_thread",
        side_effect=slow_to_thread,
    ), patch(
        "app.core.container.get_container",
        return_value=_uninitialized_container(),
    ), patch(
        "app.domain.services.telephony.recording.logger"
    ) as mock_logger:
        await asyncio.gather(
            _save_call_recording(vs, "pbx-call-id"),
            concurrent_teardown(),
        )

    assert gateway.cleared is True  # the race really happened
    # If the mix had read the LIVE (now-cleared) lists instead of a
    # snapshot, the "Saving stereo recording" info log would report 0
    # duration and the function would return early via the < 0.5s guard
    # — the container-not-initialized warning would never fire. Its
    # presence proves the mix (and the duration computed from the
    # snapshotted caller_chunks) used the pre-clear data.
    logged = " ".join(
        str(call.args[0]) for call in mock_logger.warning.call_args_list
    )
    assert "container not initialized" in logged


# ---------------------------------------------------------------------------
# 4 + Q6 — local-disk save is offloaded and byte-identical
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_local_save_offloaded_and_byte_identical(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_RECORDINGS_DIR", str(tmp_path))

    buf = RecordingBuffer(call_id="call-1", sample_rate=16000, channels=2, bit_depth=16)
    buf.add_chunk(b"\x01\x02" * 500)
    expected_wav = buf.get_wav_bytes()

    calls = []
    real_to_thread = asyncio.to_thread

    async def spy_to_thread(func, *args, **kwargs):
        calls.append(func.__name__ if hasattr(func, "__name__") else func)
        return await real_to_thread(func, *args, **kwargs)

    svc = RecordingService(db_pool=AsyncMock())
    with patch(
        "app.domain.services.recording_service.asyncio.to_thread",
        side_effect=spy_to_thread,
    ), patch.object(
        svc, "_insert_recording_record", AsyncMock(return_value=None)
    ):
        result = await svc._save_local(
            call_id="call-1", buffer=buf, tenant_id="t1", campaign_id="c1",
        )

    assert "_write_wav_file" in calls, (
        "the makedirs+open+write sequence must run via asyncio.to_thread"
    )
    written = (tmp_path / "call-1.wav").read_bytes()
    assert written == expected_wav, "recording output must be byte-identical"
    # DB insert failed (mocked None) -> falls back to returning the filepath.
    assert result is not None


# ---------------------------------------------------------------------------
# 5 + Q2 — S3 upload is dispatched via asyncio.to_thread
# ---------------------------------------------------------------------------

class _FakeAcquireCtx:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


class FakeConn:
    async def fetchrow(self, *args, **kwargs):
        return {"id": "11111111-1111-1111-1111-111111111111"}

    async def execute(self, *args, **kwargs):
        return None


class FakeDbPool:
    def acquire(self):
        return _FakeAcquireCtx(FakeConn())


@pytest.mark.asyncio
async def test_s3_upload_offloaded_to_thread():
    buf = RecordingBuffer(call_id="call-2", sample_rate=16000, channels=2, bit_depth=16)
    buf.add_chunk(b"\x03\x04" * 300)

    # A lightweight stand-in exposing exactly the sync surface
    # RecordingService touches, so `upload` genuinely runs as a plain
    # (blocking-shaped) callable — mirroring the real boto3 client method
    # (S3Client.upload() itself just calls self._client.put_object(...)
    # synchronously; faking at this layer avoids needing real boto3/AWS
    # creds while still proving the offload wraps the actual sync call).
    class FakeS3:
        bucket = "test-bucket"
        region = "us-east-1"
        upload_calls = []

        def is_available(self):
            return True

        def upload(self, key, data, content_type="audio/wav"):
            # Runs on a worker thread when correctly offloaded.
            self.upload_calls.append((key, data, content_type))

    fake_s3 = FakeS3()

    calls = []
    real_to_thread = asyncio.to_thread

    async def spy_to_thread(func, *args, **kwargs):
        calls.append(func)
        return await real_to_thread(func, *args, **kwargs)

    with patch(
        "app.domain.services.recording_service.asyncio.to_thread",
        side_effect=spy_to_thread,
    ), patch(
        "app.domain.services.recording_policy_service.RecordingPolicyService.decide",
        new=AsyncMock(return_value=type(
            "D", (), {"should_record": True, "reason": "ok"}
        )()),
    ):
        svc = RecordingService(db_pool=FakeDbPool(), s3_client=fake_s3)
        recording_id = await svc.save_and_link(
            call_id="11111111-1111-1111-1111-111111111111",
            buffer=buf,
            tenant_id="22222222-2222-2222-2222-222222222222",
            campaign_id="33333333-3333-3333-3333-333333333333",
        )

    assert fake_s3.upload in calls, (
        "S3Client.upload must be dispatched via asyncio.to_thread"
    )
    assert len(fake_s3.upload_calls) == 1
    assert recording_id is not None
