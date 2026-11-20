"""The live-state time-of-day line is the PERSON's clock (20 Nov 2026 rule).

It comes from the dialler's number-to-zone function on the dialled / caller
number, never from campaigns.calling_config.timezone and never an assumed
London for region GB. No usable number = no part-of-day hint.
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


UK_NUMBER = "+442071838750"


async def test_uk_number_with_karachi_campaign_gets_the_uk_zone_not_the_campaign_zone(tz_calls, monkeypatch):
    log = {}
    _db(monkeypatch, '{"timezone": "Asia/Karachi", "time_window_start": "09:00"}', log)
    session = _make_session()
    session._dialer_tenant_id = TENANT
    session._dialer_campaign_id = CAMPAIGN
    session._dialer_phone = UK_NUMBER

    await _run(session)

    assert tz_calls == ["Europe/London"]
    assert "Asia/Karachi" not in tz_calls


async def test_hint_never_reads_the_campaign_timezone_and_resolves_the_person_once(tz_calls, monkeypatch):
    log = {}
    _db(monkeypatch, {"timezone": "America/Chicago"}, log)
    resolved = []
    real = time_of_day.person_zone
    monkeypatch.setattr(time_of_day, "person_zone", lambda s: (resolved.append(1), real(s))[1])
    session = _make_session()
    session._dialer_tenant_id = TENANT
    session._dialer_campaign_id = CAMPAIGN
    session._dialer_phone = UK_NUMBER

    await _run(session)
    await _run(session)

    assert tz_calls == ["Europe/London", "Europe/London"]
    assert log == {}, "building the hint must not query the campaign calling_config at all"
    assert len(resolved) == 1, "the person's zone is resolved once per call, then cached"


async def test_no_campaign_timezone_does_not_assume_london_for_a_non_uk_callee(tz_calls, monkeypatch):
    _db(monkeypatch, None, {})
    session = _make_session()
    session.contact_phone_region = "PK"
    session._dialer_tenant_id = TENANT
    session._dialer_campaign_id = CAMPAIGN

    await _run(session)

    assert tz_calls == [None]


async def test_no_number_and_explicit_gb_region_gets_no_part_of_day_hint(tz_calls, monkeypatch):
    log = {}
    _db(monkeypatch, None, log)
    session = _make_session()
    session.contact_phone_region = "GB"
    session._dialer_tenant_id = TENANT
    session._dialer_campaign_id = CAMPAIGN

    await _run(session)

    assert tz_calls == [None], "region GB alone no longer assumes London"
    assert log == {}


async def test_session_carrying_a_campaign_timezone_still_gets_the_persons_zone(tz_calls, monkeypatch):
    log = {}
    _db(monkeypatch, {"timezone": "Asia/Dubai"}, log)
    session = _make_session()
    session._campaign_timezone = "America/New_York"
    session._dialer_phone = UK_NUMBER

    await _run(session)

    assert tz_calls == ["Europe/London"]
    assert log == {}
