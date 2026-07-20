import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.models.agent_config import AgentConfig, AgentGoal, ConversationFlow, ConversationRule
from app.domain.models.conversation import Message, MessageRole
from app.domain.models.conversation_state import ConversationContext, ConversationState
from app.domain.models.session import CallSession, CallState
from app.domain.services.voice_pipeline.identity_disposition import IdentityDisposition
from app.domain.services.voice_pipeline_service import VoicePipelineService


def _make_session() -> CallSession:
    session = CallSession(
        call_id="call-123",
        campaign_id="demo",
        lead_id="lead-123",
        provider_call_id="provider-123",
        system_prompt="Use plain spoken text only.",
        voice_id="voice-123",
        conversation_state=ConversationState.GREETING,
        conversation_context=ConversationContext(),
        agent_config=AgentConfig(
            goal=AgentGoal.INFORMATION_GATHERING,
            business_type="voice ai platform",
            agent_name="Assistant",
            company_name="Talky.ai",
            rules=ConversationRule(),
            flow=ConversationFlow(),
            response_max_sentences=2,
        ),
    )
    session.barge_in_event = asyncio.Event()
    return session


def test_pricing_questions_get_extended_sentence_budget_for_custom_prompt_sessions():
    service = VoicePipelineService(
        stt_provider=MagicMock(),
        llm_provider=AsyncMock(),
        tts_provider=AsyncMock(),
        media_gateway=AsyncMock(),
    )

    limit = service._response_max_sentences_for_turn(
        _make_session(),
        "Can you explain all your plans and pricing?",
        has_custom_prompt=True,
    )

    assert limit == 4


def test_non_pricing_questions_keep_default_sentence_budget():
    service = VoicePipelineService(
        stt_provider=MagicMock(),
        llm_provider=AsyncMock(),
        tts_provider=AsyncMock(),
        media_gateway=AsyncMock(),
    )

    limit = service._response_max_sentences_for_turn(
        _make_session(),
        "What does Talky do?",
        has_custom_prompt=True,
    )

    assert limit == 2


def test_ask_ai_end_session_action_parser():
    assert (
        VoicePipelineService._parse_ask_ai_end_session_action(
            '{"action":"end_session","reason":"user_goodbye","farewell":"See you soon."}'
        )
        == {"reason": "user_goodbye", "farewell": "See you soon.", "do_not_call": False}
    )
    assert (
        VoicePipelineService._parse_ask_ai_end_session_action(
            'Here is the action: {"action":"end_ask_ai_session","reason":"user_done"}'
        )
        == {"reason": "user_done", "farewell": "Goodbye, take care.", "do_not_call": False}
    )
    assert VoicePipelineService._parse_ask_ai_end_session_action(
        '{"action":"continue"}'
    ) is None
    assert VoicePipelineService._parse_ask_ai_end_session_action(
        "Can you explain how goodbye handling works?"
    ) is None


class _StreamingLLMProvider:
    def __init__(self, chunks):
        self._chunks = chunks

    async def stream_chat_with_timeout(self, *args, **kwargs):
        for chunk in self._chunks:
            yield chunk


@pytest.mark.asyncio
async def test_get_llm_response_strips_reasoning_and_keeps_full_pricing_answer():
    service = VoicePipelineService(
        stt_provider=MagicMock(),
        llm_provider=_StreamingLLMProvider([
            "<think>I should outline the pricing plan first.</think>",
            "\n### Plans\n",
            "1. **Basic** is $29 per month. ",
            "2. **Professional** is $79 per month. ",
            "3. **Enterprise** is custom pricing.",
        ]),
        tts_provider=AsyncMock(),
        media_gateway=AsyncMock(),
    )
    service.latency_tracker = MagicMock()

    session = _make_session()
    session.system_prompt = "Use plain spoken text only."
    session.conversation_history = [
        Message(role=MessageRole.USER, content="What are your packages and pricing?")
    ]

    response = await service.get_llm_response(
        session,
        "What are your packages and pricing?",
    )

    assert "<think>" not in response
    assert "**" not in response
    assert "#" not in response
    assert "1." not in response
    assert "2." not in response
    assert "3." not in response
    assert "outline the pricing plan first" not in response
    assert "Basic" in response
    assert "Professional" in response
    assert "Enterprise" in response


