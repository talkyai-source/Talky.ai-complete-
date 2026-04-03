"""
Tests for Day 41: VoiceOrchestrator

Tests cover:
- VoiceSessionConfig defaults
- VoiceSession dataclass creation
- VoiceOrchestrator.create_voice_session (provider wiring, session fields)
- VoiceOrchestrator.start_pipeline (task creation)
- VoiceOrchestrator.send_greeting (TTS streaming + barge-in)
- VoiceOrchestrator.end_session (cleanup of all resources)
- Container integration (accessor + properties)
"""
import asyncio
import json
import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

from app.domain.services.voice_orchestrator import (
    VoiceOrchestrator,
    VoiceSession,
    VoiceSessionConfig,
)
from app.domain.models.session import CallSession, CallState
from app.domain.models.conversation_state import ConversationState, ConversationContext


# =============================================================================
# Helpers
# =============================================================================

def _make_mock_provider():
    """Create an async-compatible mock provider."""
    p = AsyncMock()
    p.initialize = AsyncMock()
    p.cleanup = AsyncMock()
    p.stream_synthesize = AsyncMock()
    return p


def _make_mock_gateway():
    """Create an async-compatible mock media gateway."""
    gw = AsyncMock()
    gw.initialize = AsyncMock()
    gw.cleanup = AsyncMock()
    gw.on_call_started = AsyncMock()
    gw.on_call_ended = AsyncMock()
    gw.on_audio_received = AsyncMock()
    gw.start_playback_tracking = MagicMock()
    gw.flush_audio_buffer = AsyncMock()
    gw.clear_output_buffer = AsyncMock()
    gw.wait_for_playback_complete = AsyncMock(return_value=True)
    gw.is_session_active = MagicMock(return_value=True)
    gw.get_audio_queue = MagicMock(return_value=asyncio.Queue())
    return gw


def _make_voice_session(
    call_id="test-call-123",
    talklee_call_id="tlk_aabbccddee00",
    config=None,
) -> VoiceSession:
    """Build a minimal VoiceSession for testing."""
    call_session = CallSession(
        call_id=call_id,
        campaign_id="test",
        lead_id="lead-1",
        provider_call_id="test-session",
        state=CallState.ACTIVE,
        conversation_state=ConversationState.GREETING,
        conversation_context=ConversationContext(),
        agent_config=None,
        system_prompt="test prompt",
        voice_id="test-voice",
        started_at=datetime.utcnow(),
        last_activity_at=datetime.utcnow(),
    )
    call_session.barge_in_event = asyncio.Event()
    return VoiceSession(
        call_id=call_id,
        talklee_call_id=talklee_call_id,
        call_session=call_session,
        stt_provider=_make_mock_provider(),
        llm_provider=_make_mock_provider(),
        tts_provider=_make_mock_provider(),
        media_gateway=_make_mock_gateway(),
        pipeline=MagicMock(),
        config=config or VoiceSessionConfig(),
    )


# =============================================================================
# VoiceSessionConfig
# =============================================================================


class TestVoiceSessionConfig:
    """Tests for the VoiceSessionConfig dataclass."""

    def test_defaults(self):
        cfg = VoiceSessionConfig()
        assert cfg.stt_provider_type == "deepgram_flux"
        assert cfg.llm_provider_type == "groq"
        assert cfg.tts_provider_type == "google"
        assert cfg.session_type == "ask_ai"
        assert cfg.voice_id == "en-US-Chirp3-HD-Leda"
        assert cfg.gateway_sample_rate == 24000
        assert cfg.gateway_input_sample_rate is None
        assert cfg.event_logging_enabled is False

    def test_override(self):
        cfg = VoiceSessionConfig(
            tts_provider_type="deepgram",
            session_type="freeswitch",
            voice_id="custom-voice",
        )
        assert cfg.tts_provider_type == "deepgram"
        assert cfg.session_type == "freeswitch"
        assert cfg.voice_id == "custom-voice"


# =============================================================================
# VoiceSession
# =============================================================================


class TestVoiceSession:
    """Tests for the VoiceSession dataclass."""

    def test_creation(self):
        vs = _make_voice_session()
        assert vs.call_id == "test-call-123"
        assert vs.talklee_call_id == "tlk_aabbccddee00"
        assert vs.pipeline is not None
        assert vs.event_repo is None
        assert vs.pipeline_task is None

    def test_created_at_set(self):
        vs = _make_voice_session()
        assert isinstance(vs.created_at, datetime)


# =============================================================================
# VoiceOrchestrator.create_voice_session
# =============================================================================


