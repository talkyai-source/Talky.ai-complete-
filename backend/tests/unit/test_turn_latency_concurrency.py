"""Turn latency: the two CORE-field confirmation LLM calls must run CONCURRENTLY.

`TurnRunner.run()` gates a pending EMAIL and a pending PHONE behind the same
deterministic regex classifier. On the ambiguous tail each gate falls back to a
small LLM call bounded at ~1.5s (confirm_llm._TIMEOUT_S). Those two calls used to
run sequentially, so a turn that was ambiguous on BOTH fields stacked two bounds
— up to 3s of dead air BEFORE `_stream_llm_and_tts()` produced any audio.

The fields are independent (neither verdict feeds the other; both are applied
together in a single `update_state_from_user_turn` call), so they are now
gathered. These tests pin:

  * both-pending  -> wall clock ~1x the per-call bound, not 2x
  * single-pending -> unchanged, still a plain await (no gather overhead)
  * one branch raising -> the other branch's verdict is NOT lost
  * ordering      -> both verdicts still reach update_state_from_user_turn in
                     the same argument positions, applied at the same moment

The timing assertion is the non-vacuous part: reverting `run()` to sequential
awaits makes `test_both_pending_verdicts_run_concurrently` fail (measured wall
clock ~2x the delay).
"""
from __future__ import annotations

import asyncio
import time

import pytest

from app.domain.models.conversation import Message, MessageRole
from app.domain.services.voice_pipeline.turn_runner import TurnRunner
from app.services.scripts import CallState

EMAIL = "bob@acme.com"
PHONE = "5551234567"
# Deterministic classifier returns "unclear" for this, forcing BOTH LLM gates.
AMBIGUOUS = "um hold on a second"
# The agent's prior turn. NOTE: `run()` also SEEDS a pending email out of the
# agent's last read-back (the "gap #2" path), so a read-back that names an
# address opens the email gate even when CallState has none — hence one
# read-back per scenario rather than one shared string.
READBACK_BOTH = "So that's bob@acme.com, and I've got 555-123-4567 — did I get that right?"
READBACK_EMAIL = "So that's bob@acme.com — did I get that right?"
READBACK_PHONE = "So that's 555-123-4567 — did I get that right?"
READBACK_NEITHER = "Are you the homeowner there?"

# One fake "LLM call" takes this long. Concurrent => ~DELAY total, sequential
# => ~2*DELAY. The assertion band is wide enough for a loaded CI box but can
# never straddle both outcomes.
DELAY = 0.30


class _SlowLLM:
    """Fake provider: each streamed answer costs DELAY seconds."""

    def __init__(self, answer: str = "yes", delay: float = DELAY):
        self._answer = answer
        self._delay = delay
        self.calls: list[str] = []

    async def stream_chat_with_timeout(self, messages, system_prompt=None, **kwargs):
        self.calls.append(messages[0].content)
        await asyncio.sleep(self._delay)
        yield self._answer


class _FakePipeline:
    """Minimal stand-in for VoicePipelineService (TurnRunner reads at call time)."""

    def __init__(self, llm_provider):
        self.llm_provider = llm_provider
        self.stt_provider = None
        self.transcript_service = _FakeTranscripts()

    async def _stream_llm_and_tts(self, session, websocket):
        return "Great, thanks.", 1.0, 1.0

    def _supports_llm_end_session_action(self, session) -> bool:
        return False


class _FakeTranscripts:
    def accumulate_turn(self, **kwargs):
        pass


class _Session:
    def __init__(self, slots: CallState, readback: str):
        self.call_id = "latency-test-call"
        self.turn_id = 2
        self.talklee_call_id = None
        self.tts_active = False
        self.captured_slots = slots
        self.conversation_history = [
            Message(role=MessageRole.ASSISTANT, content=readback),
        ]


def _both_pending() -> _Session:
    return _Session(
        CallState(email=EMAIL, email_confirmed=False, phone=PHONE, phone_confirmed=False),
        READBACK_BOTH,
    )


def _email_only() -> _Session:
    return _Session(CallState(email=EMAIL, email_confirmed=False), READBACK_EMAIL)


def _phone_only() -> _Session:
    return _Session(CallState(phone=PHONE, phone_confirmed=False), READBACK_PHONE)


# ── the gate actually opens (guards against a vacuous timing test) ────────────

@pytest.mark.asyncio
async def test_both_gates_actually_invoke_the_llm_fallback():
    """If the fixture didn't open BOTH gates the timing test would pass trivially."""
    llm = _SlowLLM("yes", delay=0.0)
    session = _both_pending()
    await TurnRunner(_FakePipeline(llm)).run(session, AMBIGUOUS)

    assert len(llm.calls) == 2, f"expected 2 fallback calls, got {llm.calls}"
    subjects = " ".join(llm.calls)
    assert EMAIL in subjects and PHONE in subjects
    # And a clean 'yes' verdict is applied to BOTH slots.
    assert session.captured_slots.email_confirmed is True
    assert session.captured_slots.phone_confirmed is True


