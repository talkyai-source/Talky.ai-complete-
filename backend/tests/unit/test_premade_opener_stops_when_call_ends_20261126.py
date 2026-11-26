"""A call that ends before it is answered stops its background opener task (26 Nov 2026).

Fault: the agent's first words are prepared in a background task while the phone
rings. When the call ended first, the session was closed and its voice provider
with it, but nothing cancelled the task: it ran into the closed provider
(AttributeError, logged as opener_premade_failed, 35 a day) or held one of the
eight process-wide ElevenLabs slots until its 8 s limit. Now ``end_session`` stops
the task BEFORE the provider is closed, and the ElevenLabs request loop stops with
a dedicated error when the provider was closed under it.
"""
from __future__ import annotations

import asyncio
import logging

import aiohttp
import pytest

from app.domain.services import recording_policy_service as rps
from app.domain.services.telephony.modes.agent_first import prepare_pre_originate_greeting
from app.domain.services.voice_orchestrator import VoiceOrchestrator
from app.domain.services.voice_pipeline import premade_opener
from app.domain.services.voice_pipeline.caller_first_disclosure import (
    speak_disclosure_before_first_reply,
)
from app.infrastructure.providers.provider_concurrency import reset_guards_for_tests
from app.infrastructure.tts import elevenlabs_tts
from app.infrastructure.tts.elevenlabs_tts import ElevenLabsTTSProvider, ProviderClosedError
from tests.unit.test_premade_caller_first_opener import (  # noqa: F401  (_clean is autouse)
    CALL, LOGGER, NOTICE_OPENER, PLAIN, PLATFORM, Provider, _answer, _clean, _ring, make_voice,
)


class BlockingProvider(Provider):
    """The 2nd synthesis (the background notice opener) blocks; cleanup() records
    what was still in flight at that moment."""

    def __init__(self):
        super().__init__()
        self.in_flight = 0
        self.at_cleanup = None

    async def stream_synthesize(self, text, **kw):
        n = len(self.texts) + 1
        self.texts.append(text)
        self.in_flight += 1
        try:
            if n == 2:
                await asyncio.sleep(30)
            yield bytes([n, 0]) * 160
        finally:
            self.in_flight -= 1

    async def cleanup(self):
        self.at_cleanup = (self.in_flight, self._task.done())


def _ending(voice, provider):
    """Make the harness voice session look like the VoiceSession end_session ends."""
    provider._task = getattr(voice, "_premade_opener_task", None)
    voice.pipeline_task = None
    voice.event_repo = None
    voice.talklee_call_id = "t"
    voice.leg_id = None
    voice.config.session_type = "telephony"
    return VoiceOrchestrator(db_client=None)


def _own(caplog):
    return [r for r in caplog.records if r.name == LOGGER]