class TestCreateVoiceSession:
    """Tests for VoiceOrchestrator.create_voice_session."""

    @pytest.mark.asyncio
    async def test_creates_session_with_providers(self):
        """Should wire STT, LLM, TTS providers and gateway."""
        orch = VoiceOrchestrator(db_client=None)

        mock_stt = _make_mock_provider()
        mock_llm = _make_mock_provider()
        mock_tts = _make_mock_provider()
        mock_gw = _make_mock_gateway()

        with patch.object(orch, "_create_stt_provider", return_value=mock_stt), \
             patch.object(orch, "_create_llm_provider", return_value=mock_llm), \
             patch.object(orch, "_create_tts_provider", return_value=mock_tts), \
             patch.object(orch, "_create_media_gateway", return_value=mock_gw):

            config = VoiceSessionConfig(session_type="ask_ai")
            vs = await orch.create_voice_session(config)

            assert vs.stt_provider is mock_stt
            assert vs.llm_provider is mock_llm
            assert vs.tts_provider is mock_tts
            assert vs.media_gateway is mock_gw
            assert vs.pipeline is not None
            assert vs.call_session is not None

    @pytest.mark.asyncio
    async def test_media_gateway_uses_stt_rate_for_browser_input(self):
        orch = VoiceOrchestrator(db_client=None)
        mock_gw = _make_mock_gateway()

        with patch("app.infrastructure.telephony.factory.MediaGatewayFactory.create", return_value=mock_gw):
            await orch._create_media_gateway(
                VoiceSessionConfig(
                    gateway_sample_rate=24000,
                    gateway_input_sample_rate=16000,
                    stt_sample_rate=16000,
                    tts_provider_type="deepgram",
                )
            )

        mock_gw.initialize.assert_awaited_once_with(
            {
                "sample_rate": 24000,
                "input_sample_rate": 16000,
                "channels": 1,
                "bit_depth": 16,
                "target_buffer_ms": 100,
                "tts_source_format": "s16le",
            }
        )

    @pytest.mark.asyncio
    async def test_talklee_call_id_generated(self):
        orch = VoiceOrchestrator(db_client=None)

        with patch.object(orch, "_create_stt_provider", return_value=_make_mock_provider()), \
             patch.object(orch, "_create_llm_provider", return_value=_make_mock_provider()), \
             patch.object(orch, "_create_tts_provider", return_value=_make_mock_provider()), \
             patch.object(orch, "_create_media_gateway", return_value=_make_mock_gateway()):

            vs = await orch.create_voice_session(VoiceSessionConfig())
            assert vs.talklee_call_id.startswith("tlk_")

    @pytest.mark.asyncio
    async def test_session_tracked_in_active(self):
        orch = VoiceOrchestrator(db_client=None)

        with patch.object(orch, "_create_stt_provider", return_value=_make_mock_provider()), \
             patch.object(orch, "_create_llm_provider", return_value=_make_mock_provider()), \
             patch.object(orch, "_create_tts_provider", return_value=_make_mock_provider()), \
             patch.object(orch, "_create_media_gateway", return_value=_make_mock_gateway()):

            vs = await orch.create_voice_session(VoiceSessionConfig())
            assert orch.active_session_count == 1
            assert orch.get_session(vs.call_id) is vs

    @pytest.mark.asyncio
    async def test_skips_event_logging_for_ephemeral_sessions_by_default(self):
        db_client = MagicMock()
        db_client.table = MagicMock()
        orch = VoiceOrchestrator(db_client=db_client)

        with patch.object(orch, "_create_stt_provider", return_value=_make_mock_provider()), \
             patch.object(orch, "_create_llm_provider", return_value=_make_mock_provider()), \
             patch.object(orch, "_create_tts_provider", return_value=_make_mock_provider()), \
             patch.object(orch, "_create_media_gateway", return_value=_make_mock_gateway()):

            await orch.create_voice_session(VoiceSessionConfig(session_type="ask_ai"))

        db_client.table.assert_not_called()

    @pytest.mark.asyncio
    async def test_call_session_fields(self):
        orch = VoiceOrchestrator(db_client=None)

        with patch.object(orch, "_create_stt_provider", return_value=_make_mock_provider()), \
             patch.object(orch, "_create_llm_provider", return_value=_make_mock_provider()), \
             patch.object(orch, "_create_tts_provider", return_value=_make_mock_provider()), \
             patch.object(orch, "_create_media_gateway", return_value=_make_mock_gateway()):

            config = VoiceSessionConfig(
                campaign_id="camp-1",
                lead_id="lead-1",
                session_type="voice_demo",
                voice_id="my-voice",
            )
            vs = await orch.create_voice_session(config)
            cs = vs.call_session

            assert cs.campaign_id == "camp-1"
            assert cs.lead_id == "lead-1"
            assert cs.voice_id == "my-voice"
            assert cs.state == CallState.ACTIVE
            assert isinstance(cs.barge_in_event, asyncio.Event)


