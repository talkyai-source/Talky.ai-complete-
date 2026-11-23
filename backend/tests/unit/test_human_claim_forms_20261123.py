"""speech_guard._claims_human: more ways to say "I am human" / "I am not an AI" (23 Nov 2026).

An independent review listed sentences that still got past the check and were
spoken as written ("I'm a person.", "I'm no robot.", "Grace here, a real person.").
A claim or denial counts only when it is about the speaker and ends the clause
or goes on describing the same speaker; a new clause about someone or something
else ("It's a real person you'll speak to on Thursday.") is true and is spoken.
"""
from __future__ import annotations

import time

import pytest

from app.domain.services.voice_pipeline.speech_guard import _claims_human, guard_spoken_sentence
from tests.unit.test_realtime_ai_identity_guard import CLAIMS as RT_CLAIMS
from tests.unit.test_realtime_ai_identity_guard import PASSES as RT_PASSES
from tests.unit.test_speech_guard_ai_identity import (
    CAUGHT as OLD_CAUGHT,
    NAMED_CLAIM_ABOUT_THE_SPEAKER,
    NAMED_MUST_CATCH,
    NAMED_MUST_NOT_CATCH,
    NAMED_TRUE_NEW_CLAUSE,
    PASSES as OLD_PASSES,
    _REALTIME_CAUGHT,
    _intro_session,
)

AI_LINE = "I'm an AI assistant for All State Estimation."

LIST_A = [
    # human claims
    "I'm a person.", "I am a person.", "I'm a person, not a machine.", "Yes, I'm real.",
    "I'm real, I promise.", "It's a real person.", "It's a real person here.",
    "It's me, a real person.", "You're talking with a person.", "You're speaking to a human.",
    "You've got a real person.", "This is a person speaking.", "A human here.",
    "Definitely a human here.", "We're real people here.", "I'm a woman named Grace.",
    "I'm a guy called Mark.", "I'm Grace, a living, breathing person.",
    "Grace here, real person.", "Grace here, a real person.",
    "This is Grace, a human, not an AI.", "I'm Grace, a human being.",
    # AI denials
    "I'm no robot.", "I'm no AI.", "I'm not some AI.", "I'm certainly not a bot.",
    "I'm honestly not a robot.", "I'm not really a bot.", "I'm not actually an AI.",
    "This isn't an automated call.", "This is not an automated call.",
    "This is not a machine.", "This isn't a computer.", "You're not dealing with a bot.",
    "You're not on with a robot.", "No robots here, just me.", "Nope, not a bot.",
    "No, not a robot.", "Not an AI, just Grace.", "I'm Grace, calling from Acme, not a robot.",
]

# Spoken exactly as written today (0508cb20): none of these is caught on the base commit,
# and the guard's last step (", an AI assistant" after a name) changes none of them.
LIST_B = [
    "I'm not a person, I'm an AI assistant.", "I'm an AI assistant, not a person.",
    "I'm not a real person.", "No, I'm not human.", "I'm not a human.",
    "I'm an AI, not a robot voice recording.", "I can connect you with a person.",
    "A person will call you back.", "A real person will call you back.",
    "You'll be speaking to a person on our team tomorrow.",
    "You're not talking to a person right now, I'm an AI assistant.",
    "It's a real person you'll speak to on Thursday.",
    "It's a real person who reviews every estimate.", "Our estimators are real people.",
    "I'm really sorry about that.", "I'm real busy today, sorry.",
    "I'm a real AI assistant, not a recording.", "I'm no expert on that.",
    "I'm not some salesperson.", "I'm not really sure.",
    "I'm not actually able to see your account.", "This isn't a sales call.",
    "This is not a machine fault on your side.", "It isn't a computer problem.",
    "This isn't a computer issue, it's a billing one.",
    "No robots are involved in the estimate.", "Not a problem.",
    "No, not a problem at all.", "Not an issue, I can note that.",
    "I'm a person short on the team today.",
    "I'm calling about your estimate, not a robot vacuum.",
    "Is this a real person I'm speaking with?", "Are you a real person?",
    "You're a real person, I understand.",
    # our own
    "I'm no AI expert, but I can pass that on.", "We're real busy this week.",
    "You've got a real person to talk to on Monday.", "Hello. A person here will call you back.",
    "This is not a computer problem, it is a billing matter.",
]


def _speaker(name):
    return _intro_session(name)


@pytest.mark.parametrize("sentence", LIST_A)
def test_list_a_is_a_claim(sentence):
    # fails before: 39 of the 40 returned False on 0508cb20
    assert _claims_human(sentence)


@pytest.mark.parametrize("name", ["grace", None])
@pytest.mark.parametrize("sentence", LIST_A)
def test_list_a_is_replaced_by_the_product_line(sentence, name):
    assert guard_spoken_sentence(_speaker(name), sentence) == AI_LINE


@pytest.mark.parametrize("sentence", LIST_B)
def test_list_b_is_not_a_claim(sentence):
    assert not _claims_human(sentence)


