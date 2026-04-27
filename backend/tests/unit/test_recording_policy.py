"""T0.4 — per-tenant recording consent policy.

Covers the four behaviours the policy service promises:

1. Missing tenant row → safe default (two-party consent, announce).
2. Explicit `disabled` → no recording, no announcement.
3. `one_party` → record, no announcement.
4. `two_party` + destination in two-party list → announce.
   `two_party` + destination NOT in list → record without announcement.
"""
from __future__ import annotations

from typing import Any

import pytest

from app.domain.services.recording_policy_service import (
    RecordingPolicyService,
    CONSENT_DISABLED,
    CONSENT_ONE_PARTY,
    CONSENT_TWO_PARTY,
)


class _FakeConn:
    def __init__(self, row: Any):
        self._row = row

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def fetchrow(self, *args, **kwargs):
        return self._row


class _FakePool:
    def __init__(self, row: Any = None):
        self._row = row

    def acquire(self):
        return _FakeConn(self._row)


_BASE_ROW = {
    "default_consent_mode": CONSENT_TWO_PARTY,
    "announcement_text": "Recording notice — press 9 to opt out.",
    "opt_out_dtmf_digit": "9",
    "two_party_country_codes": ["US-CA", "DE", "GB"],
    "retention_days": 30,
}


@pytest.mark.asyncio
async def test_missing_row_safe_default():
    svc = RecordingPolicyService(_FakePool(None))
    decision = await svc.decide(tenant_id="t1")
    assert decision.should_record is True
    assert decision.announcement_required is True
    assert decision.announcement_text is not None
    assert decision.reason == "tenant_default_two_party"


@pytest.mark.asyncio
async def test_disabled_mode_blocks_recording():
    row = {**_BASE_ROW, "default_consent_mode": CONSENT_DISABLED}
    svc = RecordingPolicyService(_FakePool(row))
    decision = await svc.decide(tenant_id="t1")
    assert decision.should_record is False
    assert decision.announcement_required is False
    assert decision.reason == "tenant_policy_disabled"


@pytest.mark.asyncio
async def test_one_party_records_without_announcement():
    row = {**_BASE_ROW, "default_consent_mode": CONSENT_ONE_PARTY}
    svc = RecordingPolicyService(_FakePool(row))
    decision = await svc.decide(tenant_id="t1")
    assert decision.should_record is True
    assert decision.announcement_required is False
    assert decision.reason == "tenant_policy_one_party"


@pytest.mark.asyncio
async def test_two_party_with_matching_country_announces():
    svc = RecordingPolicyService(_FakePool(dict(_BASE_ROW)))
    decision = await svc.decide(tenant_id="t1", destination_country_code="DE")
    assert decision.should_record is True
    assert decision.announcement_required is True
    assert decision.announcement_text == _BASE_ROW["announcement_text"]
    assert decision.opt_out_dtmf_digit == "9"


@pytest.mark.asyncio
async def test_two_party_with_subdivision_match():
    """US-CA must match even though the list stores it with the dash."""
    svc = RecordingPolicyService(_FakePool(dict(_BASE_ROW)))
    decision = await svc.decide(tenant_id="t1", destination_country_code="US-CA")
    assert decision.announcement_required is True


@pytest.mark.asyncio
async def test_two_party_with_non_matching_country_skips_announcement():
    """One-party-consent state (e.g. NY) against a two_party list of
    {US-CA, DE, GB} → record, no announcement."""
    svc = RecordingPolicyService(_FakePool(dict(_BASE_ROW)))
    decision = await svc.decide(tenant_id="t1", destination_country_code="US-NY")
    assert decision.should_record is True
    assert decision.announcement_required is False
    assert decision.reason == "tenant_policy_two_party_no_announce"


@pytest.mark.asyncio
async def test_two_party_empty_list_announces_everywhere():
    """Empty two_party list = safe default, announce for every call."""
    row = {**_BASE_ROW, "two_party_country_codes": []}
    svc = RecordingPolicyService(_FakePool(row))
    decision = await svc.decide(tenant_id="t1", destination_country_code="JP")
    assert decision.announcement_required is True


@pytest.mark.asyncio
async def test_two_party_unknown_country_defaults_to_announce():
    """Missing cc = we don't know the jurisdiction → announce to be safe."""
    svc = RecordingPolicyService(_FakePool(dict(_BASE_ROW)))
    decision = await svc.decide(tenant_id="t1", destination_country_code=None)
    assert decision.announcement_required is True


@pytest.mark.asyncio
async def test_db_failure_falls_back_to_safe_default():
    class BrokenPool:
        def acquire(self):
            raise RuntimeError("db down")
    svc = RecordingPolicyService(BrokenPool())
    decision = await svc.decide(tenant_id="t1")
    assert decision.should_record is True
    assert decision.announcement_required is True
    assert decision.reason == "tenant_default_two_party"
