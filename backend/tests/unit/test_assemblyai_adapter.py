"""Provider-wire tests: no network, credentials, or model-accuracy claims."""

import asyncio
import json
import logging
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from app.domain.models.assemblyai_config import ASSEMBLYAI_MODEL
from app.domain.models.conversation import AudioChunk, BargeInSignal, TranscriptChunk
from app.infrastructure.providers.provider_concurrency import ProviderConcurrencyGuard
from app.infrastructure.stt import assemblyai as adapter
from app.infrastructure.stt.assemblyai import AssemblyAISTTProvider


class FakeSocket:
    def __init__(self, *, mode="balanced", model=ASSEMBLYAI_MODEL, begin=True, terminate=True):
        self.incoming = asyncio.Queue()
        if begin:
            self.incoming.put_nowait(
                {"type": "Begin", "configuration": {"model": model, "mode": mode}}
            )
        self.sent = []
        self.closed = 0
        self.terminate = terminate
        self.on_terminate = []

    async def recv(self):
        value = await self.incoming.get()
        if callable(value):
            value = await value()
        if isinstance(value, Exception):
            raise value
        return value if isinstance(value, str) else json.dumps(value)

    async def send(self, payload):
        self.sent.append(payload)
        if (
            isinstance(payload, str)
            and json.loads(payload).get("type") == "Terminate"
            and self.terminate
        ):
            for event in self.on_terminate:
                self.incoming.put_nowait(event)
            self.incoming.put_nowait({"type": "Termination", "audio_duration_seconds": 1})

    async def close(self):
        self.closed += 1
        self.incoming.put_nowait(RuntimeError("socket closed"))


async def setup_provider(monkeypatch, *, settings=None, socket=None, **config):
    provider = AssemblyAISTTProvider()
    provider._guard = ProviderConcurrencyGuard("assemblyai_test", 3)
    settings = settings or {}
    ws = socket or FakeSocket(mode=settings.get("mode", "balanced"))
    connects = []

    async def connect(url, **kwargs):
        connects.append((url, kwargs))
        return ws

    monkeypatch.setattr(adapter.websockets, "connect", connect)
    await provider.initialize(
        {"api_key": "private-test-key", "assemblyai_settings": settings, **config}
    )
    return provider, ws, connects


async def audio(*chunks):
    for data in chunks:
        yield AudioChunk(data=data, sample_rate=16000)


def turn(order, text, final=False):
    return {
        "type": "Turn",
        "turn_order": order,
        "transcript": text,
        "end_of_turn": final,
        "turn_is_formatted": final,
        "words": [{"text": "word", "confidence": 0.9}],
    }


@pytest.mark.parametrize("mode", ["balanced", "min_latency", "max_accuracy"])
async def test_each_mode_pins_model_english_headers_and_preserves_preset(monkeypatch, mode):
    provider, ws, connects = await setup_provider(monkeypatch, settings={"mode": mode})
    await provider.pre_connect("call")
    url, options = connects[0]
    query = parse_qs(urlsplit(url).query)
    assert query["speech_model"] == [ASSEMBLYAI_MODEL]
    assert json.loads(query["language_codes"][0]) == ["en"]
    assert query["mode"] == [mode]
    assert query["encoding"] == ["pcm_s16le"]
    assert query["session_heartbeat"] == ["true"]
    assert "min_turn_silence" not in query and "max_turn_silence" not in query
    assert "interruption_delay" not in query and "format_turns" not in query
    assert "end_of_turn_confidence_threshold" not in query
    assert options["additional_headers"] == {"Authorization": "private-test-key"}
    assert "private-test-key" not in url
    assert options["logger"].isEnabledFor(logging.DEBUG) is False
    assert provider._guard.in_flight == 1
    assert provider.seconds_since_last_message("call") is not None
    await provider.cleanup()
    assert provider._guard.in_flight == 0 and ws.closed == 1


