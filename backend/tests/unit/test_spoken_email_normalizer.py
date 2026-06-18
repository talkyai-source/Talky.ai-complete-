"""Tests for spoken_email_normalizer — covers every spoken-email variant
we've seen in production transcripts, plus the 2026-04-22 regression."""
from __future__ import annotations

import pytest

from app.services.scripts.spoken_email_normalizer import (
    extract_email_from_speech,
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

    # --- punctuation / separators spoken ---
    ("mary underscore smith at gmail dot com", "mary_smith@gmail.com"),
    ("mary dash smith at gmail dot com", "mary-smith@gmail.com"),
    ("mary hyphen smith at gmail dot com", "mary-smith@gmail.com"),

    # --- digits spoken out ---
    ("bob one two three at gmail dot com", "bob123@gmail.com"),

    # --- capitalization and whitespace tolerance ---
    ("  JOHN  AT  GMAIL  DOT  COM  ", "john@gmail.com"),

    # --- the 2026-04-22 regression case verbatim ---
    ("Cloud State estimation at g mail dot com.",
     "cloudstateestimation@gmail.com"),
    ("all state estimation at the rate Gmail dot com.",
     "allstateestimation@gmail.com"),
])
def test_extract_email_positive(speech, expected):
    assert extract_email_from_speech(speech) == expected


@pytest.mark.parametrize("speech", [
    "",
    "I don't want to give my email",
    "call me on Sunday",
    "Victor Street 177, apartment 138",
    "yeah",
    "gmail dot com",  # domain only — no local part
    "john at",        # no domain
])
def test_extract_email_negative(speech):
    assert extract_email_from_speech(speech) is None


# Regression: carrier/lead-in phrase before a spoken email must be stripped,
# not glued into the local part (the "youcansendmeon…" bug from a live call).
def test_strips_leadin_carrier_phrase():
    from app.services.scripts.spoken_email_normalizer import extract_email_from_speech as e
    assert e("You can send me on all state estimation at g mail dot com.") == "allstateestimation@gmail.com"
    assert e("it is john at gmail dot com") == "john@gmail.com"
    assert e("reach me at sarah underscore jones at outlook dot com") == "sarah_jones@outlook.com"
    # No carrier phrase — unchanged.
    assert e("all state estimation at gmail dot com") == "allstateestimation@gmail.com"
