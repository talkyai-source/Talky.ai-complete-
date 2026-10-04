"""AG01 native profile contracts. Synthetic identities; no provider I/O."""
import asyncio
import hashlib
import json
from collections import deque
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.models.ai_config import AIProviderConfig
from app.domain.services.telephony_session_config import build_telephony_session_config
from app.domain.services.voice_orchestrator import VoiceOrchestrator, VoiceSessionConfig
from app.realtime.openai import OpenAIRealtimeSession
from app.realtime.xai import XAIRealtimeSession


def campaign(script):
    return {"id": "synthetic-campaign", "tenant_id": "synthetic-tenant", "script_config": script}


async def assemble(monkeypatch, config, *, mock_connect=True):
    from app.domain.services import credential_resolver
    from app.domain.services.voice_pipeline import action_execution
    from app.core import container
    monkeypatch.setattr(credential_resolver, "get_credential_resolver", lambda: SimpleNamespace(resolve=AsyncMock(return_value="synthetic-key")))
    monkeypatch.setattr(container, "get_container", lambda: SimpleNamespace(is_initialized=False))
    async def context(session):
        session._voice_action_context = None
        session._voice_action_capabilities = {"send_email": "Synthetic configured email capability"}
        session._voice_action_context_loaded = True
    monkeypatch.setattr(action_execution, "prepare_voice_action_context", context)
    if mock_connect:
        monkeypatch.setattr(OpenAIRealtimeSession, "connect", AsyncMock(return_value=True))
    owner = VoiceOrchestrator(db_client=None)
    gateway = SimpleNamespace(_sample_rate=8000, cleanup=AsyncMock())
    monkeypatch.setattr(owner, "_create_media_gateway", AsyncMock(return_value=gateway))
    result = await owner._create_realtime_voice_session(config, "synthetic-call", "synthetic-row")
    assert result is not None
    return result


@pytest.mark.asyncio
async def test_campaign_nested_prompt_survives_create_reload_and_unrelated_edit(monkeypatch):
    from app.api.v1.schemas.campaigns import CampaignCreateRequest, CampaignUpdateRequest
    from app.api.v1.endpoints.campaign_voice_config import build_campaign_voice_config
    source = AIProviderConfig(pipeline_mode="realtime", realtime_settings={"prompt": {"instructions": "ACCOUNT NOTE"}})
    common = {"name": "Synthetic", "company_name": "Acme", "agent_names": ["Alex"], "persona_type": "lead_gen"}
    nested = {"persona": "support", "goal": "CAMPAIGN GOAL", "instructions": "CAMPAIGN EXACT NOTE", "opening_greeting": "Campaign welcome"}
    script, _ = await build_campaign_voice_config(CampaignCreateRequest(**common, pipeline_mode="realtime", realtime_settings={"prompt": nested}), source)
    script = json.loads(json.dumps(script))
    script, _ = await build_campaign_voice_config(CampaignUpdateRequest(**common), source, existing=script)
    config = build_telephony_session_config(campaign=campaign(script), ai_config_override=source)
    result = await assemble(monkeypatch, config)
    wire = result.realtime_session._build_session_update()["session"]
    assert "CAMPAIGN EXACT NOTE" in wire["instructions"]
    assert "CAMPAIGN GOAL" in wire["instructions"]
    assert "Campaign welcome" in wire["instructions"]
    assert "ACCOUNT NOTE" not in wire["instructions"]


@pytest.mark.asyncio
async def test_legacy_campaign_nested_prompt_is_not_lost_at_runtime(monkeypatch):
    source = AIProviderConfig(pipeline_mode="realtime", realtime_settings={"prompt": {"instructions": "ACCOUNT NOTE"}})
    script = {"company_name": "Acme", "agent_names": ["Alex"], "realtime_settings": {"prompt": {"instructions": "LEGACY CAMPAIGN NOTE"}}}
    config = build_telephony_session_config(campaign=campaign(script), ai_config_override=source)
    result = await assemble(monkeypatch, config)
    assert "LEGACY CAMPAIGN NOTE" in result.realtime_session._instructions


