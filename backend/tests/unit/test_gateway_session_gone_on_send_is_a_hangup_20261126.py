"""A send the gateway refuses with 404 session_not_found is a hang-up.

Production 25 and 26 Nov, once a day, at the moment a person hangs up:
``send_tts_audio failed: Gateway response 404: {"error":"session_not_found"}``
followed by ``tts_delivery_failed ... reason=tts_delivery_error``, both ERROR.
The gateway had already dropped the session. It is now the same
TtsSessionGoneError the adapter raises when it has no session itself.
Every other gateway failure is unchanged.
"""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock

import pytest

from app.domain.services.voice_pipeline.tts_playback import TtsPlayback
from app.infrastructure.telephony.asterisk_adapter import (
    AsteriskAdapter,
    GatewayResponseError,
    TtsDeliveryError,
    TtsSessionGoneError,
)
from app.infrastructure.telephony.browser_media_gateway import SessionGoneError

from tests.unit.test_tts_delivery_error_stops_turn import _Pipeline, _Provider, _session

_ADAPTER_LOGGER = "app.infrastructure.telephony.asterisk_adapter"
_PLAY_LOGGER = "app.domain.services.voice_pipeline.tts_playback"
_GONE = GatewayResponseError(404, '{"error":"session_not_found"}')


def _adapter(exc):
    adapter = AsteriskAdapter.__new__(AsteriskAdapter)
    adapter._gateway_sessions = {"call": "session"}
    adapter._tts_utterances = {}
    adapter._tts_error_counts = {}
    adapter._active_sessions = {}
    adapter._gateway = AsyncMock(side_effect=exc)
    return adapter


def _errors(caplog):
    return [r for r in caplog.records if r.levelno >= logging.ERROR]


@pytest.mark.asyncio
async def test_404_session_not_found_is_a_hangup_with_no_error_line(caplog):
    caplog.set_level(logging.DEBUG, logger=_ADAPTER_LOGGER)
    adapter = _adapter(_GONE)
    with pytest.raises(SessionGoneError) as info:
        await adapter.send_tts_audio("call", b"\xff" * 320)
    assert isinstance(info.value, TtsSessionGoneError)
    assert not _errors(caplog)
    assert [r for r in caplog.records if r.levelno == logging.INFO and "session_not_found" in r.getMessage()]
    # a gone call is not a counted delivery failure
    assert adapter._tts_error_counts == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exc",
    [
        GatewayResponseError(500, '{"error":"internal"}'),
        GatewayResponseError(404, '{"error":"route_not_found"}'),
        GatewayResponseError(404, "not json"),
        GatewayResponseError(503, '{"error":"session_not_found"}'),
    ],
)
async def test_other_gateway_failures_still_error_and_raise_delivery_error(caplog, exc):
    caplog.set_level(logging.DEBUG, logger=_ADAPTER_LOGGER)
    adapter = _adapter(exc)
    with pytest.raises(TtsDeliveryError) as info:
        await adapter.send_tts_audio("call", b"\xff" * 320)
    assert not isinstance(info.value, SessionGoneError)
    assert len(_errors(caplog)) == 1
    assert adapter._tts_error_counts == {"call": 1}


@pytest.mark.asyncio
async def test_through_synthesize_and_send_the_line_is_session_gone_at_info(caplog):
    caplog.set_level(logging.DEBUG)
    adapter = _adapter(_GONE)

    class _Gateway:
        async def send_audio(self, call_id, raw):
            await adapter.send_tts_audio("call", raw)

        async def clear_output_buffer(self, call_id):
            return {"ok": True}

        async def flush_tts_buffer(self, call_id):
            return None

        play_pcmu_clip = None

    pipe = _Pipeline(_Provider([b"\x01\x02" * 80]), _Gateway())
    session = _session()
    interrupted = await TtsPlayback(pipe).synthesize_and_send(
        session, "Hello there.", None, track_latency=False
    )
    recs = [r for r in caplog.records if r.getMessage().startswith("tts_delivery_failed")]
    assert len(recs) == 1
    assert recs[0].levelno == logging.INFO
    assert "reason=session_gone" in recs[0].getMessage()
    assert not _errors(caplog)
    assert interrupted is True
    assert session._tts_failure_reason == "session_gone"
