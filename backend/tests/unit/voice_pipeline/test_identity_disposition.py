"""Case 1 fix — deterministic wrong-number / identity disposition.

The defect: "wrong number" was a coin flip (end vs pivot) because two prompt
blocks gave contradictory rules for the same phrase. These tests pin the
corrected, deterministic behavior: wrong DESTINATION ends, wrong PERSON pivots
(never ends), bare "wrong number" clarifies once then resolves to an end.
"""
from __future__ import annotations

import pytest

from app.domain.services.voice_pipeline.identity_disposition import (
    IdentityDisposition,
    classify_identity_disposition,
    contains_dnc,
    contains_explicit_goodbye,
    disposition_end_line,
    WRONG_BUSINESS_CLOSE,
    DNC_CLOSE,
)


# --- wrong DESTINATION → end -------------------------------------------------
@pytest.mark.parametrize(
    "utterance",
    [
        "Sorry, you've got the wrong company.",
        "This is a private residence.",
        "There's no business here, this is my home.",
        "Wrong number — this is a personal phone.",
        "You have the wrong business, mate.",
    ],
)
def test_wrong_business_ends(utterance):
    assert classify_identity_disposition(utterance) == IdentityDisposition.WRONG_BUSINESS


# --- self-review regressions: things a LIVE PROSPECT says must never end -----
@pytest.mark.parametrize(
    "utterance",
    [
        # Brush-off, not a wrong number — "never heard of" was removed from the
        # deterministic end list for exactly this case.
        "Never heard of you guys, what is this about?",
        # A CALLBACK REQUEST — bare "personal number" must not match.
        "Can you call me on my personal number instead?",
        # Skeptical prospect — bare "you want" must not arm wrong-person either.
        "You want to sell me something?",
    ],
)
def test_live_prospect_phrases_never_end(utterance):
    d = classify_identity_disposition(utterance)
    assert d in (IdentityDisposition.NONE, IdentityDisposition.WRONG_PERSON)
    assert disposition_end_line(d) is None


# --- wrong PERSON → pivot (the review's HIGH correction: NEVER auto-hangup) ---
@pytest.mark.parametrize(
    "utterance",
    [
        "There's no one here by that name.",
        "David isn't here right now.",
        "She's not available at the moment.",
        "You want the accounts department.",
        "He doesn't work here anymore.",
        "No one by that name, sorry.",
    ],
)
def test_wrong_person_pivots_never_ends(utterance):
    d = classify_identity_disposition(utterance)
    assert d == IdentityDisposition.WRONG_PERSON
    assert disposition_end_line(d) is None  # must NOT produce a hangup line


def test_wrong_number_plus_person_evidence_pivots():
    # "wrong number" present, but person evidence wins → pivot, not end.
    d = classify_identity_disposition("Wrong number, no one here by that name.")
    assert d == IdentityDisposition.WRONG_PERSON


# --- DNC overrides everything ------------------------------------------------
@pytest.mark.parametrize(
    "utterance",
    ["Stop calling me.", "Take me off your list.", "Do not call this number again."],
)
def test_dnc_ends(utterance):
    assert classify_identity_disposition(utterance) == IdentityDisposition.DNC


# --- bare "wrong number" → clarify once, then resolve ------------------------
def test_bare_wrong_number_is_ambiguous_first():
    d = classify_identity_disposition("Wrong number.")
    assert d == IdentityDisposition.AMBIGUOUS


def test_bare_wrong_number_after_clarify_resolves_to_business():
    # Second bare wrong-number after we already asked the scope question → end.
    d = classify_identity_disposition("Wrong number.", prior_clarify_asked=True)
    assert d == IdentityDisposition.WRONG_BUSINESS


def test_clarify_answer_person_pivots():
    # Answering the clarify with "the person" routes to a pivot, not an end.
    d = classify_identity_disposition(
        "Just the wrong person, David moved teams.", prior_clarify_asked=True
    )
    assert d == IdentityDisposition.WRONG_PERSON


def test_clarify_answer_business_ends():
    d = classify_identity_disposition(
        "No, wrong business entirely.", prior_clarify_asked=True
    )
    assert d == IdentityDisposition.WRONG_BUSINESS


# --- ordinary conversation is untouched --------------------------------------
@pytest.mark.parametrize(
    "utterance",
    ["Yeah, that sounds interesting.", "What are your rates?", "Sure, go ahead.", ""],
)
def test_ordinary_turns_are_none(utterance):
    assert classify_identity_disposition(utterance) == IdentityDisposition.NONE


# --- end lines ---------------------------------------------------------------
def test_end_lines():
    assert disposition_end_line(IdentityDisposition.WRONG_BUSINESS) == WRONG_BUSINESS_CLOSE
    assert disposition_end_line(IdentityDisposition.DNC) == DNC_CLOSE
    assert disposition_end_line(IdentityDisposition.WRONG_PERSON) is None
    assert disposition_end_line(IdentityDisposition.AMBIGUOUS) is None
    assert disposition_end_line(IdentityDisposition.NONE) is None


