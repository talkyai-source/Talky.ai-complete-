"""Outbound recording-retention window (2026-09-24, call 6e0e221b).

A telephony session's recording gate defaults OPEN at media start — only a
TRUE-inbound session starts closed (see telephony_media_gateway.py's
TelephonySession.recording_enabled comment and prepare_inbound_recording in
modes/caller_first.py). On a callee-first (first_speaker="user") outbound
call, `_speak_recording_disclosure` does not run until the callee's own
pickup has been transcribed — several seconds after answer — so on the
unfixed code every byte of caller/agent audio in that gap was retained.

Call 6e0e221b: answered 18:09:18.094, the notice did not start until
18:09:26.575 ("recording_disclosure_speaking"), and the saved WAV kept the
full 27.4s including ~8.5s of the callee's "Hello?" and two agent re-greet
nudges from before the notice ever played.

These tests drive the REAL `TelephonyMediaGateway` (the unit that owns the
recording buffers) through `agent_first._speak_recording_disclosure` (the
unit that was fixed to reset the gate), so the gate/buffer interaction is
exercised end to end rather than through a stub of either side.
"""
from __future__ import annotations

import types
from unittest.mock import AsyncMock, patch

import pytest

from app.domain.services import recording_policy_service as rps
from app.domain.services.recording_policy_service import RecordingDecision
from app.domain.services.telephony.modes import agent_first
from app.infrastructure.telephony.telephony_media_gateway import TelephonyMediaGateway

TENANT = "22222222-2222-2222-2222-222222222222"

_ANNOUNCE = RecordingDecision(
    should_record=True,
    announcement_required=True,
    announcement_text="This call may be recorded for quality and training purposes.",
    opt_out_dtmf_digit=None,
    retention_days=90,
    reason="tenant_policy_two_party_announce",
)

_NO_ANNOUNCE = RecordingDecision(
    should_record=True,
    announcement_required=False,
    announcement_text=None,
    opt_out_dtmf_digit=None,
    retention_days=90,
    reason="tenant_policy_one_party",
)


@pytest.fixture(autouse=True)
def _clean_ledger():
    """The disclosure ledger is module-global; keep tests independent."""
    rps._DISCLOSURE_LEDGER.clear()
    yield
    rps._DISCLOSURE_LEDGER.clear()


class _NoticePipeline:
    """`synthesize_and_send_audio` drives the notice text through the REAL
    media gateway's `send_audio`, so the notice's own bytes land in the
    gateway's `tts_recording_buffer` exactly as production does. Real-time
    TTS pacing (`asyncio.sleep`) is patched out — it throttles delivery to
    the C++ gateway and has no bearing on the recording-buffer accounting
    under test here (see test_telephony_media_gateway.py for the same
    pattern)."""

    def __init__(self, gateway, call_id, chunk=b"\x00\x00" * 160):
        self._gateway = gateway
        self._call_id = call_id
        self._chunk = chunk

    def clear_barge_in_event(self, session):
        pass

    async def synthesize_and_send_audio(self, session, text, websocket=None):
        with patch("asyncio.sleep", new=AsyncMock(return_value=None)):
            await self._gateway.send_audio(self._call_id, self._chunk)
        return False  # not interrupted


def _patched_policy(decision):
    container = types.SimpleNamespace(is_initialized=True, db_pool=object())
    return (
        patch("app.core.container.get_container", return_value=container),
        patch.object(
            rps.RecordingPolicyService, "decide", new=AsyncMock(return_value=decision)
        ),
    )


def _voice_session(gateway, call_id, pipeline):
    session = types.SimpleNamespace(conversation_history=[])
    return types.SimpleNamespace(
        call_id=call_id,
        call_session=session,
        pipeline=pipeline,
        media_gateway=gateway,
        _dialer_tenant_id=TENANT,
    )


@pytest.mark.asyncio
async def test_callee_first_outbound_purges_pre_notice_audio_when_notice_starts():
    """Reproduces 6e0e221b: pre-notice caller audio must not survive into
    the retained recording, and the notice itself must be captured."""
    gateway = TelephonyMediaGateway()
    await gateway.initialize({"sample_rate": 8000, "tts_source_format": "s16le"})
    call_id = "callee-first-call"
    await gateway.on_call_started(call_id, {"adapter": AsyncMock(), "pbx_call_id": "pbx-1"})
    session = gateway._sessions[call_id]
    assert session.recording_enabled is True, (
        "sanity: outbound sessions default to an OPEN gate at media start"
    )

    # The callee's pickup ("Hello?") plus the agent's re-greet nudges,
    # arriving in the seconds BEFORE the notice starts (first_speaker=user).
    await gateway.on_audio_received(call_id, b"\xff" * 1600)
    assert session.recording_buffer_bytes > 0, (
        "harness sanity: pre-notice caller audio actually landed in the buffer"
    )

    pipeline = _NoticePipeline(gateway, call_id)
    voice_session = _voice_session(gateway, call_id, pipeline)
    p_container, p_policy = _patched_policy(_ANNOUNCE)
    with p_container, p_policy:
        await agent_first._speak_recording_disclosure(voice_session)

    assert session.recording_buffer_bytes == 0, (
        "audio from before the notice must not survive into the retained "
        "recording"
    )
    assert session.tts_recording_buffer_bytes > 0, (
        "the notice itself must be captured as proof the disclosure was given"
    )
    assert session.recording_enabled is True


