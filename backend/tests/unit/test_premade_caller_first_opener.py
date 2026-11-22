"""The caller-first opener played from audio made while the phone rings.

On 20 Nov 2026 the opener (with the ", on a recorded line" notice words) was
synthesized live after the person said hello: a median 756 ms of silence at the
most fragile moment of the call. The audio is now made during pre-originate and
played through the SAME TtsPlayback.synthesize_and_send, so the gateway receipt
that proves the recording notice is obtained exactly as for live speech. These
tests run the real prepare_pre_originate_greeting, the real
_speak_recording_disclosure, the real caller-first helper and the real
TtsPlayback; only the provider, the gateway and the policy lookup are fakes.
"""
from __future__ import annotations

import asyncio
import logging
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.domain.models.conversation import MessageRole
from app.domain.services import recording_policy_service as rps
from app.domain.services.recording_policy_service import RecordingDecision
from app.domain.services.telephony.config import _build_call_greeting, outbound_opener_with_notice
from app.domain.services.telephony.modes.agent_first import (
    _send_outbound_greeting,
    _speak_recording_disclosure,
    prepare_pre_originate_greeting,
)
from app.domain.services.voice_pipeline import premade_opener
from app.domain.services.voice_pipeline.caller_first_disclosure import (
    speak_disclosure_before_first_reply,
)
from app.domain.services.voice_pipeline.premade_opener import premade_kwargs
from app.domain.services.voice_pipeline.tts_playback import TtsPlayback

TENANT = "22222222-2222-2222-2222-222222222222"
CALL = "11111111-1111-1111-1111-111111111111"
PLAIN = "Hi, this is Sarah, an AI assistant from All State Estimation. Am I speaking with John?"
NOTICE_OPENER = (
    "Hi, this is Sarah, an AI assistant from All State Estimation, on a recorded line. "
    "Am I speaking with John?"
)
TENANT_TEXT = "Acme records calls to train its team."
LOGGER = "app.domain.services.voice_pipeline.premade_opener"


def _decision(required, text=None):
    return RecordingDecision(
        should_record=True, announcement_required=required, announcement_text=text,
        opt_out_dtmf_digit=None, retention_days=90, reason="test",
    )


PLATFORM = _decision(True, "This call may be recorded for quality and training purposes. "
                           "Press 9 at any time to opt out of recording.")
TENANT_NOTICE = _decision(True, TENANT_TEXT)
NO_NOTICE = _decision(False)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    rps._DISCLOSURE_LEDGER.clear()
    monkeypatch.delenv("OPENER_PREMADE_ENABLED", raising=False)
    yield
    rps._DISCLOSURE_LEDGER.clear()


class Provider:
    """Records every synthesis; each call yields two chunks stamped with its number."""

    name = "fake"

    def __init__(self, fail_on=None, delay=0.0):
        self.texts = []
        self.fail_on = fail_on
        self.delay = delay

    async def stream_synthesize(self, text, **kw):
        n = len(self.texts) + 1
        self.texts.append(text)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail_on == n:
            raise RuntimeError("boom")
        yield bytes([n, 0]) * 160
        yield bytes([n, 0]) * 160


class Gateway:
    def __init__(self, status="transmitted", evidence="transmitted", *, mismatch=False,
                 interrupt_after=None, fail_after=None):
        self.status, self.evidence, self.mismatch = status, evidence, mismatch
        self.interrupt_after, self.fail_after = interrupt_after, fail_after
        self.sent, self.begin_uids, self.finish_uids = [], [], []
        self.event = None

    async def begin_playback(self, call_id, uid):
        self.begin_uids.append(uid)

    async def send_audio(self, call_id, audio):
        if self.fail_after is not None and len(self.sent) >= self.fail_after:
            from app.infrastructure.telephony.asterisk_adapter import TtsDeliveryError
            raise TtsDeliveryError("synthetic")
        self.sent.append(audio)
        if self.interrupt_after is not None and len(self.sent) == self.interrupt_after:
            self.event.set()

    async def flush_tts_buffer(self, call_id):
        pass

    async def finish_playback(self, call_id, uid):
        self.finish_uids.append(uid)
        return {"utterance_id": "other" if self.mismatch else uid,
                "status": self.status, "evidence": self.evidence}

    async def clear_output_buffer(self, call_id):
        pass


