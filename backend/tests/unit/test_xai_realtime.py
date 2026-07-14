"""Tests for the xAI Grok Voice realtime provider adapter
(app/infrastructure/realtime/xai_realtime.py).

Covers the documented protocol differences from OpenAI Realtime and proves
the adapter is a safe drop-in for RealtimeBridge:

  1. URL selection — ?model=... default, ?agent_id=... when configured.
  2. Turn-detection default is server_vad/0.85 (not OpenAI's semantic_vad),
     but an explicit operator override still passes straight through.
  3. Voice field is OMITTED (not forced to OpenAI's "marin") when unset.
  4. Cumulative transcript (`conversation.item.input_audio_transcription
     .updated`) is diffed into the same incremental `caller_transcript`
     RealtimeEvent shape OpenAI's `.delta` produces — including a
     mid-stream correction (non-prefix update) and multi-item interleaving.
  5. Audio-delta / function-call / response.done flow is inherited
     UNCHANGED from OpenAIRealtimeSession (no re-implementation drift).
  6. Barge-in ("interrupted") is handled purely client-side: the local
     outbound audio queue is flushed and NO output_audio_buffer.clear (or
     any other extra) is ever sent over the WebSocket — xAI doesn't support
     that op over WS, and neither provider needs it.

All WebSocket I/O is mocked; no network is used.
"""
from __future__ import annotations

import asyncio
import base64
import json

import pytest

from app.infrastructure.realtime.openai_realtime import RealtimeEvent
from app.infrastructure.realtime.xai_realtime import (
    XAIRealtimeSession,
    XAI_DEFAULT_MODEL,
)


# ---------------------------------------------------------------------------
# 1. URL selection
# ---------------------------------------------------------------------------

def test_url_defaults_to_model_query():
    sess = XAIRealtimeSession(api_key="xai-test")
    assert sess._build_url() == (
        f"wss://api.x.ai/v1/realtime?model={XAI_DEFAULT_MODEL}"
    )


def test_url_uses_custom_model():
    sess = XAIRealtimeSession(api_key="xai-test", model="grok-voice-custom")
    assert sess._build_url() == "wss://api.x.ai/v1/realtime?model=grok-voice-custom"


def test_url_prefers_agent_id_when_set():
    sess = XAIRealtimeSession(api_key="xai-test", agent_id="agent-123")
    assert sess._build_url() == "wss://api.x.ai/v1/realtime?agent_id=agent-123"


# ---------------------------------------------------------------------------
# 2. Turn detection default + override passthrough
# ---------------------------------------------------------------------------

def test_turn_detection_defaults_to_server_vad_085():
    s = XAIRealtimeSession(api_key="xai-test")._build_session_update()["session"]
    assert s["audio"]["input"]["turn_detection"] == {
        "type": "server_vad", "threshold": 0.85,
    }


def test_turn_detection_explicit_override_passes_through():
    override = {"type": "server_vad", "threshold": 0.5, "silence_duration_ms": 400}
    s = XAIRealtimeSession(
        api_key="xai-test", settings={"turn_detection": override},
    )._build_session_update()["session"]
    assert s["audio"]["input"]["turn_detection"] == override


def test_turn_detection_bare_eagerness_string_still_normalised():
    # Inherited normalisation (semantic_vad shorthand) still applies when the
    # operator explicitly asks for it, even though it isn't our new default.
    s = XAIRealtimeSession(
        api_key="xai-test", settings={"turn_detection": "high"},
    )._build_session_update()["session"]
    assert s["audio"]["input"]["turn_detection"] == {
        "type": "semantic_vad", "eagerness": "high",
    }


# ---------------------------------------------------------------------------
# 3. Voice field omission
# ---------------------------------------------------------------------------

def test_voice_omitted_when_unset():
    s = XAIRealtimeSession(api_key="xai-test")._build_session_update()["session"]
    assert "voice" not in s["audio"]["output"]


