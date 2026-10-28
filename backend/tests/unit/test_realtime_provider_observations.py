"""Real adapter event paths, synthetic transport/credentials, no provider calls."""
import asyncio
import base64
import json
from collections import deque
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.infrastructure.metrics import provider_observations as observations
from app.realtime.openai import OpenAIRealtimeSession
from app.realtime.xai import XAIRealtimeSession


@pytest.fixture
def captured(monkeypatch):
    items = []
    monkeypatch.setattr(observations, "_signing_key", lambda: b"synthetic-observation-key")
    monkeypatch.setattr(observations, "publish", lambda value: items.append(value))
    return items


def session(kind=OpenAIRealtimeSession):
    return kind(api_key="synthetic-secret-never-retained", instructions="private prompt",
        observation_context={"source": "tenant", "tenant_id": "11111111-1111-4111-8111-111111111111",
            "credential_id": "22222222-2222-4222-8222-222222222222"})


async def created(subject, identity="response-private"):
    await subject._handle_server_event({"type": "response.created", "response": {"id": identity}})


async def audio(subject, identity="response-private", *, transcript=True):
    await subject._handle_server_event({"type": "response.output_audio.delta", "response_id": identity,
        "item_id": "private-item", "delta": base64.b64encode(b"private-audio").decode()})
    if transcript:
        await subject._handle_server_event({"type": "response.output_audio_transcript.done", "response_id": identity,
            "item_id": "private-item", "transcript": "private transcript"})


async def done(subject, status="completed", identity="response-private", details=None):
    await subject._handle_server_event({"type": "response.done", "response": {
        "id": identity, "status": status, "status_details": details}})


@pytest.mark.parametrize("kind,provider", [(OpenAIRealtimeSession, "openai"), (XAIRealtimeSession, "xai")])
async def test_owned_audio_and_terminal_proof_emit_one_scoped_content_free_observation(captured, kind, provider):
    subject = session(kind)
    await created(subject)
    await audio(subject)
    assert captured == []
    await done(subject)
    await done(subject)  # replay cannot produce another observation
    await subject.close()
    assert len(captured) == 1
    item = captured[0]
    assert (item.component, item.provider, item.outcome) == ("realtime", provider, "completed")
    assert item.protocol_accepted and item.output_seen and item.first_output_ms is not None
    assert item.credential_source == "tenant" and item.credential_id.startswith("22222222")
    assert item.tenant_id.startswith("11111111")
    raw = item.to_json()
    assert all(value not in raw for value in ("private", "synthetic-secret", "prompt", "transcript", "arguments"))


@pytest.mark.parametrize("status,details,expected", [
    ("completed", None, "completed"), ("cancelled", None, "cancelled"),
    ("incomplete", {"reason": "max_output_tokens"}, "incomplete"),
    ("incomplete", {"reason": "turn_detected"}, "cancelled"),
    ("failed", {"error": {"code": "rate_limit_exceeded", "message": "private error"}}, "failed"),
])
async def test_response_done_status_is_not_assumed_success(captured, status, details, expected):
    subject = session()
    await created(subject)
    await audio(subject)
    await done(subject, status, details=details)
    assert captured[-1].outcome == expected
    assert captured[-1].error_category == ("rate_limit" if status == "failed" else "none")


@pytest.mark.parametrize("mode", ["empty", "missing_transcript", "stale_audio", "invalid_tool", "mixed_partial_audio_invalid_tool"])
async def test_completed_without_valid_owned_output_remains_incomplete(captured, mode):
    subject = session()
    await created(subject)
    if mode in {"missing_transcript", "mixed_partial_audio_invalid_tool"}:
        await audio(subject, transcript=False)
    if mode == "stale_audio":
        await audio(subject, "other-response")
    if mode in {"invalid_tool", "mixed_partial_audio_invalid_tool"}:
        await subject._handle_server_event({"type": "response.function_call_arguments.done", "response_id": "response-private",
            "call_id": "private-tool-id", "name": "private-tool", "arguments": "not-json"})
    await done(subject)
    assert captured[-1].outcome == "incomplete"
    if mode not in {"missing_transcript", "mixed_partial_audio_invalid_tool"}:
        assert not captured[-1].output_seen and captured[-1].first_output_ms is None


