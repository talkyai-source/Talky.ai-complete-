"""The pre-TTS hold must also end when NO EndOfTurn is ever coming.

WHY (production, 2026-08-17..19)
-------------------------------
Every pre-TTS hold that ever ran in production hit its 2.5s cap:

    16 pre_tts_hold_timeout        0 pre_tts_hold_released

Clearing `_caller_speaking` at the EndOfTurn (see
test_caller_speaking_cleared_on_end_of_turn) fixes the holds where an EndOfTurn
arrived and a filter swallowed it. Classifying all sixteen timeouts in the
journal — anchored on the moment each hold was ARMED, so an EndOfTurn belonging
to the previous turn cannot be miscounted — showed that is the small minority:

    (a) EndOfTurn arrived but was swallowed :  3
    (b) no EndOfTurn ever arrived           : 13

Case (b) has nothing to clear. Flux raises StartOfTurn on any speech-like sound
but only raises EndOfTurn on a real turn boundary, so a cough, a line blip or a
caller who trails off leaves the flag up with no counterpart event — forever.

    2026-08-18T19:58:54  barge_in_ignored_final_pre_tts   <- flag TRUE, hold armed
    2026-08-18T19:58:55  audio_level
    2026-08-18T19:58:56  audio_level                       (no EndOfTurn, ever)
    2026-08-18T19:58:57  pre_tts_hold_timeout waited=2.51s

The only witness left is the audio. `note_voice_activity` already stamps
`_caller_voice_last_at` on every voiced frame at RMS >= 500 — the same writer
that produced 236 detect_ms samples, so unlike three guards shipped earlier this
month it is known to VARY in production rather than sitting constant.

FAILING SAFE IS THE WHOLE DESIGN
--------------------------------
No measurement must never read as silence. A missing stamp (browser sessions
never run the telephony ingest loop), a garbage stamp, or a clock that went
backwards all have to leave the hold exactly as it behaved before this signal
existed — otherwise the fix for talking-over-people becomes a cause of it.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from app.domain.models.session import CallSession, CallState
from app.domain.services.voice_pipeline import playback_gate
from app.domain.services.voice_pipeline.playback_gate import (
    _MAX_HOLD_S,
    _QUIET_RELEASE_S,
    _caller_quiet_for_s,
    _fmt_ms,
    arm_pre_tts_hold,
    await_caller_pause,
    mark_caller_speaking,
    mark_caller_stopped,
)


def real_session(**over) -> CallSession:
    """The pydantic model, not a stand-in — `_caller_voice_last_at` is only
    assignable because it is underscore-prefixed."""
    s = CallSession(
        call_id="06a6f8c9-89c9-4707-ab3b-98d12f4930fe",
        campaign_id="c2b6734d-8992-4038-aaf5-b54a885e7abe",
        lead_id="lead-1",
        provider_call_id="talky-out-1",
        system_prompt="sp",
        voice_id="v1",
    )
    s.state = CallState.SPEAKING
    for k, v in over.items():
        setattr(s, k, v)
    return s


def quiet_for(session, seconds: float) -> None:
    """The caller's last voiced frame was `seconds` ago."""
    session._caller_voice_last_at = time.monotonic() - seconds


# ── the measurement ─────────────────────────────────────────────────────────

def test_no_stamp_is_no_opinion():
    assert _caller_quiet_for_s(real_session()) is None


def test_a_fresh_stamp_reads_as_near_zero():
    s = real_session()
    quiet_for(s, 0.0)
    q = _caller_quiet_for_s(s)
    assert q is not None and 0.0 <= q < 0.2


def test_a_stale_stamp_reads_as_its_age():
    s = real_session()
    quiet_for(s, 1.5)
    q = _caller_quiet_for_s(s)
    assert q is not None and 1.3 < q < 1.7


def test_a_garbage_stamp_is_no_opinion():
    """Never crash the hold, and never guess."""
    s = real_session()
    s._caller_voice_last_at = "not-a-number"
    assert _caller_quiet_for_s(s) is None


