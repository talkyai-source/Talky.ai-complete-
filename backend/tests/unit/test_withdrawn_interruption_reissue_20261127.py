"""An interruption that AssemblyAI withdraws (empty turn) must not leave silence.

The barge-in cancels the reply on the first partial; if AssemblyAI then closes
the turn with no words, the handler drops it and nothing answered again. The
old recovery never fires on AssemblyAI because the barge-in's own partial
stamps `_caller_last_text_at`.
"""
from __future__ import annotations

import asyncio
import logging
import time
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.models.conversation import Message, MessageRole
from app.domain.models.session import CallSession, CallState
from app.domain.services.voice_pipeline.transcript_handler import TranscriptHandler
from app.domain.services.voice_pipeline_service import VoicePipelineService

TEXT = "Hello?"


class _Chunk:
    def __init__(self, text="", is_final=True, metadata=None):
        self.text = text
        self.is_final = is_final
        self.confidence = None
        self.metadata = metadata or {}


class _STT:
    @staticmethod
    def detect_turn_end(chunk) -> bool:
        return bool(chunk.is_final) and not chunk.text


def _empty_final():
    return _Chunk(text="", is_final=True, metadata={"provider": "assemblyai", "empty_turn": True})


def _service():
    svc = VoicePipelineService(
        stt_provider=AsyncMock(),
        llm_provider=AsyncMock(),
        tts_provider=AsyncMock(),
        media_gateway=AsyncMock(),
    )
    svc.stt_provider = _STT()
    svc.handle_turn_end = AsyncMock()
    return svc


def _session():
    s = CallSession(
        call_id="call-withdrawn-1234567",
        campaign_id="campaign-1",
        lead_id="lead-1",
        provider_call_id="provider-1",
        system_prompt="x",
        voice_id="v",
    )
    s.state = CallState.LISTENING
    s._accepted_caller_turn_order = 1
    return s


def _arm_cut(svc, s, text=TEXT, prior=()):
    """A pending final reply for `text`, with the opener playing."""
    s.conversation_history = [Message(role=MessageRole.USER, content=text)]
    svc._barge_in_events.setdefault(s.call_id, asyncio.Event())
    task = asyncio.create_task(asyncio.sleep(30))
    task._turn_type = "final"
    task._source_text = text
    task._caller_turn_order = 1
    task._prior_caller_turn_orders = prior
    svc._pending_llm_tasks[s.call_id] = task
    s.tts_active = True


async def _cut_then_partial(svc, s, text=TEXT, stamp=True, prior=()):
    _arm_cut(svc, s, text, prior)
    await svc.handle_barge_in(s, None, "This")
    if stamp:
        # the real AssemblyAI partial stamps this right after the barge-in signal
        s._caller_last_text_at = time.monotonic() + 0.001


async def _cleanup():
    me = asyncio.current_task()
    for t in asyncio.all_tasks():
        if t is not me:
            t.cancel()
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_a_withdrawn_interruption_reissues_the_cut_reply(monkeypatch, caplog):
    monkeypatch.setenv("VOICE_FALSE_BARGE_IN_WINDOW_S", "30")
    caplog.set_level(logging.INFO)
    svc, s = _service(), _session()
    await _cut_then_partial(svc, s)
    await TranscriptHandler(svc).handle(s, _empty_final())
    await asyncio.sleep(0)
    svc.handle_turn_end.assert_awaited_once()
    assert svc.handle_turn_end.await_args.kwargs["source"] == "final"
    assert svc.handle_turn_end.await_args.kwargs["user_text"] == TEXT
    # NOTE: the re-issued task drops the kept USER copy when it starts (the
    # wrapper), and this mocked handle_turn_end does not re-append it, so zero
    # copies remain. The real turn re-appends it. In production the turn
    # runner's cancel path has usually appended an assistant line after the
    # kept message, so the copy is NOT dropped and the re-run appends a
    # duplicate (harmless).
    assert [m for m in s.conversation_history if m.role == MessageRole.USER and m.content == TEXT] == []
    assert caplog.text.count("withdrawn_interruption_reissued") == 1
    await _cleanup()