@pytest.mark.asyncio
async def test_synthesize_and_send_audio_clears_buffer_on_barge_in_without_muting():
    stt_provider = AsyncMock()
    media_gateway = AsyncMock()
    media_gateway.start_playback_tracking = MagicMock()
    tts_provider = AsyncMock()

    service = VoicePipelineService(
        stt_provider=stt_provider,
        llm_provider=AsyncMock(),
        tts_provider=tts_provider,
        media_gateway=media_gateway,
        mute_during_tts=False,
    )
    service.latency_tracker = MagicMock()
    service.latency_tracker.mark_tts_end = MagicMock()

    session = _make_session()
    session.turn_id = 3

    async def _interrupting_stream(*args, **kwargs):
        service._barge_in_events[session.call_id].set()
        yield MagicMock(data=b"\x00" * 3200)

    tts_provider.stream_synthesize = _interrupting_stream

    websocket = AsyncMock()

    await service.synthesize_and_send_audio(session, "Hello there.", websocket)

    stt_provider.mute.assert_not_awaited()
    stt_provider.unmute.assert_not_awaited()
    media_gateway.clear_output_buffer.assert_awaited_once_with(session.call_id)
    media_gateway.flush_audio_buffer.assert_not_awaited()
    websocket.send_json.assert_awaited_with({"type": "tts_interrupted", "reason": "barge_in"})


@pytest.mark.asyncio
async def test_synthesize_and_send_audio_skips_stale_reply_when_barge_in_is_pending():
    stt_provider = AsyncMock()
    media_gateway = AsyncMock()
    media_gateway.start_playback_tracking = MagicMock()
    tts_provider = AsyncMock()

    service = VoicePipelineService(
        stt_provider=stt_provider,
        llm_provider=AsyncMock(),
        tts_provider=tts_provider,
        media_gateway=media_gateway,
        mute_during_tts=False,
    )
    service.latency_tracker = MagicMock()
    service.latency_tracker.mark_tts_end = MagicMock()

    session = _make_session()
    session.turn_id = 4
    session.barge_in_event.set()

    websocket = AsyncMock()

    await service.synthesize_and_send_audio(session, "Outdated answer.", websocket)

    tts_provider.stream_synthesize.assert_not_called()
    media_gateway.clear_output_buffer.assert_awaited_once_with(session.call_id)
    media_gateway.flush_audio_buffer.assert_not_awaited()
    websocket.send_json.assert_awaited_with({"type": "tts_interrupted", "reason": "barge_in"})
    assert session.barge_in_event.is_set() is False


@pytest.mark.asyncio
async def test_ask_ai_llm_end_session_action_says_farewell_then_closes():
    media_gateway = AsyncMock()
    media_gateway.start_playback_tracking = MagicMock()
    media_gateway.wait_for_playback_complete = AsyncMock(return_value=True)
    # A browser / ask_ai gateway has no PBX hangup — so teardown happens
    # locally via on_call_ended (unlike the telephony path, where it is
    # deferred to ChannelDestroyed). Model that absence explicitly.
    media_gateway.hangup_call = None
    tts_provider = MagicMock()

    async def _farewell_audio_stream(*args, **kwargs):
        yield MagicMock(data=b"\x00" * 320)

    tts_provider.stream_synthesize = MagicMock(side_effect=_farewell_audio_stream)
    service = VoicePipelineService(
        stt_provider=AsyncMock(),
        llm_provider=_StreamingLLMProvider([
            '{"action":"end_ask_ai_session","reason":"user_goodbye","farewell":"See you soon."}'
        ]),
        tts_provider=tts_provider,
        media_gateway=media_gateway,
        mute_during_tts=False,
    )
    service.latency_tracker = MagicMock()
    service.latency_tracker.get_metrics.return_value = None

    session = _make_session()
    session.campaign_id = "ask-ai"
    session.current_user_input = "good bye"
    websocket = AsyncMock()

    await service.handle_turn_end(session, websocket)

    tts_provider.stream_synthesize.assert_called_once()
    assert tts_provider.stream_synthesize.call_args.args[0] == "See you soon."
    media_gateway.start_playback_tracking.assert_called_once_with(session.call_id)
    websocket.send_json.assert_any_await({"type": "tts_audio_complete"})
    media_gateway.wait_for_playback_complete.assert_awaited_once_with(session.call_id)
    media_gateway.on_call_ended.assert_awaited_once_with(session.call_id, "user_goodbye")
    websocket.send_json.assert_any_await(
        {"type": "session_ending", "reason": "user_goodbye"}
    )
    websocket.close.assert_awaited_once_with(code=1000, reason="user_goodbye")
    assert session.state == CallState.ENDED
    assert [message.role for message in session.conversation_history] == [MessageRole.USER]