def test_voice_included_when_explicitly_set():
    s = XAIRealtimeSession(
        api_key="xai-test", voice="some-xai-voice",
    )._build_session_update()["session"]
    assert s["audio"]["output"]["voice"] == "some-xai-voice"


def test_audio_format_still_mulaw_8k_both_directions():
    # The shared telephony contract must hold regardless of provider.
    s = XAIRealtimeSession(api_key="xai-test")._build_session_update()["session"]
    assert s["audio"]["input"]["format"] == {"type": "audio/pcmu"}
    assert s["audio"]["output"]["format"] == {"type": "audio/pcmu"}


# ---------------------------------------------------------------------------
# 4. Cumulative transcript diffing
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cumulative_transcript_updated_emits_incremental_deltas():
    sess = XAIRealtimeSession(api_key="xai-test", call_id="xai-transcript-1")

    events = []
    orig_offer = sess._offer_event
    def _capture(ev):
        if ev is not None:
            events.append(ev)
        orig_offer(ev)
    sess._offer_event = _capture

    await sess._handle_server_event({
        "type": "conversation.item.input_audio_transcription.updated",
        "item_id": "item-1",
        "transcript": "hel",
    })
    await sess._handle_server_event({
        "type": "conversation.item.input_audio_transcription.updated",
        "item_id": "item-1",
        "transcript": "hello there",
    })

    caller_events = [e for e in events if e.kind == "caller_transcript"]
    assert len(caller_events) == 2
    assert caller_events[0].text == "hel"
    assert caller_events[0].is_final is False
    # Second update only carries the NEW suffix, not the full cumulative text.
    assert caller_events[1].text == "lo there"
    assert caller_events[1].is_final is False


@pytest.mark.asyncio
async def test_cumulative_transcript_non_prefix_correction_fails_soft():
    """If the model revises earlier words (new text doesn't extend the old
    prefix), emit the full new text rather than crashing or dropping it."""
    sess = XAIRealtimeSession(api_key="xai-test")
    events = []
    sess._offer_event = lambda ev: events.append(ev) if ev else None

    await sess._handle_server_event({
        "type": "conversation.item.input_audio_transcription.updated",
        "item_id": "item-2",
        "transcript": "I want to buy a car",
    })
    await sess._handle_server_event({
        "type": "conversation.item.input_audio_transcription.updated",
        "item_id": "item-2",
        "transcript": "I want to sell a car",  # revised, not just appended
    })

    caller_events = [e for e in events if e.kind == "caller_transcript"]
    assert caller_events[0].text == "I want to buy a car"
    assert caller_events[1].text == "I want to sell a car"  # full text, fail-soft


@pytest.mark.asyncio
async def test_cumulative_transcript_completed_clears_cache_and_marks_final():
    sess = XAIRealtimeSession(api_key="xai-test")
    events = []
    sess._offer_event = lambda ev: events.append(ev) if ev else None

    await sess._handle_server_event({
        "type": "conversation.item.input_audio_transcription.updated",
        "item_id": "item-3",
        "transcript": "hi",
    })
    assert sess._caller_transcript_cumulative["item-3"] == "hi"

    await sess._handle_server_event({
        "type": "conversation.item.input_audio_transcription.completed",
        "item_id": "item-3",
        "transcript": "hi there",
    })
    # Delegated to the inherited handler: full text, is_final=True.
    finals = [e for e in events if e.kind == "caller_transcript" and e.is_final]
    assert len(finals) == 1
    assert finals[0].text == "hi there"
    # Cache cleared so a reused item_id later doesn't diff against stale text.
    assert "item-3" not in sess._caller_transcript_cumulative