@pytest.mark.asyncio
async def test_the_old_timer_and_the_withdrawn_path_give_one_reissue_between_them(monkeypatch):
    # No stamp after the cut: the old 2 s timer is eligible to fire as well.
    monkeypatch.setenv("VOICE_FALSE_BARGE_IN_WINDOW_S", "0.05")
    svc, s = _service(), _session()
    await _cut_then_partial(svc, s, stamp=False)
    await TranscriptHandler(svc).handle(s, _empty_final())
    await asyncio.sleep(0.3)
    assert svc.handle_turn_end.await_count == 1
    await _cleanup()


@pytest.mark.asyncio
async def test_successive_withdrawn_cuts_share_the_cap(monkeypatch, caplog):
    monkeypatch.setenv("VOICE_FALSE_BARGE_IN_WINDOW_S", "30")
    caplog.set_level(logging.INFO)
    svc, s = _service(), _session()
    for _ in range(3):
        svc._pending_llm_tasks.pop(s.call_id, None)
        s.tts_active = False
        s._last_interrupt_at = None  # rounds are seconds apart; skip the 0.35 s dedupe
        await _cut_then_partial(svc, s)
        await TranscriptHandler(svc).handle(s, _empty_final())
        await asyncio.sleep(0)
    assert svc.handle_turn_end.await_count == 2
    assert caplog.text.count("withdrawn_interruption_reissued") == 2
    assert "withdrawn_interruption_not_reissued" in caplog.text
    assert "reason=capped" in caplog.text
    await _cleanup()


@pytest.mark.asyncio
async def test_nothing_stored_means_no_reissue_and_no_log_line(caplog):
    caplog.set_level(logging.INFO)
    svc, s = _service(), _session()
    svc._barge_in_events[s.call_id] = asyncio.Event()
    await TranscriptHandler(svc).handle(s, _empty_final())
    await asyncio.sleep(0)
    svc.handle_turn_end.assert_not_awaited()
    assert "withdrawn_interruption" not in caplog.text


def _stored(s, barge_at=None, order=1):
    from app.domain.services.voice_pipeline.withdrawn_interruption import remember_cut_reply

    remember_cut_reply(
        s, {"text": TEXT, "type": "final", "caller_turn_order": order},
        time.monotonic() if barge_at is None else barge_at,
    )


def _gw(**probes):
    return types.SimpleNamespace(**probes)


def _setup_reason(reason, svc, s, monkeypatch):
    """Arrange ONE blocking condition; returns (barge_at, after_store_fn)."""
    from app.domain.services.voice_pipeline.action_execution import TRANSFER_MARKER

    barge_at, after = None, None
    if reason == "switched_off":
        monkeypatch.setenv("VOICE_WITHDRAWN_REISSUE_ENABLED", "0")
    elif reason == "inbound":
        s._call_direction = "inbound"
    elif reason == "too_old":
        barge_at = time.monotonic() - 1000
    elif reason == "session_gone":
        svc._barge_in_events.clear()
    elif reason == "state_ending":
        s.state = CallState.ENDING
    elif reason == "state_ended":
        s.state = CallState.ENDED
    elif reason == "state_error":
        s.state = CallState.ERROR
    elif reason == "end_session_action":
        s._end_session_action_handled = True
    elif reason == "end_call_requested":
        s._end_call_requested = True
    elif reason == "voice_call_ending":
        s._voice_session_ref = types.SimpleNamespace(_call_ending=True)
    elif reason == "gateway_inactive":
        svc.media_gateway = _gw(is_session_active=lambda cid: False)
    elif reason == "gateway_hangup_requested":
        svc.media_gateway = _gw(is_hangup_requested=lambda cid: True)
    elif reason == "gateway_hangup_pending":
        svc.media_gateway = _gw(is_hangup_pending=lambda cid: True)
    elif reason == "transfer_marker":
        setattr(s, TRANSFER_MARKER, time.monotonic())
    elif reason == "transfer_connected":
        s._voice_session_ref = types.SimpleNamespace(_transfer_connected=True)
    elif reason == "opted_out":
        s._caller_opted_out = True
    elif reason == "voicemail":
        s._amd_voicemail = True
    elif reason == "screening":
        s._machine_screening = True
    elif reason == "newer_turn":
        s._accepted_caller_turn_order = 2
    elif reason == "running_task":
        svc._pending_llm_tasks[s.call_id] = asyncio.create_task(asyncio.sleep(30))
    elif reason == "tts_active":
        s.tts_active = True
    elif reason == "llm_active":
        s.llm_active = True
    elif reason == "agent_spoke_since":
        def after():  # set AFTER the store, as the real chunk would be
            s._last_tts_chunk_at = time.monotonic() + 5
    return barge_at, after


