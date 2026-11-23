"""Opt-out wordings: the corpus, the guards and every consumer (24 Nov 2026).

Data under tests/unit/data/opt_out_wordings/ (copies of the design's sentence files):
must_catch.txt (211 clear requests), must_not_catch.txt (342 ordinary sentences),
unclear.txt (36 rows "sentence || decision || why"), holdout.txt and holdout2.txt
(rows "C|" must be caught, "N|" must not).

Each exception list below is EXACT and comes from the design (plan.md section 0). A list is
never a way to hide a fault: the test fails when a listed sentence stops being an exception
and when a new one appears.
"""
import re
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.services.voice_pipeline import identity_disposition as idn
from app.domain.services.voice_pipeline.identity_disposition import (
    contains_dnc, dnc_assertion_position,
)

DATA = Path(__file__).resolve().parent / "data" / "opt_out_wordings"


def _read(name):
    out = []
    with open(DATA / name, encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if line and not line.startswith("#"):
                out.append(line)
    return out


MUST = _read("must_catch.txt")
MUST_NOT = _read("must_not_catch.txt")
UNCLEAR = [tuple(p.strip() for p in line.split("||")) for line in _read("unclear.txt")]
_H1 = [line.split("|", 1) for line in _read("holdout.txt")]
_H2 = [line.split("|", 1) for line in _read("holdout2.txt")]
H1_C = [s for k, s in _H1 if k == "C"]
H1_N = [s for k, s in _H1 if k == "N"]
H2_C = [s for k, s in _H2 if k == "C"]
H2_N = [s for k, s in _H2 if k == "N"]

CATCH_STILL_MISSED: list[str] = []
# The plan names five sentences; two are in must_not_catch.txt, the other three in the holdouts.
NOT_STILL_CAUGHT = [
    "Can you take me off the group email but keep me on the phone list?",
    "Do I need to unsubscribe from the app first?",
]
PLAN_FIVE = NOT_STILL_CAUGHT + [
    "Does the do not call list cover mobiles?",
    "Is there a do not call list I can join for other companies?",
    "Don't call me Sir, I'm a woman.",
]
HOLDOUT_C_MISSED: list[str] = []
HOLDOUT_N_CAUGHT = ["Does the do not call list cover mobiles?"]
HOLDOUT2_C_MISSED: list[str] = []
HOLDOUT2_N_CAUGHT = [
    "Is there a do not call list I can join for other companies?",
    "Don't call me Sir, I'm a woman.",
]
UNCLEAR_EXCEPTIONS = [
    "Don't call me sweetheart, it's patronising.",
    "I'm registered with the TPS.",
    "Is my number on your do not call list?",
    "If you call again I'll report you.",
    "Enough calls.",
]


def _diff(expected, actual):
    return (f"no longer an exception (remove from list): {[s for s in expected if s not in actual]}; "
            f"new exceptions: {[s for s in actual if s not in expected]}")


def test_data_files_are_complete():
    assert len(MUST) == 211
    assert len(MUST_NOT) == 342
    assert len(UNCLEAR) == 36
    assert len(_H1) == 80 and len(H1_C) == 40 and len(H1_N) == 40
    assert len(_H2) == 60 and len(H2_C) == 30 and len(H2_N) == 30
    assert len(set(MUST)) == len(MUST)
    assert len(set(MUST_NOT)) == len(MUST_NOT)


def test_list_sizes():
    assert len(PLAN_FIVE) == 5 and len(UNCLEAR_EXCEPTIONS) == 5
    assert sorted(PLAN_FIVE) == sorted(NOT_STILL_CAUGHT + HOLDOUT_N_CAUGHT + HOLDOUT2_N_CAUGHT)
    assert CATCH_STILL_MISSED == [] and HOLDOUT_C_MISSED == [] and HOLDOUT2_C_MISSED == []


def test_every_clear_request_is_caught():
    actual = [s for s in MUST if not contains_dnc(s)]
    assert sorted(actual) == sorted(CATCH_STILL_MISSED), _diff(CATCH_STILL_MISSED, actual)


def test_ordinary_sentences_are_not_caught_except_the_listed():
    actual = [s for s in MUST_NOT if contains_dnc(s)]
    assert sorted(actual) == sorted(NOT_STILL_CAUGHT), _diff(NOT_STILL_CAUGHT, actual)
    assert set(NOT_STILL_CAUGHT) <= set(MUST_NOT)


def test_unclear_rows_follow_the_designers_decisions_except_the_five():
    actual = [s for s, d, _ in UNCLEAR if contains_dnc(s) != (d == "catch")]
    assert sorted(actual) == sorted(UNCLEAR_EXCEPTIONS), _diff(UNCLEAR_EXCEPTIONS, actual)


def test_holdouts():
    assert sorted(s for s in H1_C if not contains_dnc(s)) == HOLDOUT_C_MISSED
    assert sorted(s for s in H1_N if contains_dnc(s)) == sorted(HOLDOUT_N_CAUGHT)
    assert sorted(s for s in H2_C if not contains_dnc(s)) == HOLDOUT2_C_MISSED
    assert sorted(s for s in H2_N if contains_dnc(s)) == sorted(HOLDOUT2_N_CAUGHT)


# --------------------------------------------------------------------------- guards

def test_today_phrase_list_is_kept_entry_for_entry():
    for phrase in ("stop calling", "dont call me", "take me off", "never contact", "fuck you", "do not call list"):
        assert phrase in idn._DNC_PHRASES
    assert len(idn._DNC_PHRASES) == 51


def test_prepare_has_the_same_length():
    for text in ("Do-not-call me.", "why do you keep calling me stop calling me", "x" * 50, "", "a-b c‐d"):
        assert len(idn._prepare(text)) == len(text)


@pytest.mark.parametrize("text,needle", [
    ("why do you keep calling me stop calling me", "stop calling me"),
    ("Hello there, I have already told you guys stop calling me", "stop calling me"),
    ("I am telling you stop calling me", "stop calling me"),
    ("Put me on the do-not-call list.", "Put me on the do-not-call list."),
    ("Please do-not-call this number again", "do-not-call this number again"),
])
def test_position_is_the_index_in_the_original_text(text, needle):
    # the offset points into the ORIGINAL text (run-on and hyphen cases), not a shifted copy
    assert dnc_assertion_position(text) == text.index(needle)


@pytest.mark.parametrize("text", [
    # time / place limits (stage 3)
    "Don't call me on weekends.",
    "Don't call me at night.",
    "Don't call me during lunch.",
    "Don't call me next week.",
    "Don't call me now, call me later.",
    # recording choice (stage 4)
    "Can I opt out of the recording?",
    # habit statement by a subject (stage 4)
    "My kids don't call anymore.",
    "I quit calling them.",
    "I'll remove this number from my contacts.",
    # third party (stage 4)
    "I told them to stop calling.",
    "My husband asked them to remove our number.",
    # naming (stage 5)
    "Stop calling it a discount.",
    "Don't call me Mrs Jones.",
    "Stop calling me that.",
    # take me off hold, never call me back, lose my number (stage 5)
    "Take me off hold.",
    "Take me off speaker please.",
    "You never call me back.",
    "Did you lose my number?",
])
def test_each_guard_leaves_an_ordinary_sentence_alone(text):
    assert contains_dnc(text) is False


@pytest.mark.parametrize("text", [
    "Stop calling me.",
    "Don't call me again.",
    "Please never call me again.",
    "Take me off your list.",
    "Remove my number.",
    "Don't ever call me again, you lose my number.",
    "Put me on the do-not-call list.",
    "I want to opt out of your calls.",
    "Stop calling me right now.",
    "Quit calling.",
])
def test_requests_next_to_the_guards_are_still_caught(text):
    assert contains_dnc(text) is True


def test_a_later_request_still_wins_over_an_earlier_guarded_phrase():
    assert contains_dnc("Don't call me tomorrow. Please stop calling me.") is True


# --------------------------------------------------------------------------- safety (wide margins)

def _best_of_three(fn):
    best = 1e9
    for _ in range(3):
        t0 = time.perf_counter()
        fn()
        best = min(best, time.perf_counter() - t0)
    return best


def test_a_50000_character_transcript_is_fast():
    chunk = "Hello there, I am just asking about the price of the plan and the delivery. "
    text = (chunk * (50000 // len(chunk) + 1))[:50000] + " Stop calling me."
    assert len(text) > 50000
    text = text[-50000:]
    assert len(text) == 50000
    assert _best_of_three(lambda: dnc_assertion_position(text)) < 2.0


@pytest.mark.parametrize("word", [
    "stop", "calling", "never", "dont", "do not", "remove", "me", "my number", "take", "get", "want",
    "to be", "no more", "calls", "opt out", "of", "your", "from", "list", "off", "told", "you people",
    "again", "please", "call", "ever", " ", "-",
])
def test_hostile_inputs_return_fast(word):
    text = (word + " ") * (5000 // (len(word) + 1))
    assert _best_of_three(lambda: dnc_assertion_position(text)) < 2.0
    spaces = " " * 5000
    assert _best_of_three(lambda: dnc_assertion_position(spaces)) < 2.0


# --------------------------------------------------------------------------- consumers
ROWS_YES = ["Quit calling.", "Remove my number.", "why do you keep calling me stop calling me",
            "Put me on the do-not-call list."]
ROW_NO = "Don't call me after six."


@pytest.mark.asyncio
@pytest.mark.parametrize("words", ROWS_YES)
async def test_consumer_1_2_turn_end_records_the_opt_out_before_the_model(monkeypatch, words):
    from app.domain.services.dialer import opt_out
    from tests.unit.test_voice_pipeline_service import _make_service_for_disposition, _make_session

    monkeypatch.setenv("TELEPHONY_FILLER_DELAY_MS", "0")
    purge = AsyncMock(return_value=True)
    monkeypatch.setattr(opt_out, "purge_opt_out_before_farewell", purge)
    service = _make_service_for_disposition(["Understood."])
    session = _make_session()
    session.campaign_id = "campaign-123"
    session.current_user_input = words
    await service.handle_turn_end(session, AsyncMock())
    purge.assert_awaited()
    assert session._caller_opted_out is True


@pytest.mark.asyncio
async def test_consumer_1_2_scheduling_sentence_is_not_recorded(monkeypatch):
    from app.domain.services.dialer import opt_out
    from tests.unit.test_voice_pipeline_service import _make_service_for_disposition, _make_session

    monkeypatch.setenv("TELEPHONY_FILLER_DELAY_MS", "0")
    purge = AsyncMock(return_value=True)
    monkeypatch.setattr(opt_out, "purge_opt_out_before_farewell", purge)
    service = _make_service_for_disposition(["Sure."])
    session = _make_session()
    session.campaign_id = "campaign-123"
    session.current_user_input = ROW_NO
    await service.handle_turn_end(session, AsyncMock())
    purge.assert_not_awaited()
    assert getattr(session, "_caller_opted_out", False) is False


@pytest.mark.asyncio
@pytest.mark.parametrize("words", ["Quit calling.", "Remove my number.", "Put me on the do-not-call list."])
async def test_consumer_1_2_end_of_call_follows_a_new_wording(monkeypatch, words):
    """Same contract as "stop calling me" (test_call_experience_end_scope.py:25)."""
    from app.domain.models.conversation import Message, MessageRole
    from tests.unit.test_voice_pipeline_service import _make_service_for_disposition, _make_session
    from app.domain.services.dialer import opt_out

    monkeypatch.setenv("TELEPHONY_FILLER_DELAY_MS", "0")
    monkeypatch.setattr(opt_out, "purge_opt_out_before_farewell", AsyncMock(return_value=True))
    service = _make_service_for_disposition(
        ['{"action":"end_session","reason":"user_done","farewell":"Goodbye."}'])
    service._shutdown_session_for_end_action = AsyncMock()
    session = _make_session()
    session.campaign_id = "synthetic-campaign"
    session.turn_id = 4
    session._has_introduced = True
    session.current_user_input = words
    session.conversation_history = [Message(role=MessageRole.ASSISTANT, content="Would you like details by email?")]
    await service.handle_turn_end(session, AsyncMock())
    assert service._shutdown_session_for_end_action.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("words", ["Quit calling.", "Remove my number.", "Put me on the do-not-call list."])
async def test_consumer_3_a_low_confidence_first_turn_is_kept(words):
    from tests.unit.test_voice_pipeline_service import _make_service_for_disposition, _make_session

    service = _make_service_for_disposition(["Understood."])
    session = _make_session()
    session.campaign_id = "campaign-123"
    session.current_user_input = words
    session._last_transcript_confidence = 0.2
    await service.handle_turn_end(session, AsyncMock())
    assert getattr(session, "_caller_opted_out", False) is True


@pytest.mark.asyncio
@pytest.mark.parametrize("words", ["Quit calling.", "Remove my number.", "why do you keep calling me stop calling me",
                                   "Put me on the do-not-call list."])
async def test_consumer_6_7_partial_at_hangup_is_purged(monkeypatch, words):
    from tests.unit.test_opt_out_never_missed_20261120 import _teardown

    purge, cs = await _teardown(monkeypatch, current_user_input=words)
    assert purge.await_count == 1
    assert cs._caller_opted_out is True


@pytest.mark.asyncio
@pytest.mark.parametrize("words", ["Quit calling.", "Remove my number.", "Put me on the do-not-call list."])
async def test_consumer_6_7_queued_turn_at_hangup_is_purged(monkeypatch, words):
    from tests.unit.test_opt_out_never_missed_20261120 import _teardown

    purge, _ = await _teardown(monkeypatch, current_user_input="",
                               queued={"text": words, "latest_text": words, "seq": 2})
    assert purge.await_count == 1


@pytest.mark.asyncio
async def test_consumer_6_7_scheduling_sentence_at_hangup_is_not_purged(monkeypatch):
    from tests.unit.test_opt_out_never_missed_20261120 import _teardown

    purge, _ = await _teardown(monkeypatch, current_user_input=ROW_NO)
    assert purge.await_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("words", ["Quit calling.", "Remove my number.", "Put me on the do-not-call list."])
async def test_consumer_8_end_call_tool_with_the_flag_and_no_quote_records_the_opt_out(monkeypatch, words):
    from app.domain.services.voice_pipeline.action_tools import run_voice_action

    purge = AsyncMock(return_value=True)
    monkeypatch.setattr("app.domain.services.dialer.opt_out.purge_opt_out_before_farewell", purge)
    session = SimpleNamespace(conversation_history=[])
    result = await run_voice_action(session, "end_call", {"do_not_call": True}, user_text=words)
    assert result["success"] is True
    assert result["opt_out"] == "recorded"
    purge.assert_awaited_once()
    assert session._end_call_requested is True


@pytest.mark.asyncio
async def test_consumer_8_a_scheduling_sentence_with_the_flag_is_refused(monkeypatch):
    from app.domain.services.voice_pipeline.action_tools import run_voice_action

    purge = AsyncMock(return_value=True)
    monkeypatch.setattr("app.domain.services.dialer.opt_out.purge_opt_out_before_farewell", purge)
    session = SimpleNamespace(conversation_history=[])
    result = await run_voice_action(session, "end_call", {"do_not_call": True}, user_text=ROW_NO)
    assert result["success"] is False
    purge.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("words", ["No more calls.", "Quit calling.", "Remove my number."])
async def test_consumer_11_empty_model_reply_after_a_new_wording_says_goodbye_and_ends(monkeypatch, words):
    from tests.unit.test_refused_ending_is_honest_20261124 import _PlainLLM, _turn

    monkeypatch.setenv("TELEPHONY_FILLER_DELAY_MS", "0")
    spoken, session = await _turn(_PlainLLM([]), words)
    assert spoken == ["Goodbye."]
    assert session._end_call_requested is True


@pytest.mark.asyncio
async def test_consumer_11_empty_model_reply_after_a_scheduling_sentence_does_not_end(monkeypatch):
    from tests.unit.test_refused_ending_is_honest_20261124 import _PlainLLM, _turn

    monkeypatch.setenv("TELEPHONY_FILLER_DELAY_MS", "0")
    spoken, session = await _turn(_PlainLLM([]), ROW_NO)
    assert spoken != ["Goodbye."]
    assert not getattr(session, "_end_call_requested", False)


@pytest.mark.asyncio
@pytest.mark.parametrize("words", ["Quit calling.", "Remove my number.", "why do you keep calling me stop calling me",
                                   "Put me on the do-not-call list."])
async def test_consumer_12_13_realtime_records_and_arms_the_end(words):
    from tests.unit.test_realtime_end_call_ownership import _end_tool, _events, _fixture
    from app.realtime.openai import RealtimeEvent

    bridge, provider, _, ended, session = _fixture()
    await _events(bridge, provider, RealtimeEvent(kind="caller_transcript", text=words, is_final=True))
    assert session._caller_opted_out is True
    await _end_tool(bridge)
    bridge._goodbye_completed.set()
    if bridge._termination_task:
        await bridge._termination_task
    assert ended.await_count == 1
    assert session._end_call_requested is True
    await bridge.stop()


@pytest.mark.asyncio
async def test_consumer_12_13_realtime_scheduling_sentence_is_not_an_opt_out():
    from tests.unit.test_realtime_end_call_ownership import _end_tool, _events, _fixture
    from app.realtime.openai import RealtimeEvent

    bridge, provider, _, ended, session = _fixture()
    await _events(bridge, provider, RealtimeEvent(kind="caller_transcript", text=ROW_NO, is_final=True))
    assert getattr(session, "_caller_opted_out", False) is False
    await _end_tool(bridge)
    ended.assert_not_awaited()
    await bridge.stop()


def test_the_pattern_text_ors_the_grammar_after_the_unchanged_phrase_list():
    from app.domain.services.caller_assertions import phrase_pattern

    assert idn._DNC_PATTERN.pattern.startswith(phrase_pattern(idn._DNC_PHRASES).pattern + "|")
    assert len(idn._DNC_FORMS) == 19
    assert re.compile(idn._DNC_PATTERN.pattern, re.I)


# Found by the lead's own check of the build (24 Nov 2026). The habit-statement guard treats
# "people" before the verb as somebody else ("people stop calling after a while"). In "you
# people" it is the company being addressed: the commonest angry wording of a request to stop.
# Seven of these were caught before this work and skipped by the first build.
YOU_PEOPLE_REQUESTS = [
    "I have already told you people stop calling me",
    "You people stop calling me",
    "you people stop calling this number",
    "Can you people stop calling me please",
    "Will you people stop ringing me",
    "I wish you people would stop calling me",
    "you people never call me again",
    "You people remove my number",
    "You people need to stop calling me",
    "I have already told you guys stop calling me",
    "I want you people to take me off your list",
]
OTHER_PEOPLES_HABITS = [
    "people stop calling me when I tell them to",
    "my kids don't call anymore",
    "They stop calling after six",
    "most people would stop calling after the first no",
]


@pytest.mark.parametrize("sentence", YOU_PEOPLE_REQUESTS)
def test_a_request_addressed_to_you_people_is_caught(sentence):
    assert idn.dnc_assertion_position(sentence) >= 0


@pytest.mark.parametrize("sentence", OTHER_PEOPLES_HABITS)
def test_a_statement_about_other_peoples_habits_is_not_caught(sentence):
    assert idn.dnc_assertion_position(sentence) < 0
