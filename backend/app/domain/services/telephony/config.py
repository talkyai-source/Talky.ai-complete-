"""Configuration helpers for the telephony bridge.

Leaf module — no dependencies on other telephony submodules. Owns:
- env-driven first-speaker default
- session-config builder
- greeting builder
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from app.domain.services.telephony_session_config import (
    build_telephony_session_config,
    build_telephony_greeting,
)

logger = logging.getLogger(__name__)


def _outbound_first_speaker() -> str:
    """
    Who speaks first on an outbound (campaign) call after the callee answers.

    Returns "user" or "agent".  Default is "agent" — the estimation agent speaks
    an immediate greeting so the callee never hears dead silence after picking up.
    Set TELEPHONY_FIRST_SPEAKER=user to wait for the callee to speak first
    (useful for inbound-style testing).
    """
    val = (os.getenv("TELEPHONY_FIRST_SPEAKER") or "agent").strip().lower()
    return "user" if val == "user" else "agent"


def _build_telephony_session_config(
    gateway_type: str = "telephony",
    campaign=None,
    agent_name: Optional[str] = None,
):
    """
    Thin shim kept for call-site compatibility.
    All config logic lives in telephony_session_config.build_telephony_session_config().
    """
    return build_telephony_session_config(
        gateway_type=gateway_type,
        campaign=campaign,
        agent_name_override=agent_name,
    )


def _build_outbound_greeting(session) -> str:
    """
    Build the estimation agent's opening line from the session's agent_config.

    Delegates to telephony_session_config.build_telephony_greeting() so the
    greeting and the system prompt always reference the same agent_name and
    company_name — both set in build_telephony_session_config().
    """
    agent_config = getattr(session, "agent_config", None)
    agent_name = (
        getattr(agent_config, "agent_name", None) if agent_config else None
    ) or "your assistant"
    company = (
        getattr(agent_config, "company_name", None) if agent_config else None
    ) or "All States Estimation"
    return build_telephony_greeting(agent_name, company)
