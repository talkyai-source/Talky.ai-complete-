"""Opt-out wordings: ADDITIVE, with a STRICT CORE as the only new path (24 Nov 2026, fourth build).

The rules under test:
1. Whatever the 78f3ce42 function (main's) catches stays caught, at the same position, and nothing of the
   new path governs it (THE GATE, and the parity test against a frozen copy of main's module).
2. Only where the legacy function finds nothing does the new path run. It is a strict core: the WHOLE TURN is
   requests and courtesy sentences only; a request sentence is lead-ins, ONE core form and tails; a question
   is never a request. A reason, a question, a time, a name, a correction or another request attached means
   "not caught here": those stay with the language model, as on main. A wrong catch writes a PERMANENT entry.

Data under tests/unit/data/opt_out_wordings/: must_catch.txt, must_not_catch.txt, unclear.txt (rows
"sentence || decision || why"), holdout.txt and holdout2.txt (rows "C|" must be caught, "N|" must not),
review_catch.txt and review_nocatch.txt (first independent review), review2_requests.txt,
review2_ordinary.txt, review2_ordinary2.txt, review2_arguable.txt and review2_stop_and_ask.txt (second
independent review), review3_ordinary.txt and review3_requests.txt (third review, labelled rows), review4_ordinary.txt and
review4_requests.txt (fourth review, labelled rows), and the frozen copies of main: legacy_identity_disposition_78f3ce42.py.txt and
legacy_caller_assertions_main.py.txt (origin/main's caller_assertions.py, which the frozen module uses).

Every list below states what the code does today. A list is never a way to hide a fault: the test fails
when a listed sentence stops being an exception and when a new one appears.
"""
import hashlib
import importlib.machinery
import importlib.util
import re
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.services.voice_pipeline import identity_disposition as idn
from app.domain.services.voice_pipeline.identity_disposition import (
    _legacy_dnc_assertion_position, _new_dnc_assertion_position, contains_dnc, dnc_assertion_position,
)

DATA = Path(__file__).resolve().parent / "data" / "opt_out_wordings"


