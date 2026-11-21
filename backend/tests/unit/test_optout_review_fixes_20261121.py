"""Opt-out followed by a request (2026-11-21), after four independent reviews.

Acceptance rule (asymmetric):
 (a) an opt-out is always recorded, at most once per call;
 (b) after an opt-out the DEFAULT is to close, as main does; the call stays open
     only when the words after the opt-out match a tight request pattern
     (KEEP_OPEN_REAL_REQUESTS below); when unsure: close;
 (c) a later turn after a stored opt-out closes only on the caller's own end signal
     or a turn of nothing but courtesy words.

Every case runs through the real functions in the order the traditional pipeline
uses them: the turn finisher's phrase flag + write, the model's end_call tool
(run_voice_action), the finisher's fixed-rule close (fixed_rule_close), then the
end-of-turn gate (model_end_call_allowed).
"""
import time
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.services.voice_pipeline.action_tools import run_voice_action
from app.domain.services.voice_pipeline.end_call import fixed_rule_close, model_end_call_allowed
from app.domain.services.voice_pipeline.identity_disposition import contains_dnc
from app.domain.services.voice_pipeline.live_structured_state import (
    OPT_OUT_RULE, reduce_cascaded_session_live_state, render_live_state_block,
)

PREV = "Hi, this is Sarah from Acme. Do you have a minute?"


class FakePurge:
    """Stands in for the do-not-call write; idempotent per call like the real one."""

    def __init__(self, ok=True):
        self.ok, self.calls, self.effects = ok, 0, 0

    async def __call__(self, session, *, timeout_s=2.5):
        self.calls += 1
        if getattr(session, "_opt_out_inline_failed", False) is True:
            return False
        if not self.ok:
            session._opt_out_inline_failed = True
            return False
        if not getattr(session, "_opt_out_purged", False):
            self.effects += 1
            session._opt_out_purged = True
        session._opt_out_recorded = True
        return True


@pytest.fixture
def purge(monkeypatch):
    fake = FakePurge()
    monkeypatch.setattr("app.domain.services.dialer.opt_out.purge_opt_out_before_farewell", fake)
    return fake


def msg(role, content):
    return SimpleNamespace(role=role, content=content)


def new_session(**flags):
    return SimpleNamespace(conversation_history=[], call_id="unit-call-0001", **flags)


async def turn(session, text, args=None, prev=PREV, model_calls_end=True):
    """One caller turn through the real gates, in pipeline order."""
    if prev is not None:
        session.conversation_history.append(msg("assistant", prev))
    session.conversation_history.append(msg("user", text))
    if contains_dnc(text):  # turn_ender: the phrase floor flags and writes first
        session._caller_opted_out = True
        await __import__("app.domain.services.dialer.opt_out", fromlist=["x"]).purge_opt_out_before_farewell(session)
    session._end_call_requested = False
    tool = message = None
    if model_calls_end:
        result = await run_voice_action(session, "end_call", dict(args or {}), user_text=text)
        tool, message = result["success"], result["message"]
    if fixed_rule_close(session, text):  # turn_ender: the finisher's own close
        session._end_call_requested = True
    gate = model_end_call_allowed(session, text)
    hung = session._end_call_requested is True and gate
    session.conversation_history.append(msg("assistant", "(reply)"))
    if tool:
        assert gate, f"tool gate accepted but the end-of-turn gate refuses: {text!r}"
    return SimpleNamespace(tool=tool, gate=gate, hung=hung, message=message)


def dnc(quote=None):
    return {"do_not_call": True, **({"opt_out_quote": quote} if quote else {})}