@pytest.mark.asyncio
async def test_explicit_xai_voice_reaches_real_runtime_serializer(monkeypatch):
    config = VoiceSessionConfig(pipeline_mode="realtime", realtime_voice="eve", realtime_settings={"provider": "xai"})
    result = await assemble(monkeypatch, config)
    assert isinstance(result.realtime_session, XAIRealtimeSession)
    assert result.realtime_session._build_session_update()["session"]["voice"] == "eve"


@pytest.mark.asyncio
async def test_initial_effective_prompt_identity_includes_capability_instructions(monkeypatch):
    config = VoiceSessionConfig(pipeline_mode="realtime")
    result = await assemble(monkeypatch, config)
    instructions = result.realtime_session._build_session_update()["session"]["instructions"]
    assert "send_email" in instructions
    assert config.system_prompt == instructions
    assert config.prompt_hash == hashlib.sha256(instructions.encode()).hexdigest()[:16]
    assert result.call_session.system_prompt == instructions


class Handshake:
    def __init__(self, error):
        self.inbox = deque([{"type": "session.created"}])
        self.error = error
        self.sent = []
        self.closed = False
    async def send(self, value):
        value = json.loads(value); self.sent.append(value)
        self.inbox.append({"type": "error", "error": self.error} if len(self.sent) == 1 else {"type": "session.updated", "session": value["session"]})
    async def recv(self):
        if self.inbox: return json.dumps(self.inbox.popleft())
        await asyncio.Event().wait()
    async def close(self): self.closed = True


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [
    {"code": "invalid_api_key", "param": None, "message": "Credentials rejected"},
    {"code": "model_not_found", "param": "model", "message": "Unavailable model"},
    {"code": "rate_limit_exceeded", "param": None, "message": "Wait"},
    {"code": "unknown_parameter", "param": "session.speed", "message": "Unrelated unsupported control"},
    {"message": "unknown parameter: reasoning"},
])
async def test_handshake_rejections_do_not_silently_change_selected_reasoning(monkeypatch, error):
    import app.realtime.openai as module
    transport = Handshake(error)
    monkeypatch.setattr(module.websockets, "connect", AsyncMock(return_value=transport))
    session = OpenAIRealtimeSession(api_key="synthetic-key", settings={"reasoning_effort": "medium"})
    try:
        assert await session.connect() is False
        assert len(transport.sent) == 1
        assert transport.sent[0]["session"]["reasoning"] == {"effort": "medium"}
    finally:
        await session.close()

@pytest.mark.parametrize("voice", ["marin", "ash", "", "MARIN", " marin ", " ", " eve "])
def test_xai_inherited_openai_voice_is_rejected_before_runtime(voice):
    from app.realtime.config import validate_realtime
    with pytest.raises(ValueError, match="Select an xAI Realtime voice"):
        validate_realtime("gpt-realtime-2", voice, {"provider": "xai"})


def test_xai_numeric_settings_are_numbers_on_wire():
    session = XAIRealtimeSession(api_key="synthetic-key", voice="eve", settings={
        "turn_detection": {"type": "server_vad", "threshold": "0.5", "silence_duration_ms": "400", "prefix_padding_ms": "300"}, "speed": "1.2"})
    wire = session._build_session_update()["session"]
    assert wire["turn_detection"] == {"type": "server_vad", "threshold": .5, "silence_duration_ms": 400, "prefix_padding_ms": 300}
    assert wire["audio"]["output"]["speed"] == 1.2


def test_openai_selected_noise_off_is_explicit_on_wire():
    session = OpenAIRealtimeSession(api_key="synthetic-key", settings={"noise_reduction": "none"})
    wire = session._build_session_update()["session"]["audio"]["input"]
    assert "noise_reduction" in wire
    assert wire["noise_reduction"] is None

