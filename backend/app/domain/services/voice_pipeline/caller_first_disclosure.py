"""Speak the recording notice on caller-first outbound calls.

Agent-first calls speak it as the first audio of the greeting
(agent_first._send_outbound_greeting). Caller-first calls used to get it the
same way, right after the caller's first "Hello?", through the instant opener;
when that was disconnected on 2026-10-07 the notice went with it, so a
two-party tenant's recordings were all discarded
(recording_retention_suppressed ... tenant_policy_two_party_announce).

Owner decision 2026-10-21: play the notice again as before, with no scripted
greeting: the notice, then the agent's normal reply to the caller.
_speak_recording_disclosure keeps its own rules: once per call, the policy
decides whether a notice is owed, and an interrupted or unproven notice still
discards the recording.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def speak_disclosure_before_first_reply(session) -> None:
    if getattr(session, "_caller_first_disclosure_done", False):
        return
    session._caller_first_disclosure_done = True
    voice_session = getattr(session, "_voice_session_ref", None)
    if voice_session is None:
        return  # no telephony session (browser test agent): nothing to record
    if getattr(voice_session, "_recording_disclosure_attempted", False):
        return  # the inbound gate already handled it
    from app.domain.services.telephony.modes.agent_first import _speak_recording_disclosure

    session._disclosure_in_flight = True
    try:
        await _speak_recording_disclosure(voice_session)
    finally:
        session._disclosure_in_flight = False