# ------------------------------------------------------------------ first-turn corpus
# (sentence, the model's quote when the phrase list does not catch it, else None)
CLOSES = [
    # plain
    ("Stop calling me.", None), ("Do not call me again.", None), ("Take me off your list please.", None),
    ("Never bother this number again.", "never bother this number again"),
    ("I don't want any more calls from you.", "I don't want any more calls"),
    ("Please stop ringing my mobile.", "stop ringing my mobile"),
    # hostile or rhetorical (main hung up on every one of these)
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
    ("Stop calling me, but when I say stop I mean stop!", None),
    ("Stop calling me, but how dare you ring me at dinner!", None),
    ("Stop calling me. I actually don't know how you got my number.", None),
    ("Stop calling me before I lose my temper, how hard is that?", None),
    ("Stop calling me. Also, when I say no I mean no.", None),
    ("Stop calling me, I've told you before, what is it with you lot?", None),
    ("Stop calling me, but where do you people get off?", None),
    ("Stop calling me, but when will you lot learn?", None),
    ("Take me off your list, but what is wrong with you people?", None),
    ("Stop calling me, but what the hell is wrong with you?", None),
    ("Take me off your list. I've asked before. How many times?", None),
    ("Remove me from your list, but how come you have my number?", None),
    ("Stop calling me, instead what you should do is email me.", None),
    ("I don't want any more calls, why do you keep doing this?", "I don't want any more calls"),
    ("Stop calling me but when will you ever learn?", None),
    ("Take me off your list. And what are you going to do about it?", None),
    ("Stop calling me. What the hell is wrong with you?", None),
    ("Do not call me again. How hard is it to understand?", None),
    ("Stop calling me, but what part of no don't you understand?", None),
    ("Take me off your list, what is it with you people?", None),
    ("Stop calling me, but why on earth do you keep doing this?", None),
    ("Don't call me again, but first answer me this: why?", None),
    # a request BEFORE the opt-out, or a goodbye after the request
    ("What are your hours? Also, stop calling me.", None),
    ("What are your hours? Actually never mind, do not call me again.", None),
    ("Can you tell me your address? And never bother this number again.", "never bother this number again"),
    ("Stop calling me. Take me off your list.", None),
    ("Stop calling me, but what are your hours? Actually just take me off your list.", None),
    ("Don't call me again. Goodbye.", None),
    ("Don't call me again, but what are your hours? Okay bye.", None),
    ("Stop calling me, but what are your hours? Actually, goodbye.", None),
]
# Every sentence on this list is a real request: main hung up on it, the branch keeps
# the call open and tells the model to answer only that. No hostile or rhetorical
# sentence may be here (test_no_hostile_sentence_is_on_the_keep_open_list).
KEEP_OPEN_REAL_REQUESTS = [
    ("Don't call me again, but I need help with my account.", None),
    ("Do not call me again, but first, what are your opening hours?", None),
    ("Do not call me again, but what are your opening hours?", None),
    ("Don't call me again. What are your opening hours?", None),
    ("Do not call me again, what are your hours?", None),
    ("Stop calling me, and also how do I get a refund?", None),
    ("Stop calling me, but I want to cancel my subscription.", None),
    ("Stop calling me, but first tell me your opening hours.", None),
    ("Remove me from your list. I want to cancel my subscription.", None),
    ("Don't call me again, but I want to make a complaint.", None),
    ("Stop calling me, but I'd like a refund.", None),
    ("Take me off your list. I'd like to speak to a manager.", None),
    ("Stop calling me, but can I get a refund?", None),
    ("Stop calling me, but could I speak to someone?", None),
    ("Stop calling me, but please send me the contract.", None),
    ("Stop calling me, but I need to update my address.", None),
    ("Take me off your list, but I need to update my address.", None),
    ("Don't call me again, but please send me the contract.", None),
    ("Don't call me again, but can I get a refund?", None),
    ("Don't call me again, but could I speak to someone?", None),
    ("Don't call me again, but I have a question.", None),
    ("Take me off your marketing list. I'm calling about my order.", None),
    ("Take me off your list, and can I book an appointment for Friday?", None),
    ("Stop calling me but can you send me your address by email?", None),
    ("Never bother this number again, but what are your hours?", "never bother this number again"),
    ("I don't want any more calls. Can you tell me your address though?", "I don't want any more calls"),
    ("I don't want any more calls, but first, where is your office?", "I don't want any more calls"),
    ("Please stop ringing my mobile, but how much is it?", "stop ringing my mobile"),
    ("Stop calling me. How did you get my number?", None),
]
HOSTILE_SAMPLE = [s for s, _ in CLOSES if any(
    w in s.lower() for w in ("why", "how dare", "how many times", "how hard", "what is it with", "wrong with",
                             "what's more", "get off", "learn", "when i say"))]


@pytest.mark.asyncio
@pytest.mark.parametrize("text,quote", CLOSES)
async def test_default_is_to_close_after_an_opt_out(purge, text, quote):
    session = new_session()
    result = await turn(session, text, dnc(quote))
    assert result.hung is True, text
    assert purge.effects == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("text,quote", KEEP_OPEN_REAL_REQUESTS)
async def test_a_real_request_after_the_opt_out_keeps_the_call_open(purge, text, quote):
    session = new_session()
    result = await turn(session, text, dnc(quote))
    assert result.hung is False and result.tool is False, text
    assert purge.effects == 1  # recorded, once
    assert "make no offer and do not continue the campaign" in result.message


@pytest.mark.asyncio
@pytest.mark.parametrize("text,quote", [r for r in KEEP_OPEN_REAL_REQUESTS if r[1] is None])
async def test_model_that_only_answers_does_not_hang_up_on_a_real_request(purge, text, quote):
    session = new_session()
    result = await turn(session, text, None, model_calls_end=False)
    assert result.hung is False, text
    assert purge.effects == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("text,quote", [r for r in CLOSES if r[1] is None])
async def test_the_turn_finisher_closes_hostile_and_plain_opt_outs_without_a_model_request(purge, text, quote):
    session = new_session()
    result = await turn(session, text, None, model_calls_end=False)
    assert result.hung is True, text


