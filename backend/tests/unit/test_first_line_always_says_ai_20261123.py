"""An outgoing call's first sentence always says it is an AI (23 Nov 2026).

On caller-first calls the opening was said only after greeting words; for any other
first words, or a phone's call-screening assistant, only the bare recording notice was
said and the model then introduced itself without "AI" (0 of 31 replies). Now one
sentence goes first: "This is Sarah, an AI assistant from Acme, on a recorded line."
These tests run the real caller-first helper, the real notice function and the real
TtsPlayback (harness of test_premade_caller_first_opener); only the provider, the
gateway and the policy lookup are fakes. Ids are UUIDs (the log redaction masks a
letter followed by a long run of digits).
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
from app.domain.services.telephony.modes.agent_first import prepare_pre_originate_greeting
from app.domain.services.telephony_session_config import (
    TELEPHONY_COMPANY_NAME,
    build_identity_opener,
)
from app.domain.services.voice_pipeline.caller_first_disclosure import (
    speak_disclosure_before_first_reply,
)
from app.domain.services.voice_pipeline.first_line_identity import (
    identity_clause,
    identity_line,
    say_outbound_identity_line,
)
from app.domain.services.voice_pipeline.machine_detection import build_screening_message
from tests.unit import test_premade_caller_first_opener as pm
from tests.unit.test_premade_caller_first_opener import (
    CALL,
    IDENTITY_NOTICE,
    NO_NOTICE,
    NOTICE_OPENER,
    PLATFORM,
    TENANT_NOTICE,
    TENANT_TEXT,
    Gateway,
    Provider,
    _answer,
    _ring,
    make_voice,
)

IDENTITY_PLAIN = "This is Sarah, an AI assistant from All State Estimation."
FIRST_LOGGER = "app.domain.services.voice_pipeline.first_line_identity"
WORDS = "I'm busy right now, what is this about?"

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    rps._DISCLOSURE_LEDGER.clear()
    monkeypatch.delenv("OPENER_PREMADE_ENABLED", raising=False)
    yield
    rps._DISCLOSURE_LEDGER.clear()


def _lines(caplog):
    return [r.getMessage() for r in caplog.records
            if r.name == FIRST_LOGGER and "first_line_identity" in r.getMessage()]


async def _say(monkeypatch, decision, words=WORDS, *, ring=True, **make):
    voice, s, gw, provider, pipe = make_voice(monkeypatch, **make)
    if ring:
        await _ring(voice)
    n = len(provider.texts)
    result = await _answer(decision, lambda: speak_disclosure_before_first_reply(s, words))
    return result, voice, s, gw, provider, pipe, n


def _spoken(pipe):
    return [c[0] for c in pipe.calls]


# ------------------------------------------------------------------ the words

_NAMES = [("Sarah", "All State Estimation"), ("", "All State Estimation"), ("Sarah", ""),
          (None, None), ("  Sarah ", " Acme  ")]


@pytest.mark.parametrize("agent,company", _NAMES)
async def test_the_clause_and_the_opening_builder_cannot_drift_apart(agent, company):
    clause = identity_clause(agent, company)
    for recorded in (True, False):
        suffix = ", on a recorded line" if recorded else ""
        assert build_identity_opener(agent, company, "John", recorded_line=recorded) == (
            f"Hi, {clause}{suffix}. Am I speaking with John?")
        assert build_identity_opener(agent, company, None, recorded_line=recorded) == (
            f"Hi, {clause}{suffix}. Who am I speaking with?")


async def test_the_words_for_every_account():
    assert identity_line("Sarah", "Acme", recorded_line=True) == (
        "This is Sarah, an AI assistant from Acme, on a recorded line.")
    assert identity_line("Sarah", "Acme", recorded_line=False) == "This is Sarah, an AI assistant from Acme."
    assert identity_line("", "Acme", recorded_line=True) == "This is an AI assistant from Acme, on a recorded line."
    assert identity_line("Sarah", "", recorded_line=False) == (
        f"This is Sarah, an AI assistant from {TELEPHONY_COMPANY_NAME}.")
    assert identity_line(None, None, recorded_line=True) == (
        f"This is an AI assistant from {TELEPHONY_COMPANY_NAME}, on a recorded line.")
    for agent, company in _NAMES:
        assert "AI assistant" in identity_line(agent, company, recorded_line=False)


# ------------------------------------------------------- the platform notice

async def test_platform_notice_non_greeting_says_one_sentence_as_the_notice_then_the_model_runs(
        monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    result, voice, s, gw, provider, pipe, n = await _say(monkeypatch, PLATFORM)
    assert result is False                                   # the model still answers them
    assert _spoken(pipe) == [IDENTITY_NOTICE]                # ONE sentence, no bare notice
    assert voice._opener_spoken_as_notice is True and s._has_introduced is True
    assert [(m.role, m.content) for m in s.conversation_history] == [
        (MessageRole.ASSISTANT, IDENTITY_NOTICE)]
    assert rps.get_disclosure_state(CALL) == "transmitted"
    assert _lines(caplog) == [f"first_line_identity call_id={CALL[:12]} said=in_notice notice=transmitted delivered=true"]
    assert "busy" not in caplog.text.split("first_line_identity")[-1]


@pytest.mark.parametrize("row", pm._ROWS)
async def test_every_receipt_row_gives_the_same_ledger_result_as_the_opening(monkeypatch, row):
    status, evidence, mismatch, expected = row
    gateway = Gateway(status, evidence, mismatch=mismatch)
    result, voice, s, gw, *_ = await _say(monkeypatch, PLATFORM, gateway=gateway)
    assert result is False
    assert rps.get_disclosure_state(CALL) == expected
    assert voice._opener_spoken_as_notice is True
    delivered = expected != "failed"
    assert getattr(s, "_has_introduced", False) is delivered
    assert [m.content for m in s.conversation_history] == ([IDENTITY_NOTICE] if delivered else [])


async def test_a_cut_off_sentence_is_not_recorded_not_restarted_and_nothing_follows(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    result, voice, s, gw, provider, pipe, n = await _say(
        monkeypatch, PLATFORM, gateway=Gateway(interrupt_after=1))
    assert result is False
    assert _spoken(pipe) == [IDENTITY_NOTICE]
    assert rps.get_disclosure_state(CALL) == "failed"
    assert s.conversation_history == [] and not getattr(s, "_has_introduced", False)
    assert voice._opener_spoken_as_notice is True            # attempted: never restarted
    await say_outbound_identity_line(s, voice)
    assert await speak_disclosure_before_first_reply(s, "Hello?") is False
    assert _spoken(pipe) == [IDENTITY_NOTICE]
    assert _lines(caplog) == [f"first_line_identity call_id={CALL[:12]} said=in_notice notice=failed delivered=false"]


# ------------------------------------------------ the account's own sentence

async def test_own_sentence_default_placement_is_said_first_then_the_plain_sentence(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    result, voice, s, gw, provider, pipe, n = await _say(monkeypatch, TENANT_NOTICE)
    assert result is False
    assert _spoken(pipe) == [TENANT_TEXT, IDENTITY_PLAIN]
    assert [m.content for m in s.conversation_history] == [TENANT_TEXT, IDENTITY_PLAIN]
    assert s._has_introduced is True
    assert _lines(caplog) == [f"first_line_identity call_id={CALL[:12]} said=after_notice notice=transmitted delivered=true"]


PLACEMENT_LOGGER = "app.domain.services.notice_placement"


def _in_opening_decision(text=TENANT_TEXT):
    return RecordingDecision(
        should_record=True, announcement_required=True, announcement_text=text,
        opt_out_dtmf_digit=None, retention_days=90, reason="test",
        outbound_notice_placement="in_opening")


async def test_own_sentence_with_the_account_choice_in_opening_is_still_said_then_the_plain_sentence(
        monkeypatch, caplog):
    # The Settings page promises the account's own sentence is still said on a call
    # where the opening is not said (e.g. first words that are not a greeting).
    caplog.set_level(logging.INFO)
    monkeypatch.setenv("OUTBOUND_NOTICE_PLACEMENT_CHOICE_ENABLED", "true")
    result, voice, s, gw, provider, pipe, n = await _say(monkeypatch, _in_opening_decision())
    assert result is False
    assert _spoken(pipe) == [TENANT_TEXT, IDENTITY_PLAIN]
    assert voice._opener_spoken_as_notice is False
    assert [m.content for m in s.conversation_history] == [TENANT_TEXT, IDENTITY_PLAIN]
    assert s._has_introduced is True
    # the proof ledger is decided on the account's own sentence
    assert rps.get_disclosure_state(CALL) == "transmitted"
    assert _lines(caplog) == [f"first_line_identity call_id={CALL[:12]} said=after_notice notice=transmitted delivered=true"]
    placement = [r.getMessage() for r in caplog.records if r.name == PLACEMENT_LOGGER]
    assert len(placement) == 1
    assert ("stored=in_opening choice_switch=on said=notice_then_identity_line "
            "why=account_choice_is_for_the_opening") in placement[0]


async def test_platform_notice_placement_line_names_the_identity_line(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    await _say(monkeypatch, PLATFORM)
    placement = [r.getMessage() for r in caplog.records if r.name == PLACEMENT_LOGGER]
    assert len(placement) == 1
    assert "said=identity_line_with_recorded_line why=platform_wording" in placement[0]


async def test_policy_read_error_still_says_the_plain_sentence_and_the_log_claims_no_notice(
        monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    voice, s, gw, provider, pipe = make_voice(monkeypatch)
    await _ring(voice)
    container = types.SimpleNamespace(is_initialized=True, db_pool=object())
    with patch("app.core.container.get_container", return_value=container), patch.object(
            rps.RecordingPolicyService, "decide", new=AsyncMock(side_effect=RuntimeError("db down"))):
        assert await speak_disclosure_before_first_reply(s, WORDS) is False
    assert _spoken(pipe) == [IDENTITY_PLAIN]
    assert rps.get_disclosure_state(CALL) == "failed"
    assert _lines(caplog) == [f"first_line_identity call_id={CALL[:12]} said=alone notice=failed delivered=true"]


async def test_an_own_sentence_that_is_cut_off_is_still_followed_by_the_plain_sentence(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    result, voice, s, gw, provider, pipe, n = await _say(
        monkeypatch, TENANT_NOTICE, gateway=Gateway(interrupt_after=1))
    assert result is False
    assert _spoken(pipe) == [TENANT_TEXT, IDENTITY_PLAIN]
    assert rps.get_disclosure_state(CALL) == "failed"        # the notice is NOT confirmed
    assert [m.content for m in s.conversation_history] == [IDENTITY_PLAIN]   # only the delivered one
    assert s._has_introduced is True
    assert _lines(caplog) == [f"first_line_identity call_id={CALL[:12]} said=after_notice notice=failed delivered=true"]


async def test_recording_switched_off_says_the_plain_sentence_alone(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    off = RecordingDecision(
        should_record=False, announcement_required=False, announcement_text=None,
        opt_out_dtmf_digit=None, retention_days=90, reason="tenant_policy_disabled")
    result, voice, s, gw, provider, pipe, n = await _say(monkeypatch, off)
    assert result is False
    assert _spoken(pipe) == [IDENTITY_PLAIN]
    assert rps.get_disclosure_state(CALL) == "not_required"
    assert _lines(caplog) == [f"first_line_identity call_id={CALL[:12]} said=alone notice=not_required delivered=true"]


async def test_no_notice_owed_says_the_plain_sentence_alone(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    result, voice, s, gw, provider, pipe, n = await _say(monkeypatch, NO_NOTICE)
    assert result is False
    assert _spoken(pipe) == [IDENTITY_PLAIN]
    assert rps.get_disclosure_state(CALL) == "not_required"
    assert s._has_introduced is True
    assert _lines(caplog) == [f"first_line_identity call_id={CALL[:12]} said=alone notice=not_required delivered=true"]


async def test_a_cut_off_plain_sentence_is_not_recorded_and_not_restarted(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    result, voice, s, gw, provider, pipe, n = await _say(
        monkeypatch, NO_NOTICE, gateway=Gateway(interrupt_after=1))
    assert _spoken(pipe) == [IDENTITY_PLAIN]
    assert s.conversation_history == [] and not getattr(s, "_has_introduced", False)
    await say_outbound_identity_line(s, voice)
    assert _spoken(pipe) == [IDENTITY_PLAIN]
    assert _lines(caplog) == [f"first_line_identity call_id={CALL[:12]} said=alone notice=not_required delivered=false"]


@pytest.mark.parametrize("opener", [True, False])
async def test_an_opt_out_or_goodbye_first_also_gets_the_sentence(monkeypatch, opener):
    voice, s, gw, provider, pipe = make_voice(monkeypatch)
    await _ring(voice)
    result = await _answer(PLATFORM, lambda: speak_disclosure_before_first_reply(
        s, "Do not call me again.", opener=opener))
    assert result is False and _spoken(pipe) == [IDENTITY_NOTICE]


# ----------------------------------------------- no second line, incoming calls

async def test_greeting_words_get_the_opening_and_no_second_identity_line(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    result, voice, s, gw, provider, pipe, n = await _say(monkeypatch, PLATFORM, "Hello?")
    assert result is True and _spoken(pipe) == [NOTICE_OPENER]
    assert _lines(caplog) == []                               # the opening said it; no identity line


async def test_after_a_silent_pickup_opening_no_identity_line_is_added(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    voice, s, gw, provider, pipe = make_voice(monkeypatch)
    voice._recording_disclosure_attempted = True
    voice._opener_spoken_as_notice = True
    s._has_introduced = True
    assert await speak_disclosure_before_first_reply(s, WORDS) is False
    assert pipe.calls == [] and _lines(caplog) == []


@pytest.mark.parametrize("decision", ["TENANT_NOTICE", "NO_NOTICE", "PLATFORM"])
async def test_a_cut_off_silent_pickup_opening_is_not_followed_by_an_identity_line(monkeypatch, caplog, decision):
    # The silent-pickup greeting ran (notice attempted, opening started) and the
    # person cut the opening off: nothing was delivered. Their words then get the
    # model's reply, and no identity line (the opening is never said a second time
    # in another form): the same rule the pickup-words branch has always had.
    caplog.set_level(logging.INFO)
    voice, s, gw, provider, pipe = make_voice(monkeypatch)
    voice._recording_disclosure_attempted = True
    voice._opener_spoken_as_notice = False
    s._has_introduced = False
    result = await _answer(getattr(pm, decision), lambda: speak_disclosure_before_first_reply(s, WORDS))
    assert result is False and pipe.calls == [] and _lines(caplog) == []
    assert s.conversation_history == [] and not getattr(s, "_has_introduced", False)


async def test_an_incoming_call_is_exactly_as_before(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    result, voice, s, gw, provider, pipe, n = await _say(
        monkeypatch, PLATFORM, ring=False, direction="inbound")
    assert result is False
    assert _spoken(pipe) == [
        "This call may be recorded for quality and training purposes.",
        "Thank you for calling All State Estimation. This is Sarah, an AI assistant."]
    assert _lines(caplog) == []


# ------------------------------------------------ the model still runs, once

async def test_through_the_real_turn_handler_the_model_runs_once_and_sees_the_line_first():
    from tests.unit.test_instant_opener_introduced_greeting_stops import (
        _FakeVoiceSession, _pipeline_stub, _session,
    )
    from app.domain.services.voice_pipeline.turn_ender import TurnEnder

    session = _session()
    voice_session = _FakeVoiceSession(session)
    session._voice_session_ref = voice_session
    pipeline = _pipeline_stub()
    voice_session.pipeline = pipeline
    await TurnEnder(pipeline).handle(session, None, user_text=WORDS)
    assert pipeline._run_turn.await_count == 1
    first = session.conversation_history[0]
    assert first.role == MessageRole.ASSISTANT and first.content.startswith("This is ")
    assert "an AI assistant" in first.content
    assert session._has_introduced is True


# ------------------------------------------------------------- pre-made audio

async def test_ringing_makes_the_identity_sentence_after_the_notice_opener(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    voice, s, gw, provider, pipe = make_voice(monkeypatch)
    await _ring(voice)
    assert provider.texts == [pm.PLAIN, NOTICE_OPENER, IDENTITY_NOTICE]   # in sequence
    line = voice._premade_opener_lines[IDENTITY_NOTICE]
    assert line.kind == "identity_notice" and line.text == IDENTITY_NOTICE
    assert "kind=identity_notice" in caplog.text


async def test_the_identity_sentence_is_never_awaited_on_the_dial_path(monkeypatch):
    voice, s, gw, provider, pipe = make_voice(monkeypatch, provider=Provider(delay=5.0, delay_on=3))
    await asyncio.wait_for(prepare_pre_originate_greeting(voice, "user"), timeout=1.0)
    assert IDENTITY_NOTICE not in voice._premade_opener_lines
    voice._premade_opener_task.cancel()
    await asyncio.gather(voice._premade_opener_task, return_exceptions=True)


async def test_a_ready_premade_sentence_is_used_once(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    result, voice, s, gw, provider, pipe, n = await _say(monkeypatch, PLATFORM)
    assert len(provider.texts) == n                           # no provider call after ringing
    assert "premade_chunks" in pipe.calls[0][1]
    assert "opener_premade_used call_id=" in caplog.text and "kind=identity_notice" in caplog.text
    assert gw.sent == [bytes([3, 0]) * 160] * 2
    assert pm.premade_kwargs(voice, s, IDENTITY_NOTICE) == {}  # used at most once


async def test_not_ready_is_spoken_live_and_the_late_result_is_dropped(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    voice, s, gw, provider, pipe = make_voice(monkeypatch, provider=Provider(delay=0.4, delay_on=3))
    await pm.prepare_pre_originate_greeting(voice, "user")
    await asyncio.sleep(0.05)                                 # notice opener done, identity in flight
    result = await _answer(PLATFORM, lambda: speak_disclosure_before_first_reply(s, WORDS))
    assert result is False and _spoken(pipe) == [IDENTITY_NOTICE]
    assert "premade_chunks" not in pipe.calls[0][1]
    assert provider.texts[-1] == IDENTITY_NOTICE and provider.texts.count(IDENTITY_NOTICE) == 2
    assert "reason=not_ready_yet" in caplog.text
    await asyncio.gather(voice._premade_opener_task, return_exceptions=True)
    assert IDENTITY_NOTICE not in voice._premade_opener_lines
    assert "opener_premade_dropped call_id=11111111-111 kind=identity_notice" in caplog.text
    assert pm.premade_kwargs(voice, s, IDENTITY_NOTICE) == {}


async def test_the_not_ready_log_line_names_the_identity_sentence(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    voice, s, gw, provider, pipe = make_voice(monkeypatch, provider=Provider(delay=0.4, delay_on=3))
    await pm.prepare_pre_originate_greeting(voice, "user")
    await asyncio.sleep(0.05)
    await _answer(PLATFORM, lambda: speak_disclosure_before_first_reply(s, WORDS))
    skipped = [r.getMessage() for r in caplog.records
               if r.name == pm.LOGGER and "opener_premade_skipped" in r.getMessage()]
    assert len(skipped) == 1 and "kind=identity_notice reason=not_ready_yet" in skipped[0]
    await asyncio.gather(voice._premade_opener_task, return_exceptions=True)


async def test_once_the_opening_was_spoken_the_identity_sentence_is_not_started(monkeypatch, caplog):
    # The person said hello before the notice opening was ready: it is spoken live.
    # The identity sentence can now never be needed, so the background task does not
    # ask the provider for it while the call is talking.
    caplog.set_level(logging.INFO)
    voice, s, gw, provider, pipe = make_voice(monkeypatch, provider=Provider(delay=0.2, delay_on=2))
    await pm.prepare_pre_originate_greeting(voice, "user")
    assert await _answer(PLATFORM, lambda: speak_disclosure_before_first_reply(s, "Hello?")) is True
    await asyncio.gather(voice._premade_opener_task, return_exceptions=True)
    assert IDENTITY_NOTICE not in provider.texts
    assert "opener_premade_not_started call_id=11111111-111 kind=identity_notice" in caplog.text
    assert IDENTITY_NOTICE not in voice._premade_opener_pending


async def test_a_failing_provider_is_not_asked_for_the_identity_sentence(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    voice, s, gw, provider, pipe = make_voice(monkeypatch, provider=Provider(fail_on=2))
    await _ring(voice)
    assert provider.texts == [pm.PLAIN, NOTICE_OPENER]
    assert len([r for r in caplog.records if "opener_premade_failed" in r.getMessage()]) == 1


# ---------------------------------------------------------- screening message

def _cfg(agent, company, reason=None):
    return types.SimpleNamespace(agent_config=types.SimpleNamespace(
        agent_name=agent, company_name=company, call_reason=reason))


async def test_the_screening_message_says_an_ai_assistant_in_every_case():
    tail = " We'll try again another time. Goodbye."
    assert build_screening_message(_cfg("Grace Lee", "All State")) == (
        "Thanks, this is Grace, an AI assistant from All State." + tail)
    assert build_screening_message(_cfg("Grace", "")) == "Thanks, this is Grace, an AI assistant." + tail
    assert build_screening_message(_cfg("", "All State")) == (
        "Thanks, this is an AI assistant from All State." + tail)
    assert build_screening_message(_cfg("", "")) == "Thanks, this is an AI assistant." + tail
    assert build_screening_message(types.SimpleNamespace()) == "Thanks, this is an AI assistant." + tail
    assert build_screening_message(_cfg("Grace", "All State", "to book a survey")) == (
        "Thanks, this is Grace, an AI assistant from All State, calling to book a survey." + tail)
    assert build_screening_message(_cfg("Grace", "All State", "your estimate")) == (
        "Thanks, this is Grace, an AI assistant from All State, calling about your estimate." + tail)


# ------------------------------------------------------------------ realtime pin

async def test_realtime_is_untouched():
    import pathlib

    import app.realtime as realtime

    root = pathlib.Path(realtime.__file__).parent
    assert not [p.name for p in root.rglob("*.py") if "first_line_identity" in p.read_text(encoding="utf-8")]


# ------------------------------------- an unclear first utterance (turn-0 floor)

REPROMPT = "Sorry, I didn't catch that — could you say that again?"
INCOMING_NOTICE = "This call may be recorded for quality and training purposes."
INCOMING_LINE = "Thank you for calling All State Estimation. This is Sarah, an AI assistant."


def _turn_rig(monkeypatch, *, direction="outbound", first_speaker="user"):
    from app.domain.services.voice_pipeline.turn_ender import TurnEnder

    voice, s, gw, provider, pipe = make_voice(monkeypatch, direction=direction)
    s._first_speaker = first_speaker
    s.state = None
    s.increment_turn = lambda: None
    s.add_latency_measurement = lambda *a, **k: None
    p = MagicMock()
    p._is_repetitive_transcript = lambda text: False
    p.synthesize_and_send_audio = pipe.synthesize_and_send_audio
    p._run_turn = AsyncMock(return_value=("Sure, go on.", 10.0, 10.0))
    p.latency_tracker = MagicMock()
    p.latency_tracker.get_metrics.return_value = None

    async def turn(decision, text, confidence):
        await _answer(decision, lambda: TurnEnder(p).handle(s, None, user_text=text, confidence=confidence))

    return voice, s, pipe, p, turn


async def test_outgoing_caller_first_unclear_words_hear_the_identity_sentence_then_the_reprompt(
        monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    voice, s, pipe, p, turn = _turn_rig(monkeypatch)
    await _ring(voice)
    await turn(PLATFORM, "mm uh", 0.05)
    assert _spoken(pipe) == [IDENTITY_NOTICE, REPROMPT]       # AI first, then the reprompt
    assert p._run_turn.await_count == 0
    assert rps.get_disclosure_state(CALL) == "transmitted"
    # a second unclear utterance: the one-try flag keeps the identity sentence to once
    await turn(PLATFORM, "uh mm", 0.05)
    assert _spoken(pipe) == [IDENTITY_NOTICE, REPROMPT, REPROMPT]
    # the next accepted turn: the model answers; the identity sentence is not said again
    await turn(PLATFORM, WORDS, 0.97)
    assert _spoken(pipe) == [IDENTITY_NOTICE, REPROMPT, REPROMPT]
    assert p._run_turn.await_count == 1
    assert [m.content for m in s.conversation_history].count(IDENTITY_NOTICE) == 1
    assert _lines(caplog) == [f"first_line_identity call_id={CALL[:12]} said=in_notice notice=transmitted delivered=true"]


async def test_outgoing_caller_first_unclear_words_own_notice_then_plain_sentence_then_reprompt(monkeypatch):
    voice, s, pipe, p, turn = _turn_rig(monkeypatch)
    await _ring(voice)
    await turn(TENANT_NOTICE, "mm uh", 0.05)
    assert _spoken(pipe) == [TENANT_TEXT, IDENTITY_PLAIN, REPROMPT]


async def test_incoming_call_unclear_words_get_only_the_reprompt_and_the_identity_line_comes_later(monkeypatch):
    voice, s, pipe, p, turn = _turn_rig(monkeypatch, direction="inbound")
    await turn(PLATFORM, "mm uh", 0.05)
    assert _spoken(pipe) == [REPROMPT]
    assert not getattr(s, "_caller_first_disclosure_done", False)   # the one try is not used up
    await turn(PLATFORM, WORDS, 0.97)
    assert _spoken(pipe) == [REPROMPT, INCOMING_NOTICE, INCOMING_LINE]
    assert p._run_turn.await_count == 1


async def test_agent_first_outgoing_call_unclear_words_are_exactly_as_before(monkeypatch):
    voice, s, pipe, p, turn = _turn_rig(monkeypatch, first_speaker="agent")
    await turn(PLATFORM, "mm uh", 0.05)
    assert _spoken(pipe) == [REPROMPT]
    assert not getattr(s, "_caller_first_disclosure_done", False)
    await turn(PLATFORM, WORDS, 0.97)
    assert _spoken(pipe) == [REPROMPT]
    assert p._run_turn.await_count == 1


async def test_a_failure_of_the_identity_step_never_stops_the_reprompt(monkeypatch):
    voice, s, pipe, p, turn = _turn_rig(monkeypatch)
    await _ring(voice)
    monkeypatch.setattr(
        "app.domain.services.voice_pipeline.caller_first_disclosure.speak_disclosure_before_first_reply",
        AsyncMock(side_effect=RuntimeError("boom")))
    await turn(PLATFORM, "mm uh", 0.05)
    assert _spoken(pipe) == [REPROMPT]