@pytest.mark.asyncio
@pytest.mark.parametrize("code,param", [("unknown_parameter", "session.reasoning"), ("unsupported_parameter", "session.reasoning.effort")])
async def test_exact_reasoning_compatibility_retry_records_unapplied_choice(monkeypatch, code, param):
    import app.realtime.openai as module
    transport = Handshake({"code": code, "param": param, "message": "private submitted text"})
    monkeypatch.setattr(module.websockets, "connect", AsyncMock(return_value=transport))
    session = OpenAIRealtimeSession(api_key="synthetic-key", settings={"reasoning_effort": "medium"})
    try:
        assert await session.connect()
        profile = session.effective_profile()
        assert len(transport.sent) == 2
        assert profile["requested"]["reasoning_effort"] == "medium"
        assert profile["submitted"]["reasoning_effort"] is None
        assert profile["acknowledged"]["reasoning_effort"] is None
        assert profile["compatibility_changes"] == ["reasoning_field_unsupported_omitted"]
        assert profile["session_update_acknowledged"] is True
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_repeated_supported_rejection_stops_after_one_retry(monkeypatch):
    import app.realtime.openai as module
    class RejectEveryUpdate(Handshake):
        async def send(self, value):
            self.sent.append(json.loads(value))
            self.inbox.append({"type": "error", "error": self.error})
    transport = RejectEveryUpdate({"code": "unknown_parameter", "param": "session.reasoning"})
    monkeypatch.setattr(module.websockets, "connect", AsyncMock(return_value=transport))
    session = OpenAIRealtimeSession(api_key="synthetic-key")
    assert not await session.connect()
    assert len(transport.sent) == 2
    assert session.closed()


@pytest.mark.asyncio
async def test_handshake_timeout_does_not_drop_reasoning_or_retry(monkeypatch):
    import app.realtime.openai as module
    class Timeout(Handshake):
        async def send(self, value): self.sent.append(json.loads(value))
        async def recv(self):
            if self.inbox: return json.dumps(self.inbox.popleft())
            raise asyncio.TimeoutError()
    transport = Timeout({})
    monkeypatch.setattr(module.websockets, "connect", AsyncMock(return_value=transport))
    session = OpenAIRealtimeSession(api_key="synthetic-key")
    assert not await session.connect()
    assert len(transport.sent) == 1


@pytest.mark.asyncio
async def test_echo_omissions_and_runtime_instruction_digest_are_truthful(monkeypatch, caplog):
    from app.realtime.profile import instruction_digest
    import app.realtime.openai as module
    class EmptyEcho(Handshake):
        async def send(self, value):
            self.sent.append(json.loads(value))
            self.inbox.append({"type": "session.updated", "session": {}})
    transport = EmptyEcho({})
    monkeypatch.setattr(module.websockets, "connect", AsyncMock(return_value=transport))
    session = OpenAIRealtimeSession(api_key="synthetic-key-never-log", instructions="PRIVATE CAMPAIGN INSTRUCTIONS", settings={"reasoning_effort": "none"})
    try:
        with caplog.at_level("INFO"):
            assert await session.connect()
            profile = session.effective_profile()
            assert profile["requested_controls"]["reasoning_effort"] == "none"
            assert profile["submitted"]["reasoning_effort"] is None
            assert profile["acknowledged"]["voice"] is None
            assert profile["acknowledged"]["instructions_sha256"] is None
            assert profile["submitted"]["instructions_sha256"] == instruction_digest("PRIVATE CAMPAIGN INSTRUCTIONS")
            await session.update_live_state("PRIVATE LIVE CALLER STATE")
            assert session.effective_profile()["last_submitted_instructions_sha256"] == instruction_digest(transport.sent[-1]["session"]["instructions"])
        serialized = json.dumps(profile) + caplog.text
        assert "PRIVATE CAMPAIGN" not in serialized
        assert "PRIVATE LIVE" not in serialized
        assert "synthetic-key-never-log" not in serialized
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_campaign_prompt_precedence_on_nested_update_and_explicit_alias():
    from app.api.v1.schemas.campaigns import CampaignUpdateRequest
    from app.api.v1.endpoints.campaign_voice_config import build_campaign_voice_config
    source = AIProviderConfig(pipeline_mode="realtime", realtime_settings={"prompt": {"instructions": "ACCOUNT"}})
    common = {"name": "Synthetic", "company_name": "Acme", "agent_names": ["Alex"], "persona_type": "lead_gen"}
    previous = {"pipeline_mode": "realtime", "realtime_prompt": {"instructions": "OLD"}}
    nested = {"prompt": {"instructions": "NESTED"}}
    update = CampaignUpdateRequest(**common, realtime_settings=nested)
    script, _ = await build_campaign_voice_config(update, source, existing=previous)
    assert script["realtime_prompt"]["instructions"] == "NESTED"
    update = CampaignUpdateRequest(**common, realtime_settings=nested, realtime_prompt={"instructions": "EXPLICIT"})
    script, _ = await build_campaign_voice_config(update, source, existing=previous)
    assert script["realtime_prompt"]["instructions"] == "EXPLICIT"
    update = CampaignUpdateRequest(**common, realtime_settings={"speed": 1.2})
    script, _ = await build_campaign_voice_config(update, source, existing=previous)
    assert script["realtime_prompt"]["instructions"] == "OLD"


