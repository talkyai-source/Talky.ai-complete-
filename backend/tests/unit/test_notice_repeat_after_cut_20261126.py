"""A recording notice cut off on an outgoing call is said once more, at the next reply.

Production 25 Nov 2026, two calls: the person spoke over the opener ("... on a recorded
line ..."); the notice was never said again and the recording was stored "unconfirmed".
On one path the task speaking the notice was cancelled and nothing was logged at all.
"""
from __future__ import annotations

import asyncio
import logging
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.models.conversation import MessageRole
from app.domain.services import recording_policy_service as policy
from app.domain.services.telephony.modes.agent_first import _speak_recording_disclosure
from app.domain.services.voice_pipeline.first_line_guard import is_first_line_greeting_repeat
from app.domain.services.voice_pipeline.turn_runner import TurnRunner
from app.domain.services.end_session_action import previous_assistant_turn
from app.services.scripts.echo_guard import strip_self_echo_multi
from tests.unit.test_recording_notice_live_receipts import (
    CALL, FRAME_S16, DeployedGateway, _phone_call,
)

PLATFORM = policy.MINIMUM_DISCLOSURE_TEXT
DECISION = policy.RecordingDecision(
    True, True, None, None, 90, "tenant_policy_absent_default_announce"
)
OPENER = (
    "Hi, this is Sam, an AI assistant from Acme, "
    f"{PLATFORM.replace('This call is being recorded', 'on a recorded line')} Am I speaking with Joe?"
)


class _Provider:
    """The opener streams 6 s of audio (300 frames) when `slow`; any other text, 1 frame."""
    name = "synthetic"

    def __init__(self):
        self.slow = False
        self.long_texts = {OPENER}
        self.texts = []

    async def stream_synthesize(self, text, **kwargs):
        self.texts.append(text)
        for _ in range(300 if (self.slow and text in self.long_texts) else 1):
            await asyncio.sleep(0)
            yield SimpleNamespace(data=FRAME_S16)


async def _call(monkeypatch, *, slow=False, with_event=False, direction=None, decision=DECISION):
    monkeypatch.setattr(policy.RecordingPolicyService, "decide", AsyncMock(return_value=decision))
    original = DeployedGateway.__call__

    async def with_interrupt(self, method, path, payload=None, **kw):
        if path == "/v1/sessions/tts/interrupt":
            return {"dropped_frames": 0, "interrupted_segments": 1}
        return await original(self, method, path, payload, **kw)

    monkeypatch.setattr(DeployedGateway, "__call__", with_interrupt)
    voice, gateway, deployed = await _phone_call(monkeypatch)
    monkeypatch.setattr(policy.RecordingPolicyService, "decide", AsyncMock(return_value=decision))
    pipe, session = voice.pipeline, voice.call_session
    provider = _Provider()
    provider.slow = slow
    pipe.tts_provider = provider
    event = asyncio.Event()
    if with_event:
        playback = pipe.synthesize_and_send_audio.__self__
        gateway.set_barge_in_event(CALL, event)

        async def say(sess, text, websocket=None, **kw):
            return await playback.synthesize_and_send(sess, text, websocket, barge_in_event=event, **kw)

        pipe.synthesize_and_send_audio = say
    if direction:
        voice._call_direction = direction
    order = []
    pipe.transcript_service = MagicMock()
    pipe._supports_llm_end_session_action = lambda s: False

    async def stream(sess, ws):
        order.append("reply")
        return "Sure, go ahead.", 1.0, 1.0

    pipe._stream_llm_and_tts = stream
    session._voice_session_ref = voice
    session.talklee_call_id = None
    session._line_phone_checked = True
    return SimpleNamespace(voice=voice, pipe=pipe, session=session, provider=provider,
                           event=event, gateway=gateway, deployed=deployed, order=order)


async def _cut_opener(h, cut_after=0.3):
    task = asyncio.create_task(_speak_recording_disclosure(h.voice, opener_with_notice=OPENER))
    while h.deployed.accepted == 0:
        await asyncio.sleep(0.001)
    await asyncio.sleep(cut_after)
    h.event.set()
    await task
    h.event.clear()


async def _turn(h, text="Who is this?"):
    return await TurnRunner(h.pipe).run(h.session, text, None, 1)


