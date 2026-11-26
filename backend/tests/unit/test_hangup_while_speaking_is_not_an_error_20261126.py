"""A caller hanging up while the agent is speaking is a normal event.

Production 25 Nov: 17 of 21 ERROR lines were
``tts_delivery_failed ... reason=session_gone``. That reason means the call
ended while a sentence was being sent. It is logged at INFO now; every other
reason stays at ERROR. The text of the line is unchanged.
"""
from __future__ import annotations

import logging

import pytest

from app.domain.services.voice_pipeline.tts_playback import TtsPlayback
from app.infrastructure.telephony.asterisk_adapter import TtsDeliveryError
from app.infrastructure.telephony.browser_media_gateway import SessionGoneError

from tests.unit.test_tts_delivery_error_stops_turn import _Pipeline, _Provider, _session

_LOGGER = "app.domain.services.voice_pipeline.tts_playback"


def _gateway(exc):
    class _Gateway:
        async def send_audio(self, call_id, raw):
            raise exc

        async def clear_output_buffer(self, call_id):
            return {"ok": True}

        async def flush_tts_buffer(self, call_id):
            return None

        play_pcmu_clip = None

    return _Gateway()


async def _run(exc):
    pipe = _Pipeline(_Provider([b"\x01\x02" * 80]), _gateway(exc))
    session = _session()
    interrupted = await TtsPlayback(pipe).synthesize_and_send(
        session, "Hello there.", None, track_latency=False
    )
    return interrupted, session, pipe


def _failed_records(caplog):
    return [r for r in caplog.records if r.getMessage().startswith("tts_delivery_failed")]


@pytest.mark.asyncio
async def test_session_gone_is_logged_at_info_not_error(caplog):
    caplog.set_level(logging.DEBUG, logger=_LOGGER)
    interrupted, session, _ = await _run(SessionGoneError("call ended"))
    recs = _failed_records(caplog)
    assert len(recs) == 1
    assert recs[0].levelno == logging.INFO
    assert "reason=session_gone" in recs[0].getMessage()
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    # behaviour unchanged
    assert interrupted is True
    assert session._tts_delivery_failed is True
    assert session._tts_failure_reason == "session_gone"


@pytest.mark.asyncio
async def test_other_delivery_failure_is_still_an_error(caplog):
    caplog.set_level(logging.DEBUG, logger=_LOGGER)
    _, session, _ = await _run(TtsDeliveryError("gateway refused"))
    recs = _failed_records(caplog)
    assert len(recs) == 1
    assert recs[0].levelno == logging.ERROR
    assert "reason=tts_delivery_error" in recs[0].getMessage()
    assert session._tts_failure_reason == "tts_delivery_error"