async def test_structured_tool_output_is_provider_output_but_not_business_effect_proof(captured):
    subject = session()
    await created(subject)
    await subject._handle_server_event({"type": "response.function_call_arguments.done", "response_id": "response-private",
        "call_id": "private-tool-id", "name": "private-tool", "arguments": '{"private":"content"}'})
    await done(subject)
    assert captured[-1].outcome == "completed" and captured[-1].output_seen
    assert "private" not in captured[-1].to_json()


async def test_interruption_late_completion_and_replacement_do_not_relabel_current_response(captured):
    subject = session()
    await created(subject)
    await audio(subject)
    subject._on_interruption("speech_started")
    await created(subject, "next")
    await done(subject)  # late response-private completion
    await done(subject, identity="next")
    assert [item.outcome for item in captured] == ["cancelled", "incomplete"]
    assert not captured[-1].output_seen


async def test_transport_error_and_explicit_close_keep_distinct_outcomes(captured):
    subject = session()
    await created(subject)
    async def transport():
        if False:
            yield None
        raise ConnectionError("private transport details")
    subject._ws = transport()
    await subject._recv_loop()
    assert captured[-1].outcome == "failed" and captured[-1].error_category == "transport"
    another = session()
    await created(another)
    await another.close()
    assert captured[-1].outcome == "cancelled"


class Handshake:
    def __init__(self):
        self.queue = deque([{"type": "session.created"}])
    async def recv(self):
        return json.dumps(self.queue.popleft())
    async def send(self, raw):
        self.queue.append({"type": "session.updated", "session": json.loads(raw)["session"]})
    def __aiter__(self):
        return self
    async def __anext__(self):
        await asyncio.Event().wait()
    async def close(self):
        pass


async def test_connected_is_not_a_successful_model_response(captured, monkeypatch):
    monkeypatch.setattr("app.realtime.openai.websockets.connect", AsyncMock(return_value=Handshake()))
    subject = session()
    assert await subject.connect()
    await subject.close()
    assert len(captured) == 1
    assert captured[0].outcome == "connected" and captured[0].protocol_accepted
    assert not captured[0].output_seen and captured[0].first_output_ms is None


async def test_failed_handshake_reports_actual_error_not_connected(captured, monkeypatch):
    monkeypatch.setattr("app.realtime.openai.websockets.connect", AsyncMock(side_effect=TimeoutError()))
    assert not await session().connect()
    assert captured[-1].outcome == "failed" and captured[-1].error_category == "timeout"
    assert not captured[-1].protocol_accepted


@pytest.mark.parametrize("provider", ["openai", "xai"])
async def test_runtime_passes_the_exact_selected_key_and_retained_scope(monkeypatch, provider):
    from app.domain.services import credential_resolver
    from app.domain.services.voice_pipeline import action_execution
    from app.domain.services.voice_orchestrator import VoiceSessionConfig
    from app.realtime import runtime, openai, xai
    material = credential_resolver.ResolvedCredential("selected-key", source="platform", tenant_id="tenant")
    resolver = SimpleNamespace(resolve_with_metadata=AsyncMock(return_value=material))
    monkeypatch.setattr(credential_resolver, "get_credential_resolver", lambda: resolver)
    monkeypatch.setattr(action_execution, "prepare_voice_action_context", AsyncMock())
    captured_options = []
    def factory(**options):
        captured_options.append(options)
        return SimpleNamespace(connect=AsyncMock(return_value=False), close=AsyncMock())
    monkeypatch.setattr(openai if provider == "openai" else xai,
                        "OpenAIRealtimeSession" if provider == "openai" else "XAIRealtimeSession", factory)
    gateway = SimpleNamespace(_sample_rate=8000, cleanup=AsyncMock())
    owner = SimpleNamespace(_create_media_gateway=AsyncMock(return_value=gateway))
    config = VoiceSessionConfig(pipeline_mode="realtime", tenant_id="tenant", realtime_voice="eve" if provider == "xai" else "marin",
                                realtime_settings={"provider": provider})
    assert await runtime.create_realtime_voice_session(owner, config, "call", "row") is None
    resolver.resolve_with_metadata.assert_awaited_once_with(provider, tenant_id="tenant")
    assert captured_options[0]["api_key"] == material.secret
    assert captured_options[0]["observation_context"] == material.observation_context