def _call_session(name="John", **extra):
    return types.SimpleNamespace(
        call_id=CALL, tenant_id=TENANT, llm_active=False, tts_active=False,
        barge_in_event=asyncio.Event(), conversation_history=[], persona_type="lead_gen",
        voice_id="v", turn_id=0, tts_pronunciations=None,
        agent_config=types.SimpleNamespace(
            agent_name="Sarah", company_name="All State Estimation", call_reason=None),
        config=types.SimpleNamespace(direction="outbound", callee_first_name=name),
        **extra,
    )


def make_voice(monkeypatch, *, gateway=None, provider=None, direction="outbound", **session_extra):
    gateway = gateway or Gateway()
    provider = provider or Provider()
    session = _call_session(**session_extra)
    session.config.direction = direction
    gateway.event = session.barge_in_event
    pipe = types.SimpleNamespace(
        tts_provider=provider, media_gateway=gateway, stt_provider=object(),
        latency_tracker=MagicMock(), tts_sample_rate=16000, _record_silent_turn=MagicMock(),
        transcript_service=types.SimpleNamespace(accumulate_turn=lambda **_: None),
        clear_barge_in_event=lambda s: None,
    )
    playback = TtsPlayback(pipe)
    monkeypatch.setattr(playback, "_try_emergency_voice_clip", AsyncMock(return_value=False))
    pipe.calls = []

    async def send(sess, text, websocket=None, *, track_latency=True, **kw):
        pipe.calls.append((text, dict(kw)))
        return await playback.synthesize_and_send(
            sess, text, websocket, barge_in_event=sess.barge_in_event,
            track_latency=track_latency, **kw)

    pipe.synthesize_and_send_audio = send
    voice = types.SimpleNamespace(
        call_id=CALL, call_session=session, pipeline=pipe, media_gateway=gateway,
        tts_provider=provider, config=types.SimpleNamespace(voice_id="v", tts_sample_rate=16000),
        _dialer_tenant_id=TENANT,
    )
    session._voice_session_ref = voice
    return voice, session, gateway, provider, pipe


async def _answer(decision, fn):
    container = types.SimpleNamespace(is_initialized=True, db_pool=object())
    with patch("app.core.container.get_container", return_value=container), patch.object(
        rps.RecordingPolicyService, "decide", new=AsyncMock(return_value=decision),
    ):
        return await fn()


# ---------------------------------------------------------------- ringing

