import asyncio
import base64
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.models.ai_config import AIProviderConfig
from app.domain.services.voice_orchestrator import VoiceOrchestrator, VoiceSessionConfig
from app.realtime.openai import OpenAIRealtimeSession, RealtimeEvent
from app.realtime.bridge import RealtimeBridge
from app.realtime.config import RealtimeSettings


async def generate(session, *, text="Hello there", response="r1", complete=True):
    await session._handle_server_event({"type": "response.created", "response": {"id": response}})
    common = {"response_id": response, "item_id": "i1", "content_index": 0}
    await session._handle_server_event({"type": "response.output_audio.delta", **common, "delta": base64.b64encode(b"\xff" * 320).decode()})
    if text is not None:
        await session._handle_server_event({"type": "response.output_audio_transcript.done", **common, "transcript": text})
    if complete:
        await session._handle_server_event({"type": "response.done", "response": {"id": response, "status": "completed"}})


@pytest.mark.asyncio
async def test_audio_waits_for_completed_matching_transcript():
    session = OpenAIRealtimeSession(api_key="test")
    await generate(session, complete=False)
    assert session._event_queue.empty(), "Neither draft text nor audio may reach playback"
    await session._handle_server_event({"type": "response.done", "response": {"id": "r1", "status": "completed"}})
    event = session._event_queue.get_nowait()
    assert event.kind == "response_candidate"
    assert event.text == "Hello there"
    assert event.audio == b"\xff" * 320


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["missing_transcript", "interrupt", "overflow", "stale_response", "incomplete"])
async def test_unverified_audio_never_released(failure):
    session = OpenAIRealtimeSession(api_key="test")
    await generate(session, text=None if failure == "missing_transcript" else "Hello", complete=False)
    if failure == "interrupt":
        await session._handle_server_event({"type": "input_audio_buffer.speech_started"})
    if failure == "overflow":
        session._playout.MAX_AUDIO_BYTES = 320
        session._playout.add_audio({"response_id": "r1", "item_id": "i1"}, b"a")
    await session._handle_server_event({"type": "response.done", "response": {
        "id": "wrong" if failure == "stale_response" else "r1",
        "status": "incomplete" if failure == "incomplete" else "completed",
    }})
    events = []
    while not session._event_queue.empty():
        events.append(session._event_queue.get_nowait())
    assert not any(e and e.kind in {"audio", "response_candidate"} for e in events)


@pytest.mark.asyncio
async def test_false_completion_is_withheld_and_repair_is_bounded():
    async def events():
        for _ in range(3):
            yield RealtimeEvent(kind="response_candidate", text="I have sent the email.", audio=b"x", raw={"response": {"id": "r"}})
    provider = SimpleNamespace(events=events, repair_unspoken_response=AsyncMock())
    gateway = SimpleNamespace(send_audio=AsyncMock())
    bridge = RealtimeBridge(call_id="test", realtime_session=provider, media_gateway=gateway)
    await bridge._pump_model_events()
    gateway.send_audio.assert_not_awaited()
    provider.repair_unspoken_response.assert_awaited_once()
    assert bridge._failure_reason


@pytest.mark.asyncio
async def test_barge_in_cancels_playback_while_pump_keeps_reading():
    started = asyncio.Event()
    async def send(*_args):
        started.set()
        await asyncio.Event().wait()
    async def events():
        yield RealtimeEvent(kind="response_candidate", text="Hello there", audio=b"\xff" * 640)
        await started.wait()
        yield RealtimeEvent(kind="interrupted")
    gateway = SimpleNamespace(send_audio=send, clear_output_buffer=AsyncMock())
    bridge = RealtimeBridge(call_id="test", realtime_session=SimpleNamespace(events=events), media_gateway=gateway)
    await asyncio.wait_for(bridge._pump_model_events(), 1)
    assert bridge._playback_task.cancelled()
    gateway.clear_output_buffer.assert_awaited_once()


@pytest.mark.asyncio
async def test_failed_connection_never_constructs_traditional_providers(monkeypatch):
    orchestrator = VoiceOrchestrator(db_client=None)
    monkeypatch.setattr(orchestrator, "_create_realtime_voice_session", AsyncMock(return_value=None))
    traditional = AsyncMock()
    monkeypatch.setattr(orchestrator, "_create_stt_provider", traditional)
    config = VoiceSessionConfig(pipeline_mode="realtime")
    with pytest.raises(RuntimeError, match="no alternative engine"):
        await orchestrator.create_voice_session(config)
    traditional.assert_not_awaited()
    assert config.pipeline_mode == "realtime"


