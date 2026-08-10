"""The spoken recording disclosure (2026-07-28 legal-exposure fix).

Before this fix, `RecordingPolicyService` computed `announcement_required`
and `announcement_text`, and NOTHING ever spoke them: the only consumer
was `RecordingService.save_and_link()`, which runs after the call has
ended and merely gates the upload. Two-party-consent tenants — the SAFE
DEFAULT for every tenant with no policy row — were therefore recorded
while the callee was never told.

What these tests pin down:

1. ORDER — when the policy requires a notice, it is emitted BEFORE any
   opener audio. A disclosure that lands after the callee has spoken is
   worthless, so "it was emitted" is not enough; the position matters.
2. The notice is NOT emitted when the policy does not require one.
3. RETENTION — audio is not kept for a call whose required notice was
   not delivered (fail closed), and is kept when it was.
4. The unwired DTMF opt-out is not promised out loud.

Non-vacuity: every ordering assertion runs against a harness that
records BOTH the disclosure and the greeting on one ordered log, and
each test asserts what the log must NOT contain as well as what it must
— so a handler that emitted nothing, or emitted the notice too late, or
emitted it unconditionally, fails.
"""
from __future__ import annotations

import types
from unittest.mock import AsyncMock, patch

import pytest

from app.domain.models.conversation import MessageRole
from app.domain.services import recording_policy_service as rps
from app.domain.services.recording_policy_service import (
    CONSENT_ONE_PARTY,
    CONSENT_TWO_PARTY,
    DISCLOSURE_FAILED,
    DISCLOSURE_SPOKEN,
    RecordingDecision,
    consent_satisfied,
    get_disclosure_state,
    record_disclosure_state,
    spoken_disclosure_text,
)
from app.domain.services.recording_service import RecordingBuffer, RecordingService
from app.domain.services.telephony.modes.agent_first import _send_outbound_greeting

TENANT = "22222222-2222-2222-2222-222222222222"
CALL_UUID = "11111111-1111-1111-1111-111111111111"

GREETING = "Hi, this is Sarah calling from Acme — do you have a quick minute?"

