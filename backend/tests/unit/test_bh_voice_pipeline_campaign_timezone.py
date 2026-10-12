"""voice-pipeline-6: the live-state time-of-day line must use the callee's
campaign timezone, not a hard-coded Europe/London.

Nothing ever wrote session._campaign_timezone and AgentConfig has no timezone
field, so every call was told the London hour/date. The streamer now loads
campaigns.calling_config.timezone (tenant-scoped, once per call) and, with no
campaign timezone, only assumes London for a UK (GB) contact region.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from app.domain.services.voice_pipeline import time_of_day, turn_streamer
from app.domain.services.voice_pipeline.turn_streamer import TurnStreamer

from tests.unit.test_turn_streamer_tts_delivery_error_transcript_gap import (
    _FakePipeline,
    _make_session,
)

pytestmark = pytest.mark.asyncio

TENANT = "11111111-1111-1111-1111-111111111111"
CAMPAIGN = "22222222-2222-2222-2222-222222222222"


class _OkGateway:
    async def send_audio(self, call_id, raw):
        return None

    async def clear_output_buffer(self, call_id):
        return {"ok": True}

    async def flush_tts_buffer(self, call_id):
        return None


@pytest.fixture
def tz_calls(monkeypatch):
    monkeypatch.setenv("TELEPHONY_FILLER_DELAY_MS", "0")
    calls = []

    def _spy(tz_name, **_kw):
        calls.append(tz_name)
        return ""

    monkeypatch.setattr(time_of_day, "time_of_day_line", _spy)
    return calls


def _db(monkeypatch, row_value, log):
    class _Conn:
        async def fetchval(self, sql, *args):
            log["args"] = args
            log["sql"] = sql
            return row_value

    @asynccontextmanager
    async def _acquire(_pool, tenant):
        log["tenant"] = tenant
        yield _Conn()

    monkeypatch.setattr(turn_streamer, "acquire_with_tenant", _acquire)
    monkeypatch.setattr(
        "app.core.container.get_container",
        lambda: SimpleNamespace(is_initialized=True, db_pool=object()),
    )


async def _run(session):
    pipeline = _FakePipeline("Sure thing.", [b"\x01\x02" * 80], _OkGateway())
    await TurnStreamer(pipeline).stream(session, websocket=None)


async def test_campaign_timezone_is_loaded_tenant_scoped_and_used(tz_calls, monkeypatch):
    log = {}
    _db(monkeypatch, '{"timezone": "Asia/Karachi", "time_window_start": "09:00"}', log)
    session = _make_session()
    session._dialer_tenant_id = TENANT
    session._dialer_campaign_id = CAMPAIGN

    await _run(session)

    assert tz_calls == ["Asia/Karachi"]
    assert log["tenant"] == TENANT
    assert log["args"] == (CAMPAIGN, TENANT)
    assert "tenant_id" in log["sql"]


async def test_timezone_is_looked_up_once_per_call(tz_calls, monkeypatch):
    log = {}
    _db(monkeypatch, {"timezone": "America/Chicago"}, log)
    session = _make_session()
    session._dialer_tenant_id = TENANT
    session._dialer_campaign_id = CAMPAIGN

    await _run(session)
    log.clear()
    await _run(session)

    assert tz_calls == ["America/Chicago", "America/Chicago"]
    assert log == {}, "second turn must reuse the cached zone, not query again"


async def test_no_campaign_timezone_does_not_assume_london_for_a_non_uk_callee(tz_calls, monkeypatch):
    _db(monkeypatch, None, {})
    session = _make_session()
    session.contact_phone_region = "PK"
    session._dialer_tenant_id = TENANT
    session._dialer_campaign_id = CAMPAIGN

    await _run(session)

    assert tz_calls == [None]


async def test_no_campaign_timezone_keeps_london_for_an_explicit_uk_region(tz_calls, monkeypatch):
    _db(monkeypatch, None, {})
    session = _make_session()
    session.contact_phone_region = "GB"
    session._dialer_tenant_id = TENANT
    session._dialer_campaign_id = CAMPAIGN

    await _run(session)

    assert tz_calls == ["Europe/London"]


async def test_session_already_carrying_a_timezone_skips_the_lookup(tz_calls, monkeypatch):
    log = {}
    _db(monkeypatch, {"timezone": "Asia/Dubai"}, log)
    session = _make_session()
    session._campaign_timezone = "America/New_York"

    await _run(session)

    assert tz_calls == ["America/New_York"]
    assert log == {}