_REASONS = {
    "switched_off": "switched_off", "inbound": "inbound", "too_old": "too_old",
    "session_gone": "session_gone",
    "state_ending": "call_ending", "state_ended": "call_ending", "state_error": "call_ending",
    "end_session_action": "call_ending", "end_call_requested": "call_ending",
    "voice_call_ending": "call_ending", "gateway_inactive": "call_ending",
    "gateway_hangup_requested": "call_ending", "gateway_hangup_pending": "call_ending",
    "transfer_marker": "transfer", "transfer_connected": "transfer",
    "opted_out": "opted_out", "voicemail": "voicemail", "screening": "voicemail",
    "newer_turn": "newer_turn",
    "running_task": "reply_running", "tts_active": "reply_running", "llm_active": "reply_running",
    "agent_spoke_since": "agent_spoke_since",
}


@pytest.mark.asyncio
@pytest.mark.parametrize("case", list(_REASONS))
async def test_each_skip_reason_blocks_the_reissue(case, monkeypatch, caplog):
    from app.domain.services.voice_pipeline.withdrawn_interruption import (
        reissue_after_withdrawn_turn,
    )

    caplog.set_level(logging.INFO)
    svc, s = _service(), _session()
    svc._barge_in_events[s.call_id] = asyncio.Event()
    barge_at, after = _setup_reason(case, svc, s, monkeypatch)
    _stored(s, barge_at)
    if after:
        after()
    assert await reissue_after_withdrawn_turn(svc, s, None) is False
    await asyncio.sleep(0)
    svc.handle_turn_end.assert_not_awaited()
    assert caplog.text.count("withdrawn_interruption_not_reissued") == 1
    assert f"reason={_REASONS[case]}" in caplog.text
    assert s._withdrawn_cut_reply is None
    running = svc._pending_llm_tasks.get(s.call_id)
    if running:
        running.cancel()


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["none", "string", "bool", "missing", "not_a_dict"])
async def test_malformed_stored_values_are_consumed_without_raising(bad, caplog):
    from app.domain.services.voice_pipeline.withdrawn_interruption import (
        reissue_after_withdrawn_turn,
    )

    caplog.set_level(logging.INFO)
    svc, s = _service(), _session()
    svc._barge_in_events[s.call_id] = asyncio.Event()
    good = {"text": TEXT, "caller_turn_order": 1}
    s._withdrawn_cut_reply = {
        "none": {**good, "barge_at": None},
        "string": {**good, "barge_at": "x"},
        "bool": {**good, "barge_at": True},
        "missing": dict(good),
        "not_a_dict": "oops",
    }[bad]
    assert await reissue_after_withdrawn_turn(svc, s, None) is False
    await asyncio.sleep(0)
    svc.handle_turn_end.assert_not_awaited()
    assert s._withdrawn_cut_reply is None
    if bad != "not_a_dict":
        assert "reason=malformed" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["nan", "inf"])
async def test_a_bad_max_age_falls_back_to_the_default(value, monkeypatch, caplog):
    from app.domain.services.voice_pipeline.withdrawn_interruption import (
        reissue_after_withdrawn_turn,
    )

    caplog.set_level(logging.INFO)
    monkeypatch.setenv("VOICE_WITHDRAWN_REISSUE_MAX_AGE_S", value)
    svc, s = _service(), _session()
    svc._barge_in_events[s.call_id] = asyncio.Event()
    _stored(s, time.monotonic() - 1000)
    assert await reissue_after_withdrawn_turn(svc, s, None) is False
    assert "reason=too_old" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["-1", "0"])
async def test_a_non_positive_max_age_falls_back_to_eight_seconds(value, monkeypatch):
    from app.domain.services.voice_pipeline.withdrawn_interruption import (
        reissue_after_withdrawn_turn,
    )

    monkeypatch.setenv("VOICE_WITHDRAWN_REISSUE_MAX_AGE_S", value)
    svc, s = _service(), _session()
    svc._barge_in_events[s.call_id] = asyncio.Event()
    _stored(s, time.monotonic())  # fresh: only the 8 s fallback lets it through
    assert await reissue_after_withdrawn_turn(svc, s, None) is True
    await asyncio.sleep(0)
    svc.handle_turn_end.assert_awaited_once()
    await _cleanup()


