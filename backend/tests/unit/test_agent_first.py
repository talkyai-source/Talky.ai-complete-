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

2026-08-11 (opener redesign): the OTHER half of that same flag now matters
just as much. The default outbound opener is no longer a monologue — it is a
bare pickup greeting ("Hi there.", "Hello?") with no name, no company, and no
reason in it, played immediately on answer; identity + reason move to the
model's own first generated reply, once the callee has actually spoken (see
telephony_session_config.build_telephony_greeting and the persona STAGE 1 /
OPENING blocks). So ``_has_introduced`` must now stay FALSE after a bare
greeting plays — flipping it True here (the old unconditional behaviour)
would tell LIVE STATE the agent already introduced itself when the callee
has heard nothing but "Hi there.", and the agent would never give its name
or the reason for the call on any turn of the call.
"""
from __future__ import annotations

import types

import pytest

from app.domain.models.conversation import MessageRole
from app.domain.services.telephony.modes.agent_first import (
    _has_real_opener_content,
    _looks_like_bare_pickup_greeting,
    _send_outbound_greeting,
)


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


# ── 2026-08-11 opener redesign: bare pickup greeting ──────────────────────


@pytest.mark.asyncio
async def test_bare_pickup_greeting_does_not_set_has_introduced_flag():
    """The new default outbound opener ("Hi there.") carries no identity, so
    the flag that tells LIVE STATE "you already introduced yourself" must
    stay False — otherwise the agent never introduces itself on any turn."""
    voice_session, session = _make_voice_session(
        _presynth_greeting_text="Hi there.",
    )

    await _send_outbound_greeting(voice_session)

    assert getattr(session, "_has_introduced", False) is False
    assert len(session.conversation_history) == 1
    assert session.conversation_history[0].content == "Hi there."


@pytest.mark.asyncio
async def test_bare_pickup_greeting_live_state_still_invites_introduction():
    """Mirrors test_live_state_does_not_reinvite_intro_after_agent_first_
    greeting above, for the bare-greeting case: LIVE STATE must tell the
    model it still needs to give its opening on the turn after the callee
    replies, not that it already introduced itself."""
    from app.services.scripts.prompts.live_state import build_live_state_block

    voice_session, session = _make_voice_session(
        _presynth_greeting_text="Hello?",
    )
    await _send_outbound_greeting(voice_session)

    block = build_live_state_block(
        agent_name="Sarah",
        company_name="Acme",
        has_introduced=getattr(session, "_has_introduced", False),
    )
    assert "have not introduced yourself" in block
    assert "ALREADY introduced" not in block


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Hi there.", True),
        ("Hello?", True),
        ("Hi, hello.", True),
        ("Hi, this is Sarah calling from Acme — do you have a minute?", False),
        ("", False),
        (None, False),
        ("   ", False),
    ],
)
def test_looks_like_bare_pickup_greeting(text, expected):
    assert _looks_like_bare_pickup_greeting(text) is expected


class TestHasRealOpenerContentAcceptsBareGreetings:
    """2026-08-11: this guard used to reject exactly the text the redesign
    now speaks as the DEFAULT opener on every outbound call. If it still
    rejected "Hello?" / "Hi there.", the presynth-rejection branch in
    _send_outbound_greeting would fire on every single call and silently
    replace the new bare greeting with the old full-monologue fallback —
    undoing the redesign while looking like nothing changed."""

    @pytest.mark.parametrize(
        "text",
        ["Hi there.", "Hello?", "Hi, hello.", "Hey there.", "Hi", "hello"],
    )
    def test_deliberate_short_greetings_are_accepted(self, text):
        assert _has_real_opener_content(text) is True

    @pytest.mark.parametrize(
        "text",
        [None, "", "   ", "\t\n", "...", "?", "!!", "--- ---"],
    )
    def test_empty_or_punctuation_only_text_is_still_rejected(self, text):
        """The bug this guard exists to catch — the TTS pipeline had
        NOTHING to say — must still be caught. Only the "a single word is
        automatically broken" heuristic was removed."""
        assert _has_real_opener_content(text) is False
