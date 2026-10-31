"""Provider truncation is not successful speech or permission to replay a tool."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.models.conversation import Message, MessageRole
from app.infrastructure.llm.groq import GroqLLMProvider, LLMStreamStalled


def _chunk(text=None, *, reason=None, calls=None):
    return SimpleNamespace(choices=[SimpleNamespace(
        delta=SimpleNamespace(content=text, tool_calls=calls), finish_reason=reason,
    )])


def _provider(chunks):
    async def stream():
        for chunk in chunks:
            if isinstance(chunk, Exception):
                raise chunk
            yield chunk
    provider = GroqLLMProvider()
    create = AsyncMock(side_effect=lambda **_: stream())
    provider._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    return provider, create


@pytest.mark.parametrize("reason", ["length", "content_filter"])
@pytest.mark.parametrize("content", [None, "This is an unfinished"])
async def test_incomplete_provider_reason_raises_without_same_provider_replay(reason, content):
    provider, create = _provider([_chunk(content), _chunk(reason=reason)])
    with pytest.raises(LLMStreamStalled):
        _ = [x async for x in provider.stream_chat([Message(role=MessageRole.USER, content="Hello")])]
    assert create.await_count == 1


@pytest.mark.parametrize("reason", ["length", None])
async def test_truncated_tool_arguments_never_reach_runner_or_continuation(reason):
    fragment = SimpleNamespace(index=0, id="call_fixture", function=SimpleNamespace(
        name="lookup", arguments='{"query":"warranty"}',
    ))
    provider, create = _provider([_chunk(calls=[fragment]), _chunk(reason=reason)])
    runner = AsyncMock(return_value={"success": True})
    tool = {"type": "function", "function": {"name": "lookup", "parameters": {
        "type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"],
    }}}
    with pytest.raises(LLMStreamStalled):
        _ = [x async for x in provider.stream_chat_with_tools(
            [Message(role=MessageRole.USER, content="Check the warranty")], tools=[tool], tool_runner=runner,
        )]
    runner.assert_not_awaited()
    assert create.await_count == 1


async def test_normal_stop_still_returns_complete_response():
    provider, create = _provider([_chunk("Hello."), _chunk(reason="stop")])
    assert [x async for x in provider.stream_chat([Message(role=MessageRole.USER, content="Hello")])] == ["Hello."]
    assert create.await_count == 1


@pytest.mark.parametrize("content", [None, "This sentence looks complete."])
async def test_clean_http_eof_without_terminal_reason_is_incomplete(content):
    provider, create = _provider([_chunk(content)])
    with pytest.raises(LLMStreamStalled, match="terminal reason"):
        _ = [x async for x in provider.stream_chat([Message(role=MessageRole.USER, content="Hello")])]
    assert create.await_count == 1


@pytest.mark.parametrize("done_marker", [False, True])
async def test_real_sdk_clean_sse_eof_without_choice_completion_is_rejected(done_marker):
    import httpx
    from groq import AsyncGroq

    requests = []

    def respond(request):
        requests.append(request)
        event = 'data: {"id":"fixture","object":"chat.completion.chunk","created":0,"model":"fixture","choices":[{"index":0,"delta":{"content":"An unfinished answer"},"finish_reason":null}]}\n\n'
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              text=event + ("data: [DONE]\n\n" if done_marker else ""))

    provider = GroqLLMProvider()
    provider._client = AsyncGroq(api_key="synthetic-test-placeholder", max_retries=0,
                                http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)))
    try:
        with pytest.raises(LLMStreamStalled, match="terminal reason"):
            _ = [x async for x in provider.stream_chat([Message(role=MessageRole.USER, content="Hello")])]
        assert len(requests) == 1
    finally:
        await provider.cleanup()


async def test_successful_tool_terminal_publishes_assembled_call():
    fragment = SimpleNamespace(index=0, id="call_fixture", function=SimpleNamespace(
        name="lookup", arguments='{"query":"warranty"}',
    ))
    provider, create = _provider([_chunk(calls=[fragment]), _chunk(reason="tool_calls")])
    sink = []
    assert [x async for x in provider.stream_chat(
        [Message(role=MessageRole.USER, content="Check warranty")], tool_calls_sink=sink,
    )] == []
    assert sink[0]["name"] == "lookup"
    assert sink[0]["arguments"] == {"query": "warranty"}
    assert create.await_count == 1


async def test_consumer_closing_before_terminal_reason_is_not_provider_failure():
    provider, create = _provider([_chunk("Hello."), _chunk("More words."), _chunk(reason="stop")])
    stream = provider.stream_chat([Message(role=MessageRole.USER, content="Hello")])
    assert await anext(stream) == "Hello."
    await stream.aclose()
    assert create.await_count == 1


@pytest.mark.parametrize("failure", ["length", "eof", "timeout", "transport_error"])
async def test_actual_streamer_drops_unsubmitted_tail_after_partial_speech(monkeypatch, failure):
    from tests.unit.test_voice_pipeline_service import _make_service_for_disposition, _make_session
    monkeypatch.setenv("TELEPHONY_FILLER_DELAY_MS", "0")
    end = (_chunk(reason="length") if failure == "length" else
           _chunk() if failure == "eof" else
           LLMStreamStalled("Synthetic wait budget exhausted") if failure == "timeout" else
           RuntimeError("Synthetic transport failure"))
    provider, create = _provider([_chunk("I can explain that. "), _chunk("Here is the unfinished"), end])
    service = _make_service_for_disposition([])
    service.llm_provider = provider
    service.synthesize_and_send_audio = AsyncMock(return_value=False)
    session = _make_session()
    session.turn_id = 4
    session.conversation_history = [Message(role=MessageRole.USER, content="Please explain.")]
    response, _, _ = await service._stream_llm_and_tts(session, None)
    submitted = [call.args[1] for call in service.synthesize_and_send_audio.await_args_list]
    assert submitted == ["I can explain that."]
    assert response == " ".join(submitted)
    assert "unfinished" not in response
    assert create.await_count == 1  # No replacement model turn after partial speech.
    # Submission alone must not invent a correlated playback receipt.
    assert not getattr(session, "_tts_playout_completed", False)
    assert not getattr(session, "_voice_action_delivered_text", "")


async def test_partial_error_discards_unsubmitted_end_token_before_aggregate_parse(monkeypatch):
    from tests.unit.test_voice_pipeline_service import _make_service_for_disposition, _make_session
    monkeypatch.setenv("TELEPHONY_FILLER_DELAY_MS", "0")
    # Exercise the sentinel compatibility path, where no action-tool buffer
    # hides a sentence already submitted before the provider fails.
    monkeypatch.setattr(GroqLLMProvider, "supports_tools", False)
    provider, create = _provider([
        _chunk("I can explain that. "), _chunk(" [[END_CALL]]"), _chunk(reason="length"),
    ])
    service = _make_service_for_disposition([])
    service.llm_provider = provider
    service.synthesize_and_send_audio = AsyncMock(return_value=False)
    session = _make_session()
    session.turn_id = 4
    # Caller intent would permit a token; this proves the *unsubmitted tail*
    # never reaches the aggregate parser. TurnEnder owns the caller fallback.
    session.conversation_history = [Message(role=MessageRole.USER, content="Goodbye.")]
    response, _, _ = await service._stream_llm_and_tts(session, None)
    assert response == "I can explain that."
    assert not getattr(session, "_end_call_requested", False)
    assert create.await_count == 1


@pytest.mark.parametrize("tail,expected_chunks", [
    ("There is more detail available.", 3),
    ("Would you like more detail?", 4),
    ("Would you like us to work through the information together, and check which details are relevant?", 4),
])
async def test_normal_clause_chunk_cap_history_matches_only_completed_submissions(monkeypatch, tail, expected_chunks):
    from tests.unit.test_voice_pipeline_service import _make_service_for_disposition, _make_session
    monkeypatch.setenv("TELEPHONY_FILLER_DELAY_MS", "0")
    # Real token pacing flushes the long clause before the first full stop.
    # Counting full stops again for history grants text an extra, unsaid turn.
    paragraph = (
        "We can discuss the information you want to review, and work through the details together. "
        "The next step depends on your preference. "
        + tail
    )
    provider, create = _provider([*(_chunk(char) for char in paragraph), _chunk(reason="stop")])
    service = _make_service_for_disposition([])
    service.llm_provider = provider
    service.synthesize_and_send_audio = AsyncMock(return_value=False)
    session = _make_session()
    session.turn_id = 4
    session.conversation_history = [Message(role=MessageRole.USER, content="Please explain.")]
    response, _, _ = await service._stream_llm_and_tts(session, None)
    submitted = [call.args[1] for call in service.synthesize_and_send_audio.await_args_list]
    assert len(submitted) == expected_chunks
    assert submitted[0].endswith(",")
    assert response == " ".join(submitted)
    assert (tail in response) == (expected_chunks == 4)  # Existing one-question grace still works.
    assert create.await_count == 1
    assert not getattr(session, "_tts_playout_completed", False)
    assert not getattr(session, "_voice_action_delivered_text", "")


@pytest.mark.parametrize("paragraph,expected_chunks,expected", [
    (
        "Sorry for the mix-up. I don't have information about why your number was called, and I won't assume you're a customer.",
        3,
        "Sorry for the mix-up. I don't have information about why your number was called, and I won't assume you're a customer.",
    ),
    ("First answer. Second detail. Extra material.", 2, "First answer. Second detail."),
])
async def test_cap_counts_complete_sentences_instead_of_playback_chunks(monkeypatch, paragraph, expected_chunks, expected):
    from tests.unit.test_voice_pipeline_service import _make_service_for_disposition, _make_session
    monkeypatch.setenv("TELEPHONY_FILLER_DELAY_MS", "0")
    provider, create = _provider([*(_chunk(char) for char in paragraph), _chunk(reason="stop")])
    service = _make_service_for_disposition([])
    service.llm_provider = provider
    service.synthesize_and_send_audio = AsyncMock(return_value=False)
    session = _make_session()
    session.turn_id = 4
    session.conversation_history = [Message(role=MessageRole.USER, content="Please explain.")]
    response, _, _ = await service._stream_llm_and_tts(session, None)
    submitted = [call.args[1] for call in service.synthesize_and_send_audio.await_args_list]
    assert len(submitted) == expected_chunks
    assert response == " ".join(submitted) == expected
    assert create.await_count == 1


@pytest.mark.parametrize("failure", ["provider", "barge_in"])
async def test_early_clause_submission_survives_failure_before_sentence_completes(monkeypatch, failure):
    from tests.unit.test_voice_pipeline_service import _make_service_for_disposition, _make_session
    monkeypatch.setenv("TELEPHONY_FILLER_DELAY_MS", "0")
    paragraph = "We can discuss the information you want to review, and work through the details together"
    chunks = [*(_chunk(char) for char in paragraph)]
    chunks += [_chunk(reason="length")] if failure == "provider" else [_chunk("."), _chunk(reason="stop")]
    provider, create = _provider(chunks)
    service = _make_service_for_disposition([])
    service.llm_provider = provider
    session = _make_session()
    session.turn_id = 4
    session.conversation_history = [Message(role=MessageRole.USER, content="Please explain.")]
    service._barge_in_events[session.call_id] = session.barge_in_event
    submitted = []

    async def synthesize(_session, text, *_args, **_kwargs):
        submitted.append(text)
        if failure == "barge_in" and len(submitted) == 2:
            session.barge_in_event.set()
            return True
        return False

    service.synthesize_and_send_audio = synthesize
    response, _, _ = await service._stream_llm_and_tts(session, None)
    assert submitted[0] == "We can discuss the information you want to review,"
    expected = submitted[0] + (" [interrupted by caller]" if failure == "barge_in" else "")
    assert response == expected
    assert session._spoken_sentences == [submitted[0]]
    assert create.await_count == 1