@pytest.mark.asyncio
async def test_a_reissue_cancelled_before_it_starts_keeps_the_callers_question():
    from app.domain.services.voice_pipeline.withdrawn_interruption import (
        reissue_after_withdrawn_turn,
    )

    svc, s = _service(), _session()
    svc._barge_in_events[s.call_id] = asyncio.Event()
    s.conversation_history = [Message(role=MessageRole.USER, content=TEXT)]
    _stored(s)
    assert await reissue_after_withdrawn_turn(svc, s, None) is True
    task = svc._pending_llm_tasks[s.call_id]  # the loop has not run it yet
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert [(m.role, m.content) for m in s.conversation_history] == [(MessageRole.USER, TEXT)]
    svc.handle_turn_end.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_started_reissue_does_not_duplicate_the_callers_question():
    from app.domain.services.voice_pipeline.withdrawn_interruption import (
        reissue_after_withdrawn_turn,
    )

    svc, s = _service(), _session()
    svc._barge_in_events[s.call_id] = asyncio.Event()
    s.conversation_history = [Message(role=MessageRole.USER, content=TEXT)]

    async def real_like_turn(session, websocket, **kw):
        session.conversation_history.append(Message(role=MessageRole.USER, content=kw["user_text"]))

    svc.handle_turn_end = real_like_turn
    _stored(s)
    assert await reissue_after_withdrawn_turn(svc, s, None) is True
    await svc._pending_llm_tasks[s.call_id]
    assert [(m.role, m.content) for m in s.conversation_history] == [(MessageRole.USER, TEXT)]


@pytest.mark.asyncio
async def test_the_tail_of_the_cut_reply_does_not_block_the_reissue():
    from app.domain.services.voice_pipeline.withdrawn_interruption import (
        reissue_after_withdrawn_turn,
    )

    svc, s = _service(), _session()
    svc._barge_in_events[s.call_id] = asyncio.Event()
    barge_at = time.monotonic()
    _stored(s, barge_at)
    s._last_tts_chunk_at = barge_at + 0.05  # what every real cut leaves behind
    assert await reissue_after_withdrawn_turn(svc, s, None) is True
    await asyncio.sleep(0)
    svc.handle_turn_end.assert_awaited_once()
    assert svc.handle_turn_end.await_args.kwargs["user_text"] == TEXT
    await _cleanup()


@pytest.mark.asyncio
async def test_audio_sent_well_after_the_cut_blocks_the_reissue(caplog):
    from app.domain.services.voice_pipeline.withdrawn_interruption import (
        reissue_after_withdrawn_turn,
    )

    caplog.set_level(logging.INFO)
    svc, s = _service(), _session()
    svc._barge_in_events[s.call_id] = asyncio.Event()
    barge_at = time.monotonic()
    _stored(s, barge_at)
    s._last_tts_chunk_at = barge_at + 0.6
    assert await reissue_after_withdrawn_turn(svc, s, None) is False
    svc.handle_turn_end.assert_not_awaited()
    assert "reason=agent_spoke_since" in caplog.text


@pytest.mark.asyncio
async def test_real_words_after_the_cut_are_answered_as_before(monkeypatch, caplog):
    monkeypatch.setenv("VOICE_FALSE_BARGE_IN_WINDOW_S", "30")
    caplog.set_level(logging.INFO)
    svc, s = _service(), _session()
    await _cut_then_partial(svc, s)
    handler = TranscriptHandler(svc)
    # What AssemblyAI sends for a turn WITH words: the text, then the empty
    # end marker WITHOUT empty_turn (assemblyai.py: text chunk, then marker).
    await handler.handle(s, _Chunk(text="I need a quote", is_final=True,
                                   metadata={"provider": "assemblyai"}))
    await handler.handle(s, _Chunk(text="", is_final=True,
                                   metadata={"provider": "assemblyai"}))
    await asyncio.sleep(0)
    assert svc.handle_turn_end.await_count == 1
    assert svc.handle_turn_end.await_args.kwargs["user_text"] == "I need a quote"
    assert s._accepted_caller_turn_order == 2
    # The cut is then withdrawn by a later empty turn: the newer turn wins.
    await handler.handle(s, _empty_final())
    await asyncio.sleep(0)
    assert svc.handle_turn_end.await_count == 1
    assert "reason=newer_turn" in caplog.text
    await _cleanup()


