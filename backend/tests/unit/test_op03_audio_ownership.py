"""Offline ownership controls: real wrappers/gateway, synthetic transports only."""

import asyncio
import json

import pytest

from app.domain.models.conversation import AudioChunk, TranscriptChunk
from app.domain.services.resilient_stt import ResilientSTTProvider
from app.infrastructure.telephony.browser_media_gateway import BrowserMediaGateway
from app.infrastructure.telephony.twilio_media_gateway import TwilioMediaGateway


class HeldProvider:
    name = "offline-held"

    def __init__(self):
        self.closed = 0
        self.iterator = None

    def stream_transcribe(self, audio, **kwargs):
        async def run():
            try:
                await anext(audio)
                yield TranscriptChunk(text="synthetic partial", is_final=False)
                await asyncio.Event().wait()
            finally:
                self.closed += 1

        # Keep the underlying object alive: resource ownership must not depend
        # on reference counting/async-generator garbage collection.
        self.iterator = run()
        return self.iterator


@pytest.mark.asyncio
@pytest.mark.parametrize("fallback", [False, True])
async def test_consumer_close_at_transcript_yield_closes_provider_before_return(fallback):
    input_closed = asyncio.Event()

    async def audio():
        try:
            yield AudioChunk(data=bytes(320), sample_rate=16000)
            await asyncio.Event().wait()
        finally:
            input_closed.set()

    class FailedPrimary:
        name = "offline-failed"

        async def stream_transcribe(self, audio, **kwargs):
            raise RuntimeError("synthetic transport failure")
            yield

    held = HeldProvider()
    wrapper = (
        ResilientSTTProvider(FailedPrimary(), held) if fallback else ResilientSTTProvider(held)
    )
    stream = wrapper.stream_transcribe(audio(), call_id="offline-close")
    try:
        assert (await anext(stream)).text == "synthetic partial"
        await stream.aclose()
        assert input_closed.is_set()
        assert held.closed == 1, "close returned while the provider still owned its stream"
    finally:
        await stream.aclose()
        if held.iterator is not None:
            await held.iterator.aclose()


class BrowserSocket:
    def __init__(self):
        self.controls = []
        self.audio = []

    async def send_json(self, payload):
        self.controls.append(payload)

    async def send_bytes(self, payload):
        self.audio.append(payload)

    async def send_text(self, payload):
        self.controls.append(payload)


@pytest.mark.asyncio
async def test_new_browser_completion_cannot_complete_superseded_utterance():
    gateway = BrowserMediaGateway()
    socket = BrowserSocket()
    await gateway.on_call_started("offline-playback", {"websocket": socket})
    await gateway.begin_playback("offline-playback", "old-utterance")
    old_waiter = asyncio.create_task(gateway.finish_playback("offline-playback", "old-utterance"))
    try:
        # Ensure A is actually waiting on the gateway's event before B begins.
        for _ in range(10):
            await asyncio.sleep(0)
        assert not old_waiter.done()
        await gateway.begin_playback("offline-playback", "new-utterance")
        await gateway.send_audio("offline-playback", bytes(3200))
        await gateway.flush_audio_buffer("offline-playback")
        gateway.mark_playback_complete("offline-playback", "new-utterance")
        old_receipt = await asyncio.wait_for(old_waiter, 1)
        assert old_receipt["status"] != "completed"
        assert old_receipt["evidence"] != "transport_played"
        # An obsolete waiter must also leave B's own completion intact.
        new_receipt = await gateway.finish_playback("offline-playback", "new-utterance")
        assert new_receipt["status"] == "completed"
        assert new_receipt["evidence"] == "transport_played"
        assert new_receipt["played_ms"] == 100
    finally:
        old_waiter.cancel()
        await asyncio.gather(old_waiter, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["clear", "hangup"])
async def test_browser_stop_wakes_waiter_without_playout_proof(ending):
    gateway = BrowserMediaGateway()
    await gateway.on_call_started("offline-stop", {"websocket": BrowserSocket()})
    await gateway.begin_playback("offline-stop", "stopped")
    waiter = asyncio.create_task(gateway.finish_playback("offline-stop", "stopped"))
    try:
        for _ in range(10):
            await asyncio.sleep(0)
        assert not waiter.done()
        if ending == "clear":
            await gateway.clear_output_buffer("offline-stop")
        else:
            await gateway.on_call_ended("offline-stop", "synthetic-end")
        receipt = await asyncio.wait_for(waiter, 0.2)
        assert receipt["status"] == "interrupted"
        assert receipt["evidence"] == "unknown"
        assert receipt["played_ms"] == 0
    finally:
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)


@pytest.mark.asyncio
async def test_old_ack_does_not_complete_current_browser_utterance():
    gateway = BrowserMediaGateway()
    await gateway.on_call_started("offline-ack", {"websocket": BrowserSocket()})
    await gateway.begin_playback("offline-ack", "old")
    await gateway.begin_playback("offline-ack", "current")
    waiter = asyncio.create_task(gateway.finish_playback("offline-ack", "current"))
    try:
        for _ in range(10):
            await asyncio.sleep(0)
        gateway.mark_playback_complete("offline-ack", "old")
        gateway.mark_playback_complete("offline-ack")
        for _ in range(10):
            await asyncio.sleep(0)
        assert not waiter.done()
        gateway.mark_playback_complete("offline-ack", "current")
        receipt = await asyncio.wait_for(waiter, 0.2)
        assert receipt["status"] == "completed"
        assert receipt["utterance_id"] == "current"
    finally:
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)


