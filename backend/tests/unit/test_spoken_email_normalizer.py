"""Tests for spoken_email_normalizer — covers every spoken-email variant
we've seen in production transcripts, plus the 2026-04-22 regression."""
from __future__ import annotations

import pytest

from app.services.scripts.spoken_email_normalizer import (
    extract_email_from_agent_readback,
    extract_email_from_speech,
    extract_phone_from_speech,
    natural_email_readback,
    natural_phone_readback,
    spell_out_email,
)


def test_spell_out_email_basic():
    assert spell_out_email("bob@gmail.com") == "b-o-b at gmail dot com"


def test_spell_out_email_long_local():
    assert (
        spell_out_email("allstateestimation@gmail.com")
        == "a-l-l-s-t-a-t-e-e-s-t-i-m-a-t-i-o-n at gmail dot com"
    )


def test_spell_out_email_multi_dot_domain():
    assert spell_out_email("x@mail.co.uk") == "x at mail dot co dot uk"


def test_spell_out_email_invalid_returns_blank():
    assert spell_out_email("") == ""
    assert spell_out_email("notanemail") == ""
    assert spell_out_email(None) == ""


# PINNED deterministically: written emails, and SINGLE-TOKEN spoken locals
# (incl. separator-glued ones like "mary underscore smith" -> "mary_smith").
@pytest.mark.parametrize("speech,expected", [
    # --- plain written form passes through ---
    ("my email is john@gmail.com", "john@gmail.com"),
    ("John@Example.COM", "john@example.com"),

    # --- "at" / "at the rate" (Indian English) / "at sign" ---
    ("john at gmail dot com", "john@gmail.com"),
    ("allstateestimation at the rate gmail dot com", "allstateestimation@gmail.com"),
    ("john at sign gmail dot com", "john@gmail.com"),

    # --- "dot" variants ---
    ("bob at yahoo dot co dot uk", "bob@yahoo.co.uk"),
    ("bob at yahoo period com", "bob@yahoo.com"),

    # --- spoken separators glue the local into ONE token ---
    ("mary underscore smith at gmail dot com", "mary_smith@gmail.com"),
    ("mary dash smith at gmail dot com", "mary-smith@gmail.com"),
    ("mary hyphen smith at gmail dot com", "mary-smith@gmail.com"),
    ("john dot smith at gmail dot com", "john.smith@gmail.com"),

    # --- capitalization and whitespace tolerance ---
    ("  JOHN  AT  GMAIL  DOT  COM  ", "john@gmail.com"),
])
def test_extract_email_positive(speech, expected):
    assert extract_email_from_speech(speech) == expected


# 2026-07-13 fix: trailing conversational filler after the domain must NOT
# fuse into the TLD ("bob@gmail.complease"). The domain boundary is the
# longest domain-syntax prefix ("label.label...tld"), not "everything left
# in the utterance with whitespace stripped".
@pytest.mark.parametrize("speech,expected", [
    ("bob at gmail dot com please", "bob@gmail.com"),
    ("bob at gmail dot com thank you", "bob@gmail.com"),
    ("bob at gmail dot com thanks", "bob@gmail.com"),
    ("bob at gmail dot com cheers", "bob@gmail.com"),
    ("bob at gmail dot com okay", "bob@gmail.com"),
    ("bob at gmail dot com yeah", "bob@gmail.com"),
    ("john at gmail dot com, that's it", "john@gmail.com"),
    # multi-part domain still glues correctly even with trailing filler
    ("bob at yahoo dot co dot uk please", "bob@yahoo.co.uk"),
])
def test_extract_email_trailing_filler_does_not_fuse_into_domain(speech, expected):
    assert extract_email_from_speech(speech) == expected


@pytest.mark.parametrize("speech", [
    "",
    "I don't want to give my email",
    "call me on Sunday",
    "Victor Street 177, apartment 138",
    "yeah",
    "gmail dot com",  # domain only — no local part
    "john at",        # no domain

    # 2026-06-24 hybrid: multi-word / carrier-prefixed / spoken-digit locals are
    # NO LONGER joined by regex — they return None and the LLM assembles them.
    "all state estimation at the rate gmail dot com",   # multi-word local
    "Cloud State estimation at g mail dot com.",          # multi-word local
    "you can send me on bob at gmail dot com",            # carrier phrase
    "bob one two three at gmail dot com",                 # spoken digits, multi-token
])
def test_extract_email_negative(speech):
    assert extract_email_from_speech(speech) is None