@pytest.mark.asyncio
async def test_a_new_barge_in_cancels_the_reissued_reply(monkeypatch, caplog):
    monkeypatch.setenv("VOICE_FALSE_BARGE_IN_WINDOW_S", "30")
    caplog.set_level(logging.INFO)
    svc, s = _service(), _session()
    gate = asyncio.Event()

    async def _slow(*a, **k):
        await gate.wait()

    svc.handle_turn_end = AsyncMock(side_effect=_slow)
    await _cut_then_partial(svc, s)
    await TranscriptHandler(svc).handle(s, _empty_final())
    await asyncio.sleep(0)
    reissued = svc._pending_llm_tasks[s.call_id]
    assert reissued._withdrawn_reissue is True and not reissued.done()
    caplog.clear()
    s.tts_active = False
    s._last_interrupt_at = None  # skip the 0.35 s dedupe of the first cut
    await svc.handle_barge_in(s, None, "actually I")
    await asyncio.sleep(0.05)
    assert reissued.cancelled() or reissued.done()
    assert svc._pending_llm_tasks.get(s.call_id) is not reissued
    assert "barge_in_ignored_final_pre_tts" not in caplog.text
    gate.set()
    await _cleanup()


@pytest.mark.asyncio
async def test_an_ordinary_pending_final_reply_is_still_protected(monkeypatch, caplog):
    monkeypatch.setenv("VOICE_FALSE_BARGE_IN_WINDOW_S", "30")
    caplog.set_level(logging.INFO)
    svc, s = _service(), _session()
    svc._barge_in_events[s.call_id] = asyncio.Event()
    task = asyncio.create_task(asyncio.sleep(30))
    task._turn_type = "final"
    svc._pending_llm_tasks[s.call_id] = task
    s.tts_active = False
    await svc.handle_barge_in(s, None, "actually I")
    assert "barge_in_ignored_final_pre_tts" in caplog.text
    assert not task.cancelled()
    await _cleanup()


@pytest.mark.asyncio
async def test_the_reissued_task_carries_the_prior_turn_orders(monkeypatch):
    monkeypatch.setenv("VOICE_FALSE_BARGE_IN_WINDOW_S", "30")
    svc, s = _service(), _session()
    await _cut_then_partial(svc, s, prior=(3,))
    await TranscriptHandler(svc).handle(s, _empty_final())
    task = svc._pending_llm_tasks[s.call_id]
    assert task._prior_caller_turn_orders == (3,)
    assert task._withdrawn_reissue is True
    await asyncio.sleep(0)
    await _cleanup()


@pytest.mark.asyncio
async def test_the_withdrawn_words_never_reach_the_model(monkeypatch):
    monkeypatch.setenv("VOICE_FALSE_BARGE_IN_WINDOW_S", "30")
    svc, s = _service(), _session()
    await _cut_then_partial(svc, s)
    s.current_user_input = "This"
    await TranscriptHandler(svc).handle(s, _empty_final())
    await asyncio.sleep(0)
    for call in svc.handle_turn_end.await_args_list:
        assert call.kwargs["user_text"] == TEXT
        assert "This" not in call.kwargs["user_text"]
    assert svc.handle_turn_end.await_count == 1
    await _cleanup()


def test_remember_cut_reply_ignores_a_cut_with_no_text_or_a_non_final_turn():
    from app.domain.services.voice_pipeline.withdrawn_interruption import remember_cut_reply

    s = _session()
    remember_cut_reply(s, {}, 1.0)
    remember_cut_reply(s, {"text": "", "type": "final"}, 1.0)
    remember_cut_reply(s, {"text": TEXT, "type": "speculative"}, 1.0)
    assert getattr(s, "_withdrawn_cut_reply", None) is None
    remember_cut_reply(s, {"text": TEXT, "type": "final", "caller_turn_order": 3}, 5.0)
    assert s._withdrawn_cut_reply["text"] == TEXT
    assert s._withdrawn_cut_reply["caller_turn_order"] == 3
    assert s._withdrawn_cut_reply["barge_at"] == 5.0
    assert s._withdrawn_cut_reply["prior_caller_turn_orders"] == ()
    # a non-iterable prior-orders value is stored as () and nothing raises
    remember_cut_reply(
        s, {"text": TEXT, "type": "final", "prior_caller_turn_orders": 5}, 6.0)
    assert s._withdrawn_cut_reply["prior_caller_turn_orders"] == ()
    assert s._withdrawn_cut_reply["barge_at"] == 6.0