async def test_advanced_settings_are_encoded_without_unrelated_flux_fields(monkeypatch):
    settings = {
        "region": "eu",
        "min_turn_silence": 400,
        "max_turn_silence": 3000,
        "interruption_delay": 750,
        "vad_threshold": 0.4,
        "include_partial_turns": False,
        "prompt": "Appointments at A&B Clinic.",
        "keyterms_prompt": ["A&B", "López"],
        "previous_context_n_turns": 8,
        "language_detection": True,
        "voice_focus": "near-field",
        "voice_focus_threshold": 0.8,
        "domain": "medical-v1",
        "speaker_labels": True,
        "max_speakers": 2,
        "speaker_labels_revision_interval_ms": 300000,
        "redact_pii": True,
        "redact_pii_policies": ["email_address", "phone_number"],
        "redact_pii_sub": "entity_name",
        "filter_profanity": True,
        "inactivity_timeout": 30,
    }
    provider, ws, connects = await setup_provider(
        monkeypatch, settings=settings, keyterms=["A&B", "Product", "x" * 51]
    )
    await provider.update_agent_context("call", "What is your email address?")
    await provider.pre_connect("call")
    url, _ = connects[0]
    query = parse_qs(urlsplit(url).query)
    assert urlsplit(url).hostname == "streaming.eu.assemblyai.com"
    assert query["prompt"] == [settings["prompt"]]
    assert json.loads(query["keyterms_prompt"][0]) == ["A&B", "López"]
    assert json.loads(query["redact_pii_policies"][0]) == settings["redact_pii_policies"]
    assert query["agent_context"] == ["What is your email address?"]
    for name in (
        "min_turn_silence",
        "max_turn_silence",
        "interruption_delay",
        "vad_threshold",
        "previous_context_n_turns",
        "voice_focus",
        "voice_focus_threshold",
        "domain",
        "max_speakers",
        "speaker_labels_revision_interval_ms",
        "redact_pii_sub",
        "inactivity_timeout",
    ):
        assert query[name] == [str(settings[name])]
    for name in ("language_detection", "speaker_labels", "redact_pii", "filter_profanity"):
        assert query[name] == ["true"]
    assert query["include_partial_turns"] == ["false"]
    assert "auto_agent_context" not in query and "region" not in query
    await provider.cleanup()


async def test_empty_keyterms_control_does_not_apply_hidden_campaign_boosts(monkeypatch):
    provider, _, connects = await setup_provider(
        monkeypatch, keyterms=["Campaign Product", "Brand"]
    )
    await provider.pre_connect("call")
    query = parse_qs(urlsplit(connects[0][0]).query)
    assert "keyterms_prompt" not in query
    await provider.cleanup()


@pytest.mark.parametrize("bad_language", ["es", "ur", "multi", "auto"])
async def test_non_english_rejected_before_socket(monkeypatch, bad_language):
    provider, _, connects = await setup_provider(monkeypatch)
    with pytest.raises(ValueError, match="English only"):
        _ = [item async for item in provider.stream_transcribe(audio(), language=bad_language)]
    assert not connects


@pytest.mark.parametrize(
    "model,mode",
    [("universal-3-5-pro", "balanced"), (None, "balanced"), (ASSEMBLYAI_MODEL, "max_accuracy")],
)
async def test_prewarm_rejects_missing_or_wrong_ack_and_releases_permit(monkeypatch, model, mode):
    ws = FakeSocket(model=model, mode=mode)
    provider, _, _ = await setup_provider(monkeypatch, socket=ws)
    with pytest.raises(RuntimeError, match="did not acknowledge"):
        await provider.pre_connect("call")
    assert ws.closed == 1 and provider._guard.in_flight == 0
    assert not provider._connections and not provider._pre_connections


async def test_auth_error_is_safe_and_no_permit_leak(monkeypatch):
    provider, _, _ = await setup_provider(monkeypatch)

    class AuthError(Exception):
        response = SimpleNamespace(status_code=401)

    async def fail(*args, **kwargs):
        raise AuthError("private-test-key and full secret prompt")

    monkeypatch.setattr(adapter.websockets, "connect", fail)
    with pytest.raises(RuntimeError, match="authentication failed") as error:
        await provider.pre_connect("call")
    assert "private-test-key" not in str(error.value)
    assert error.value.__suppress_context__ is True
    assert provider._guard.in_flight == 0


async def test_begin_timeout_closes_open_socket(monkeypatch):
    provider, ws, _ = await setup_provider(monkeypatch, socket=FakeSocket(begin=False))
    monkeypatch.setattr(adapter, "_BEGIN_TIMEOUT", 0.01)
    with pytest.raises(RuntimeError, match="timed out"):
        await provider.pre_connect("call")
    assert ws.closed == 1 and provider._guard.in_flight == 0


