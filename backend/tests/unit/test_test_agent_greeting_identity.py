"""The Test agent must resolve its opener from the same inputs as a live call.

Every company_name_fallback warning in the 30 days to 2026-09-23 came from the
Test agent -- never from a real phone call. The phone path builds the greeting
from ``voice_session.call_session``; the Test agent passed the VoiceSession
wrapper, which carries no agent_config, persona_type or call_reason.

Today's outbound openers are bare hellos, so the difference was not audible.
But a test harness that resolves its opener from different inputs than
production is not testing production, and its warnings made the logs report a
misconfiguration that did not exist on real calls.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.domain.services.telephony.config import (
    _build_outbound_greeting,
    _resolve_greeting_context,
)
from app.domain.services.telephony_session_config import TELEPHONY_COMPANY_NAME


def _call_session():
    return SimpleNamespace(
        agent_config=SimpleNamespace(
            agent_name="Sarah", company_name="Allstate Estimation UK"
        ),
        config=None,
        tenant_id="t",
        campaign_id="c",
    )


def test_the_wrapper_has_no_identity_which_is_why_the_bug_happened():
    wrapper = SimpleNamespace(agent_config=None, call_session=_call_session())
    agent, company = _resolve_greeting_context(wrapper)
    assert company == TELEPHONY_COMPANY_NAME
    assert agent == "your assistant"


def test_the_call_session_carries_the_real_identity():
    agent, company = _resolve_greeting_context(_call_session())
    assert (agent, company) == ("Sarah", "Allstate Estimation UK")


def test_the_call_session_path_does_not_log_a_false_fallback(caplog):
    import logging

    with caplog.at_level(logging.WARNING):
        _build_outbound_greeting(_call_session())
    assert "company_name_fallback" not in caplog.text


def test_the_test_agent_builds_its_greeting_the_same_way_the_phone_path_does():
    root = Path(__file__).resolve().parents[2] / "app"
    test_ws = (root / "api" / "v1" / "endpoints" / "campaign_test_ws.py").read_text(
        encoding="utf-8"
    )
    phone = (root / "domain" / "services" / "telephony" / "lifecycle.py").read_text(
        encoding="utf-8"
    )
    assert "_build_outbound_greeting(voice_session.call_session)" in phone
    assert "_build_outbound_greeting(voice_session)" not in test_ws
    assert 'getattr(voice_session, "call_session", None) or voice_session' in test_ws
