"""DNC receipt admission through actual native pump/playback, synthetic ports only."""
import asyncio
import base64
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.domain.services.dialer.opt_out import OPT_OUT_UNCONFIRMED_FAREWELL
from app.realtime.openai import OpenAIRealtimeSession, RealtimeEvent
from app.realtime.xai import XAIRealtimeSession
from tests.unit.test_realtime_end_call_ownership import _events, _fixture

CONTINUE = "I cannot confirm that your do-not-call request was saved."
CLAIM = "I've removed your number from our calling list. Goodbye."
DNC = "Do not call me again, but first I need help with my account."


def candidate(text=CLAIM, response_id="original"):
    return RealtimeEvent(kind="response_candidate", text=text, audio=b"\xff" * 320,
                         raw={"response": {"id": response_id, "output": []}})


def caller(text=DNC):
    return RealtimeEvent(kind="caller_transcript", text=text, is_final=True)


@pytest.fixture
def setup(monkeypatch):
    bridge, provider, gateway, ended, session = _fixture()
    provider.repair_unspoken_response = AsyncMock()
    gateway.send_control_event = AsyncMock()
    gateway.finish_playback = AsyncMock(side_effect=lambda _call, utterance: {
        "utterance_id": utterance, "status": "completed",
        "evidence": "transport_played", "played_ms": 40})
    session.call_id = "synthetic-close"
    persistence = AsyncMock(return_value=False)
    monkeypatch.setattr("app.domain.services.dialer.opt_out.purge_opt_out_before_farewell", persistence)
    return SimpleNamespace(bridge=bridge, provider=provider, gateway=gateway,
                           ended=ended, session=session, persistence=persistence)


@pytest.mark.asyncio
@pytest.mark.parametrize("result", [False, None])
async def test_failed_or_unknown_dnc_never_submits_original_audio_or_control(setup, result):
    s = setup
    s.persistence.return_value = result
    try:
        await _events(s.bridge, s.provider, caller(), candidate())
        s.gateway.send_audio.assert_not_awaited()
        s.gateway.begin_playback.assert_not_awaited()
        assert not any(c.args[1].get("type") == "llm_response"
                       for c in s.gateway.send_control_event.await_args_list)
        s.provider.repair_unspoken_response.assert_awaited_once_with(
            {"id": "original", "output": []}, required_text=CONTINUE)
        s.ended.assert_not_awaited()
    finally:
        await s.bridge.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("input_text,safe_text,closes", [
    (DNC, CONTINUE, False),
    ("Do not call me again. Goodbye.", OPT_OUT_UNCONFIRMED_FAREWELL, True),
])
async def test_only_exact_safe_repair_plays_and_current_caller_owns_close(setup, input_text, safe_text, closes):
    s = setup
    try:
        await _events(s.bridge, s.provider, caller(input_text), candidate())
        assert s.gateway.send_audio.await_count == 0
        await _events(s.bridge, s.provider, candidate(safe_text, "repair"))
        s.gateway.send_audio.assert_awaited_once()
        s.provider.repair_unspoken_response.assert_awaited_once()
        if closes:
            await asyncio.wait_for(s.bridge._termination_task, 1)
            s.ended.assert_awaited_once()
        else:
            assert s.bridge._termination_task is None
            s.ended.assert_not_awaited()
    finally:
        await s.bridge.stop()


@pytest.mark.asyncio
async def test_wrong_second_candidate_is_withheld_without_repair_loop(setup):
    s = setup
    try:
        await _events(s.bridge, s.provider, caller(), candidate())
        await _events(s.bridge, s.provider, candidate(CONTINUE + " You have been removed.", "bad-repair"))
        s.gateway.send_audio.assert_not_awaited()
        s.provider.repair_unspoken_response.assert_awaited_once()
        assert s.bridge._failure_reason
        s.ended.assert_not_awaited()
    finally:
        await s.bridge.stop()