@pytest.mark.asyncio
async def test_agent_first_outbound_notice_start_is_not_lost_by_the_gate_reset():
    """8b3176ca: the notice started 21ms after the gateway session was
    created (no pre-notice audio at all). The reset-then-open sequence must
    not cost agent-first calls a single byte of the notice, since the reset
    completes synchronously (no `await` in between) before any TTS audio is
    sent."""
    chunk = b"\x00\x01" * 400

    # Control: the identical chunk through send_audio with no prior gate
    # activity at all — the ground truth for "nothing lost".
    control_gateway = TelephonyMediaGateway()
    await control_gateway.initialize({"sample_rate": 8000, "tts_source_format": "s16le"})
    await control_gateway.on_call_started("control", {"adapter": AsyncMock()})
    with patch("asyncio.sleep", new=AsyncMock(return_value=None)):
        await control_gateway.send_audio("control", chunk)
    control_bytes = control_gateway._sessions["control"].tts_recording_buffer_bytes
    assert control_bytes > 0, "harness sanity: the control chunk actually recorded"

    gateway = TelephonyMediaGateway()
    await gateway.initialize({"sample_rate": 8000, "tts_source_format": "s16le"})
    call_id = "agent-first-call"
    await gateway.on_call_started(call_id, {"adapter": AsyncMock()})
    session = gateway._sessions[call_id]

    pipeline = _NoticePipeline(gateway, call_id, chunk=chunk)
    voice_session = _voice_session(gateway, call_id, pipeline)
    p_container, p_policy = _patched_policy(_ANNOUNCE)
    with p_container, p_policy:
        await agent_first._speak_recording_disclosure(voice_session)

    assert session.tts_recording_buffer_bytes == control_bytes, (
        "the gate reset must not drop any of the notice's own audio"
    )


@pytest.mark.asyncio
async def test_one_party_outbound_never_touches_the_gate():
    """Policies that need no announcement must keep recording from the true
    start exactly as before — the new reset must not fire at all."""
    gateway = TelephonyMediaGateway()
    await gateway.initialize({"sample_rate": 8000})
    call_id = "one-party-call"
    await gateway.on_call_started(call_id, {"adapter": AsyncMock()})
    session = gateway._sessions[call_id]
    await gateway.on_audio_received(call_id, b"\xff" * 800)
    buffered_before = session.recording_buffer_bytes
    assert buffered_before > 0

    pipeline = _NoticePipeline(gateway, call_id)
    voice_session = _voice_session(gateway, call_id, pipeline)
    p_container, p_policy = _patched_policy(_NO_ANNOUNCE)
    with p_container, p_policy:
        await agent_first._speak_recording_disclosure(voice_session)

    assert session.recording_enabled is True
    assert session.recording_buffer_bytes == buffered_before, (
        "a one-party policy must keep recording from the true start — "
        "nothing before it may be purged"
    )


@pytest.mark.asyncio
async def test_true_inbound_session_is_not_double_driven_by_the_new_gate_reset():
    """prepare_inbound_recording (modes/caller_first.py, out of fence) already
    owns the inbound gate: it closes it before calling this shared helper and
    only reopens it once disclosure_spoken AND the live switch are both
    confirmed, AFTER this function returns. The new callee-first reset must
    recognise a true-inbound session (voice_session._inbound_admission) and
    skip — otherwise it would reopen the gate mid-notice before the inbound
    path has actually decided to allow recording at all."""
    gateway = TelephonyMediaGateway()
    await gateway.initialize({"sample_rate": 8000, "tts_source_format": "s16le"})
    call_id = "inbound-call"
    await gateway.on_call_started(call_id, {"adapter": AsyncMock()})
    session = gateway._sessions[call_id]
    # Mirrors prepare_inbound_recording's own first action.
    assert gateway.set_recording_enabled(call_id, False) is True

    pipeline = _NoticePipeline(gateway, call_id)
    voice_session = _voice_session(gateway, call_id, pipeline)
    voice_session._inbound_admission = {"config_snapshot": {}}
    p_container, p_policy = _patched_policy(_ANNOUNCE)
    with p_container, p_policy:
        await agent_first._speak_recording_disclosure(voice_session)

    assert session.recording_enabled is False, (
        "inbound's own gate decision must not be pre-empted by the "
        "outbound reset"
    )
    assert session.tts_recording_buffer_bytes == 0