@pytest.mark.asyncio
async def test_cancelled_old_waiter_cannot_clear_new_tracking():
    gateway = BrowserMediaGateway()
    await gateway.on_call_started("offline-cancel", {"websocket": BrowserSocket()})
    await gateway.begin_playback("offline-cancel", "old")
    waiter = asyncio.create_task(gateway.finish_playback("offline-cancel", "old"))
    for _ in range(10):
        await asyncio.sleep(0)
    await gateway.begin_playback("offline-cancel", "new")
    waiter.cancel()
    await asyncio.gather(waiter, return_exceptions=True)
    assert gateway._sessions["offline-cancel"].playback_tracking_active
    gateway.mark_playback_complete("offline-cancel", "new")
    receipt = await gateway.finish_playback("offline-cancel", "new")
    assert receipt["status"] == "completed"


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["clear", "send", "flush"])
async def test_old_transport_completion_preserves_new_buffer_and_accounting(operation):
    entered = asyncio.Event()
    release = asyncio.Event()

    class HeldSocket(BrowserSocket):
        async def send_bytes(self, payload):
            if not entered.is_set():
                entered.set()
                await release.wait()
            await super().send_bytes(payload)

    gateway = BrowserMediaGateway()
    await gateway.on_call_started("offline-buffer", {"websocket": HeldSocket()})
    await gateway.begin_playback("offline-buffer", "old")
    session = gateway._sessions["offline-buffer"]
    if operation == "send":
        pending = asyncio.create_task(gateway.send_audio("offline-buffer", bytes(3200)))
    else:
        session.output_buffer = bytearray(200)
        method = gateway.clear_output_buffer if operation == "clear" else gateway.flush_audio_buffer
        pending = asyncio.create_task(method("offline-buffer"))
    try:
        await asyncio.wait_for(entered.wait(), 0.2)
        if operation != "clear":
            await gateway.clear_output_buffer("offline-buffer")
        await gateway.begin_playback("offline-buffer", "new")
        session.output_buffer = bytearray(b"\x01\x00" * 200)
        release.set()
        await pending
        assert session.playback_tracking_active
        assert session.playback_bytes_sent == 0, "old payload counted as new audio"
        assert session.output_buffer == b"\x01\x00" * 200
        gateway.mark_playback_complete("offline-buffer", "new")
        assert (await gateway.finish_playback("offline-buffer", "new"))["status"] == "completed"
    finally:
        release.set()
        await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.asyncio
async def test_twilio_clear_wakes_old_waiter_and_preserves_new_completion():
    gateway = TwilioMediaGateway()
    socket = BrowserSocket()
    await gateway.on_call_started("offline-twilio", {"websocket": socket})
    gateway.set_stream_sid("offline-twilio", "synthetic-stream")
    await gateway.begin_playback("offline-twilio", "old")
    waiter = asyncio.create_task(gateway.finish_playback("offline-twilio", "old"))
    try:
        for _ in range(10):
            await asyncio.sleep(0)
        assert not waiter.done()
        await gateway.clear_output_buffer("offline-twilio")
        receipt = await asyncio.wait_for(waiter, 0.2)
        assert receipt["status"] == "interrupted"
        assert receipt["evidence"] == "unknown"
        await gateway.begin_playback("offline-twilio", "new")
        gateway.mark_playback_complete("offline-twilio", "old")
        assert not gateway._sessions["offline-twilio"].playback_complete_event.is_set()
        gateway.mark_playback_complete("offline-twilio", "new")
        assert (await gateway.finish_playback("offline-twilio", "new"))["status"] == "completed"
    finally:
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("gateway_type", [BrowserMediaGateway, TwilioMediaGateway])
@pytest.mark.parametrize("first_frame_succeeds", [False, True])
async def test_dropped_audio_cannot_be_promoted_by_matching_playback_ack(
    gateway_type, first_frame_succeeds
):
    class LossySocket(BrowserSocket):
        media_count = 0

        async def send_bytes(self, payload):
            self.media_count += 1
            if not first_frame_succeeds or self.media_count > 1:
                await asyncio.Event().wait()
            await super().send_bytes(payload)

        async def send_text(self, payload):
            if json.loads(payload)["event"] == "media":
                await self.send_bytes(payload)
            else:
                await super().send_text(payload)

    gateway = gateway_type()
    gateway._ws_send_timeout_ms = 1
    await gateway.on_call_started("offline-loss", {"websocket": LossySocket()})
    if isinstance(gateway, TwilioMediaGateway):
        gateway.set_stream_sid("offline-loss", "synthetic-stream")
    await gateway.begin_playback("offline-loss", "lossy")
    await gateway.send_audio("offline-loss", bytes(3200))
    await gateway.flush_audio_buffer("offline-loss")
    session = gateway._sessions["offline-loss"]
    assert session.dropped_output_bytes > 0
    assert session.total_bytes_sent == (1920 if first_frame_succeeds else 0)
    gateway.mark_playback_complete("offline-loss", "lossy")
    receipt = await gateway.finish_playback("offline-loss", "lossy")
    assert receipt["status"] == "failed"
    assert receipt["evidence"] == "unknown"
    assert receipt["played_ms"] == 0
