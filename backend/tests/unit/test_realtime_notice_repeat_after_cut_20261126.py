"""Realtime phone calls: a recording notice cut off by the person is said once more.

The realtime equivalent of ``voice_pipeline/notice_repeat.py``. When the person speaks
while the opening audio is being sent, the notice is settled "unconfirmed". The next
model reply is then withheld, the notice SENTENCE alone is requested once, played only
if the model's transcript matches word for word, and proven by the same receipt rule.
Fake model events and a fake gateway through the real RealtimeBridge: this proves the
bridge logic, not the live model.
"""
import asyncio
import logging
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.services import recording_policy_service as rps
from app.domain.services.recording_policy_service import get_disclosure_state
from app.realtime import notice_repeat as nr
from app.realtime import opening_notice as on
from app.realtime.openai import RealtimeEvent
from tests.unit.test_realtime_opening_notice import (
    CALL,
    PLAIN_OPENER,
    PLATFORM_OPENER,
    _sleep5,
    build,
    start,
)
from tests.unit.test_recording_disclosure import _ANNOUNCE, _NO_ANNOUNCE

TOOL_OUTPUT = [{"type": "function_call", "name": "lookup", "call_id": "c1"}]


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    rps._DISCLOSURE_LEDGER.clear()
    monkeypatch.delenv(on.ENV_SWITCH, raising=False)
    yield
    rps._DISCLOSURE_LEDGER.clear()


def mk(**kw):
    h = build(**kw)
    h.rt.truncate_response = AsyncMock()
    h.rt._response_epoch = 0
    h.rt.observation_provider = "openai"
    h.st = {"block": False, "n": 0}
    return h


def reply(text, *, chunks=1, output=None, rid="r2"):
    return RealtimeEvent(kind="response_candidate", text=text, audio=b"\xff" * (320 * chunks),
                         raw={"response": {"id": rid, "output": output or []}})


def arm(h):
    """The next utterance blocks inside send_audio after its first chunk."""
    h.st["block"], h.st["n"] = True, 0

    async def send_audio(call_id, pcm):
        h.st["n"] += 1
        h.sent.append(pcm)
        if h.st["block"] and h.st["n"] >= 2:
            await asyncio.sleep(5)
    h.gw.send_audio = send_audio


def disarm(h):
    h.st["block"] = False


async def cut(h, text):
    """Play ``text`` (two chunks); the person speaks after the first chunk was sent."""
    arm(h)

    async def events():
        yield reply(text, chunks=2, rid="r1")
        for _ in range(500):
            if h.st["n"] >= 2:
                break
            await asyncio.sleep(0)
        h.bridge._playback_task.cancel()
        await asyncio.gather(h.bridge._playback_task, return_exceptions=True)
    h.bridge._rt.events = events
    await h.bridge._pump_model_events()
    disarm(h)


async def feed_events(h, *evs):
    async def events():
        for ev in evs:
            yield ev
            if h.bridge._playback_task:
                await asyncio.gather(h.bridge._playback_task, return_exceptions=True)
    h.bridge._rt.events = events
    await h.bridge._pump_model_events()
    if h.bridge._playback_task:
        await asyncio.gather(h.bridge._playback_task, return_exceptions=True)


async def owed(**kw):
    h = mk(**kw)
    gate = await start(h)
    await cut(h, PLATFORM_OPENER)
    return h, gate


def msgs(caplog):
    return [r.getMessage() for r in caplog.records]


# 1
@pytest.mark.asyncio
async def test_cut_opening_then_next_reply_is_withheld_and_only_the_sentence_requested():
    h, gate = await owed()
    assert gate.state == "unconfirmed" and gate.reason == "interrupted"
    assert gate.repeat_state == "owed" and gate.notice_sentence
    assert "Am I speaking" not in gate.notice_sentence
    sent_before = len(h.sent)
    await feed_events(h, reply("Sure, I can help with that."))
    h.rt.repair_unspoken_response.assert_awaited_once()
    kwargs = h.rt.repair_unspoken_response.await_args.kwargs
    assert kwargs["required_text"] == gate.notice_sentence
    assert "Am I speaking" not in kwargs["required_text"]
    assert len(h.sent) == sent_before  # no byte of the withheld reply was sent
    assert gate.repeat_state == "requested"


