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

from app.domain.services.voice_pipeline.speech_guard import _claims_human

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
