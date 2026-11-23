"""Cases the reviews of the Realtime opening notice named, now pinned.

4/5: the provider's "caller started speaking" event goes through the bridge's own
     `interrupted` branch (-> `_cancel_playback`), not a hand-cancelled task.
6:   a notice owed + a "do not record" policy never opens the recording gate, end to end
     through `run_realtime_bridge`.
"""
import asyncio

import pytest

from app.domain.services.recording_policy_service import RecordingDecision, get_disclosure_state
from app.realtime import opening_notice as on
from app.realtime.openai import RealtimeEvent
from tests.unit.test_realtime_opening_notice import (  # noqa: F401  (autouse fixture is re-used)
    CALL,
    PLATFORM_OPENER,
    _clean,
    _patched_policy,
    _sleep5,
    build,
    cand,
    feed,
    start,
)
from unittest.mock import AsyncMock

SPEECH_STARTED = RealtimeEvent(kind="interrupted", raw={"reason": "speech_started"})


@pytest.mark.asyncio
async def test_caller_speech_event_in_the_middle_of_the_opening_is_unconfirmed_and_not_restarted():
    h = build()
    gate = await start(h)
    h.gw.finish_playback = AsyncMock(side_effect=_sleep5)

    async def events():
        yield cand(PLATFORM_OPENER)
        for _ in range(200):
            if h.sent:
                break
            await asyncio.sleep(0)
        assert h.sent, "audio must have been submitted before the interruption"
        yield SPEECH_STARTED
    h.bridge._rt.events = events
    await h.bridge._pump_model_events()
    if h.bridge._playback_task:
        await asyncio.gather(h.bridge._playback_task, return_exceptions=True)
    assert gate.state == "unconfirmed" and gate.reason == "interrupted"
    assert get_disclosure_state(CALL) == "failed"
    assert gate.state != "delivered" and get_disclosure_state(CALL) not in {"spoken", "transmitted"}
    h.rt.repair_unspoken_response.assert_not_awaited()
    assert len(h.sent) == 1 and h.gw.begin_playback.await_count == 1  # the opening was not played again


@pytest.mark.asyncio
async def test_caller_speech_event_before_any_audio_returns_the_gate_to_pending_then_the_next_match_is_delivered():
    h = build()
    gate = await start(h)
    release = asyncio.Event()

    async def slow_begin(*a):
        await release.wait()
    h.gw.begin_playback = AsyncMock(side_effect=slow_begin)

    async def events():
        yield cand(PLATFORM_OPENER)
        for _ in range(200):
            if h.gw.begin_playback.await_count:
                break
            await asyncio.sleep(0)
        assert h.gw.begin_playback.await_count == 1 and gate.state == "playing"
        yield SPEECH_STARTED
    h.bridge._rt.events = events
    await h.bridge._pump_model_events()
    if h.bridge._playback_task:
        await asyncio.gather(h.bridge._playback_task, return_exceptions=True)
    assert h.sent == [] and gate.state == "pending"
    assert get_disclosure_state(CALL) is None
    h.gw.begin_playback = AsyncMock()
    await feed(h.bridge, PLATFORM_OPENER)
    assert gate.state == "delivered" and len(h.sent) == 1
    assert get_disclosure_state(CALL) == "transmitted"


_REFUSED_WITH_NOTICE = RecordingDecision(
    should_record=False, announcement_required=True,
    announcement_text="This call may be recorded for quality and training purposes.",
    opt_out_dtmf_digit=None, retention_days=90, reason="tenant_policy_disabled")


@pytest.mark.asyncio
async def test_a_notice_owed_with_a_do_not_record_policy_never_opens_the_recording_gate_through_the_full_flow():
    h = build()

    async def run():
        await feed(h.bridge, h.bridge._opening_gate.required_text)
    h.bridge.run = run
    p_container, p_policy = _patched_policy(_REFUSED_WITH_NOTICE)
    with p_container, p_policy:
        await on.run_realtime_bridge(h.vs)
    gate = h.bridge._opening_gate
    assert gate.notice_owed is True, "the case needs a notice that is owed"
    assert h.vs._recording_refused_by_policy is True
    calls = [c.args for c in h.gw.set_recording_enabled.call_args_list]
    assert calls and all(args == (CALL, False) for args in calls), calls
    assert gate.state == "delivered" and len(h.sent) == 1
