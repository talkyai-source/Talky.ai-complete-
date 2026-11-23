"""The contact's own time zone reaches the agent's time-of-day greeting.

``time_of_day.person_zone`` has always preferred a zone stamped on the session
(``_dialer_lead_timezone``) over the phone number's zone, but nothing stamped
it. The dialler worker now sends ``leads.timezone`` to the bridge and the bridge
stamps it on the pre-warmed session beside ``_dialer_phone``.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import aiohttp
import pytest

from app.api.v1.endpoints import telephony_bridge
from app.domain.models.calling_rules import CallingRules
from app.domain.models.dialer_job import DialerJob
from app.domain.services.voice_pipeline.time_of_day import (
    person_time_of_day_line,
    person_zone,
)
from app.workers.dialer_worker import DialerCallIntent, DialerWorker
from tests.unit.test_outbound_campaign_boundaries import (
    _campaign,
    _install_call_path,
    _internal_intent_row,
)

UK = "+442071838750"  # London number
# 14:00 UTC: London afternoon (14:00), Dubai evening (18:00).
NOW = datetime(2026, 11, 20, 14, 0, tzinfo=timezone.utc)


async def _run_bridge(monkeypatch, lead_timezone):
    row = _internal_intent_row()
    row["phone_number"] = UK
    path = _install_call_path(
        monkeypatch,
        campaign_rows=[_campaign(), _campaign()],
        internal=True,
        internal_intent_row=row,
    )
    path.body.destination = UK
    if lead_timezone is not None:
        path.body.lead_timezone = lead_timezone
    await telephony_bridge.make_call(path.request, path.body)
    return path.session


@pytest.mark.asyncio
async def test_contact_zone_differing_from_number_zone_is_stamped_and_used(monkeypatch):
    session = await _run_bridge(monkeypatch, "Asia/Dubai")
    assert session._dialer_lead_timezone == "Asia/Dubai"
    assert person_zone(session) == ("Asia/Dubai", "lead")
    line = person_time_of_day_line(session, _now=NOW)
    assert "evening" in line and "afternoon" not in line


@pytest.mark.asyncio
async def test_contact_without_zone_leaves_attribute_unset_and_uses_number(monkeypatch):
    session = await _run_bridge(monkeypatch, None)
    assert not hasattr(session, "_dialer_lead_timezone")
    assert person_zone(session) == ("Europe/London", "number")
    assert "afternoon" in person_time_of_day_line(session, _now=NOW)


@pytest.mark.asyncio
async def test_invalid_contact_zone_falls_through_to_number(monkeypatch):
    session = await _run_bridge(monkeypatch, "Not/AZone")
    assert person_zone(session) == ("Europe/London", "number")
    assert "afternoon" in person_time_of_day_line(session, _now=NOW)


@pytest.mark.asyncio
async def test_zone_never_logged_together_with_the_phone_number(monkeypatch, caplog):
    with caplog.at_level(logging.DEBUG):
        session = await _run_bridge(monkeypatch, "Asia/Dubai")
        person_time_of_day_line(session, _now=NOW)
    for rec in caplog.records:
        msg = rec.getMessage()
        if "Asia/Dubai" in msg:
            assert "442071838750" not in msg and UK not in msg, msg


# -- the dialler worker sends the contact's zone --------------------------


class _Resp:
    status = 500

    async def text(self):
        return "x"

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Sess:
    def __init__(self, sink):
        self._sink = sink

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def post(self, url, **kw):
        self._sink.append(kw["json"])
        return _Resp()


async def _payload(monkeypatch, tz_result):
    sink: list = []
    monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **k: _Sess(sink))
    w = DialerWorker()
    if isinstance(tz_result, Exception):
        w._get_lead_timezone = AsyncMock(side_effect=tz_result)
    else:
        w._get_lead_timezone = AsyncMock(return_value=tz_result)
    job = DialerJob(
        job_id="job-9", campaign_id="campaign-9", lead_id="lead-9",
        tenant_id="tenant-9", phone_number=UK,
    )
    intent = DialerCallIntent("call-1", "tk-1", "leg-1", "initiated", None, True)
    await w._make_call(job, CallingRules.default(), call_intent=intent)
    assert len(sink) == 1
    return sink[0]


@pytest.mark.asyncio
async def test_worker_sends_the_contacts_stored_zone(monkeypatch):
    assert (await _payload(monkeypatch, "Asia/Dubai"))["lead_timezone"] == "Asia/Dubai"


@pytest.mark.asyncio
@pytest.mark.parametrize("stored", [None, "", "   ", "Z" * 65])
async def test_worker_omits_zone_when_contact_has_none(monkeypatch, stored):
    assert "lead_timezone" not in await _payload(monkeypatch, stored)


@pytest.mark.asyncio
async def test_worker_still_places_the_call_when_the_zone_read_fails(monkeypatch):
    payload = await _payload(monkeypatch, OSError("db down"))
    assert "lead_timezone" not in payload
    assert payload["destination"] == UK
    json.dumps(payload)