@pytest.mark.asyncio
async def test_telephony_llm_end_session_action_says_farewell_then_hangs_up():
    media_gateway = AsyncMock()
    tts_provider = MagicMock()

    async def _farewell_audio_stream(*args, **kwargs):
        yield MagicMock(data=b"\x00" * 320)

    tts_provider.stream_synthesize = MagicMock(side_effect=_farewell_audio_stream)
    service = VoicePipelineService(
        stt_provider=AsyncMock(),
        llm_provider=_StreamingLLMProvider([
            '{"action":"end_session","reason":"user_goodbye","farewell":"Goodbye, have a good one."}'
        ]),
        tts_provider=tts_provider,
        media_gateway=media_gateway,
        mute_during_tts=False,
    )
    service.latency_tracker = MagicMock()
    service.latency_tracker.get_metrics.return_value = None

    session = _make_session()
    session.campaign_id = "campaign-123"
    session.current_user_input = "bye for now"

    await service.handle_turn_end(session, None)

    assert tts_provider.stream_synthesize.call_args.args[0] == "Goodbye, have a good one."
    media_gateway.hangup_call.assert_awaited_once_with(session.call_id, "user_goodbye")
    # PBX hangup was requested: the authoritative call-ended teardown (recording
    # save + gateway-session cleanup) is deferred to lifecycle._on_call_ended on
    # ChannelDestroyed. Tearing it down here would pop the recording buffer early
    # (the dropped-recordings root cause), so on_call_ended must NOT fire here.
    media_gateway.on_call_ended.assert_not_awaited()
    assert session.state == CallState.ENDED


# --- Defect 5 / Defect 6 — identity-disposition session-type gate and the
# explicit-goodbye exception in the reverse END_CALL-stripping gate ----------

def _make_end_to_end_tts_provider() -> MagicMock:
    """A tts_provider whose stream_synthesize yields one audio chunk, usable
    both for normal turn playback and for the deterministic-disposition /
    end-session farewell paths (mirrors the pattern used by the end-session
    tests above)."""
    async def _audio_stream(*args, **kwargs):
        yield MagicMock(data=b"\x00" * 320)

    tts_provider = MagicMock()
    tts_provider.stream_synthesize = MagicMock(side_effect=_audio_stream)
    return tts_provider


def _make_service_for_disposition(llm_chunks) -> VoicePipelineService:
    media_gateway = AsyncMock()
    media_gateway.start_playback_tracking = MagicMock()
    media_gateway.wait_for_playback_complete = AsyncMock(return_value=True)
    service = VoicePipelineService(
        stt_provider=AsyncMock(),
        llm_provider=_StreamingLLMProvider(llm_chunks),
        tts_provider=_make_end_to_end_tts_provider(),
        media_gateway=media_gateway,
        mute_during_tts=False,
    )
    service.latency_tracker = MagicMock()
    service.latency_tracker.get_metrics.return_value = None
    return service


@pytest.mark.asyncio
async def test_ask_ai_session_skips_identity_disposition_block():
    # Defect 5: an ask-AI (browser assistant) session saying "wrong number"
    # must NOT be deterministically hung up with a telephony close line — the
    # disposition block must be gated off entirely for this session type.
    service = _make_service_for_disposition(["No problem — how can I help you today?"])
    session = _make_session()
    session.campaign_id = "ask-ai"
    session.current_user_input = "Sorry, you've got the wrong number."
    websocket = AsyncMock()

    await service.handle_turn_end(session, websocket)

    assert session._turn_disposition == IdentityDisposition.NONE
    service.media_gateway.hangup_call.assert_not_awaited()
    assert session.state != CallState.ENDED


@pytest.mark.asyncio
async def test_telephony_session_wrong_business_still_ends_deterministically():
    # Telephony behavior (campaign_id is a real campaign/lead id, not
    # "ask-ai"/"voice-demo") must be unchanged by the session-type gate.
    service = _make_service_for_disposition([])  # LLM must not be invoked
    session = _make_session()
    session.campaign_id = "campaign-123"
    session.current_user_input = "Sorry, you've got the wrong company."
    websocket = AsyncMock()

    await service.handle_turn_end(session, websocket)

    assert session._turn_disposition == IdentityDisposition.WRONG_BUSINESS
    service.media_gateway.hangup_call.assert_awaited_once_with(
        session.call_id, "wrong_number_disposition"
    )
    assert session.state == CallState.ENDED


