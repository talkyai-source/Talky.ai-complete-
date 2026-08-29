"""Direction isolation for answering-machine detection.

AMD describes the remote party that answered a platform-originated call.  A
caller speaking on a genuine inbound call must never be classified as that
remote answering machine, even when their words resemble a voicemail greeting.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.domain.services.voice_orchestrator import (
    Direction,
    VoiceOrchestrator,
    VoiceSessionConfig,
)
from app.domain.services.voice_pipeline.machine_detection import (
    handle_machine_interim,
)
from app.domain.services.voice_pipeline.realtime_bridge import RealtimeBridge
from app.domain.services.voice_pipeline.voicemail_detector import (
    detect_and_hang_up_voicemail,
)
from app.infrastructure.realtime.openai_realtime import RealtimeEvent


_VOICEMAIL_GREETING = "Please leave a message after the tone."


@pytest.mark.asyncio
async def test_interim_amd_never_flags_or_hangs_up_true_inbound_call():
    session = SimpleNamespace(_call_direction="inbound", turn_id=0)
    gateway = SimpleNamespace(hangup_call=AsyncMock(return_value=True))

    handled = await handle_machine_interim(
        "inbound-interim", session, _VOICEMAIL_GREETING, gateway
    )

    assert handled is False
    assert not hasattr(session, "_amd_voicemail")
    assert not hasattr(session, "_machine_screening")
    gateway.hangup_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_interim_amd_keeps_outbound_voicemail_hangup_behavior():
    session = SimpleNamespace(_call_direction="outbound", turn_id=0)
    gateway = SimpleNamespace(hangup_call=AsyncMock(return_value=True))

    handled = await handle_machine_interim(
        "outbound-interim", session, _VOICEMAIL_GREETING, gateway
    )

    assert handled is True
    assert session._amd_voicemail is True
    gateway.hangup_call.assert_awaited_once_with(
        "outbound-interim", reason="voicemail_detected"
    )


@pytest.mark.asyncio
async def test_interim_amd_keeps_outbound_adapter_fallback(monkeypatch):
    session = SimpleNamespace(_call_direction="outbound", turn_id=0)
    adapter = SimpleNamespace(hangup=AsyncMock())

    import app.domain.services.telephony.adapter_registry as adapter_registry

    monkeypatch.setattr(adapter_registry, "get_adapter", lambda: adapter)

    handled = await handle_machine_interim(
        "outbound-interim-fallback", session, _VOICEMAIL_GREETING
    )

    assert handled is True
    assert session._amd_voicemail is True
    adapter.hangup.assert_awaited_once_with("outbound-interim-fallback")


def _install_live_session(monkeypatch, *, direction: Direction):
    """Install the smallest lifecycle state used by final-transcript AMD."""

    voice_session = SimpleNamespace(
        call_session=SimpleNamespace(_call_direction=direction.value),
        config=SimpleNamespace(direction=direction),
    )
    adapter = SimpleNamespace(hangup=AsyncMock())
    state = SimpleNamespace(get_voice_session=lambda _call_id: voice_session)

    import app.domain.services.telephony.lifecycle as lifecycle
    import app.domain.services.telephony.adapter_registry as adapter_registry

    monkeypatch.setattr(lifecycle, "_state", lambda: state)
    monkeypatch.setattr(
        adapter_registry, "get_adapter", lambda: adapter
    )
    return voice_session, adapter


@pytest.mark.asyncio
async def test_final_voicemail_detector_never_hangs_up_true_inbound_call(monkeypatch):
    voice_session, adapter = _install_live_session(
        monkeypatch, direction=Direction.INBOUND
    )

    detected = await detect_and_hang_up_voicemail(
        "inbound-final", _VOICEMAIL_GREETING, 0
    )

    assert detected is False
    assert not hasattr(voice_session, "_amd_voicemail")
    assert not hasattr(voice_session.call_session, "_amd_voicemail")
    adapter.hangup.assert_not_awaited()


@pytest.mark.asyncio
async def test_final_voicemail_detector_keeps_outbound_behavior(monkeypatch):
    voice_session, adapter = _install_live_session(
        monkeypatch, direction=Direction.OUTBOUND
    )

    detected = await detect_and_hang_up_voicemail(
        "outbound-final", _VOICEMAIL_GREETING, 0
    )

    assert detected is True
    assert voice_session._amd_voicemail is True
    assert voice_session.call_session._amd_voicemail is True
    adapter.hangup.assert_awaited_once_with("outbound-final")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("direction", "should_detect"),
    [("inbound", False), ("outbound", True)],
)
async def test_realtime_bridge_runs_voicemail_detection_only_outbound(
    monkeypatch, direction, should_detect
):
    async def events():
        yield RealtimeEvent(
            kind="caller_transcript", text=_VOICEMAIL_GREETING, is_final=True
        )

    realtime_session = SimpleNamespace(events=events)
    detector = AsyncMock(return_value=True)

    import app.domain.services.voice_pipeline.voicemail_detector as detector_module

    monkeypatch.setattr(
        detector_module, "detect_and_hang_up_voicemail", detector
    )

    bridge = RealtimeBridge(
        call_id=f"{direction}-realtime",
        realtime_session=realtime_session,
        media_gateway=object(),
        internal_sample_rate=8000,
        call_direction=direction,
    )
    await bridge._pump_model_events()

    if should_detect:
        detector.assert_awaited_once_with(
            "outbound-realtime", _VOICEMAIL_GREETING, 0
        )
    else:
        detector.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("direction", "greet_on_start"),
    [(Direction.INBOUND, False), (Direction.OUTBOUND, True)],
)
async def test_realtime_orchestrator_pins_direction_on_session_and_bridge(
    direction, greet_on_start
):
    orchestrator = VoiceOrchestrator(db_client=None)
    config = VoiceSessionConfig(
        direction=direction,
        pipeline_mode="realtime",
        gateway_sample_rate=8000,
    )

    resolver = SimpleNamespace(resolve=AsyncMock(return_value="test-api-key"))
    realtime_session = SimpleNamespace(
        connect=AsyncMock(return_value=True), close=AsyncMock()
    )
    gateway = SimpleNamespace(
        _sample_rate=8000,
        set_barge_in_event=MagicMock(),
    )

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
            "app.services.scripts.realtime_instructions.build_realtime_instructions",
            return_value="test instructions",
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
        voice_session = await orchestrator._create_realtime_voice_session(
            config,
            call_id=f"{direction.value}-orchestrated",
            talklee_call_id="INAMD0000000000000001",
        )

    assert voice_session is not None
    assert voice_session.call_session._call_direction == direction.value
    assert bridge_cls.call_args.kwargs["call_direction"] == direction.value
    assert bridge_cls.call_args.kwargs["greet_on_start"] is greet_on_start