def test_no_hostile_sentence_is_on_the_keep_open_list():
    keep = {s for s, _ in KEEP_OPEN_REAL_REQUESTS}
    assert HOSTILE_SAMPLE and not keep & set(HOSTILE_SAMPLE)
    assert not keep & {s for s, _ in CLOSES}


@pytest.mark.asyncio
@pytest.mark.parametrize("text", [
    'The script says "do not call me again", but what are your hours?',
    "I am not saying do not call me again. What are your hours?",
    "Yesterday he said do not call me again. What are your hours?",
])
async def test_reported_or_negated_opt_outs_write_nothing_and_do_not_close(purge, text):
    session = new_session()
    result = await turn(session, text, dnc())
    assert result.hung is False
    assert purge.effects == 0
    assert getattr(session, "_caller_opted_out", False) is not True


# ------------------------------------------------------------------ later turns (F4, F6)
ASK_NUMBER = "Can I take your account number so I can look that up?"
ALLOWED_LATER = [
    ("Okay, thanks.", PREV), ("Okay thanks, bye.", PREV), ("No, that's all, goodbye.", PREV),
    ("Nothing else, thank you.", PREV), ("That is everything.", PREV),
    ("Alright, cheers mate.", PREV), ("Lovely, ta.", PREV), ("Okay, cool.", PREV),
    ("Brilliant, thank you.", PREV), ("Yeah, that's great, thanks.", PREV), ("Okay then.", PREV),
    ("Fair enough.", PREV), ("Yep.", PREV), ("Okay, will do.", PREV), ("Much appreciated.", PREV),
    ("Cheers.", PREV), ("Thank you, bye bye.", PREV),
    ("No.", "Is there anything else I can help with?"),
    ("Okay.", "We open at nine on weekdays."),
]
REFUSED_LATER = [
    ("My account number is 55012.", PREV), ("It's about my bill.", PREV),
    ("Hello? Are you still there?", PREV), ("The problem is with my last invoice.", PREV),
    ("Actually, can you tell me your address?", PREV), ("", PREV),
    ("Okay, 55012.", ASK_NUMBER), ("Sure, 07700 900123.", "What's the best number to reach you on?"),
    ("Right, 4 5 6 7.", "And the last four digits of the card?"),
    ("Okay thanks, and my account number is 55012.", PREV),
    ("No.", "Do you have your account number handy?"),
    ("Okay.", ASK_NUMBER), ("Sure.", "Would you like me to read the hours again?"),
    ("Okay thanks, so what about Sundays?", PREV),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("text,prev", ALLOWED_LATER)
@pytest.mark.parametrize("first,quote", [
    ("Don't call me again, but what are your hours?", None),
    ("Never bother this number again, but what are your hours?", "never bother this number again"),
])
async def test_later_turn_courtesy_closes(purge, first, quote, text, prev):
    session = new_session()
    assert (await turn(session, first, dnc(quote))).hung is False
    result = await turn(session, text, {}, prev=prev)
    assert result.hung is True and result.tool is True, text
    assert purge.effects == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("text,prev", REFUSED_LATER)
@pytest.mark.parametrize("first,quote", [
    ("Don't call me again, but what are your hours?", None),
    ("Never bother this number again, but what are your hours?", "never bother this number again"),
])
async def test_later_turn_that_answers_or_asks_does_not_close(purge, first, quote, text, prev):
    session = new_session()
    await turn(session, first, dnc(quote))
    result = await turn(session, text, {}, prev=prev)
    assert result.hung is False and result.tool is False, text
    assert purge.effects == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["Okay.", "My account number is 55012.", ""])
async def test_a_session_without_an_opt_out_is_unchanged(purge, text):
    session = new_session()
    result = await turn(session, text, {})
    assert result.hung is False
    assert purge.effects == 0


# ------------------------------------------------------------------ N4: the realtime bridge's re-run
@pytest.mark.asyncio
async def test_bridge_style_rerun_with_empty_text_and_a_stored_opt_out_is_refused(purge):
    session = new_session(_caller_opted_out=True)
    result = await run_voice_action(session, "end_call", user_text="")
    assert result["success"] is False
    assert getattr(session, "_end_call_requested", False) is not True


# ------------------------------------------------------------------ N9: never claim "recorded" on a failed write
@pytest.mark.asyncio
async def test_a_failed_write_is_not_called_recorded(monkeypatch):
    monkeypatch.setattr("app.domain.services.dialer.opt_out.purge_opt_out_before_farewell", FakePurge(ok=False))
    session = new_session()
    result = await run_voice_action(
        session, "end_call", {"do_not_call": True},
        user_text="Do not call me again, but what are your opening hours?")
    assert result["success"] is False
    assert "recorded" not in result["message"]
    assert "asked not to be called again" in result["message"]
    assert "make no offer and do not continue the campaign" in result["message"]
    assert result["opt_out"] == "unconfirmed"