# ── the actual latency fix ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_both_pending_verdicts_run_concurrently():
    """Wall clock must be ~1x the per-call bound, not 2x (was up to 3s of dead air)."""
    llm = _SlowLLM("yes")
    session = _both_pending()

    t0 = time.perf_counter()
    await TurnRunner(_FakePipeline(llm)).run(session, AMBIGUOUS)
    elapsed = time.perf_counter() - t0

    assert len(llm.calls) == 2
    assert elapsed < DELAY * 1.8, (
        f"email+phone verdicts appear SEQUENTIAL: {elapsed:.3f}s for two "
        f"{DELAY}s calls (concurrent should be ~{DELAY}s)"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("make_session", [_email_only, _phone_only])
async def test_single_pending_field_is_unchanged(make_session):
    """The common single-field case still costs exactly one bounded call."""
    llm = _SlowLLM("yes")
    session = make_session()

    t0 = time.perf_counter()
    await TurnRunner(_FakePipeline(llm)).run(session, AMBIGUOUS)
    elapsed = time.perf_counter() - t0

    assert len(llm.calls) == 1
    assert elapsed < DELAY * 1.8
    slots = session.captured_slots
    assert (slots.email_confirmed or slots.phone_confirmed) is True


@pytest.mark.asyncio
async def test_one_branch_failing_does_not_lose_the_other_verdict():
    """A provider blow-up on one field must not abort or discard the other."""

    class _HalfBrokenLLM:
        def __init__(self):
            self.calls: list[str] = []

        async def stream_chat_with_timeout(self, messages, system_prompt=None, **kwargs):
            content = messages[0].content
            self.calls.append(content)
            await asyncio.sleep(0.01)
            if EMAIL in content:
                raise RuntimeError("provider down for the email verdict")
            yield "yes"

    llm = _HalfBrokenLLM()
    session = _both_pending()
    await TurnRunner(_FakePipeline(llm)).run(session, AMBIGUOUS)

    assert len(llm.calls) == 2, "the failing branch aborted the other one"
    slots = session.captured_slots
    assert slots.phone_confirmed is True, "the healthy phone verdict was lost"
    assert slots.email_confirmed is False, "the failed email verdict must fail CLOSED"
    assert slots.email == EMAIL, "failing closed must leave the email PENDING, not wipe it"


@pytest.mark.asyncio
async def test_one_branch_timing_out_does_not_lose_the_other(monkeypatch):
    """Same, via the real 1.5s timeout path (shortened) — never-silent contract:
    a timed-out verdict returns 'unclear' and the turn still speaks."""
    import app.domain.services.voice_pipeline.confirm_llm as confirm_llm
    monkeypatch.setattr(confirm_llm, "_TIMEOUT_S", 0.05)

    class _OneSlowLLM:
        def __init__(self):
            self.calls: list[str] = []

        async def stream_chat_with_timeout(self, messages, system_prompt=None, **kwargs):
            content = messages[0].content
            self.calls.append(content)
            if EMAIL in content:
                await asyncio.sleep(5.0)  # far past the (patched) bound
            yield "yes"

    llm = _OneSlowLLM()
    session = _both_pending()
    reply, _, _ = await TurnRunner(_FakePipeline(llm)).run(session, AMBIGUOUS)

    assert len(llm.calls) == 2
    assert session.captured_slots.phone_confirmed is True
    assert session.captured_slots.email_confirmed is False
    assert reply == "Great, thanks."  # never silent


# ── application order is preserved ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_both_verdicts_are_applied_together_in_one_state_update():
    """Gathering cannot reorder application: both verdicts are passed to a SINGLE
    update_state_from_user_turn call, in their existing argument positions."""
    import app.domain.services.voice_pipeline.turn_runner as tr

    seen: list[dict] = []
    real = tr.update_state_from_user_turn

    def _spy(state, utterance, **kwargs):
        seen.append(kwargs)
        return real(state, utterance, **kwargs)

    monkey = pytest.MonkeyPatch()
    monkey.setattr(tr, "update_state_from_user_turn", _spy)
    try:
        llm = _SlowLLM("yes")
        await TurnRunner(_FakePipeline(llm)).run(_both_pending(), AMBIGUOUS)
    finally:
        monkey.undo()

    assert len(seen) == 1, "verdicts must still be applied in ONE atomic state update"
    kw = seen[0]
    assert kw["readback_issued"] is True
    assert kw["phone_readback_issued"] is True
    assert kw["confirmation_verdict"] == "affirm"
    assert kw["phone_confirmation_verdict"] == "affirm"


@pytest.mark.asyncio
async def test_no_pending_fields_makes_no_llm_call():
    """The overwhelmingly common turn adds zero confirmation latency."""
    llm = _SlowLLM("yes")
    session = _Session(CallState(), READBACK_NEITHER)
    await TurnRunner(_FakePipeline(llm)).run(session, "sure, tell me more")
    assert llm.calls == []


@pytest.mark.asyncio
async def test_deterministic_verdict_skips_the_llm_entirely():
    """A clear 'yes' is resolved by the regex classifier — no fallback call."""
    llm = _SlowLLM("yes")
    session = _both_pending()
    await TurnRunner(_FakePipeline(llm)).run(session, "yes that's correct")
    assert llm.calls == []
    assert session.captured_slots.email_confirmed is True
    assert session.captured_slots.phone_confirmed is True