@pytest.mark.parametrize("name", ["grace", None])
@pytest.mark.parametrize("sentence", LIST_B)
def test_list_b_is_spoken_as_written(sentence, name):
    # "Today" on 0508cb20 is: spoken unchanged (no name, no human claim, no introduction).
    assert guard_spoken_sentence(_speaker(name), sentence) == sentence


@pytest.mark.parametrize("sentence", OLD_CAUGHT + _REALTIME_CAUGHT + NAMED_MUST_CATCH
                         + NAMED_CLAIM_ABOUT_THE_SPEAKER + RT_CLAIMS)
def test_everything_caught_before_is_still_caught(sentence):
    assert _claims_human(sentence)


@pytest.mark.parametrize("sentence", [s for s in OLD_PASSES + NAMED_MUST_NOT_CATCH
                                      + NAMED_TRUE_NEW_CLAUSE + RT_PASSES if s])
def test_everything_not_caught_before_is_still_not_caught(sentence):
    assert not _claims_human(sentence)


@pytest.mark.parametrize("sentence", [
    "An AI assistant here. I'm not a robot voice recording, I'm an AI.",
    "I'm an AI, not a robot.",
    "I'm not a person, I'm an AI assistant.",
    "This isn't a computer, I'm an AI assistant.",
])
def test_a_sentence_that_also_says_it_is_an_ai_is_honest_for_denials(sentence):
    assert not _claims_human(sentence)


def test_a_positive_claim_is_not_excused_by_an_ai_mention():
    assert _claims_human("I'm an AI assistant, and I'm a person.")
    # "of" goes on describing the speaker: this is a human claim, replaced even beside "AI".
    assert _claims_human("I'm a person of few words, but I'm an AI assistant.")


# --- Realtime path: whole replies, the claim can sit in a later sentence ---------
# Every new form is anchored at the start of a SENTENCE (the text start, or after
# ". " / "! " / "? ") or is a free-standing "I'm ..." / "you're ..." phrase, so a form
# in the second sentence is caught. Sentence-start only: "A human here.", "Definitely a
# human here.", "Nope, not a bot.", "No robots here.", "Not an AI.", "Grace here, a real
# person." A start of a clause after a comma ("Well, a human here.") is not seen.
@pytest.mark.parametrize("reply", [
    "Hello there! A human here. How can I help?",
    "Thanks for picking up. Nope, not a bot. I'm calling about your estimate.",
    "Hi. Grace here, a real person. I'm calling from Acme.",
    "Sure. No robots here, just me. What can I do for you?",
    "Good morning. I'm a person. How can I help?",
    "Thanks. This isn't an automated call. I'm calling about your estimate.",
])
def test_a_form_in_the_second_sentence_of_a_whole_reply_is_caught(reply):
    assert _claims_human(reply)


def test_known_limit_a_sentence_start_form_after_a_comma_is_not_seen():
    assert not _claims_human("Well, a human here.")
    assert not _claims_human("Sure, not a bot.")


# --- Matching stays linear ------------------------------------------------------
@pytest.mark.parametrize("make", [
    lambda n: "I'm " * (n // 4),
    lambda n: "not " * (n // 4),
    lambda n: "it's a real " * (n // 12),
    lambda n: "No, " * (n // 4),
    lambda n: "I'm Grace, calling " * (n // 19),
    lambda n: ". Grace here, " * (n // 14),
    lambda n: "This is not " * (n // 12),
])
def test_the_new_forms_are_linear_on_long_input(make):
    # 50,000 characters in well under two seconds (best of three). A rule that
    # backtracks takes many seconds or never finishes; a linear one takes a few
    # hundredths of a second. The wide margin is deliberate: a tight wall-clock
    # limit fails at random on a busy machine.
    text = make(50_000)
    runs = []
    for _ in range(3):
        started = time.perf_counter()
        _claims_human(text)
        runs.append(time.perf_counter() - started)
    assert min(runs) < 2.0, runs


# --- lead, 23 Nov 2026: a bare person after a name must end the clause ---
# "This is Grace, a human." is a claim. "This is Grace, a person from our team
# will call you back" is a true promise about someone else and must be spoken.
_BARE_AFTER_NAME_TRUE = [
    "This is Grace, a person from our team will call you back shortly.",
    "Grace here, a person from our team will call you back.",
    "This is Acme, a person from the team will call you back.",
    "It's Grace, a human from the estimating team will review it tomorrow.",
]
_BARE_AFTER_NAME_CLAIM = [
    "This is Grace, a human.",
    "I'm Grace, a human being.",
    "This is Grace, a human, not an AI.",
    "Grace here, a person.",
    "Grace here, a real person from Acme.",
    "This is Grace, a real person from Acme.",
]


@pytest.mark.parametrize("sentence", _BARE_AFTER_NAME_TRUE)
def test_a_bare_person_after_a_name_that_starts_a_new_clause_is_not_a_claim(sentence):
    from app.domain.services.voice_pipeline.speech_guard import _claims_human

    assert not _claims_human(sentence)


@pytest.mark.parametrize("sentence", _BARE_AFTER_NAME_CLAIM)
def test_a_bare_person_after_a_name_that_ends_the_clause_is_a_claim(sentence):
    from app.domain.services.voice_pipeline.speech_guard import _claims_human

    assert _claims_human(sentence)
