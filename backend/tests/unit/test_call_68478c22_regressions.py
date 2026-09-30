"""Browser test call 68478c22 (2026-09-30, "Estimation new"), replayed.

The caller spelled "u k six seven zero one three at g mail dot com"; the agent
read it back correctly with "Let me confirm that — ..." and the caller said
"Yes." -- but the read-back reader lost the preamble at the dash and counted
the seven spelled characters as seven words, so nothing was confirmed. The
goodbye guard then asked "What's the best email address for you?" twice and
the caller had to say "I have just shared the email with you."
"""
from __future__ import annotations

from pathlib import Path

from app.domain.services.voice_pipeline.conversation_guards import pending_contact_ask
from app.services.scripts.spoken_email_normalizer import extract_email_from_agent_readback

from tests.unit.test_call_1436672a_regressions import Replay

READBACK = "Let me confirm that — u k six seven zero one three at g mail dot com. Is that correct?"


def test_a_spelled_read_back_after_a_dash_is_read():
    assert extract_email_from_agent_readback(READBACK) == "uk67013@gmail.com"
    # Sentences that are not a read-back of an address stay unread.
    assert extract_email_from_agent_readback(
        "Great — I will send the brochure over to you at gmail dot com, is that right?"
    ) is None
    assert extract_email_from_agent_readback(
        "Should I reach you at your gmail dot com address?"
    ) is None


def test_the_callers_yes_confirms_the_spelled_email():
    r = Replay()
    r.agent("Perfect. What’s the best email address for you?")
    r.caller("u k six seven zero one three at g mail dot com.")
    r.agent(READBACK)
    r.caller("Yes.")
    assert r.state.email == "uk67013@gmail.com"
    assert r.state.email_confirmed is True
    assert pending_contact_ask(r.state) is None


def test_an_address_already_given_is_not_asked_for_again():
    r = Replay()
    r.agent("Perfect. What’s the best email address for you?")
    r.caller("u k six seven zero one three at g mail dot com.")
    ask = pending_contact_ask(r.state)
    assert ask is not None
    assert "best email address" not in ask
    assert "spell the part before the at" in ask


def test_the_goodbye_guard_never_repeats_its_own_recent_question():
    src = Path(__file__).resolve().parents[2].joinpath(
        "app", "domain", "services", "voice_pipeline", "turn_streamer.py"
    ).read_text(encoding="utf-8")
    assert "_open_ask in t for t in _earlier_agent_turns[-2:]" in src