@pytest.mark.asyncio
async def test_wrong_person_plus_explicit_goodbye_honors_model_end_call():
    # Defect 6: person-mismatch evidence AND an explicit goodbye in the SAME
    # utterance means the model's own END_CALL should be honored, not stripped.
    service = _make_service_for_disposition(
        ["Alright, thank you, goodbye! [[END_CALL]]"]
    )
    session = _make_session()
    session.campaign_id = "campaign-123"
    session.current_user_input = "She's not here — goodbye."
    websocket = AsyncMock()

    await service.handle_turn_end(session, websocket)

    assert session._turn_disposition == IdentityDisposition.WRONG_PERSON
    service.media_gateway.hangup_call.assert_awaited_once_with(
        session.call_id, "agent_end_call"
    )


@pytest.mark.asyncio
async def test_wrong_person_without_goodbye_still_strips_model_end_call():
    # Existing invariant preserved: person-mismatch ALONE (no goodbye) must
    # never auto-hang-up — the model's END_CALL is stripped and the call
    # stays alive so the pivot can happen.
    service = _make_service_for_disposition(
        ["Sure, no worries. [[END_CALL]]"]
    )
    session = _make_session()
    session.campaign_id = "campaign-123"
    session.current_user_input = "She's not here."
    websocket = AsyncMock()

    await service.handle_turn_end(session, websocket)

    assert session._turn_disposition == IdentityDisposition.WRONG_PERSON
    service.media_gateway.hangup_call.assert_not_awaited()
    assert getattr(session, "_end_call_requested", False) is False


@pytest.mark.asyncio
async def test_goodbye_alone_does_not_affect_a_non_wrong_person_end_call():
    # Bare goodbye (no wrong-person/wrong-business evidence) resolves to
    # disposition NONE, so the reverse gate's condition never engages in the
    # first place — the model's END_CALL is honored exactly as it always was.
    service = _make_service_for_disposition(["Goodbye now! [[END_CALL]]"])
    session = _make_session()
    session.campaign_id = "campaign-123"
    session.current_user_input = "Goodbye."
    websocket = AsyncMock()

    await service.handle_turn_end(session, websocket)

    assert session._turn_disposition == IdentityDisposition.NONE
    service.media_gateway.hangup_call.assert_awaited_once_with(
        session.call_id, "agent_end_call"
    )


@pytest.mark.asyncio
async def test_turn_disposition_does_not_go_stale_across_turns():
    # Staleness check: a WRONG_PERSON turn followed by an ordinary turn must
    # NOT leave the reverse gate acting on the first turn's stale disposition.
    service = _make_service_for_disposition(
        [
            "Got it, thanks for letting me know.",  # turn 1: pivot, no END_CALL
            "Great, take care! [[END_CALL]]",        # turn 2: ordinary end_call
        ]
    )
    session = _make_session()
    session.campaign_id = "campaign-123"
    websocket = AsyncMock()

    session.current_user_input = "She's not here."
    await service.handle_turn_end(session, websocket)
    assert session._turn_disposition == IdentityDisposition.WRONG_PERSON
    service.media_gateway.hangup_call.assert_not_awaited()

    session.current_user_input = "Sounds good, thanks a lot!"
    await service.handle_turn_end(session, websocket)
    assert session._turn_disposition == IdentityDisposition.NONE
    service.media_gateway.hangup_call.assert_awaited_once_with(
        session.call_id, "agent_end_call"
    )


def _make_service_for_bargein() -> VoicePipelineService:
    service = VoicePipelineService(
        stt_provider=AsyncMock(),
        llm_provider=AsyncMock(),
        tts_provider=AsyncMock(),
        media_gateway=AsyncMock(),
        mute_during_tts=False,
    )
    service.latency_tracker = MagicMock()
    return service


@pytest.mark.asyncio
async def test_cancel_active_turn_cancels_in_flight_task():
    """On hangup/teardown, an in-flight turn task must be cancelled so it stops
    streaming TTS to a gone channel."""
    service = _make_service_for_bargein()
    session = _make_session()
    cancelled = asyncio.Event()

    async def _running():
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    task = asyncio.create_task(_running())
    service._register_active_turn_task(session.call_id, task)
    await asyncio.sleep(0)

    await service.cancel_active_turn(session.call_id)
    await asyncio.wait_for(cancelled.wait(), timeout=1)
    assert service._pending_llm_tasks.get(session.call_id) is None


