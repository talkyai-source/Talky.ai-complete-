"""Tests for the agent-first greeting handler
(telephony/modes/agent_first.py).

Covers the introduction-flag fix: after the agent-first greeting is
delivered and committed to conversation_history, ``session._has_introduced``
must be flipped True — the same flag turn_runner sets after the first
LLM-generated reply, and the flag live_state.py reads to decide whether the
per-turn LIVE STATE block tells the model it has already introduced itself
(see prompts/live_state.py). Without this, agent-first calls could be told
on turn 2 that they hadn't introduced themselves yet, inviting a duplicate
introduction.
"""
from __future__ import annotations

import types

import pytest

from app.domain.models.conversation import MessageRole
from app.domain.services.telephony.modes.agent_first import _send_outbound_greeting


class _FakeMediaGateway:
    def __init__(self):
        self.sent = []

    async def send_audio(self, call_id, chunk):
        self.sent.append(chunk)

    async def flush_tts_buffer(self, call_id):
        pass

    async def clear_output_buffer(self, call_id):
        pass


class _FakePipeline:
    def clear_barge_in_event(self, session):
        pass


def _make_voice_session(**overrides):
    session = types.SimpleNamespace(
        llm_active=False,
        tts_active=False,
        barge_in_event=None,
        conversation_history=[],
    )
    voice_session = types.SimpleNamespace(
        call_id="call-123456789",
        call_session=session,
        pipeline=_FakePipeline(),
        media_gateway=_FakeMediaGateway(),
        _presynth_greeting_audio=[b"\x00\x01" * 10],
        _presynth_greeting_text=(
            "Hi, this is Sarah calling from Acme — do you have a quick minute?"
        ),
    )
    for key, value in overrides.items():
        setattr(voice_session, key, value)
    return voice_session, session


@pytest.mark.asyncio
async def test_agent_first_greeting_sets_has_introduced_flag():
    voice_session, session = _make_voice_session()

    await _send_outbound_greeting(voice_session)

    assert getattr(session, "_has_introduced", False) is True
    assert len(session.conversation_history) == 1
    assert session.conversation_history[0].role == MessageRole.ASSISTANT


@pytest.mark.asyncio
async def test_live_state_does_not_reinvite_intro_after_agent_first_greeting():
    """End-to-end proof: after the greeting handler runs, feeding the flag it
    set into build_live_state_block produces the anti-re-introduction line,
    not the "give your opening" line — so a second intro can't be prompted."""
    from app.services.scripts.prompts.live_state import build_live_state_block

    voice_session, session = _make_voice_session()
    await _send_outbound_greeting(voice_session)

    block = build_live_state_block(
        agent_name="Sarah",
        company_name="Acme",
        has_introduced=getattr(session, "_has_introduced", False),
    )
    assert "ALREADY introduced" in block
    assert "Do NOT introduce yourself again" in block
    assert "have not introduced yourself" not in block


@pytest.mark.asyncio
async def test_has_introduced_not_set_when_greeting_raises_before_history():
    """If the greeting path blows up before anything is committed to
    history, the flag must NOT be set — there is nothing to avoid
    re-introducing."""
    voice_session, session = _make_voice_session()

    class _BoomGateway(_FakeMediaGateway):
        async def send_audio(self, call_id, chunk):
            raise RuntimeError("boom")

    voice_session.media_gateway = _BoomGateway()

    await _send_outbound_greeting(voice_session)

    assert getattr(session, "_has_introduced", False) is False
    assert session.conversation_history == []