def test_realtime_prompt_never_uses_traditional_composer_or_guidance(monkeypatch):
    from app.domain.services import telephony_session_config as traditional
    def forbidden(*_args, **_kwargs):
        pytest.fail("Traditional prompt composer reached")
    monkeypatch.setattr(traditional, "compose_prompt", forbidden)
    source = AIProviderConfig(pipeline_mode="realtime", realtime_settings={"prompt": {
        "persona": "support", "goal": "Resolve requests", "instructions": "REALTIME ONLY", "opening_greeting": "Welcome"
    }})
    config = traditional.build_telephony_session_config(ai_config_override=source, campaign={
        "id": "campaign", "tenant_id": "tenant", "voice_id": "invalid-traditional-voice",
        "script_config": {"company_name": "Acme", "agent_names": ["Alex"], "additional_instructions": "TRADITIONAL ONLY", "campaign_slots": {}},
    })
    from app.realtime.prompts import build_realtime_instructions
    text = build_realtime_instructions(VoiceOrchestrator._build_realtime_persona(config))
    # Account-wide Realtime notes are ADDED to the campaign's guidance, never
    # swapped in for it (browser test 94f47f14, 2026-09-30: a one-line
    # account note replaced Dojo-PC's script). The composer is still unused.
    assert "REALTIME ONLY" in text and "TRADITIONAL ONLY" in text
    assert "verified troubleshooting" in text
    assert "Welcome" in text


@pytest.mark.asyncio
async def test_campaign_realtime_roundtrip_preserves_separate_settings(monkeypatch):
    from app.api.v1.schemas.campaigns import CampaignCreateRequest, CampaignUpdateRequest
    from app.api.v1.endpoints.campaign_voice_config import build_campaign_voice_config
    from app.api.v1.endpoints import campaigns
    body = dict(name="Sample", company_name="Acme", agent_names=["Alex"], persona_type="lead_gen")
    data = CampaignCreateRequest(**body, pipeline_mode="realtime", realtime_voice="ash", realtime_prompt={"instructions": "RT custom"})
    source = AIProviderConfig()
    script, voice = await build_campaign_voice_config(data, source)
    assert script["realtime_prompt"]["instructions"] == "RT custom"
    assert script["realtime_voice"] == "ash"
    # Old clients omit new fields: an unrelated edit must retain them.
    script2, _ = await build_campaign_voice_config(CampaignUpdateRequest(**body), source, existing=script)
    assert script2["pipeline_mode"] == "realtime"
    assert script2["realtime_prompt"] == script["realtime_prompt"]
    monkeypatch.setattr(campaigns, "_valid_voice_ids_for_provider", AsyncMock(return_value={voice}))
    monkeypatch.setattr(campaigns, "_build_validated_script_config", lambda **kw: {"additional_instructions": kw["additional_instructions"]})
    standard, _ = await build_campaign_voice_config(CampaignUpdateRequest(**body, pipeline_mode="cascaded", voice_id=voice, system_prompt="Traditional"), source, existing=script2)
    assert standard["realtime_prompt"] == script["realtime_prompt"]
    assert standard["additional_instructions"] == "Traditional"


@pytest.mark.asyncio
async def test_realtime_preview_uses_own_persona_and_prompt():
    from app.api.v1.endpoints.campaigns import preview_prompt
    from app.api.v1.schemas.campaigns import CampaignPromptPreviewRequest
    result = await preview_prompt(CampaignPromptPreviewRequest(
        pipeline_mode="realtime", persona_type="lead_gen", company_name="Acme", agent_name="Alex", campaign_slots={},
        additional_instructions="TRADITIONAL SECRET", realtime_prompt={"persona": "receptionist", "instructions": "RT special"},
    ), current_user=SimpleNamespace())
    assert "RT special" in result.system_prompt
    assert "TRADITIONAL SECRET" not in result.system_prompt
    assert result.layers[0].key == "realtime"


def test_controls_validate_and_preserve_persona():
    data = RealtimeSettings(prompt={"persona": "sales", "instructions": "Ask one question"}, speed=1.2, max_output_tokens=512)
    assert RealtimeSettings.model_validate(data.model_dump()).prompt.persona == "sales"
    with pytest.raises(ValueError):
        RealtimeSettings(speed=10)


def test_old_implementation_locations_removed():
    app = Path(__file__).parents[2] / "app"
    for path in ["infrastructure/realtime/openai_realtime.py", "infrastructure/realtime/xai_realtime.py", "domain/services/voice_pipeline/realtime_bridge.py", "services/scripts/realtime_instructions.py"]:
        assert not (app / path).exists()
    for path in ["prompts.py", "personas.py", "runtime.py", "openai.py", "xai.py", "config.py"]:
        assert (app / "realtime" / path).exists()


@pytest.mark.asyncio
async def test_tool_continuation_waits_for_playback_without_blocking_events():
    release = asyncio.Event()
    provider = SimpleNamespace(send_function_result=AsyncMock())
    bridge = RealtimeBridge(call_id="test", realtime_session=provider, media_gateway=SimpleNamespace())
    bridge._playback_task = asyncio.create_task(release.wait())
    result_task = asyncio.create_task(bridge._send_tool_result_after_playback("tool", "verified answer"))
    await asyncio.sleep(0)
    provider.send_function_result.assert_not_awaited()
    release.set()
    await result_task
    provider.send_function_result.assert_awaited_once_with("tool", "verified answer")