@pytest.mark.asyncio
async def test_invalid_xai_voice_fails_before_credentials_or_transport(monkeypatch):
    from app.domain.services import credential_resolver
    resolver = AsyncMock(return_value="synthetic-key")
    monkeypatch.setattr(credential_resolver, "get_credential_resolver", lambda: SimpleNamespace(resolve=resolver))
    owner = VoiceOrchestrator(db_client=None)
    gateway = AsyncMock()
    monkeypatch.setattr(owner, "_create_media_gateway", gateway)
    config = VoiceSessionConfig(pipeline_mode="realtime", realtime_settings={"provider": "xai"})
    assert await owner._create_realtime_voice_session(config, "synthetic-call", "synthetic-row") is None
    resolver.assert_not_awaited()
    gateway.assert_not_awaited()


@pytest.mark.parametrize("value", [None, "customer-private-data", "f" * 63, "g" * 64])
def test_untrusted_knowledge_reference_is_not_logged(value):
    from app.realtime.profile import knowledge_reference
    assert knowledge_reference(SimpleNamespace(_knowledge_snapshot_checksum=value)) == {"status": "unversioned", "snapshot_sha256": None}


def test_valid_knowledge_snapshot_hash_is_versioned_without_content():
    from app.realtime.profile import knowledge_reference
    assert knowledge_reference(SimpleNamespace(_knowledge_snapshot_checksum="A" * 64)) == {"status": "versioned", "snapshot_sha256": "a" * 64}


@pytest.mark.parametrize("settings", [
    {"turn_detection": {"type": "server_vad", "threshold": "nan"}},
    {"turn_detection": {"type": "server_vad", "prefix_padding_ms": 3.5}},
    {"turn_detection": {"type": "semantic_vad"}},
    {"speed": 1.6},
    {"speed": None},
    {"turn_detection": {"type": "server_vad", "threshold": None}},
    {"turn_detection": {"type": "server_vad", "prefix_padding_ms": {}}},
    {"reasoning_effort": "low"},
])
def test_xai_unsupported_controls_fail_before_serialization(settings):
    with pytest.raises(ValueError):
        XAIRealtimeSession(api_key="synthetic-key", voice="eve", settings=settings)

