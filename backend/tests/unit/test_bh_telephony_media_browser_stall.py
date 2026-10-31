"""telephony-infra-media-11: BrowserMediaGateway stalled-consumer handling.

Production: 1780 stt_input_queue_overrun lines in 30 minutes from sockets whose
STT consumer had stopped, plus an 'Unknown session for audio' warning per 8 ms
chunk after session end, and hangup_call raising on an already-closed socket
before on_call_ended could run.
"""
from __future__ import annotations

import asyncio
import logging

import pytest

from app.infrastructure.telephony.browser_media_gateway import BrowserMediaGateway

CHUNK_20MS = b"\x00\x00" * 320  # 16 kHz s16le mono, passes INPUT_BUFFER_MIN_MS


class _Ws:
    def __init__(self, close_error: Exception | None = None):
        self.close_error = close_error
        self.closed_with: list[tuple[int, str]] = []

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed_with.append((code, reason))
        if self.close_error is not None:
            raise self.close_error

    async def send_bytes(self, payload: bytes) -> None:  # pragma: no cover - unused
        pass


async def _gateway(ws, *, queue: int = 1, stall_after_s: float = 0.05):
    gateway = BrowserMediaGateway()
    await gateway.initialize({"sample_rate": 16000, "max_queue_size": queue})
    gateway._consumer_stall_after_s = stall_after_s
    await gateway.on_call_started("c1", {"websocket": ws})
    return gateway


@pytest.mark.asyncio
async def test_unknown_session_audio_warning_is_throttled(caplog):
    gateway = BrowserMediaGateway()
    await gateway.initialize({"sample_rate": 16000})

    with caplog.at_level(logging.WARNING):
        for _ in range(200):
            await gateway.on_audio_received("ended-call", CHUNK_20MS)

    warnings = [r for r in caplog.records if "Unknown session for audio" in r.getMessage()]
    assert len(warnings) == 1


@pytest.mark.asyncio
async def test_continuous_overrun_marks_the_consumer_stalled_and_closes_the_socket():
    ws = _Ws()
    gateway = await _gateway(ws)
    session = gateway._sessions["c1"]

    await gateway.on_audio_received("c1", CHUNK_20MS)  # fills the queue (size 1)
    await gateway.on_audio_received("c1", CHUNK_20MS)  # first overrun
    assert session.consumer_stalled is False
    await asyncio.sleep(0.08)
    await gateway.on_audio_received("c1", CHUNK_20MS)  # overrun past the threshold

    assert session.consumer_stalled is True
    assert len(ws.closed_with) == 1
    assert ws.closed_with[0][0] == 1011


@pytest.mark.asyncio
async def test_recording_keeps_filling_while_stt_is_merely_slow_and_stops_once_stalled():
    ws = _Ws()
    gateway = await _gateway(ws, stall_after_s=0.05)
    session = gateway._sessions["c1"]

    await gateway.on_audio_received("c1", CHUNK_20MS)
    await gateway.on_audio_received("c1", CHUNK_20MS)
    await gateway.on_audio_received("c1", CHUNK_20MS)
    slow_bytes = session.recording_buffer_bytes
    assert slow_bytes == 3 * len(CHUNK_20MS), "slow STT must never cost recorded audio"

    await asyncio.sleep(0.08)
    await gateway.on_audio_received("c1", CHUNK_20MS)  # trips the stall
    stalled_bytes = session.recording_buffer_bytes
    await gateway.on_audio_received("c1", CHUNK_20MS)
    await gateway.on_audio_received("c1", CHUNK_20MS)

    assert session.consumer_stalled is True
    assert session.recording_buffer_bytes == stalled_bytes


@pytest.mark.asyncio
async def test_a_draining_consumer_resets_the_stall_clock():
    ws = _Ws()
    gateway = await _gateway(ws, stall_after_s=0.05)
    session = gateway._sessions["c1"]
    queue = gateway.get_audio_queue("c1")

    await gateway.on_audio_received("c1", CHUNK_20MS)
    await gateway.on_audio_received("c1", CHUNK_20MS)  # overrun starts the clock
    await asyncio.sleep(0.08)
    queue.get_nowait()  # consumer catches up
    await gateway.on_audio_received("c1", CHUNK_20MS)  # accepted -> clock resets
    await gateway.on_audio_received("c1", CHUNK_20MS)  # a fresh, short overrun

    assert session.consumer_stalled is False
    assert ws.closed_with == []


@pytest.mark.asyncio
async def test_hangup_call_on_an_already_closed_socket_still_ends_the_session():
    ws = _Ws(close_error=RuntimeError("Cannot call 'send' once a close message has been sent."))
    gateway = await _gateway(ws)

    result = await gateway.hangup_call("c1", "agent_end_call")

    assert result is True
    assert "c1" not in gateway._sessions