def test_a_clock_that_went_backwards_is_no_opinion():
    s = real_session()
    s._caller_voice_last_at = time.monotonic() + 5.0
    assert _caller_quiet_for_s(s) is None, (
        "a negative age must not be reported as 0ms, which would read as the "
        "caller speaking this instant"
    )


def test_none_and_zero_are_distinguishable_in_the_log():
    """`none` and `0` mean opposite things — "could not tell" versus "making
    noise right now". Collapsing them is how a dead signal reads as healthy."""
    assert _fmt_ms(None) == "none"
    assert _fmt_ms(0.0) == "0"
    assert _fmt_ms(0.7) == "700"


# ── the release ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_production_case_now_releases_early(caplog):
    """THE FIX. StartOfTurn armed the hold, no EndOfTurn is coming, and the
    caller has been silent since. Before: 2.5s. Now: released on the audio."""
    s = real_session()
    mark_caller_speaking(s)          # ...and nothing will ever clear it
    quiet_for(s, _QUIET_RELEASE_S + 0.3)
    arm_pre_tts_hold(s)

    started = time.monotonic()
    with caplog.at_level("INFO"):
        waited = await await_caller_pause(s, call_id=s.call_id)
    elapsed = time.monotonic() - started

    assert elapsed < 0.5, f"still held for {elapsed:.2f}s"
    assert waited < 0.5
    assert "pre_tts_hold_released" in caplog.text
    assert "released_by=acoustic_quiet" in caplog.text


@pytest.mark.asyncio
async def test_a_caller_still_talking_is_still_protected():
    """THE LOAD-BEARING NEGATIVE. The gate exists to stop the agent starting on
    top of someone mid-sentence. Fresh audio must hold, right up to the cap."""
    s = real_session()
    mark_caller_speaking(s)
    arm_pre_tts_hold(s)

    async def keep_talking():
        # a voiced frame every 20ms, exactly as the ingest loop stamps them
        while True:
            quiet_for(s, 0.0)
            await asyncio.sleep(0.02)

    talker = asyncio.create_task(keep_talking())
    try:
        waited = await await_caller_pause(s, call_id=s.call_id)
    finally:
        talker.cancel()

    assert waited >= _MAX_HOLD_S, (
        f"released after {waited:.2f}s while the caller was still audible"
    )


@pytest.mark.asyncio
async def test_end_of_turn_still_wins_and_is_attributed(caplog):
    """The EndOfTurn path must keep working, and the log must say which of the
    two exits fired — the wiring has to be a fact in the journal."""
    s = real_session()
    mark_caller_speaking(s)
    quiet_for(s, 0.0)                       # audio says: still talking
    arm_pre_tts_hold(s)

    async def end_of_turn_arrives():
        await asyncio.sleep(0.08)
        mark_caller_stopped(s)

    asyncio.create_task(end_of_turn_arrives())
    with caplog.at_level("INFO"):
        waited = await await_caller_pause(s, call_id=s.call_id)

    assert 0.05 < waited < 1.0
    assert "released_by=end_of_turn" in caplog.text


@pytest.mark.asyncio
async def test_a_session_with_no_audio_measurement_behaves_exactly_as_before():
    """Browser / ask-AI sessions never run the telephony ingest loop, so the
    stamp is absent. That must mean "no opinion", not "silent"."""
    s = real_session()
    mark_caller_speaking(s)
    arm_pre_tts_hold(s)
    assert _caller_quiet_for_s(s) is None

    waited = await await_caller_pause(s, call_id=s.call_id)

    assert waited >= _MAX_HOLD_S, (
        "a missing measurement released the hold — that is the failure mode "
        "that talks over people"
    )


@pytest.mark.asyncio
async def test_line_noise_degrades_to_the_old_behaviour():
    """Noise above RMS 500 keeps the stamp fresh, so the hold runs its cap. No
    worse than before the change — the safe direction to fail."""
    s = real_session()
    mark_caller_speaking(s)
    arm_pre_tts_hold(s)

    async def hissing_line():
        while True:
            quiet_for(s, 0.0)
            await asyncio.sleep(0.02)

    noise = asyncio.create_task(hissing_line())
    try:
        waited = await await_caller_pause(s, call_id=s.call_id)
    finally:
        noise.cancel()
    assert waited >= _MAX_HOLD_S


