"""Hidden pushes behind repetitive, scripted calls (2026-10-01).

Cascaded test cb1b28c3: "Nothing." three times, four rephrasings of the same
question -- a plain no never registered anywhere the model reads last.
Realtime test 1ddf8844: "Let me think about the best next question here."
spoken aloud, a question answered by the agent itself, and the call-or-
WhatsApp question pushed three times.
"""
from __future__ import annotations

from app.domain.models.conversation import Message, MessageRole
from app.domain.services.voice_pipeline.conversation_craft import CRAFT_REANCHOR
from app.domain.services.voice_pipeline.conversation_guards import (
    declined_note,
    is_bare_no,
)


def _a(text):
    return Message(role=MessageRole.ASSISTANT, content=text)


def _u(text):
    return Message(role=MessageRole.USER, content=text)


# ── cascaded: a plain no closes the topic ──────────────────────────────────

def test_the_calls_three_nothings_become_a_stop_signal():
    history = [
        _a("Anything new in the business since you last spoke with Azian, like a new site or busier trade?"),
        _u("There are plenty of things as I mentioned."),
        _a("What would you like Azian to look at first?"),
        _u("Nothing?"),
        _a("What would you say is the biggest change or challenge right now?"),
        _u("Nothing."),
    ]
    note = declined_note(history)
    assert "What would you like Azian to look at first?" in note
    assert "do not ask it again in other words" in note
    assert "stop probing" in note


def test_one_no_closes_the_topic_but_is_not_yet_a_stop():
    note = declined_note([_a("Would you like a quick call?"), _u("No thanks."), _a("Okay."), _u("Well, tell me about Dojo Plus.")])
    assert "Would you like a quick call?" in note
    assert "stop probing" not in note


def test_a_real_answer_is_not_a_decline():
    assert not is_bare_no("No, I use Square now")
    assert not is_bare_no("Yes")
    assert declined_note([_a("Are you still with Dojo?"), _u("No, I use Square now.")]) is None


def test_the_trailing_block_scopes_refusal_without_treating_every_no_as_goodbye():
    text = " ".join(CRAFT_REANCHOR.split())
    assert "A refusal closes that offer, not every topic" in text
    assert 'A factual "no" describes their situation; it is not a refusal' in text
    assert "thanks alone is not goodbye" in text


# ── realtime: no narration, one reply, one offer, no invented claims ──────

def _realtime_text():
    from app.realtime.prompts import RealtimePersona, build_realtime_instructions

    return build_realtime_instructions(RealtimePersona(persona_type="sales"))


def test_realtime_never_narrates_its_thinking():
    text = _realtime_text()
    assert "I'll check the details." not in text
    assert "Never say that you are thinking" in text


def test_realtime_says_one_reply_then_listens():
    assert "never answer it yourself, and never add a closing line after it" in _realtime_text()


def test_realtime_offers_a_next_step_once():
    text = " ".join(_realtime_text().split())
    assert "Offer one available next step when it fits their stated need" in text
    assert "If they decline it, do not repackage the same offer" in text
    assert "Offer a team handoff only if the runtime explicitly supplies that capability" in text


def test_realtime_claims_nothing_beyond_what_was_confirmed():
    assert "not proof it works for WhatsApp" in _realtime_text()


def test_the_declined_note_is_sent_last_to_the_model():
    from pathlib import Path

    src = Path(__file__).resolve().parents[2].joinpath(
        "app", "domain", "services", "voice_pipeline", "turn_streamer.py"
    ).read_text(encoding="utf-8")
    assert "declined_note(messages)" in src
