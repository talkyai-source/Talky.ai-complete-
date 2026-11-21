"""Review fixes for the opt-out / goodbye changes (2026-11-21).

C1 a refused close after an opt-out tells the model the opt-out is recorded and
   the fact stays in the live-state block on later turns.
C2 hostile or rhetorical questions do not keep an opt-out call open.
C3 a stored opt-out authorises the agent's close on later turns (tool path).
D1 "bye-bye" counts only as a standalone farewell (hyphen and space alike).
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.services.voice_pipeline.action_tools import run_voice_action
from app.domain.services.voice_pipeline.end_call import model_end_call_allowed
from app.domain.services.voice_pipeline.identity_disposition import contains_explicit_goodbye
from app.domain.services.voice_pipeline.live_structured_state import (
    reduce_cascaded_session_live_state, render_live_state_block,
)

REQUEST = "Do not call me again, but I need help with my account."
HOSTILE = [
    "Stop calling me. Why do you keep calling?",
    "How many times do I have to tell you, stop calling me!",
]
# "How many times ..." is not caught by the phrase floor (its "how" prefix reads as
# a meta question), so the opt-out reaches the agent as the model's verified quote.
QUOTE = {"Stop calling me. Why do you keep calling?": "stop calling me",
         "How many times do I have to tell you, stop calling me!": "stop calling me"}
KEEP_OPEN = [
    "Don't call me again, but I need help with my account.",
    "Do not call me again, but first, what are your opening hours?",
]
RECORDED_SENTENCE = (
    "The caller's opt-out is recorded. Answer only what the caller just asked, in one sentence; "
    "make no offer and do not continue the campaign; then end the call."
)


def _session(**flags):
    return SimpleNamespace(conversation_history=[], **flags)


@pytest.fixture
def purge(monkeypatch):
    mock = AsyncMock(return_value=True)
    monkeypatch.setattr("app.domain.services.dialer.opt_out.purge_opt_out_before_farewell", mock)
    return mock


# ------------------------------------------------------------------ C1
@pytest.mark.asyncio
async def test_c1_refused_close_after_opt_out_tells_the_model(purge):
    session = _session()
    result = await run_voice_action(session, "end_call", {"do_not_call": True}, user_text=REQUEST)
    assert result["success"] is False
    assert RECORDED_SENTENCE in result["message"]
    assert result["opt_out"] == "recorded"


@pytest.mark.asyncio
async def test_c1_live_state_keeps_the_fact_on_the_next_turn_phrase_path(purge):
    session = _session()
    await run_voice_action(session, "end_call", {"do_not_call": True}, user_text=REQUEST)
    block = render_live_state_block(reduce_cascaded_session_live_state(session))
    assert "caller_opted_out=yes" in block


@pytest.mark.asyncio
async def test_c1_live_state_fact_when_opt_out_came_only_from_the_model_quote(purge):
    text = "Never bother this number again, but can you tell me your address?"
    session = _session()
    args = {"do_not_call": True, "opt_out_quote": "never bother this number again"}
    result = await run_voice_action(session, "end_call", args, user_text=text)
    assert result["success"] is False
    assert RECORDED_SENTENCE in result["message"]
    block = render_live_state_block(reduce_cascaded_session_live_state(session))
    assert "caller_opted_out=yes" in block


def test_c1_live_state_has_no_fact_without_an_opt_out():
    block = render_live_state_block(reduce_cascaded_session_live_state(_session()))
    assert "caller_opted_out" not in block


# ------------------------------------------------------------------ C2
@pytest.mark.asyncio
@pytest.mark.parametrize("text", HOSTILE)
async def test_c2_hostile_opt_out_closes_on_the_tool_path(purge, text):
    session = _session()
    args = {"do_not_call": True, "opt_out_quote": QUOTE[text]}
    result = await run_voice_action(session, "end_call", args, user_text=text)
    assert purge.await_count == 1
    assert result["success"] is True
    assert session._end_call_requested is True


@pytest.mark.parametrize("text", HOSTILE[:1])
def test_c2_hostile_opt_out_closes_on_the_marker_path(text):
    assert model_end_call_allowed(_session(_caller_opted_out=True), text) is True


@pytest.mark.asyncio
@pytest.mark.parametrize("text", KEEP_OPEN)
async def test_c2_real_request_after_the_opt_out_keeps_the_call_open(purge, text):
    session = _session()
    result = await run_voice_action(session, "end_call", {"do_not_call": True}, user_text=text)
    assert purge.await_count == 1
    assert result["success"] is False
    assert getattr(session, "_end_call_requested", False) is not True
    assert model_end_call_allowed(_session(_caller_opted_out=True), text) is False


@pytest.mark.asyncio
async def test_c2_question_word_before_the_opt_out_does_not_keep_it_open(purge):
    text = "What is this about? Stop calling me."
    session = _session()
    result = await run_voice_action(session, "end_call", {"do_not_call": True}, user_text=text)
    assert result["success"] is True
    assert model_end_call_allowed(_session(_caller_opted_out=True), text) is True


# ------------------------------------------------------------------ C3
@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["Okay.", "Hello?"])
async def test_c3_stored_opt_out_authorises_the_close_on_a_later_turn(purge, text):
    session = _session(_caller_opted_out=True)
    result = await run_voice_action(session, "end_call", {}, user_text=text)
    assert result["success"] is True
    assert session._end_call_requested is True


@pytest.mark.asyncio
async def test_c3_stored_opt_out_still_refuses_on_a_new_request(purge):
    session = _session(_caller_opted_out=True)
    result = await run_voice_action(
        session, "end_call", {}, user_text="Actually, can you tell me your address?")
    assert result["success"] is False
    assert getattr(session, "_end_call_requested", False) is not True


@pytest.mark.asyncio
async def test_c3_no_opt_out_still_needs_the_caller_to_end_it(purge):
    session = _session()
    result = await run_voice_action(session, "end_call", {}, user_text="Okay.")
    assert result["success"] is False


# ------------------------------------------------------------------ D1
NOT_A_FAREWELL = [
    "We said bye-bye to BT in March, so what do you offer?",
    "No, we said bye-bye to them last year.",
    "I work at Bye-Bye Baby.",
    "Say bye-bye to grandma",
    "Hang on. Bye-bye darling. Sorry, go on.",
]
FAREWELL = ["Bye-bye.", "bye-bye", "Okay, bye-bye then.", "Bye-bye, thanks.", "Bye, bye."]


@pytest.mark.parametrize("text", NOT_A_FAREWELL)
def test_d1_sentences_that_merely_contain_bye_bye_are_not_a_goodbye(text):
    assert contains_explicit_goodbye(text) is False
    assert contains_explicit_goodbye(text.replace("-", " ")) is False


@pytest.mark.parametrize("text", FAREWELL)
def test_d1_standalone_farewells_still_count(text):
    assert contains_explicit_goodbye(text) is True
    assert contains_explicit_goodbye(text.replace("-", " ")) is True
