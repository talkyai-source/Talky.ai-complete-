"""Caller activity, not an old model tool, owns the pending Realtime hangup."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.realtime.bridge import RealtimeBridge
from app.realtime.openai import RealtimeEvent


def _fixture():
    provider = SimpleNamespace(close=AsyncMock(), update_live_state=AsyncMock(),
        send_function_result=AsyncMock(), interrupt_with_text=AsyncMock(),
        update_contact_directive=AsyncMock(), truncate_response=AsyncMock())
    gateway = SimpleNamespace(send_audio=AsyncMock(), begin_playback=AsyncMock(),
        clear_output_buffer=AsyncMock(), finish_playback=AsyncMock(return_value={
            "utterance_id": "rt-1", "status": "completed", "evidence": "transport_played", "played_ms": 40}))
    ended, session = AsyncMock(), SimpleNamespace()
    bridge = RealtimeBridge(call_id="synthetic-close", realtime_session=provider,
        media_gateway=gateway, action_session=session, on_end_call=ended, call_direction="inbound")
    return bridge, provider, gateway, ended, session


async def _events(bridge, provider, *events):
    async def stream():
        for event in events:
            yield event
    provider.events = stream
    await bridge._pump_model_events()
    if bridge._playback_task:
        await asyncio.gather(bridge._playback_task, return_exceptions=True)


async def _end_tool(bridge):
    await bridge._handle_function_call(SimpleNamespace(name="end_call", call_id="tool-close", parsed_arguments=lambda: {}))


@pytest.mark.asyncio
async def test_new_caller_request_revokes_accepted_tool_hangup():
    bridge, provider, _, ended, session = _fixture()
    bridge._latest_caller_text = "Goodbye."
    await _end_tool(bridge)
    old_close = bridge._termination_task
    await _events(bridge, provider, RealtimeEvent(kind="caller_transcript", is_final=True,
        text="Wait, do not hang up. I need help."))
    bridge._goodbye_completed.set()  # A late completion must not revive it.
    await asyncio.sleep(0)
    assert old_close.cancelled() or old_close.done()
    ended.assert_not_awaited()
    assert not session._end_call_requested
    await bridge.stop()


@pytest.mark.asyncio
async def test_barge_in_revokes_hangup_before_new_transcription_arrives():
    bridge, provider, _, ended, session = _fixture()
    bridge._latest_caller_text = "Goodbye."
    await _end_tool(bridge)
    await _events(bridge, provider, RealtimeEvent(kind="interrupted", raw={"during_response": True}))
    await _end_tool(bridge)  # Delayed tool from the old response must not rearm.
    bridge._goodbye_completed.set()
    await asyncio.sleep(0)
    ended.assert_not_awaited()
    assert bridge._termination_task is None
    assert not session._end_call_requested
    await bridge.stop()


@pytest.mark.asyncio
async def test_final_hangup_rechecks_current_caller_intent():
    bridge, _, _, ended, _ = _fixture()
    bridge._latest_caller_text = "Goodbye."
    await _end_tool(bridge)
    closing = bridge._termination_task
    bridge._latest_caller_text = "Wait, I have another question."
    bridge._goodbye_completed.set()
    await closing
    ended.assert_not_awaited()
    await bridge.stop()


@pytest.mark.asyncio
async def test_explicit_caller_end_with_plain_model_goodbye_closes_once():
    bridge, provider, _, ended, _ = _fixture()
    await _events(bridge, provider,
        RealtimeEvent(kind="caller_transcript", text="Goodbye. Please end this call.", is_final=True),
        RealtimeEvent(kind="response_candidate", text="Goodbye.", audio=b"\xff" * 320))
    assert bridge._termination_task is not None
    await asyncio.wait_for(bridge._termination_task, 1)
    ended.assert_awaited_once()
    provider.close.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["No thanks to email.", "I'm not interested in email."])
async def test_topic_refusal_without_end_tool_does_not_automatically_hang_up(text):
    bridge, provider, _, ended, session = _fixture()
    await _events(bridge, provider,
        RealtimeEvent(kind="caller_transcript", text=text, is_final=True),
        RealtimeEvent(kind="response_candidate", text="Understood.", audio=b"\xff" * 320))
    assert bridge._termination_task is None
    assert not session._end_call_requested
    ended.assert_not_awaited()
    await bridge.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["No thanks to email.", "I'm not interested in SMS.",
    "That's all for my email.", "I'm done giving my number.", "Thanks, I have no card setup."])
async def test_model_end_tool_cannot_turn_topic_completion_into_call_completion(text):
    bridge, provider, _, ended, session = _fixture()
    await _events(bridge, provider, RealtimeEvent(kind="caller_transcript", text=text, is_final=True))
    await _end_tool(bridge)
    bridge._goodbye_completed.set()
    await asyncio.sleep(0)
    assert bridge._termination_task is None
    assert not session._end_call_requested
    ended.assert_not_awaited()
    await bridge.stop()


@pytest.mark.asyncio
async def test_interrupted_opening_does_not_mark_later_answer_as_delivered_identity():
    bridge, provider, _, ended, _ = _fixture()
    await _events(bridge, provider,
        RealtimeEvent(kind="interrupted", raw={"during_response": True}),
        RealtimeEvent(kind="caller_transcript", text="Wait, what is this about?", is_final=True),
        RealtimeEvent(kind="response_candidate", text="This is about your inquiry.", audio=b"\xff" * 320))
    assert bridge._opening_interrupted
    assert not bridge._identity_opening_pending
    assert bridge._live_state.identity_introduced is not True
    assert "do not restart the greeting" in provider.update_live_state.call_args.args[0]
    provider.truncate_response.assert_awaited()
    ended.assert_not_awaited()
    await bridge.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("text,dnc", [
    ("Please stop calling me.", True), ("Goodbye.", False),
    ("Stop calling me, but I need help first.", True),
    ("What does 'do not call me again' mean?", False),
    ("Don't stop calling me.", False),
])
async def test_only_actual_caller_dnc_sets_shared_lifecycle_flag(text, dnc):
    bridge, provider, _, _, session = _fixture()
    await _events(bridge, provider, RealtimeEvent(kind="caller_transcript", text=text, is_final=True))
    await _end_tool(bridge)
    assert bool(getattr(session, "_caller_opted_out", False)) is dnc
    await bridge.stop()


@pytest.mark.asyncio
async def test_dnc_survives_canceled_close_and_later_goodbye_can_rearm():
    bridge, provider, _, ended, session = _fixture()
    await _events(bridge, provider,
        RealtimeEvent(kind="caller_transcript", text="Please stop calling me.", is_final=True),
        RealtimeEvent(kind="interrupted", raw={"during_response": True}),
        RealtimeEvent(kind="caller_transcript", text="Wait, I need help first.", is_final=True))
    assert session._caller_opted_out is True
    ended.assert_not_awaited()
    await _events(bridge, provider,
        RealtimeEvent(kind="caller_transcript", text="Now please end the call. Goodbye.", is_final=True),
        RealtimeEvent(kind="response_candidate", text="Goodbye.", audio=b"\xff" * 320))
    await asyncio.wait_for(bridge._termination_task, 1)
    ended.assert_awaited_once()
    assert session._caller_opted_out is True


@pytest.mark.asyncio
async def test_caller_activity_during_tool_publication_does_not_rearm_close():
    bridge, provider, _, ended, session = _fixture()
    bridge._latest_caller_text = "Goodbye."

    async def caller_resumes(_block):
        bridge._revoke_pending_end_call()
        bridge._latest_caller_text = "Wait, I need help."

    provider.update_live_state.side_effect = caller_resumes
    await _end_tool(bridge)
    bridge._goodbye_completed.set()
    await asyncio.sleep(0)
    assert bridge._termination_task is None
    assert not session._end_call_requested
    ended.assert_not_awaited()
    await bridge.stop()
