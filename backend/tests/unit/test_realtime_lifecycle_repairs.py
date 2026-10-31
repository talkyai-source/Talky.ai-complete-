"""Fault-boundary regressions using actual adapters and synthetic transports."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.realtime.bridge import RealtimeBridge
from app.realtime.openai import OpenAIRealtimeSession, RealtimeEvent
from app.realtime.config import normalize_realtime_settings
from app.infrastructure.telephony.browser_media_gateway import BrowserMediaGateway, BrowserSession
from app.infrastructure.telephony.twilio_media_gateway import TwilioMediaGateway


@pytest.mark.asyncio
async def test_interruption_truncates_unheard_provider_item_not_generated_duration():
    provider = OpenAIRealtimeSession(api_key="test")
    provider._ws = SimpleNamespace(send=AsyncMock())
    bridge = RealtimeBridge(call_id="test", realtime_session=provider,
                            media_gateway=SimpleNamespace(clear_output_buffer=AsyncMock()))
    raw = {"audio_parts": [{"item_id": "item", "content_index": 0, "audio_bytes": 80000}]}
    bridge._utterance = {"id": "rt-1", "status": "playing", "raw": raw, "receipt": None}
    await bridge._cancel_playback()
    payload = json.loads(provider._ws.send.await_args.args[0])
    assert payload == {"type": "conversation.item.truncate", "item_id": "item", "content_index": 0, "audio_end_ms": 0}
    await bridge._cancel_playback()
    assert provider._ws.send.await_count == 1


@pytest.mark.asyncio
async def test_parallel_tool_results_trigger_one_continuation_after_all_results():
    provider = OpenAIRealtimeSession(api_key="test")
    provider._ws = SimpleNamespace(send=AsyncMock())
    await provider._handle_server_event({"type":"response.created","response":{"id":"r"}})
    for call in ("tool-a", "tool-b"):
        await provider._handle_server_event({"type":"response.function_call_arguments.done","response_id":"r",
            "call_id":call,"name":"knowledge_lookup","arguments":"{}"})
    await provider._handle_server_event({"type":"response.done","response":{"id":"r","status":"completed","output":[]}})
    await provider.send_function_result("tool-a",{"facts":"first"})
    assert [json.loads(c.args[0])["type"] for c in provider._ws.send.await_args_list] == ["conversation.item.create"]
    await provider.send_function_result("tool-b",{"facts":"second"})
    types = [json.loads(c.args[0])["type"] for c in provider._ws.send.await_args_list]
    assert types == ["conversation.item.create", "conversation.item.create", "response.create"]
    assert provider._response_active


@pytest.mark.asyncio
async def test_late_tool_result_does_not_resume_the_interrupted_response():
    provider = OpenAIRealtimeSession(api_key="test")
    provider._ws = SimpleNamespace(send=AsyncMock())
    provider._tool_batches = {"old":{"tool"}}
    provider._tool_call_batches = {"tool":("old",provider._response_epoch)}
    provider._on_interruption("speech_started")
    await provider.send_function_result("tool",{"facts":"stale query"})
    assert [json.loads(c.args[0])["type"] for c in provider._ws.send.await_args_list] == ["conversation.item.create"]


@pytest.mark.asyncio
async def test_actual_browser_receipt_rejects_stale_and_uncorrelated_ack():
    gateway = BrowserMediaGateway()
    session = BrowserSession(call_id="call", websocket=SimpleNamespace(send_json=AsyncMock()))
    gateway._sessions["call"] = session
    await gateway.begin_playback("call", "new")
    session.playback_bytes_sent = 3200
    finish = asyncio.create_task(gateway.finish_playback("call", "new"))
    await asyncio.sleep(0)
    gateway.mark_playback_complete("call", "old")
    gateway.mark_playback_complete("call")
    assert not session.playback_complete_event.is_set()
    gateway.mark_playback_complete("call", "new")
    receipt = await finish
    assert receipt == {"utterance_id": "new", "status": "completed", "evidence": "transport_played", "played_ms": 100}


@pytest.mark.asyncio
async def test_twilio_mark_protocol_and_clear_cannot_confirm_readback():
    gateway = TwilioMediaGateway()
    ws = SimpleNamespace(send_text=AsyncMock(), send_json=AsyncMock())
    session = BrowserSession(call_id="call", websocket=ws)
    gateway._sessions["call"] = session
    gateway.set_stream_sid("call", "stream")
    await gateway.begin_playback("call", "u1")
    await gateway.send_control_event("call", {"type": "llm_response", "text": "hello"})
    await gateway.send_control_event("call", {"type": "tts_audio_complete", "utterance_id": "u1"})
    assert json.loads(ws.send_text.await_args.args[0]) == {"event": "mark", "streamSid": "stream", "mark": {"name": "u1"}}
    await gateway.clear_output_buffer("call")
    gateway.mark_playback_complete("call", "u1")
    assert not session.playback_complete_event.is_set()
    assert gateway.playback_receipt("call", "u1")["status"] == "interrupted"
    ws.send_json.assert_not_awaited()


@pytest.mark.asyncio
async def test_budget_failure_retries_once_with_full_policy_and_no_audio():
    async def events():
        for _ in range(2):
            yield RealtimeEvent(kind="generation_incomplete", raw={"response": {"id": "r"}})
    rt = SimpleNamespace(events=events, repair_unspoken_response=AsyncMock())
    gw = SimpleNamespace(clear_output_buffer=AsyncMock(), send_audio=AsyncMock())
    bridge = RealtimeBridge(call_id="call", realtime_session=rt, media_gateway=gw)
    await bridge._pump_model_events()
    rt.repair_unspoken_response.assert_awaited_once()
    gw.send_audio.assert_not_awaited()
    assert "one shorter retry" in bridge._failure_reason
    provider = OpenAIRealtimeSession(api_key="test", instructions="CANONICAL POLICY")
    provider._ws = SimpleNamespace(send=AsyncMock())
    await provider.repair_unspoken_response({})
    assert "CANONICAL POLICY" in json.loads(provider._ws.send.await_args.args[0])["response"]["instructions"]


@pytest.mark.asyncio
async def test_end_call_waits_for_new_goodbye_then_uses_termination_capability():
    gateway = SimpleNamespace(send_audio=AsyncMock(), begin_playback=AsyncMock(),
        finish_playback=AsyncMock(return_value={"utterance_id": "rt-1", "status": "completed", "evidence": "transport_played", "played_ms": 40}))
    ended = AsyncMock()
    rt = SimpleNamespace(close=AsyncMock(), update_live_state=AsyncMock())
    bridge = RealtimeBridge(call_id="call", realtime_session=rt, media_gateway=gateway, on_end_call=ended)
    bridge._latest_caller_text = "Goodbye."
    assert bridge._arm_caller_end_call()
    closing = bridge._termination_task
    await asyncio.sleep(0)
    ended.assert_not_awaited()
    await bridge._play_validated_response(RealtimeEvent(kind="response_candidate", audio=b"\xff" * 320, text="Goodbye."))
    await closing
    ended.assert_awaited_once()
    rt.close.assert_awaited_once()


def test_legacy_settings_normalize_to_effective_wire_values():
    settings = normalize_realtime_settings({"noise_reduction": "off", "temperature": 0.8,
        "transcription_model": "gpt-realtime-whisper", "turn_detection": {"type": "server_vad", "threshold": 0.6}})
    payload = OpenAIRealtimeSession(api_key="test", settings=settings)._build_session_update()["session"]
    assert settings["noise_reduction"] == "none"
    assert "temperature" not in payload
    assert payload["audio"]["input"]["noise_reduction"] is None
    assert payload["max_output_tokens"] == settings["max_output_tokens"] == 1024
    assert payload["audio"]["input"]["turn_detection"] == settings["turn_detection"]




@pytest.mark.asyncio
@pytest.mark.parametrize("text,capabilities,knowledge", [
    ("I can transfer you to our sales team.", {"transfer_call": "configured"}, []),
    ("I can share the download link https://example.test/brochure.", {},
     ["Download the brochure at https://example.test/brochure."]),
])
async def test_configured_action_or_verified_resource_offer_passes_realtime_gate(text, capabilities, knowledge):
    async def events():
        yield RealtimeEvent(kind="response_candidate", text=text, audio=b"\xff" * 320)
    provider = SimpleNamespace(events=events, repair_unspoken_response=AsyncMock())
    session = SimpleNamespace(_voice_action_capabilities=capabilities)
    bridge = RealtimeBridge(call_id="fixture", realtime_session=provider, media_gateway=SimpleNamespace(), action_session=session)
    bridge._verified_knowledge = knowledge
    bridge._play_validated_response = AsyncMock()
    await bridge._pump_model_events()
    assert bridge._playback_task is not None
    await bridge._playback_task
    bridge._play_validated_response.assert_awaited_once()
    provider.repair_unspoken_response.assert_not_awaited()
