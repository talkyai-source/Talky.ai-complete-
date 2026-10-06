"""Conversation progress must follow caller evidence, not scripted milestones."""
from types import SimpleNamespace

import pytest

from app.domain.services.end_session_action import caller_signaled_end, repeated_decline_allows_end
from app.domain.services.voice_pipeline.turn_runner import _note_unheard_greeting_bargein
from app.services.scripts.prompts.live_state import build_live_state_block


@pytest.mark.parametrize("text", [
    "No thanks to email.", "I'm not interested in SMS.",
    "That's all for my email.", "I'm done giving my number.",
    "No, I don't have a card machine.", "Thanks, I am new to taking payments.",
])
def test_topic_refusal_or_factual_negative_never_authorizes_model_hangup(text):
    assert not caller_signaled_end(text)
    assert not repeated_decline_allows_end(text, 3)


@pytest.mark.parametrize("text", [
    "No thanks.", "I'm not interested.", "I'm done, thanks.",
    "No thanks to email. Goodbye.", "Please end the call.", "Stop calling me.",
    "I'm done with this call.", "Not interested in this conversation.", "That's all for now.",
])
def test_actual_call_refusal_or_goodbye_still_authorizes_hangup(text):
    assert caller_signaled_end(text)


def test_unheard_opening_interruptions_do_not_invent_delivered_identity():
    session = SimpleNamespace()
    for _ in range(3):
        _note_unheard_greeting_bargein(session)
    assert not getattr(session, "_has_introduced", False)
    block = build_live_state_block(agent_name="Ava", company_name="Northwind",
        opening_interrupted=bool(session._greeting_bargein_count))
    assert "interrupted" in block
    assert "latest" in block
    assert "ALREADY introduced" not in block
    assert "then stop and let them answer" not in block