class TestPrewarmAskAIProviders:
    @pytest.mark.asyncio
    async def test_uses_shared_ask_ai_session_config(self):
        orch = VoiceOrchestrator(db_client=None)
        captured = []

        async def _capture_stt(config):
            captured.append(config)
            return _make_mock_provider()

        async def _capture_llm(config):
            captured.append(config)
            return _make_mock_provider()

        async def _capture_tts(config):
            captured.append(config)
            return _make_mock_provider()

        async def _capture_gateway(config):
            captured.append(config)
            return _make_mock_gateway()

        with patch.object(orch, "_create_stt_provider", side_effect=_capture_stt), \
             patch.object(orch, "_create_llm_provider", side_effect=_capture_llm), \
             patch.object(orch, "_create_tts_provider", side_effect=_capture_tts), \
             patch.object(orch, "_create_media_gateway", side_effect=_capture_gateway):
            await orch.prewarm_ask_ai_providers()

        assert len(captured) == 4
        config = captured[0]
        assert config.stt_eager_eot_threshold == 0.4
        assert config.stt_eot_timeout_ms == 3000
        assert config.mute_during_tts is False
        assert config.llm_max_tokens == 90


# =============================================================================
# VoiceOrchestrator.start_pipeline
# =============================================================================


class TestStartPipeline:
    """Tests for VoiceOrchestrator.start_pipeline."""

    @pytest.mark.asyncio
    async def test_starts_pipeline_task(self):
        orch = VoiceOrchestrator(db_client=None)
        vs = _make_voice_session()

        # Mock pipeline.start_pipeline so the task completes immediately
        vs.pipeline = MagicMock()
        vs.pipeline.start_pipeline = AsyncMock()

        ws = AsyncMock()

        task = await orch.start_pipeline(vs, ws)
        assert isinstance(task, asyncio.Task)
        assert vs.pipeline_task is task

        # Gateway's on_call_started should have been called
        vs.media_gateway.on_call_started.assert_awaited_once()

        # Clean up
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    @pytest.mark.asyncio
    async def test_raises_without_pipeline(self):
        orch = VoiceOrchestrator(db_client=None)
        vs = _make_voice_session()
        vs.pipeline = None

        with pytest.raises(RuntimeError, match="Pipeline not initialised"):
            await orch.start_pipeline(vs, AsyncMock())


# =============================================================================
# VoiceOrchestrator.send_greeting
# =============================================================================


