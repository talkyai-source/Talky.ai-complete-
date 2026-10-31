"""The control-plane acknowledgement is an audio delivery contract."""
from unittest.mock import AsyncMock

import pytest

from app.infrastructure.telephony.asterisk_adapter import AsteriskAdapter, TtsDeliveryError


def make_adapter(ack):
    adapter = AsteriskAdapter.__new__(AsteriskAdapter)
    adapter._gateway_sessions = {"call": "session"}
    adapter._tts_utterances = {}
    adapter._tts_error_counts = {}
    adapter._active_sessions = {}
    adapter._gateway = AsyncMock(return_value=ack)
    return adapter


@pytest.mark.asyncio
@pytest.mark.parametrize("ack", [None, {}, {"status": "queued", "session_id": "session", "queued_frames": 1},
    {"status": "queued", "session_id": "foreign", "queued_frames": 2},
    {"status": "queued", "session_id": "session", "queued_frames": True}])
async def test_partial_missing_or_foreign_ack_cannot_claim_success(ack):
    adapter = make_adapter(ack)
    with pytest.raises(TtsDeliveryError, match="acknowledgement"):
        await adapter.send_tts_audio("call", b"\xff" * 320)


@pytest.mark.asyncio
async def test_complete_ack_is_accepted():
    adapter = make_adapter({"status": "queued", "session_id": "session", "queued_frames": 2})
    await adapter.send_tts_audio("call", b"\xff" * 320)
    assert adapter._gateway.await_count == 1


@pytest.mark.asyncio
async def test_ambiguous_network_failure_is_not_retried():
    adapter = make_adapter({})
    adapter._gateway.side_effect = TimeoutError("response lost")
    with pytest.raises(TtsDeliveryError):
        await adapter.send_tts_audio("call", b"\xff" * 320)
    assert adapter._gateway.await_count == 1


@pytest.mark.asyncio
async def test_explicit_nonacceptance_retries_the_same_identity():
    from app.infrastructure.telephony.asterisk_adapter import GatewayResponseError

    adapter = make_adapter({})
    adapter._gateway.side_effect = [GatewayResponseError(429, '{"error":"tts_queue_full"}'),
        {"status": "queued", "session_id": "session", "queued_frames": 2}]
    await adapter.send_tts_audio("call", b"\xff" * 320)
    calls = adapter._gateway.await_args_list
    assert len(calls) == 2 and calls[0].kwargs["payload"] == calls[1].kwargs["payload"]


@pytest.mark.asyncio
async def test_backpressure_retry_budget_is_bounded():
    from app.infrastructure.telephony.asterisk_adapter import GatewayResponseError

    adapter = make_adapter({})
    adapter._gateway.side_effect = GatewayResponseError(429, '{"error":"tts_queue_full"}')
    with pytest.raises(TtsDeliveryError):
        await adapter.send_tts_audio("call", b"\xff" * 320)
    assert adapter._gateway.await_count == 3


@pytest.mark.asyncio
async def test_interrupt_during_backpressure_does_not_replay_old_speech(monkeypatch):
    from app.infrastructure.telephony.asterisk_adapter import GatewayResponseError

    adapter = make_adapter({})
    adapter._gateway.side_effect = GatewayResponseError(429, '{"error":"tts_queue_full"}')

    async def interrupt(_delay):
        adapter._tts_utterances["call"] = {"utterance_id": "new-utterance"}

    monkeypatch.setattr("app.infrastructure.telephony.asterisk_adapter.asyncio.sleep", interrupt)
    with pytest.raises(TtsDeliveryError, match="interrupted"):
        await adapter.send_tts_audio("call", b"\xff" * 320)
    assert adapter._gateway.await_count == 1


@pytest.mark.asyncio
async def test_unrelated_rate_limit_is_not_assumed_to_be_atomic_queue_refusal():
    from app.infrastructure.telephony.asterisk_adapter import GatewayResponseError

    adapter = make_adapter({})
    adapter._gateway.side_effect = GatewayResponseError(429, '{"error":"rate_limit"}')
    with pytest.raises(TtsDeliveryError):
        await adapter.send_tts_audio("call", b"\xff" * 320)
    assert adapter._gateway.await_count == 1