@pytest.mark.asyncio
async def test_acknowledged_dnc_permits_normal_validated_response(setup):
    s = setup
    s.persistence.return_value = True
    try:
        await _events(s.bridge, s.provider, caller(), candidate("Your number is on our do-not-call list. How can I help?"))
        s.gateway.send_audio.assert_awaited_once()
        s.provider.repair_unspoken_response.assert_not_awaited()
        s.ended.assert_not_awaited()
    finally:
        await s.bridge.stop()


@pytest.mark.asyncio
async def test_new_accepted_turn_retries_failed_write_once_then_restores_normal_speech(setup):
    s = setup
    s.persistence.side_effect = [False, True]
    try:
        await _events(s.bridge, s.provider, caller(), candidate())
        await _events(s.bridge, s.provider, candidate(CONTINUE, "safe-repair"))
        s.gateway.send_audio.reset_mock()
        await _events(s.bridge, s.provider, caller("Can you help with my account now?"),
                      candidate("Yes. What do you need help with?", "next-turn"))
        assert s.persistence.await_count == 2
        s.gateway.send_audio.assert_awaited_once()
        assert s.provider.repair_unspoken_response.await_count == 1
        s.ended.assert_not_awaited()
    finally:
        await s.bridge.stop()


@pytest.mark.asyncio
async def test_held_dnc_does_not_block_pump_or_allow_stale_ack_to_play(setup):
    s = setup
    started, release = asyncio.Event(), asyncio.Event()

    async def held(_session):
        started.set()
        await release.wait()
        return True

    s.persistence.side_effect = held
    try:
        await _events(s.bridge, s.provider, caller())
        await asyncio.wait_for(started.wait(), 1)

        async def events():
            yield candidate()

        s.provider.events = events
        await s.bridge._pump_model_events()  # Must return while the DB port is held.
        original_playback = s.bridge._playback_task
        await asyncio.sleep(0)
        s.gateway.send_audio.assert_not_awaited()
        await _events(s.bridge, s.provider,
                      RealtimeEvent(kind="interrupted", raw={"reason": "speech_started"}),
                      caller("Wait, I have a different question."))
        release.set()
        await s.bridge._opt_out_task
        await asyncio.gather(original_playback, return_exceptions=True)
        s.gateway.send_audio.assert_not_awaited()
        s.ended.assert_not_awaited()
        assert s.persistence.await_count == 1
    finally:
        release.set()
        await s.bridge.stop()


@pytest.mark.asyncio
async def test_interrupted_unresolved_repair_cannot_become_new_turn_audio(setup):
    s = setup
    s.persistence.side_effect = [False, True]
    try:
        await _events(s.bridge, s.provider, caller("Do not call me again. Goodbye."), candidate())
        await _events(s.bridge, s.provider,
                      RealtimeEvent(kind="interrupted", raw={"reason": "speech_started"}),
                      caller("Wait, I need help first."))
        await _events(s.bridge, s.provider, candidate(OPT_OUT_UNCONFIRMED_FAREWELL, "late-repair"))
        s.gateway.send_audio.assert_not_awaited()
        s.ended.assert_not_awaited()
        assert s.bridge._failure_reason
    finally:
        await s.bridge.stop()


@pytest.mark.asyncio
async def test_cancelled_dnc_task_does_not_authorize_candidate(setup):
    s = setup
    started = asyncio.Event()

    async def held(_session):
        started.set()
        await asyncio.Event().wait()

    s.persistence.side_effect = held
    try:
        await _events(s.bridge, s.provider, caller())
        await asyncio.wait_for(started.wait(), 1)
        s.bridge._opt_out_task.cancel()
        await asyncio.gather(s.bridge._opt_out_task, return_exceptions=True)
        await _events(s.bridge, s.provider, candidate())
        s.gateway.send_audio.assert_not_awaited()
        s.provider.repair_unspoken_response.assert_awaited_once()
    finally:
        await s.bridge.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("input_text", ["No thanks to email.", "What does 'do not call me' mean?", "Don't stop calling me."])