@pytest.mark.asyncio
async def test_partial_campaign_settings_do_not_replace_account_prompt_with_schema_default():
    from app.api.v1.schemas.campaigns import CampaignCreateRequest
    from app.api.v1.endpoints.campaign_voice_config import build_campaign_voice_config
    source = AIProviderConfig(pipeline_mode="realtime", realtime_settings={"prompt": {"instructions": "ACCOUNT"}})
    data = CampaignCreateRequest(name="Synthetic", company_name="Acme", agent_names=["Alex"], persona_type="lead_gen", realtime_settings={"speed": 1.2})
    script, _ = await build_campaign_voice_config(data, source)
    assert script["realtime_prompt"]["instructions"] == "ACCOUNT"

@pytest.mark.asyncio
async def test_all_offered_native_voices_and_hidden_xai_have_sanitized_wire_evidence(monkeypatch):
    import os
    from contextlib import asynccontextmanager
    from pathlib import Path
    import app.realtime.openai as module
    from app.api.v1.endpoints.ai_options import config as endpoint
    from app.domain.services import credential_resolver
    from tests.unit.test_ai_options_profile_roundtrip import Storage
    from app.realtime.catalog import REALTIME_MODEL, REALTIME_VOICES
    from app.realtime.xai import XAI_DEFAULT_MODEL
    from app.api.v1.schemas.campaigns import CampaignCreateRequest
    from app.api.v1.endpoints.campaign_voice_config import build_campaign_voice_config
    storage = Storage()
    @asynccontextmanager
    async def acquire(*args):
        yield storage
    monkeypatch.setattr(endpoint, "acquire_with_tenant", acquire)
    monkeypatch.setattr(credential_resolver, "get_credential_resolver", lambda: SimpleNamespace(resolve=AsyncMock(return_value="synthetic-key")))
    user, db = SimpleNamespace(tenant_id="synthetic-tenant"), SimpleNamespace(pool=object())
    class Accept(Handshake):
        async def send(self, value):
            value = json.loads(value)
            self.sent.append(value)
            self.inbox.append({"type": "session.updated", "session": value["session"]})
    evidence = []
    for index, voice in enumerate([entry["id"] for entry in REALTIME_VOICES] + ["eve", "custom-synthetic-voice"]):
        transport = Accept({})
        monkeypatch.setattr(module.websockets, "connect", AsyncMock(return_value=transport))
        xai = index >= len(REALTIME_VOICES)
        if xai:
            settings = {"provider": "xai", "speed": 1.1, "reasoning_effort": "high", "turn_detection": {"type": "server_vad", "threshold": .65}}
        else:
            settings = {"speed": 1.15, "reasoning_effort": "medium", "max_output_tokens": 512,
                        "noise_reduction": "far_field", "transcription_model": "gpt-4o-transcribe",
                        "turn_detection": {"type": "server_vad", "threshold": .65, "silence_duration_ms": 500},
                        "prompt": {"instructions": "SYNTHETIC PRIVATE CAMPAIGN NOTE"}}
        selected = AIProviderConfig(pipeline_mode="realtime", realtime_model=REALTIME_MODEL, realtime_voice=voice, realtime_settings=settings)
        saved = await endpoint.save_config(selected, user, db)
        source = await endpoint.get_config(user, db)
        for field in ("pipeline_mode", "realtime_model", "realtime_voice", "realtime_settings"):
            assert getattr(saved.config, field) == getattr(source, field)
        assert source.realtime_voice == voice
        if xai:
            # xAI remains opt-in through saved legacy configuration; the GPT
            # campaign editor intentionally does not advertise this provider.
            config = build_telephony_session_config(campaign=campaign({}), ai_config_override=source)
        else:
            data = CampaignCreateRequest(name="Synthetic", company_name="Synthetic", agent_names=["Alex"], persona_type="lead_gen")
            script, _ = await build_campaign_voice_config(data, source)
            config = build_telephony_session_config(campaign=campaign(json.loads(json.dumps(script))), ai_config_override=source)
        result = await assemble(monkeypatch, config, mock_connect=False)
        try:
            wire = transport.sent[0]["session"]
            profile = result.realtime_session.effective_profile()
            assert profile["submitted"]["voice"] == voice
            assert profile["submitted"]["model"] == (XAI_DEFAULT_MODEL if xai else REALTIME_MODEL)
            assert profile["submitted"]["input_format"] == {"type": "audio/pcmu"}
            assert profile["submitted"]["output_format"] == {"type": "audio/pcmu"}
            assert profile["submitted"]["turn_detection"]["threshold"] == .65
            assert profile["submitted"]["reasoning_effort"] == settings["reasoning_effort"]
            assert profile["submitted"]["speed"] == settings["speed"]
            assert profile["submitted"]["instructions_sha256"] == hashlib.sha256(wire["instructions"].encode()).hexdigest()
            assert "knowledge_lookup" in profile["submitted"]["enabled_tools"]
            assert "send_email" in profile["submitted"]["enabled_tools"]
            assert "temperature" not in wire
            assert profile["knowledge_reference"]["status"] == "unversioned"
            if not xai:
                assert profile["submitted"]["noise_reduction"] == {"type": "far_field"}
                assert profile["submitted"]["max_output_tokens"] == 512
                assert "SYNTHETIC PRIVATE CAMPAIGN NOTE" in wire["instructions"]
            else:
                assert "type" not in wire
                assert "turn_detection" in wire
                assert profile["submitted"]["noise_reduction_specified"] is False
            evidence.append({"offered_in_ui": not xai, "transport_sample_rate_hz": 8000,
                             "prompt_template": config.prompt_template, "prompt_version": config.prompt_version,
                             "profile": profile})
        finally:
            await result.realtime_session.close()
    document = {"scope": "Actual candidate AI Options save/reload SQL serialization, campaign/runtime/serializer with fake storage and synthetic WebSocket acknowledgement; no database/RLS, provider API or customer calls.",
                "source_base_revision": "7c0ffac7", "includes_uncommitted_ag01_changes": True,
                "limitations": ["Echoes are synthetic, not provider acceptance or account availability proof.",
                                "xAI is hidden opt-in; pinned grok-voice-think-fast-1.0 availability remains unverified.",
                                "Legacy xAI rows using an OpenAI voice require an explicit xAI voice selection.",
                                "No pinned knowledge snapshot is present in these assembly fixtures.",
                                "OpenAI reasoning none means omitted/provider default; temperature is inapplicable."],
                "profiles": evidence}
    serialized = json.dumps(document, indent=2)
    assert "SYNTHETIC PRIVATE CAMPAIGN NOTE" not in serialized
    assert "synthetic-key" not in serialized
    if target := os.environ.get("AG01_NATIVE_PROFILE_OUTPUT"):
        Path(target).write_text(serialized + "\n", encoding="utf-8")