@pytest.mark.asyncio
async def test_a_call_ended_before_answer_stops_the_opener_before_the_provider_closes(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    provider = BlockingProvider()
    voice, *_ = make_voice(monkeypatch, provider=provider)
    await prepare_pre_originate_greeting(voice, "user")
    await asyncio.sleep(0)
    task = voice._premade_opener_task
    assert not task.done()
    orch = _ending(voice, provider)
    await orch.end_session(voice)
    assert provider.at_cleanup == (0, True)           # nothing in flight when cleanup() ran
    assert task.cancelled() and task.done()
    stopped = [r.getMessage() for r in _own(caplog) if "opener_premade_stopped" in r.getMessage()]
    assert len(stopped) == 1
    assert stopped[0].startswith("opener_premade_stopped call_id=11111111-111 kind=notice_opener")
    assert stopped[0].endswith("reason=call_ended")
    assert "opener_premade_failed" not in caplog.text
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


@pytest.mark.asyncio
async def test_a_finished_task_is_left_alone_and_logs_nothing(monkeypatch, caplog):
    voice, *_ = make_voice(monkeypatch)
    await _ring(voice)                                 # lines ready, task done
    assert voice._premade_opener_task.done()
    caplog.clear()
    caplog.set_level(logging.DEBUG)
    await premade_opener.stop_premade_for_ended_call(voice)
    assert not [r for r in caplog.records if r.name == LOGGER]
    class NoTask: call_id = CALL
    await premade_opener.stop_premade_for_ended_call(NoTask())   # no task at all
    assert not [r for r in caplog.records if r.name == LOGGER]


@pytest.mark.asyncio
async def test_a_task_that_ignores_cancellation_is_left_after_the_wait_and_end_session_completes(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(premade_opener, "PREMADE_STOP_WAIT_S", 0.05)
    provider = Provider()
    voice, *_ = make_voice(monkeypatch, provider=provider)
    release = asyncio.Event()

    async def stubborn():
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                pass

    voice._premade_opener_task = asyncio.ensure_future(stubborn())
    await asyncio.sleep(0)
    cleaned = []
    provider.cleanup = lambda: _record(cleaned)
    orch = _ending(voice, provider)
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    await orch.end_session(voice)
    assert loop.time() - t0 < 1.0
    release.set()
    await voice._premade_opener_task
    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings == [f"opener_premade_stop_timeout call_id={CALL[:12]}"]
    assert cleaned == [True]


async def _record(sink):
    sink.append(True)


@pytest.mark.asyncio
async def test_the_stop_function_raising_never_stops_end_session(monkeypatch):
    provider = Provider()
    voice, *_ = make_voice(monkeypatch, provider=provider)
    cleaned = []
    provider.cleanup = lambda: _record(cleaned)

    async def boom(_session, *a, **k):
        raise RuntimeError("stop failed")

    monkeypatch.setattr(premade_opener, "stop_premade_for_ended_call", boom)
    orch = _ending(voice, provider)
    await orch.end_session(voice)
    assert cleaned == [True]


# ------------------------------------------------------------ ElevenLabs loop

class _Obs:
    """Stands in for ProviderAttempt and records every finish()."""

    finishes: list = []

    def __init__(self, *a, **k):
        self.closed = False                            # like the real one: the first finish wins

    def accepted(self): pass
    def first_output(self): pass
    def complete(self): pass

    def finish(self, error=None, *, outcome=None):
        if self.closed:
            return
        self.closed = True
        _Obs.finishes.append((type(error).__name__ if error else None, outcome))


class _CM:
    def __init__(self, provider, outcome):
        self.p, self.outcome = provider, outcome

    async def __aenter__(self):
        if isinstance(self.outcome, BaseException):
            self.p._session = None                     # cleanup() ran during the request
            raise self.outcome
        return self.outcome

    async def __aexit__(self, *a):
        return False


class _Session:
    def __init__(self, outcomes):
        self.outcomes, self.post_calls, self.provider = list(outcomes), 0, None

    def post(self, url, headers=None, params=None, json=None):
        o = self.outcomes[self.post_calls]
        self.post_calls += 1
        return _CM(self.provider, o)


def _el(session):
    p = ElevenLabsTTSProvider()
    p._session, p._api_key, p._pool = session, "test-key", None
    p._voice_id, p._sample_rate, p._model_id = "voice-1", 24000, "eleven_flash_v2_5"
    session.provider = p
    return p


@pytest.fixture
def _fast(monkeypatch):
    reset_guards_for_tests()
    _Obs.finishes = []
    monkeypatch.setattr(elevenlabs_tts, "_EL_RETRY_BASE_DELAY", 0.0)
    monkeypatch.setattr(elevenlabs_tts, "ProviderAttempt", _Obs)
    yield
    reset_guards_for_tests()


@pytest.mark.asyncio
async def test_a_provider_closed_during_the_request_stops_with_its_own_error(_fast, caplog):
    caplog.set_level(logging.INFO)
    session = _Session([aiohttp.ServerDisconnectedError("closed under us"), AssertionError("must not be tried")])
    p = _el(session)
    with pytest.raises(ProviderClosedError):
        [c async for c in p.stream_synthesize("Hello there.", "voice-1", sample_rate=24000)]
    assert session.post_calls == 1                     # no second attempt, no AttributeError
    assert "connection error" not in caplog.text and "retry" not in caplog.text
    assert _Obs.finishes == [(None, "cancelled")]      # no failed observation


@pytest.mark.asyncio
async def test_a_provider_closed_between_two_attempts_stops_before_the_next_post(_fast, caplog):
    caplog.set_level(logging.INFO)

    class Resp503:
        status, headers = 503, {}

        async def text(self):
            session.provider._session = None           # closed while the retry is pending
            return ""

    session = _Session([Resp503(), AssertionError("must not be tried")])
    p = _el(session)
    with pytest.raises(ProviderClosedError):
        [c async for c in p.stream_synthesize("Hello there.", "voice-1", sample_rate=24000)]
    assert session.post_calls == 1
    assert _Obs.finishes == [("RuntimeError", None)]   # only the real 503; the closure adds nothing


# ------------------------------------------------- real faults, answered calls

@pytest.mark.asyncio
async def test_a_real_provider_fault_with_the_call_alive_is_a_warning_with_the_error_type(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    voice, *_ = make_voice(monkeypatch, provider=Provider(fail_on=2))
    await _ring(voice)
    failed = [r for r in _own(caplog) if "opener_premade_failed" in r.getMessage()]
    assert len(failed) == 1 and failed[0].levelno == logging.WARNING
    assert failed[0].getMessage() == "opener_premade_failed call_id=11111111-111 kind=notice_opener reason=error:RuntimeError"


class FailsLikeAClosedElevenLabs(Provider):
    async def stream_synthesize(self, text, **kw):
        n = len(self.texts) + 1
        self.texts.append(text)
        if n == 2:
            raise AttributeError("'NoneType' object has no attribute 'post'")
        yield bytes([n, 0]) * 160
        yield bytes([n, 0]) * 160


@pytest.mark.asyncio
async def test_an_answered_call_whose_prepared_line_failed_speaks_the_opener_live(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    voice, s, gw, provider, pipe = make_voice(monkeypatch, provider=FailsLikeAClosedElevenLabs())
    await _ring(voice)
    assert "opener_premade_failed call_id=11111111-111 kind=notice_opener reason=error:AttributeError" in caplog.text
    assert await _answer(PLATFORM, lambda: speak_disclosure_before_first_reply(s, "Hello?")) is True
    assert provider.texts == [PLAIN, NOTICE_OPENER, NOTICE_OPENER]
    assert "premade_chunks" not in pipe.calls[0][1]
    assert "opener_premade_skipped" in caplog.text and "reason=no_matching_text" in caplog.text
    assert "recording_disclosure_delivered" in caplog.text
    assert rps.get_disclosure_state(CALL, CALL) in (rps.DISCLOSURE_TRANSMITTED, rps.DISCLOSURE_SPOKEN)


@pytest.mark.asyncio
async def test_an_answered_call_whose_prepared_line_was_stopped_speaks_the_opener_live(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    voice, s, gw, provider, pipe = make_voice(monkeypatch, provider=Provider(delay=30, delay_on=2))
    await prepare_pre_originate_greeting(voice, "user")
    await asyncio.sleep(0)
    await premade_opener.stop_premade_for_ended_call(voice)
    assert await _answer(PLATFORM, lambda: speak_disclosure_before_first_reply(s, "Hello?")) is True
    assert provider.texts == [PLAIN, NOTICE_OPENER, NOTICE_OPENER]   # ringing, stopped one, then LIVE
    assert "premade_chunks" not in pipe.calls[0][1]
    assert "recording_disclosure_delivered" in caplog.text


# ---------------------------------------- real close ordering (26 Nov, round 2)

@pytest.mark.asyncio
async def test_cleanup_with_a_request_in_flight_is_a_closure_not_a_failure(_fast, monkeypatch, caplog):
    """Real aiohttp hands the disconnect to the in-flight request DURING
    ``session.close()``, i.e. before cleanup() has set ``_session = None``."""
    caplog.set_level(logging.INFO)
    reported = []

    class Lease(elevenlabs_tts._SingleKeyLease):
        def report_failure(self, retryable=True):
            reported.append(retryable)

    monkeypatch.setattr(elevenlabs_tts, "_SingleKeyLease", Lease)
    release, in_flight = asyncio.Event(), asyncio.Event()

    class PendingCM:
        async def __aenter__(self):
            in_flight.set()
            await release.wait()
            raise aiohttp.ServerDisconnectedError("closed by close()")

        async def __aexit__(self, *a):
            return False

    class RealOrderSession:
        post_calls = 0

        def post(self, *a, **k):
            self.post_calls += 1
            return PendingCM()

        async def close(self):
            release.set()                      # the request is woken ...
            await asyncio.sleep(0)             # ... and runs its handler ...
            await asyncio.sleep(0)             # ... before close() returns

    session = RealOrderSession()
    p = ElevenLabsTTSProvider()
    p._session, p._api_key, p._pool = session, "test-key", None
    p._voice_id, p._sample_rate, p._model_id = "voice-1", 24000, "eleven_flash_v2_5"
    task = asyncio.ensure_future(_drain(p))
    await in_flight.wait()
    await p.cleanup()
    with pytest.raises(ProviderClosedError):
        await task
    assert session.post_calls == 1
    assert "retry" not in caplog.text and "connection error" not in caplog.text
    assert _Obs.finishes == [(None, "cancelled")]
    assert reported == []                      # no failure charged to the key


async def _drain(p):
    return [c async for c in p.stream_synthesize("Hello there.", "voice-1", sample_rate=24000)]


# ------------------------------------------- playback: a closed provider is session_gone

def _playback_with(provider, monkeypatch):
    from unittest.mock import AsyncMock

    from app.domain.services.voice_pipeline.tts_playback import TtsPlayback

    voice, s, gw, _p, pipe = make_voice(monkeypatch)
    pipe.tts_provider = provider
    pb = TtsPlayback(pipe)
    clip = AsyncMock(return_value=False)
    monkeypatch.setattr(pb, "_try_emergency_voice_clip", clip)
    return pb, s, clip


class _Raises:
    name = "fake"

    def __init__(self, exc):
        self.exc = exc

    def stream_synthesize(self, text, **kw):
        exc = self.exc

        async def gen():
            raise exc
            yield b""  # pragma: no cover

        return gen()


@pytest.mark.asyncio
async def test_a_provider_closed_by_the_ending_call_is_session_gone_not_a_provider_fault(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    pb, s, clip = _playback_with(_Raises(ProviderClosedError("closed")), monkeypatch)
    await pb.synthesize_and_send(s, "Hello there.", None, barge_in_event=s.barge_in_event)
    assert s._tts_failure_reason == "session_gone"
    clip.assert_not_called()
    assert not [r for r in caplog.records if r.levelno >= logging.CRITICAL]
    assert "tts_provider_emergency_fallback" not in caplog.text


@pytest.mark.asyncio
async def test_any_other_provider_runtime_error_keeps_todays_handling(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    pb, s, clip = _playback_with(_Raises(RuntimeError("boom")), monkeypatch)
    await pb.synthesize_and_send(s, "Hello there.", None, barge_in_event=s.barge_in_event)
    assert s._tts_failure_reason == "tts_provider_error"
    clip.assert_awaited_once()


# ------------------------------------------------------ stop log: kind=unknown

@pytest.mark.asyncio
async def test_stop_says_kind_unknown_when_nothing_is_pending_but_the_task_runs(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    voice, *_ = make_voice(monkeypatch)
    voice._premade_opener_kinds = {"x": "notice_opener"}
    voice._premade_opener_pending = {}
    voice._premade_opener_task = asyncio.ensure_future(asyncio.sleep(30))
    await asyncio.sleep(0)
    await premade_opener.stop_premade_for_ended_call(voice)
    assert voice._premade_opener_task.cancelled()
    msgs = [r.getMessage() for r in _own(caplog) if "opener_premade_stopped" in r.getMessage()]
    assert msgs == [f"opener_premade_stopped call_id={CALL[:12]} kind=unknown reason=call_ended"]