# ── The first turn, through the REAL turn runner ────────────────────────────
# Real: reissue_after_withdrawn_turn, TurnRunner.run (the turn function the
# notice-repeat tests drive), repeat_cut_notice_once, the opener/notice speaker,
# the recording-policy disclosure state and the deployed-gateway receipt harness
# of test_notice_repeat_after_cut_20261126. Stubbed: the pipeline object (that
# harness uses a SimpleNamespace, not VoicePipelineService), so the pipeline's
# handle_turn_end is a thin function that records its call and runs
# TurnRunner.run (NOT the real TurnEnder.handle, whose turn-0 floor needs a real
# CallSession); the TTS provider (records texts); the model stream (appends
# "reply"); the transcript service. Neither test depends on this batch's code
# beyond the new module, so on the code before this batch they could only fail
# for lack of that module.

async def _reissued_first_turn(monkeypatch, *, proving=True):
    from tests.unit.test_notice_repeat_after_cut_20261126 import (
        CALL, OPENER, PLATFORM, _call, _cut_opener, policy,
    )
    from app.domain.services.voice_pipeline.turn_runner import TurnRunner
    from app.domain.services.voice_pipeline.withdrawn_interruption import (
        reissue_after_withdrawn_turn, remember_cut_reply,
    )

    h = await _call(monkeypatch, slow=True, with_event=True)
    await _cut_opener(h)
    assert policy.get_disclosure_state(CALL) == "failed"
    assert h.voice._notice_repeat_text == PLATFORM
    s = h.session
    # the state the cancel path leaves
    s._caller_first_disclosure_done = True
    s.conversation_history = [Message(role=MessageRole.USER, content=TEXT)]
    s._accepted_caller_turn_order = 1
    s.tts_active = False
    calls = []

    async def handle_turn_end(session, websocket, **kw):
        calls.append(kw)
        return await TurnRunner(h.pipe).run(session, kw["user_text"], None, 1)

    pipeline = types.SimpleNamespace(
        _barge_in_events={CALL: asyncio.Event()}, _pending_llm_tasks={}, _utterance_seq={},
        media_gateway=h.pipe.media_gateway, handle_turn_end=handle_turn_end,
    )
    recorded = []
    if not proving:
        async def no_receipt(sess, text, websocket=None, **kw):
            recorded.append(text)
            return False  # completed, but the phone system gave no receipt

        h.pipe.synthesize_and_send_audio = no_receipt
    gate = MagicMock()  # spy on the REAL gateway's recording gate
    h.gateway.set_recording_enabled = gate
    remember_cut_reply(s, {"text": TEXT, "type": "final", "caller_turn_order": 1}, time.monotonic())
    assert await reissue_after_withdrawn_turn(pipeline, s, None) is True
    await pipeline._pending_llm_tasks[CALL]
    assert calls == [{"source": "final", "user_text": TEXT}]
    return h, gate, recorded, (CALL, OPENER, PLATFORM, policy)


@pytest.mark.asyncio
async def test_a_reissued_first_turn_says_the_notice_once_then_answers(monkeypatch):
    h, gate, _, (CALL, OPENER, PLATFORM, policy) = await _reissued_first_turn(monkeypatch)
    # 1 + 2: the opener is not played again; the notice sentence exactly once.
    assert h.provider.texts == [OPENER, PLATFORM]
    # 3: then the model's reply
    assert h.order == ["reply"]
    # 4: the recording gate is never touched
    gate.assert_not_called()
    # 5: delivered, because the receipt proved it
    assert policy.get_disclosure_state(CALL) == "transmitted"
    assert [m.content for m in h.session.conversation_history if m.role == MessageRole.USER] == [TEXT]


@pytest.mark.asyncio
async def test_a_reissued_first_turn_without_a_proving_receipt_is_not_delivered(monkeypatch):
    h, gate, recorded, (CALL, OPENER, PLATFORM, policy) = await _reissued_first_turn(
        monkeypatch, proving=False)
    assert recorded == [PLATFORM]          # the notice was tried exactly once
    assert h.provider.texts == [OPENER]    # the opener sentence was not played again
    assert h.order == ["reply"]
    gate.assert_not_called()
    assert policy.get_disclosure_state(CALL) == "failed"