_ANNOUNCE = RecordingDecision(
    should_record=True,
    announcement_required=True,
    announcement_text=(
        "This call may be recorded for quality and training purposes. "
        "Press 9 at any time to opt out of recording."
    ),
    opt_out_dtmf_digit="9",
    retention_days=90,
    reason="tenant_default_two_party",
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
    """The ledger is module-global; keep tests independent."""
    rps._DISCLOSURE_LEDGER.clear()
    yield
    rps._DISCLOSURE_LEDGER.clear()


# ---------------------------------------------------------------------------
# Greeting-path harness — one ordered log of everything the callee hears
# ---------------------------------------------------------------------------

class _FakeMediaGateway:
    def __init__(self, log):
        self._log = log

    async def send_audio(self, call_id, chunk):
        self._log.append(("opener_audio", chunk))

    async def flush_tts_buffer(self, call_id):
        pass

    async def clear_output_buffer(self, call_id):
        pass


class _FakePipeline:
    """Records TTS emissions in the same log as gateway audio, so the
    relative ORDER of disclosure vs. opener is directly observable."""

    def __init__(self, log, interrupted=False, raises=False):
        self._log = log
        self._interrupted = interrupted
        self._raises = raises

    def clear_barge_in_event(self, session):
        pass

    async def synthesize_and_send_audio(self, session, text, websocket=None):
        if self._raises:
            raise RuntimeError("tts down")
        self._log.append(("tts", text))
        return self._interrupted


def _make_voice_session(log, **pipeline_kwargs):
    session = types.SimpleNamespace(
        call_id=CALL_UUID,
        tenant_id=TENANT,
        llm_active=False,
        tts_active=False,
        barge_in_event=None,
        conversation_history=[],
    )
    voice_session = types.SimpleNamespace(
        call_id=CALL_UUID,
        call_session=session,
        pipeline=_FakePipeline(log, **pipeline_kwargs),
        media_gateway=_FakeMediaGateway(log),
        _presynth_greeting_audio=[b"\x00\x01" * 10],
        _presynth_greeting_text=GREETING,
    )
    return voice_session, session


def _patched_policy(decision, initialized=True):
    """Patch the container + policy lookup the greeting path performs."""
    container = types.SimpleNamespace(
        is_initialized=initialized, db_pool=object() if initialized else None
    )
    return (
        patch("app.core.container.get_container", return_value=container),
        patch.object(
            rps.RecordingPolicyService,
            "decide",
            new=AsyncMock(return_value=decision),
        ),
    )


async def _run_greeting(decision, initialized=True, **pipeline_kwargs):
    log = []
    voice_session, session = _make_voice_session(log, **pipeline_kwargs)
    p_container, p_policy = _patched_policy(decision, initialized=initialized)
    with p_container, p_policy:
        await _send_outbound_greeting(voice_session)
    return log, session


# ---------------------------------------------------------------------------
# 1. Ordering — the notice precedes the opener
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_disclosure_is_emitted_before_any_opener_audio():
    log, _ = await _run_greeting(_ANNOUNCE)

    kinds = [kind for kind, _ in log]
    assert "tts" in kinds, "the required recording notice was never spoken"
    assert "opener_audio" in kinds, (
        "harness sanity: the greeting must still play, otherwise the "
        "ordering assertion below would pass vacuously"
    )
    assert kinds.index("tts") < kinds.index("opener_audio"), (
        "the disclosure must be the FIRST audio — a notice played after "
        "the opener lets the callee speak before being told"
    )
    assert kinds[0] == "tts"

    spoken = log[0][1]
    assert "recorded" in spoken.lower()
    assert spoken != GREETING, "the notice must be its own utterance"


@pytest.mark.asyncio
async def test_disclosure_marks_ledger_and_history_in_order():
    log, session = await _run_greeting(_ANNOUNCE)

    assert get_disclosure_state(CALL_UUID) == DISCLOSURE_SPOKEN

    contents = [m.content for m in session.conversation_history]
    assert len(contents) == 2, contents
    assert "recorded" in contents[0].lower()
    assert contents[1] == GREETING, "greeting must follow the notice"
    assert all(m.role == MessageRole.ASSISTANT for m in session.conversation_history)


# ---------------------------------------------------------------------------
# 2. No notice when the policy does not require one
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_disclosure_when_policy_does_not_require_it():
    log, session = await _run_greeting(_NO_ANNOUNCE)

    kinds = [kind for kind, _ in log]
    assert "tts" not in kinds, (
        "a one-party tenant must not have a recording notice forced on it"
    )
    assert kinds == ["opener_audio"] * len(kinds) and kinds, (
        "the greeting itself must still play"
    )
    assert [m.content for m in session.conversation_history] == [GREETING]
    assert get_disclosure_state(CALL_UUID) == rps.DISCLOSURE_NOT_REQUIRED


@pytest.mark.asyncio
async def test_notice_and_no_notice_paths_differ():
    """Non-vacuity: the same harness produces materially different
    output for the two policies, so neither assertion is trivially true."""
    announced, _ = await _run_greeting(_ANNOUNCE)
    silent, _ = await _run_greeting(_NO_ANNOUNCE)

    assert [k for k, _ in announced] != [k for k, _ in silent]
    assert len(announced) == len(silent) + 1


# ---------------------------------------------------------------------------
# 3. Failure modes — fail closed on the RECORDING, never on the call
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tts_failure_suppresses_recording_but_call_continues():
    log, session = await _run_greeting(_ANNOUNCE, raises=True)

    assert get_disclosure_state(CALL_UUID) == DISCLOSURE_FAILED
    assert [k for k, _ in log] == ["opener_audio"] * len(log) and log, (
        "the call must continue — only the recording is sacrificed"
    )
    assert [m.content for m in session.conversation_history] == [GREETING]


@pytest.mark.asyncio
async def test_interrupted_disclosure_is_not_counted_as_delivered():
    await _run_greeting(_ANNOUNCE, interrupted=True)
    assert get_disclosure_state(CALL_UUID) == DISCLOSURE_FAILED


@pytest.mark.asyncio
async def test_policy_unavailable_fails_closed():
    """No DB pool = no way to prove a notice was unnecessary."""
    log, _ = await _run_greeting(_ANNOUNCE, initialized=False)

    assert get_disclosure_state(CALL_UUID) == DISCLOSURE_FAILED
    assert [k for k, _ in log] == ["opener_audio"] * len(log) and log


# ---------------------------------------------------------------------------
# 4. Retention gate
# ---------------------------------------------------------------------------

class _FakeAcquireCtx:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


class _FakeTxnCtx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeConn:
    async def fetchrow(self, *args, **kwargs):
        return {"id": CALL_UUID}

    async def execute(self, *args, **kwargs):
        return None

    def transaction(self):
        return _FakeTxnCtx()


class _FakeDbPool:
    def acquire(self):
        return _FakeAcquireCtx(_FakeConn())


class _FakeS3:
    bucket = "test-bucket"
    region = "us-east-1"

    def __init__(self):
        self.uploads = []

    def is_available(self):
        return True

    def upload(self, key, data, content_type="audio/wav"):
        self.uploads.append(key)


def _buffer():
    buf = RecordingBuffer(call_id=CALL_UUID, sample_rate=16000)
    buf.add_chunk(b"\x01\x02" * 200)
    return buf


async def _save(decision):
    s3 = _FakeS3()
    svc = RecordingService(db_pool=_FakeDbPool(), s3_client=s3)
    with patch.object(
        rps.RecordingPolicyService, "decide", new=AsyncMock(return_value=decision)
    ):
        result = await svc.save_and_link(
            call_id=CALL_UUID,
            buffer=_buffer(),
            tenant_id=TENANT,
            campaign_id="33333333-3333-3333-3333-333333333333",
        )
    return result, s3


@pytest.mark.asyncio
async def test_recording_suppressed_when_required_notice_not_delivered():
    result, s3 = await _save(_ANNOUNCE)

    assert result is None, "audio must not be retained without the notice"
    assert s3.uploads == [], "nothing may reach storage"


@pytest.mark.asyncio
async def test_recording_retained_when_notice_was_spoken():
    record_disclosure_state(DISCLOSURE_SPOKEN, CALL_UUID)
    result, s3 = await _save(_ANNOUNCE)

    assert result is not None
    assert len(s3.uploads) == 1


@pytest.mark.asyncio
async def test_recording_retained_when_no_notice_required():
    """Non-vacuity for the gate: the SAME call id with no ledger entry
    uploads fine once the policy stops demanding a notice — so the
    suppression above is caused by the missing disclosure, not by the
    harness failing to upload at all."""
    result, s3 = await _save(_NO_ANNOUNCE)

    assert result is not None
    assert len(s3.uploads) == 1


@pytest.mark.asyncio
async def test_local_save_blocked_after_known_disclosure_failure():
    """The teardown fallback calls _save_local directly, bypassing
    save_and_link's gate."""
    record_disclosure_state(DISCLOSURE_FAILED, CALL_UUID)
    svc = RecordingService(db_pool=_FakeDbPool(), s3_client=_FakeS3())

    result = await svc._save_local(
        call_id=CALL_UUID, buffer=_buffer(), tenant_id=TENANT, campaign_id="c1",
    )
    assert result is None


# ---------------------------------------------------------------------------
# 5. Disabled policy — the LAWFUL way to drop the spoken disclosure
#
# The client wants the disclosure gone from the opener. Removing the spoken
# notice while still recording would be unlawful in a two-party jurisdiction
# (this is a UK campaign) and the ledger would silently bin every recording
# anyway (announcement_required=True + never spoken => DISCLOSURE_FAILED =>
# save_and_link refuses the upload). The compliant route is
# default_consent_mode='disabled': should_record goes False too, so the
# announcement is dropped as a CONSEQUENCE of not recording, not by lying
# about a still-active recording.
# ---------------------------------------------------------------------------

class _DisabledRowConn:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def fetchrow(self, *a, **k):
        return {
            "default_consent_mode": rps.CONSENT_DISABLED,
            "retention_days": 90,
            "announcement_text": (
                "This call may be recorded for quality and training purposes."
            ),
            "opt_out_dtmf_digit": "9",
            "two_party_country_codes": [],
        }


class _DisabledRowPool:
    def acquire(self):
        return _DisabledRowConn()


_DISABLED = RecordingDecision(
    should_record=False,
    announcement_required=False,
    announcement_text=None,
    opt_out_dtmf_digit=None,
    retention_days=90,
    reason="tenant_policy_disabled",
)


@pytest.mark.asyncio
async def test_disabled_policy_turns_off_both_recording_and_announcement():
    """decide() must turn BOTH knobs off together. A decision that dropped
    the announcement but left should_record=True would record the callee
    silently — worse than the two-party default it replaces."""
    decision = await rps.RecordingPolicyService(_DisabledRowPool()).decide(
        tenant_id="t-disabled"
    )

    assert decision.should_record is False
    assert decision.announcement_required is False
    assert decision.announcement_text is None
    assert spoken_disclosure_text(decision) is None, (
        "no text to speak means the greeting path cannot accidentally "
        "synthesize an announcement for a disabled tenant"
    )


@pytest.mark.asyncio
async def test_disabled_policy_speaks_no_announcement_on_a_call():
    log, session = await _run_greeting(_DISABLED)

    kinds = [kind for kind, _ in log]
    assert "tts" not in kinds, "a disabled tenant must never hear a recording notice"
    assert kinds == ["opener_audio"] * len(kinds) and kinds, (
        "the greeting itself must still play — disabling recording must "
        "not silence the call"
    )
    assert get_disclosure_state(CALL_UUID) == rps.DISCLOSURE_NOT_REQUIRED


@pytest.mark.asyncio
async def test_disabled_policy_never_uploads_a_recording():
    result, s3 = await _save(_DISABLED)

    assert result is None, "should_record=False must be an absolute gate"
    assert s3.uploads == []


@pytest.mark.asyncio
async def test_two_party_default_still_announces_and_records():
    """Non-regression on the flip side of the disabled test above: the
    ordinary two-party default (what every tenant gets unless disabled is
    set explicitly) must still both SPEAK the notice and RETAIN the
    recording. Disabling must be an opt-in per-tenant choice, never an
    accidental side effect of this fix."""
    log, _ = await _run_greeting(_ANNOUNCE)
    assert "tts" in [k for k, _ in log], "two-party default must still announce"
    assert get_disclosure_state(CALL_UUID) == DISCLOSURE_SPOKEN

    result, s3 = await _save(_ANNOUNCE)  # ledger already SPOKEN from the run above
    assert result is not None, "two-party default must still record"
    assert len(s3.uploads) == 1


@pytest.mark.asyncio
async def test_partial_interrupted_notice_never_marks_ledger_spoken_or_retains():
    """A notice cut short by barge-in must read as FAILED, never SPOKEN —
    and that must propagate all the way to save_and_link refusing the
    upload, not just to the ledger state in isolation. This is the ledger
    honesty guarantee the disclosure-retry fix (agent_first.py) depends
    on: an attempt that didn't finish must never be reported as delivered
    just to keep the recording."""
    await _run_greeting(_ANNOUNCE, interrupted=True)

    assert get_disclosure_state(CALL_UUID) == DISCLOSURE_FAILED
    assert get_disclosure_state(CALL_UUID) != DISCLOSURE_SPOKEN

    result, s3 = await _save(_ANNOUNCE)
    assert result is None, "a call whose notice never finished must not be retained"
    assert s3.uploads == []


# ---------------------------------------------------------------------------
# 6. Ledger + text helpers
# ---------------------------------------------------------------------------

def test_consent_fails_closed_for_unknown_call():
    assert consent_satisfied(_ANNOUNCE, "never-seen-this-call") is False
    assert consent_satisfied(_NO_ANNOUNCE, "never-seen-this-call") is True


def test_consent_matches_any_registered_alias():
    record_disclosure_state(DISCLOSURE_SPOKEN, "voice-uuid", "calls-id")
    assert consent_satisfied(_ANNOUNCE, "calls-id") is True
    assert consent_satisfied(_ANNOUNCE, "voice-uuid") is True
    assert consent_satisfied(_ANNOUNCE, "some-other-call") is False


def test_duck_typed_decision_without_announcement_field_is_permitted():
    """Existing callers/tests pass a stub carrying only `should_record`;
    that must keep its old behaviour rather than raising."""
    stub = types.SimpleNamespace(should_record=True, reason="ok")
    assert consent_satisfied(stub, "anything") is True


def test_opt_out_promise_is_not_spoken_while_dtmf_is_unwired():
    assert rps.DTMF_OPT_OUT_SUPPORTED is False, (
        "if DTMF opt-out is now wired, this test and the stripping "
        "behaviour it guards should be revisited"
    )
    text = spoken_disclosure_text(_ANNOUNCE)

    assert "recorded" in text.lower(), "the notice itself must survive"
    assert "press 9" not in text.lower(), (
        "we must not promise an opt-out that nothing honours"
    )
    assert "opt out" not in text.lower()


def test_opt_out_only_text_falls_back_to_a_real_notice():
    only_opt_out = RecordingDecision(
        should_record=True,
        announcement_required=True,
        announcement_text="Press 9 to opt out.",
        opt_out_dtmf_digit="9",
        retention_days=30,
        reason="two_party",
    )
    text = spoken_disclosure_text(only_opt_out)
    assert text == rps.MINIMUM_DISCLOSURE_TEXT
    assert "recorded" in text.lower()


def test_no_text_when_no_announcement_required():
    assert spoken_disclosure_text(_NO_ANNOUNCE) is None


def test_ordinary_notice_text_is_left_alone():
    decision = RecordingDecision(
        should_record=True,
        announcement_required=True,
        announcement_text="This call is recorded for quality and training.",
        opt_out_dtmf_digit=None,
        retention_days=30,
        reason="two_party",
    )
    assert (
        spoken_disclosure_text(decision)
        == "This call is recorded for quality and training."
    )


@pytest.mark.asyncio
async def test_safe_default_tenant_requires_a_notice():
    """End-to-end guard on the premise of this whole fix: a tenant with
    no policy row (the common case) is two-party and MUST be announced."""

    class _NoRowConn:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def fetchrow(self, *a, **k):
            return None

    class _NoRowPool:
        def acquire(self):
            return _NoRowConn()

    decision = await rps.RecordingPolicyService(_NoRowPool()).decide(tenant_id="t1")
    assert decision.should_record is True
    assert decision.announcement_required is True
    assert consent_satisfied(decision, "fresh-call") is False
    assert CONSENT_TWO_PARTY in decision.reason or "two_party" in decision.reason
    assert CONSENT_ONE_PARTY not in decision.reason
