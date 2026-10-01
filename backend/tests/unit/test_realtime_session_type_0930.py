"""Browser tests 980a2caa and 0457b4b9 (2026-09-30): Realtime died after the greeting.

The first mid-call session.update (the live-state refresh after the caller's
first turn) had no ``session.type``. The GA Realtime API answered
"Missing required parameter: 'session.type'", the bridge treated that error as
fatal and stopped, and the browser kept sending audio into nothing for 30-60 s
(stt_input_queue_overrun). Every session.update must carry the type, and a
rejected mid-call instructions must be surfaced to the owning lifecycle.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.realtime.openai import OpenAIRealtimeSession


class _RecordingWS:
    def __init__(self):
        self.sent = []

    async def send(self, payload):
        self.sent.append(json.loads(payload))


def _session():
    from app.realtime.prompts import RealtimePersona, build_realtime_instructions

    s = OpenAIRealtimeSession(
        api_key="sk-test",
        instructions=build_realtime_instructions(RealtimePersona()),
    )
    s._ws = _RecordingWS()
    return s


@pytest.mark.asyncio
async def test_the_live_state_update_carries_session_type():
    from app.domain.services.voice_pipeline.live_structured_state import (
        IdentityEvidence,
        LiveConversationState,
        reduce_live_state,
        render_live_state_block,
    )

    s = _session()
    await s.update_live_state(
        render_live_state_block(
            reduce_live_state(LiveConversationState(), IdentityEvidence(introduced=True))
        )
    )
    updates = [m for m in s._ws.sent if m["type"] == "session.update"]
    assert updates and all(m["session"].get("type") == "realtime" for m in updates)


@pytest.mark.asyncio
async def test_the_contact_directive_update_carries_session_type():
    s = _session()
    await s.interrupt_with_text("Ask for the caller's email address now.")
    updates = [m for m in s._ws.sent if m["type"] == "session.update"]
    assert updates and all(m["session"].get("type") == "realtime" for m in updates)


@pytest.mark.asyncio
async def test_a_rejected_session_setting_reaches_the_owning_lifecycle():
    s = _session()
    offered = []
    s._offer_event = offered.append
    await s._handle_server_event({
        "type": "error",
        "error": {
            "type": "invalid_request_error",
            "code": "missing_required_parameter",
            "message": "Missing required parameter: 'session.type'.",
            "param": "session.type",
        },
    })
    assert [e for e in offered if getattr(e, "kind", None) == "error"]


@pytest.mark.asyncio
async def test_other_provider_errors_still_reach_the_bridge():
    s = _session()
    offered = []
    s._offer_event = offered.append
    await s._handle_server_event({
        "type": "error",
        "error": {"type": "server_error", "message": "The server had an error."},
    })
    assert [e for e in offered if getattr(e, "kind", None) == "error"]


def test_the_browser_test_ends_visibly_when_realtime_stops():
    """Wiring guard: the Test-agent socket waits on the realtime task too and
    tells the browser when it ends."""
    src = Path(__file__).resolve().parents[2].joinpath(
        "app", "api", "v1", "endpoints", "campaign_test_ws.py"
    ).read_text(encoding="utf-8")
    assert "waiters.add(realtime_task)" in src
    assert '"code": "realtime_ended"' in src


# ── the Realtime agent runs the campaign it is calling for ───────────────

_SCRIPT = (
    'ROLE You are Alex, an AI voice assistant helping Azian with Dojo card '
    'payments. OPENING Say: "Hi, is that [First Name]?" WAIT.'
)


def _realtime_cfg(script_config, **kw):
    from app.domain.models.ai_config import AIProviderConfig
    from app.domain.services.telephony_session_config import build_telephony_session_config
    from app.domain.services.voice_orchestrator import Direction

    campaign = {
        "id": "c-dojo",
        "tenant_id": "11111111-1111-4111-8111-111111111111",
        "script_config": script_config,
    }
    return build_telephony_session_config(
        gateway_type="browser",
        campaign=campaign,
        direction=Direction.OUTBOUND,
        ai_config_override=AIProviderConfig(pipeline_mode="realtime", realtime_voice="ash"),
        **kw,
    )


def test_a_campaign_without_a_realtime_prompt_runs_on_its_own_script():
    cfg = _realtime_cfg({
        "company_name": "Dojo", "agent_names": ["Alex"], "persona_type": "lead_gen",
        "additional_instructions": _SCRIPT,
    })
    assert cfg.pipeline_mode == "realtime"
    assert "helping Azian" in cfg.system_prompt
    assert "Understand the business need" in cfg.system_prompt  # sales persona
    assert "No name is on file" in cfg.system_prompt


def test_a_realtime_specific_prompt_still_wins():
    cfg = _realtime_cfg({
        "company_name": "Dojo", "agent_names": ["Alex"], "persona_type": "lead_gen",
        "additional_instructions": _SCRIPT,
        "realtime_prompt": {"persona": "support", "instructions": "Only talk about refunds."},
    })
    assert "Only talk about refunds." in cfg.system_prompt
    assert "helping Azian" not in cfg.system_prompt


def test_with_a_name_on_file_realtime_greets_them_by_it():
    cfg = _realtime_cfg(
        {"company_name": "Dojo", "agent_names": ["Alex"], "additional_instructions": _SCRIPT},
        lead_first_name="Uzair", lead_last_name="Khan",
    )
    assert "You are calling Uzair Khan" in cfg.system_prompt
    assert "No name is on file" not in cfg.system_prompt


# ── browser tests cb1b28c3 / 94f47f14 (2026-09-30, after the first fix) ─────

def test_gemini_3_8_is_sent_its_lowest_supported_thinking_level():
    """cb1b28c3: every turn got 400 "Thinking level MINIMAL is not supported
    for this model" and failed over to Groq."""
    from app.infrastructure.llm.gemini import GeminiLLMProvider as G

    assert str(G._build_thinking_config("gemini-3.8-flash", 0).thinking_level).lower().endswith("low")
    assert str(G._build_thinking_config("gemini-3.6-flash", 0).thinking_level).lower().endswith("minimal")