async def test_unrelated_scoped_or_quoted_refusal_preserves_normal_reply(setup, input_text):
    s = setup
    try:
        await _events(s.bridge, s.provider, caller(input_text), candidate("Understood. How can I help?"))
        s.gateway.send_audio.assert_awaited_once()
        s.persistence.assert_not_awaited()
        s.provider.repair_unspoken_response.assert_not_awaited()
    finally:
        await s.bridge.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_class", [OpenAIRealtimeSession, XAIRealtimeSession])
async def test_exact_repair_uses_existing_response_instructions_without_session_or_wire_extension(provider_class):
    provider = provider_class(api_key="synthetic", instructions="EXISTING POLICY")
    provider._ws = SimpleNamespace(send=AsyncMock())
    original = provider._instructions
    await provider.repair_unspoken_response(
        {"output": [{"type": "message", "id": "withheld"}]}, required_text=CONTINUE)
    frames = [json.loads(c.args[0]) for c in provider._ws.send.await_args_list]
    assert frames[0] == {"type": "conversation.item.delete", "item_id": "withheld"}
    assert frames[1]["type"] == "response.create"
    assert set(frames[1]["response"]) == {"instructions"}
    assert CONTINUE in frames[1]["response"]["instructions"]
    assert "EXISTING POLICY" in frames[1]["response"]["instructions"]
    assert provider._instructions == original


def correlated_candidate(s, text=CONTINUE, response_id="repair", *, token=None):
    event = candidate(text, response_id)
    event.raw["response"]["metadata"] = {"talky_dnc_repair_id": token or s.bridge._opt_out_repair["id"]}
    return event


@pytest.mark.asyncio
async def test_openai_correlated_safe_repair_requires_token_id_and_exact_text(setup):
    s = setup
    s.provider.supports_response_metadata = True
    try:
        await _events(s.bridge, s.provider, caller(), candidate())
        repair_id = s.provider.repair_unspoken_response.await_args.kwargs["repair_id"]
        assert repair_id == s.bridge._opt_out_repair["id"]
        await _events(s.bridge, s.provider, correlated_candidate(s, token="not-ours"))
        s.gateway.send_audio.assert_not_awaited()
        await _events(s.bridge, s.provider, correlated_candidate(s))
        s.gateway.send_audio.assert_awaited_once()
        await _events(s.bridge, s.provider, correlated_candidate(s))  # Duplicate completion.
        s.gateway.send_audio.assert_awaited_once()
    finally:
        await s.bridge.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["metadata", "response_id"])
async def test_openai_safe_text_without_complete_correlation_is_not_accepted(setup, missing):
    s = setup
    s.provider.supports_response_metadata = True
    try:
        await _events(s.bridge, s.provider, caller(), candidate())
        event = correlated_candidate(s)
        event.raw["response"].pop("metadata" if missing == "metadata" else "id")
        await _events(s.bridge, s.provider, event)
        s.gateway.send_audio.assert_not_awaited()
        assert s.bridge._failure_reason
        s.provider.repair_unspoken_response.assert_awaited_once()
    finally:
        await s.bridge.stop()


@pytest.mark.asyncio
async def test_old_correlated_farewell_cannot_play_after_new_ack_but_new_reply_can(setup):
    s = setup
    s.provider.supports_response_metadata = True
    s.persistence.side_effect = [False, True]
    try:
        await _events(s.bridge, s.provider, caller("Do not call me again. Goodbye."), candidate())
        old = correlated_candidate(s, OPT_OUT_UNCONFIRMED_FAREWELL, "old-repair")
        await _events(s.bridge, s.provider,
                      RealtimeEvent(kind="interrupted", raw={"reason": "speech_started"}),
                      caller("Wait, can you help with my account?"))
        await s.bridge._opt_out_task
        assert s.persistence.await_count == 2
        await _events(s.bridge, s.provider, old)
        s.gateway.send_audio.assert_not_awaited()
        s.ended.assert_not_awaited()
        await _events(s.bridge, s.provider, candidate("What do you need help with?", "new"))
        s.gateway.send_audio.assert_awaited_once()
        s.ended.assert_not_awaited()
    finally:
        await s.bridge.stop()