async def test_partial_replacement_final_once_barge_signal_and_ignore_revision(monkeypatch):
    provider, ws, _ = await setup_provider(monkeypatch)
    callbacks = []
    ws.on_terminate = [
        {"type": "SpeechStarted", "confidence": 0.9},
        turn(0, "My email is john at example"),
        turn(0, "My email is john@example.com.", True),
        turn(0, "My email is john@example.com.", True),
        {"type": "SpeakerRevision", "revisions": [{"turn_order": 0, "speaker_label": "A"}]},
        {"type": "Heartbeat"},
        turn(1, "Yes, that's correct.", True),
    ]
    chunks = [
        chunk
        async for chunk in provider.stream_transcribe(
            audio(b"\x01\x02" * 160), call_id="call", on_barge_in=callbacks.append
        )
    ]
    signals = [chunk for chunk in chunks if isinstance(chunk, BargeInSignal)]
    transcripts = [chunk for chunk in chunks if isinstance(chunk, TranscriptChunk)]
    assert len(signals) == 1 and callbacks == ["My email is john at example"]
    assert [(chunk.text, chunk.is_final) for chunk in transcripts] == [
        ("My email is john at example", False),
        ("My email is john@example.com.", True),
        ("", True),
        ("Yes, that's correct.", True),
        ("", True),
    ]
    assert transcripts[1].confidence == 0.9
    assert provider._guard.in_flight == 0 and ws.closed == 1


async def test_backchannel_does_not_interrupt(monkeypatch):
    provider, ws, _ = await setup_provider(monkeypatch)
    ws.on_terminate = [{"type": "SpeechStarted"}, turn(0, "uh huh", True)]
    callbacks = []
    chunks = [
        chunk async for chunk in provider.stream_transcribe(audio(), on_barge_in=callbacks.append)
    ]
    assert not callbacks and not any(isinstance(chunk, BargeInSignal) for chunk in chunks)


@pytest.mark.parametrize("opening", ["uh", "yes"])
async def test_backchannel_partial_can_grow_into_one_real_interruption(monkeypatch, opening):
    provider, ws, _ = await setup_provider(monkeypatch)
    expanded = opening + ", I need to correct my email"
    ws.on_terminate = [
        {"type": "SpeechStarted"},
        turn(0, opening),
        turn(0, expanded),
        turn(0, expanded + " address", True),
    ]
    callbacks = []
    chunks = [
        chunk async for chunk in provider.stream_transcribe(audio(), on_barge_in=callbacks.append)
    ]
    assert callbacks == [expanded]
    assert [chunk.text for chunk in chunks if isinstance(chunk, BargeInSignal)] == [expanded]


async def test_muted_turn_is_not_replayed_after_unmute(monkeypatch):
    provider, ws, _ = await setup_provider(monkeypatch)

    async def muted_partial():
        await provider.mute("call")
        return turn(0, "agent echo")

    async def unmuted_final():
        await provider.unmute("call")
        return turn(0, "agent echo full", True)

    ws.on_terminate = [muted_partial, unmuted_final, turn(1, "Actual caller response", True)]
    chunks = [chunk async for chunk in provider.stream_transcribe(audio(), call_id="call")]
    assert [(chunk.text, chunk.is_final) for chunk in chunks] == [
        ("", True),
        ("Actual caller response", True),
        ("", True),
    ]
    assert chunks[0].metadata["empty_turn"] is True


async def test_turn_that_started_muted_stays_suppressed_after_unmute(monkeypatch):
    provider, ws, _ = await setup_provider(monkeypatch)

    async def muted_start():
        await provider.mute("call")
        return {"type": "SpeechStarted"}

    async def unmuted_turn():
        await provider.unmute("call")
        return turn(0, "agent echo arriving late", True)

    ws.on_terminate = [
        muted_start,
        unmuted_turn,
        {"type": "SpeechStarted"},
        turn(1, "Actual caller response", True),
    ]
    chunks = [chunk async for chunk in provider.stream_transcribe(audio(), call_id="call")]
    assert [
        (chunk.text, chunk.is_final) for chunk in chunks if isinstance(chunk, TranscriptChunk)
    ] == [
        ("", True),
        ("Actual caller response", True),
        ("", True),
    ]
    assert chunks[0].metadata["empty_turn"] is True
    assert [chunk.text for chunk in chunks if isinstance(chunk, BargeInSignal)] == [
        "Actual caller response"
    ]


async def test_empty_final_releases_turn_without_promoting_previous_partial(monkeypatch):
    provider, ws, _ = await setup_provider(monkeypatch)
    ws.on_terminate = [{"type": "SpeechStarted"}, turn(0, "possibly an email"), turn(0, "", True)]
    chunks = [chunk async for chunk in provider.stream_transcribe(audio())]
    finals = [chunk for chunk in chunks if isinstance(chunk, TranscriptChunk) and chunk.is_final]
    assert len(finals) == 1 and finals[0].text == ""
    assert finals[0].metadata == {"provider": "assemblyai", "turn_order": 0, "empty_turn": True}


