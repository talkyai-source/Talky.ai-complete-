"""The Call Issues card for a call dropped because the contact's number changed.

The dialler drops a queued call whose number no longer matches its contact. The
card for that job used to mislead: a dropped RETRY still carried the reason of the
attempt before it ("No answer: it will retry", although nothing will), and a first
job fell back to generic "check caller ID, voice provider, calling hours" advice.
The drop now records its own reason where the card reads it, and the reason has its
own advice, which says how the new number gets called.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.domain.models.dialer_job import JobStatus
from app.domain.services.call_issue_advice import advise
from tests.unit.test_w1_dialer_job_lifecycle import _job, _worker


def test_the_reason_has_its_own_advice():
    advice = advise("lead_number_changed")
    assert advice.title == "Number changed after the call was queued"
    assert "old number was not placed" in advice.suggestion
    assert "pause and resume" in advice.suggestion
    # neither of the two cards it used to get
    assert advice != advise("no_answer")
    assert advice != advise("some_reason_nobody_wrote_advice_for")


@pytest.mark.parametrize("stored", ["lead_number_changed", "stale_payload:lead_number_changed"])
def test_the_advice_is_found_however_the_reason_is_stored(stored):
    assert advise(stored).title == "Number changed after the call was queued"


def test_the_neighbouring_reasons_keep_their_own_advice():
    assert advise("contact_permission_missing").title == "Permission to call not confirmed"
    assert advise("lead_not_dialable").title == "Contact can't be dialed"


@pytest.mark.asyncio
async def test_the_drop_records_its_reason_where_the_card_reads_it():
    w = _worker()
    w._release_lead_of_stopped_campaign_job = AsyncMock()

    await w._drop_stale_payload(_job(), "lead_number_changed")

    w._update_job_status.assert_awaited_once()
    call = w._update_job_status.call_args
    assert call.args[1] == JobStatus.SKIPPED
    assert call.kwargs["error"] == "lead_number_changed"
    assert call.kwargs["reason"] == "lead_number_changed"


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["lead_not_dialable", "contact_permission_missing"])
async def test_the_other_drops_write_what_they_wrote_before(reason):
    w = _worker()
    w._release_lead_of_stopped_campaign_job = AsyncMock()
    w._publish_block = AsyncMock()

    await w._drop_stale_payload(_job(), reason)

    call = w._update_job_status.call_args
    assert call.kwargs["error"] == reason
    assert call.kwargs.get("reason") is None