# --- Defect 6: explicit-goodbye detector (feeds the reverse gate only) -------
@pytest.mark.parametrize(
    "utterance",
    [
        "She's not here, goodbye.",
        "Good bye then.",
        "Alright, bye now.",
        "Okay, bye bye.",
        "I'm hanging up now.",
        "Sorry, I have to go.",
        "I've got to go, bye.",
        "I gotta go, sorry.",
        "gotta go!",
    ],
)
def test_explicit_goodbye_detected(utterance):
    assert contains_explicit_goodbye(utterance) is True


@pytest.mark.parametrize(
    "utterance",
    [
        "",
        "Okay, thanks.",
        "Alright, sounds good.",
        "By the way, can you call back later?",  # "by" must not match "bye"
        "I'll buy it, thanks.",
        "She's not here right now.",
        "bye",  # bare "bye" deliberately excluded (STT-noise risk)
    ],
)
def test_explicit_goodbye_not_detected(utterance):
    assert contains_explicit_goodbye(utterance) is False


def test_explicit_goodbye_does_not_change_classify_precedence():
    # A goodbye riding along with person-mismatch evidence must still
    # classify as WRONG_PERSON (pivot) — the detector only feeds the reverse
    # gate's strip/keep decision, never classify()'s own precedence.
    d = classify_identity_disposition("She's not here — goodbye.")
    assert d == IdentityDisposition.WRONG_PERSON
    assert contains_explicit_goodbye("She's not here — goodbye.") is True


# --- the prompt blocks must no longer BOTH claim bare "wrong number" ---------
def test_prompt_blocks_are_not_contradictory_on_wrong_number():
    from app.domain.services.voice_pipeline.end_call import CALL_CONTROL_RULES
    from app.domain.services.voice_pipeline.gatekeeper import GATEKEEPER_RULES

    # end_call now scopes the token to a wrong BUSINESS, not bare "wrong number".
    assert "wrong business" in CALL_CONTROL_RULES.casefold()
    assert "wrong person is not this" in CALL_CONTROL_RULES.casefold()
    # gatekeeper explicitly hands a wrong-BUSINESS off to ENDING THE CALL.
    assert "exit, not a" in GATEKEEPER_RULES.casefold()


# --- F-14 (2026-07-20): substring false positives must NOT deterministically
#     hang up on valid prospects --------------------------------------------
@pytest.mark.parametrize(
    "utterance",
    [
        # bare "dont call" used to match this naming-convention remark → DNC
        "I don't call it Acme anymore, we're Bright Solar now.",
        # bare "this is my home" used to match a legitimate remote employee
        "This is my home office for Acme Solar.",
        # a live prospect brush-off, never a wrong number
        "I've never heard of you guys.",
        # callback request, not a wrong destination
        "Call my personal number instead.",
    ],
)
def test_valid_prospect_phrasings_do_not_end(utterance):
    d = classify_identity_disposition(utterance)
    assert d not in (IdentityDisposition.WRONG_BUSINESS, IdentityDisposition.DNC), (
        f"{utterance!r} deterministically ended the call as {d}"
    )


def test_post_clarify_right_business_pivots_not_ends():
    # The caller CONFIRMS the business is right (wrong/old contact) in answer to
    # the clarify question — must pivot (WRONG_PERSON), never hang up as
    # WRONG_BUSINESS on the bare presence of "business"/"no".
    d = classify_identity_disposition(
        "No, this is the right business, you have an old contact",
        prior_clarify_asked=True,
    )
    assert d == IdentityDisposition.WRONG_PERSON


def test_post_clarify_person_answer_pivots():
    d = classify_identity_disposition(
        "Oh, you want the wrong person — try the accounts team",
        prior_clarify_asked=True,
    )
    assert d == IdentityDisposition.WRONG_PERSON


def test_post_clarify_business_answer_still_ends():
    # A genuine wrong-destination answer to the clarify still ends.
    d = classify_identity_disposition(
        "Yeah, wrong company entirely", prior_clarify_asked=True,
    )
    assert d == IdentityDisposition.WRONG_BUSINESS


def test_second_bare_wrong_number_after_clarify_resolves_to_business():
    d = classify_identity_disposition("wrong number", prior_clarify_asked=True)
    assert d == IdentityDisposition.WRONG_BUSINESS


# --- genuine DNC still fires (directed forms) --------------------------------
@pytest.mark.parametrize(
    "utterance",
    [
        "Please stop calling me.",
        "Do not call me again.",
        "Don't call this number anymore.",
        "Take me off your list.",
        "Lose my number.",
        "Do not contact us.",
    ],
)
def test_genuine_dnc_still_ends(utterance):
    assert classify_identity_disposition(utterance) == IdentityDisposition.DNC


@pytest.mark.parametrize(
    "utterance,expected",
    [
        ("no no no no no no stop calling me", True),   # F-13: survives repetition guard
        ("Please take me off your list.", True),
        ("I don't call it Acme anymore.", False),      # naming remark, not DNC
        ("What time do you call back?", False),
    ],
)
def test_contains_dnc(utterance, expected):
    assert contains_dnc(utterance) is expected