# 2
@pytest.mark.asyncio
async def test_matching_sentence_plays_is_delivered_and_the_conversation_continues(caplog):
    h, gate = await owed()
    await feed_events(h, reply("Sure, I can help with that."))
    sent_before = len(h.sent)
    with caplog.at_level(logging.INFO):
        await feed_events(h, reply(gate.notice_sentence, rid="r3"))
    assert len(h.sent) == sent_before + 1
    assert get_disclosure_state(CALL) == "transmitted"
    assert any("recording_disclosure_delivered" in m and "attempt=2 late=True" in m
               and "path=realtime" in m for m in msgs(caplog))
    h.rt.request_response.assert_awaited_once()
    assert gate.repeat_state == "done"


# 3
@pytest.mark.asyncio
async def test_tenant_own_sentence_is_the_only_text_requested():
    h = mk()
    tenant_text = "Calls with Acme are recorded for training."
    decision = _ANNOUNCE.__class__(**{**_ANNOUNCE.__dict__, "announcement_text": tenant_text})
    gate = await start(h, decision)
    assert gate.notice == "tenant_text_first" and gate.notice_sentence == tenant_text
    await cut(h, gate.required_text)
    assert gate.repeat_state == "owed"
    await feed_events(h, reply("Sure."))
    assert h.rt.repair_unspoken_response.await_args.kwargs["required_text"] == tenant_text
    assert PLAIN_OPENER not in h.rt.repair_unspoken_response.await_args.kwargs["required_text"]


# 4
@pytest.mark.asyncio
async def test_sentence_cut_again_is_unconfirmed_never_tried_a_third_time(caplog):
    h, gate = await owed()
    await feed_events(h, reply("Sure."))
    with caplog.at_level(logging.INFO):
        await cut(h, gate.notice_sentence)
    assert get_disclosure_state(CALL) == "failed"
    assert any("recording_disclosure_unconfirmed" in m and "attempt=2" in m
               and "path=realtime" in m for m in msgs(caplog))
    h.rt.request_response.assert_not_awaited()
    assert gate.repeat_state == "done"
    sent_before = len(h.sent)
    await feed_events(h, reply("Anything else?", rid="r5"))
    assert h.rt.repair_unspoken_response.await_count == 1  # no third attempt
    assert len(h.sent) == sent_before + 1  # the reply plays untouched


# 5
@pytest.mark.asyncio
async def test_wrong_words_get_one_plain_repair_and_stay_unconfirmed(caplog):
    h, gate = await owed()
    await feed_events(h, reply("Sure."))
    sent_before = len(h.sent)
    with caplog.at_level(logging.INFO):
        await feed_events(h, reply("Something else entirely", rid="r3"))
    assert h.rt.repair_unspoken_response.await_count == 2
    assert h.rt.repair_unspoken_response.await_args_list[1].kwargs.get("required_text") is None
    assert any("attempt=2" in m and "reason=text_mismatch" in m and "path=realtime" in m
               for m in msgs(caplog))
    assert get_disclosure_state(CALL) == "failed"
    assert len(h.sent) == sent_before
    h.rt.request_response.assert_not_awaited()


# 6
@pytest.mark.asyncio
async def test_person_speaks_again_before_the_sentence_arrives_the_reply_plays(caplog):
    h, gate = await owed()
    await feed_events(h, reply("Sure."))
    h.rt._response_epoch += 1  # the person spoke again
    sent_before = len(h.sent)
    with caplog.at_level(logging.INFO):
        # Even a reply whose words equal the sentence plays: it answers the newer words.
        await feed_events(h, reply(gate.notice_sentence, rid="r3"))
    assert len(h.sent) == sent_before + 1
    h.rt.truncate_response.assert_not_awaited()
    h.rt.request_response.assert_not_awaited()
    assert any("attempt=2" in m and "reason=caller_spoke_again" in m for m in msgs(caplog))
    assert gate.repeat_state == "done" and get_disclosure_state(CALL) == "failed"
    await feed_events(h, reply("Fresh answer.", rid="r4"))
    assert len(h.sent) == sent_before + 2
    assert h.rt.repair_unspoken_response.await_count == 1


def _set(obj, name, value):
    setattr(obj, name, value)


