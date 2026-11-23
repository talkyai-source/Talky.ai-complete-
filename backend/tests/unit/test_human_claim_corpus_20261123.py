"""Corpus test for the human-claim / AI-denial check (speech_guard._claims_human).

Three data files under tests/unit/data/human_claims/ were written for the review of
23 November 2026:

- honest.txt: honest sentences that must be spoken.
- claims.txt: ``P|`` plain claims or denials a rule must catch, ``E|`` evasions that a
  pattern rule does not try to catch (no assertion, the count caught is printed).
- mixed.txt: ``C|`` contains a claim or denial (must be caught), ``H|`` honest (must be
  spoken).

Each "still" list below is EXACT: the sentences the reviewed patterns really do not
handle. The test fails when a listed sentence is no longer an exception (remove it from
the list) and when a new exception appears (a regression, or a deliberate change that
needs a reason here). A list entry is never a way to hide a fault the patterns were
meant to fix.
"""

from pathlib import Path

import pytest

from app.domain.services.voice_pipeline.speech_guard import _claims_human, guard_spoken_sentence

DATA = Path(__file__).resolve().parent / "data" / "human_claims"


def _read(name: str) -> list[str]:
    lines = []
    with open(DATA / name, encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if line and not line.startswith("#"):
                lines.append(line)
    return lines


HONEST = _read("honest.txt")
_CLAIM_ROWS = [line.split("|", 1) for line in _read("claims.txt")]
PLAIN = [s for k, s in _CLAIM_ROWS if k == "P"]
EVASIONS = [s for k, s in _CLAIM_ROWS if k == "E"]
_MIXED_ROWS = [line.split("|", 1) for line in _read("mixed.txt")]
MIXED_C = [s for k, s in _MIXED_ROWS if k == "C"]
MIXED_H = [s for k, s in _MIXED_ROWS if k == "H"]

# Honest sentences that are still replaced. The agent cannot tell from the sentence
# alone whether it answers the caller's question about itself, so these stay caught.
HONEST_STILL_CAUGHT = [
    # older form, not changed here: "we're real people"
    "I'm an AI assistant, but we're real people at Acme who build every estimate.",
    # older form, not changed here: "you're speaking with a live agent"
    "In a moment you're speaking with a live agent.",
    # "no robots / no machines here" reads as a denial; depends on the caller's question
    "No robots here, our crews do the work by hand.",
    "No machines here, every estimate is done by hand.",
    # elliptical "not AI": depends on the caller's question
    "No, not AI.",
    "Got it. No, not AI. Our estimators prepare every quote by hand.",
    "Okay. Not a computer, no. A licensed estimator does every takeoff.",
    # "it's a live agent / a real person": depends on the caller's question
    "Who answers the after-hours line? It's a live agent.",
    "It's a live agent.",
    "It's a real person.",
    # sentence-start "a real person here," followed by a comma: depends on the caller's question
    "Yes, a real person here, one of our estimators, reviews every drawing.",
    "Definitely a human here, on our side, checks every number.",
    "Thanks for asking. A person here, our office manager, reviews it.",
    # "this isn't AI" before "-generated": older form, not changed here
    "This isn't AI-generated, an estimator writes every line.",
    "This isn't an AI-generated estimate, an estimator prepares it.",
    # "I'm real" ended by a comma: older form, not changed here
    "I'm real, real sorry about that.",
    # "not a computer / machine" followed by a noun: older form, not changed here
    "I'm not a computer person, but I can take a message for IT.",
    "I'm not a computer technician, but I can book one.",
    "I'm not a machine learning expert.",
    # "this is a real person / a live agent / a real human" as a noun phrase: older form, not changed here
    "This is a real person's job, not software.",
    "This is a live agent queue, so the wait may vary.",
    "This is a real human touch our customers love.",
    # "I'm human" before "resources": older form, not changed here
    "I'm human resources' assistant for scheduling interviews.",
]

# Plain claims that are still spoken (a known gap of the reviewed patterns).
PLAIN_CLAIMS_STILL_MISSED = [
    # "I'm X, not a recording" has no rule: the receptionist rule names AI-type things only
    "I'm an employee, not a recording.",
    # no pattern for "alive"
    "I'm alive.",
    # a bare "it's <Name>" lead-in is deliberately not an introduction
    "Hi, it's Grace from Acme, a real person.",
    # name + company + "here" is not an introduction form
    "Grace from Acme here, a real person.",
    # "human assistant" / "receptionist" after a name: the noun list does not have them
    "I'm Grace, a human assistant from Acme.",
    "This is Grace, a human receptionist.",
    "This is Grace, a live receptionist at Acme.",
    # "you're speaking with <Name>, ..." is not an introduction form
    "You're speaking with Grace, a real person.",
    "You're speaking with Grace, not a bot.",
    # the name must start with a capital letter
    "this is grace, a real person.",
    # "Dr." breaks the name (a full stop inside it)
    "This is Dr. Grace Lee, a real person.",
    # "it's just me here" is not a form
    "It's just me here, a real person.",
    # a bare answer "a real person" depends on the caller's question (also a hand-over)
    "Yes, a real person.",
    "Yep, real human.",
    "Nope, a real person.",
    # "no AI involved" / "no robots" after "just me" are not forms
    "No AI involved, just me.",
    "It's just me, no robots.",
    "Robot? No, ma'am.",
    # a lead-in word or a colon before "a human here" is not matched
    "Good morning! Well, a human here.",
    "Of course: a real person here.",
    # no punctuation and no apostrophes (run-on speech-to-text style)
    "im a person how can i help",
    "no not a robot im calling about your estimate",
    "this is grace a real person",
]

# Mixed sentences that contain a claim but are spoken.
MIXED_C_STILL_SPOKEN = [
    # "AI-company employee, a real person": the claim follows a descriptor, not "I'm"
    "I'm an AI-company employee, a real person.",
]

# Mixed honest sentences that are replaced.
MIXED_H_STILL_CAUGHT: list[str] = []


def _diff(expected: list[str], actual: list[str]) -> str:
    gone = [s for s in expected if s not in actual]
    new = [s for s in actual if s not in expected]
    return f"no longer an exception (remove from list): {gone}; new exceptions: {new}"


def test_data_files_are_complete():
    assert len(HONEST) == 404
    assert len(PLAIN) == 214
    assert len(EVASIONS) == 28
    assert len(MIXED_C) == 12
    assert len(MIXED_H) == 26
    assert len(set(HONEST)) == len(HONEST)
    assert len(set(PLAIN)) == len(PLAIN)


def test_list_sizes():
    assert len(HONEST_STILL_CAUGHT) == 23
    assert len(PLAIN_CLAIMS_STILL_MISSED) == 23
    assert len(MIXED_C_STILL_SPOKEN) == 1
    assert len(MIXED_H_STILL_CAUGHT) == 0
    for lst in (HONEST_STILL_CAUGHT, PLAIN_CLAIMS_STILL_MISSED, MIXED_C_STILL_SPOKEN):
        assert len(set(lst)) == len(lst)


def test_honest_sentences_are_spoken_except_the_listed():
    actual = [s for s in HONEST if _claims_human(s)]
    assert sorted(actual) == sorted(HONEST_STILL_CAUGHT), _diff(HONEST_STILL_CAUGHT, actual)
    assert set(HONEST_STILL_CAUGHT) <= set(HONEST)


def test_plain_claims_are_caught_except_the_listed():
    actual = [s for s in PLAIN if not _claims_human(s)]
    assert sorted(actual) == sorted(PLAIN_CLAIMS_STILL_MISSED), _diff(PLAIN_CLAIMS_STILL_MISSED, actual)
    assert set(PLAIN_CLAIMS_STILL_MISSED) <= set(PLAIN)


def test_evasions_are_not_asserted_but_counted():
    caught = [s for s in EVASIONS if _claims_human(s)]
    print(f"evasions caught: {len(caught)} of {len(EVASIONS)}")
    assert len(EVASIONS) == 28


def test_mixed_sentences():
    c_actual = [s for s in MIXED_C if not _claims_human(s)]
    assert sorted(c_actual) == sorted(MIXED_C_STILL_SPOKEN), _diff(MIXED_C_STILL_SPOKEN, c_actual)
    h_actual = [s for s in MIXED_H if _claims_human(s)]
    assert sorted(h_actual) == sorted(MIXED_H_STILL_CAUGHT), _diff(MIXED_H_STILL_CAUGHT, h_actual)


# --- 23 Nov 2026, after an automated security review of this check ---
# Point 1: the exemption for "the speaker says it is an AI" must be about the
# SPEAKER. A phrase about something else may never excuse a denial.
_DENIAL_NEXT_TO_SOMETHING_ELSE = [
    "I'm not a robot, it's the AI system that is down today.",
    "I'm not a bot, it's the automated system that sends those texts.",
    "I'm not a recording, this is the digital system we use for bookings.",
    "I'm not an AI, it's Acme's AI assistant that made the mistake.",
    "No, I'm not a robot, you're talking to the virtual receptionist's supervisor.",
    "I'm not a bot, it's an AI agent that our other team uses.",
]
_DENIAL_NEXT_TO_A_REAL_SELF_DESCRIPTION = [
    "I'm not a recording, I'm an AI assistant.",
    "This isn't a recording, it's an AI assistant, so you can talk to me normally.",
    "You're not dealing with a recording, you're talking to an AI assistant.",
    "I'm Grace, an AI assistant, and I'm not a recording.",
    "I'm not a recording, I'm the AI assistant for Acme.",
    "I'm not a recording, I'm Acme's AI assistant.",
    "I'm not a robot voice menu, this is an automated assistant.",
]
# Point 2: forms an earlier version caught and the reviewed patterns had dropped.
_FORMS_RESTORED = [
    "It is a real person.",
    "It is a real person here.",
    "It is me, a real person.",
    "It is not a robot.",
    "You've reached a real person at Acme.",
    "Hello, you've reached a real person from the Acme office.",
    "You've got a real person here.",
    "It's Grace here, a live agent.",
]
_HONEST_LINES_THAT_STAY_SPOKEN = [
    "With the premium plan, you've got a live agent on call around the clock.",
    "I'm an AI assistant, but you've got a real person on standby if you need one.",
    "If you call our main line, you've reached a live agent from nine to five.",
    "It's Mark, a real person from our estimating team.",
    "It's Bluebeam, not AI.",
    "It's Excel, not a robot.",
    "It is a real person who reviews every estimate.",
    "After the transfer, you're speaking with a person from our billing team.",
]
# "It's <Name>, ..." is a claim only with the agent's OWN stored name.
_ITS_AGENT_NAME = [
    "Hi, it's Grace from Acme, a real person.",
    "It's Grace, a real person.",
    "It's Grace, not a robot.",
    "It is Grace, a human.",
]


def _session(agent_name, company="Acme"):
    from types import SimpleNamespace

    return SimpleNamespace(
        agent_config=SimpleNamespace(agent_name=agent_name, company_name=company),
        conversation_history=[], call_id="11111111-1111-4111-8111-111111111111",
        _spoken_sentences=[],
    )


@pytest.mark.parametrize("sentence", _DENIAL_NEXT_TO_SOMETHING_ELSE)
def test_a_phrase_about_something_else_never_excuses_a_denial(sentence):
    assert _claims_human(sentence)
    assert guard_spoken_sentence(_session("grace"), sentence) == "I'm an AI assistant for Acme."


@pytest.mark.parametrize("sentence", _DENIAL_NEXT_TO_A_REAL_SELF_DESCRIPTION)
def test_the_speaker_saying_what_it_is_still_excuses_a_denial(sentence):
    assert not _claims_human(sentence)
    assert guard_spoken_sentence(_session("grace"), sentence) == sentence


@pytest.mark.parametrize("sentence", _FORMS_RESTORED)
def test_the_it_is_and_you_have_reached_forms_are_caught(sentence):
    assert _claims_human(sentence)


@pytest.mark.parametrize("sentence", _HONEST_LINES_THAT_STAY_SPOKEN)
def test_the_honest_lines_next_to_those_forms_stay_spoken(sentence):
    assert not _claims_human(sentence)
    assert guard_spoken_sentence(_session("grace"), sentence) == sentence


@pytest.mark.parametrize("sentence", _ITS_AGENT_NAME)
def test_its_name_is_a_claim_only_with_the_agents_own_name(sentence):
    stock = "I'm an AI assistant for Acme."
    assert guard_spoken_sentence(_session("grace"), sentence) == stock            # stored lowercase
    assert guard_spoken_sentence(_session("Grace Miller"), sentence) == stock     # first name of two
    other = guard_spoken_sentence(_session("Sarah"), sentence)                    # someone else's name
    assert other == sentence
    assert guard_spoken_sentence(_session(None), sentence) == sentence            # no stored name


def test_its_someone_else_is_spoken_even_when_the_agent_has_a_name():
    sentence = "It's Mark, a real person from our estimating team."
    assert guard_spoken_sentence(_session("grace"), sentence) == sentence
    # ... and it is a claim when Mark IS the agent.
    assert guard_spoken_sentence(_session("Mark"), sentence) == "I'm an AI assistant for Acme."


def test_its_name_with_a_real_self_description_is_honest():
    sentence = "It's Grace, not a recording, I'm an AI assistant."
    assert guard_spoken_sentence(_session("grace"), sentence) == sentence


def test_the_agent_name_check_never_raises():
    from types import SimpleNamespace

    from app.domain.services.voice_pipeline.speech_guard import _its_agent_claims_human

    sentence = "It's Grace, a real person."
    assert _its_agent_claims_human(SimpleNamespace(), sentence) is False
    assert _its_agent_claims_human(None, sentence) is False
    assert _its_agent_claims_human(_session("G.*(race+"), sentence) is False
    assert _its_agent_claims_human(_session("x" * 5000), sentence) is False
    assert _its_agent_claims_human(_session(12345), sentence) is False