def _roles(h):
    return [(m.role, m.content) for m in h.session.conversation_history]


@pytest.mark.asyncio
async def test_call_a_cut_notice_is_said_once_at_the_next_reply(monkeypatch):
    h = await _call(monkeypatch, slow=True, with_event=True)
    await _cut_opener(h)
    assert policy.get_disclosure_state(CALL) == "failed"
    assert h.voice._notice_repeat_text == PLATFORM
    await _turn(h)
    assert h.provider.texts == [OPENER, PLATFORM]
    assert h.order == ["reply"]
    assert policy.get_disclosure_state(CALL) == "transmitted"
    assert policy.notice_state(DECISION, CALL) == "delivered"


@pytest.mark.asyncio
async def test_call_b_cancelled_notice_is_failed_logged_and_owed(monkeypatch, caplog):
    h = await _call(monkeypatch, slow=True)
    caplog.set_level(logging.INFO)
    task = asyncio.create_task(_speak_recording_disclosure(h.voice, opener_with_notice=OPENER))
    await asyncio.sleep(0.5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert policy.get_disclosure_state(CALL) == "failed"
    assert any("recording_disclosure_unconfirmed" in r.getMessage() and "cancelled=True" in r.getMessage()
               for r in caplog.records if r.levelno == logging.WARNING)
    assert h.voice._notice_repeat_text == PLATFORM


@pytest.mark.asyncio
async def test_a_notice_delivered_whole_is_never_repeated(monkeypatch):
    h = await _call(monkeypatch)
    await _speak_recording_disclosure(h.voice, opener_with_notice=OPENER)
    assert policy.get_disclosure_state(CALL) == "transmitted"
    assert getattr(h.voice, "_notice_repeat_text", None) is None
    await _turn(h)
    assert h.provider.texts == [OPENER]


@pytest.mark.asyncio
async def test_an_unproven_receipt_without_a_cut_is_not_repeated(monkeypatch):
    h = await _call(monkeypatch)
    calls = []

    async def no_receipt(sess, text, websocket=None, **kw):
        calls.append(text)
        return False  # completed, but the phone system gave no receipt

    h.pipe.synthesize_and_send_audio = no_receipt
    await _speak_recording_disclosure(h.voice, opener_with_notice=OPENER)
    assert policy.get_disclosure_state(CALL) == "failed"
    assert getattr(h.voice, "_notice_repeat_text", None) is None
    await _turn(h)
    assert calls == [OPENER]


@pytest.mark.asyncio
async def test_the_repeat_is_tried_once_never_a_third_time(monkeypatch):
    h = await _call(monkeypatch, slow=True, with_event=True)
    await _cut_opener(h)
    calls = []

    async def cut_again(sess, text, websocket=None, **kw):
        calls.append(text)
        sess._last_tts_chunk_at = (getattr(sess, "_last_tts_chunk_at", None) or 0.0) + 1.0  # part of the sentence reached the line
        return True

    h.pipe.synthesize_and_send_audio = cut_again
    await _turn(h)
    assert calls == [PLATFORM]
    assert policy.get_disclosure_state(CALL) == "failed"
    assert policy.notice_state(DECISION, CALL) == "unconfirmed"
    await _turn(h)  # a reply re-issued after a false barge-in, and a later turn
    await _turn(h, "Tell me more.")
    assert calls == [PLATFORM]
    assert h.order == ["reply"] * 3


@pytest.mark.asyncio
@pytest.mark.parametrize("why", ["opted_out", "transfer_in_progress", "call_ended"])
async def test_no_repeat_on_opt_out_transfer_or_call_ending(monkeypatch, caplog, why):
    h = await _call(monkeypatch)
    policy.record_disclosure_state("failed", CALL)
    h.voice._notice_repeat_text = PLATFORM
    if why == "opted_out":
        h.session._caller_opted_out = True
    elif why == "transfer_in_progress":
        h.session._transfer_ringing_since = time.monotonic()
    else:
        h.voice._call_ending = True
    caplog.set_level(logging.INFO)
    await _turn(h)
    assert h.provider.texts == []
    assert h.order == ["reply"]
    assert policy.get_disclosure_state(CALL) == "failed"
    assert any(why in r.getMessage() for r in caplog.records if r.levelno == logging.INFO)


@pytest.mark.asyncio
async def test_inbound_is_untouched(monkeypatch):
    h = await _call(monkeypatch, slow=True, with_event=True, direction="inbound")
    await _cut_opener(h)
    assert policy.get_disclosure_state(CALL) == "failed"
    assert getattr(h.voice, "_notice_repeat_text", None) is None
    await _turn(h)
    assert h.provider.texts == [OPENER]


@pytest.mark.asyncio
async def test_a_bare_hello_does_not_cut_the_repeat_and_the_flag_is_restored_on_cancel(monkeypatch, caplog):
    h = await _call(monkeypatch)
    h.session._disclosure_in_flight = False
    policy.record_disclosure_state("failed", CALL)
    h.voice._notice_repeat_text = PLATFORM
    seen = []

    async def watched(sess, text, websocket=None, **kw):
        seen.append((getattr(sess, "_disclosure_in_flight", False),
                     is_first_line_greeting_repeat(sess, "Hello?")))
        sess._last_tts_chunk_at = (getattr(sess, "_last_tts_chunk_at", None) or 0.0) + 1.0  # part of the sentence reached the line
        raise asyncio.CancelledError

    h.pipe.synthesize_and_send_audio = watched
    with caplog.at_level("WARNING"), pytest.raises(asyncio.CancelledError):
        await _turn(h)
    assert seen == [(True, True)]
    assert h.session._disclosure_in_flight is False
    # a repeat cut off by its task being cancelled is never silent either
    cut = [r.getMessage() for r in caplog.records if "recording_disclosure_unconfirmed" in r.getMessage()]
    assert len(cut) == 1 and "attempt=2" in cut[0] and "cancelled=True" in cut[0]


@pytest.mark.asyncio
async def test_the_repeat_never_resets_the_recording_gate(monkeypatch):
    h = await _call(monkeypatch, slow=True, with_event=True)
    await _cut_opener(h)
    spy = MagicMock()
    h.gateway.set_recording_enabled = spy
    await _turn(h)
    assert h.provider.texts[-1] == PLATFORM
    spy.assert_not_called()


def _agent_lines_since_user(history):
    """The list turn_ender compares the next caller turn with (every agent line since the last USER)."""
    lines = []
    for m in reversed(history):
        if m.role == MessageRole.USER:
            break
        if m.role == MessageRole.ASSISTANT and m.content:
            lines.append(m.content)
    return lines


@pytest.mark.asyncio
async def test_history_holds_user_then_notice_so_its_echo_is_stripped_next_turn(monkeypatch):
    h = await _call(monkeypatch, slow=True, with_event=True)
    await _cut_opener(h)
    seen = []

    async def stream(sess, ws):
        seen.append([(m.role, m.content) for m in sess.conversation_history])
        return "Sure, go ahead.", 1.0, 1.0

    h.pipe._stream_llm_and_tts = stream
    await _turn(h, "Who is this?")
    # (a) the model's request for this turn ends with the person's words; the notice is not in it
    assert seen[0] == [(MessageRole.USER, "Who is this?")]
    # (b) afterwards: USER -> notice -> reply
    assert _roles(h) == [(MessageRole.USER, "Who is this?"), (MessageRole.ASSISTANT, PLATFORM),
                         (MessageRole.ASSISTANT, "Sure, go ahead.")]
    # (c) next turn: the real helpers see the right lines
    heard = "this call is being recorded for quality and training purposes"
    assert strip_self_echo_multi(heard, _agent_lines_since_user(h.session.conversation_history)).strip() == ""
    without = [m for m in h.session.conversation_history if m.content != PLATFORM]
    assert strip_self_echo_multi(heard, _agent_lines_since_user(without)).strip() != ""
    await _turn(h, "Okay.")
    assert seen[1][-1] == (MessageRole.USER, "Okay.")
    assert previous_assistant_turn(h.session.conversation_history) == "Sure, go ahead."


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["cancel", "event"])
async def test_a_notice_fully_handed_over_is_not_repeated_when_the_receipt_wait_is_cancelled(monkeypatch, path):
    h = await _call(monkeypatch, with_event=(path == "event"))
    h.deployed.pending_polls = 40  # the receipt says "pending" for a while
    task = asyncio.create_task(_speak_recording_disclosure(h.voice, opener_with_notice=OPENER))
    while not h.deployed.final:
        await asyncio.sleep(0.005)
    await asyncio.sleep(0.1)
    assert h.session._tts_delivery_status == "submitted"
    if path == "cancel":
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        h.event.set()
        await task
        h.event.clear()
    if path == "cancel":  # (on the event path the receipt may still arrive: then it is simply delivered)
        assert policy.get_disclosure_state(CALL) == "failed"
    assert getattr(h.voice, "_notice_repeat_text", None) is None
    await _turn(h)
    assert h.provider.texts == [OPENER]