SKIPS = [
    ("opted_out", lambda h: _set(h.vs.call_session, "_caller_opted_out", True)),
    ("opt_out_phrase", lambda h: _set(h.bridge, "_latest_caller_text", "do not call me again")),
    ("caller_ending", lambda h: _set(h.bridge, "_latest_caller_text", "ok goodbye")),
    ("transfer_in_progress", lambda h: _set(h.vs.call_session, "_transfer_ringing_since", time.monotonic())),
    ("already_delivered", lambda h: rps.record_disclosure_state("transmitted", CALL)),
    ("end_call_armed", lambda h: _set(h.bridge, "_hangup_started", True)),
    ("end_call_armed", lambda h: _set(h.bridge, "_closing_after_generation", 0)),
    ("end_call_armed", lambda h: _set(h.vs.call_session, "_end_call_requested", True)),
    ("call_ended", lambda h: _set(h.vs, "_call_ending", True)),
]


# 7
@pytest.mark.asyncio
@pytest.mark.parametrize("reason,setup", SKIPS)
async def test_skips_play_the_reply_untouched_and_log_the_reason(reason, setup, caplog):
    h, gate = await owed()
    setup(h)
    with caplog.at_level(logging.INFO):
        ok = await nr.admit(h.bridge, reply("Next."), {"id": "rt-9"})
    assert ok is True
    h.rt.repair_unspoken_response.assert_not_awaited()
    assert any("recording_notice_repeat_skipped" in m and f"reason={reason}" in m
               and "path=realtime" in m for m in msgs(caplog))
    assert gate.repeat_state == "skipped"


# 8
@pytest.mark.asyncio
async def test_a_tool_reply_plays_once_then_the_next_reply_is_withheld():
    h, gate = await owed()
    assert await nr.admit(h.bridge, reply("x", output=TOOL_OUTPUT), {"id": "rt-9"}) is True
    assert gate.repeat_state == "owed" and gate.repeat_deferrals == 1
    h.rt.repair_unspoken_response.assert_not_awaited()
    assert await nr.admit(h.bridge, reply("Answer."), {"id": "rt-10"}) is False
    h.rt.repair_unspoken_response.assert_awaited_once()
    assert gate.repeat_state == "requested"


@pytest.mark.asyncio
async def test_two_tool_replies_in_a_row_skip_the_repeat(caplog):
    h, gate = await owed()
    with caplog.at_level(logging.INFO):
        assert await nr.admit(h.bridge, reply("x", output=TOOL_OUTPUT), {"id": "a"}) is True
        assert await nr.admit(h.bridge, reply("y", output=TOOL_OUTPUT), {"id": "b"}) is True
    assert gate.repeat_state == "skipped"
    assert any("recording_notice_repeat_skipped" in m and "reason=tool_calls" in m for m in msgs(caplog))
    h.rt.repair_unspoken_response.assert_not_awaited()


# 9
@pytest.mark.asyncio
async def test_caller_words_still_pending_after_the_wait_skips(monkeypatch, caplog):
    monkeypatch.setattr(nr, "_CALLER_WORDS_WAIT_S", 0.05)
    h, gate = await owed()
    h.bridge._caller_transcript_pending = True
    with caplog.at_level(logging.INFO):
        assert await nr.admit(h.bridge, reply("Next."), {"id": "rt-9"}) is True
    assert any("reason=caller_words_unknown" in m for m in msgs(caplog))
    h.rt.repair_unspoken_response.assert_not_awaited()


@pytest.mark.asyncio
async def test_caller_words_arriving_within_the_wait_let_the_repeat_go_ahead(monkeypatch):
    monkeypatch.setattr(nr, "_CALLER_WORDS_WAIT_S", 2.0)
    h, gate = await owed()
    h.bridge._caller_transcript_pending = True

    async def clear():
        await asyncio.sleep(0.1)
        h.bridge._caller_transcript_pending = False
    task = asyncio.create_task(clear())
    assert await nr.admit(h.bridge, reply("Next."), {"id": "rt-9"}) is False
    await task
    h.rt.repair_unspoken_response.assert_awaited_once()


# 10
@pytest.mark.asyncio
async def test_all_audio_sent_then_cut_waiting_for_the_receipt_is_not_owed():
    h = mk()
    gate = await start(h)
    h.gw.finish_playback = AsyncMock(side_effect=_sleep5)

    async def events():
        yield reply(PLATFORM_OPENER, chunks=2, rid="r1")
        for _ in range(500):
            if h.gw.finish_playback.await_count:
                break
            await asyncio.sleep(0)
        h.bridge._playback_task.cancel()
        await asyncio.gather(h.bridge._playback_task, return_exceptions=True)
    h.bridge._rt.events = events
    await h.bridge._pump_model_events()
    assert gate.state == "unconfirmed" and gate.reason == "interrupted"
    assert gate.repeat_state == "none"
    await feed_events(h, reply("Next."))
    h.rt.repair_unspoken_response.assert_not_awaited()


