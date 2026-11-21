"""Opt-out followed by a request, after the second review (2026-11-21).

The rule: an opt-out is always recorded; it ends the call unless the caller, AFTER
the opt-out, asks for something; and an opted-out person is never pitched and never
left on an open line. Every case runs through the real functions in sequence:
``run_voice_action`` (the tool gate) and then ``model_end_call_allowed`` (the
end-of-turn gate), and the two must agree.
"""
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.services.voice_pipeline.action_tools import run_voice_action
from app.domain.services.voice_pipeline.end_call import model_end_call_allowed
from app.domain.services.voice_pipeline.live_structured_state import (
    reduce_cascaded_session_live_state, render_live_state_block,
)


def _session(**flags):
    return SimpleNamespace(conversation_history=[], **flags)


@pytest.fixture
def purge(monkeypatch):
    mock = AsyncMock(return_value=True)
    monkeypatch.setattr("app.domain.services.dialer.opt_out.purge_opt_out_before_farewell", mock)
    return mock


async def both_gates(session, text, args):
    """The tool gate, then the end-of-turn gate on the same words."""
    result = await run_voice_action(session, "end_call", args, user_text=text)
    gate = model_end_call_allowed(session, text)
    assert result["success"] is gate, (text, result["success"], gate)
    return result


# (sentence, the model's quote or None when the phrase list already catches it)
HOSTILE = [
    ("How many times do I have to tell you, stop calling me!", "stop calling me"),
    ("Why do you keep phoning? Quit phoning me.", "quit phoning me"),
    ("Quit phoning me. Why do you keep doing this?", "quit phoning me"),
    ("Stop calling me. Why do you keep calling?", None),
    ("Stop calling me, and when I say stop I mean stop!", None),
    ("Take me off your list and how dare you call me at dinner!", None),
    ("Stop calling me and I don't care how.", None),
    ("Take me off your list and what's wrong with you people?", None),
    ("Stop calling me and what's more I'm reporting you.", None),
    ("Stop calling me, and when you call again I'll report you.", None),
]
KEEP_OPEN = [
    "Don't call me again, but I need help with my account.",
    "Do not call me again, but first, what are your opening hours?",
    "Do not call me again, but what are your opening hours?",
    "Stop calling me, and also how do I get a refund?",
]


def _args(quote):
    return {"do_not_call": True, **({"opt_out_quote": quote} if quote else {})}


# ------------------------------------------------ N1 + N2: hostile opt-outs close
@pytest.mark.asyncio
@pytest.mark.parametrize("text,quote", HOSTILE)
async def test_hostile_opt_out_closes_through_both_gates(purge, text, quote):
    session = _session()
    result = await both_gates(session, text, _args(quote))
    assert result["success"] is True
    assert session._end_call_requested is True
    assert purge.await_count == 1


# ------------------------------------------------ opt-out + a real request stays open
@pytest.mark.asyncio
@pytest.mark.parametrize("text", KEEP_OPEN)
async def test_request_after_the_opt_out_keeps_the_call_open_and_tells_the_model(purge, text):
    session = _session()
    result = await both_gates(session, text, {"do_not_call": True})
    assert result["success"] is False
    assert getattr(session, "_end_call_requested", False) is not True
    assert purge.await_count == 1
    assert "make no offer and do not continue the campaign" in result["message"]
    assert "opt-out is recorded" in result["message"]


@pytest.mark.asyncio
async def test_opt_out_alone_closes_and_writes_once(purge):
    session = _session()
    result = await both_gates(session, "Do not call me again.", {"do_not_call": True})
    assert result["success"] is True and purge.await_count == 1


@pytest.mark.asyncio
async def test_request_first_then_the_opt_out_closes(purge):
    session = _session()
    result = await both_gates(
        session, "What are your hours? Actually never mind, do not call me again.", {"do_not_call": True})
    assert result["success"] is True and purge.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("text", [
    'The script says "do not call me again", but what are your hours?',
    "I am not saying do not call me again. What are your hours?",
    "Yesterday he said do not call me again. What are your hours?",
])
async def test_reported_or_negated_opt_outs_write_nothing_and_do_not_close(purge, text):
    session = _session()
    result = await both_gates(session, text, {"do_not_call": True})
    assert result["success"] is False
    assert purge.await_count == 0
    assert getattr(session, "_caller_opted_out", False) is not True