@pytest.mark.asyncio
async def test_receipt_wait_timeout_never_leaks_or_cancels_write(setup, monkeypatch):
    s = setup
    monkeypatch.setattr("app.realtime.bridge._OPT_OUT_RECEIPT_TIMEOUT_S", .01)
    release = asyncio.Event()

    async def held(_session):
        await release.wait()
        return True

    s.persistence.side_effect = held
    try:
        await _events(s.bridge, s.provider, caller(), candidate())
        assert not s.bridge._opt_out_task.done()
        s.gateway.send_audio.assert_not_awaited()
        s.provider.repair_unspoken_response.assert_awaited_once()
    finally:
        release.set()
        await s.bridge.stop()


async def provider_response(provider, response_id, *, token=None, create=True, done=True):
    response = {"id": response_id, "status": "completed"}
    if token is not None:
        response["metadata"] = {"talky_dnc_repair_id": token}
    if create:
        await provider._handle_server_event({"type": "response.created", "response": response})
    common = {"response_id": response_id, "item_id": "item-" + response_id, "content_index": 0}
    await provider._handle_server_event({"type": "response.output_audio.delta", **common,
        "delta": base64.b64encode(b"\xff" * 320).decode()})
    await provider._handle_server_event({"type": "response.output_audio_transcript.done", **common,
                                        "transcript": CONTINUE})
    if done:
        await provider._handle_server_event({"type": "response.done", "response": response})


def collected(provider):
    events = []
    while not provider._event_queue.empty():
        events.append(provider._event_queue.get_nowait())
    return [e for e in events if e and e.kind == "response_candidate"]


@pytest.mark.asyncio
async def test_openai_metadata_wire_and_echo_preserve_exact_response_ownership():
    provider = OpenAIRealtimeSession(api_key="synthetic", instructions="EXISTING POLICY")
    provider._ws = SimpleNamespace(send=AsyncMock())
    await provider.repair_unspoken_response({}, required_text=CONTINUE, repair_id="synthetic-repair")
    wire = json.loads(provider._ws.send.await_args.args[0])
    assert wire["response"]["metadata"] == {"talky_dnc_repair_id": "synthetic-repair"}
    await provider_response(provider, "owned", token="synthetic-repair")
    events = collected(provider)
    assert len(events) == 1
    assert events[0].raw["response"]["id"] == "owned"
    assert events[0].raw["response"]["metadata"] == wire["response"]["metadata"]


@pytest.mark.asyncio
async def test_old_repair_created_audio_done_cannot_relabel_current_epoch_or_destroy_new_buffer():
    provider = OpenAIRealtimeSession(api_key="synthetic")
    provider._ws = SimpleNamespace(send=AsyncMock())
    await provider.repair_unspoken_response({}, required_text=CONTINUE, repair_id="old-token")
    provider._on_interruption("speech_started")
    await provider_response(provider, "new", done=False)
    await provider_response(provider, "old", token="old-token")
    assert provider._playout.response_id == "new"
    await provider._handle_server_event({"type": "response.done", "response": {"id": "new", "status": "completed"}})
    events = collected(provider)
    assert [e.raw["response"]["id"] for e in events] == ["new"]
    assert len(events[0].audio) == 320


