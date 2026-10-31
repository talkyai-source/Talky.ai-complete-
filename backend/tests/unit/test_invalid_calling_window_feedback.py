"""An invalid calling window must say so (review 2026-10-20).

``CallingRules.is_within_time_window`` fails closed with ``invalid_time_window``
for an unreadable window or one whose start is after its end (overnight windows
are deliberately not supported: consumer calling hours never span midnight).
Before this, the code had no mapping: the campaign showed a generic "paused"
card, never named the calling hours as the cause, and re-polled every 5 minutes.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.api.v1.schemas.campaigns import CampaignCallingSchedule
from app.domain.services.call_issue_advice import advise
from app.domain.services.dialer.block_reasons import (
    SCHEDULE_BLOCK_CODES,
    BlockCode,
    classify,
)


def test_invalid_window_is_classified_and_named():
    reason = classify("invalid_time_window")
    assert reason.code is BlockCode.SCHEDULE_INVALID
    assert reason.severity == "error"
    assert reason.stage == "schedule"
    assert "calling hours" in reason.message.lower()


def test_invalid_window_is_not_a_bypassable_schedule_wait():
    # SCHEDULE_BLOCK_CODES feed the testing override and "wait for the window
    # to open": an invalid window has no opening and must stay closed.
    assert BlockCode.SCHEDULE_INVALID not in SCHEDULE_BLOCK_CODES


@pytest.mark.parametrize("code", ["invalid_time_window", "schedule_invalid"])
def test_call_issues_card_names_the_calling_hours(code):
    advice = advise(code)
    assert advice.title == "Calling hours are invalid"
    assert advice.severity == "error"


@pytest.mark.asyncio
@pytest.mark.parametrize("override", [False, True])
async def test_dialer_never_dials_an_invalid_window_and_rechecks_hourly(override, monkeypatch):
    from unittest.mock import AsyncMock

    from app.domain.services.dialer.testing_override import TESTING_OVERRIDE_ENV
    from tests.unit.test_dialer_worker_block_visibility import _job, _worker

    if override:
        monkeypatch.setenv(TESTING_OVERRIDE_ENV, "true")  # must not open it either
    else:
        monkeypatch.delenv(TESTING_OVERRIDE_ENV, raising=False)
    w = _worker()
    w.rules_engine.can_make_call = AsyncMock(return_value=(False, "invalid_time_window"))

    await w.process_job(_job())

    w._make_call.assert_not_awaited()
    _, kwargs = w.queue_service._redefer_inflight.call_args
    assert kwargs["delay_seconds"] == 3600  # hourly, not every 5 minutes


def test_campaign_schedule_rejects_a_window_that_ends_before_it_starts():
    with pytest.raises(ValidationError, match="start before"):
        CampaignCallingSchedule(time_window_start="22:00", time_window_end="06:00")


def test_campaign_schedule_accepts_a_normal_window_and_partial_updates():
    ok = CampaignCallingSchedule(time_window_start="09:00", time_window_end="17:30")
    assert (ok.time_window_start, ok.time_window_end) == ("09:00", "17:30")
    # One side only: the other comes from the tenant default, checked at dial time.
    assert CampaignCallingSchedule(time_window_end="06:00").time_window_end == "06:00"
