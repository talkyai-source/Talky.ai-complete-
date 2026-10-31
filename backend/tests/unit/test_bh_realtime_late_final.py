"""Late ASR finals: contact evidence (realtime-5), goodbye anchor (realtime-2)."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.services.voice_pipeline.contact_recording import _quote_turn
from app.realtime.bridge import RealtimeBridge
from app.realtime.openai import RealtimeEvent


def _fixture(*, gateway_receipts=None):
    provider = SimpleNamespace(close=AsyncMock(), update_live_state=AsyncMock(),
        send_function_result=AsyncMock(), interrupt_with_text=AsyncMock(),
        update_contact_directive=AsyncMock(), truncate_response=AsyncMock())
    gateway = SimpleNamespace(send_audio=AsyncMock(), begin_playback=AsyncMock(),
        clear_output_buffer=AsyncMock(), finish_playback=AsyncMock(return_value={
            "utterance_id": "rt-1", "status": "completed", "evidence": "transport_played", "played_ms": 40}))
    ended, session = AsyncMock(), SimpleNamespace()
    bridge = RealtimeBridge(call_id="synthetic-late", realtime_session=provider,
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


def _start(item, ms):
    return RealtimeEvent(kind="interrupted", raw={
        "reason": "speech_started", "item_id": item, "audio_start_ms": ms, "during_response": False})


def _final(item, text):
    return RealtimeEvent(kind="caller_transcript", is_final=True, text=text, raw={"item_id": item})


@pytest.mark.asyncio
async def test_late_final_of_an_earlier_turn_is_quotable_by_record_contact():
    bridge, provider, _, _, _ = _fixture()
    email = "My email is sam at example dot com"
    await _events(bridge, provider, _start("u1", 0), _start("u2", 1000),
        _final("u2", "that's all lowercase"), _final("u1", email))
    contact = bridge._contact_session
    # The late turn is evidence the model may quote ...
    quoted = _quote_turn(contact, contact._contact_turn, email, current_only=False)
    assert quoted is not None and quoted.source.provider_item_id == "u1"
    assert quoted.source.caller_turn_order == 1
    # ... in caller order, without displacing the live turn or the live text.
    assert [t.source.caller_turn_order for t in contact._contact_turns] == [1, 2]
    assert contact._contact_turn.source.provider_item_id == "u2"
    assert bridge._latest_caller_text == "that's all lowercase"
    users = [m.content for m in bridge._contact_history if getattr(m.role, "value", m.role) == "user"]
    assert users == [email, "that's all lowercase"]
    await bridge.stop()


@pytest.mark.asyncio
async def test_late_final_arriving_before_the_later_final_keeps_caller_order():
    bridge, provider, _, _, _ = _fixture()
    await _events(bridge, provider, _start("u1", 0), _start("u2", 1000),
        _final("u1", "My email is sam at example dot com"), _final("u2", "that's all lowercase"))
    contact = bridge._contact_session
    assert [t.source.caller_turn_order for t in contact._contact_turns] == [1, 2]
    assert contact._contact_turn.source.provider_item_id == "u2"
    users = [m.content for m in bridge._contact_history if getattr(m.role, "value", m.role) == "user"]
    assert users == ["My email is sam at example dot com", "that's all lowercase"]
    await bridge.stop()


def _response(text):
    return RealtimeEvent(kind="response_candidate", text=text, audio=b"\xff" * 320)


@pytest.mark.asyncio
async def test_goodbye_played_before_the_late_caller_final_closes_without_waiting_15s():
    bridge, provider, _, ended, _ = _fixture()
    await _events(bridge, provider, _start("u1", 0), _response("Goodbye."),
        _final("u1", "Goodbye. Please end this call."))
    assert bridge._termination_task is not None
    await asyncio.wait_for(bridge._termination_task, 1)
    ended.assert_awaited_once()
    provider.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_question_finished_before_the_caller_spoke_is_not_taken_for_the_goodbye():
    bridge, provider, _, ended, _ = _fixture()
    await _events(bridge, provider, _response("Anything else?"), _start("u1", 0),
        _final("u1", "No, goodbye. Please end this call."))
    assert bridge._termination_task is not None
    await asyncio.sleep(0.3)
    assert not bridge._goodbye_completed.is_set()
    assert not bridge._termination_task.done()
    ended.assert_not_awaited()
    await bridge.stop()


def _parts(call):
    return [part["item_id"] for part in call.args[0].get("audio_parts", [])], call.kwargs.get("played_ms")


@pytest.mark.asyncio
async def test_second_barge_in_truncates_the_still_generating_response_too():
    bridge, provider, _, _, _ = _fixture()
    bridge._utterance = {"id": "rt-1", "generation": 1, "status": "interrupted", "receipt": None,
        "text": "", "raw": {"audio_parts": [{"item_id": "old", "audio_bytes": 800}]}}
    await _events(bridge, provider, RealtimeEvent(kind="interrupted", raw={
        "reason": "speech_started", "during_response": True,
        "audio_parts": [{"item_id": "r2item", "audio_bytes": 1600}]}))
    calls = [_parts(call) for call in provider.truncate_response.await_args_list]
    assert (["old"], 0) in calls
    assert (["r2item"], 0) in calls
    await bridge.stop()


@pytest.mark.asyncio
async def test_barge_in_after_response_done_does_not_erase_heard_context():
    bridge, provider, _, _, _ = _fixture()
    previous = [{"item_id": "playing", "audio_bytes": 1600}]
    bridge._utterance = {"id": "rt-1", "generation": 1, "status": "playing", "receipt": None,
        "text": "", "raw": {"audio_parts": previous}}
    # during_response is False: audio_parts still lists the previous response.
    await _events(bridge, provider, RealtimeEvent(kind="interrupted", raw={
        "reason": "speech_started", "during_response": False, "audio_parts": previous}))
    assert [_parts(call)[0] for call in provider.truncate_response.await_args_list] == [["playing"]]
    await bridge.stop()