@pytest.mark.asyncio
@pytest.mark.parametrize("token", [None, "wrong-token"])
async def test_repair_done_with_changed_or_missing_metadata_cannot_release_audio(token):
    provider = OpenAIRealtimeSession(api_key="synthetic")
    provider._ws = SimpleNamespace(send=AsyncMock())
    await provider.repair_unspoken_response({}, required_text=CONTINUE, repair_id="owned-token")
    await provider_response(provider, "owned", token="owned-token", done=False)
    response = {"id": "owned", "status": "completed"}
    if token:
        response["metadata"] = {"talky_dnc_repair_id": token}
    await provider._handle_server_event({"type": "response.done", "response": response})
    assert collected(provider) == []


@pytest.mark.asyncio
async def test_xai_does_not_silently_send_unverified_metadata():
    provider = XAIRealtimeSession(api_key="synthetic")
    provider._ws = SimpleNamespace(send=AsyncMock())
    assert provider.supports_response_metadata is False
    with pytest.raises(ValueError, match="unavailable"):
        await provider.repair_unspoken_response({}, required_text=CONTINUE, repair_id="not-supported")
    provider._ws.send.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["begin", "send"])
async def test_late_final_dnc_fences_held_transport_and_preserves_partial_evidence(setup, boundary):
    s = setup
    entered, release = asyncio.Event(), asyncio.Event()
    s.bridge._record_turn = Mock()

    async def held(*_args):
        entered.set()
        await release.wait()

    (s.gateway.begin_playback if boundary == "begin" else s.gateway.send_audio).side_effect = held
    event = candidate()
    event.audio = b"\xff" * 960

    async def events():
        yield event

    s.provider.events = events
    try:
        await s.bridge._pump_model_events()
        old_playback = s.bridge._playback_task
        await asyncio.wait_for(entered.wait(), 1)

        async def final_events():
            yield caller()

        s.provider.events = final_events
        await s.bridge._pump_model_events()  # No new speech_started: delayed final ASR.
        release.set()
        await old_playback
        assert s.gateway.send_audio.await_count == (1 if boundary == "send" else 0)
        s.gateway.finish_playback.assert_not_awaited()
        s.ended.assert_not_awaited()
        if boundary == "send":
            s.gateway.clear_output_buffer.assert_awaited()
            s.provider.truncate_response.assert_awaited()
            partial = [c for c in s.bridge._record_turn.call_args_list if c.args[0] == "assistant"]
            assert len(partial) == 1
            assert partial[0].kwargs["metadata"]["delivery"]["status"] == "interrupted"
            s.provider.repair_unspoken_response.assert_not_awaited()  # Never replay possibly submitted text.
        else:
            s.provider.repair_unspoken_response.assert_awaited_once()
    finally:
        release.set()
        await s.bridge.stop()


@pytest.mark.asyncio
async def test_stale_correlated_repair_cannot_cancel_new_playback(setup):
    s = setup
    s.provider.supports_response_metadata = True
    s.persistence.side_effect = [False, True]
    entered, release = asyncio.Event(), asyncio.Event()

    async def held_begin(*_args):
        entered.set()
        await release.wait()

    try:
        await _events(s.bridge, s.provider, caller(), candidate())
        old = correlated_candidate(s, CONTINUE, "old")
        await _events(s.bridge, s.provider, caller("Can you help now?"))
        await s.bridge._opt_out_task
        s.gateway.begin_playback.side_effect = held_begin

        async def events():
            yield candidate("How can I help?", "current")

        s.provider.events = events
        await s.bridge._pump_model_events()
        current = s.bridge._playback_task
        await asyncio.wait_for(entered.wait(), 1)

        async def stale():
            yield old

        s.provider.events = stale
        await s.bridge._pump_model_events()
        assert s.bridge._playback_task is current and not current.done()
        s.gateway.clear_output_buffer.assert_not_awaited()
        release.set()
        await current
        s.gateway.send_audio.assert_awaited_once()
    finally:
        release.set()
        await s.bridge.stop()