# ------------------------------------------------ N3: a stored opt-out is no standing permission
@pytest.mark.asyncio
@pytest.mark.parametrize("text,closes", [
    ("My account number is 55012.", False),
    ("It's about my bill.", False),
    ("Hello? Are you still there?", False),
    ("The problem is with my last invoice.", False),
    ("Actually, can you tell me your address?", False),
    ("", False),
    ("Okay, thanks.", True),
    ("Okay thanks, bye.", True),
    ("No, that's all, goodbye.", True),
    ("Nothing else, thank you.", True),
    ("That is everything.", True),
])
async def test_later_turn_after_a_stored_opt_out(purge, text, closes):
    session = _session()
    first = await both_gates(session, "Don't call me again, but I need help with my account.",
                             {"do_not_call": True})
    assert first["success"] is False
    second = await both_gates(session, text, {})
    assert second["success"] is closes
    assert purge.await_count == 1
    assert (getattr(session, "_end_call_requested", False) is True) is closes


def test_courtesy_check_is_bounded_before_the_regex_runs():
    from app.domain.services.caller_assertions import courtesy_only
    long_turn = "no thanks " * 400
    started = time.perf_counter()
    assert courtesy_only(long_turn) is False
    assert courtesy_only("okay thanks " * 4 + "no") is False  # nine words
    assert time.perf_counter() - started < 0.2


# ------------------------------------------------ N4: the realtime bridge's empty-text re-run
@pytest.mark.asyncio
async def test_bridge_style_rerun_with_empty_text_and_a_stored_opt_out_is_refused(purge):
    session = _session(_caller_opted_out=True)
    result = await run_voice_action(session, "end_call", user_text="")
    assert result["success"] is False
    assert getattr(session, "_end_call_requested", False) is not True


# ------------------------------------------------ N9: never claim "recorded" when the write failed
@pytest.mark.asyncio
async def test_a_failed_write_is_not_called_recorded(monkeypatch):
    monkeypatch.setattr(
        "app.domain.services.dialer.opt_out.purge_opt_out_before_farewell", AsyncMock(return_value=False))
    session = _session()
    result = await run_voice_action(
        session, "end_call", {"do_not_call": True},
        user_text="Do not call me again, but what are your opening hours?")
    assert result["success"] is False
    assert "recorded" not in result["message"]
    assert "asked not to be called again" in result["message"]
    assert "make no offer and do not continue the campaign" in result["message"]
    assert result["opt_out"] == "unconfirmed"


# ------------------------------------------------ the fact stays visible on later turns
@pytest.mark.asyncio
async def test_live_state_keeps_the_fact_and_the_conduct_without_telling_it_to_hang_up(purge):
    session = _session()
    await run_voice_action(
        session, "end_call", {"do_not_call": True, "opt_out_quote": "never bother this number again"},
        user_text="Never bother this number again, but can you tell me your address?")
    block = render_live_state_block(reduce_cascaded_session_live_state(session))
    assert "caller_opted_out=yes" in block
    assert "The caller has opted out of future calls." in block
    assert "make no offer" in block and "do not continue the campaign" in block
    assert "end the call" not in block


def test_live_state_has_no_fact_without_an_opt_out():
    block = render_live_state_block(reduce_cascaded_session_live_state(_session()))
    assert "caller_opted_out" not in block


# ------------------------------------------------ no opt-out: behaves as before
@pytest.mark.asyncio
@pytest.mark.parametrize("text,closes", [
    ("Okay, thanks.", False),
    ("Goodbye.", True),
    ("What are your hours?", False),
    ("My account number is 55012.", False),
    ("", False),
])
async def test_a_session_without_an_opt_out_is_unchanged(purge, text, closes):
    session = _session()
    result = await both_gates(session, text, {})
    assert result["success"] is closes
    assert purge.await_count == 0