# 11
@pytest.mark.asyncio
async def test_inbound_and_no_notice_calls_never_owe_a_repeat():
    h = mk(inbound=True)
    gate = await start(h)
    await cut(h, gate.required_text)
    assert gate.repeat_state == "none"
    await feed_events(h, reply("Next."))
    h.rt.repair_unspoken_response.assert_not_awaited()

    h2 = mk()
    gate2 = await start(h2, _NO_ANNOUNCE)
    await cut(h2, gate2.required_text)
    assert gate2.notice_owed is False and gate2.repeat_state == "none"


# 12
@pytest.mark.asyncio
async def test_the_recording_gate_is_never_touched_by_the_repeat():
    h, gate = await owed()
    before = list(h.gw.set_recording_enabled.call_args_list)
    await feed_events(h, reply("Sure."))
    await feed_events(h, reply(gate.notice_sentence, rid="r3"))
    assert h.gw.set_recording_enabled.call_args_list == before


# 13
@pytest.mark.asyncio
async def test_an_error_in_a_skip_check_plays_the_reply_and_marks_nothing(monkeypatch, caplog):
    h, gate = await owed()
    monkeypatch.setattr("app.domain.services.telephony.modes.agent_first._notice_skip_reason",
                        AsyncMock(side_effect=RuntimeError("boom")))
    with caplog.at_level(logging.INFO):
        assert await nr.admit(h.bridge, reply("Next."), {"id": "rt-9"}) is True
    assert any("recording_notice_repeat_error" in m and "error_type=RuntimeError" in m
               and "path=realtime" in m for m in msgs(caplog))
    h.rt.repair_unspoken_response.assert_not_awaited()
    assert get_disclosure_state(CALL) == "failed"
    assert gate.repeat_state == "done"


# 14
@pytest.mark.asyncio
async def test_the_repeated_notice_is_marked_in_the_transcript():
    h, gate = await owed()
    ts = MagicMock()
    ts.accumulate_turn.return_value = 1
    h.bridge._transcript_service = ts
    await feed_events(h, reply("Sure."))
    await feed_events(h, reply(gate.notice_sentence, rid="r3"))
    marked = [c for c in ts.accumulate_turn.call_args_list
              if c.args[1] == "assistant" and c.args[2] == gate.notice_sentence]
    assert marked and marked[0].kwargs["metadata"]["recording_notice"] == "repeated_after_cut"


# 15
@pytest.mark.asyncio
async def test_with_the_switch_off_there_is_no_gate_and_no_repeat(monkeypatch):
    monkeypatch.setenv(on.ENV_SWITCH, "0")
    h = mk()

    async def run():
        await feed_events(h, reply(PLATFORM_OPENER, rid="r1"), reply("Next.", rid="r2"))
    h.bridge.run = run
    await on.run_realtime_bridge(h.vs)
    assert h.bridge._opening_gate is None
    assert len(h.sent) == 2
    h.rt.repair_unspoken_response.assert_not_awaited()


# F1
@pytest.mark.asyncio
async def test_sentence_played_but_receipt_unproven_still_continues_the_conversation(caplog):
    h, gate = await owed()
    await feed_events(h, reply("Sure."))
    h.gw.finish_playback = AsyncMock(side_effect=lambda cid, uid: {
        "utterance_id": uid, "status": "unknown", "evidence": "unknown", "played_ms": 0})
    with caplog.at_level(logging.INFO):
        await feed_events(h, reply(gate.notice_sentence, rid="r3"))
    h.rt.request_response.assert_awaited_once()
    assert get_disclosure_state(CALL) == "failed"
    assert any("attempt=2" in m and "not proven" in m and "path=realtime" in m for m in msgs(caplog))


@pytest.mark.asyncio
async def test_proof_code_raising_still_continues_and_marks_nothing(monkeypatch, caplog):
    h, gate = await owed()
    gate.repeat_state, gate.repeat_epoch = "playing", h.rt._response_epoch
    monkeypatch.setattr(on, "receipt_evidence", MagicMock(side_effect=RuntimeError("boom")))
    with caplog.at_level(logging.INFO):
        await nr.settle(h.bridge, {"id": "u", "receipt": {}, "submission_started": True}, "t")
    h.rt.request_response.assert_awaited_once()
    assert get_disclosure_state(CALL) == "failed"
    assert any("recording_notice_repeat_error" in m and "error_type=RuntimeError" in m for m in msgs(caplog))