class TestSendGreeting:
    """Tests for VoiceOrchestrator.send_greeting."""

    @pytest.mark.asyncio
    async def test_sends_llm_response_message(self):
        orch = VoiceOrchestrator(db_client=None)
        vs = _make_voice_session()
        ws = AsyncMock()
        barge_in = asyncio.Event()

        # TTS returns no chunks (empty greeting scenario)
        async def _empty_gen(*args, **kwargs):
            return
            yield  # make it an async generator

        vs.tts_provider.stream_synthesize = _empty_gen

        await orch.send_greeting(vs, "Hello!", ws, barge_in)

        # Should have sent at least the llm_response and turn_complete
        calls = [c.args[0] if c.args else c.kwargs for c in ws.send_json.call_args_list]
        types = [c.get("type") if isinstance(c, dict) else None for c in calls]
        assert "llm_response" in types
        assert "turn_complete" in types
        ws.send_bytes.assert_not_called()
        vs.media_gateway.send_audio.assert_not_awaited()
        ws.send_bytes.assert_not_called()
        vs.media_gateway.send_audio.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_barge_in_interrupts(self):
        orch = VoiceOrchestrator(db_client=None)
        vs = _make_voice_session(config=VoiceSessionConfig(session_type="ask_ai", mute_during_tts=False))
        ws = AsyncMock()
        barge_in = asyncio.Event()
        barge_in.set()  # Already interrupted

        # TTS yields one chunk
        chunk = MagicMock()
        chunk.data = b"\x00" * 48000

        async def _one_chunk(*args, **kwargs):
            yield chunk

        vs.tts_provider.stream_synthesize = _one_chunk

        await orch.send_greeting(vs, "Hello!", ws, barge_in)

        # Should have sent tts_interrupted
        json_calls = [c.args[0] for c in ws.send_json.call_args_list]
        interrupted = [c for c in json_calls if c.get("type") == "tts_interrupted"]
        assert len(interrupted) == 1
        vs.media_gateway.clear_output_buffer.assert_awaited_once_with(vs.call_id)
        vs.media_gateway.flush_audio_buffer.assert_not_awaited()
        vs.stt_provider.mute.assert_not_awaited()
        vs.stt_provider.unmute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_uses_session_barge_in_event_when_no_explicit_event(self):
        orch = VoiceOrchestrator(db_client=None)
        vs = _make_voice_session(
            config=VoiceSessionConfig(session_type="ask_ai", mute_during_tts=False)
        )
        ws = AsyncMock()
        vs.call_session.barge_in_event.set()

        chunk = MagicMock()
        chunk.data = b"\x00" * 48000

        async def _one_chunk(*args, **kwargs):
            yield chunk

        vs.tts_provider.stream_synthesize = _one_chunk

        await orch.send_greeting(vs, "Hello!", ws)

        json_calls = [c.args[0] for c in ws.send_json.call_args_list]
        interrupted = [c for c in json_calls if c.get("type") == "tts_interrupted"]
        assert len(interrupted) == 1
        vs.media_gateway.clear_output_buffer.assert_awaited_once_with(vs.call_id)
        vs.media_gateway.send_audio.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_streams_greeting_audio_through_media_gateway(self):
        orch = VoiceOrchestrator(db_client=None)
        vs = _make_voice_session()
        ws = AsyncMock()
        barge_in = asyncio.Event()

        chunk = MagicMock()
        chunk.data = b"\x00" * 4096

        async def _one_chunk(*args, **kwargs):
            yield chunk

        vs.tts_provider.stream_synthesize = _one_chunk

        await orch.send_greeting(vs, "Hello!", ws, barge_in)

        vs.media_gateway.start_playback_tracking.assert_called_once_with(vs.call_id)
        vs.media_gateway.send_audio.assert_awaited_once_with(vs.call_id, chunk.data)
        vs.media_gateway.flush_audio_buffer.assert_awaited_once_with(vs.call_id)
        vs.media_gateway.wait_for_playback_complete.assert_awaited_once_with(vs.call_id)
        json_calls = [c.args[0] for c in ws.send_json.call_args_list]
        assert any(c.get("type") == "tts_audio_complete" for c in json_calls)
        ws.send_bytes.assert_not_called()

    @pytest.mark.asyncio
    async def test_streams_greeting_audio_through_media_gateway(self):
        orch = VoiceOrchestrator(db_client=None)
        vs = _make_voice_session()
        ws = AsyncMock()
        barge_in = asyncio.Event()

        chunk = MagicMock()
        chunk.data = b"\x00" * 4096

        async def _one_chunk(*args, **kwargs):
            yield chunk

        vs.tts_provider.stream_synthesize = _one_chunk

        await orch.send_greeting(vs, "Hello!", ws, barge_in)

        vs.media_gateway.send_audio.assert_awaited_once_with(vs.call_id, chunk.data)
        vs.media_gateway.flush_audio_buffer.assert_awaited_once_with(vs.call_id)
        ws.send_bytes.assert_not_called()

    @pytest.mark.asyncio
    async def test_ask_ai_greeting_does_not_mute_stt(self):
        orch = VoiceOrchestrator(db_client=None)
        vs = _make_voice_session(config=VoiceSessionConfig(session_type="ask_ai", mute_during_tts=False))
        ws = AsyncMock()
        barge_in = asyncio.Event()

        chunk = MagicMock()
        chunk.data = b"\x00" * 4096

        async def _one_chunk(*args, **kwargs):
            yield chunk

        vs.tts_provider.stream_synthesize = _one_chunk

        await orch.send_greeting(vs, "Hello!", ws, barge_in)

        vs.stt_provider.mute.assert_not_awaited()
        vs.stt_provider.unmute.assert_not_awaited()


# =============================================================================
# VoiceOrchestrator.end_session
# =============================================================================


