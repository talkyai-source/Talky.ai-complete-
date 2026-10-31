"""Conversation memory follows available model context, not a turn counter."""
import pytest

from app.domain.models.conversation import Message, MessageRole
from app.domain.services.voice_pipeline import turn_streamer
from tests.unit.test_model_driven_voice_turn import setup_turn


async def test_early_caller_correction_reaches_model_after_twenty_exchanges(monkeypatch):
    service, session, rounds = setup_turn(monkeypatch, "What did I tell you earlier?", ["You said you are a new customer."])
    history = [Message(role=MessageRole.USER, content="I am not an existing customer. This is a new business.")]
    for index in range(30):
        history.extend([
            Message(role=MessageRole.ASSISTANT, content=f"Here is detail {index}."),
            Message(role=MessageRole.USER, content=f"Please explain option {index}."),
        ])
    session.conversation_history = history
    await service._stream_llm_and_tts(session)
    assert rounds[0][0] == history
    assert session.conversation_history == history


def test_capacity_trimming_keeps_whole_exchanges_and_current_runtime_result(monkeypatch):
    monkeypatch.setattr(turn_streamer, "context_window_for", lambda _model: 1800)
    original = [
        Message(role=MessageRole.USER, content="Earlier caller detail. " * 300),
        Message(role=MessageRole.ASSISTANT, content="Earlier agent answer."),
        Message(role=MessageRole.USER, content="Recent question."),
        Message(role=MessageRole.ASSISTANT, content="Recent answer."),
        Message(role=MessageRole.SYSTEM, content="Current opt-out persistence result: recorded."),
        Message(role=MessageRole.USER, content="Please finish answering my question."),
    ]
    snapshot = list(original)
    selected, omitted = turn_streamer._history_for_context(
        original, model="small", system_prompt="Help the caller.", tools=[], max_tokens=100,
    )
    assert selected == original[2:]
    assert omitted == 2
    assert original == snapshot


def test_current_caller_quote_is_never_sliced_to_meet_context_estimate(monkeypatch):
    monkeypatch.setattr(turn_streamer, "context_window_for", lambda _model: 1800)
    latest = Message(role=MessageRole.USER, content="Exact current words. " * 1000)
    history = [Message(role=MessageRole.USER, content="Earlier turn."), latest]
    selected, omitted = turn_streamer._history_for_context(
        history, model="small", system_prompt="Help.", tools=[], max_tokens=100,
    )
    assert selected == [latest] and omitted == 1


async def test_true_capacity_omission_is_visible_without_changing_stored_history(monkeypatch):
    service, session, rounds = setup_turn(monkeypatch, "Please explain.", ["Which earlier detail do you mean?"])
    monkeypatch.setattr(turn_streamer, "context_window_for", lambda _model: 8192)
    previous = Message(role=MessageRole.USER, content="Old detail. " * 9000)
    session.conversation_history.insert(0, previous)
    canonical = list(session.conversation_history)
    await service._stream_llm_and_tts(session)
    assert previous not in rounds[0][0]
    assert rounds[0][0][-1] is canonical[-1]
    assert turn_streamer._HISTORY_OMISSION_NOTICE in rounds[0][1]["system_prompt"]
    assert session.conversation_history == canonical


@pytest.mark.parametrize("model", ["gpt-oss-120b", "openai/gpt-oss-20b", "gemini-2.5-flash"])
def test_declared_models_keep_long_normal_conversations_without_turn_limit(model):
    history = [Message(role=MessageRole.USER if i % 2 == 0 else MessageRole.ASSISTANT,
                       content=f"Conversation detail {i}: " + "ordinary words " * 20)
               for i in range(200)]
    selected, omitted = turn_streamer._history_for_context(
        history, model=model, system_prompt="Guide. " * 500,
        tools=[{"name": "read_company_sources"}], max_tokens=1000,
    )
    assert selected == history and omitted == 0