async def test_ringing_premakes_both_openers_on_caller_first_calls(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    voice, session, _, provider, _ = make_voice(monkeypatch)
    await prepare_pre_originate_greeting(voice, "user")
    assert provider.texts == [PLAIN, NOTICE_OPENER]
    store = voice._premade_opener_lines
    assert store[PLAIN].kind == "opener"
    assert store[PLAIN].chunks == tuple(voice._presynth_greeting_audio)
    assert store[NOTICE_OPENER].kind == "notice_opener"
    assert store[NOTICE_OPENER].text == outbound_opener_with_notice(session)
    assert "opener_premade_ready call_id=" in caplog.text and "kind=notice_opener" in caplog.text
    assert "John" not in " ".join(r.getMessage() for r in caplog.records if r.name == LOGGER)


async def test_agent_first_calls_premake_nothing_new(monkeypatch):
    voice, _, _, provider, _ = make_voice(monkeypatch)
    await prepare_pre_originate_greeting(voice, "agent")
    assert provider.texts == [PLAIN]
    assert not hasattr(voice, "_premade_opener_lines")


@pytest.mark.parametrize("how", ["raise", "timeout"])
async def test_a_failed_or_slow_notice_premake_never_stops_the_call(monkeypatch, caplog, how):
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(premade_opener, "OPENER_PREMADE_TIMEOUT_S", 0.05)
    provider = Provider(fail_on=2) if how == "raise" else Provider()
    voice, *_ = make_voice(monkeypatch, provider=provider)
    if how == "timeout":
        real = provider.stream_synthesize

        async def slow(text, **kw):
            if len(provider.texts) >= 1:
                await asyncio.sleep(5)
            async for c in real(text, **kw):
                yield c

        provider.stream_synthesize = slow
    await prepare_pre_originate_greeting(voice, "user")
    assert voice._presynth_greeting_audio
    assert NOTICE_OPENER not in voice._premade_opener_lines
    assert PLAIN in voice._premade_opener_lines
    assert "opener_premade_failed" in caplog.text


async def test_cancellation_during_the_notice_premake_still_propagates(monkeypatch):
    voice, session, _, provider, _ = make_voice(monkeypatch)

    async def cancelled(text, **kw):
        if provider.texts:
            raise asyncio.CancelledError()
        provider.texts.append(text)
        yield b"\x01\x00" * 4

    provider.stream_synthesize = cancelled
    with pytest.raises(asyncio.CancelledError):
        await prepare_pre_originate_greeting(voice, "user")


async def test_the_premake_is_bounded_to_2_5_seconds_and_leaves_a_failed_line(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    assert premade_opener.OPENER_PREMADE_TIMEOUT_S == 2.5
    monkeypatch.setattr(premade_opener, "OPENER_PREMADE_TIMEOUT_S", 0.2)
    provider = Provider()
    voice, *_ = make_voice(monkeypatch, provider=provider)
    real = provider.stream_synthesize

    async def slow(text, **kw):
        if provider.texts:
            await asyncio.sleep(30)
        async for c in real(text, **kw):
            yield c

    provider.stream_synthesize = slow
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    await prepare_pre_originate_greeting(voice, "user")
    assert loop.time() - t0 < 1.0
    assert "opener_premade_failed" in caplog.text and "reason=timeout" in caplog.text


# ---------------------------------------------------------------- the proof

_ROWS = [
    ("completed", "transport_played", False, "spoken"),
    ("completed", "transmitted", False, "transmitted"),
    ("transmitted", "transmitted", False, "transmitted"),
    ("unknown", "unknown", False, "failed"),
    ("failed", "transport_played", False, "failed"),
    ("interrupted", "transport_played", False, "failed"),
    ("partial", "transport_played", False, "failed"),
    ("completed", "unknown", False, "failed"),
    ("completed", "transport_played", True, "failed"),
]


async def _run_notice_opener(monkeypatch, row, *, premade):
    status, evidence, mismatch, _ = row
    voice, session, gw, provider, pipe = make_voice(
        monkeypatch, gateway=Gateway(status, evidence, mismatch=mismatch))
    await prepare_pre_originate_greeting(voice, "user")
    if not premade:
        del voice._premade_opener_lines
    before = len(provider.texts)
    rps.clear_disclosure_state(CALL)
    await _answer(PLATFORM, lambda: _speak_recording_disclosure(
        voice, opener_with_notice=NOTICE_OPENER))
    outcome = (
        rps.get_disclosure_state(CALL),
        [m.content for m in session.conversation_history],
        getattr(session, "_has_introduced", None),
        getattr(voice, "_opener_spoken_as_notice", None),
        session._tts_delivery_status,
        (session._tts_playback_receipt or {}).get("status"),
        (session._tts_playback_receipt or {}).get("evidence"),
    )
    return outcome, voice, gw, provider, before, session


@pytest.mark.parametrize("row", _ROWS)
async def test_the_premade_notice_opener_has_the_same_proof(monkeypatch, row):
    outcome, voice, gw, provider, before, session = await _run_notice_opener(
        monkeypatch, row, premade=True)
    assert len(provider.texts) == before                      # the provider was not called
    assert gw.sent == [bytes([2, 0]) * 160] * 2               # the stored notice chunks
    assert outcome[0] == row[3]
    assert bool(outcome[1]) is (row[3] != "failed")
    assert len(gw.begin_uids) == 1 and gw.finish_uids == gw.begin_uids


@pytest.mark.parametrize("row", _ROWS)
async def test_premade_and_live_outcomes_are_identical(monkeypatch, row):
    premade, *_ = await _run_notice_opener(monkeypatch, row, premade=True)
    live, _v, gw, provider, before, _s = await _run_notice_opener(monkeypatch, row, premade=False)
    assert len(provider.texts) == before + 1                  # the live run did synthesize
    assert premade == live


# ------------------------------------------------- through the real helper

async def test_through_the_real_caller_first_helper_the_notice_opener_is_premade(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    voice, s, gw, provider, _ = make_voice(monkeypatch)
    await prepare_pre_originate_greeting(voice, "user")
    n = len(provider.texts)
    assert await _answer(PLATFORM, lambda: speak_disclosure_before_first_reply(s, "Hello?")) is True
    assert len(provider.texts) == n
    assert [(m.role, m.content) for m in s.conversation_history] == [
        (MessageRole.USER, "Hello?"), (MessageRole.ASSISTANT, NOTICE_OPENER)]
    assert s._has_introduced is True
    assert rps.get_disclosure_state(CALL) == "transmitted"
    assert "opener_premade_used call_id=" in caplog.text and "kind=notice_opener" in caplog.text
    assert gw.sent == [bytes([2, 0]) * 160] * 2


async def test_no_notice_owed_the_plain_opener_uses_the_ringing_audio(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    voice, s, gw, provider, _ = make_voice(monkeypatch)
    await prepare_pre_originate_greeting(voice, "user")
    n = len(provider.texts)
    assert await _answer(NO_NOTICE, lambda: speak_disclosure_before_first_reply(s, "Hello?")) is True
    assert len(provider.texts) == n
    assert gw.sent == list(voice._presynth_greeting_audio)
    assert rps.get_disclosure_state(CALL) == "not_required"
    assert s._has_introduced is True
    assert "opener_premade_used call_id=" in caplog.text and "kind=opener " in caplog.text


async def test_a_tenants_own_notice_is_live_then_the_opener_is_premade(monkeypatch):
    voice, s, gw, provider, _ = make_voice(monkeypatch)
    await prepare_pre_originate_greeting(voice, "user")
    n = len(provider.texts)
    assert await _answer(TENANT_NOTICE, lambda: speak_disclosure_before_first_reply(s, "Hello?")) is True
    assert provider.texts[n:] == [TENANT_TEXT]                # one live synthesis, the tenant text
    assert [m.content for m in s.conversation_history] == ["Hello?", TENANT_TEXT, PLAIN]
    assert gw.sent[-2:] == list(voice._presynth_greeting_audio)


# ------------------------------------------------- any difference is live

@pytest.mark.parametrize("change, reason", [
    ("name", "no_matching_text"),
    ("pronunciation", "spoken_form_differs"),
    ("voice", "voice_differs"),
    ("rate", "rate_differs"),
    ("provider", "provider_differs"),
])
async def test_any_difference_speaks_live(monkeypatch, caplog, change, reason):
    caplog.set_level(logging.INFO)
    voice, s, gw, provider, pipe = make_voice(monkeypatch)
    await prepare_pre_originate_greeting(voice, "user")
    live_provider = provider
    if change == "name":
        s.config.callee_first_name = "Jane"
    elif change == "pronunciation":
        s.tts_pronunciations = [("State", "Stayt")]
    elif change == "voice":
        s.voice_id = "other"
    elif change == "rate":
        pipe.tts_sample_rate = 8000
    else:
        live_provider = pipe.tts_provider = Provider()
    assert await _answer(PLATFORM, lambda: _speak_recording_disclosure(
        voice, opener_with_notice=outbound_opener_with_notice(s))) is None
    spoken = [t for t, _ in pipe.calls]
    assert len(spoken) == 1 and "premade_chunks" not in pipe.calls[0][1]
    assert live_provider.texts[-1] == apply_expected(spoken[0], s)
    assert f"opener_premade_skipped call_id=" in caplog.text and f"reason={reason}" in caplog.text
    assert "opener_premade_used" not in caplog.text


def apply_expected(text, session):
    from app.domain.services.voice_pipeline.spoken_pronunciations import apply_pronunciations

    return apply_pronunciations(text, session.tts_pronunciations)


# ------------------------------------------------- once, and the two paths

async def test_each_premade_line_is_used_at_most_once(monkeypatch):
    voice, s, gw, provider, pipe = make_voice(monkeypatch)
    await prepare_pre_originate_greeting(voice, "user")
    assert "premade_chunks" in premade_kwargs(voice, s, NOTICE_OPENER)
    assert premade_kwargs(voice, s, NOTICE_OPENER) == {}
    # after the caller-first opener, the silent-pickup greeting speaks nothing
    voice2, s2, gw2, provider2, _ = make_voice(monkeypatch)
    await prepare_pre_originate_greeting(voice2, "user")
    await _answer(PLATFORM, lambda: speak_disclosure_before_first_reply(s2, "Hello?"))
    sent, texts = list(gw2.sent), list(provider2.texts)
    await _answer(PLATFORM, lambda: _send_outbound_greeting(voice2))
    assert gw2.sent == sent and provider2.texts == texts
    # and the other way round: the silent-pickup greeting ran, the helper then speaks nothing
    voice3, s3, gw3, provider3, _ = make_voice(monkeypatch)
    await prepare_pre_originate_greeting(voice3, "user")
    await _answer(PLATFORM, lambda: _send_outbound_greeting(voice3))
    sent, texts = list(gw3.sent), list(provider3.texts)
    assert await _answer(PLATFORM, lambda: speak_disclosure_before_first_reply(s3, "Hello?")) is False
    assert gw3.sent == sent and provider3.texts == texts


async def test_barge_in_during_the_premade_opener_keeps_the_caller_first_rule(monkeypatch):
    voice, s, gw, provider, pipe = make_voice(monkeypatch, gateway=Gateway(interrupt_after=1))
    await prepare_pre_originate_greeting(voice, "user")
    n, sent_before = len(provider.texts), 0
    assert await _answer(PLATFORM, lambda: speak_disclosure_before_first_reply(s, "Hello?")) is True
    assert rps.get_disclosure_state(CALL) == "failed"
    assert [m.content for m in s.conversation_history] == ["Hello?"]
    assert getattr(s, "_has_introduced", None) is not True
    assert voice._opener_spoken_as_notice is True
    assert len(provider.texts) == n                           # nothing re-synthesized live
    assert len(pipe.calls) == 1                               # no second playback
    assert all("…" not in m.content for m in s.conversation_history)


async def test_a_partial_premade_playback_is_never_finished_live(monkeypatch):
    voice, s, gw, provider, pipe = make_voice(monkeypatch, gateway=Gateway(fail_after=1))
    await prepare_pre_originate_greeting(voice, "user")
    n = len(provider.texts)
    await _answer(PLATFORM, lambda: _speak_recording_disclosure(
        voice, opener_with_notice=NOTICE_OPENER))
    assert s._tts_delivery_failed is True
    assert len(provider.texts) == n
    assert rps.get_disclosure_state(CALL) == "failed"


async def test_the_silent_pickup_greeting_uses_the_premade_notice_opener(monkeypatch):
    voice, s, gw, provider, pipe = make_voice(monkeypatch)
    await prepare_pre_originate_greeting(voice, "user")
    n = len(provider.texts)
    await _answer(PLATFORM, lambda: _send_outbound_greeting(voice))
    assert len(provider.texts) == n
    assert voice._opener_spoken_as_notice is True
    assert NOTICE_OPENER not in voice._premade_opener_lines   # consumed
    assert gw.sent == [bytes([2, 0]) * 160] * 2


# ------------------------------------------------- scope and equivalence

async def test_sessions_without_a_store_are_untouched(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    # agent-first, an inbound-shaped call and a Test-agent-shaped session: no store
    voice, s, gw, provider, pipe = make_voice(monkeypatch, direction="inbound")
    await prepare_pre_originate_greeting(voice, "user")
    assert not hasattr(voice, "_premade_opener_lines")
    assert premade_kwargs(voice, s, PLAIN) == {}
    assert premade_kwargs(types.SimpleNamespace(), s, PLAIN) == {}
    await _answer(PLATFORM, lambda: _speak_recording_disclosure(voice, opener_with_notice=None))
    assert all(kw == {} for _, kw in pipe.calls)
    assert "opener_premade_used" not in caplog.text and "opener_premade_skipped" not in caplog.text


async def test_an_incoming_call_never_uses_or_makes_premade_opener_audio(monkeypatch):
    voice, s, gw, provider, pipe = make_voice(monkeypatch, direction="inbound")
    s._inbound_greeting = None
    await prepare_pre_originate_greeting(voice, "user")
    assert not hasattr(voice, "_premade_opener_lines")
    assert provider.texts and len(provider.texts) == 1        # only today's single ringing synthesis
    # even a store planted by mistake is never reached by the incoming identity line
    voice._premade_opener_lines = {"x": object()}
    await _answer(PLATFORM, lambda: speak_disclosure_before_first_reply(s, "Hello?"))
    assert all("premade_chunks" not in kw for _, kw in pipe.calls)


async def test_the_kill_switch_off_runs_todays_live_path(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    monkeypatch.setenv("OPENER_PREMADE_ENABLED", "false")
    voice, s, gw, provider, pipe = make_voice(monkeypatch)
    await prepare_pre_originate_greeting(voice, "user")
    assert provider.texts == [PLAIN]                          # no extra synthesis
    assert not hasattr(voice, "_premade_opener_lines")
    assert "opener_premade_wiring" in caplog.text and "enabled=False" in caplog.text
    assert "opener_premade_off" in caplog.text
    assert await _answer(PLATFORM, lambda: speak_disclosure_before_first_reply(s, "Hello?")) is True
    assert provider.texts == [PLAIN, NOTICE_OPENER]           # live, as today
    assert pipe.calls == [(NOTICE_OPENER, {"websocket": None})] or all(
        "premade_chunks" not in kw for _, kw in pipe.calls)


async def test_tts_playback_with_premade_chunks_gets_its_own_receipt(monkeypatch):
    voice, s, gw, provider, pipe = make_voice(monkeypatch)
    playback = TtsPlayback(pipe)
    chunks = [b"\x05\x00" * 160, b"\x06\x00" * 160]
    s.tts_pronunciations = None
    interrupted = await playback.synthesize_and_send(s, "Hello there.", track_latency=False,
                                                     premade_chunks=chunks)
    assert interrupted is False
    assert provider.texts == []                               # provider not called
    assert gw.sent == chunks
    assert len(gw.begin_uids) == 1 and gw.finish_uids == gw.begin_uids
    assert s._tts_playback_utterance_id == gw.begin_uids[0]
    assert s._tts_playback_receipt["utterance_id"] == gw.begin_uids[0]
    assert s._first_tts_chunk_at is not None                  # talk-over clock stamped as for live


async def test_premade_chunks_none_is_the_same_as_not_passing_it(monkeypatch):
    results = []
    for kwargs in ({}, {"premade_chunks": None}):
        voice, s, gw, provider, pipe = make_voice(monkeypatch)
        playback = TtsPlayback(pipe)
        out = await playback.synthesize_and_send(s, "Hello there.", track_latency=False, **kwargs)
        results.append((out, provider.texts, gw.sent, s._tts_delivery_status,
                        s._tts_playback_receipt["status"]))
    assert results[0] == results[1]
    assert results[0][1] == ["Hello there."]


async def test_the_pipeline_passes_premade_chunks_only_when_given(monkeypatch):
    from app.domain.services.voice_pipeline_service import VoicePipelineService

    svc = VoicePipelineService.__new__(VoicePipelineService)
    svc.__dict__["_tts_playback_inst"] = types.SimpleNamespace(
        synthesize_and_send=AsyncMock(return_value=False))
    svc._barge_in_event_for = lambda s: None
    sess = types.SimpleNamespace()
    await svc.synthesize_and_send_audio(sess, "a", track_latency=False)
    await svc.synthesize_and_send_audio(sess, "a", track_latency=False, premade_chunks=[b"\x00\x00"])
    first, second = svc.__dict__["_tts_playback_inst"].synthesize_and_send.await_args_list
    assert "premade_chunks" not in first.kwargs
    assert second.kwargs["premade_chunks"] == [b"\x00\x00"]
