"""A bare "No" ends the call only right after the agent's own "anything else?" question (24 Nov 2026)."""
import re
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.models.conversation import Message, MessageRole
from app.domain.services import end_session_action as es
from app.domain.services.end_session_action import caller_signaled_end

QUALIFY = [
    "Is there anything else I can help with?",
    "No problem. Is there anything else I can help with?",
    "Anything else?",
    "Great. Is there anything else I can help you with today?",
    "Can I help you with anything else?",
    "Is there anything else you need?",
]
NOT_QUALIFY = [
    "Would you like details by email?",
    "Is now a good time to talk?",
    "Got a minute?",
    "Is there anything else I can help with? Have a great day.",
    "Is there anything else I can help with, or shall I send the details?",
    "Anything else on your desk you're pricing up?",
    "Is there anything else I can help with [interrupted by caller]",
    "Sorry, I didn't quite catch that — could you say it again?",
]
END = [
    "No", "No.", "No!", "Nope", "Nope.", "Nah", "No no", "No, no.", "No, I'm good.", "No I'm good",
    "I'm good.", "I'm fine", "No, I'm fine, thanks.", "I'm all good", "All good.", "No, all good, thanks.",
    "No, we're good.", "Not really.", "No, not really.", "Nothing.", "No, nothing.", "No, I don't think so.",
    "I don't think so.", "No, that's fine.", "No that's everything", "Not right now.", "No, cheers.",
    "Nope, have a good day.", "No, thanks, mate.", "no im ok", "No, I'm alright.", "Nah I'm good thanks",
    "no thats great thank you", "No thank you, bye.",
]
STAY = [
    "No?", "No, wait.", "No, wait, one more thing.", "No, actually I have a question.", "No, I mean the other one.",
    "No, but can you send me the details?", "No... hold on.", "No, what about the price?",
    "No, who did you say you were?", "No, not yet.", "No, I'm not sure.", "No, I'm good, but what about the fee?",
    "No. Why?", "Nope, oh wait, one thing.", "I'm good, how about you?", "No I need to think",
    "No, I've got another question.", "No, hang on.", "No, can you repeat that?", "Yes", "Yes, one thing",
    "No, tell me more about the contract", "No not really but", "No, that's not what I asked", "No, I said Tuesday",
    "No, I'm fine with the price but not the term", "No, go on", "No, I want to book", "Nope, you didn't answer me",
    "No, sorry, say that again",
]


def test_lists_have_the_designed_sizes():
    assert len(END) == 34 and len(STAY) == 30
    assert len(QUALIFY) == 6 and len(NOT_QUALIFY) == 8


def test_the_recomposed_pattern_text_equals_todays():
    today = (
        r"(?:is there |do you need |can (?:i|we) help (?:you )?with )?anything else"
        r"(?: (?:i|we) can (?:help(?: you)?(?: with)?|do(?: for you)?)| you (?:need|want to (?:ask|discuss)))?"
        r"(?: today| before (?:we|you) go)?|"
        r"(?:do you have|have you got|got) (?:a|one|a quick) (?:minute|moment)(?: to (?:talk|chat))?|"
        r"is (?:now|this) (?:a )?good time(?: to (?:talk|chat))?"
    )
    assert es._NEGATIVE_REPLY_ENDS_CALL.pattern == today
    assert es._NEGATIVE_REPLY_ENDS_CALL.flags & re.I
    assert today.startswith(es._ANYTHING_ELSE_QUESTION.pattern + "|")


@pytest.mark.parametrize("prev", QUALIFY)
def test_a_bare_negative_after_anything_else_ends(prev):
    assert [r for r in END if not caller_signaled_end(r, previous_assistant_text=prev)] == []


@pytest.mark.parametrize("prev", QUALIFY)
def test_replies_that_keep_the_call_open_stay_open_after_anything_else(prev):
    assert [r for r in STAY if caller_signaled_end(r, previous_assistant_text=prev)] == []


@pytest.mark.parametrize("prev", NOT_QUALIFY)
def test_a_previous_line_that_does_not_qualify_changes_nothing(prev):
    # Only the two replies that already ended before this change may end here.
    ended = sorted(r for r in END + STAY if caller_signaled_end(r, previous_assistant_text=prev))
    assert set(ended) <= {"No, thanks, mate.", "No thank you, bye."}, ended
    for r in ("No", "No.", "Nope", "No, I'm good.", "I'm fine"):
        assert not caller_signaled_end(r, previous_assistant_text=prev)


def test_with_no_previous_line_a_bare_no_does_not_end():
    assert not caller_signaled_end("No.")
    assert not caller_signaled_end("No.", previous_assistant_text=None)
    assert not caller_signaled_end("No.", previous_assistant_text="")


def test_ends_with_anything_else_helper():
    assert es._ends_with_anything_else("Is there anything else I can help with?")
    assert es._ends_with_anything_else('"Is there anything else I can help with?"')
    assert es._ends_with_anything_else("Great. Is there anything else I can help with?")
    assert not es._ends_with_anything_else("Is there anything else I can help with")
    assert not es._ends_with_anything_else("Is there anything else I can help with? Have a great day.")
    assert not es._ends_with_anything_else(None)
    assert not es._ends_with_anything_else("Got a minute?")
    assert not es._ends_with_anything_else("Is now a good time to talk?")