async def test_short_telephony_frames_buffer_and_tail_pad_without_muted_audio(monkeypatch):
    provider, ws, _ = await setup_provider(monkeypatch)

    async def input_audio():
        yield AudioChunk(data=b"\x11\x11" * 320)  # 20ms, buffered
        await provider.mute("call")
        yield AudioChunk(data=b"\x22\x22" * 640)  # 40ms, silence
        await provider.unmute("call")
        yield AudioChunk(data=b"\x33\x33" * 640)  # 40ms, caller

    _ = [chunk async for chunk in provider.stream_transcribe(input_audio(), call_id="call")]
    binary = [payload for payload in ws.sent if isinstance(payload, bytes)]
    assert len(binary) == 2 and all(len(payload) == 1600 for payload in binary)
    joined = b"".join(binary)
    assert b"\x11\x11" not in joined and b"\x22\x22" not in joined
    assert joined.count(b"\x33\x33") == 640
    assert joined.startswith(bytes(1280)) and joined.endswith(bytes(640))


async def test_queued_audio_is_paced_not_sent_faster_than_realtime(monkeypatch):
    provider, ws, _ = await setup_provider(monkeypatch)
    times = []
    original = ws.send

    async def record(payload):
        if isinstance(payload, bytes):
            times.append(asyncio.get_running_loop().time())
        await original(payload)

    ws.send = record
    _ = [item async for item in provider.stream_transcribe(audio(bytes(4800)))]
    assert len(times) == 3
    assert times[2] - times[0] >= 0.09  # 3 x 50ms packets, no burst replay.


@pytest.mark.parametrize(
    "send_delays, expected_starts",
    [
        ([0.03] * 4, [0.0, 0.05, 0.10, 0.15]),
        ([0.08, 0.0, 0.0, 0.0], [0.0, 0.08, 0.13, 0.18]),
    ],
)
async def test_write_latency_counts_toward_frame_period_without_catchup_bursts(
    monkeypatch, send_delays, expected_starts
):
    provider, ws, _ = await setup_provider(monkeypatch)
    clock = SimpleNamespace(now=0.0)
    starts = []
    original_send = ws.send

    async def fake_sleep(delay):
        clock.now += delay

    class PacedAsyncio:
        sleep = staticmethod(fake_sleep)

        def __getattr__(self, name):
            return getattr(asyncio, name)

    async def delayed_write(payload):
        if isinstance(payload, bytes):
            starts.append(clock.now)
            clock.now += send_delays[len(starts) - 1]
        await original_send(payload)

    # Replace only the adapter's clock/sleep; asyncio's event-loop clock and
    # timeout machinery must continue to run normally.
    monkeypatch.setattr(adapter, "time", SimpleNamespace(monotonic=lambda: clock.now))
    monkeypatch.setattr(adapter, "asyncio", PacedAsyncio())
    ws.send = delayed_write
    _ = [item async for item in provider.stream_transcribe(audio(bytes(6400)))]
    assert starts == pytest.approx(expected_starts)
    assert all(later - earlier >= 0.05 - 1e-9 for earlier, later in zip(starts, starts[1:]))


async def test_mulaw_uses_8khz_frames_and_correct_silence_padding(monkeypatch):
    provider, ws, connects = await setup_provider(monkeypatch, sample_rate=8000, encoding="mulaw")

    async def input_audio():
        yield AudioChunk(data=b"\x11" * 160, sample_rate=8000)

    _ = [item async for item in provider.stream_transcribe(input_audio())]
    query = parse_qs(urlsplit(connects[0][0]).query)
    assert query["encoding"] == ["pcm_mulaw"] and query["sample_rate"] == ["8000"]
    assert [payload for payload in ws.sent if isinstance(payload, bytes)] == [
        b"\x11" * 160 + b"\xff" * 240
    ]


