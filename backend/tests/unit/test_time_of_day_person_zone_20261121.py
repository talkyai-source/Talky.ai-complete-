"""The time of day the agent talks about is the PERSON's, not the account's.

20 Nov 2026: UK people were told "have a good evening" at 14:00 UK time because
the hint came from the campaign's (Karachi) zone.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.domain.services.voice_pipeline import time_of_day
from app.domain.services.voice_pipeline.time_of_day import person_time_of_day_line, person_zone

UK = "+442071838750"       # London landline
US_EAST = "+12125551234"   # New York
JP = "+81312345678"        # Tokyo
NOV14 = datetime(2026, 11, 20, 14, 9, tzinfo=timezone.utc)


def _session(phone, campaign_tz="Asia/Karachi"):
    return SimpleNamespace(
        call_id="c1", _dialer_phone=phone, _campaign_timezone=campaign_tz,
        agent_config=SimpleNamespace(timezone=campaign_tz),
    )


def test_uk_number_at_1409_utc_from_karachi_account_is_afternoon():
    line = person_time_of_day_line(_session(UK), _now=NOV14)
    assert "afternoon" in line and "evening" not in line


def test_other_zones_get_their_own_part_of_day():
    now = datetime(2026, 11, 20, 14, 0, tzinfo=timezone.utc)
    assert "morning" in person_time_of_day_line(_session(US_EAST), _now=now)   # 09:00 EST
    assert "evening" in person_time_of_day_line(_session(JP), _now=now)        # 23:00 JST


@pytest.mark.parametrize("phone", [None, "", "unknown", "07911 123456", "+99"])
def test_unknown_number_gives_no_hint_and_no_part_of_day_words(phone):
    line = person_time_of_day_line(_session(phone), _now=NOV14)
    assert line == (
        "You do not know the local time of the person you are speaking with. "
        "Use greetings and goodbyes that do not depend on the time of day."
    )
    for word in ("morning", "afternoon", "evening", "tonight"):
        assert word not in line


@pytest.mark.parametrize("utc_hour,part", [(4, "evening"), (5, "morning"), (11, "morning"),
                                           (12, "afternoon"), (16, "afternoon"),
                                           (17, "evening"), (21, "evening"), (23, "evening")])
def test_module_boundaries_hold_in_the_persons_zone(utc_hour, part):
    now = datetime(2026, 11, 20, utc_hour, 30, tzinfo=timezone.utc)  # UK = UTC in Nov
    assert part in person_time_of_day_line(_session(UK), _now=now)


def test_uk_daylight_saving_july_1730_utc_is_1830_local():
    now = datetime(2026, 7, 8, 17, 30, tzinfo=timezone.utc)
    line = person_time_of_day_line(_session(UK), _now=now)
    assert "~18:00" in line and "evening" in line


def test_lead_zone_on_session_outranks_number():
    s = _session(UK)
    s._dialer_lead_timezone = "America/New_York"
    assert person_zone(s) == ("America/New_York", "lead")


def test_one_log_line_per_call(caplog):
    s = _session(UK)
    with caplog.at_level(logging.INFO, logger=time_of_day.logger.name):
        person_time_of_day_line(s, _now=NOV14)
        person_time_of_day_line(s, _now=NOV14)
    lines = [r.getMessage() for r in caplog.records if "time_of_day_hint" in r.getMessage()]
    assert lines == ["time_of_day_hint call=c1 zone=Europe/London source=number part=afternoon"]


def test_unknown_logs_source_none(caplog):
    with caplog.at_level(logging.INFO, logger=time_of_day.logger.name):
        person_time_of_day_line(_session(None), _now=NOV14)
    assert any(r.getMessage() == "time_of_day_hint call=c1 zone=None source=none part=none"
               for r in caplog.records)


@pytest.mark.asyncio
async def test_streamer_uses_person_zone_not_campaign_zone(monkeypatch):
    from app.domain.services.voice_pipeline import turn_streamer
    from app.domain.services.voice_pipeline.turn_streamer import TurnStreamer
    from tests.unit.test_turn_streamer_tts_delivery_error_transcript_gap import (
        _FakePipeline, _make_session,
    )

    class _Gw:
        async def send_audio(self, *a): return None
        async def clear_output_buffer(self, *a): return {"ok": True}
        async def flush_tts_buffer(self, *a): return None

    monkeypatch.setenv("TELEPHONY_FILLER_DELAY_MS", "0")
    monkeypatch.setattr(time_of_day, "_utcnow", lambda: NOV14)
    seen = []
    real = turn_streamer.build_live_state_block
    monkeypatch.setattr(turn_streamer, "build_live_state_block",
                        lambda **kw: (seen.append(kw["time_of_day_line"]), real(**kw))[1])
    session = _make_session()
    session._dialer_phone = UK
    session._campaign_timezone = "Asia/Karachi"
    await TurnStreamer(_FakePipeline("Sure.", [b"\x01\x02" * 80], _Gw())).stream(session, websocket=None)
    assert seen and "afternoon" in seen[0] and "evening" not in seen[0]