@pytest.mark.asyncio
async def test_failed_write_retries_on_new_final_not_deltas_or_duplicate_final(setup):
    s = setup
    s.persistence.side_effect = [False, True]
    first = caller()
    first.raw = {"item_id": "first"}
    try:
        await _events(s.bridge, s.provider,
                      RealtimeEvent(kind="caller_turn", raw={"item_id": "first"}), first)
        await s.bridge._opt_out_task
        await _events(s.bridge, s.provider, first,
                      RealtimeEvent(kind="caller_transcript", text="Can you", is_final=False))
        assert s.persistence.await_count == 1
        second = caller("Can you help now?")
        second.raw = {"item_id": "second"}
        await _events(s.bridge, s.provider,
                      RealtimeEvent(kind="caller_turn", raw={"item_id": "second", "previous_item_id": "first"}),
                      second)
        await s.bridge._opt_out_task
        assert s.persistence.await_count == 2
    finally:
        await s.bridge.stop()


@pytest.mark.asyncio
async def test_actual_openai_event_parser_to_bridge_accepts_only_correlated_safe_repair(setup):
    s = setup
    provider = OpenAIRealtimeSession(api_key="synthetic")
    sent_repair, played = asyncio.Event(), asyncio.Event()
    frames = []

    async def send(frame):
        payload = json.loads(frame)
        frames.append(payload)
        if payload["type"] == "response.create" and "metadata" in payload.get("response", {}):
            sent_repair.set()

    async def finish(_call, utterance):
        played.set()
        return {"utterance_id": utterance, "status": "completed", "evidence": "transport_played", "played_ms": 40}

    provider._ws = SimpleNamespace(send=AsyncMock(side_effect=send), close=AsyncMock())
    s.gateway.finish_playback.side_effect = finish
    s.bridge._rt = provider
    pump = asyncio.create_task(s.bridge._pump_model_events())
    try:
        await provider._handle_server_event({"type": "conversation.item.input_audio_transcription.completed", "transcript": DNC})
        await provider_response(provider, "original")
        await asyncio.wait_for(sent_repair.wait(), 1)
        s.gateway.send_audio.assert_not_awaited()
        metadata = next(f["response"]["metadata"] for f in frames if "metadata" in f.get("response", {}))
        await provider_response(provider, "safe", token=metadata["talky_dnc_repair_id"])
        await asyncio.wait_for(played.wait(), 1)
        s.gateway.send_audio.assert_awaited_once()
        assert s.bridge._opt_out_repair["response_id"] == "safe"
        s.ended.assert_not_awaited()
    finally:
        await s.bridge.stop()
        pump.cancel()
        await asyncio.gather(pump, return_exceptions=True)


@pytest.mark.asyncio
async def test_caller_revision_during_legacy_completion_control_retains_partial_evidence(setup):
    s = setup
    s.persistence.return_value = True
    entered, release = asyncio.Event(), asyncio.Event()
    del s.gateway.finish_playback
    s.bridge._record_turn = Mock()

    async def control(_call, payload):
        if payload["type"] == "tts_audio_complete":
            entered.set()
            await release.wait()

    s.gateway.send_control_event.side_effect = control
    try:
        await _events(s.bridge, s.provider, caller())

        async def events():
            yield candidate("You are on our do-not-call list. How can I help?")

        s.provider.events = events
        await s.bridge._pump_model_events()
        playback = s.bridge._playback_task
        await asyncio.wait_for(entered.wait(), 1)

        async def final_events():
            yield caller("Wait, I have another question.")

        s.provider.events = final_events
        await s.bridge._pump_model_events()
        release.set()
        await playback
        s.gateway.send_audio.assert_awaited_once()
        s.gateway.clear_output_buffer.assert_awaited_once()
        s.provider.truncate_response.assert_awaited_once()
        partial = [c for c in s.bridge._record_turn.call_args_list if c.args[0] == "assistant"]
        assert len(partial) == 1
        assert partial[0].kwargs["metadata"]["delivery"]["status"] == "interrupted"
        s.ended.assert_not_awaited()
    finally:
        release.set()
        await s.bridge.stop()