def test_the_bare_negative_pattern_is_fast_on_long_input():
    prev = QUALIFY[0]
    for text in ("no " * 200, "no thanks " * 60, "no, " * 300 + "x", "a" * 600, " " * 600):
        t0 = time.perf_counter()
        caller_signaled_end(text, previous_assistant_text=prev)
        assert time.perf_counter() - t0 < 2.0


# --- the three harnesses of test_call_experience_end_scope.py, with the plan's rows
CASES = [
    ("Is there anything else I can help with?", "No.", True),
    ("Is there anything else I can help with?", "No?", False),
    ("Is there anything else I can help with?", "No, wait.", False),
    ("Would you like details by email?", "No.", False),
]


def _history(prior, caller):
    return [Message(role=MessageRole.ASSISTANT, content=prior), Message(role=MessageRole.USER, content=caller)]


@pytest.mark.parametrize("prior,caller,ends", CASES)
@pytest.mark.asyncio
async def test_action_admission_end_call(prior, caller, ends):
    from app.domain.services.voice_pipeline.action_tools import run_voice_action

    history = _history(prior, caller)
    session = SimpleNamespace(conversation_history=history + [Message(role=MessageRole.ASSISTANT, content="Goodbye.")])
    result = await run_voice_action(session, "end_call", {}, user_text=caller)
    assert result["success"] is ends
    assert bool(getattr(session, "_end_call_requested", False)) is ends


@pytest.mark.parametrize("prior,caller,ends", CASES)
@pytest.mark.parametrize("reply", [
    '{"action":"end_session","reason":"user_done","farewell":"Goodbye."}',
    "Understood. [[END_CALL]]",
])
@pytest.mark.asyncio
async def test_turn_runner_and_finisher(monkeypatch, prior, caller, ends, reply):
    from app.services.scripts.call_state_tracker import CallState
    from tests.unit.test_voice_pipeline_service import _make_service_for_disposition, _make_session

    monkeypatch.setenv("TELEPHONY_FILLER_DELAY_MS", "0")
    service = _make_service_for_disposition([reply])
    service._shutdown_session_for_end_action = AsyncMock()
    session = _make_session()
    session.campaign_id = "synthetic-campaign"
    session.turn_id = 4
    session._has_introduced = True
    session.current_user_input = caller
    session.conversation_history = _history(prior, caller)[:-1]
    session.captured_slots = CallState(declined_count=3)
    await service.handle_turn_end(session, AsyncMock())
    assert bool(service._shutdown_session_for_end_action.await_count) is ends
    if not ends:
        assert not getattr(session, "_end_call_requested", False)


@pytest.mark.parametrize("prior,caller,ends", CASES)
@pytest.mark.asyncio
async def test_realtime_tool(prior, caller, ends):
    from app.realtime.openai import RealtimeEvent
    from tests.unit.test_realtime_end_call_ownership import _end_tool, _events, _fixture

    bridge, provider, _, ended, session = _fixture()
    bridge._remember_contact_turn("assistant", prior)
    await _events(bridge, provider, RealtimeEvent(kind="caller_transcript", text=caller, is_final=True))
    bridge._remember_contact_turn("assistant", "Goodbye.")
    await _end_tool(bridge)
    bridge._goodbye_completed.set()
    if bridge._termination_task:
        await bridge._termination_task
    assert bool(ended.await_count) is ends
    assert bool(getattr(session, "_end_call_requested", False)) is ends
    await bridge.stop()


# --- zero-token turn
@pytest.mark.asyncio
async def test_zero_token_turn_after_anything_else_and_no_says_goodbye_and_ends(monkeypatch):
    from tests.unit.test_refused_ending_is_honest_20261124 import _PlainLLM, _turn
    from app.domain.models.session import CallSession
    import asyncio

    monkeypatch.setenv("TELEPHONY_FILLER_DELAY_MS", "0")
    session = CallSession(call_id="call-bare-no-1", campaign_id="c", lead_id="l",
                          provider_call_id="p", system_prompt="Plain.", voice_id="v")
    session.barge_in_event = asyncio.Event()
    session.conversation_history.append(
        Message(role=MessageRole.ASSISTANT, content="Is there anything else I can help with?"))
    spoken, session = await _turn(_PlainLLM([]), "No.", session)
    assert spoken == ["Goodbye."]
    assert session._end_call_requested is True


@pytest.mark.asyncio
async def test_zero_token_turn_after_an_email_offer_and_no_does_not_say_goodbye(monkeypatch):
    from tests.unit.test_refused_ending_is_honest_20261124 import _PlainLLM, _turn
    from app.domain.models.session import CallSession
    import asyncio

    monkeypatch.setenv("TELEPHONY_FILLER_DELAY_MS", "0")
    session = CallSession(call_id="call-bare-no-2", campaign_id="c", lead_id="l",
                          provider_call_id="p", system_prompt="Plain.", voice_id="v")
    session.barge_in_event = asyncio.Event()
    session.conversation_history.append(
        Message(role=MessageRole.ASSISTANT, content="Would you like details by email?"))
    spoken, session = await _turn(_PlainLLM([]), "No.", session)
    assert spoken != ["Goodbye."]
    assert not getattr(session, "_end_call_requested", False)
