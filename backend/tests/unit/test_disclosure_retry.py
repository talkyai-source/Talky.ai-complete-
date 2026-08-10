"""The recording notice is NOT re-delivered when the callee talks over it.

WHY THIS EXISTS (production measurement, 2026-07-31, REVERSED 2026-08-11)
---------------------------------------------------------------------------
The spoken disclosure is the FIRST audio on the call, which puts a ~4-second
sentence in the exact moment people always speak: the two seconds after they
pick up. The original implementation (pre-2026-07-31) treated one
interruption as final — the whole call's recording was suppressed.

A 2026-07-31 change tried to recover the lost recordings by RETRYING the
notice — restarting it from the top — once the callee stopped talking.
Measured on the first 7 production calls after that shipped:

    recording_disclosure_spoken        4
    recording_disclosure_interrupted   3     <- 43% of calls lost their recording

That looked like progress, but the retry itself was never actually observed
recovering a call in practice: instead, two later production traces showed
``recording_disclosure_interrupted_retrying attempt=1/2`` followed by the
caller hanging up ~2s after the restart played. Restarting from the top means
the callee hears "This call may be recorded..." begin, get interrupted, and
then BEGIN AGAIN — which reads as a broken robot, not a considerate
re-delivery, and it talks over whatever they were saying a second time.

The fix reverts to a single attempt, fail-closed: one attempt, and if it's
interrupted we stop trying rather than restart over the caller. This can only
ever REMOVE a retry; it cannot cause a recording to be kept that would not
have been kept before. `test_a_single_interrupted_attempt_fails_closed` is the
load-bearing test.
"""
from __future__ import annotations

import types
from unittest.mock import AsyncMock, patch

import pytest

from app.domain.services.telephony.modes import agent_first
from app.domain.services import recording_policy_service as rps
from app.domain.services.recording_policy_service import (
    DISCLOSURE_FAILED,
    DISCLOSURE_SPOKEN,
    RecordingDecision,
    get_disclosure_state,
    record_disclosure_state,
)

_TENANT = "22222222-2222-2222-2222-222222222222"
_CALL_UUID = "44444444-4444-4444-4444-444444444444"

_ANNOUNCE = RecordingDecision(
    should_record=True,
    announcement_required=True,
    announcement_text="This call may be recorded for quality and training purposes.",
    opt_out_dtmf_digit=None,
    retention_days=90,
    reason="tenant_default_two_party",
)


class _CountingInterruptedPipeline:
    """Every call to synthesize_and_send_audio reports an interruption —
    the callee talked over the notice on the one attempt made."""

    def __init__(self):
        self.calls = 0

    def clear_barge_in_event(self, session):
        pass

    async def synthesize_and_send_audio(self, session, text, websocket=None):
        self.calls += 1
        return True  # interrupted


@pytest.mark.asyncio
async def test_a_single_interrupted_attempt_fails_closed():
    """Load-bearing test for the 2026-08-11 fix: an interrupted notice is
    attempted exactly ONCE (no restart-from-the-top), and the ledger ends
    on DISCLOSURE_FAILED so the recording is suppressed."""
    pipeline = _CountingInterruptedPipeline()
    session = types.SimpleNamespace(conversation_history=[])
    voice_session = types.SimpleNamespace(
        call_id=_CALL_UUID,
        call_session=session,
        pipeline=pipeline,
        _dialer_tenant_id=_TENANT,
    )
    container = types.SimpleNamespace(is_initialized=True, db_pool=object())

    with patch(
        "app.core.container.get_container", return_value=container
    ), patch.object(
        rps.RecordingPolicyService, "decide", new=AsyncMock(return_value=_ANNOUNCE)
    ):
        await agent_first._speak_recording_disclosure(voice_session)

    assert pipeline.calls == 1, (
        "the notice must be attempted exactly once — a second call means "
        "the restart-from-the-top bug is back"
    )
    assert get_disclosure_state(_CALL_UUID) == DISCLOSURE_FAILED
    assert session.conversation_history == [], (
        "an interrupted, never-completed notice must not be recorded as "
        "having been said to the LLM either"
    )


def test_no_retry_after_interruption():
    """2026-08-11: restarting from the top talked over the caller a second
    time and both traced production calls hung up right after. There must
    be exactly one attempt — no restart, no settle-gap sleep to burn."""
    assert agent_first._DISCLOSURE_MAX_ATTEMPTS == 1
    assert not hasattr(agent_first, "_DISCLOSURE_RETRY_SETTLE_S"), (
        "a settle-gap constant implies a retry loop exists again; the "
        "restart-from-the-top behaviour it supported is the bug this test "
        "guards against reintroducing"
    )


def test_disclosure_fn_does_not_restart_or_sleep():
    """Structural pin: no retry loop, no restart, no sleep between attempts.

    A future edit that reintroduces a `for` loop over synthesize_and_send_audio
    or an `asyncio.sleep` inside `_speak_recording_disclosure` would bring
    back exactly the "restart talks over the caller" defect this file exists
    to prevent.
    """
    import inspect

    src = inspect.getsource(agent_first._speak_recording_disclosure)
    assert "DISCLOSURE_FAILED" in src, "the fail-closed path must remain"
    assert "asyncio.sleep" not in src, (
        "a settle-gap sleep implies a retry loop; do not restart the notice"
    )
    assert src.count("synthesize_and_send_audio(") == 1, (
        "the notice must be attempted exactly once — no restart-from-the-top"
    )


def test_ledger_still_fails_closed_for_an_unknown_call():
    """An unknown call must read as 'no disclosure', so losing the in-memory
    ledger can only ever suppress a recording, never permit one."""
    assert get_disclosure_state("call-that-never-existed") is None


def test_failed_state_is_distinguishable_from_spoken():
    record_disclosure_state(DISCLOSURE_FAILED, "t-failed-call")
    record_disclosure_state(DISCLOSURE_SPOKEN, "t-spoken-call")
    assert get_disclosure_state("t-failed-call") == DISCLOSURE_FAILED
    assert get_disclosure_state("t-spoken-call") == DISCLOSURE_SPOKEN


def test_consent_requires_spoken_not_merely_attempted():
    """consent_satisfied must key on SPOKEN. A call whose notice was attempted
    and failed must never be treated as consented."""
    from app.domain.services.recording_policy_service import consent_satisfied

    class _Decision:
        announcement_required = True

    record_disclosure_state(DISCLOSURE_FAILED, "t-consent-failed")
    assert consent_satisfied(_Decision(), "t-consent-failed") is False

    record_disclosure_state(DISCLOSURE_SPOKEN, "t-consent-spoken")
    assert consent_satisfied(_Decision(), "t-consent-spoken") is True


def test_no_announcement_required_needs_no_disclosure():
    """A one-party / disabled policy must not be gated on the ledger at all."""
    from app.domain.services.recording_policy_service import consent_satisfied

    class _NoAnnounce:
        announcement_required = False

    assert consent_satisfied(_NoAnnounce(), "t-never-seen-call") is True
