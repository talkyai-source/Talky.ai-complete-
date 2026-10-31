"""Opt-out is the model's judgement, proven by the caller's own words.

Standard CMP-1 (docs/standards/voice-agent-standards.md): a caller who asks not
to be contacted again is suppressed in the same call, in any wording. Before
2026-10-08 only a fixed phrase list counted: "never ring this number again"
made the model flag do_not_call, the flag was dropped, the hangup was denied
and the caller stayed callable. On the end_call tool path (every campaign with
tools) there was no do_not_call channel at all.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.services.end_session_action import (
    caller_quote_verified,
    parse_end_session_action,
    should_honor_end_session,
    verified_opt_out,
)
from app.domain.services.voice_pipeline.action_tools import ACTION_END_CALL, run_voice_action
from app.domain.services.voice_pipeline.end_call import model_end_call_allowed
from app.domain.services.voice_pipeline.identity_disposition import contains_dnc

PARAPHRASE = "Look, never bother this number again, alright?"


def test_quote_must_be_words_the_caller_actually_said():
    assert caller_quote_verified("never bother this number again", PARAPHRASE)
    assert caller_quote_verified("Never bother THIS number again!", PARAPHRASE)
    assert not caller_quote_verified("stop calling me", PARAPHRASE)
    assert not caller_quote_verified("again", PARAPHRASE)  # one word proves nothing
    assert not caller_quote_verified("", PARAPHRASE)
    assert not caller_quote_verified(None, PARAPHRASE)


def test_model_judgement_needs_a_verified_quote():
    with_quote = {"do_not_call": True, "opt_out_quote": "never bother this number again"}
    assert verified_opt_out(with_quote, PARAPHRASE)
    assert not verified_opt_out({"do_not_call": True}, PARAPHRASE)
    assert not verified_opt_out({"do_not_call": True, "opt_out_quote": "remove me"}, PARAPHRASE)
    assert not verified_opt_out({"do_not_call": False, "opt_out_quote": "never bother this number again"}, PARAPHRASE)
    # The phrase floor needs no model at all.
    assert verified_opt_out(None, "please stop calling me")


def test_paraphrased_opt_out_ends_the_call_only_with_the_quote():
    action = {"reason": "user_done", "farewell": "Understood.", "do_not_call": True}
    assert should_honor_end_session(action, PARAPHRASE, user_turn_count=2) is False
    action["opt_out_quote"] = "never bother this number again"
    assert should_honor_end_session(action, PARAPHRASE, user_turn_count=2) is True


def test_envelope_carries_the_quote_only_for_an_opt_out():
    opted = parse_end_session_action(json.dumps({
        "action": "end_session", "reason": "user_done", "farewell": "Understood.",
        "do_not_call": True, "opt_out_quote": " never bother this number again ",
    }))
    assert opted["opt_out_quote"] == "never bother this number again"
    plain = parse_end_session_action(json.dumps({
        "action": "end_session", "reason": "user_goodbye", "farewell": "Bye.",
        "opt_out_quote": "goodbye then",
    }))
    assert "opt_out_quote" not in plain


def _session():
    return SimpleNamespace(call_id="call-optout-1", conversation_history=[])


@pytest.mark.parametrize("recorded", [True, False])
async def test_end_call_tool_records_a_verified_opt_out_before_the_goodbye(monkeypatch, recorded):
    purge = AsyncMock(return_value=recorded)
    monkeypatch.setattr("app.domain.services.dialer.opt_out.purge_opt_out_before_farewell", purge)
    session = _session()
    result = await run_voice_action(
        session, ACTION_END_CALL,
        {"reason": "opt out", "do_not_call": True, "opt_out_quote": "never bother this number again"},
        user_text=PARAPHRASE,
    )
    purge.assert_awaited_once_with(session)
    assert session._caller_opted_out is True and session._end_call_requested is True
    assert result["status"] == "accepted"
    assert result["opt_out"] == ("recorded" if recorded else "unconfirmed")
    # The goodbye may claim removal only when the write landed.
    assert ("won't be contacted again" in result["message"]) is recorded
    assert model_end_call_allowed(session, PARAPHRASE) is True


async def test_end_call_tool_rejects_an_invented_opt_out(monkeypatch):
    purge = AsyncMock(return_value=True)
    monkeypatch.setattr("app.domain.services.dialer.opt_out.purge_opt_out_before_farewell", purge)
    session = _session()
    result = await run_voice_action(
        session, ACTION_END_CALL,
        {"reason": "opt out", "do_not_call": True, "opt_out_quote": "remove me from your list"},
        user_text="What does the sleeper seat cost?",
    )
    purge.assert_not_awaited()
    assert getattr(session, "_caller_opted_out", False) is False
    assert result["status"] == "caller_intent_unconfirmed"


def test_uk_wording_is_in_the_phrase_floor_without_catching_complaints():
    for said in ("stop ringing me", "don't ring me again", "please never ring me",
                 "put me on your do not call list", "do not phone me"):
        assert contains_dnc(said), said
    for said in ("your team never ring back", "can you ring me tomorrow", "I'll phone you later"):
        assert not contains_dnc(said), said


async def test_end_call_tool_never_lets_an_unbacked_opt_out_claim_removal(monkeypatch):
    # The caller said goodbye, so the call may end, but the model's opt-out
    # quote is not theirs: nothing is recorded and the goodbye must not say so.
    purge = AsyncMock(return_value=True)
    monkeypatch.setattr("app.domain.services.dialer.opt_out.purge_opt_out_before_farewell", purge)
    session = _session()
    result = await run_voice_action(
        session, ACTION_END_CALL,
        {"reason": "bye", "do_not_call": True, "opt_out_quote": "remove me from your list"},
        user_text="Okay, goodbye.",
    )
    purge.assert_not_awaited()
    assert result["status"] == "accepted" and result["opt_out"] == "not_recorded"
    assert "do not say they were removed" in result["message"]


def test_a_removal_claim_is_spoken_only_after_the_write_landed():
    from app.domain.services.voice_pipeline.speech_guard import OPT_OUT_PENDING_LINE, guard_spoken_sentence
    session = SimpleNamespace(_voice_action_results={}, conversation_history=[])
    for claim in ("I've removed you from our list.", "You've been taken off our list.",
                  "You're now unsubscribed."):
        assert guard_spoken_sentence(session, claim) == OPT_OUT_PENDING_LINE, claim
    session._opt_out_recorded = True
    assert guard_spoken_sentence(session, "I've removed you from our list.") == "I've removed you from our list."
    # Ordinary uses of the words are untouched.
    assert guard_spoken_sentence(SimpleNamespace(_voice_action_results={}, conversation_history=[]),
                                 "We removed the booking fee last year.") == "We removed the booking fee last year."


async def test_purge_success_marks_the_live_session(monkeypatch):
    from app.domain.services.dialer import opt_out
    monkeypatch.setattr(opt_out, "_purge_opt_out_before_farewell", AsyncMock(return_value=True))
    session = _session()
    assert await opt_out.purge_opt_out_before_farewell(session) is True
    assert session._opt_out_recorded is True
    monkeypatch.setattr(opt_out, "_purge_opt_out_before_farewell", AsyncMock(return_value=False))
    other = _session()
    assert await opt_out.purge_opt_out_before_farewell(other) is False
    assert not hasattr(other, "_opt_out_recorded")


# Review 2026-10-08: a model flag plus ANY two caller words used to count.
@pytest.mark.parametrize("quote, said", [
    ("not interested", "No, I'm not interested thanks."),
    ("I'm busy", "Sorry I'm busy right now"),
    ("call me", "can you call me tomorrow"),
    ("no thanks", "No thanks, bye."),
    ("I am", "I am not interested thanks"),
    ("don't call me tomorrow", "Don't call me tomorrow, call me Friday."),
    ("quit bothering me", "Quit bothering me. Wait, actually, what's the price?"),
])
def test_a_decline_or_a_preference_is_never_an_opt_out(quote, said):
    assert verified_opt_out({"do_not_call": True, "opt_out_quote": quote}, said) is False


@pytest.mark.parametrize("quote, said", [
    ("do not call me again", "Don't call me again."),
    ("dont ever bother me", "Don't ever bother me, please."),
    ("please take me off the list", "Take me off your list."),
    ("quit bothering me", "Quit bothering me, why do you people keep phoning?"),
    ("never text me", "Never text me, ok?"),
])
def test_real_opt_outs_match_across_stt_variants(quote, said):
    assert verified_opt_out({"do_not_call": True, "opt_out_quote": quote}, said) is True


def test_uk_floor_phrases_need_a_directed_object():
    assert not contains_dnc("sorry the doorbell keeps ringing, one sec I need to stop ringing it")
    assert not contains_dnc("I would never ring this number normally")
    assert contains_dnc("never ring this number again")


async def test_one_failed_write_does_not_stack_dead_air(monkeypatch):
    from app.domain.services.dialer import opt_out
    inner = AsyncMock(return_value=False)
    monkeypatch.setattr(opt_out, "_purge_opt_out_before_farewell", inner)
    session = _session()
    assert await opt_out.purge_opt_out_before_farewell(session) is False
    assert await opt_out.purge_opt_out_before_farewell(session) is False
    inner.assert_awaited_once()  # the teardown purge retries, not the live call


@pytest.mark.parametrize("said", [
    "Don't call me tomorrow, call me Friday.", "Please don't call me at work.",
    "Don't call me right now, I'm driving.", "Do not call me before ten.",
])
def test_a_scheduling_preference_is_not_an_opt_out(said):
    # Pre-existing floor false positive found in review 2026-10-08.
    assert not contains_dnc(said)


@pytest.mark.parametrize("said", [
    "Don't call me again.", "Stop calling me now!", "Do not call me, ever.",
    "Take me off your list.", "Never call me again, I'm not interested.",
])
def test_directed_opt_outs_still_hit_the_floor(said):
    assert contains_dnc(said)
