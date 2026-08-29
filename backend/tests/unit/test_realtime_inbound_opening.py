"""Realtime true-inbound opening and dormant-provider ingress guards."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api.v1.endpoints.twilio_bridge as twilio_bridge
import app.api.v1.endpoints.vonage_bridge as vonage_bridge
from app.domain.services.telephony import lifecycle
from app.domain.services.voice_pipeline.realtime_bridge import RealtimeBridge
from app.domain.services.voice_orchestrator import (
    Direction,
    VoiceOrchestrator,
    VoiceSessionConfig,
)
from app.services.scripts.realtime_instructions import (
    RealtimePersona,
    build_realtime_instructions,
)


def _admission(*, opening_mode: str, greeting: str = "Welcome to Acme") -> dict:
    return {
        "opening_mode": opening_mode,
        "config_snapshot": {
            "campaign": {},
            "inbound_config": {
                "opening_mode": opening_mode,
                "greeting": greeting,
                "after_hours_message": "Acme is closed. Please leave a short message.",
                "qualification_config": {},
            },
        },
    }


@pytest.mark.parametrize(
    ("opening_mode", "action", "expected_greet", "expected_greeting", "intake"),
    [
        ("agent_first", "agent", True, "Welcome to Acme", False),
        ("caller_first", "agent", False, None, False),
        (
            "caller_first",
            "voicemail",
            True,
            "Acme is closed. Please leave a short message.",
            True,
        ),
    ],
)
def test_pinned_inbound_config_carries_realtime_opening_policy(
    monkeypatch,
    opening_mode,
    action,
    expected_greet,
    expected_greeting,
    intake,
):
    config = VoiceSessionConfig(
        direction=Direction.INBOUND,
        pipeline_mode="realtime",
        system_prompt="Pinned campaign prompt",
    )
    monkeypatch.setattr(lifecycle, "apply_qualification_overrides", lambda c, q: c)
    monkeypatch.setattr(
        lifecycle,
        "_pinned_inbound_ai_config",
        lambda _payload: (object(), object()),
    )
    monkeypatch.setattr(
        lifecycle,
        "_build_telephony_session_config",
        lambda **_kwargs: config,
    )

    built, _campaign = lifecycle._build_pinned_inbound_config(
        _admission(opening_mode=opening_mode),
        gateway_type="telephony",
        selected_action=action,
    )

    assert built.realtime_greet_on_start is expected_greet
    assert built.realtime_opening_greeting == expected_greeting
    assert built.realtime_message_intake is intake


def test_realtime_inbound_instructions_never_use_outbound_framing():
    text = build_realtime_instructions(
        RealtimePersona(
            agent_name="Sam",
            company_name="Acme",
            call_direction="inbound",
            opening_greeting="Thanks for calling Acme. How can I help?",
        )
    )

    assert "caller contacted the company" in text
    assert "why you're calling" not in text
    assert "Thanks for calling Acme. How can I help?" in text
    assert "Never say or imply that you called them" in text


def test_realtime_message_intake_is_conversational_not_sales_or_voicemail_claims():
    text = build_realtime_instructions(
        RealtimePersona(
            agent_name="Sam",
            company_name="Acme",
            call_direction="inbound",
            opening_greeting="We are closed. Please leave a short message.",
            message_intake=True,
        )
    )

    assert "INBOUND after-hours AI message-intake" in text
    assert "do not sell or qualify" in text
    assert "invite one concise message" in text
    assert "why you're calling" not in text


def test_realtime_outbound_opening_remains_unchanged():
    text = build_realtime_instructions(RealtimePersona())
    assert "why you're calling" in text


def test_ai_message_intake_projects_answered_not_outbound_voicemail():
    voice_session = SimpleNamespace(
        _inbound_selected_action="voicemail",
        _pipeline_failed=False,
    )

    assert (
        lifecycle._resolve_inbound_terminal_outcome(
            voice_session,
            {},
            hangup_reason=None,
        )
        == "answered"
    )

    voice_session._pipeline_failed = True
    assert (
        lifecycle._resolve_inbound_terminal_outcome(
            voice_session,
            {},
            hangup_reason=None,
        )
        == "failed"
    )


def test_recovered_inbound_outcome_is_deterministic_without_live_session():
    assert (
        lifecycle._resolve_inbound_terminal_outcome(
            None,
            {"_transfer_connected": True},
        )
        == "answered"
    )
    assert lifecycle._resolve_inbound_terminal_outcome(None, {}) == "failed"


@pytest.mark.asyncio
async def test_realtime_interruption_flushes_opening_audio_and_unlatches_event():
    barge_in = asyncio.Event()
    observed = []

    async def events():
        yield SimpleNamespace(kind="interrupted")

    async def clear_output_buffer(call_id):
        observed.append((call_id, barge_in.is_set()))

    bridge = RealtimeBridge(
        call_id="inbound-barge-in",
        realtime_session=SimpleNamespace(events=events),
        media_gateway=SimpleNamespace(clear_output_buffer=clear_output_buffer),
        barge_in_event=barge_in,
        call_direction="inbound",
    )

    await bridge._pump_model_events()

    assert observed == [("inbound-barge-in", True)]
    assert barge_in.is_set() is False


@pytest.mark.asyncio
async def test_orchestrator_honors_explicit_agent_first_inbound_realtime_policy():
    orchestrator = VoiceOrchestrator(db_client=None)
    config = VoiceSessionConfig(
        direction=Direction.INBOUND,
        pipeline_mode="realtime",
        gateway_sample_rate=8000,
        realtime_greet_on_start=True,
        realtime_opening_greeting="Thanks for calling Acme.",
    )
    resolver = SimpleNamespace(resolve=AsyncMock(return_value="test-api-key"))
    realtime_session = SimpleNamespace(
        connect=AsyncMock(return_value=True), close=AsyncMock()
    )
    gateway = SimpleNamespace(_sample_rate=8000, set_barge_in_event=MagicMock())

    with (
        patch(
            "app.domain.services.credential_resolver.get_credential_resolver",
            return_value=resolver,
        ),
        patch(
            "app.infrastructure.realtime.openai_realtime.OpenAIRealtimeSession",
            return_value=realtime_session,
        ),
        patch(
            "app.infrastructure.realtime.openai_realtime.knowledge_lookup_tool",
            return_value={},
        ),
        patch(
            "app.domain.services.voice_pipeline.realtime_bridge.RealtimeBridge"
        ) as bridge_cls,
        patch(
            "app.core.container.get_container",
            return_value=SimpleNamespace(is_initialized=False),
        ),
        patch.object(
            orchestrator,
            "_create_media_gateway",
            new=AsyncMock(return_value=gateway),
        ),
    ):
        session = await orchestrator._create_realtime_voice_session(
            config,
            call_id="agent-first-inbound",
            talklee_call_id="INREALTIME000000001",
        )

    assert session is not None
    assert bridge_cls.call_args.kwargs["greet_on_start"] is True


@pytest.mark.parametrize(
    ("module", "direction", "payload"),
    [
        (twilio_bridge, "inbound", {"CallSid": "CA1", "From": "+1", "To": "+2"}),
        (vonage_bridge, "inbound", {"uuid": "uuid-1", "from": "+1", "to": "+2"}),
    ],
)
def test_signed_legacy_provider_answer_still_rejects_inbound(
    monkeypatch, module, direction, payload
):
    app = FastAPI()
    app.include_router(module.router, prefix="/api/v1")
    if module is twilio_bridge:
        monkeypatch.setenv("TWILIO_BRIDGE_ENABLED", "true")
        monkeypatch.setenv("TWILIO_AUTH_TOKEN", "configured")
        monkeypatch.setattr(module, "verify_twilio_signature", lambda **_kwargs: True)
        monkeypatch.setattr(
            module,
            "_mint_ws_token",
            lambda **_kwargs: (_ for _ in ()).throw(
                AssertionError("inbound must not mint a media token")
            ),
        )
        response = TestClient(app).post(
            "/api/v1/twilio/answer",
            data={**payload, "Direction": direction},
        )
    else:
        monkeypatch.setenv("VONAGE_BRIDGE_ENABLED", "true")
        monkeypatch.setenv("VONAGE_SIGNATURE_SECRET", "configured")
        monkeypatch.setattr(module, "verify_vonage_signature", lambda **_kwargs: True)
        monkeypatch.setattr(
            module,
            "_mint_ws_token",
            lambda **_kwargs: (_ for _ in ()).throw(
                AssertionError("inbound must not mint an audio token")
            ),
        )
        response = TestClient(app).post(
            "/api/v1/vonage/answer",
            json={**payload, "direction": direction},
        )

    assert response.status_code == 503


def test_legacy_provider_direction_guard_preserves_outbound_only():
    assert twilio_bridge._is_supported_answer_direction("outbound-api") is True
    assert twilio_bridge._is_supported_answer_direction("outbound-dial") is True
    assert vonage_bridge._is_supported_answer_direction("outbound") is True
    for unsafe in (None, "", "inbound", "unknown"):
        assert twilio_bridge._is_supported_answer_direction(unsafe) is False
        assert vonage_bridge._is_supported_answer_direction(unsafe) is False


@pytest.mark.parametrize("module", [twilio_bridge, vonage_bridge])
def test_provider_event_webhook_is_closed_when_bridge_disabled(monkeypatch, module):
    app = FastAPI()
    app.include_router(module.router, prefix="/api/v1")
    if module is twilio_bridge:
        monkeypatch.delenv("TWILIO_BRIDGE_ENABLED", raising=False)
        response = TestClient(app).post("/api/v1/twilio/event", data={})
    else:
        monkeypatch.delenv("VONAGE_BRIDGE_ENABLED", raising=False)
        response = TestClient(app).post("/api/v1/vonage/event", json={})
    assert response.status_code == 404