@pytest.mark.asyncio
async def test_cancel_active_turn_noop_when_nothing_running():
    service = _make_service_for_bargein()
    # No registered task — must not raise.
    await service.cancel_active_turn("no-such-call")


@pytest.mark.asyncio
async def test_handle_barge_in_protects_final_task_before_tts_starts():
    """A confirmed FINAL answer still in the LLM phase (no audio playing yet)
    must NOT be cancelled by a StartOfTurn — that was the silent-call bug.
    The caller's new words become the next turn; the answer finishes & speaks."""
    service = _make_service_for_bargein()
    session = _make_session()
    session.tts_active = False  # answer has not begun playing

    started = asyncio.Event()

    async def _running_final():
        started.set()
        await asyncio.sleep(10)

    turn_task = asyncio.create_task(_running_final())
    turn_task._turn_type = "final"
    service._register_active_turn_task(session.call_id, turn_task)
    await asyncio.wait_for(started.wait(), timeout=1)

    await service.handle_barge_in(session, AsyncMock())

    # The final task must survive and remain registered.
    assert not turn_task.done()
    assert service._pending_llm_tasks.get(session.call_id) is turn_task
    turn_task.cancel()


@pytest.mark.asyncio
async def test_handle_barge_in_cancels_speculative_task_before_tts():
    """A SPECULATIVE task (user may still be talking) IS cancelled by a
    barge-in even before TTS — it is tentative."""
    service = _make_service_for_bargein()
    session = _make_session()
    session.tts_active = False

    cancelled = asyncio.Event()

    async def _running_spec():
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    turn_task = asyncio.create_task(_running_spec())
    turn_task._turn_type = "speculative"
    service._register_active_turn_task(session.call_id, turn_task)
    await asyncio.sleep(0)

    await service.handle_barge_in(session, AsyncMock())
    await asyncio.wait_for(cancelled.wait(), timeout=1)
    assert service._pending_llm_tasks.get(session.call_id) is None


@pytest.mark.asyncio
async def test_handle_barge_in_cancels_final_task_when_tts_playing():
    """When TTS is actively playing, interrupting a FINAL answer IS a real
    barge-in and must cancel it (the caller is cutting off audible speech)."""
    service = _make_service_for_bargein()
    session = _make_session()
    session.tts_active = True  # answer is being spoken

    cancelled = asyncio.Event()

    async def _running_final():
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    turn_task = asyncio.create_task(_running_final())
    turn_task._turn_type = "final"
    service._register_active_turn_task(session.call_id, turn_task)
    await asyncio.sleep(0)

    await service.handle_barge_in(session, AsyncMock())
    await asyncio.wait_for(cancelled.wait(), timeout=1)
    assert service._pending_llm_tasks.get(session.call_id) is None
    assert session.tts_active is False


@pytest.mark.asyncio
async def test_handle_barge_in_cancels_active_turn_task_immediately():
    media_gateway = AsyncMock()
    service = VoicePipelineService(
        stt_provider=AsyncMock(),
        llm_provider=AsyncMock(),
        tts_provider=AsyncMock(),
        media_gateway=media_gateway,
        mute_during_tts=False,
    )
    service.latency_tracker = MagicMock()

    session = _make_session()
    session.tts_active = True
    websocket = AsyncMock()
    cancelled = asyncio.Event()

    async def _running_turn():
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    turn_task = asyncio.create_task(_running_turn())
    service._register_active_turn_task(session.call_id, turn_task)
    await asyncio.sleep(0)

    await service.handle_barge_in(session, websocket)
    await asyncio.wait_for(cancelled.wait(), timeout=1)
    with pytest.raises(asyncio.CancelledError):
        await turn_task

    assert session.llm_active is False
    assert session.tts_active is False
    assert session.state == CallState.LISTENING
    media_gateway.clear_output_buffer.assert_awaited_once_with(session.call_id)
    sent_payload = websocket.send_json.await_args.args[0]
    assert sent_payload["type"] == "barge_in"
    assert sent_payload["message"] == "User started speaking, stopping TTS"
    assert isinstance(sent_payload["timestamp"], str)