# ------------------------------------------------------------------ F1: the quote may come from an earlier turn
@pytest.mark.asyncio
async def test_an_opt_out_said_in_an_earlier_answered_turn_is_recorded_once_and_closes_later(purge):
    session = new_session()
    first = await turn(session, "I don't want any more calls. Can you tell me your address though?",
                       None, model_calls_end=False)
    assert first.hung is False and purge.effects == 0
    quote = dnc("I don't want any more calls")
    second = await turn(session, "Okay thanks.", quote)
    assert second.hung is True and second.tool is True
    assert purge.effects == 1
    assert purge.effects == 1


@pytest.mark.asyncio
async def test_a_quote_the_caller_never_said_is_not_recorded(purge):
    session = new_session()
    await turn(session, "I'm busy right now, what is this about?", None, model_calls_end=False)
    result = await turn(session, "Okay thanks.", dnc("never call this number again"))
    assert purge.effects == 0
    assert getattr(session, "_caller_opted_out", False) is not True
    assert result.hung is False


# ------------------------------------------------------------------ F2: fact in the block, rule outside it
def test_live_state_block_holds_only_the_fact_and_the_rule_is_a_plain_line_in_the_prompt():
    session = new_session(_caller_opted_out=True, system_prompt="BASE PROMPT")
    block = render_live_state_block(reduce_cascaded_session_live_state(session))
    assert "caller_opted_out=yes" in block
    assert "make no offer" not in block and "end the call" not in block
    assert session.system_prompt == "BASE PROMPT\n\n" + OPT_OUT_RULE
    assert OPT_OUT_RULE == (
        "OPT-OUT RULE: The caller has opted out of future calls. Answer only what they ask; "
        "make no offer; do not continue the campaign.")
    reduce_cascaded_session_live_state(session)
    assert session.system_prompt.count(OPT_OUT_RULE) == 1  # added once


def test_live_state_has_no_fact_and_no_rule_without_an_opt_out():
    session = new_session(system_prompt="BASE PROMPT")
    block = render_live_state_block(reduce_cascaded_session_live_state(session))
    assert "caller_opted_out" not in block
    assert session.system_prompt == "BASE PROMPT"


# ------------------------------------------------------------------ F8: bounded work
def test_opt_out_decisions_are_bounded_on_a_very_long_turn():
    """The opt-out decisions read only the newest 1,000 characters of the turn."""
    from app.domain.services.caller_assertions import continues_after_opt_out, courtesy_only
    from app.domain.services.voice_pipeline.end_call import opt_out_close_decision
    long_turn = "Stop calling me but " + "what when how where " * 300  # about 6,000 characters
    ending = ("what when how where " * 300) + ". Stop calling me. What are your hours?"
    assert len(long_turn) > 5900 and len(ending) > 5900
    session = new_session(_caller_opted_out=True)
    started = time.perf_counter()
    continues_after_opt_out(long_turn, 0)
    opt_out_close_decision(session, long_turn)
    assert opt_out_close_decision(session, ending) is False  # the newest words still decide
    courtesy_only("no thanks " * 400)
    courtesy_only("okay thanks " * 4 + "no")
    assert time.perf_counter() - started < 0.05


# ------------------------------------------------------------------ end to end (ag04 harness)
ROOT = Path(__file__).resolve().parents[3]


async def _run(turns):
    from tests.qualification.ag04_traditional import _load, _run_case
    data = _load(ROOT)
    case = deepcopy(next(c for c in data["cases"] if c["id"] == "ag04.dnc_with_question"))
    case["turns"] = turns
    return await _run_case(ROOT, data, case, data["profiles"][0])


@pytest.mark.asyncio
async def test_e2e_model_that_only_answers_a_real_request_keeps_the_line_open_and_records_once():
    row = await _run([{"caller": "Stop calling me, but I want to cancel my subscription.",
                       "chunks": ["I can help with that."]}])
    assert row["end"]["shutdown_count"] == 0
    assert row["end"]["dnc_effect_count"] == 1


@pytest.mark.asyncio
async def test_e2e_opt_out_through_the_model_quote_in_an_earlier_turn_is_recorded_once_and_hangs_up_once():
    row = await _run([
        {"caller": "I don't want any more calls. Can you tell me your address though?",
         "chunks": ["Our address is one High Street."]},
        {"caller": "Okay thanks.",
         "tool": {"name": "end_call", "arguments": {"do_not_call": True, "opt_out_quote": "I don't want any more calls"}},
         "chunks": ["Goodbye."]},
    ])
    assert row["end"]["dnc_effect_count"] == 1
    assert row["end"]["shutdown_count"] == 1
