"""Sentence-budget controls for the traditional streaming pipeline."""
from __future__ import annotations

import types

from app.domain.services.voice_pipeline.llm_response import (
    response_max_sentences_for_turn,
)


# ── response_max_sentences_for_turn (pure) ───────────────────────────────────

def test_turn_id_shape():
    assert response_max_sentences_for_turn(0) == 2      # first turn capped at 2
    assert response_max_sentences_for_turn(3) is None   # later turns uncapped


def test_session_shape_default_limit():
    session = types.SimpleNamespace(agent_config=types.SimpleNamespace(response_max_sentences=2))
    assert response_max_sentences_for_turn(session, "hello there") == 2


def test_pricing_question_bumps_limit_with_custom_prompt():
    session = types.SimpleNamespace(agent_config=types.SimpleNamespace(response_max_sentences=2))
    # custom prompt + pricing intent → relax to >=4
    assert response_max_sentences_for_turn(session, "what is your pricing", has_custom_prompt=True) == 4
    # without custom prompt, no bump
    assert response_max_sentences_for_turn(session, "what is your pricing", has_custom_prompt=False) == 2


# ── read-back budget (issue #6): the confirmation question must not be truncated ─

def _session_with_slots(email=None, confirmed=False):
    from app.services.scripts.call_state_tracker import CallState
    return types.SimpleNamespace(
        agent_config=types.SimpleNamespace(response_max_sentences=2),
        captured_slots=CallState(email=email, email_confirmed=confirmed),
    )


def test_unconfirmed_email_readback_raises_budget():
    # An unconfirmed email means a read-back is due; the budget must leave room
    # for the "...did I get that right?" question (else the cap drops it).
    s = _session_with_slots(email="bob@acme.com", confirmed=False)
    assert response_max_sentences_for_turn(s, "bob at acme dot com") >= 3


def test_confirmed_email_keeps_default_budget():
    s = _session_with_slots(email="bob@acme.com", confirmed=True)
    assert response_max_sentences_for_turn(s, "thanks") == 2


def test_no_pending_slot_keeps_default_budget():
    s = _session_with_slots(email=None)
    assert response_max_sentences_for_turn(s, "hello there") == 2


def test_readback_and_pricing_takes_the_max():
    s = _session_with_slots(email="bob@acme.com", confirmed=False)
    assert response_max_sentences_for_turn(s, "what's your pricing", has_custom_prompt=True) == 4