async def test_mute_unmute_during_queued_audio_discards_stale_buffer(monkeypatch):
    provider, ws, _ = await setup_provider(monkeypatch)
    original_send = ws.send
    binary_count = 0

    async def mute_after_first_packet(payload):
        nonlocal binary_count
        await original_send(payload)
        if isinstance(payload, bytes):
            binary_count += 1
            if binary_count == 1:
                await provider.mute("call")
                await provider.unmute("call")

    ws.send = mute_after_first_packet
    _ = [
        item
        async for item in provider.stream_transcribe(
            audio(b"\x11\x11" * 2400, b"\x22\x22" * 800),
            call_id="call",
        )
    ]
    binary = [payload for payload in ws.sent if isinstance(payload, bytes)]
    assert binary == [b"\x11\x11" * 800, bytes(1600), bytes(1600), b"\x22\x22" * 800]


async def test_context_updates_replace_latest_and_do_not_send_when_disabled(monkeypatch):
    provider, ws, _ = await setup_provider(monkeypatch)
    await provider.pre_connect("call")
    await provider.update_agent_context("call", "What is your name?")
    await provider.update_agent_context("call", "What is your name?")
    await provider.update_agent_context("call", "x" * 1900 + " What is your email?")
    updates = [json.loads(payload) for payload in ws.sent if isinstance(payload, str)]
    assert len(updates) == 2
    assert updates[0] == {"type": "UpdateConfiguration", "agent_context": "What is your name?"}
    assert len(updates[1]["agent_context"]) == 1750
    assert updates[1]["agent_context"].endswith("What is your email?")
    await provider.cleanup()
    provider, ws, _ = await setup_provider(monkeypatch, settings={"auto_agent_context": False})
    await provider.pre_connect("call")
    await provider.update_agent_context("call", "private reply")
    assert not ws.sent and not provider._pending_context
    await provider.cleanup()


async def test_prewarm_is_reused_and_cleanup_is_idempotent(monkeypatch):
    provider, ws, connects = await setup_provider(monkeypatch)
    await asyncio.gather(provider.pre_connect("call"), provider.pre_connect("call"))
    assert len(connects) == 1 and provider._guard.in_flight == 1
    _ = [chunk async for chunk in provider.stream_transcribe(audio(), call_id="call")]
    await asyncio.gather(provider.cleanup(), provider.cleanup())
    assert len(connects) == 1 and ws.closed == 1 and provider._guard.in_flight == 0


async def test_concurrent_stream_cannot_steal_prewarm_or_clear_existing_claim(monkeypatch):
    provider, ws, connects = await setup_provider(monkeypatch, socket=FakeSocket(begin=False))

    async def consume():
        return [chunk async for chunk in provider.stream_transcribe(audio(), call_id="call")]

    first = asyncio.create_task(consume())
    while not connects:
        await asyncio.sleep(0)
    claim = provider._stream_claims["call"]
    with pytest.raises(RuntimeError, match="already has an active stream"):
        await consume()
    assert provider._stream_claims["call"] is claim
    assert provider._guard.in_flight == 1 and ws.closed == 0
    ws.incoming.put_nowait(
        {"type": "Begin", "configuration": {"model": ASSEMBLYAI_MODEL, "mode": "balanced"}}
    )
    await first
    assert not provider._stream_claims and ws.closed == 1 and provider._guard.in_flight == 0


async def test_failed_prewarm_context_update_closes_socket_and_releases_slot(monkeypatch):
    provider, ws, _ = await setup_provider(monkeypatch, socket=FakeSocket(begin=False))

    async def begin_after_greeting_arrives():
        await provider.update_agent_context("call", "What is your email?")
        return {"type": "Begin", "configuration": {"model": ASSEMBLYAI_MODEL, "mode": "balanced"}}

    original_send = ws.send

    async def fail_context_update(payload):
        if isinstance(payload, str) and json.loads(payload).get("type") == "UpdateConfiguration":
            raise RuntimeError("private provider URL")
        await original_send(payload)

    ws.incoming.put_nowait(begin_after_greeting_arrives)
    ws.send = fail_context_update
    with pytest.raises(RuntimeError, match="context update failed") as error:
        await provider.pre_connect("call")
    assert "private" not in str(error.value)
    assert ws.closed == 1 and provider._guard.in_flight == 0
    assert not provider._pre_connections and not provider._connections


async def test_inactivity_keepalive_holds_prewarm_and_cleanup_stops_it(monkeypatch):
    provider, ws, _ = await setup_provider(monkeypatch, settings={"inactivity_timeout": 5})
    received_keepalive = asyncio.Event()
    original_send = ws.send

    async def observe_keepalive(payload):
        await original_send(payload)
        if isinstance(payload, str) and json.loads(payload).get("type") == "KeepAlive":
            received_keepalive.set()

    ws.send = observe_keepalive
    await provider.pre_connect("call")
    connection = provider._pre_connections["call"]
    await asyncio.wait_for(received_keepalive.wait(), timeout=4)
    await provider.cleanup()
    assert connection.keepalive_task.done()
    assert ws.closed == 1 and provider._guard.in_flight == 0