@pytest.mark.asyncio
async def test_cumulative_transcript_multi_item_interleaving_isolated():
    """Two items' cumulative transcripts must not bleed into each other."""
    sess = XAIRealtimeSession(api_key="xai-test")
    events = []
    sess._offer_event = lambda ev: events.append(ev) if ev else None

    await sess._handle_server_event({
        "type": "conversation.item.input_audio_transcription.updated",
        "item_id": "a", "transcript": "foo",
    })
    await sess._handle_server_event({
        "type": "conversation.item.input_audio_transcription.updated",
        "item_id": "b", "transcript": "bar",
    })
    await sess._handle_server_event({
        "type": "conversation.item.input_audio_transcription.updated",
        "item_id": "a", "transcript": "foobaz",
    })

    caller_events = [e for e in events if e.kind == "caller_transcript"]
    assert [e.text for e in caller_events] == ["foo", "bar", "baz"]


# ---------------------------------------------------------------------------
# 5. Everything else is inherited/unchanged: audio delta, function calls,
#    response.done bookkeeping.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_audio_delta_still_decodes_to_bytes_via_inherited_path():
    sess = XAIRealtimeSession(api_key="xai-test")
    mulaw = bytes(range(160))
    b64 = base64.b64encode(mulaw).decode("ascii")

    events = []
    sess._offer_event = lambda ev: events.append(ev) if ev else None

    # response.created establishes the active epoch (inherited logic).
    await sess._handle_server_event({"type": "response.created"})
    await sess._handle_server_event({
        "type": "response.output_audio.delta", "delta": b64,
    })

    audio_events = [e for e in events if e.kind == "audio"]
    assert len(audio_events) == 1
    assert audio_events[0].audio == mulaw


@pytest.mark.asyncio
async def test_function_call_flow_inherited_unchanged():
    sess = XAIRealtimeSession(api_key="xai-test")
    events = []
    sess._offer_event = lambda ev: events.append(ev) if ev else None

    await sess._handle_server_event({
        "type": "response.function_call_arguments.done",
        "call_id": "call-1",
        "name": "knowledge_lookup",
        "arguments": json.dumps({"query": "hours"}),
    })

    fc_events = [e for e in events if e.kind == "function_call"]
    assert len(fc_events) == 1
    fc = fc_events[0].function_call
    assert fc.call_id == "call-1"
    assert fc.name == "knowledge_lookup"
    assert fc.parsed_arguments() == {"query": "hours"}
    assert sess.stats.function_calls == 1


# ---------------------------------------------------------------------------
# 6. Barge-in: local flush only, NEVER output_audio_buffer.clear over WS.
# ---------------------------------------------------------------------------

class _RecordingWS:
    """Fake WS that just records everything sent, for the "never call
    output_audio_buffer.clear" assertion."""
    def __init__(self):
        self.sent = []

    async def send(self, data):
        self.sent.append(json.loads(data))

    async def close(self):
        pass


@pytest.mark.asyncio
async def test_barge_in_flushes_local_queue_without_ws_buffer_clear():
    sess = XAIRealtimeSession(api_key="xai-test")
    ws = _RecordingWS()
    sess._ws = ws  # simulate an open connection without a real handshake

    events = []
    sess._offer_event = lambda ev: events.append(ev) if ev else None

    # Queue up some model audio for a response, then barge in.
    await sess._handle_server_event({"type": "response.created"})
    await sess._handle_server_event({
        "type": "response.output_audio.delta",
        "delta": base64.b64encode(bytes(160)).decode("ascii"),
    })
    await sess._handle_server_event({"type": "input_audio_buffer.speech_started"})

    interrupted = [e for e in events if e.kind == "interrupted"]
    assert len(interrupted) == 1
    # The epoch bump means a LATE-arriving delta from the old response is
    # dropped as stale (proves the client-side-only flush actually works).
    stale_before = sess.stats.audio_frames_dropped_stale
    await sess._handle_server_event({
        "type": "response.output_audio.delta",
        "delta": base64.b64encode(bytes(160)).decode("ascii"),
    })
    assert sess.stats.audio_frames_dropped_stale == stale_before + 1

    # Nothing was ever sent to the server to accomplish this — xAI doesn't
    # support output_audio_buffer.clear over WS, and we never attempt it.
    assert all(msg.get("type") != "output_audio_buffer.clear" for msg in ws.sent)
    assert ws.sent == []  # we sent NOTHING at all for the interruption itself
