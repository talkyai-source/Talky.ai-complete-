"""A name column holding something that is not a name.

2026-08-13: the campaign's only lead was ``first_name='Call'``,
``last_name='30'`` — somebody had typed "Call 30" (as in "call thirty numbers")
into the name field. The digits were stripped by the existing shape allowlist,
but "Call" passed straight through, so every one of that day's 40 calls opened:

    Assistant: Hi, is this Call?

The guard drops an implausible PERSON name to "", which the pipeline already
handles: the agent falls back to the other name field, or opens without a name.

The tests below are weighted deliberately. Rejecting a placeholder is a nice
win; wrongly rejecting a real person's name is a worse failure than the bug
being fixed, so most of these assert that real names are LEFT ALONE.
"""
from __future__ import annotations

import pytest

from app.domain.services.telephony_session_config import (
    _is_implausible_person_name,
    _sanitize_lead_field,
)


# ── what must be rejected ────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", [
    "Call",          # THE production value
    "Call 30",       # the full thing somebody typed
    "test",
    "Test Test",
    "unknown",
    "N/A",
    "TBD",
    "placeholder",
    "customer",
    "Lead",
    "Mr",
    "30",
    "A",
])
def test_placeholders_are_rejected(bad):
    assert _is_implausible_person_name(bad) is True


def test_the_production_lead_no_longer_reaches_the_prompt():
    assert _sanitize_lead_field("Call", field="first_name") == ""
    assert _sanitize_lead_field("30", field="last_name") == ""


# ── what must survive ────────────────────────────────────────────────────────

@pytest.mark.parametrize("good", [
    "Sarah", "Mohammed", "Jean-Luc", "O'Brien", "Ana María", "李伟",
    "May", "June", "April", "Summer",      # months and seasons ARE given names
    "Grace", "Hope", "Faith",              # virtue names
    "Lee", "Bo", "Ng", "Al",               # short but real
    "Mark",                                # also a verb; still a name
    "Art", "Will", "Rose", "Dawn",         # nouns that are common given names
    "Call Robertson",                      # implausible first word, real surname
])
def test_real_names_are_untouched(good):
    assert _is_implausible_person_name(good) is False, f"{good!r} was rejected"


@pytest.mark.parametrize("good", ["Sarah", "Jean-Luc", "O'Brien", "Lee"])
def test_real_names_survive_the_full_sanitizer(good):
    assert _sanitize_lead_field(good, field="first_name") == good


def test_a_partly_placeholder_name_is_kept():
    """Rejection requires EVERY word to be a placeholder — one real word is
    enough to make it a usable form of address."""
    assert _is_implausible_person_name("Test Anderson") is False


# ── companies are a different question ───────────────────────────────────────

def test_company_names_are_not_plausibility_checked():
    """"Test Kitchen" and "Number 10" are real businesses. The guard is scoped
    to person names precisely so it cannot break them."""
    assert _sanitize_lead_field("Test Kitchen", field="company", is_company=True) == "Test Kitchen"
    assert _sanitize_lead_field("Number 10", field="company", is_company=True) == "Number 10"


# ── degradation is to a supported state ──────────────────────────────────────

def test_rejection_yields_empty_not_an_error():
    """The caller treats "" as "no name known" — an already-supported path.
    Raising here would take the call down over a bad CRM row."""
    for value in (None, "", "   ", "Call", 30):
        assert _sanitize_lead_field(value, field="first_name") == ""