async def test_cleanup_cancels_pending_begin_without_leaking_permit(monkeypatch):
    provider, ws, connects = await setup_provider(monkeypatch, socket=FakeSocket(begin=False))
    task = asyncio.create_task(provider.pre_connect("call"))
    while not connects:
        await asyncio.sleep(0)
    await provider.cleanup()
    result = await asyncio.gather(task, return_exceptions=True)
    assert isinstance(result[0], asyncio.CancelledError)
    assert ws.closed == 1 and provider._guard.in_flight == 0
    assert not provider._connections and not provider._pre_connections


async def test_closing_stream_cancels_audio_and_releases_socket(monkeypatch):
    provider, ws, _ = await setup_provider(monkeypatch)
    audio_started = asyncio.Event()
    audio_closed = asyncio.Event()

    async def ongoing_audio():
        try:
            audio_started.set()
            await asyncio.Future()
            yield AudioChunk(data=b"")
        finally:
            audio_closed.set()

    ws.incoming.put_nowait(turn(0, "incomplete email"))
    stream = provider.stream_transcribe(ongoing_audio(), call_id="call")
    chunk = await anext(stream)
    assert chunk.is_final is False
    await audio_started.wait()
    await stream.aclose()
    assert audio_closed.is_set()
    assert ws.closed == 1 and provider._guard.in_flight == 0
    assert not provider._connections and not provider._stream_claims


async def test_unexpected_socket_failure_does_not_finalize_partial(monkeypatch):
    provider, ws, _ = await setup_provider(monkeypatch)
    ws.incoming.put_nowait(turn(0, "email at example"))
    ws.incoming.put_nowait(RuntimeError("secret full provider URL"))

    async def ongoing_audio():
        await asyncio.Future()
        yield AudioChunk(data=b"")

    chunks = []
    with pytest.raises(RuntimeError, match="AssemblyAI stream failed") as error:
        async for chunk in provider.stream_transcribe(ongoing_audio(), call_id="call"):
            chunks.append(chunk)
    assert len(chunks) == 1 and chunks[0].is_final is False
    assert "secret" not in str(error.value)
    assert ws.closed == 1 and provider._guard.in_flight == 0


async def test_provider_error_code_preserved_without_echoing_raw_error(monkeypatch):
    provider, ws, _ = await setup_provider(monkeypatch)
    ws.on_terminate = [{"type": "Error", "error_code": 3007, "error": "private caller content"}]
    with pytest.raises(RuntimeError, match="code 3007") as error:
        _ = [chunk async for chunk in provider.stream_transcribe(audio())]
    assert "private" not in str(error.value)
    assert ws.closed == 1


async def test_missing_termination_confirmation_is_visible_failure(monkeypatch):
    provider, ws, _ = await setup_provider(monkeypatch, socket=FakeSocket(terminate=False))
    monkeypatch.setattr(adapter, "_DRAIN_TIMEOUT", 0.01)
    with pytest.raises(RuntimeError, match="termination timed out"):
        _ = [chunk async for chunk in provider.stream_transcribe(audio())]
    assert ws.closed == 1 and provider._guard.in_flight == 0


async def test_bad_audio_format_fails_instead_of_transcribing_garbage(monkeypatch):
    provider, ws, _ = await setup_provider(monkeypatch)

    async def wrong_rate():
        yield AudioChunk(data=bytes(1600), sample_rate=8000)

    with pytest.raises(ValueError, match="audio format"):
        _ = [chunk async for chunk in provider.stream_transcribe(wrong_rate())]
    assert ws.closed == 1 and not any(isinstance(payload, bytes) for payload in ws.sent)


async def test_stalled_audio_write_fails_bounded_and_releases_session(monkeypatch):
    provider, ws, _ = await setup_provider(monkeypatch)
    original_send = ws.send

    async def stall_audio(payload):
        if isinstance(payload, bytes):
            await asyncio.Future()
        await original_send(payload)

    ws.send = stall_audio
    monkeypatch.setattr(adapter, "_SEND_TIMEOUT", 0.01)
    with pytest.raises(RuntimeError, match="audio send timed out"):
        _ = [item async for item in provider.stream_transcribe(audio(bytes(1600)))]
    assert ws.closed == 1 and provider._guard.in_flight == 0