@pytest.mark.asyncio
async def test_a_failing_continuation_is_logged_and_never_raised(caplog):
    h, gate = await owed()
    gate.repeat_state, gate.repeat_epoch = "playing", h.rt._response_epoch
    h.rt.request_response.side_effect = RuntimeError("boom")
    with caplog.at_level(logging.INFO):
        await nr.settle(h.bridge, {"id": "u", "receipt": None, "submission_started": True}, "t")
    assert any("recording_notice_repeat_error" in m and "path=realtime" in m for m in msgs(caplog))


# F2
@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["xai", None])
async def test_a_provider_not_known_to_accept_the_repair_is_left_untouched(provider, caplog):
    h, gate = await owed()
    if provider is None:
        del h.rt.observation_provider
    else:
        h.rt.observation_provider = provider
    with caplog.at_level(logging.INFO):
        assert await nr.admit(h.bridge, reply("Next."), {"id": "rt-9"}) is True
    h.rt.repair_unspoken_response.assert_not_awaited()
    assert any("recording_notice_repeat_skipped" in m and "reason=provider_unverified" in m
               and "path=realtime" in m for m in msgs(caplog))
    assert gate.repeat_state == "skipped"


# F4
@pytest.mark.asyncio
async def test_sentence_reply_that_also_carries_a_tool_call_is_not_played_as_the_notice(caplog):
    h, gate = await owed()
    await feed_events(h, reply("Sure."))
    sent_before = len(h.sent)
    with caplog.at_level(logging.INFO):
        await feed_events(h, reply(gate.notice_sentence, rid="r3", output=TOOL_OUTPUT))
    assert len(h.sent) == sent_before
    assert h.rt.repair_unspoken_response.await_count == 2
    assert h.rt.repair_unspoken_response.await_args_list[1].kwargs.get("required_text") is None
    assert any("attempt=2" in m and "reason=text_mismatch" in m for m in msgs(caplog))
    assert gate.repeat_state == "done" and get_disclosure_state(CALL) == "failed"
    h.rt.request_response.assert_not_awaited()


# (a) a real barge-in event during the repeated sentence
@pytest.mark.asyncio
async def test_real_barge_in_during_the_repeated_sentence_is_unconfirmed_and_not_retried(caplog):
    h, gate = await owed()
    await feed_events(h, reply("Sure."))
    arm(h)

    async def events():
        yield reply(gate.notice_sentence, chunks=2, rid="r3")
        for _ in range(500):
            if h.st["n"] >= 2:
                break
            await asyncio.sleep(0)
        h.rt._response_epoch += 1  # what the session does first on a barge-in
        yield RealtimeEvent(kind="interrupted", raw={"reason": "speech_started", "item_id": "c1",
                                                     "during_response": False, "audio_parts": []})
        await asyncio.gather(h.bridge._playback_task, return_exceptions=True)
    h.bridge._rt.events = events
    with caplog.at_level(logging.INFO):
        await h.bridge._pump_model_events()
    disarm(h)
    assert any("recording_disclosure_unconfirmed" in m and "attempt=2 interrupted=True" in m
               and "path=realtime" in m for m in msgs(caplog))
    assert gate.repeat_state == "done" and get_disclosure_state(CALL) == "failed"
    h.rt.request_response.assert_not_awaited()
    h.bridge._caller_transcript_pending = False
    sent_before = len(h.sent)
    await feed_events(h, reply("Anything else?", rid="r5"))
    assert h.rt.repair_unspoken_response.await_count == 1  # never a third try
    assert len(h.sent) == sent_before + 1


# (b) the repair sent nothing because the person spoke meanwhile
@pytest.mark.asyncio
async def test_repair_that_sent_nothing_because_the_person_spoke_never_leaves_two_withheld_replies():
    h, gate = await owed()

    async def spoke_meanwhile(*a, **k):
        h.rt._response_epoch += 1  # the real repair returns without response.create
    h.rt.repair_unspoken_response.side_effect = spoke_meanwhile
    await feed_events(h, reply("Sure."))
    assert gate.repeat_state == "requested"
    sent_before = len(h.sent)
    # The provider answers the person's newer words: that reply plays, nothing else asked.
    await feed_events(h, reply("Answer to the newer words.", rid="r3"))
    assert len(h.sent) == sent_before + 1
    assert h.rt.repair_unspoken_response.await_count == 1
    h.rt.request_response.assert_not_awaited()
    assert gate.repeat_state == "done"