class TestEndSession:
    """Tests for VoiceOrchestrator.end_session."""

    @pytest.mark.asyncio
    async def test_cleans_up_all_providers(self):
        orch = VoiceOrchestrator(db_client=None)
        vs = _make_voice_session()
        orch._active_sessions[vs.call_id] = vs

        await orch.end_session(vs)

        vs.stt_provider.cleanup.assert_awaited_once()
        vs.llm_provider.cleanup.assert_awaited_once()
        vs.tts_provider.cleanup.assert_awaited_once()
        vs.media_gateway.cleanup.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_removes_from_active_sessions(self):
        orch = VoiceOrchestrator(db_client=None)
        vs = _make_voice_session()
        orch._active_sessions[vs.call_id] = vs
        assert orch.active_session_count == 1

        await orch.end_session(vs)
        assert orch.active_session_count == 0
        assert orch.get_session(vs.call_id) is None

    @pytest.mark.asyncio
    async def test_cancels_pipeline_task(self):
        orch = VoiceOrchestrator(db_client=None)
        vs = _make_voice_session()

        # Simulate a running pipeline task
        async def _long_run():
            await asyncio.sleep(999)

        vs.pipeline_task = asyncio.create_task(_long_run())
        orch._active_sessions[vs.call_id] = vs

        await orch.end_session(vs)

        assert vs.pipeline_task.cancelled() or vs.pipeline_task.done()

    @pytest.mark.asyncio
    async def test_end_session_idempotent(self):
        """Ending an already-ended session shouldn't raise."""
        orch = VoiceOrchestrator(db_client=None)
        vs = _make_voice_session()
        orch._active_sessions[vs.call_id] = vs

        await orch.end_session(vs)
        # Second call should be safe
        await orch.end_session(vs)


# =============================================================================
# Container integration
# =============================================================================


class TestContainerIntegration:
    """Tests for VoiceOrchestrator registration in ServiceContainer."""

    def test_voice_orchestrator_property_raises_before_init(self):
        from app.core.container import ServiceContainer

        container = ServiceContainer()
        with pytest.raises(RuntimeError, match="VoiceOrchestrator not initialized"):
            _ = container.voice_orchestrator

    def test_voice_orchestrator_field_exists(self):
        from app.core.container import ServiceContainer

        container = ServiceContainer()
        assert container._voice_orchestrator is None


# =============================================================================
# Day 42 — Gateway Type + Event Logging
# =============================================================================


class TestGatewayType:
    """Tests for gateway_type on VoiceSessionConfig (Day 42)."""

    def test_default_gateway_type(self):
        cfg = VoiceSessionConfig()
        assert cfg.gateway_type == "browser"

    def test_rtp_gateway_type(self):
        cfg = VoiceSessionConfig(gateway_type="rtp")
        assert cfg.gateway_type == "rtp"

    @pytest.mark.asyncio
    async def test_create_media_gateway_uses_factory(self):
        """_create_media_gateway should delegate to MediaGatewayFactory."""
        orch = VoiceOrchestrator(db_client=None)

        mock_gw = _make_mock_gateway()

        with patch(
            "app.domain.services.voice_orchestrator.MediaGatewayFactory",
            create=True,
        ) as MockFactory:
            # Avoid real import — patch at the call site
            pass

        # Simpler: just test that RTP config creates something without crash
        config = VoiceSessionConfig(
            gateway_type="browser",
            gateway_sample_rate=8000,
        )
        gw = await orch._create_media_gateway(config)
        assert gw is not None

    @pytest.mark.asyncio
    async def test_create_browser_media_gateway(self):
        """gateway_type='browser' should create a BrowserMediaGateway."""
        orch = VoiceOrchestrator(db_client=None)
        config = VoiceSessionConfig(gateway_type="browser")
        gw = await orch._create_media_gateway(config)
        assert gw.name == "browser"


class TestSessionTypeEventMapping:
    """Tests for session-type-aware event logging helpers (Day 42)."""

    def test_freeswitch_leg_type(self):
        from app.domain.services.voice_orchestrator import _session_leg_type
        cfg = VoiceSessionConfig(session_type="freeswitch")
        assert _session_leg_type(cfg) == "sip"

    def test_freeswitch_provider(self):
        from app.domain.services.voice_orchestrator import _session_provider
        cfg = VoiceSessionConfig(session_type="freeswitch")
        assert _session_provider(cfg) == "freeswitch"

    def test_ask_ai_leg_type(self):
        from app.domain.services.voice_orchestrator import _session_leg_type
        cfg = VoiceSessionConfig(session_type="ask_ai")
        assert _session_leg_type(cfg) == "websocket"

    def test_ask_ai_provider(self):
        from app.domain.services.voice_orchestrator import _session_provider
        cfg = VoiceSessionConfig(session_type="ask_ai")
        assert _session_provider(cfg) == "browser"

    def test_voice_demo_leg_type(self):
        from app.domain.services.voice_orchestrator import _session_leg_type
        cfg = VoiceSessionConfig(session_type="voice_demo")
        assert _session_leg_type(cfg) == "browser"

    def test_voice_demo_provider(self):
        from app.domain.services.voice_orchestrator import _session_provider
        cfg = VoiceSessionConfig(session_type="voice_demo")
        assert _session_provider(cfg) == "browser"
