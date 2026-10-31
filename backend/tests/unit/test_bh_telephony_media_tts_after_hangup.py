"""telephony-infra-media-4 / -8: TTS after the caller hung up, and stale tails.

-4: AsteriskAdapter.send_tts_audio used to raise a plain TtsDeliveryError when
    the call's gateway session was already popped.  tts_playback maps that to
    'tts_delivery_error' (a TTS-provider fault), plays the emergency clip on the
    dead call and logs CRITICAL.  A gone call must surface as SessionGoneError,
    and must not re-create the per-call error counter it just popped.
-8: a TtsDeliveryError mid-utterance left the unsent PCMU tail in tts_buffer,
    which the next reply played before its own first word.
"""
from __future__ import annotations

import types
from unittest.mock import AsyncMock

import pytest

from app.domain.services.voice_pipeline.tts_playback import TtsPlayback
from app.infrastructure.telephony.asterisk_adapter import AsteriskAdapter, TtsDeliveryError
from app.infrastructure.telephony.browser_media_gateway import SessionGoneError
from app.infrastructure.telephony.telephony_media_gateway import TelephonyMediaGateway

PCMU_20MS = b"\x7f" * 160


async def _gateway(adapter, call_id="c1"):
    gateway = TelephonyMediaGateway()
    await gateway.initialize({"sample_rate": 16000, "tts_source_format": "s16le"})
    await gateway.on_call_started(call_id, {"adapter": adapter, "pbx_call_id": "pbx-" + call_id})
    return gateway


# --------------------------------------------------------------------------
# -4  TTS after hangup
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_tts_audio_without_gateway_session_means_the_call_is_gone():
    adapter = AsteriskAdapter()

    with pytest.raises(SessionGoneError):
        await adapter.send_tts_audio("pbx-gone", PCMU_20MS)


@pytest.mark.asyncio
async def test_gone_call_does_not_recreate_the_popped_error_counter():
    adapter = AsteriskAdapter()

    with pytest.raises(TtsDeliveryError):
        await adapter.send_tts_audio("pbx-gone", PCMU_20MS)

    assert "pbx-gone" not in adapter._tts_error_counts


@pytest.mark.asyncio
async def test_real_gateway_failure_with_a_live_session_still_fails_on_first_proof():
    """A POST failure while the gateway session exists is a real delivery fault."""
    adapter = AsteriskAdapter()
    adapter._gateway_sessions["pbx-live"] = "gw-1"
    adapter._gateway = AsyncMock(side_effect=RuntimeError("gateway 500"))

    with pytest.raises(TtsDeliveryError) as exc:
        await adapter.send_tts_audio("pbx-live", PCMU_20MS)

    assert not isinstance(exc.value, SessionGoneError)


@pytest.mark.asyncio
async def test_media_gateway_marks_the_session_inactive_when_the_call_is_gone():
    adapter = AsteriskAdapter()
    gateway = await _gateway(adapter)
    session = gateway._sessions["c1"]

    with pytest.raises(SessionGoneError):
        await gateway.send_audio("c1", b"\x01\x02" * 1600)

    assert session.is_active is False
    assert session.tts_buffer == b""


@pytest.mark.asyncio
async def test_beginning_an_utterance_on_a_gone_call_is_session_gone():
    adapter = AsteriskAdapter()
    adapter._tts_receipt_protocol = 1

    with pytest.raises(SessionGoneError):
        await adapter.begin_tts_utterance("pbx-gone", "utt-1")


class _Chunk:
    def __init__(self, data):
        self.data = data


class _Provider:
    name = "fake-tts"

    def stream_synthesize(self, text, **kwargs):
        async def _gen():
            yield _Chunk(b"\x01\x02" * 1600)

        return _gen()


class _Latency:
    def __getattr__(self, name):
        return lambda *a, **k: None


class _Pipeline:
    def __init__(self, gateway):
        self.tts_provider = _Provider()
        self.media_gateway = gateway
        self.latency_tracker = _Latency()
        self.tts_sample_rate = 16000
        self.stt_provider = None
        self.silent_turns: list[str] = []

    def _record_silent_turn(self, call_id, reason):
        self.silent_turns.append(reason)


@pytest.mark.asyncio
async def test_tts_after_hangup_is_session_gone_not_a_provider_fault():
    adapter = AsteriskAdapter()
    # The utterance began while the call was alive; teardown popped the gateway
    # session before the first packet (the production window).
    adapter.begin_tts_utterance = AsyncMock()
    gateway = await _gateway(adapter, call_id="c1")
    gateway.play_pcmu_clip = AsyncMock()
    pipe = _Pipeline(gateway)
    session = types.SimpleNamespace(call_id="c1", tts_active=True, voice_id="v1", turn_id=1)

    interrupted = await TtsPlayback(pipe).synthesize_and_send(
        session, "Hello there.", None, track_latency=False
    )

    assert interrupted is True
    assert session._tts_failure_reason == "session_gone"
    gateway.play_pcmu_clip.assert_not_awaited()


# --------------------------------------------------------------------------
# -8  stale PCMU tail
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_typed_delivery_error_does_not_leave_a_tail_for_the_next_utterance():
    adapter = AsyncMock()
    adapter.send_tts_audio.side_effect = TtsDeliveryError("tts_queue_full")
    gateway = await _gateway(adapter)
    session = gateway._sessions["c1"]

    with pytest.raises(TtsDeliveryError):
        # 1.5 packets after resampling: one packet is attempted, the rest queued.
        await gateway.send_audio("c1", b"\x01\x02" * 480)

    assert session.tts_buffer == b""
    assert session._tts_pending_bytes == b""
    assert session._tts_send_deadline is None


@pytest.mark.asyncio
async def test_repeated_untyped_failures_do_not_leave_a_tail_either():
    adapter = AsyncMock()
    adapter.send_tts_audio.side_effect = RuntimeError("pbx write failed")
    gateway = await _gateway(adapter)
    session = gateway._sessions["c1"]
    session.consecutive_tts_failures = 4

    with pytest.raises(TtsDeliveryError):
        await gateway.send_audio("c1", b"\x01\x02" * 480)

    assert session.tts_buffer == b""
    assert session._tts_pending_bytes == b""
    assert session._tts_send_deadline is None


@pytest.mark.asyncio
async def test_next_utterance_after_a_failed_one_starts_clean():
    adapter = AsyncMock()
    adapter.send_tts_audio.side_effect = [TtsDeliveryError("tts_queue_full"), None, None, None]
    gateway = await _gateway(adapter)

    with pytest.raises(TtsDeliveryError):
        await gateway.send_audio("c1", b"\x01\x02" * 480)
    adapter.send_tts_audio.reset_mock()
    adapter.send_tts_audio.side_effect = None
    adapter.send_tts_audio.return_value = None

    await gateway.send_audio("c1", b"\x05\x06" * 320)  # exactly one 20 ms packet @16k->8k
    await gateway.flush_tts_buffer("c1")

    sent = b"".join(c.args[1] for c in adapter.send_tts_audio.await_args_list)
    assert len(sent) == 160, "only the new utterance may reach the wire"