def _notice_turns(h):
    return [c for c in h.pipe.transcript_service.accumulate_turn.call_args_list
            if c.kwargs.get("content") == PLATFORM]


@pytest.mark.asyncio
async def test_a_reply_cancelled_after_a_delivered_repeat_keeps_the_notice_in_order(monkeypatch):
    h = await _call(monkeypatch, slow=True, with_event=True)
    await _cut_opener(h)

    async def cancelled(sess, ws):
        raise asyncio.CancelledError

    h.pipe._stream_llm_and_tts = cancelled
    with pytest.raises(asyncio.CancelledError):
        await _turn(h, "Who is this?")
    assert _roles(h) == [(MessageRole.USER, "Who is this?"), (MessageRole.ASSISTANT, PLATFORM)]
    assert policy.get_disclosure_state(CALL) == "transmitted"
    seen = []

    async def stream(sess, ws):
        seen.append([(m.role, m.content) for m in sess.conversation_history])
        return "Fine.", 1.0, 1.0

    h.pipe._stream_llm_and_tts = stream
    await _turn(h, "Go on.")
    assert seen[0] == [(MessageRole.USER, "Who is this?"), (MessageRole.ASSISTANT, PLATFORM),
                       (MessageRole.USER, "Go on.")]
    assert len(_notice_turns(h)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("how", ["empty", "error"])
async def test_no_reply_after_a_delivered_repeat_is_handled_as_usual_and_the_notice_is_kept(monkeypatch, how):
    h = await _call(monkeypatch, slow=True, with_event=True)
    await _cut_opener(h)

    async def nothing(sess, ws):
        if how == "error":
            raise RuntimeError("synthetic model failure")
        return "", 1.0, 1.0

    h.pipe._stream_llm_and_tts = nothing
    await _turn(h, "Who is this?")
    # The usual rollback removes the person's words; the notice, which was SAID, stays as the
    # agent's last line (the next turn's model and echo strip need it), and the transcript has it.
    assert _roles(h) == [(MessageRole.ASSISTANT, PLATFORM)]
    assert policy.notice_state(DECISION, CALL) == "delivered"
    assert len(_notice_turns(h)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("words", ["Bye.", "Please do not call me again.", "Goodbye, thanks."])
async def test_a_goodbye_or_an_opt_out_is_not_answered_with_the_notice(monkeypatch, caplog, words):
    h = await _call(monkeypatch, slow=True, with_event=True)
    await _cut_opener(h)
    caplog.set_level(logging.INFO)
    await _turn(h, words)
    assert h.provider.texts == [OPENER]
    assert any("recording_notice_repeat_skipped" in r.getMessage()
               and ("reason=caller_ending" in r.getMessage() or "reason=opt_out_phrase" in r.getMessage())
               for r in caplog.records)


@pytest.mark.asyncio
async def test_the_repeated_notice_is_marked_in_the_transcript(monkeypatch):
    h = await _call(monkeypatch, slow=True, with_event=True)
    await _cut_opener(h)
    await _turn(h)
    turns = _notice_turns(h)
    assert len(turns) == 1
    assert turns[0].kwargs["metadata"] == {"recording_notice": "repeated_after_cut"}


@pytest.mark.asyncio
async def test_an_accounts_own_sentence_cut_is_owed_and_repeated_alone(monkeypatch):
    own = "Calls to this line may be recorded by Acme for training."
    decision = policy.RecordingDecision(True, True, own, None, 90, "tenant_policy_two_party_announce")
    h = await _call(monkeypatch, slow=True, with_event=True, decision=decision)
    h.provider.long_texts = {own}
    await _cut_opener(h)  # the account's sentence is spoken alone first (not inside the opener)
    assert h.provider.texts[0] == own
    assert h.voice._notice_repeat_text == own
    await _turn(h)
    assert h.provider.texts == [own, own]
    assert policy.get_disclosure_state(CALL) == "transmitted"


@pytest.mark.asyncio
async def test_llm_latency_excludes_the_repeat(monkeypatch):
    h = await _call(monkeypatch, slow=True, with_event=True)
    await _cut_opener(h)
    order = []
    real = h.pipe.synthesize_and_send_audio

    async def notice(sess, text, websocket=None, **kw):
        order.append("notice")
        return await real(sess, text, websocket, **kw)

    h.pipe.synthesize_and_send_audio = notice
    h.pipe.latency_tracker.mark_llm_start.side_effect = lambda cid: order.append(("mark", cid))

    async def stream(sess, ws):
        order.append("reply")
        return "Sure.", 1.0, 1.0

    h.pipe._stream_llm_and_tts = stream
    await _turn(h)
    assert order == ["notice", ("mark", CALL), "reply"]


@pytest.mark.asyncio
async def test_an_error_inside_the_repeat_never_breaks_the_turn(monkeypatch, caplog):
    h = await _call(monkeypatch, slow=True, with_event=True)
    await _cut_opener(h)
    calls = []

    async def boom(sess, text, websocket=None, **kw):
        calls.append(text)
        raise RuntimeError("synthetic provider failure")

    h.pipe.synthesize_and_send_audio = boom
    caplog.set_level(logging.INFO)
    await _turn(h)
    await _turn(h, "Tell me more.")
    warnings = [r.getMessage() for r in caplog.records
                if r.levelno == logging.WARNING and "error_type=RuntimeError" in r.getMessage()]
    assert len(warnings) == 1
    assert "synthetic provider failure" not in warnings[0]
    assert h.order == ["reply", "reply"]
    assert calls == [PLATFORM]


@pytest.mark.asyncio
async def test_a_bare_no_after_anything_else_is_a_goodbye_and_the_notice_is_not_repeated(monkeypatch, caplog):
    from app.domain.models.conversation import Message
    h = await _call(monkeypatch, slow=True, with_event=True)
    await _cut_opener(h)
    h.session.conversation_history.append(
        Message(role=MessageRole.ASSISTANT, content="Is there anything else I can help you with?"))
    caplog.set_level(logging.INFO)
    await _turn(h, "No.")
    assert h.provider.texts == [OPENER]
    assert any("recording_notice_repeat_skipped" in r.getMessage() and "reason=caller_ending" in r.getMessage()
               for r in caplog.records)


@pytest.mark.asyncio
async def test_a_bare_no_to_another_question_is_an_answer_and_the_notice_is_repeated(monkeypatch):
    from app.domain.models.conversation import Message
    h = await _call(monkeypatch, slow=True, with_event=True)
    await _cut_opener(h)
    h.session.conversation_history.append(
        Message(role=MessageRole.ASSISTANT, content="Am I speaking with John?"))
    await _turn(h, "No.")
    assert h.provider.texts == [OPENER, PLATFORM]


# --- a repeat that put NO audio on the line does not use up the one attempt ---------

async def _owed(monkeypatch):
    h = await _call(monkeypatch)
    policy.record_disclosure_state("failed", CALL)
    h.voice._notice_repeat_text = PLATFORM
    return h


def _messages(caplog, needle):
    return [r.getMessage() for r in caplog.records if needle in r.getMessage()]


@pytest.mark.asyncio
async def test_cancelled_with_no_audio_handed_over_is_tried_again(monkeypatch, caplog):
    h = await _owed(monkeypatch)
    calls = []

    async def cancelled_early(sess, text, websocket=None, **kw):
        calls.append(text)
        raise asyncio.CancelledError

    h.pipe.synthesize_and_send_audio = cancelled_early
    caplog.set_level(logging.INFO)
    with pytest.raises(asyncio.CancelledError):
        await _turn(h)
    assert h.voice._notice_repeat_done is False
    assert h.voice._notice_repeat_tries == 1
    assert getattr(h.session, "_disclosure_in_flight", False) is False  # restored on this path too
    deferred = _messages(caplog, "recording_notice_repeat_deferred")
    assert len(deferred) == 1 and "reason=no_audio_sent" in deferred[0] and "tries=1" in deferred[0]
    assert not _messages(caplog, "not tried again")
    with pytest.raises(asyncio.CancelledError):
        await _turn(h, "Hello?")
    assert calls == [PLATFORM, PLATFORM]


@pytest.mark.asyncio
async def test_cancelled_after_audio_was_handed_over_is_spent(monkeypatch, caplog):
    h = await _owed(monkeypatch)
    calls = []

    async def cancelled_late(sess, text, websocket=None, **kw):
        calls.append(text)
        sess._last_tts_chunk_at = (getattr(sess, "_last_tts_chunk_at", None) or 0.0) + 1.0  # tts_playback stamps this after each chunk
        raise asyncio.CancelledError

    h.pipe.synthesize_and_send_audio = cancelled_late
    caplog.set_level(logging.INFO)
    with pytest.raises(asyncio.CancelledError):
        await _turn(h)
    assert h.voice._notice_repeat_done is True
    assert not _messages(caplog, "recording_notice_repeat_deferred")
    assert len(_messages(caplog, "not tried again")) == 1
    await _turn(h, "Hello?")
    assert calls == [PLATFORM]


@pytest.mark.asyncio
async def test_returning_unproven_with_no_audio_is_tried_again(monkeypatch, caplog):
    h = await _owed(monkeypatch)
    calls = []

    async def failed_before_audio(sess, text, websocket=None, **kw):
        calls.append(text)
        sess._tts_delivery_status = "failed"  # tts_playback: provider failed before the first chunk
        return True

    h.pipe.synthesize_and_send_audio = failed_before_audio
    caplog.set_level(logging.INFO)
    await _turn(h)
    assert h.voice._notice_repeat_done is False
    assert len(_messages(caplog, "recording_notice_repeat_deferred")) == 1
    assert not _messages(caplog, "not tried again")
    await _turn(h, "Hello?")
    assert calls == [PLATFORM, PLATFORM]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["submitted", "partial", "weird", None])
async def test_an_unknown_or_audio_signal_means_spent(monkeypatch, caplog, status):
    h = await _owed(monkeypatch)
    calls = []

    async def unproven(sess, text, websocket=None, **kw):
        calls.append(text)
        sess._tts_delivery_status = status
        return False

    h.pipe.synthesize_and_send_audio = unproven
    caplog.set_level(logging.INFO)
    await _turn(h)
    assert h.voice._notice_repeat_done is True
    assert not _messages(caplog, "recording_notice_repeat_deferred")
    assert len(_messages(caplog, "not tried again")) == 1
    await _turn(h, "Hello?")
    assert calls == [PLATFORM]


@pytest.mark.asyncio
async def test_three_no_audio_tries_are_all_there_are(monkeypatch, caplog):
    h = await _owed(monkeypatch)
    calls = []

    async def failed_before_audio(sess, text, websocket=None, **kw):
        calls.append(text)
        sess._tts_delivery_status = "failed"
        return True

    h.pipe.synthesize_and_send_audio = failed_before_audio
    caplog.set_level(logging.INFO)
    for i in range(4):
        await _turn(h, f"Hello {i}?")
    assert calls == [PLATFORM] * 3
    assert h.voice._notice_repeat_tries == 3
    assert len(_messages(caplog, "recording_notice_repeat_deferred")) == 2
    assert len(_messages(caplog, "not tried again")) == 1


@pytest.mark.asyncio
async def test_a_retry_that_is_then_delivered_returns_the_sentence_and_ends_it(monkeypatch):
    h = await _owed(monkeypatch)
    real = h.pipe.synthesize_and_send_audio

    async def failed_before_audio(sess, text, websocket=None, **kw):
        sess._tts_delivery_status = "failed"
        return True

    h.pipe.synthesize_and_send_audio = failed_before_audio
    await _turn(h)
    assert h.voice._notice_repeat_done is False
    h.pipe.synthesize_and_send_audio = real
    await _turn(h, "Hello?")
    assert h.provider.texts == [PLATFORM]
    assert policy.get_disclosure_state(CALL) == "transmitted"
    assert _roles(h)[-2] == (MessageRole.ASSISTANT, PLATFORM)
    await _turn(h, "Tell me more.")
    assert h.provider.texts == [PLATFORM]


@pytest.mark.asyncio
async def test_a_fake_voice_without_the_counter_works():
    from app.domain.services.voice_pipeline.notice_repeat import repeat_cut_notice_once

    voice = SimpleNamespace(call_id="c1", _notice_repeat_text=PLATFORM)
    session = SimpleNamespace(call_id="c1", _voice_session_ref=voice, conversation_history=[])

    async def never(*a, **k):
        raise AssertionError("must not be reached")

    # skipped before the sentence is tried: the counter is never needed
    session._caller_opted_out = True
    assert await repeat_cut_notice_once(SimpleNamespace(synthesize_and_send_audio=never), session, None) is None
    assert not hasattr(voice, "_notice_repeat_tries")


# --- the REAL playback path: where exactly the cancel lands decides it ---------------

@pytest.mark.asyncio
async def test_real_path_a_cancel_before_the_voice_answers_is_tried_again_and_then_proven(monkeypatch, caplog):
    """Production 26 Nov 2026: the person was still talking. The voice provider had
    answered the request (its HTTP reply) but its first audio chunk had not arrived when
    the repeat was cancelled 5 ms later: the per-sentence ``TTS_FMT_DEBUG`` line, written
    when a first chunk arrives, is absent for that repeat, and the gateway dropped 0
    frames. No send was started, so the attempt is not spent."""
    h = await _owed(monkeypatch)
    waiting, release = asyncio.Event(), asyncio.Event()
    real_stream = h.provider.stream_synthesize

    async def late_voice(text, **kwargs):
        if not release.is_set():
            waiting.set()
            await release.wait()
        async for chunk in real_stream(text, **kwargs):
            yield chunk

    h.provider.stream_synthesize = late_voice
    frames_before = h.deployed.accepted
    caplog.set_level(logging.INFO)
    task = asyncio.create_task(_turn(h))
    await asyncio.wait_for(waiting.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert h.deployed.accepted == frames_before  # nothing reached the line
    assert h.voice._notice_repeat_done is False
    assert len(_messages(caplog, "recording_notice_repeat_deferred")) == 1
    assert not _messages(caplog, "not tried again")
    release.set()
    await _turn(h, "Hello?")
    assert h.provider.texts == [PLATFORM]
    assert policy.get_disclosure_state(CALL) == "transmitted"
    assert h.voice._notice_repeat_done is True


@pytest.mark.asyncio
async def test_real_path_a_cancel_inside_the_first_send_is_spent(monkeypatch, caplog):
    """A cancel that lands inside the very first send may already have put sound on the
    line (the gateway paces what it was given): the sentence is never started again."""
    h = await _owed(monkeypatch)
    entered = asyncio.Event()

    async def stuck_send(call_id, raw):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(h.pipe.media_gateway, "send_audio", stuck_send)
    caplog.set_level(logging.INFO)
    task = asyncio.create_task(_turn(h))
    await asyncio.wait_for(entered.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert h.session._tts_send_attempts >= 1
    assert h.voice._notice_repeat_done is True
    assert not _messages(caplog, "recording_notice_repeat_deferred")
    assert len(_messages(caplog, "not tried again")) == 1


@pytest.mark.asyncio
async def test_cancelled_while_waiting_in_the_skip_check_is_logged_and_spent(monkeypatch, caplog):
    from app.domain.services.telephony.modes import agent_first

    h = await _owed(monkeypatch)
    calls = []

    async def cancelled_wait(voice, call_id):
        raise asyncio.CancelledError

    async def never(sess, text, websocket=None, **kw):
        calls.append(text)

    monkeypatch.setattr(agent_first, "_notice_skip_reason", cancelled_wait)
    h.pipe.synthesize_and_send_audio = never
    caplog.set_level(logging.INFO)
    with pytest.raises(asyncio.CancelledError):
        await _turn(h)
    skipped = _messages(caplog, "recording_notice_repeat_skipped")
    assert len(skipped) == 1 and "reason=cancelled_before_try" in skipped[0]
    assert h.voice._notice_repeat_done is True  # stays spent
    assert calls == []