@pytest.mark.asyncio
async def test_setup_exception_releases_connected_provider_and_gateway(monkeypatch):
    from app.realtime import runtime, openai
    from app.domain.services import credential_resolver
    resolver = SimpleNamespace(resolve=AsyncMock(return_value="test"))
    monkeypatch.setattr(credential_resolver, "get_credential_resolver", lambda: resolver)
    provider = SimpleNamespace(connect=AsyncMock(return_value=True), close=AsyncMock())
    monkeypatch.setattr(openai, "OpenAIRealtimeSession", lambda **kw: provider)
    def fail(**kw):
        raise RuntimeError("assembly failure")
    monkeypatch.setattr(runtime, "CallSession", fail)
    gateway = SimpleNamespace(cleanup=AsyncMock())
    owner = SimpleNamespace(_create_media_gateway=AsyncMock(return_value=gateway))
    assert await runtime.create_realtime_voice_session(owner, VoiceSessionConfig(pipeline_mode="realtime"), "call", "db-call") is None
    provider.close.assert_awaited_once()
    gateway.cleanup.assert_awaited_once()


@pytest.mark.asyncio
async def test_availability_resolves_tenant_credential(monkeypatch):
    from app.realtime.credentials import resolve_openai_key
    from app.domain.services import credential_resolver
    resolve = AsyncMock(return_value="tenant-only-test-key")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(credential_resolver, "get_credential_resolver", lambda: SimpleNamespace(resolve=resolve))
    assert await resolve_openai_key("tenant-a") == "tenant-only-test-key"
    resolve.assert_awaited_once_with("openai", tenant_id="tenant-a")


def test_existing_xai_opt_in_is_retained():
    from app.realtime.config import validate_realtime
    validate_realtime("grok-voice-think-fast-1.0", "ash", {"provider": "xai", "agent_id": "fixture-agent"})


@pytest.mark.asyncio
async def test_browser_playback_controls_flush_before_requesting_acknowledgement():
    order = []
    async def control(_call, payload):
        order.append(payload["type"])
    async def audio(*args):
        order.append("audio")
    async def flush(*args):
        order.append("flush")
    async def ack(*args):
        assert order[-1] == "tts_audio_complete"
        order.append("ack")
        return True
    gateway = SimpleNamespace(send_control_event=control, send_audio=audio,
                              flush_audio_buffer=flush, wait_for_playback_complete=ack)
    provider = SimpleNamespace(update_live_state=AsyncMock(), close=AsyncMock())
    bridge = RealtimeBridge(call_id="browser", realtime_session=provider, media_gateway=gateway)
    await bridge._play_validated_response(RealtimeEvent(kind="response_candidate", text="Hello there", audio=b"\xff" * 320))
    assert order == ["llm_response", "audio", "flush", "tts_audio_complete", "ack", "turn_complete"]
    provider.close.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("direction", ["inbound", "outbound"])
async def test_campaign_preview_matches_independent_runtime_prompt(direction):
    """The prompt an operator reviews must be the one assembled for that campaign."""
    import hashlib
    from app.api.v1.endpoints.campaigns import preview_prompt
    from app.api.v1.schemas.campaigns import CampaignPromptPreviewRequest
    from app.domain.services.telephony_session_config import build_telephony_session_config
    from app.domain.services.voice_orchestrator import Direction
    from app.realtime.prompts import PROMPT_VERSION

    prompt = {"persona": "support", "goal": "Help with service requests",
              "instructions": "Ask which service the caller needs.", "opening_greeting": "Welcome to Acme."}
    config = build_telephony_session_config(
        ai_config_override=AIProviderConfig(pipeline_mode="realtime"),
        direction=Direction(direction),
        campaign={"id": "fixture-campaign", "tenant_id": "fixture-tenant", "script_config": {
            "company_name": "Acme", "agent_names": ["Sam"], "realtime_prompt": prompt,
            "additional_instructions": "TRADITIONAL-ONLY-GUIDANCE",
        }},
    )
    preview = await preview_prompt(CampaignPromptPreviewRequest(
        pipeline_mode="realtime", persona_type="lead_gen", company_name="Acme", agent_name="Sam",
        campaign_slots={}, realtime_prompt=prompt, direction=direction,
        additional_instructions="TRADITIONAL-ONLY-GUIDANCE",
    ), current_user=SimpleNamespace())
    assert preview.system_prompt == config.system_prompt
    assert "TRADITIONAL-ONLY-GUIDANCE" not in config.system_prompt
    assert config.prompt_version == PROMPT_VERSION
    assert config.prompt_hash == hashlib.sha256(config.system_prompt.encode()).hexdigest()[:16]
