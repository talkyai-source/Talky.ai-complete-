"""The per-turn craft block describes HOW to speak, never WHAT to offer.

It is re-sent at the end of every turn of every call for every tenant, so
anything campaign-specific in it is pushed onto every customer as the freshest
instruction in context. Until 2026-09-23 it told every agent to steer toward
"their email for a sample" and used "who prices the tenders" as its example -
one estimation campaign's offer, sent to the payments campaigns too, and the
platform half of the email-first reflex on call 2427af7e.
"""
from __future__ import annotations

import re

import pytest

from app.domain.services.voice_pipeline.conversation_craft import (
    CRAFT_REANCHOR,
    craft_reanchor,
)

# Read the way the model reads it: as continuous prose, not wrapped lines.
BLOCK = " ".join(craft_reanchor().lower().split())


@pytest.mark.parametrize(
    "campaign_word",
    [
        "tender", "sample", "estimat", "takeoff", "quote",   # estimation
        "dojo", "terminal", "card payment",                   # payments
        "allstate", "email for",                              # the old offer
    ],
)
def test_no_campaign_content_is_baked_into_the_platform_block(campaign_word):
    assert campaign_word not in BLOCK


def test_the_next_step_comes_from_the_campaign():
    assert "continuing the campaign" in BLOCK
    assert "next step only when its route exists" in BLOCK


def test_the_next_step_waits_until_the_question_is_answered():
    assert "answer their latest question or correction before continuing" in BLOCK


def test_a_mishearing_is_asked_about_not_mirrored():
    assert "speech is unclear, ask for the unclear part" in BLOCK


def test_links_are_named_among_the_things_it_must_not_invent():
    assert "next step only when its route exists" in BLOCK
    assert "available actions" in BLOCK


def test_rules_that_were_working_are_still_there():
    for kept in ("one useful question", "then listen", "not done", "known contact details"):
        assert kept in BLOCK
    assert "already have their number" not in BLOCK


def test_still_no_number_in_the_block():
    """The module's standing rule: a number here overrides the real cap."""
    assert not re.search(r"\d", CRAFT_REANCHOR)