def _read(name):
    out = []
    with open(DATA / name, encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if line.strip() and not line.lstrip().startswith("#"):
                out.append(line)
    return out


def _load_from_file(filename, module_name):
    loader = importlib.machinery.SourceFileLoader(module_name, str(DATA / filename))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def _load_frozen_main():
    """Load the frozen identity_disposition of main so that it uses the FROZEN caller_assertions: the frozen
    caller_assertions is executed first and placed in sys.modules under the application's name for the
    duration of the frozen module's import statement, then the real entry is put back."""
    shared_filter_name = "app.domain.services.caller_assertions"
    frozen_assertions = _load_from_file("legacy_caller_assertions_main.py.txt", "frozen_caller_assertions_main")
    saved = sys.modules.get(shared_filter_name)
    sys.modules[shared_filter_name] = frozen_assertions
    try:
        module = _load_from_file("legacy_identity_disposition_78f3ce42.py.txt",
                                 "frozen_identity_disposition_78f3ce42")
    finally:
        if saved is None:
            sys.modules.pop(shared_filter_name, None)
        else:
            sys.modules[shared_filter_name] = saved
    return module


MAIN = _load_frozen_main()

MUST = _read("must_catch.txt")
MUST_NOT = _read("must_not_catch.txt")
UNCLEAR = [tuple(p.strip() for p in line.split("||")) for line in _read("unclear.txt")]
_H1 = [line.split("|", 1) for line in _read("holdout.txt")]
_H2 = [line.split("|", 1) for line in _read("holdout2.txt")]
H1_C = [s for k, s in _H1 if k == "C"]
H1_N = [s for k, s in _H1 if k == "N"]
H2_C = [s for k, s in _H2 if k == "C"]
H2_N = [s for k, s in _H2 if k == "N"]
REVIEW_C = _read("review_catch.txt")
REVIEW_N = _read("review_nocatch.txt")
R2_REQUESTS = _read("review2_requests.txt")
R2_ORDINARY = _read("review2_ordinary.txt")
R2_ORDINARY2 = _read("review2_ordinary2.txt")
R2_ARGUABLE = _read("review2_arguable.txt")
R2_STOP_AND_ASK = _read("review2_stop_and_ask.txt")
# Third review: rows "label|sentence". Ordinary: N = a catch is wrong, A = arguable. Requests: P = plain, X = with
# a reason or something else attached; every request row is a person asking not to be called.
R3_ORDINARY_LABELLED = [tuple(line.split("|", 1)) for line in _read("review3_ordinary.txt")]
R3_ORDINARY = [s for _, s in R3_ORDINARY_LABELLED]
R3_ORDINARY_LABEL = {s: k for k, s in R3_ORDINARY_LABELLED}
R3_REQUESTS = [line.split("|", 1)[1] for line in _read("review3_requests.txt")]
# Fourth review. Ordinary rows "label|kind|turn|context": label W wrong, A arguable, R real request (each "if
# caught"); the detector sees only the turn. Request rows "kind|turn": P plain, Q polite question, X reason attached.
R4_ORDINARY_ROWS = [tuple(line.split("|", 3)) for line in _read("review4_ordinary.txt")]
R4_ORDINARY = [row[2] for row in R4_ORDINARY_ROWS]
R4_REQUEST_ROWS = [tuple(line.split("|", 1)) for line in _read("review4_requests.txt")]
R4_REQUESTS = [row[1] for row in R4_REQUEST_ROWS]

REQUEST_FILES = {"must_catch": MUST, "holdout_C": H1_C, "holdout2_C": H2_C, "review_catch": REVIEW_C,
                 "review2_requests": R2_REQUESTS, "review3_requests": R3_REQUESTS}
ORDINARY_FILES = {"must_not_catch": MUST_NOT, "holdout_N": H1_N, "holdout2_N": H2_N, "review_nocatch": REVIEW_N,
                  "review2_ordinary": R2_ORDINARY, "review2_ordinary2": R2_ORDINARY2,
                  "review2_arguable": R2_ARGUABLE, "review3_ordinary": R3_ORDINARY}
ALL_SENTENCES = (
    [s for v in REQUEST_FILES.values() for s in v] + [s for v in ORDINARY_FILES.values() for s in v]
    + [s for s, _, _ in UNCLEAR] + R2_STOP_AND_ASK + R4_ORDINARY + R4_REQUESTS
)

# (a) per request file: (caught by the legacy function, caught ONLY by the new path, still missed).
REQUEST_COUNTS = {
    "must_catch": (90, 59, 62),
    "holdout_C": (14, 6, 20),
    "holdout2_C": (4, 6, 20),
    "review_catch": (228, 34, 47),
    "review2_requests": (75, 32, 168),
    "review3_requests": (37, 56, 77),
}
# Before the fifth round (review 4: bare remove/delete need the list part, eight answer/greeting courtesy entries
# and four question lead-ins removed) the same three numbers were: must_catch (90, 64, 57),
# review_catch (228, 38, 43), review2_requests (75, 37, 163), review3_requests (37, 69, 64); the others unchanged.
# Before the fourth build (strict core without the whole-turn rule) the same three numbers were:
# must_catch (90, 55, 66), holdout2_C (4, 9, 17), review_catch (228, 35, 46), review3_requests (37, 48, 85);
# holdout_C and review2_requests were unchanged.

# Requests the third-build core caught that the fourth build no longer catches (expected: a second sentence that
# is not courtesy, or an object that is not "me/us/here/this number/my number/this phone/my phone").
NO_LONGER_CAUGHT_BY_THE_FOURTH_BUILD = [
    'Never ring this house again.',
    'Please stop phoning this house.',
    'Stop ringing my mobile.',
    'Never phone my house again.',
    "Don't phone my mobile again.",
    "Don't call me again. After the last call I'm done with you.",
    'Stop ringing this house.',
    'Never call this house again.',
    "Don't phone this house again.",
    'Remove my number. Instead of calling people you should get a real job.',
    'Remove my number please. Can you send me an email to confirm?',
    'Remove my number. Email too.',
    'Remove my number. Text messages too.',
    "I'm busy right now. Quit calling me.",
]

# The sentences caught ONLY by the new path, across all request files, in file order (what the change adds).
ADDED_BY_THE_NEW_PATH = [
    'Stop ringing.',
    'Stop phoning me.',
    'Quit calling.',
    'No more calls.',
    'Never call here again.',
    'Take this number off your list.',
    'Put me on your do-not-call list.',
    'Please stop ringing.',
    'Stop phoning.',
    'Stop phoning this number.',
    'stop phoning us please',
    'Please stop phoning me.',
    'Stop phoning here.',
    'Quit calling me.',
    'quit calling me',
    'Quit calling this number.',
    'Quit ringing me.',
    'Quit phoning me.',
    'Stop contacting me.',
    'Please stop contacting us.',
    'dont ring here again',
    'dont phone here again',
    "Don't you ever call me again.",
    'dont you ever call here again',
    "Don't ever ring me again.",
    'never call here again please',
    'never call this number again',
    'Never ring here again.',
    'Never phone me again.',
    'never phone this number again',
    'No more calls, thank you.',
    'No more calls please.',
    'no more calls',
    'No more phone calls.',
    'No more calls from you.',
    'Remove my number from your list.',
    'Remove this number from your list.',
    'remove us from your list',
    'take this number off your list please',
    'Take us off your list.',
    'Take my name off your list.',
    'Get me off your list.',
    'get me off your calling list',
    'I want to be removed from your list.',
    "I'd like to be removed from your list.",
    'i want to be taken off your list',
    'Put me on the do-not-call list.',
    'Put me on your no call list.',
    "I don't want any more calls.",
    "I don't want these calls.",
    "I don't want your calls.",
    "I don't want you to call me again.",
    "I don't want to be called again.",
    "I don't want to be contacted again.",
    'I do not wish to be contacted.',
    "We don't want any more calls.",
    'look i dont want any more calls',
    "I don't want you to call here again.",
    "I don't want you to call me anymore.",
    "I don't want any more of these calls.",
    "Don't contact this number again.",
    'Cease calling me.',
    'Quit ringing this number.',
    'No more phone calls please, thanks.',
    "Please don't ring this number any more.",
    'i want you to quit ringing me',
    "Don't you dare ring me again.",
    'Get us off your list please.',
    'no more of these calls please',
    'Cease contacting me.',
    "I don't want you phoning here again.",
    'Stop phoning me.',
    'Stop phoning here.',
    'Stop contacting me.',
    'Quit calling me.',
    'Quit calling.',
    'Stop bloody calling me.',
    'Stop fucking calling me.',
    'Seriously, quit ringing me.',
    'Cease calling this number.',
    "Don't you dare call me again.",
    'Never call this number again.',
    'Never phone here again.',
    'Never ever call me again.',
    'Take this number off your list.',
    'Remove my number from your list.',
    'Remove my number from your system.',
    'Get me off your list.',
    'I want to be removed from your list.',
    'No more calls.',
    'No more calls please.',
    'No more calls, I mean it.',
    'No more phone calls.',
    'No more of these calls.',
    "I don't want any more calls.",
    "I don't want these calls.",
    "I don't want your calls.",
    "I don't want to be called again.",
    "I don't want you to call me again.",
    'Put me on your do-not-call list.',
    "Don't ring this number again.",
    "So, um, I'd like to be removed from your list.",
    "I'd like to be taken off your list.",
    'Put me on the no call list.',
    'Remove us from your list.',
    "Please don't ring this number again.",
    'I do not wish to be contacted again.',
    'Please cease all calls to this number.',
    "Please don't phone us again, thank you.",
    'Quit phoning me!',
    'Stop bloody phoning me.',
    "Don't you ever ring this number again.",
    'Never ever phone me again.',
    'Do not ring this number ever again.',
    'Never call here again.',
    "Don't ever phone here again.",
    'Stop fucking phoning me.',
    "Don't fucking ring here again.",
    'Stop f***ing ringing me.',
    'Stop effing phoning this number.',
    'Quit calling here.',
    'Please quit calling.',
    'I want you to quit calling me.',
    'yeah no stop phoning me please',
    'look just stop phoning me ok',
    'dont ring this number again thanks bye',
    'please remove my number from your list thanks',
    'um yeah no i dont want these calls anymore',
    'i said stop phoning me',
    'okay stop phoning me now',
    'no more calls please',
    'remove my number from your list',
    'take this number off your list',
    'please stop contacting me thanks',
    "Erm, yeah, I don't want any more calls, thanks.",
    'Honestly, just stop phoning me.',
    'Never phone me again, I mean it.',
    'Please stop phoning.',
    'Stop contacting me.',
    'Quit ringing me.',
    'Quit phoning this number.',
    'Cease contacting me.',
    'Stop ringing my phone.',
    'No more calls please.',
    'No more phone calls.',
    'No more calls from you, thanks.',
    'No more of your calls.',
    'Never ring here again.',
    'Never phone me again.',
    'Never call this number again.',
    "Don't ring this number again.",
    "Don't phone here again.",
    "Don't ever ring here again.",
    "Don't you ever phone me again.",
    'Do not contact this number again.',
    'Take us off your list.',
    'Remove us from your list.',
    "I don't want any more calls.",
    "We don't want any more calls.",
    "I don't want your calls.",
    "I don't want you to ring me again.",
    "I don't want you phoning me anymore.",
    'Listen, no more calls.',
    'For the last time, stop phoning me.',
    "I've told you, stop ringing.",
    'Honestly, just stop phoning me.',
    'Please, never ring here again.',
    'No more calls, I mean it.',
    "Stop phoning, I'm not interested.",
    'You need to stop phoning me.',
    'Cease all calls.',
    'Cease all calls to this number.',
    'Take my name off your list.',
    'Delete me from your list.',
    'Get me off your list.',
    'Get me off your calling list.',
    'I want to be taken off your list.',
    'I want to be removed from your list.',
    "I'd like to be removed from your list.",
    "I don't want to be called again.",
    "I don't want to be contacted again.",
    'I do not wish to be contacted.',
    'I do not wish to receive any more calls.',
    "I don't want calls from you.",
    "I don't want any calls from you.",
    "I don't want any more of these calls.",
    'no more calls im not interested',
    'please dont ring this number again thanks',
    'i dont want any more calls thank you',
    'No more calls. Bye.',
    'No more calls. Bye bye.',
    'Stop f***ing ringing me.',
    'Stop effing phoning me.',
]

# Ordinary sentences caught by the legacy function too (not made worse; the new path adds nothing here).
ORDINARY_CAUGHT_BY_LEGACY_TOO = {
    "must_not_catch": [
        "Don't call me on weekends.",
        'dont call me on the weekend',
        "Don't call me so early.",
        "Don't call me this early.",
        "Don't call me this late.",
        "Don't call me at night.",
        "Don't call me during dinner.",
        "Don't call me during the day, I work.",
        "Don't call me between nine and five.",
        'dont call me while im driving',
        "Don't call me when I'm at work.",
        "Don't call me on Sundays.",
        'dont ring me in the mornings',
        "Don't phone me during work hours.",
        "Don't call back now, call back later.",
        'dont call back till next week',
        'Do not call me at lunchtime.',
        "Don't call me for a couple of weeks, I'm away.",
        "Don't call me for the next month.",
        "Don't call me at the office.",
        "Don't call me Dave, my name is David.",
        "Don't call me Mr Smith, call me John.",
        "Stop calling me Mrs Jones, it's Ms Jones.",
        "Don't call me by my first name.",
        'Stop calling me by my old name.',
        "Please don't call me that.",
        'Stop calling me that.',
        "They don't call me boss for nothing.",
        'Can you stop calling it a discount?',
        "Stop calling it free, it isn't.",
        'My wife told them to stop calling.',
        "The last company said they'd stop calling.",
        "I've told my son to stop ringing me at work.",
        'He keeps telling me to stop calling him.',
        "My kids don't call anymore.",
        'You never call me back when I need you.',
        'Can you take me off speaker?',
        'take me off hold please',
        'Can you take me off speakerphone now?',
        'Can you take me off the group email but keep me on the phone list?',
        'Did you lose my number?',
        'Do not contact me by email, phone is better.',
        'Do I need to unsubscribe from the app first?',
        'They stop calling after a while.',
        'I wish they would stop calling.',
        "Don't call me now, call me later.",
        "Don't contact me by text, email is fine.",
        'Do not call me by my surname.',
    ],
    "holdout_N": [
        'Stop calling it a bargain, mate.',
        "Don't call me late at night please.",
        "Please don't call me during the school run.",
        "Don't call me at home, call me at the shop.",
        "Do not call me Mister, it's Doctor.",
        'You should stop calling it a free trial.',
        'Does the do not call list cover mobiles?',
    ],
    "holdout2_N": [
        "Don't ring us on Mondays, we're closed.",
        "I'd never contact this number without a reason.",
        'They never ring us back.',
        'Is there a do not call list I can join for other companies?',
        "Don't call me Sir, I'm a woman.",
    ],
    "review_nocatch": [
        "My old number's wrong, delete my number and use this one.",
        "Don't call me sweetheart.",
        "Don't call me that.",
        'Stop calling me mate.',
        'Stop calling it a discount.',
        'You never call me back.',
        'They never call us back.',
        "Don't call me at the office.",
        "Don't call me on Saturdays.",
        "Don't call me during work hours.",
        "Don't call me this early.",
        "Don't call me so late.",
        'Take me off hold please.',
        'Take me off speaker.',
        "No, don't call me now, I'm driving, call me in an hour.",
        "My kids don't call me anymore.",
        "My neighbour keeps calling, I wish he'd stop calling me.",
        'Can you take me off the direct debit?',
    ],
    "review2_ordinary": [
        'Stop calling my work phone, ring the mobile.',
        "Can the system stop ringing me before I've finished my shift?",
        "I'll tell my son to stop ringing me about it.",
        "Don't phone me late, phone me early.",
        "Delete my number from your records, it's a typo.",
        "Delete my number, the one ending 456, it's my old one.",
        'Put my mum on the do not call list, she keeps getting scam calls from other firms, but I still want yours.',
        "Shall we call you on this number? No, don't call this number, use my mobile.",
        "Would you like a call back? No, don't call back, I'll come in on Saturday.",
    ],
    "review2_ordinary2": [
        'Take my number off the WhatsApp group list.',
    ],
    "review2_arguable": [
    ],
    "review3_ordinary": [
        'Never call me. Before nine.',
        'Stop calling my landline. Call the mobile.',
        'Take my number off. The invoice I mean.',
    ],
}

# Ordinary sentences caught NEWLY (the legacy function does not catch them). The aim is an EMPTY list; these are
# the exceptions, found by the third review, each with the closed list that lets it through. They are N rows of
# review3_ordinary.txt (a catch is wrong): a caller ANSWERING the agent, or a lead-in/courtesy word in front of
# the form. The lead-in / courtesy lists were fixed by the brief; they are not widened or narrowed here.
ORDINARY_NEWLY_CAUGHT_N = {}
# Arguable (A) rows caught, with the reason each is arguably a request to stop.
ORDINARY_NEWLY_CAUGHT_A = {
    'No more calls.': "bare whole-sentence request to stop; the same words a caller answering a check could say",
    'Stop ringing.': "bare whole-sentence request to stop; could be about a bell or device",
}
ORDINARY_NEWLY_CAUGHT = list(ORDINARY_NEWLY_CAUGHT_N) + list(ORDINARY_NEWLY_CAUGHT_A)

# review2_stop_and_ask.txt: a request to stop plus a question or another request. The owner has that case
# pending; this only records what happens today (True = caught). Since the whole-turn rule a second sentence
# that is not courtesy keeps the strict core from catching; the one True row is caught by the legacy phrase list.
STOP_AND_ASK_CAUGHT = {
    'Remove my number, and who gave it to you?': False,
    'Remove my number and tell me where you got it.': False,
    'No more calls, but can you send me the quote?': False,
    'Quit calling me. What company is this anyway?': False,
    'Remove my number, but I want to complain, put me through to a manager.': False,
    'Quit ringing me, I want to speak to your supervisor.': False,
    'Stop phoning me, and I want my refund.': False,
    'Never phone me again, but I need the cancellation reference.': False,
    'Delete my details, then tell me what data you hold on me.': False,
    'No more calls, how do I cancel the direct debit?': False,
    "I don't want any more calls, but can you confirm my account is closed?": False,
    'Stop phoning me, I need to make a complaint.': False,
    "Remove my number. Also, my boiler's still broken, when's the engineer coming?": False,
    'Stop contacting me but I still want the parcel delivered.': False,
    'No more calls please, but I do want the brochure posted.': False,
    'Remove my number, and I want to know who your manager is.': False,
    'Quit phoning me, and put me through to someone in charge.': False,
    'Remove my details, and give me your company address.': False,
    "Stop phoning me, what's your company registration number?": False,
    'Never phone me again, and tell me your name for my complaint.': False,
    'No more calls, I want my account closed today.': False,
    'Take my number off your list, and cancel my order too.': True,
    "I don't want any more calls from you, but I want my deposit back.": False,
    "Remove my number, I'll still use the shop though.": False,
    "Stop phoning me, I'd like a copy of my data under GDPR.": False,
}

UNCLEAR_EXCEPTIONS = [
    "Don't call me sweetheart, it's patronising.",
    "I'm registered with the TPS.",
    'Is my number on your do not call list?',
    'I want to be removed from your list if this is a sales call.',
    "If you call again I'll report you.",
    "I don't want to hear from you.",
    'Enough calls.',
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
    assert len(REVIEW_C) == 309 and len(REVIEW_N) == 122
    assert len(R2_REQUESTS) == 275 and len(R2_ORDINARY) == 263
    assert len(R2_ORDINARY2) == 32 and len(R2_ARGUABLE) == 53
    assert len(R2_STOP_AND_ASK) == 25
    assert len(R3_ORDINARY) == 233 and len(R3_REQUESTS) == 170
    assert len(R4_ORDINARY) == 219 and len(R4_REQUESTS) == 182
    assert len(set(R3_ORDINARY)) == len(R3_ORDINARY)
    assert len(set(MUST)) == len(MUST)
    assert len(set(MUST_NOT)) == len(MUST_NOT)


# --------------------------------------------------------------------------- the gate and the parity

def test_gate_every_sentence_the_legacy_function_catches_is_caught_at_the_same_position():
    assert len(ALL_SENTENCES) > 1800
    exceptions = []
    for s in ALL_SENTENCES:
        legacy = _legacy_dnc_assertion_position(s)
        if legacy >= 0 and dnc_assertion_position(s) != legacy:
            exceptions.append((s, legacy, dnc_assertion_position(s)))
    assert exceptions == []


def test_legacy_function_equals_the_frozen_function_of_main_on_every_sentence():
    assert len(ALL_SENTENCES) > 1800
    mismatches = [s for s in ALL_SENTENCES
                  if _legacy_dnc_assertion_position(s) != MAIN.dnc_assertion_position(s)]
    assert mismatches == []


def test_the_frozen_module_uses_the_frozen_caller_assertions_not_the_live_one():
    from app.domain.services import caller_assertions as live

    assert MAIN.assertion_matches.__module__ == "frozen_caller_assertions_main"
    assert MAIN.assertion_matches.__globals__["__name__"] == "frozen_caller_assertions_main"
    assert MAIN.assertion_matches is not live.assertion_matches
    assert sys.modules["app.domain.services.caller_assertions"] is live


@pytest.mark.parametrize("text", [
    "", "   ", "Stop calling me.", "STOP CALLING ME", "Don't call me tomorrow.", "Don't call me, call me later.",
    "I told them to stop calling.", "Stop calling it a discount.", "Don't call me Mrs Jones.",
    "You never call me back.", "Take me off hold.", "Say \"stop calling me\" again", "x" * 3000,
    "Don’t call me now.", "stop-calling me", "why do you keep calling me stop calling me",
])
def test_legacy_function_equals_the_frozen_function_of_main_on_extra_inputs(text):
    assert _legacy_dnc_assertion_position(text) == MAIN.dnc_assertion_position(text)


def test_legacy_function_handles_none_like_main():
    assert _legacy_dnc_assertion_position(None) == MAIN.dnc_assertion_position(None) == -1


def test_the_other_public_names_are_those_of_main():
    for name in ("contains_dnc", "dnc_assertion_position", "contains_explicit_goodbye"):
        assert callable(getattr(idn, name)) and callable(getattr(MAIN, name))
    for s in ("Goodbye.", "bye-bye", "Bye bye, thanks.", "kiss your money bye-bye", "I have to go now", "hello"):
        assert idn.contains_explicit_goodbye(s) == MAIN.contains_explicit_goodbye(s)
    assert idn._DNC_PHRASES == MAIN._DNC_PHRASES
    assert idn._DNC_PATTERN.pattern == MAIN._DNC_PATTERN.pattern


def test_the_grammar_path_is_gone_and_the_strict_core_is_one_anchored_pattern():
    for gone in ("_DNC_FORMS", "_NEW_DNC_PATTERN", "_prepare", "_RUN_ON", "_CONTINUATION", "_ADDRESSED_TO_US",
                 "_SCHEDULABLE_PHRASE", "_STATEMENT_HEAD", "_THIRD_PARTY_BEFORE"):
        assert not hasattr(idn, gone), gone
    pattern = idn._SENTENCE_REQUEST.pattern
    assert "(?<" not in pattern and "(?=" not in pattern and "(?!" not in pattern
    # \s* and the escaped literal asterisks of the masked words ("f***ing") are the only stars allowed
    assert "{0,3}" in pattern and "*" not in pattern.replace(r"\s*", "").replace(r"\*", "") and "+)+" not in pattern
    assert idn._SENTENCE_REQUEST.fullmatch("stop calling me")
    assert not idn._SENTENCE_REQUEST.fullmatch("stop calling me because I am busy")


# --------------------------------------------------------------------------- (b) requests

@pytest.mark.parametrize("name", list(REQUEST_FILES))
def test_requests_caught_by_legacy_caught_only_by_the_new_path_and_missed(name):
    rows = REQUEST_FILES[name]
    legacy = sum(1 for s in rows if _legacy_dnc_assertion_position(s) >= 0)
    only_new = sum(1 for s in rows if _legacy_dnc_assertion_position(s) < 0 and _new_dnc_assertion_position(s) >= 0)
    missed = sum(1 for s in rows if not contains_dnc(s))
    assert (legacy, only_new, missed) == REQUEST_COUNTS[name]
    assert legacy + only_new + missed == len(rows)


def test_the_sentences_the_new_path_adds_are_exactly_the_listed():
    actual = [s for rows in REQUEST_FILES.values() for s in rows
              if _legacy_dnc_assertion_position(s) < 0 and _new_dnc_assertion_position(s) >= 0]
    assert actual == ADDED_BY_THE_NEW_PATH, _diff(ADDED_BY_THE_NEW_PATH, actual)


# --------------------------------------------------------------------------- (a) ordinary sentences

@pytest.mark.parametrize("name", list(ORDINARY_FILES))
def test_ordinary_sentences_caught_by_the_legacy_function_too_are_exactly_the_listed(name):
    actual = [s for s in ORDINARY_FILES[name] if _legacy_dnc_assertion_position(s) >= 0]
    assert actual == ORDINARY_CAUGHT_BY_LEGACY_TOO[name], _diff(ORDINARY_CAUGHT_BY_LEGACY_TOO[name], actual)


def test_ordinary_sentences_newly_caught_are_exactly_the_listed():
    actual = [s for rows in ORDINARY_FILES.values() for s in rows
              if _new_dnc_assertion_position(s) >= 0 and _legacy_dnc_assertion_position(s) < 0]
    assert actual == ORDINARY_NEWLY_CAUGHT, _diff(ORDINARY_NEWLY_CAUGHT, actual)


def test_newly_caught_ordinary_sentences_are_only_third_review_rows_with_their_labels():
    # Every older ordinary file (and the N rows of holdout files) has NONE newly caught.
    for name, rows in ORDINARY_FILES.items():
        if name != "review3_ordinary":
            assert [s for s in rows if _new_dnc_assertion_position(s) >= 0
                    and _legacy_dnc_assertion_position(s) < 0] == [], name
    assert {s for s in ORDINARY_NEWLY_CAUGHT_N if R3_ORDINARY_LABEL[s] == "N"} == set(ORDINARY_NEWLY_CAUGHT_N)
    assert {s for s in ORDINARY_NEWLY_CAUGHT_A if R3_ORDINARY_LABEL[s] == "A"} == set(ORDINARY_NEWLY_CAUGHT_A)
    assert (len(ORDINARY_NEWLY_CAUGHT_N), len(ORDINARY_NEWLY_CAUGHT_A)) == (0, 2)
    assert all(ORDINARY_NEWLY_CAUGHT_N.values()) and all(ORDINARY_NEWLY_CAUGHT_A.values())


def test_nothing_else_is_caught_among_the_ordinary_sentences():
    for name, rows in ORDINARY_FILES.items():
        expected = set(ORDINARY_CAUGHT_BY_LEGACY_TOO[name]) | {s for s in rows if s in ORDINARY_NEWLY_CAUGHT}
        assert {s for s in rows if contains_dnc(s)} == expected


def test_review3_ordinary_n_rows_newly_caught_before_and_after_the_counts():
    n_rows = [s for k, s in R3_ORDINARY_LABELLED if k == "N"]
    a_rows = [s for k, s in R3_ORDINARY_LABELLED if k == "A"]
    new_only = lambda s: _legacy_dnc_assertion_position(s) < 0 and _new_dnc_assertion_position(s) >= 0  # noqa: E731
    assert (len(n_rows), len(a_rows)) == (213, 20)
    assert sum(1 for s in n_rows if new_only(s)) == 0 and sum(1 for s in a_rows if new_only(s)) == 2
    assert sum(1 for s in n_rows if _legacy_dnc_assertion_position(s) >= 0) == 3
    assert sum(1 for s in a_rows if _legacy_dnc_assertion_position(s) >= 0) == 0


# Requests the fifth build (own lead-ins and courtesy for form C4, no "sorry"/"understood"/"got it") stopped
# catching: the only one is a general lead-in that is not a C4 lead-in.
NO_LONGER_CAUGHT_BY_THE_FIFTH_BUILD = ['Seriously, no more calls.']


def test_requests_no_longer_caught_by_the_fifth_build_are_exactly_the_listed():
    all_requests = [s for rows in REQUEST_FILES.values() for s in rows]
    lost = [s for s in all_requests if s in NO_LONGER_CAUGHT_BY_THE_FIFTH_BUILD]
    assert lost == NO_LONGER_CAUGHT_BY_THE_FIFTH_BUILD
    assert all(_legacy_dnc_assertion_position(s) < 0 and _new_dnc_assertion_position(s) == -1 for s in lost)


def test_requests_no_longer_caught_by_the_fourth_build_are_exactly_the_listed():
    assert all(_legacy_dnc_assertion_position(s) < 0 and _new_dnc_assertion_position(s) == -1
               for s in NO_LONGER_CAUGHT_BY_THE_FOURTH_BUILD)
    assert len(NO_LONGER_CAUGHT_BY_THE_FOURTH_BUILD) == 14


def test_unclear_rows_follow_the_designers_decisions_except_the_listed():
    actual = [s for s, d, _ in UNCLEAR if contains_dnc(s) != (d == "catch")]
    assert actual == UNCLEAR_EXCEPTIONS, _diff(UNCLEAR_EXCEPTIONS, actual)


# --------------------------------------------------------------------------- (c) stop and ask

def test_stop_and_ask_rows_are_recorded_one_by_one():
    actual = {s: contains_dnc(s) for s in R2_STOP_AND_ASK}
    assert actual == STOP_AND_ASK_CAUGHT


# --------------------------------------------------------------------------- (d) rows per core form

# (sentence, the text at which the core form starts). The exact position is asserted on the new path alone,
# so the row stays valid whether or not the legacy function would catch the same words.
CORE_ROWS = {
    "C1": [("Quit calling me.", "Quit"), ("Please, cease bloody phoning here!", "cease"),
           ("look, just quit contacting this number", "quit"), ("Stop ringing.", "Stop")],
    "C2": [("Don't ever ring me again.", "Don't"), ("Please do not phone this number anymore", "do not"),
           ("dont you dare contact my phone ever again", "dont"), ("Don't you ever call me again", "Don't")],
    "C3": [("Never call me again.", "Never"), ("Never ever phone this number", "Never"),
           ("Yeah, never ring my phone ever again.", "never")],
    "C4": [("No more calls.", "No"), ("Please, no more of these phone calls from you!", "no more"),
           ("No more calls please.", "No"), ("Please, no more calls.", "no more"),
           ("No more calls. Thank you.", "No"), ("Look, no more calls from you.", "no more"),
           ("no more of your calls please", "no more")],
    "C5": [("Remove my number from your list.", "Remove"), ("Delete this number from your system", "Delete"),
           ("Please take my number off your marketing list.", "take"),
           ("Remove this number from your records please.", "Remove")],
    "C6": [("Remove me from your list.", "Remove"), ("Take us off your call list please", "Take"),
           ("take me off your list", "take")],
    "C7": [("Put me on the do not call list.", "Put"), ("put my number on your dnc list", "put"),
           ("Please put us on the no call list!", "put")],
    "C8": [("I don't want any more calls.", "I don't"), ("We do not want your calls, thanks.", "We do"),
           ("Honestly I dont want these calls anymore", "I dont")],
    "C9": [("I don't want you to call me again.", "I don't"), ("We do not want you phoning here anymore.", "We do"),
           ("I dont want you to ring this number ever", "I dont")],
}
CORE_ROW_LIST = [(form, s, needle) for form, rows in CORE_ROWS.items() for s, needle in rows]


@pytest.mark.parametrize("form,text,needle", CORE_ROW_LIST)
def test_each_core_form_is_caught_with_the_exact_position(form, text, needle):
    assert _new_dnc_assertion_position(text) == text.index(needle)
    assert contains_dnc(text) is True


ECHO_OR_ASIDE_ROWS = ["No. No more calls.", "Sorry. Stop ringing.", "No, no more calls.", "Yeah, no more calls.",
                      "Honestly, no more calls.", "So, no more calls.", "Right, so no more calls.",
                      "So no more calls, understood."]


@pytest.mark.parametrize("text", ECHO_OR_ASIDE_ROWS)
def test_an_echo_or_an_aside_is_not_a_request_to_stop(text):
    assert _new_dnc_assertion_position(text) == -1 and contains_dnc(text) is False


def test_form_c4_has_its_own_leadins_and_a_stricter_courtesy_list():
    assert set(idn._C4_LEADINS) == {"please", "look", "listen", "just", "i said"}
    assert set(idn._C4_COURTESY) <= set(idn._CORE_COURTESY)
    assert _new_dnc_assertion_position("No more calls. Thank you.") == 0
    assert _new_dnc_assertion_position("Okay. No more calls.") == -1
    assert _new_dnc_assertion_position("No more calls. Sorry.") == -1
    assert _new_dnc_assertion_position("Yes. No more calls.") == -1
    # other forms keep the general courtesy list and lead-ins
    assert _new_dnc_assertion_position("Yeah, stop ringing.") == 6  # "yeah" is a lead-in, not a courtesy sentence
    assert _new_dnc_assertion_position("Okay. Stop ringing.") == 6


def test_every_core_form_has_at_least_three_rows():
    assert all(len(rows) >= 3 for rows in CORE_ROWS.values()) and len(CORE_ROWS) == 9


# Sentences that contain a core form's words and are NOT requests to stop: review findings 1 to 8 and more.
NOT_CAUGHT_ROWS = [
    # a continuation after a comma (another way, another number, another time)
    "No, don't ring back, I'll ring you.",
    "Can you not call me now, I'm driving, call me in an hour.",
    "Quit ringing me at work, ring me at home.",
    "Quit phoning me, I'm busy, phone me tomorrow.",
    "Stop ringing my mobile, ring the landline.",
    "Stop phoning me, I'll phone you.",
    "Quit calling me, email me instead.",
    "Remove my number, it's wrong.",
    "Take this number off, it's the wrong one.",
    "Delete this number, I gave you the wrong one.",
    "No, remove my number and use this one instead.",
    # a question
    "Can you stop phoning me?",
    "Could you quit calling me?",
    "Will you remove my number?",
    "Can I remove my number from your list?",
    "Do you quit calling people who ask?",
    "Why don't you ever call me again?",
    # "back"
    "Don't ring back.",
    "Never ring back.",
    "Quit calling back.",
    "Stop phoning back before nine.",
    # a time
    "Don't ring this number again before nine.",
    "Don't phone me again until Monday.",
    "No more calls after six.",
    "Quit phoning me tonight.",
    "Quit ringing this number at the weekend.",
    "I don't want any more calls tonight.",
    "No more calls from you today.",
    # another list
    "Remove my name from the waiting list.",
    "Remove my number from the waiting list.",
    "Take this number off the mailing list.",
    "Take us off the guest list.",
    "Put me on the waiting list.",
    "Put me on the guest list.",
    "Take this number off the dinner list.",
    # a name
    "Quit calling me Dave.",
    "Remove my number, it's Sarah Jones.",
    "Stop phoning me Mr Smith.",
    # another company, or a third party
    "Tell Sky to stop phoning me.",
    "I want the bank to quit ringing me.",
    "I told them to quit calling me.",
    "My mum doesn't want any more calls.",
    "Quit calling me about the insurance company.",
    # texting is not calling
    "Quit texting me.",
    "Stop messaging me.",
    "No more texts.",
    "Remove my number from your text list.",
    "I don't want any more texts.",
    "Never text me again.",
    # a correction, a reason, an extra request
    "Remove my number from the form.",
    "We don't want you to call here before nine again.",
    "Stop phoning me, I'm sick of it.",
    "Remove my number because I moved house.",
    "No more calls, put me through to a manager.",
]


@pytest.mark.parametrize("text", NOT_CAUGHT_ROWS)
def test_a_sentence_with_a_core_forms_words_but_more_attached_is_left_alone(text):
    assert _legacy_dnc_assertion_position(text) < 0
    assert _new_dnc_assertion_position(text) == -1
    assert contains_dnc(text) is False


def test_there_are_at_least_forty_rows_that_must_not_be_caught():
    assert len(NOT_CAUGHT_ROWS) >= 40 and len(set(NOT_CAUGHT_ROWS)) == len(NOT_CAUGHT_ROWS)


def test_a_question_is_never_a_request_even_when_it_is_a_core_form():
    assert _new_dnc_assertion_position("Stop phoning me?") == -1
    assert _new_dnc_assertion_position("Stop phoning me!") == 0
    assert _new_dnc_assertion_position("Stop phoning me.") == 0


def test_the_last_qualifying_sentence_wins_and_the_offset_is_in_the_original_text():
    text = "Okay. Quit calling me. Thanks. Bye. Remove my number from your list!"
    assert _new_dnc_assertion_position(text) == text.index("Remove")
    assert _new_dnc_assertion_position("Quit calling me. What company is this anyway?") == -1
    assert _new_dnc_assertion_position("I’m busy right now. Quit calling me.") == -1
    assert _new_dnc_assertion_position("Thanks.\nQuit calling me.") == len("Thanks.\n")
    assert _new_dnc_assertion_position("") == -1 and _new_dnc_assertion_position(None) == -1


def test_at_most_three_lead_ins_and_three_tails():
    assert _new_dnc_assertion_position("please just look quit calling me") == len("please just look ")
    assert _new_dnc_assertion_position("please just look okay quit calling me") == -1
    assert _new_dnc_assertion_position("quit calling me please thanks okay") == 0
    assert _new_dnc_assertion_position("quit calling me please thanks okay alright") == -1


def test_the_forms_that_need_an_object_and_an_again_word_need_both():
    assert _new_dnc_assertion_position("don't call me") == -1
    assert _new_dnc_assertion_position("don't call again") == -1
    assert _new_dnc_assertion_position("don't call me again") == 0
    assert _new_dnc_assertion_position("I don't want you to call me") == -1
    assert _new_dnc_assertion_position("never call back") == -1


# --------------------------------------------------------------------------- fourth build: fixes F1 to F9

# Sentences (from the third review) that are NOT caught since the fix named by the key; legacy catches none.
F_NOT_CAUGHT = {
    "F1 bare take my number is giving it": [
        "Take my number.", "Yeah, take my number.", "Can you take my number.", "Take this number. It's my direct line.",
        "I'd like you to take my number.",
    ],
    "F2 a time follows, or now/right now after a form other than C1": [
        "Don't call me again. Until Friday.", "Don't call me again. Tomorrow.", "Do not call me again. After six.",
        "No more calls. Today.", "I'm driving. No more calls right now.", "Don't call me again right now.",
        "No more calls right now, thanks.",
    ],
    "F3 another sentence that is not courtesy": [
        "Remove my number. Actually no, keep it.", "Stop ringing. Sorry, the kids are on the doorbell.",
        "The guest list for Saturday. Take us off the list.", "Remove my number. From the old account.",
        "The sign on the gate. No more calls.", "Stop ringing my phone. Use the landline.",
    ],
    "F4 any calls alone": [
        "Data only. I don't want any calls.", "I don't want any calls.",
        "The SIM is for my tablet. I don't want any calls.", "I don't want any calls thanks.",
    ],
    "F5 a name tail": [
        "Quit calling me mate.", "Quit calling me love.", "Quit calling me pal.", "Stop phoning me love.",
    ],
    "F6 quoted text": [
        'He wrote: "Hi. Remove my number. Thanks."', '"Remove my number."', '"Quit calling me" he said.',
    ],
    "F7 not our list, not our number": [
        "Take us off the list.", "Remove us from the list.", "Take this number off the list.",
        "Stop ringing my mobile.", "Never phone my house again.", "Stop ringing my landline.",
        "Never call this house again.",
    ],
    "F8 a question mark ends the sentence": [
        "Stop phoning me?", "Stop phoning me!?", "Stop phoning me?!", "Remove my number?",
    ],
}
F_STILL_CAUGHT = {
    "F1": ("Take my number off your list.", "Take"),
    "F2": ("Stop ringing me now.", "Stop"),
    "F3": ("Remove my number from your list. Thanks.", "Remove"),
    "F4": ("I don't want any more calls.", "I don't"),
    "F5": ("Quit calling me please.", "Quit"),
    "F6": ('Remove my number from your list "bye".', "Remove"),
    "F7": ("Take us off your list.", "Take"),
    "F8": ("Stop phoning me!", "Stop"),
}
F_NOT_CAUGHT_LIST = [(rule, s) for rule, rows in F_NOT_CAUGHT.items() for s in rows]


@pytest.mark.parametrize("rule,text", F_NOT_CAUGHT_LIST)
def test_the_fixed_rules_leave_these_sentences_to_the_model(rule, text):
    assert _legacy_dnc_assertion_position(text) < 0
    assert _new_dnc_assertion_position(text) == -1
    assert contains_dnc(text) is False


@pytest.mark.parametrize("rule", list(F_STILL_CAUGHT))
def test_beside_each_fixed_rule_one_request_is_still_caught(rule):
    text, needle = F_STILL_CAUGHT[rule]
    assert _new_dnc_assertion_position(text) == text.index(needle)
    assert contains_dnc(text) is True


def test_every_fixed_rule_has_at_least_three_not_caught_rows():
    assert len(F_NOT_CAUGHT) == 8 and all(len(rows) >= 3 for rows in F_NOT_CAUGHT.values())


def test_the_review_sentences_of_the_rules_are_data_rows():
    # The F1 to F8 rows that come from review3_ordinary.txt are rows of that file, label N.
    for s in ("Take my number.", "Yeah, take my number.", "Can you take my number.", "Don't call me again. Until Friday.",
              "Remove my number. Actually no, keep it.", "Data only. I don't want any calls.", "Quit calling me mate.",
              "Take us off the list.", "Stop ringing my mobile. Use the landline."):
        assert s in R3_ORDINARY


def test_every_courtesy_entry_may_stand_beside_a_request_and_nothing_else_may():
    assert len(idn._CORE_COURTESY) == 19
    assert "sorry" not in idn._CORE_COURTESY
    assert not {"understood", "got it"} & set(idn._CORE_TAILS)
    for entry in idn._CORE_COURTESY:
        assert _new_dnc_assertion_position(f"Stop ringing. {entry.capitalize()}.") == 0, entry
        assert _new_dnc_assertion_position(f"{entry.capitalize()}. Stop ringing") == len(entry) + 2, entry
        assert _new_dnc_assertion_position(f"Stop ringing. {entry.capitalize()}?") == -1, entry
    assert _new_dnc_assertion_position("Stop ringing. Thanks. Bye. Goodbye.") == 0
    assert _new_dnc_assertion_position("Stop ringing. Thanks for your time.") == -1
    assert _new_dnc_assertion_position("Stop ringing. Thanks, bye.") == -1


def test_the_masked_and_apostrophe_free_spellings_are_matched_like_the_written_ones():
    for a, b in (("I don't want any more calls.", "i dont want any more calls"),
                 ("Don't ever ring me again.", "dont ever ring me again"),
                 ("I'd like you to remove my number from your list.", "id like you to remove my number from your list"),
                 ("I've told you, stop ringing me.", "ive told you stop ringing me")):
        assert _new_dnc_assertion_position(a) >= 0 and _new_dnc_assertion_position(b) >= 0


# (rule, sentence, text at which the core form starts): the exact position is asserted on the new path alone.
F9_ROWS = [
    ("N1 get me off your list", "Get me off your list.", "Get"),
    ("N1 get me off your list", "get us off your calling list please", "get"),
    ("N1 get me off your list", "Okay, get me off your list.", "get"),
    ("N2 delete me from your list", "Delete me from your list.", "Delete"),
    ("N2 delete me from your list", "delete us from your list please", "delete"),
    ("N2 delete me from your list", "Please delete me from your list.", "delete"),
    ("N3 take my name off your list", "Take my name off your list.", "Take"),
    ("N3 take my name off your list", "please take my name off your list", "take"),
    ("N3 take my name off your list", "Just take my name off your list thanks", "take"),
    ("N4 remove my name from your list", "Remove my name from your list.", "Remove"),
    ("N4 remove my name from your list", "Remove my name from your marketing list.", "Remove"),
    ("N4 remove my name from your list", "please remove my name from your calling list", "remove"),
    ("N5 I want to be removed", "I want to be removed from your list.", "I want"),
    ("N5 I want to be removed", "We'd like to be taken off your call list.", "We'd"),
    ("N5 I want to be removed", "i would like to be removed off your list please", "i would"),
    ("N6 I do not wish", "I do not wish to be contacted.", "I do not"),
    ("N6 I do not wish", "We don't wish to receive any more calls.", "We don't"),
    ("N6 I do not wish", "i do not wish to receive any more calls thanks", "i do not"),
    ("N7 I don't want to be called again", "I don't want to be called again.", "I don't"),
    ("N7 I don't want to be called again", "we do not want to be contacted again", "we do"),
    ("N7 I don't want to be called again", "Honestly I dont want to be called again.", "I dont"),
    ("N8 cease all calls", "Cease all calls.", "Cease"),
    ("N8 cease all calls", "Please cease all calls to this number.", "cease"),
    ("N8 cease all calls", "cease all calls to this number thanks", "cease"),
    ("C8b calls from you", "I don't want calls from you.", "I don't"),
    ("C8b calls from you", "We do not want any calls from you.", "We do"),
    ("C8b calls from you", "I don't want any more of these calls.", "I don't"),
    ("lead-in i've told you / not interested", "I've told you stop ringing me.", "stop"),
    ("lead-in i've told you / not interested", "I have told you remove my number from your list", "remove"),
    ("lead-in i've told you / not interested", "Not interested remove my number from your list.", "remove"),
    ("tail bye / goodbye / bye bye / not interested", "Stop ringing me bye.", "Stop"),
    ("tail bye / goodbye / bye bye / not interested", "Remove my number from your list goodbye", "Remove"),
    ("tail bye / goodbye / bye bye / not interested", "No more calls bye bye", "No more"),
    ("tail bye / goodbye / bye bye / not interested", "Remove my number from your list not interested", "Remove"),
    ("masked intensifier", "Stop f***ing ringing me.", "Stop"),
    ("masked intensifier", "quit f**king calling me", "quit"),
    ("masked intensifier", "Stop f*cking phoning me.", "Stop"),
    ("apostrophe optional", "dont ring this number again", "dont"),
    ("apostrophe optional", "i dont want any more calls", "i dont"),
    ("apostrophe optional", "id like you to remove my number from your list", "remove"),
]


@pytest.mark.parametrize("rule,text,needle", F9_ROWS)
def test_each_safe_addition_is_caught_with_the_exact_position(rule, text, needle):
    assert _new_dnc_assertion_position(text) == text.index(needle)
    assert contains_dnc(text) is True


def test_every_new_form_of_the_safe_additions_has_three_rows():
    for rule in ("N1", "N2", "N3", "N4", "N5", "N6", "N7", "N8", "C8b"):
        assert sum(1 for r, _, _ in F9_ROWS if r.startswith(rule)) >= 3, rule


def test_a_list_form_that_is_not_ours_or_has_more_attached_is_not_caught():
    for text in ("Get me off the list.", "Delete me from the guest list.", "Take my name off the list.",
                 "Remove my name from the waiting list.", "I want to be removed from the list.",
                 "I do not wish to be contacted tomorrow.", "I don't want to be called again before nine.",
                 "Cease all calls to my mobile."):
        assert _new_dnc_assertion_position(text) == -1, text


def test_today_phrase_list_is_kept_entry_for_entry():
    for phrase in ("stop calling", "dont call me", "take me off", "never contact", "fuck you", "do not call list"):
        assert phrase in idn._DNC_PHRASES
    assert len(idn._DNC_PHRASES) == 51


@pytest.mark.parametrize("text,needle", [
    ("Okay. Quit calling me.", "Quit calling me"),
    ("Please do-not-call this number again", "do-not-call this number again"),
    ("Please don’t ring this number again.", "don’t"),
    ("Please quit-calling me now", "quit-calling"),
])
def test_position_is_the_index_in_the_original_text(text, needle):
    assert dnc_assertion_position(text) == text.index(needle)


@pytest.mark.parametrize("text", [
    "Don't call me tomorrow. Stop calling me.",
    "Don't call me Mrs Jones, stop calling me.",
])
def test_legacy_position_is_returned_untouched_and_is_the_last_catch(text):
    assert dnc_assertion_position(text) == _legacy_dnc_assertion_position(text) == MAIN.dnc_assertion_position(text)


# Ordinary sentences the legacy function decides: caught, at the legacy position (the additive rule).
LEGACY_ROWS = [
    "Don't call me on weekends.", "Don't call me at night.", "Don't call me during lunch.",
    "Don't call me next week.", "Don't call me now, call me later.", "Can I opt out of the recording?",
    "My kids don't call anymore.", "I'll remove this number from my contacts.",
    "I told them to stop calling.", "My husband asked them to remove our number.",
    "Stop calling it a discount.", "Don't call me Mrs Jones.", "Stop calling me that.",
    "Take me off hold.", "Take me off speaker please.", "You never call me back.", "Did you lose my number?",
]


@pytest.mark.parametrize("text", LEGACY_ROWS)
def test_rows_of_the_first_attempt_that_the_legacy_function_decides(text):
    legacy = _legacy_dnc_assertion_position(text)
    assert legacy == MAIN.dnc_assertion_position(text)
    assert dnc_assertion_position(text) == legacy


@pytest.mark.parametrize("text", [
    "Stop calling me.",
    "Don't call me again.",
    "Please never call me again.",
    "Take me off your list.",
    "Remove my number from your list.",
    "Don't ever call me again, you lose my number.",
    "Put me on the do-not-call list.",
    "Stop calling me right now.",
    "Quit calling.",
    "Please never call me back.",
    "Never call me back. Ever.",
    "Stop calling me, my name is Sarah Jones.",
    "Don't call me again, this time I mean it.",
    "I said to stop calling me.",
    "Your people stop calling me.",
    "Stop calling it’s ridiculous.",
])
def test_requests_next_to_the_guards_are_still_caught(text):
    assert contains_dnc(text) is True


def test_a_later_request_still_wins_over_an_earlier_guarded_phrase():
    assert contains_dnc("Don't call me tomorrow. Please stop calling me.") is True


# --------------------------------------------------------------------------- speed (wide margins)

def _best_of_three(fn):
    best = 1e9
    for _ in range(3):
        t0 = time.perf_counter()
        fn()
        best = min(best, time.perf_counter() - t0)
    return best


def _fill(unit: str, size: int = 5000) -> str:
    return (unit * (size // len(unit) + 1))[:size]


ORDINARY_CHUNK = "Hello there, I am just asking about the price of the plan and the delivery. "
FORM_UNITS = [
    "Stop calling me ", "Don't ever call me again ", "Never call me again ", "No more of these phone calls from you ",
    "Remove my number from your call list ", "Remove me from your list ", "Put me on the do not call list ",
    "I don't want any more calls ", "I don't want you to call me again ",
]
HOSTILE_UNITS = ["do-not-call-me-", "stop calling-", "no more ", "don't ", "told them to ", " ", "please ",
                 "i told you ", "stop calling me please. "]


def test_a_5000_character_ordinary_transcript_is_fast():
    text = _fill(ORDINARY_CHUNK)
    assert len(text) == 5000
    assert _best_of_three(lambda: dnc_assertion_position(text)) < 2.0


@pytest.mark.parametrize("separator", ["", ". "])
@pytest.mark.parametrize("unit", FORM_UNITS)
def test_5000_characters_of_each_core_form_repeated_are_fast(unit, separator):
    text = _fill(unit.rstrip() + separator if separator else unit)
    assert len(text) == 5000
    assert _best_of_three(lambda: dnc_assertion_position(text)) < 2.0


@pytest.mark.parametrize("unit", HOSTILE_UNITS)
def test_5000_characters_of_hostile_units_are_fast(unit):
    text = _fill(unit)
    assert _best_of_three(lambda: dnc_assertion_position(text)) < 2.0


def test_measure_20000_characters_legacy_alone_and_whole_function_is_reported_not_asserted(capsys):
    rows = []
    for unit in FORM_UNITS + HOSTILE_UNITS + [ORDINARY_CHUNK]:
        text = _fill(unit, 20000)
        t0 = time.perf_counter()
        _legacy_dnc_assertion_position(text)
        legacy = time.perf_counter() - t0
        t0 = time.perf_counter()
        dnc_assertion_position(text)
        whole = time.perf_counter() - t0
        rows.append((whole, legacy, unit))
    rows.sort(reverse=True)
    with capsys.disabled():
        for whole, legacy, unit in rows[:3]:
            print(f"\nMEASURED 20000 chars of {unit!r}: legacy alone {legacy:.3f}s, whole function {whole:.3f}s")
    assert len(rows) == len(FORM_UNITS) + len(HOSTILE_UNITS) + 1



# --------------------------------------------------------------------------- consumers
ROWS_YES = ["Quit calling.", "Remove my number from your list.", "Okay. Quit calling me.",
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
@pytest.mark.parametrize("words", ["Quit calling.", "Remove my number from your list.", "Put me on the do-not-call list."])
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
@pytest.mark.parametrize("words", ["Quit calling.", "Remove my number from your list.", "Put me on the do-not-call list."])
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
@pytest.mark.parametrize("words", ["Quit calling.", "Remove my number from your list.", "Okay. Quit calling me.",
                                   "Put me on the do-not-call list."])
async def test_consumer_6_7_partial_at_hangup_is_purged(monkeypatch, words):
    from tests.unit.test_opt_out_never_missed_20261120 import _teardown

    purge, cs = await _teardown(monkeypatch, current_user_input=words)
    assert purge.await_count == 1
    assert cs._caller_opted_out is True


@pytest.mark.asyncio
@pytest.mark.parametrize("words", ["Quit calling.", "Remove my number from your list.", "Put me on the do-not-call list."])
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
@pytest.mark.parametrize("words", ["Quit calling.", "Remove my number from your list.", "Put me on the do-not-call list."])
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
@pytest.mark.parametrize("words", ["No more calls.", "Quit calling.", "Remove my number from your list."])
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
@pytest.mark.parametrize("words", ["Quit calling.", "Remove my number from your list.", "Okay. Quit calling me.",
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


# The habit-statement guard treats "people" before the verb as somebody else ("people stop calling
# after a while"). In "you people" it is the company being addressed. Every row of both lists is
# decided by the legacy function; the gate test above proves nothing the legacy function catches is lost.
YOU_PEOPLE_REQUESTS = [
    "I have already told you people stop calling me",
    "You people stop calling me",
    "you people stop calling this number",
    "Can you people stop calling me please",
    "Will you people stop ringing me",
    "I wish you people would stop calling me",
    "you people never call me again",
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
def test_a_statement_about_other_peoples_habits_is_what_the_legacy_function_says(sentence):
    # additive rule: these contain legacy phrases ("stop calling", "dont call"), so the legacy
    # function decides and the new path adds nothing.
    assert idn.dnc_assertion_position(sentence) == _legacy_dnc_assertion_position(sentence)


@pytest.mark.parametrize("sentence", [
    "why do you keep calling me stop calling me",
    "You people remove my number",
    "Quit ringing me, I'm sick of it.",
])
def test_a_run_on_or_a_reason_attached_stays_with_the_language_model_as_on_main(sentence):
    # accepted trade of the strict core: missed here on purpose, exactly as on main
    assert dnc_assertion_position(sentence) == MAIN.dnc_assertion_position(sentence) == -1



# --------------------------------------------------------------------------- the round after the fourth review
# R1 to R3 and R5 only REMOVE something the new path accepted; R4 is the one bug fix (a request beside C4).

# T1: every closed list of the new path, written out. A one-entry change (a lead-in, a tail, a list kind, a
# courtesy word) fails here. The inline alternatives inside the forms are covered by the pattern hash below.
CLOSED_LISTS = {
    "_CORE_LEADINS": (
        'please',
        'just',
        'look',
        'listen',
        'right',
        'ok',
        'okay',
        'no',
        'hey',
        'oi',
        'erm',
        'um',
        'uh',
        'yeah',
        'so',
        'well',
        'honestly',
        'seriously',
        "for god's sake",
        'for the last time',
        'i said',
        'i told you',
        'i told you to',
        "i'm telling you",
        "i'm telling you to",
        'you need to',
        'you have to',
        'i want you to',
        'i need you to',
        "i'd like you to",
        "i've told you",
        'i have told you',
        'not interested',
    ),
    "_C4_LEADINS": (
        'please',
        'look',
        'listen',
        'just',
        'i said',
    ),
    "_CORE_INTENSIFIERS": (
        'bloody',
        'fucking',
        'freaking',
        'frigging',
        'flipping',
        'effing',
        'damn',
        'f***ing',
        'f**king',
        'f*cking',
    ),
    "_CORE_OBJECTS": (
        'me',
        'us',
        'here',
        'this number',
        'my number',
        'this phone',
        'my phone',
    ),
    "_CORE_LIST_KINDS": (
        'call',
        'calling',
        'contact',
        'phone',
        'dialling',
        'dialing',
        'marketing',
    ),
    "_CORE_RECORDS": (
        'list',
        'system',
        'database',
        'records',
    ),
    "_CORE_TAILS": (
        'please',
        'thanks',
        'thank you',
        'ok',
        'okay',
        'alright',
        'at all',
        'ever',
        'again',
        'anymore',
        'any more',
        'i mean it',
        'do you hear me',
        "i'm not interested",
        'i am not interested',
        "i've told you before",
        "i've told you already",
        'i told you already',
        'bye',
        'goodbye',
        'bye bye',
        'not interested',
    ),
    "_CORE_COURTESY": (
        'thanks',
        'thank you',
        'thanks very much',
        'cheers',
        'bye',
        'goodbye',
        'bye bye',
        'ok',
        'okay',
        'alright',
        'please',
        'no',
        'no thanks',
        'no thank you',
        'not interested',
        "i'm not interested",
        'i am not interested',
        "we're not interested",
        'i mean it',
    ),
    "_C4_COURTESY": (
        'thanks',
        'thank you',
        'thanks very much',
        'cheers',
        'bye',
        'goodbye',
        'bye bye',
        'please',
        'not interested',
        "i'm not interested",
        'i am not interested',
        "we're not interested",
        'i mean it',
    ),
    "_CORE_VERBS": (
        'call',
        'ring',
        'phone',
        'contact',
    ),
    "_CORE_VERBS_ING": (
        'calling',
        'ringing',
        'phoning',
        'contacting',
    ),
    "_CORE_AGAIN": (
        'again',
        'anymore',
        'any more',
        'ever again',
        'ever',
    ),
}

FORM_C4_PATTERN = 'no\\s+more\\s+(?:of\\s+(?:these|your)\\s+)?(?:phone\\s+)?calls(?:\\s+from\\s+you)?'
SENTENCE_REQUEST_PATTERN_SHA256 = 'd83bfb78b9ecb3561bf18d6196284f3343d7943cce1825c17d5e0353d4baedfa'
CORE_FORMS_COUNT = 17


# Any change to an inline alternative of a form (the verbs of C5, the objects of C7, the tuple of N5, ...) changes
# the compiled pattern and therefore this hash.


def test_every_closed_list_of_the_new_path_is_pinned_to_a_literal():
    for name, literal in CLOSED_LISTS.items():
        assert getattr(idn, name) == literal, name
    assert idn._FORM_C4 == FORM_C4_PATTERN
    assert len(idn._CORE_FORMS) == CORE_FORMS_COUNT
    assert hashlib.sha256(idn._SENTENCE_REQUEST.pattern.encode()).hexdigest() == SENTENCE_REQUEST_PATTERN_SHA256


def test_the_removed_entries_are_gone():
    for word in ("yes", "yeah", "right", "hello", "hi", "that's it", "that's all", "that is all"):
        assert word not in idn._CORE_COURTESY, word
    for word in ("can you", "could you", "will you", "would you"):
        assert word not in idn._CORE_LEADINS, word


@pytest.mark.parametrize("text", ["No more calls now.", "Don't ring this number again now."])
def test_now_is_not_a_tail_of_any_form_but_c1(text):
    assert _legacy_dnc_assertion_position(text) < 0
    assert _new_dnc_assertion_position(text) == -1 and dnc_assertion_position(text) == -1


# T6: rows for R1 to R4 (every group: at least three not caught, one still caught beside them).
ROUND_NOT_CAUGHT = {
    "R1 bare remove / delete needs the list part": [
        "Remove my number.", "Delete this number.", "Remove this number.", "Please remove my number.",
        "Yeah, delete this number.", "Okay. Remove my number.",
    ],
    "R2 answer and greeting words are not courtesy": [
        "Yes. Stop phoning.", "Yeah. Stop phoning.", "Hello. Stop phoning.", "Hi. Stop phoning.",
        "Right. Stop phoning.", "Stop phoning. That's it.", "Stop phoning. That's all.",
        "Stop phoning. That is all.", "Stop phoning. Yes.",
    ],
    "R3 a question lead-in is not a lead-in": [
        "Can you stop phoning me.", "Could you quit calling me.", "Will you stop phoning.",
        "Would you quit ringing me.", "Could you remove my number from your list.",
    ],
    "R4 C4 beside another turn that is not courtesy": [
        "No. No more calls.", "So, no more calls.", "No more calls. Okay.", "Yes. No more calls.",
        "Stop phoning. No more calls. Okay.", "Remove my number. No more calls.",
    ],
}
ROUND_STILL_CAUGHT = {
    "R1": ("Remove my number from your list.", "Remove"),
    "R2": ("Stop phoning. Thanks.", "Stop"),
    "R3": ("Please stop phoning me.", "stop"),
    "R4": ("Stop phoning. No more calls.", "No more"),
}
ROUND_NOT_CAUGHT_LIST = [(rule, s) for rule, rows in ROUND_NOT_CAUGHT.items() for s in rows]


@pytest.mark.parametrize("rule,text", ROUND_NOT_CAUGHT_LIST)
def test_round_rows_that_are_no_longer_caught(rule, text):
    assert _legacy_dnc_assertion_position(text) < 0
    assert _new_dnc_assertion_position(text) == -1
    assert contains_dnc(text) is False


@pytest.mark.parametrize("rule", list(ROUND_STILL_CAUGHT))
def test_round_beside_each_group_one_request_is_still_caught(rule):
    text, needle = ROUND_STILL_CAUGHT[rule]
    assert _new_dnc_assertion_position(text) == text.index(needle)
    assert contains_dnc(text) is True


def test_round_every_group_has_at_least_three_rows():
    assert len(ROUND_NOT_CAUGHT) == 4 and all(len(rows) >= 3 for rows in ROUND_NOT_CAUGHT.values())


@pytest.mark.parametrize("text,needle", [
    ("Stop phoning. No more calls.", "No more"),
    ("No more calls. Take us off your list.", "Take"),
    ("No more calls. Remove my number from your list.", "Remove"),
    ("Look, no more calls. Quit ringing me.", "Quit"),
    ("No more calls. Thanks.", "No more"),
])
def test_r4_a_c4_request_beside_another_request_sentence_is_caught_at_the_last_request(text, needle):
    assert _legacy_dnc_assertion_position(text) < 0
    assert _new_dnc_assertion_position(text) == text.index(needle)


# T3: review4_ordinary.txt. These W rows are CONTEXT-DEPENDENT and are recorded here, NOT hidden: the same words
# are a real request in most contexts ("Stop phoning me." said by a caller who is the person called), and no
# rule that sees only the text can tell the two apart. The reviewer counted 165 W rows caught before this round;
# after it, the (kind, turn) pairs below are still caught, in file order. A change in either direction fails.
R4_CAUGHT_W = [
    ('aside', 'Oi, stop ringing!'),
    ('aside', 'Stop ringing!'),
    ('aside', 'Hey, stop ringing.'),
    ('aside', "For god's sake, stop ringing!"),
    ('aside', 'Stop bloody ringing!'),
    ('aside', 'Seriously, stop ringing.'),
    ('aside', 'Stop ringing this phone.'),
    ('report', 'I said stop phoning.'),
    ('report', 'I said stop ringing.'),
    ('relay', 'Stop phoning me.'),
    ('relay', 'Just stop phoning.'),
    ('relay', 'Stop ringing here.'),
    ('partial', 'Stop contacting.'),
    ('relay', 'Stop phoning me now.'),
    ('slip', 'Stop ringing now.'),
    ('slip', 'Stop ringing, thanks.'),
    ('slip', 'Stop contacting me.'),
    ('slip', 'Stop phoning.'),
    ('partial', 'Quit phoning me.'),
    ('aside', 'Quit ringing.'),
    ('aside', 'Stop ringing my phone.'),
    ('relay', 'Okay, stop phoning.'),
    ('partial', 'Please stop ringing.'),
    ('relay', 'Stop contacting us.'),
    ('relay', 'Stop phoning here.'),
    ('aside', 'Stop effing ringing!'),
    ('aside', 'Stop flipping ringing.'),
    ('third', 'No, stop phoning.'),
    ('aside', 'You need to stop ringing.'),
    ('relay', 'You have to stop phoning.'),
    ('relay', 'I need you to stop phoning.'),
    ('aside', 'I told you to stop phoning.'),
    ('aside', "I'm telling you, stop ringing."),
    ('aside', 'Right, stop ringing.'),
    ('relay', 'Stop phoning ok.'),
    ('relay', "Don't ring here again."),
    ('relay', "Don't phone us again."),
    ('partial', "Don't ring this number again."),
    ('relay', "Don't phone here again."),
    ('aside', "Don't ring my phone again."),
    ('relay', "Don't contact here again."),
    ('relay', "Don't ring here again, ever."),
    ('relay', "Don't phone my number again."),
    ('relay', "Don't ever ring here again."),
    ('slip', 'Never phone me.'),
    ('slip', 'Never phone here.'),
    ('slip', 'Never call here.'),
    ('slip', 'Never ring here.'),
    ('slip', 'Never call my phone.'),
    ('slip', 'No, never phone me.'),
    ('slip', 'Never phone us.'),
    ('slip', 'Never call this number.'),
    ('slip', 'Never ring my number.'),
    ('relay', 'Never phone me again.'),
    ('relay', 'Never ever call here.'),
    ('full', 'No more calls, thanks.'),
    ('full', 'No more calls. Thank you.'),
    ('full', 'No more phone calls thanks.'),
    ('full', 'Just no more calls. Thanks.'),
    ('full', 'No more calls okay.'),
    ('full', 'I said no more calls.'),
    ('full', 'No more calls at all.'),
    ('slip', 'No more calls thank you.'),
    ('full', 'No more calls. Bye.'),
    ('relay', 'Look, no more calls.'),
    ('relay', 'Please, no more calls.'),
    ('full', 'Just no more phone calls.'),
    ('full', 'Take us off your list.'),
    ('full', 'Please remove us from your list.'),
    ('full', 'Yeah take us off your list.'),
    ('full', 'Remove me off your list.'),
    ('full', 'Take us off your phone list.'),
    ('full', 'No, remove us from your list.'),
    ('full', 'Take us off your list, thanks.'),
    ('full', 'Remove us off your list please.'),
    ('full', 'Just remove me off your list.'),
    ('third', 'Put this number on the DNC list.'),
    ('full', "I don't want any more calls. Thanks."),
    ('full', "No. I don't want any more calls."),
    ('full', "We don't want these calls."),
    ('full', "I don't want these calls."),
    ('full', "We don't want any more calls thanks."),
    ('full', "I don't want anymore calls."),
    ('slip', "I don't want these calls, thanks."),
    ('partial', "I don't want any more calls."),
    ('relay', "I don't want your calls."),
    ('full', "We don't want any more of these calls."),
    ('relay', "I don't want any more calls, okay."),
    ('partial', "I don't want you to ring me again."),
    ('relay', "I don't want you to phone here anymore."),
    ('full', 'Get me off your list.'),
    ('full', 'Just get us off your list.'),
    ('partial', 'Get us off your list. Thanks.'),
    ('full', 'Delete me from your list.'),
    ('full', 'Please delete us from your list.'),
    ('full', 'Take my name off your list please.'),
    ('full', 'Just take my name off your list.'),
    ('full', 'Remove my name from your list thanks.'),
    ('partial', 'Remove my name from your list.'),
    ('full', "I'd like to be taken off your list."),
    ('full', 'I want to be removed from your list.'),
    ('full', "We'd like to be removed from your list."),
    ('partial', 'I would like to be removed from your list please.'),
    ('partial', "I don't want to be called again."),
    ('relay', 'Cease all calls.'),
    ('partial', 'Cease all calls to this number please.'),
    ('third', 'Yeah, stop phoning.'),
    ('relay', 'No. Stop contacting me.'),
    ('child', 'Stop ringing! Stop ringing!'),
    ('report', 'I said no more calls.'),
    ('aside', "I've told you, stop ringing."),
    ('slip', 'Stop ringing.'),
    ('slip', 'Stop phoning now.'),
]
R4_CAUGHT_A = [
    ('full', 'Remove my number from your system.'),
    ('full', 'Remove my number from your database.'),
    ('full', 'Yeah, stop contacting me.'),
    ('full', 'No, stop ringing here.'),
    ('full', 'Stop phoning my phone.'),
    ('full', "No. Don't ring this number again."),
    ('partial', "Don't contact this number again."),
    ('full', 'No, never call this number.'),
    ('full', 'No more calls please.'),
    ('child', 'No more calls bye bye.'),
    ('full', 'Remove us from your contact list.'),
    ('full', "I don't want any more calls please."),
    ('full', "I don't want any calls from you."),
    ('full', "No, I don't want you to call me again."),
    ('full', 'Okay, get me off your calling list.'),
    ('full', 'Please remove my name from your contact list.'),
    ('full', 'No. I do not wish to be contacted.'),
    ('full', "I don't wish to be contacted."),
    ('full', 'I do not wish to be contacted, thank you.'),
    ('full', "I don't want to be contacted again."),
    ('full', "No, I don't want to be called again."),
    ('full', 'Okay. Get me off your list.'),
    ('child', 'Stop phoning!'),
    ('child', 'No more calls!'),
    ('child', 'Never phone us! Bye bye!'),
    ('echo', 'Remove my number from your list.'),
    ('echo', 'No more calls.'),
    ('echo', 'Stop contacting me.'),
]
R4_CAUGHT_R = [
    ('full', 'Take this number off your records please.'),
    ('vm', 'Do not phone this number again.'),
    ('full', 'Remove us from your marketing list.'),
    ('full', 'Put us on your no call list.'),
    ('full', 'Cease all calls to this number.'),
    ('vm', 'Please remove this number from your call list. Thank you.'),
    ('vm', 'Remove this number from your list.'),
    ('vm', 'Please stop phoning.'),
]

# Per label: (rows, caught by the legacy function, caught only by the new path). Before this round: W (176, 0, 165),
# A (35, 0, 35), R (8, 0, 8).
R4_ORDINARY_COUNTS = {"W": (176, 0, 113), "A": (35, 0, 28), "R": (8, 0, 8)}
R4_CAUGHT = {"W": R4_CAUGHT_W, "A": R4_CAUGHT_A, "R": R4_CAUGHT_R}


def _new_only(text):
    return _legacy_dnc_assertion_position(text) < 0 and _new_dnc_assertion_position(text) >= 0


@pytest.mark.parametrize("label", ["W", "A", "R"])
def test_review4_ordinary_counts_per_label(label):
    rows = [r for r in R4_ORDINARY_ROWS if r[0] == label]
    legacy = sum(1 for r in rows if _legacy_dnc_assertion_position(r[2]) >= 0)
    new = sum(1 for r in rows if _new_only(r[2]))
    assert (len(rows), legacy, new) == R4_ORDINARY_COUNTS[label]


@pytest.mark.parametrize("label", ["W", "A", "R"])
def test_review4_ordinary_turns_caught_are_exactly_the_recorded(label):
    actual = [(r[1], r[2]) for r in R4_ORDINARY_ROWS if r[0] == label and _new_only(r[2])]
    assert actual == R4_CAUGHT[label], _diff(R4_CAUGHT[label], actual)


def test_review4_ordinary_file_shape():
    assert len(R4_ORDINARY_ROWS) == 219 and all(len(r) == 4 and r[0] in "WAR" for r in R4_ORDINARY_ROWS)
    assert len(R4_REQUEST_ROWS) == 182 and all(r[0] in "PQX" for r in R4_REQUEST_ROWS)
    # the removed bare "remove/delete my number" rows, and the removed courtesy and lead-in words, are not caught
    assert not any(_new_only(t) for k, t in [(r[1], r[2]) for r in R4_ORDINARY_ROWS]
                   if t in ("No. Remove my number.", "Yeah, delete this number.", "Okay. Remove my number.",
                            "Could you remove my number.", "Can you remove this number."))


# T4: review4_requests.txt per kind: (caught by the legacy function, caught only by the new path, missed).
# Before this round: P (1, 33, 95), Q (0, 0, 13), X (0, 0, 40); one P row moved from the new path to missed
# ("Remove this number. Thank you. Bye-bye.") and one P row from missed to the new path (R4, below).
R4_REQUEST_COUNTS = {"P": (1, 33, 95), "Q": (0, 0, 13), "X": (0, 0, 40)}


@pytest.mark.parametrize("kind", ["P", "Q", "X"])
def test_review4_requests_per_kind(kind):
    rows = [r[1] for r in R4_REQUEST_ROWS if r[0] == kind]
    legacy = sum(1 for s in rows if _legacy_dnc_assertion_position(s) >= 0)
    new = sum(1 for s in rows if _new_only(s))
    missed = sum(1 for s in rows if not contains_dnc(s))
    assert (legacy, new, missed) == R4_REQUEST_COUNTS[kind]
    assert legacy + new + missed == len(rows)


def test_review4_requests_one_row_gained_by_r4_and_one_lost_by_r1():
    assert _new_only("Stop phoning. No more calls.") and "Stop phoning. No more calls." in R4_REQUESTS
    assert not contains_dnc("Remove this number. Thank you. Bye-bye.") and \
        "Remove this number. Thank you. Bye-bye." in R4_REQUESTS


# T5: request sentences of the data folder that the new path caught before this round and does not catch now:
# bare "remove / delete my number" and turns that began with one of the removed question lead-ins. (Per file:
# must_catch 5, review_catch 4, review2_requests 5, review3_requests 13; the rows of the holdout files and of
# review4_requests that were lost: none and 1.)
REQUESTS_THIS_ROUND_STOPPED_CATCHING = [
    'Remove my number.',
    'Remove my number, please.',
    'remove my number please',
    'Please remove this number.',
    'just remove my number',
    'Remove my number.',
    'I need you to remove my number.',
    'not interested remove my number',
    "I'd like you to remove my number.",
    'Please can you stop phoning me.',
    'Could you please stop contacting me.',
    'Could you remove this number from your records please.',
    'Not interested, please remove my number.',
    "I'm telling you for the last time, remove my number.",
    'Remove my number please.',
    'Remove this number.',
    'Okay, remove my number.',
    'Just remove my number, thank you.',
    "Remove my number, I'm not interested.",
    'Could you remove my number please.',
    'Will you stop phoning me.',
    'I need you to remove my number.',
    'Not interested, remove my number.',
    'Not interested. Remove my number.',
    'remove my number thank you bye',
    'Remove my number. Goodbye.',
    'Remove my number, goodbye.',
]


def test_requests_this_round_stopped_catching_are_exactly_the_listed_and_are_not_caught_now():
    assert len(REQUESTS_THIS_ROUND_STOPPED_CATCHING) == 27
    all_rows = {s for rows in REQUEST_FILES.values() for s in rows}
    for s in REQUESTS_THIS_ROUND_STOPPED_CATCHING:
        assert s in all_rows, s
        assert _legacy_dnc_assertion_position(s) < 0 and _new_dnc_assertion_position(s) == -1, s
    # every other request row the new path did not catch is unchanged: the listed rows are the only ones that
    # are missed among the sentences that start with "remove"/"delete my number" or a removed lead-in word
    removed_leadins = ("can you", "could you", "will you", "would you")
    still_caught_with_a_removed_start = [
        s for s in all_rows if _new_only(s) and s.lower().startswith(removed_leadins)]
    assert still_caught_with_a_removed_start == []