@pytest.mark.asyncio
async def test_the_kill_switch_restores_the_exact_previous_behaviour(monkeypatch):
    """VOICE_PRE_TTS_QUIET_RELEASE_S=0 turns the acoustic exit off without a
    redeploy, leaving only the EndOfTurn exit and the cap."""
    monkeypatch.setattr(playback_gate, "_QUIET_RELEASE_S", 0.0)
    s = real_session()
    mark_caller_speaking(s)
    quiet_for(s, 5.0)                      # silent for ages
    arm_pre_tts_hold(s)

    waited = await await_caller_pause(s, call_id=s.call_id)

    assert waited >= _MAX_HOLD_S


@pytest.mark.asyncio
async def test_an_unarmed_hold_costs_nothing():
    s = real_session()
    mark_caller_speaking(s)
    quiet_for(s, 0.0)
    assert await await_caller_pause(s, call_id=s.call_id) == 0.0


@pytest.mark.asyncio
async def test_a_quiet_caller_never_enters_the_loop():
    """Already cleared: the hold returns immediately and logs nothing, so the
    release counter keeps meaning "a hold that actually waited"."""
    s = real_session()
    mark_caller_stopped(s)
    arm_pre_tts_hold(s)
    assert await await_caller_pause(s, call_id=s.call_id) == 0.0


@pytest.mark.asyncio
async def test_the_arm_is_consumed_even_when_the_acoustic_exit_fires():
    """One-shot, or a later turn inherits a hold it never earned."""
    s = real_session()
    mark_caller_speaking(s)
    quiet_for(s, _QUIET_RELEASE_S + 0.3)
    arm_pre_tts_hold(s)

    await await_caller_pause(s, call_id=s.call_id)

    assert getattr(s, "_defer_playback_for_caller") is False
    assert await await_caller_pause(s, call_id=s.call_id) == 0.0


@pytest.mark.asyncio
async def test_the_timeout_now_reports_what_the_audio_said(caplog):
    """When it does time out, the log has to say whether the caller was audible
    — that is the difference between "a caller who never yields" (correct) and
    "a flag nothing ever lowered" (the bug this replaces)."""
    s = real_session()
    mark_caller_speaking(s)
    arm_pre_tts_hold(s)                    # no stamp at all => must time out

    with caplog.at_level("WARNING"):
        await await_caller_pause(s, call_id=s.call_id)

    assert "pre_tts_hold_timeout" in caplog.text
    assert "quiet_ms=none" in caplog.text


def test_the_window_sits_between_a_sentence_pause_and_the_cap():
    """A bound below ~0.4s fires inside ordinary speech; at or above the cap it
    can never fire at all. Both make the change pointless or harmful."""
    assert 0.4 < _QUIET_RELEASE_S < _MAX_HOLD_S


@pytest.mark.asyncio
async def test_a_hold_that_was_not_needed_says_so(caplog):
    """The fix working must not be silent.

    A barge-in armed the hold, the EndOfTurn lowered the flag before playback
    was ready, and there is nothing to wait for. That is the GOOD outcome, but
    it returns immediately and used to log nothing — making "the clear ran in
    time" indistinguishable from "the hold was never armed".

    It matters because `pre_tts_hold_released` only fires when the hold enters
    the wait loop AND the flag drops while it waits. The better the clear gets,
    the rarer that is. Counting releases alone would read a working fix as a
    dead one.
    """
    s = real_session()
    mark_caller_stopped(s)          # EndOfTurn already lowered it
    arm_pre_tts_hold(s)

    with caplog.at_level("INFO"):
        waited = await await_caller_pause(s, call_id=s.call_id)

    assert waited == 0.0
    assert "pre_tts_hold_not_needed" in caplog.text
    assert "pre_tts_hold_released" not in caplog.text, (
        "an instant return must not be counted as a release — that would "
        "inflate the metric that proves the hold does its job"
    )