def test_account_wide_realtime_notes_are_added_to_the_campaign_script():
    """94f47f14: the account note "be precise and specific and to the point"
    replaced Dojo-PC's whole script."""
    from app.domain.models.ai_config import AIProviderConfig
    from app.domain.services.telephony_session_config import build_telephony_session_config
    from app.domain.services.voice_orchestrator import Direction

    cfg = build_telephony_session_config(
        gateway_type="browser",
        campaign={"id": "c", "tenant_id": "11111111-1111-4111-8111-111111111111",
                  "script_config": {"company_name": "Dojo", "agent_names": ["Alex"],
                                    "additional_instructions": _SCRIPT}},
        direction=Direction.OUTBOUND,
        ai_config_override=AIProviderConfig(
            pipeline_mode="realtime", realtime_voice="ash",
            realtime_settings={"prompt": {"persona": "sales",
                                          "instructions": "be precise and specific and to the point"}},
        ),
    )
    assert "helping Azian" in cfg.system_prompt
    assert "be precise and specific and to the point" in cfg.system_prompt


@pytest.mark.asyncio
async def test_an_unplayable_reply_is_withheld_not_a_call_ending_error():
    s = _session()
    offered = []
    s._offer_event = offered.append
    s._playout.reset("resp_1")
    s._playout.add_audio({"response_id": "resp_1", "item_id": "i1"}, b"\x7f" * 160)
    await s._handle_server_event({"type": "response.done",
                                  "response": {"id": "resp_1", "status": "completed", "output": []}})
    kinds = [getattr(e, "kind", None) for e in offered]
    assert "generation_incomplete" in kinds
    assert "error" not in kinds


@pytest.mark.asyncio
async def test_the_bridge_replaces_one_withheld_reply_and_ends_only_on_two():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from app.realtime.openai import RealtimeEvent
    from app.realtime.bridge import RealtimeBridge

    async def events():
        for kind in ("response_unplayable", "generation_incomplete"):
            yield RealtimeEvent(kind=kind, raw={"response": {"id": "r"}})

    rt = SimpleNamespace(events=events, repair_unspoken_response=AsyncMock())
    gw = SimpleNamespace(clear_output_buffer=AsyncMock(), send_audio=AsyncMock())
    bridge = RealtimeBridge(call_id="synthetic", realtime_session=rt, media_gateway=gw)
    await bridge._pump_model_events()
    rt.repair_unspoken_response.assert_awaited_once()
    gw.send_audio.assert_not_awaited()
    assert "one shorter retry" in bridge._failure_reason