@pytest.mark.asyncio
async def test_run_turn_commits_partial_assistant_reply_on_barge_in():
    """Barge-in with a non-empty LLM response: both user and assistant are committed
    to preserve user→assistant alternation in history (prevents consecutive user messages)."""
    service = VoicePipelineService(
        stt_provider=AsyncMock(),
        llm_provider=AsyncMock(),
        tts_provider=AsyncMock(),
        media_gateway=AsyncMock(),
        mute_during_tts=False,
    )
    service.latency_tracker = MagicMock()
    service.latency_tracker.get_metrics.return_value = None
    service.get_llm_response = AsyncMock(return_value="Hello there.")
    service.synthesize_and_send_audio = AsyncMock(return_value=True)
    service.transcript_service = MagicMock()
    service.transcript_service.flush_to_database = AsyncMock()

    session = _make_session()
    websocket = AsyncMock()

    await service._run_turn(session, "Tell me about Talky.", websocket, turn_id=2)

    assert [message.role for message in session.conversation_history] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
    ]
    service.transcript_service.accumulate_turn.assert_called_once()


# --- F-17 (2026-07-20) — backchannel-authority unification -----------------
@pytest.mark.asyncio
async def test_bare_no_after_non_question_is_not_suppressed_as_backchannel():
    # turn_ender used to import is_backchannel from interruption_filter, whose
    # set wrongly includes "no"/"nope"/"nah" as backchannels — a caller saying
    # "No" (real disagreement/content) after a NON-question agent line would
    # be silently swallowed (early-return, never reaches the LLM) instead of
    # becoming a real turn. The correct authority
    # (voice_pipeline.backchannel.is_backchannel) deliberately EXCLUDES
    # "no"/"nope"/"stop"/"wait" — they must barge in / register as real
    # content, never be suppressed as a listening noise.
    service = VoicePipelineService(
        stt_provider=AsyncMock(),
        llm_provider=AsyncMock(),
        tts_provider=AsyncMock(),
        media_gateway=AsyncMock(),
        mute_during_tts=False,
    )
    service.latency_tracker = MagicMock()
    service.latency_tracker.get_metrics.return_value = None
    service.transcript_service = MagicMock()
    service._run_turn = AsyncMock(return_value=("Got it, no problem.", 10.0, 10.0))

    session = _make_session()
    session.campaign_id = "campaign-123"
    # Prior user turn + a non-question last assistant line — exactly the
    # condition under which the old code wrongly suppressed a bare "No".
    session.conversation_history.append(Message(role=MessageRole.USER, content="Hi there"))
    session.conversation_history.append(
        Message(role=MessageRole.ASSISTANT, content="Okay, I can help with that.")
    )
    session.current_user_input = "No"
    websocket = AsyncMock()

    await service.handle_turn_end(session, websocket)

    service._run_turn.assert_awaited_once()
    passed_transcript = service._run_turn.call_args.args[1]
    assert passed_transcript == "No", passed_transcript


# --- F-13 / F-14 / F-15 / F-11b (2026-07-20) — hardening today's disposition
#     work against the listening-path audit --------------------------------
@pytest.mark.asyncio
async def test_deterministic_dnc_persists_opt_out_and_hangs_up():
    # F-13: the deterministic DNC path SPOKE "I'll take you off the list" but
    # never set _caller_opted_out, so teardown's opt-out purge never ran. It
    # must now mirror the LLM-JSON path and flag the session.
    service = _make_service_for_disposition([])  # no LLM — deterministic path
    session = _make_session()
    session.campaign_id = "campaign-123"
    session.current_user_input = "Please stop calling me."
    websocket = AsyncMock()

    await service.handle_turn_end(session, websocket)

    assert session._turn_disposition == IdentityDisposition.DNC
    assert getattr(session, "_caller_opted_out", False) is True
    service.media_gateway.hangup_call.assert_awaited_once()


@pytest.mark.asyncio
async def test_deterministic_dnc_survives_repetition_guard():
    # F-13(d): an emphatic repeated "no ... stop calling me" is >50% one word,
    # so the repetitive-STT guard used to drop it before classification. It must
    # now reach the DNC path and persist the opt-out.
    service = _make_service_for_disposition([])
    session = _make_session()
    session.campaign_id = "campaign-123"
    session.current_user_input = "no no no no no no stop calling me"
    websocket = AsyncMock()

    await service.handle_turn_end(session, websocket)

    assert session._turn_disposition == IdentityDisposition.DNC
    assert getattr(session, "_caller_opted_out", False) is True


