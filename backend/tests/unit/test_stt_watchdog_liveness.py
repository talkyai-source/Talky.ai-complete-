"""A loud line is not a dead stream.

Nine Flux "stalls" from 2026-09-10 to 09-27 (latest: call d3a21591, pipeline id
c79e7f3b, 09-27 19:42:44) all happened on lines whose caller side never went
quiet: audio_level RMS 1,000-10,000, often clipping at 32,768, against 7 on a
normal line. Six seconds of "voiced" audio with no words built up after every
agent reply, Flux correctly heard no words in the noise, and the watchdog
failed a working Flux over to Nova, whose bare VAD then cut the agent off for
90 s. No Flux connection closed or errored before any of the nine.

A dead stream sends nothing. A live Flux keeps sending TurnInfo messages even
when it hears no words, so a provider that reports a recent message is alive.
"""
from typing import AsyncIterator, Callable, Optional

import pytest

from app.domain.services.resilient_stt import ResilientSTTProvider
from app.infrastructure.stt.deepgram_flux import DeepgramFluxSTTProvider
from tests.unit.test_resilient_stt_silent_stream import (
    _EchoSTT,
    _SilentSTT,
    _policy,
    _stream,
    _voiced,
)


class _LoudLineFlux(_SilentSTT):
    """Accepts every frame and returns no words — but keeps talking."""

    def __init__(self, age: Optional[float]):
        super().__init__(name="deepgram-flux")
        self._age = age
        self.asked = 0

    def seconds_since_last_message(self, call_id: Optional[str]) -> Optional[float]:
        self.asked += 1
        return self._age


async def _run(primary):
    secondary = _EchoSTT()
    wrapper = ResilientSTTProvider(primary, secondary, policy=_policy())
    out = [
        c.text
        async for c in wrapper.stream_transcribe(
            _stream([_voiced()] * 40), call_id="call-loud"
        )
    ]
    return out, secondary


@pytest.mark.asyncio
async def test_a_live_provider_on_a_loud_line_is_not_failed_over():
    primary = _LoudLineFlux(age=0.3)
    out, secondary = await _run(primary)
    assert primary.received == 40, "every frame still reached the live primary"
    assert secondary.received == 0, "a live Flux must not be replaced"
    assert out == []
    assert primary.asked >= 1


@pytest.mark.asyncio
async def test_a_provider_that_stopped_sending_still_fails_over():
    """The 2026-08-13 dead-stream case must keep working."""
    out, secondary = await _run(_LoudLineFlux(age=9.0))
    assert secondary.received > 0
    assert "rescued" in out


@pytest.mark.asyncio
async def test_a_provider_that_never_sent_anything_still_fails_over():
    out, secondary = await _run(_LoudLineFlux(age=None))
    assert secondary.received > 0


@pytest.mark.asyncio
async def test_a_provider_without_liveness_behaves_as_before():
    out, secondary = await _run(_SilentSTT())
    assert secondary.received > 0


def test_flux_reports_seconds_since_its_last_message(monkeypatch):
    flux = DeepgramFluxSTTProvider()
    assert flux.seconds_since_last_message("c1") is None
    assert flux.seconds_since_last_message(None) is None
    import app.infrastructure.stt.deepgram_flux as mod

    monkeypatch.setattr(mod.time, "monotonic", lambda: 100.0)
    flux._last_message_at["c1"] = 99.25
    assert flux.seconds_since_last_message("c1") == pytest.approx(0.75)