# 2026-06-24 hybrid design: the deterministic layer no longer strips carrier
# words or joins multi-word spoken locals — that is the LLM's job (it assembles
# the address and reads it back to confirm). Only an unambiguous single-token
# local is pinned. We never keep a carrier-word list again.
def test_multiword_and_carrier_spoken_locals_defer_to_llm():
    from app.services.scripts.spoken_email_normalizer import extract_email_from_speech as e
    # would need word-joining / carrier-stripping -> deferred to the LLM
    assert e("You can send me on all state estimation at g mail dot com.") is None
    assert e("all state estimation at gmail dot com") is None
    assert e("it is john at gmail dot com") is None
    # unambiguous single-token spoken locals are still pinned deterministically
    assert e("john at gmail dot com") == "john@gmail.com"
    assert e("sarah underscore jones at outlook dot com") == "sarah_jones@outlook.com"


# ── natural_email_readback (migrated from the deleted capture_confirmation test)
# The read-back is the audible half of the confirm gate: separators/digits must
# be voiced or a caller could yes-confirm a wrong address.

def test_natural_readback_word_local_part():
    assert natural_email_readback("allstateestimation@gmail.com") == \
        "allstateestimation at gmail dot com"


def test_natural_readback_word_plus_digits():
    assert natural_email_readback("john7890@gmail.com") == "john 7 8 9 0 at gmail dot com"


def test_natural_readback_spells_only_nonword_runs():
    assert natural_email_readback("xq7@gmail.com") == "x-q 7 at gmail dot com"


def test_natural_readback_multidot_domain():
    assert natural_email_readback("bob@yahoo.co.uk") == "bob at yahoo dot co dot uk"


def test_natural_readback_speaks_local_separators():
    assert natural_email_readback("j.smith@gmail.com") == "j dot smith at gmail dot com"
    assert natural_email_readback("john_smith@acme.com") == "john underscore smith at acme dot com"
    assert natural_email_readback("a-team@acme.com") == "a dash team at acme dot com"
    assert natural_email_readback("bob+tag@acme.com") == "bob plus tag at acme dot com"


# ── extract_email_from_agent_readback (gap #2: multi-word emails enter the gate)

@pytest.mark.parametrize("turn,expected", [
    ("So that's all state estimation at gmail dot com — did I get that right?",
     "allstateestimation@gmail.com"),
    ("Okay, so that's all state estimation at the rate gmail dot com, is that right?",
     "allstateestimation@gmail.com"),
    ("Your email is cloud state estimation at gmail dot com, correct?",
     "cloudstateestimation@gmail.com"),
    ("Let me confirm, that's mary jane at yahoo dot co dot uk — did I get that right?",
     "maryjane@yahoo.co.uk"),
])
def test_agent_readback_parses_assembled_multiword_email(turn, expected):
    assert extract_email_from_agent_readback(turn) == expected


@pytest.mark.parametrize("turn", [
    # no confirm question -> not a read-back, just a mention
    "I'll send it to all state estimation at gmail dot com.",
    "Great, I have your details.",
    # domain only / no preamble anchor -> refuse to guess a boundary
    "Should I reach you at your gmail dot com address?",
    # two spoken "at"s -> ambiguous, bail rather than mis-parse
    "You work at microsoft and it's bob at gmail dot com, right?",
    "",
])
def test_agent_readback_conservative_returns_none(turn):
    assert extract_email_from_agent_readback(turn) is None


# ── extract_phone_from_speech (gap #1: numbers get the same deterministic gate)

@pytest.mark.parametrize("speech,expected", [
    ("my number is 555 123 4567", "5551234567"),
    ("you can call me at five five five one two three four five six seven", "5551234567"),
    ("call me back on 555-123-4567", "5551234567"),
    ("my cell is (212) 555 0199", "2125550199"),
    ("reach me at plus four four seven nine one one one one one one one one",
     "+447911111111"),
])
def test_extract_phone_positive(speech, expected):
    assert extract_phone_from_speech(speech) == expected


@pytest.mark.parametrize("speech", [
    "",
    "yeah that sounds good",
    # an email's digits are NOT a phone
    "bob one two three four five six seven at gmail dot com",
    "my email is john123456@gmail.com",
    # an address is not a phone (too few contiguous digits, no phone cue)
    "Victor Street 177, apartment 138",
    # a bare short number with no phone cue
    "I have 3 or 4 projects",
    # a bare digit run with neither a cue nor phone formatting stays untouched
    "the code was 12345678",
])
def test_extract_phone_negative(speech):
    assert extract_phone_from_speech(speech) is None


def test_natural_phone_readback():
    assert natural_phone_readback("5551234567") == "5 5 5 1 2 3 4 5 6 7"
    assert natural_phone_readback("+447911") == "plus 4 4 7 9 1 1"
    assert natural_phone_readback("") == ""
    assert natural_phone_readback(None) == ""