@pytest.mark.asyncio
async def test_wrong_business_is_not_an_opt_out():
    # A wrong-business end must NOT flag an opt-out (it's a wrong number, not a
    # do-not-call request) — the persistence is DNC-only.
    service = _make_service_for_disposition([])
    session = _make_session()
    session.campaign_id = "campaign-123"
    session.current_user_input = "Sorry, you've got the wrong company."
    await service.handle_turn_end(session, AsyncMock())

    assert session._turn_disposition == IdentityDisposition.WRONG_BUSINESS
    assert getattr(session, "_caller_opted_out", False) is False


@pytest.mark.asyncio
async def test_json_end_on_wrong_person_turn_is_suppressed():
    # F-15: the JSON end-session path is the OTHER hangup gate and never checked
    # the disposition. A wrong-person turn where the model emits a
    # conversation_complete end action must NOT hang up on a valid prospect —
    # even though should_honor_end_session WOULD honor it (>=3 user turns).
    service = _make_service_for_disposition(
        ['{"action":"end_ask_ai_session","reason":"conversation_complete","farewell":"Goodbye."}']
    )
    session = _make_session()
    session.campaign_id = "campaign-123"  # telephony
    for _ in range(3):  # make should_honor_end_session want to honor it
        session.conversation_history.append(Message(role=MessageRole.USER, content="hi"))
        session.conversation_history.append(Message(role=MessageRole.ASSISTANT, content="ok"))
    session.current_user_input = "David isn't here right now."
    websocket = AsyncMock()

    await service.handle_turn_end(session, websocket)

    assert session._turn_disposition == IdentityDisposition.WRONG_PERSON
    assert session.state != CallState.ENDED
    service.media_gateway.hangup_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_json_do_not_call_still_ends_even_on_identity_turn():
    # F-15 exemption: do_not_call is a real opt-out and must ALWAYS end +
    # persist, never suppressed by the wrong-person gate.
    service = _make_service_for_disposition(
        ['{"action":"end_ask_ai_session","reason":"user_request","do_not_call":true,"farewell":"Understood."}']
    )
    session = _make_session()
    session.campaign_id = "campaign-123"
    # A non-identity transcript so disposition is NONE (do_not_call must end
    # regardless of disposition anyway).
    session.current_user_input = "yeah whatever, I'm done."
    websocket = AsyncMock()

    await service.handle_turn_end(session, websocket)

    assert getattr(session, "_caller_opted_out", False) is True
    assert session.state == CallState.ENDED


@pytest.mark.asyncio
async def test_identity_clarify_flag_is_consumed_after_one_turn():
    # F-14(d): _identity_clarify_asked was set but never cleared, so a single
    # clarify permanently armed the aggressive post-clarify branch for the rest
    # of the call. It must be consumed the turn we act on it.
    service = _make_service_for_disposition(["Let me get the right person for you."])
    session = _make_session()
    session.campaign_id = "campaign-123"
    session._identity_clarify_asked = True
    session.current_user_input = "Just the wrong person, David moved teams."

    await service.handle_turn_end(session, AsyncMock())

    assert session._identity_clarify_asked is False
    assert session._turn_disposition == IdentityDisposition.WRONG_PERSON


@pytest.mark.asyncio
async def test_disposition_early_returns_reset_speculative_snapshot():
    # F-11b: the deterministic early-returns append to history but return before
    # the finally-cleanup that clears the speculative snapshot. Left stale, a
    # barge-in truncates the just-committed exchange. Both paths must reset it.
    # WRONG_BUSINESS / DNC end path:
    service = _make_service_for_disposition([])
    session = _make_session()
    session.campaign_id = "campaign-123"
    session._speculative_history_len = 0  # stale pre-turn snapshot
    session.current_user_input = "You've got the wrong company."
    await service.handle_turn_end(session, AsyncMock())
    assert session._speculative_history_len is None

    # AMBIGUOUS clarify path (call CONTINUES — matters most):
    service2 = _make_service_for_disposition([])
    session2 = _make_session()
    session2.campaign_id = "campaign-123"
    session2._speculative_history_len = 0
    session2.current_user_input = "Wrong number."
    await service2.handle_turn_end(session2, AsyncMock())
    assert session2._turn_disposition == IdentityDisposition.AMBIGUOUS
    assert session2._speculative_history_len is None
