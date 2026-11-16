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
