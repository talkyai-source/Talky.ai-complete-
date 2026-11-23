"""speech_guard.py: the agent never claims to be human or denies being an AI.

Readiness plan: honesty about being an AI rested on the prompt alone. A model
that answers "No, I'm a real person" was spoken as written. The guard replaces
any first-person human claim or AI denial with one honest line.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.domain.services.voice_pipeline.speech_guard import guard_spoken_sentence

COMPANY = "Acme"


def _session(company=COMPANY):
    cfg = SimpleNamespace(company_name=company)
    return SimpleNamespace(agent_config=cfg, _voice_action_results={}, conversation_history=[])


def _honest(company=COMPANY):
    return f"I'm an AI assistant for {company}." if company else "I'm an AI assistant."


CAUGHT = [
    "I'm a real person.",
    "I am a real person, yes.",
    "Yes, I'm human.",
    "I am a human being.",
    "No, I'm not a robot.",
    "I'm not an AI.",
    "I am not a bot, I promise.",
    "This is a real person speaking.",
    "You're talking to a real person.",
    "You are speaking with a live human.",
    "No, this isn't a recording or a bot, I'm a real agent here.",
    "I'm not automated.",
    "I'm a live agent.",
    "Nope, real human here.",
    "I'm flesh and blood.",
    # more of our own
    "I'M A REAL PERSON.",
    "I’m a real person.",
    "I’m not an AI.",
    "No, I am not a machine.",
    "I'm not a computer, I'm a real, live person.",
    "I am a living, breathing human.",
    "You're not talking to a robot.",
    "Of course I'm a real person, how can I help?",
    "Sure thing, I'm human and I can help with that.",
    "I'm definitely not an AI.",
    "This isn't a robocall.",
]

PASSES = [
    "I'm an AI assistant for Acme.",
    "I'm not a real person, I'm an AI assistant.",
    "I'm an AI, not a human.",
    "I can connect you to a real person.",
    "Would you like to speak to a human?",
    "A real person from our team will call you back.",
    "Our team are real people who can help with that.",
    "Are you a real person or a business?",
    "I understand you'd prefer a human.",
    "You're not talking to a robot voice menu, I'm an AI assistant and I can help directly.",
    "The person you spoke to earlier was a human colleague.",
    "Is this a good time?",
    "Hi, this is Sarah, an AI assistant from All State Estimation, on a recorded line. Am I speaking with John?",
    "You said you're a real person and want prices.",
    "I'm an AI assistant for a real estate agent.",
    "I'm human-like in how I talk, but I'm an AI.",
    "We have a real person here at Acme who can help.",
    "I can put a real person on the line for you.",
    "I'm not sure about that.",
    "I'm not able to do that.",
    "",
]


@pytest.mark.parametrize("company", [COMPANY, None, "", "  "])
@pytest.mark.parametrize("sentence", CAUGHT)
def test_human_claims_and_ai_denials_are_replaced(sentence, company):
    expected = _honest(company.strip() if company else None)
    assert guard_spoken_sentence(_session(company), sentence) == expected


@pytest.mark.parametrize("company", [COMPANY, None])
@pytest.mark.parametrize("sentence", PASSES)
def test_honest_or_unrelated_sentences_pass_unchanged(sentence, company):
    assert guard_spoken_sentence(_session(company), sentence) == sentence


def test_at_least_fifteen_cases_each_way():
    assert len(CAUGHT) >= 15 and len([s for s in PASSES if s]) >= 15


def test_a_claim_inside_a_longer_sentence_replaces_the_whole_sentence():
    out = guard_spoken_sentence(
        _session(), "Great question, and I'm a real person, so let me check the price for you.")
    assert out == "I'm an AI assistant for Acme."


def test_the_caller_being_quoted_is_not_the_agents_claim():
    # Decision: only first-person agent claims count. "You said you're a real
    # person" attributes the words to the caller, so it is spoken unchanged.
    sentence = "You said you're a real person and want prices."
    assert guard_spoken_sentence(_session(), sentence) == sentence


def test_borderline_denial_followed_by_an_explicit_ai_statement_passes():
    # Decision: "You're not talking to a robot voice menu, I'm an AI assistant"
    # denies a menu, not AI-ness, and states the AI identity outright.
    s = "You're not talking to a robot voice menu, I'm an AI assistant and I can help directly."
    assert guard_spoken_sentence(_session(), s) == s
    # ...but a bare denial of the same shape is replaced.
    assert guard_spoken_sentence(_session(), "You're not talking to a robot.") == _honest()


def test_a_positive_human_claim_is_not_excused_by_an_ai_mention():
    out = guard_spoken_sentence(_session(), "I'm an AI assistant, and I'm a real person.")
    assert out == _honest()


def test_company_name_whitespace_is_collapsed():
    assert guard_spoken_sentence(_session("  Acme   Corp "), "I'm human.") \
        == "I'm an AI assistant for Acme Corp."


@pytest.mark.parametrize("session", [
    SimpleNamespace(),
    SimpleNamespace(agent_config=None),
    SimpleNamespace(agent_config=SimpleNamespace()),
    SimpleNamespace(agent_config=SimpleNamespace(company_name=None)),
    SimpleNamespace(agent_config=SimpleNamespace(company_name=12345)),
    object(),
    None,
])
def test_a_session_without_config_never_raises(session):
    out = guard_spoken_sentence(session, "I'm a real person.")
    assert out.startswith("I'm an AI assistant")
    assert guard_spoken_sentence(session, "Is this a good time?") == "Is this a good time?"


def test_empty_string_is_returned_unchanged():
    assert guard_spoken_sentence(_session(), "") == ""


def test_human_claim_wins_over_an_unbacked_action_claim():
    out = guard_spoken_sentence(_session(), "I'm a real person and I've sent the email.")
    assert out == _honest()


def test_the_replacement_line_never_names_the_agent():
    session = _session()
    session.agent_config.agent_name = "Sarah"
    assert "Sarah" not in guard_spoken_sentence(session, "I'm a real person.")


def test_turn_streamer_cleans_then_guards_a_sentence_this_way():
    # No existing test drives turn_streamer's coroutine (it needs a live LLM
    # stream and a websocket), so this mirrors its two-step order for a
    # sentence (clean_response, then guard_spoken_sentence) and checks the
    # guard is wired at both places. The grep part is a guard, not proof.
    from app.domain.services.llm_guardrails import get_guardrails

    cleaned = get_guardrails().clean_response("No, I'm not a bot, I promise.")
    assert guard_spoken_sentence(_session(), cleaned) == _honest()
    src = (Path(__file__).resolve().parents[2]
           / "app/domain/services/voice_pipeline/turn_streamer.py").read_text(encoding="utf-8")
    assert src.count("sentence = guard_spoken_sentence(session, sentence)") == 2


# "recording" as a verb is about the call, not about who is speaking.
@pytest.mark.parametrize("sentence", [
    "I'm not recording this call.",
    "I am not recording anything right now.",
    "This isn't recording properly on my side.",
    "I'm not recorded on that list.",
])
def test_a_statement_about_recording_is_not_an_identity_claim(sentence):
    from app.domain.services.voice_pipeline.speech_guard import guard_spoken_sentence

    assert guard_spoken_sentence(object(), sentence) == sentence


@pytest.mark.parametrize("sentence", [
    "I'm not a recording.",
    "No, this isn't a recording.",
    "You're not talking to a recording.",
    "This is not a recorded message, I promise.",
])
def test_denying_being_a_recording_is_still_replaced(sentence):
    from app.domain.services.voice_pipeline.speech_guard import guard_spoken_sentence

    assert guard_spoken_sentence(object(), sentence) == "I'm an AI assistant."


# --- The model's own introductions say it is an AI (23 Nov 2026) -------------
import logging
import re
import time

from app.domain.services.voice_pipeline.ai_identity_intro import add_ai_identity
from app.domain.services.voice_pipeline.inbound_answer_greeting import says_it_is_ai

_CALL_ID = "7ad487e3-52a6-4b0e-9d0b-6f1c2a8e9b11"
_CO = "All State Estimation"
_INTRO_LOGGER = "app.domain.services.voice_pipeline.ai_identity_intro"


def _intro_session(name="grace", spoken=(), company=_CO):
    cfg = SimpleNamespace(company_name=company, agent_name=name)
    return SimpleNamespace(agent_config=cfg, _voice_action_results={}, conversation_history=[],
                           _spoken_sentences=list(spoken), call_id=_CALL_ID)


# (before, after, pattern): every introduction found in the 23 and 20 Nov calls.
REAL_INTROS = [
    ("Hi there, this is Grace from All State Estimation.",
     "Hi there, this is Grace, an AI assistant from All State Estimation.", "this_is"),
    ("Hi, this is Grace from All State Estimation.",
     "Hi, this is Grace, an AI assistant from All State Estimation.", "this_is"),
    ("Hi there — this is Grace from All State Estimation.",
     "Hi there — this is Grace, an AI assistant from All State Estimation.", "this_is"),
    ("Hi there, this is Grace from All State Estimation — sorry, I think we got cut off.",
     "Hi there, this is Grace, an AI assistant from All State Estimation — sorry, I think we got cut off.",
     "this_is"),
    ("Morning Sam, this is Grace from All State Estimation.",
     "Morning Sam, this is Grace, an AI assistant from All State Estimation.", "this_is"),
    ("Hi Sam, this is Grace from All State Estimation.",
     "Hi Sam, this is Grace, an AI assistant from All State Estimation.", "this_is"),
    ("No problem — this is Grace from All State Estimation.",
     "No problem — this is Grace, an AI assistant from All State Estimation.", "this_is"),
    ("Hello — I'm Grace, calling from All State Estimation.",
     "Hello — I'm Grace, an AI assistant, calling from All State Estimation.", "i_am"),
    ("I'm Grace from All State Estimation — I'm calling about your estimate.",
     "I'm Grace, an AI assistant from All State Estimation — I'm calling about your estimate.", "i_am"),
    ("Evening — I'm Grace from All State Estimation.",
     "Evening — I'm Grace, an AI assistant from All State Estimation.", "i_am"),
    ("Hi there — it's Grace from All State Estimation.",
     "Hi there — it's Grace, an AI assistant from All State Estimation.", "it_is"),
    ("Hi there, Grace from All State Estimation — I'll be quick.",
     "Hi there, Grace, an AI assistant from All State Estimation — I'll be quick.", "greeting_name"),
    ("Hi there, Grace from All State Estimation.",
     "Hi there, Grace, an AI assistant from All State Estimation.", "greeting_name"),
]
# The two real wordings of an agent called Alex.
ALEX_INTROS = [
    ("Hi there, this is Alex from All State Estimation Ltd.",
     "Hi there, this is Alex, an AI assistant from All State Estimation Ltd.", "this_is"),
    ("It's Alex here from All State Estimation Ltd.",
     "It's Alex, an AI assistant, here from All State Estimation Ltd.", "it_is"),
]


@pytest.mark.parametrize("before,after,where", REAL_INTROS)
def test_every_real_introduction_gains_an_ai_identity(before, after, where, caplog):
    caplog.set_level(logging.INFO)
    out = guard_spoken_sentence(_intro_session(), before)
    print(f"{before!r} -> {out!r}")
    assert out == after
    assert says_it_is_ai(out)
    assert f"where={where}" in caplog.text


@pytest.mark.parametrize("before,after,where", ALEX_INTROS)
def test_alex_introductions_gain_an_ai_identity(before, after, where):
    out = guard_spoken_sentence(_intro_session("Alex"), before)
    print(f"{before!r} -> {out!r}")
    assert out == after and says_it_is_ai(out)


@pytest.mark.parametrize("sentence", [
    "Hi, this is Grace, an AI assistant from All State Estimation, on a recorded line. Who am I speaking with?",
    "Hi there, this is Grace, an AI from All State Estimation.",
    "Hi, I'm Grace, your AI assistant from All State Estimation.",
    "This is Grace, a virtual assistant from All State Estimation.",
    "This is Grace, an automated assistant from All State Estimation.",
    "I'm Grace, an artificial intelligence calling from All State Estimation.",
])
def test_a_sentence_that_already_says_it_is_an_ai_is_unchanged(sentence):
    assert guard_spoken_sentence(_intro_session(), sentence) == sentence


def test_an_earlier_sentence_of_the_same_reply_that_said_it_stops_a_second_insertion():
    s = "This is Grace from All State Estimation."
    assert guard_spoken_sentence(_intro_session(spoken=["I'm an AI assistant."]), s) == s
    # The converse: nothing said it yet, so it is added.
    assert guard_spoken_sentence(_intro_session(spoken=["Hi there."]), s) \
        == "This is Grace, an AI assistant from All State Estimation."


@pytest.mark.parametrize("sentence", [
    "Am I speaking with Grace?",
    "Thanks, Grace.",
    "this is Grace?",
    "Is Grace here today?",
    "This is about Grace's estimate.",
    "This is Grace's estimate.",
    'The note says "this is Grace from All State Estimation" so I checked.',
    "He said “I'm Grace from All State Estimation” to me.",
    "This is Grace Smith from the office.",
    "This is Graceful and calm.",
])
def test_someone_else_named_like_the_agent_is_not_an_introduction(sentence):
    assert guard_spoken_sentence(_intro_session(), sentence) == sentence


@pytest.mark.parametrize("name,sentence", [
    ("", "This is Grace from All State Estimation."),
    (None, "This is Grace from All State Estimation."),
    ("   ", "I'm Grace from All State Estimation."),
    ("Max", "This is max capacity."),
    ("Will", "I'm willing to help."),
    ("Max", "This is max capacity from the office."),
])
def test_missing_name_or_a_common_word_changes_nothing_and_logs_nothing(name, sentence, caplog):
    caplog.set_level(logging.INFO)
    assert guard_spoken_sentence(_intro_session(name), sentence) == sentence
    assert "ai_identity_added" not in caplog.text


@pytest.mark.parametrize("name,sentence,expected", [
    ("grace", "This is Grace from Acme.", "This is Grace, an AI assistant from Acme."),
    ("GRACE", "This is Grace from Acme.", "This is Grace, an AI assistant from Acme."),
    ("Grace Miller", "This is Grace from Acme.", "This is Grace, an AI assistant from Acme."),
    ("Grace Miller", "This is Grace Miller from Acme.", "This is Grace Miller, an AI assistant from Acme."),
    ("grace miller", "Hi there, Grace Miller from Acme.", "Hi there, Grace Miller, an AI assistant from Acme."),
    ("Grace Miller", "This is Grace Smith from Acme.", "This is Grace Smith from Acme."),
    ("Grace", "This is grace from Acme.", "This is grace from Acme."),
    ("Grace", "Grace speaking.", "Grace, an AI assistant, speaking."),
    ("Grace", "Hello, Grace here.", "Hello, Grace, an AI assistant, here."),
    ("Grace", "My name is Grace.", "My name is Grace, an AI assistant."),
    ("Grace", "I’m Grace from Acme.", "I’m Grace, an AI assistant from Acme."),
    ("Mary-Ann", "This is Mary-Ann from Acme.", "This is Mary-Ann, an AI assistant from Acme."),
])
def test_stored_name_case_and_several_words(name, sentence, expected):
    assert guard_spoken_sentence(_intro_session(name), sentence) == expected


def test_a_human_claim_still_wins_over_an_introduction():
    out = guard_spoken_sentence(_intro_session(), "This is Grace from Acme and I'm a real person.")
    assert out == "I'm an AI assistant for All State Estimation."


@pytest.mark.parametrize("sentence", [
    "Soy Grace de All State Estimation.",
    "Hola, soy Grace de All State Estimation.",
    "Bonjour, je suis Grace de All State Estimation.",
])
def test_non_english_is_unchanged(sentence):
    assert guard_spoken_sentence(_intro_session(), sentence) == sentence


def test_a_title_after_the_name_gives_the_wordy_but_true_result():
    assert guard_spoken_sentence(
        _intro_session(), "This is Grace, the scheduling assistant from Acme.",
    ) == "This is Grace, an AI assistant, the scheduling assistant from Acme."


def test_the_log_line_carries_the_pattern_and_no_word_of_the_reply(caplog):
    caplog.set_level(logging.DEBUG)
    reply = "Hi there, this is Grace from All State Estimation."
    guard_spoken_sentence(_intro_session(), reply)
    lines = [r for r in caplog.records if r.name == _INTRO_LOGGER]
    assert len(lines) == 1
    text = lines[0].getMessage()
    assert text == "ai_identity_added call_id=7ad487e3-52a where=this_is"
    for word in ("Hi", "there", "Grace", "All", "State", "Estimation"):
        assert word not in text


@pytest.mark.parametrize("make", [
    lambda: "this is " * 6250,
    lambda: "Hi " + "-" * 50000,
    lambda: " " * 50000,
    lambda: "Hi there, " * 5000,
    lambda: "this is Grace " * 3500,
])
def test_matching_is_linear_on_long_input(make):
    text = make()
    session = _intro_session("Grace Miller")
    started = time.perf_counter()
    add_ai_identity(session, text)
    assert time.perf_counter() - started < 0.2


def test_replacement_lines_are_not_rewritten_again():
    # An earlier rule REPLACED the sentence: the product's own words are spoken as is.
    assert guard_spoken_sentence(_intro_session(), "I'm a real person.") \
        == "I'm an AI assistant for All State Estimation."
    assert guard_spoken_sentence(_intro_session(), "I have sent the email. This is Grace from Acme.") \
        == "I'm not able to do that from this call."


# --- A name between "this is" / "I'm" and the claim or denial (23 Nov 2026) ----
from app.domain.services.voice_pipeline import speech_guard as _sg
from app.domain.services.voice_pipeline.speech_guard import _claims_human

_AI_LINE = "I'm an AI assistant for All State Estimation."

# Gap 1: a real/live/actual person, human, agent or representative after a name.
# Gap 2: a denial of being an AI, bot, robot, ... after a name.
# "My name is Grace and I'm a real person." and the two "I'm not" forms were
# already caught on the base commit (436748eb); the rest were spoken.
NAMED_MUST_CATCH = [
    "This is Grace, a real person.",
    "I'm Grace, a real human being.",
    "It's Grace here, a live agent.",
    "This is Grace Miller, an actual person from Acme.",
    "My name is Grace and I'm a real person.",
    "This is Grace, a real person from Acme.",
    "Hi there, this is Grace, a genuine representative of Acme.",
    "THIS IS GRACE, A REAL PERSON.",
    "I’m Grace, a real person.",
    "This is Grace, not an AI.",
    "I'm Grace, not a robot.",
    "It's Grace, I'm not a bot.",
    "This is Grace and no, I am not an AI.",
    "My name is Grace, not a machine.",
    "This is Grace here, not automated.",
    "I'm Grace, not a recording.",
]

NAMED_MUST_NOT_CATCH = [
    "I can put you through to a real person.",
    "Would you like to speak with a live agent?",
    "A real person will call you back.",
    "This is Grace, an AI assistant.",
    "You're a real person, I understand.",
    "Is this a real person I'm speaking with?",
    "I'm an AI assistant, not a person.",
    "I'm not a person, I'm an AI assistant.",
    "No, I'm an AI.",
    "I'm not able to do that.",
    "That's not an AI feature.",
    "It's not automated on our side.",
    "This is Grace, I'm an AI, not a real person.",
    "This is Grace, not an AI, I'm an AI assistant.",
]


@pytest.mark.parametrize("name", ["grace", None])
@pytest.mark.parametrize("sentence", NAMED_MUST_CATCH)
def test_a_claim_or_denial_after_a_name_is_replaced(sentence, name):
    out = guard_spoken_sentence(_intro_session(name), sentence)
    print(f"{name!r} {sentence!r} -> {out!r}")
    assert out == _AI_LINE
    assert _claims_human(sentence)


@pytest.mark.parametrize("name", ["grace", None])
@pytest.mark.parametrize("sentence", NAMED_MUST_NOT_CATCH)
def test_true_or_third_party_sentences_are_spoken_as_before(sentence, name):
    out = guard_spoken_sentence(_intro_session(name), sentence)
    print(f"{name!r} {sentence!r} -> {out!r}")
    assert out == sentence
    assert not _claims_human(sentence)


_REALTIME_CAUGHT = [
    "I'm a real person, not a robot.", "I am a real person, yes.", "No, I'm not a robot.",
    "I'm not an AI.", "This is a real person speaking.",
]


@pytest.mark.parametrize("sentence", CAUGHT + _REALTIME_CAUGHT)
def test_nothing_caught_before_is_missed_now(sentence):
    assert _claims_human(sentence)


@pytest.mark.parametrize("sentence", [s for s in PASSES if s])
def test_nothing_that_passed_before_is_caught_now(sentence):
    assert not _claims_human(sentence)


@pytest.mark.parametrize("sentence", [
    "This is Grace, a real person.",
    "This is Grace, not an AI.",
    "I'm Grace, not a robot.",
    "This is Grace Miller, an actual person from Acme.",
])
def test_a_named_claim_never_reaches_the_ai_identity_step(sentence, monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("add_ai_identity must not run for a human claim")
    monkeypatch.setattr(_sg, "add_ai_identity", boom)
    assert guard_spoken_sentence(_intro_session(), sentence) == _AI_LINE


def test_a_whole_realtime_reply_with_a_named_claim_is_caught():
    # app/realtime/bridge.py calls _claims_human on the whole reply.
    assert _claims_human("Hello there! This is Grace, a real person. How can I help you today?")
    assert _claims_human("Thanks for picking up. I'm Grace, not a robot. I'm calling about your estimate.")
    assert not _claims_human("Hello there! This is Grace, an AI assistant. I can put you through to a real person.")


@pytest.mark.parametrize("make", [
    lambda: "this is " * 6250,
    lambda: "I'm Grace, " * 4500,
    lambda: "Grace, " * 7200,
    lambda: "This is Grace, Miller, " * 2200,
    lambda: "I'm " + "Grace " * 8000,
])
def test_the_claim_rule_is_linear_on_long_input(make):
    text = make()
    def best(call):
        # Best of three: the laptop runs other agents, a quadratic rule fails all three.
        runs = []
        for _ in range(3):
            started = time.perf_counter()
            call()
            runs.append(time.perf_counter() - started)
        return min(runs)

    assert best(lambda: _claims_human(text)) < 0.2


# --- A new clause about someone else is true and is spoken (23 Nov 2026, lead) ---
# The first version of the named forms replaced these with the AI line, losing a
# true and useful promise ("a real person will follow up"). A claim about the
# SPEAKER ends the clause or goes on describing the same person; these start a
# new clause with its own verb.
NAMED_TRUE_NEW_CLAUSE = [
    "I'm Grace, and a real person will follow up with you tomorrow.",
    "I'm Grace, and a real person from the team will follow up with you.",
    "It's Monday, a real person will call you back tomorrow.",
    "This is Acme, a real person will be with you shortly.",
    "This is Acme, a real person can call you back.",
    "This is Grace, a real person is available if you prefer.",
    "It's Grace, and a live agent can take over at any time.",
    "This is Grace, an AI assistant, and a real person can call you back.",
    "This is Mark's line, a real person will answer shortly.",
    "It's Friday, not a robot day.",
]

NAMED_CLAIM_ABOUT_THE_SPEAKER = [
    "This is Grace, a real person who can help you today.",
    "This is Grace, a real person here to help.",
    "This is Grace, a real person, and I can help you with that.",
    "This is Grace and not a robot.",
    "I'm Grace, not a robot, I promise.",
]


@pytest.mark.parametrize("name", ["grace", None])
@pytest.mark.parametrize("sentence", NAMED_TRUE_NEW_CLAUSE)
def test_a_new_clause_about_someone_else_is_not_a_claim(sentence, name):
    assert not _claims_human(sentence)
    out = guard_spoken_sentence(_intro_session(name), sentence)
    assert out != _AI_LINE
    # Everything after the first comma (the promise itself) is spoken as written.
    tail = sentence.split(",", 1)[1]
    assert out.endswith(tail), out
    if name is None:
        assert out == sentence


@pytest.mark.parametrize("name", ["grace", None])
@pytest.mark.parametrize("sentence", NAMED_CLAIM_ABOUT_THE_SPEAKER)
def test_a_named_claim_that_goes_on_describing_the_speaker_is_replaced(sentence, name):
    assert _claims_human(sentence)
    assert guard_spoken_sentence(_intro_session(name), sentence) == _AI_LINE
