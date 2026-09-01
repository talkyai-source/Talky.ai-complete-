"""Call direction and opening mode are two fields, not one.

Since 2026-05-18 ``Direction.from_first_speaker("user")`` returned INBOUND for
an outbound dialer call whose owner merely chose "callee speaks first". The
docstring called it a bridge "until per-campaign direction lands in the UI".
It never did, and every later consumer of ``direction`` read the value as
*carrier inbound*: realtime instructions told the agent "the caller contacted
the company", AMD and voicemail detection switched off, and — after
2026-08-30 — ``recording._is_true_inbound_session`` discarded the recording
because the true-inbound admission path had never set ``_recording_allowed``.

These tests pin the split: ``direction`` says who originated the call,
``opening_mode`` says who talks first.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.domain.services.voice_orchestrator import (
    Direction,
    VoiceOrchestrator,
    VoiceSessionConfig,
    opening_mode_from_first_speaker,
)
from app.services.scripts.prompts.direction import INBOUND_DIRECTIVE_SENTINEL


_CAMPAIGN = {
    "id": "c-1",
    "tenant_id": "11111111-1111-4111-8111-111111111111",
    "script_config": {"company_name": "Acme", "agent_names": ["Alex"]},
}


# ── the helper that replaces Direction.from_first_speaker on outbound paths ──

@pytest.mark.parametrize(
    ("first_speaker", "expected"),
    [
        ("user", "callee_first"),
        ("USER ", "callee_first"),
        ("agent", "agent_first"),
        (None, "agent_first"),
        ("", "agent_first"),
        ("garbage", "agent_first"),
    ],
)
def test_opening_mode_from_first_speaker(first_speaker, expected):
    assert opening_mode_from_first_speaker(first_speaker) == expected


# ── the session-config builder ──────────────────────────────────────────────

def _callee_first_outbound_config(**overrides):
    from app.domain.services.telephony_session_config import (
        build_telephony_session_config,
    )

    return build_telephony_session_config(
        gateway_type="telephony",
        campaign=_CAMPAIGN,
        direction=Direction.OUTBOUND,
        opening_mode="callee_first",
        **overrides,
    )


def test_callee_first_outbound_config_keeps_direction_outbound():
    cfg = _callee_first_outbound_config()
    assert cfg.direction == Direction.OUTBOUND
    assert cfg.opening_mode == "callee_first"


def test_callee_first_outbound_config_still_carries_the_callee_first_directive():
    """Framing follows opening_mode, not direction: the agent waits for the
    callee, then leads with its own introduction."""
    cfg = _callee_first_outbound_config()
    assert cfg.system_prompt.startswith(INBOUND_DIRECTIVE_SENTINEL)


def test_agent_first_outbound_config_has_no_callee_first_directive():
    from app.domain.services.telephony_session_config import (
        build_telephony_session_config,
    )

    cfg = build_telephony_session_config(
        gateway_type="telephony",
        campaign=_CAMPAIGN,
        direction=Direction.OUTBOUND,
        opening_mode="agent_first",
    )
    assert cfg.opening_mode == "agent_first"
    assert INBOUND_DIRECTIVE_SENTINEL not in cfg.system_prompt


def test_callee_first_outbound_is_not_a_true_inbound_session_for_recording():
    """The 2026-08-30 recording gate discards anything it thinks is carrier
    inbound unless admission granted ``_recording_allowed``. A callee-first
    outbound call never goes through admission, so it must not look inbound."""
    from app.domain.services.telephony.recording import _is_true_inbound_session

    cfg = _callee_first_outbound_config()
    session = SimpleNamespace(config=cfg, call_session=SimpleNamespace())
    assert _is_true_inbound_session(session) is False


def test_callee_first_outbound_realtime_persona_is_outbound():
    cfg = _callee_first_outbound_config()
    persona = VoiceOrchestrator._build_realtime_persona(cfg)
    assert persona.call_direction == "outbound"


# ── realtime greet_on_start follows opening_mode ────────────────────────────

@pytest.mark.parametrize(
    ("direction", "opening_mode", "pinned", "expected"),
    [
        (Direction.OUTBOUND, "callee_first", None, False),
        (Direction.OUTBOUND, "agent_first", None, True),
        # Explicit admission pin always wins.
        (Direction.OUTBOUND, "callee_first", True, True),
        # Legacy callers that set neither keep the direction-derived default.
        (Direction.OUTBOUND, None, None, True),
        (Direction.INBOUND, None, None, False),
    ],
)
def test_realtime_greet_on_start_resolution(direction, opening_mode, pinned, expected):
    cfg = VoiceSessionConfig(
        direction=direction,
        opening_mode=opening_mode,
        realtime_greet_on_start=pinned,
        pipeline_mode="realtime",
    )
    assert VoiceOrchestrator._resolve_realtime_greet_on_start(cfg) is expected


@pytest.mark.asyncio
async def test_realtime_callee_first_outbound_bridge_is_outbound_and_waits():
    orchestrator = VoiceOrchestrator(db_client=None)
    config = VoiceSessionConfig(
        direction=Direction.OUTBOUND,
        opening_mode="callee_first",
        pipeline_mode="realtime",
        gateway_sample_rate=8000,
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
            orchestrator, "_create_media_gateway", new=AsyncMock(return_value=gateway)
        ),
    ):
        voice_session = await orchestrator._create_realtime_voice_session(
            config, call_id="callee-first-outbound", talklee_call_id="T0000000000000000001",
        )

    assert voice_session is not None
    assert voice_session.call_session._call_direction == "outbound"
    assert bridge_cls.call_args.kwargs["call_direction"] == "outbound"
    assert bridge_cls.call_args.kwargs["greet_on_start"] is False


# ── the two call sites that used to derive direction from first_speaker ─────

@pytest.mark.asyncio
async def test_prewarm_callee_first_builds_an_outbound_config(monkeypatch):
    from app.domain.services.telephony import prewarm

    captured: dict = {}

    def fake_build(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(stt_eot_timeout_ms=500)

    class _Orch:
        async def create_voice_session(self, config):
            raise RuntimeError("stop after config build")

        async def end_session(self, _s):
            return None

    async def _campaign(_cid):
        return dict(_CAMPAIGN)

    monkeypatch.setattr(prewarm, "_build_telephony_session_config", fake_build)
    monkeypatch.setattr(prewarm, "_get_orchestrator", lambda: _Orch())
    monkeypatch.setattr(prewarm, "_lookup_campaign_row", _campaign)

    from app.domain.services import voice_tuning, tenant_ai_config_resolver

    class _Resolver:
        async def for_tenant_async(self, _tid):
            return None

    monkeypatch.setattr(voice_tuning, "get_voice_tuning_resolver", lambda: _Resolver())
    monkeypatch.setattr(
        tenant_ai_config_resolver, "get_tenant_ai_config_resolver", lambda: _Resolver()
    )

    result = await prewarm.prepare_prewarmed_session(
        first_speaker="user", campaign_id="c-1", agent_name=None, container=None,
    )

    assert result.session is None  # we stopped it on purpose
    assert captured["direction"] == Direction.OUTBOUND
    assert captured["opening_mode"] == "callee_first"