@pytest.mark.asyncio
@pytest.mark.parametrize("echo", [{}, {"tools": None}, {"tools": [None, "provider-extra", {"type": "future_tool"}], "audio": None, "reasoning": None}, {"audio": {"input": {"transcription": None}, "output": None}, "tools": "future_provider_shape"}])
async def test_provider_ack_extra_or_nullable_fields_cannot_break_profile_logging(monkeypatch, echo):
    import app.realtime.openai as module
    class NullableEcho(Handshake):
        async def send(self, value):
            self.sent.append(json.loads(value))
            self.inbox.append({"type": "session.updated", "session": echo})
    transport = NullableEcho({})
    monkeypatch.setattr(module.websockets, "connect", AsyncMock(return_value=transport))
    session = OpenAIRealtimeSession(api_key="synthetic-key")
    try:
        assert await session.connect()
        profile = session.effective_profile()
        assert profile["session_update_acknowledged"]
        assert profile["acknowledged"]["voice"] is None
        assert profile["acknowledged"]["instructions_sha256"] is None
    finally:
        await session.close()

@pytest.mark.parametrize("provider,voice", [("openai", "retired-voice"), ("xai", "marin")])
def test_saved_unsupported_voice_requires_reselection_not_hidden_substitution(provider, voice):
    source = AIProviderConfig(pipeline_mode="realtime")
    script = {"realtime_voice": voice, "realtime_settings": {"provider": provider}}
    with pytest.raises(ValueError, match="voice"):
        build_telephony_session_config(campaign=campaign(script), ai_config_override=source)
    assert script["realtime_voice"] == voice


@pytest.mark.asyncio
async def test_xai_agent_selection_does_not_claim_label_as_effective_model(monkeypatch):
    import app.realtime.openai as module
    class Accept(Handshake):
        async def send(self, value):
            self.sent.append(json.loads(value))
            self.inbox.append({"type": "session.updated", "session": {}})
    transport = Accept({})
    monkeypatch.setattr(module.websockets, "connect", AsyncMock(return_value=transport))
    session = XAIRealtimeSession(api_key="synthetic-key", agent_id="synthetic-agent", voice="eve")
    try:
        assert await session.connect()
        profile = session.effective_profile()
        assert profile["agent_selection"] is True
        assert profile["submitted"]["model"] is None
        assert profile["acknowledged"]["model"] is None
        assert "synthetic-agent" not in json.dumps(profile)
    finally:
        await session.close()

@pytest.mark.asyncio
async def test_native_preview_http_handler_uses_native_path_without_tts(monkeypatch):
    from app.api.v1.endpoints.ai_options import preview as endpoint
    from app.realtime import preview
    from app.realtime.openai import RealtimeEvent
    from app.domain.services import credential_resolver
    from unittest.mock import MagicMock
    monkeypatch.setattr(credential_resolver, "get_credential_resolver", lambda: SimpleNamespace(resolve=AsyncMock(return_value="synthetic-key")))
    async def events():
        yield RealtimeEvent(kind="response_candidate", audio=b"\xff" * 160, text="Hello")
    session = SimpleNamespace(connect=AsyncMock(return_value=True), send_text=AsyncMock(), events=events, close=AsyncMock())
    factory = MagicMock(return_value=session)
    monkeypatch.setattr(preview, "OpenAIRealtimeSession", factory)
    tts_catalog = AsyncMock(side_effect=AssertionError("Native preview must not route through TTS"))
    monkeypatch.setattr(endpoint, "_get_live_cartesia_voices", tts_catalog)
    result = await endpoint.preview_voice(endpoint.VoicePreviewRequest(voice_id="ash", text="Hello", provider="realtime"), SimpleNamespace(tenant_id="synthetic-tenant"), SimpleNamespace(pool=object()))
    assert result.voice_id == "ash"
    assert result.duration_seconds == .02
    assert factory.call_args.kwargs["model"] == "gpt-realtime-2"
    assert factory.call_args.kwargs["voice"] == "ash"
    session.close.assert_awaited_once()
    tts_catalog.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("voice,key,status", [("unknown", "synthetic-key", 400), ("ash", None, 503)])
async def test_native_preview_http_handler_rejects_before_provider_creation(monkeypatch, voice, key, status):
    from app.api.v1.endpoints.ai_options import preview as endpoint
    from app.realtime import preview
    from app.domain.services import credential_resolver
    from unittest.mock import MagicMock
    from fastapi import HTTPException
    monkeypatch.setattr(credential_resolver, "get_credential_resolver", lambda: SimpleNamespace(resolve=AsyncMock(return_value=key)))
    factory = MagicMock(side_effect=AssertionError("No provider creation for invalid request"))
    monkeypatch.setattr(preview, "OpenAIRealtimeSession", factory)
    with pytest.raises(HTTPException) as exc:
        await endpoint.preview_voice(endpoint.VoicePreviewRequest(voice_id=voice, text="Hello", provider="realtime"), SimpleNamespace(tenant_id="synthetic-tenant"), SimpleNamespace(pool=object()))
    assert exc.value.status_code == status
    factory.assert_not_called()
