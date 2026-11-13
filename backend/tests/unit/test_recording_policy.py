"""T0.4 — per-tenant recording consent policy.

Covers the policy service's fail-closed behaviours:

1. Missing tenant row → recording off until explicitly configured.
2. Explicit `disabled` → no recording, no announcement.
3. `one_party` → record, no announcement, except to a callee whose country
   needs one or can't be placed (QA F12-04).
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


class _FakeTxn:
    """asyncpg transaction double.

    ``acquire_with_tenant`` opens a transaction before issuing
    ``SET LOCAL app.current_tenant_id``, because SET LOCAL is transaction
    scoped — without the transaction the GUC would leak back to the pool.
    A pool double therefore has to model ``transaction()`` and ``execute()``
    or the service falls into its lookup-failure branch.
    """

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


class _FakeConn:
    def __init__(self, row: Any):
        self._row = row

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    def transaction(self):
        return _FakeTxn()

    async def execute(self, *args, **kwargs):
        return None

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
    decision = await svc.decide(tenant_id="11111111-1111-4111-8111-111111111111")
    assert decision.should_record is False
    assert decision.announcement_required is False
    assert decision.announcement_text is None
    assert decision.reason == "tenant_policy_absent_recording_off"


@pytest.mark.asyncio
async def test_disabled_mode_blocks_recording():
    row = {**_BASE_ROW, "default_consent_mode": CONSENT_DISABLED}
    svc = RecordingPolicyService(_FakePool(row))
    decision = await svc.decide(tenant_id="11111111-1111-4111-8111-111111111111")
    assert decision.should_record is False
    assert decision.announcement_required is False
    assert decision.reason == "tenant_policy_disabled"


@pytest.mark.asyncio
async def test_one_party_records_without_announcement():
    """A callee in a one-party country (Pakistan): record, no notice."""
    row = {**_BASE_ROW, "default_consent_mode": CONSENT_ONE_PARTY}
    svc = RecordingPolicyService(_FakePool(row))
    decision = await svc.decide(
        tenant_id="11111111-1111-4111-8111-111111111111", callee_country_code="PK"
    )
    assert decision.should_record is True
    assert decision.announcement_required is False
    assert decision.reason == "tenant_policy_one_party"


# QA F12-04 (QA-CMP-006): a one-party account recorded calls to California,
# Washington, Florida, Germany, France, the UK, Australia and Canada with no
# notice. Its one-party assertion can't cover a callee whose own law needs one,
# whatever the tenant's own two-party list says. A US number places the callee
# only in "US", which can't rule out a two-party state.
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "callee", ["US-CA", "US-WA", "US-FL", "DE", "FR", "GB", "AU", "CA", "US"]
)
async def test_one_party_announces_to_a_callee_whose_law_needs_notice(callee):
    row = {**_BASE_ROW, "default_consent_mode": CONSENT_ONE_PARTY}
    svc = RecordingPolicyService(_FakePool(row))
    decision = await svc.decide(
        tenant_id="11111111-1111-4111-8111-111111111111", callee_country_code=callee
    )
    assert decision.should_record is True
    assert decision.announcement_required is True
    assert decision.announcement_text == row["announcement_text"]
    assert decision.opt_out_dtmf_digit == "9"
    assert decision.reason == "tenant_policy_one_party_two_party_destination"


@pytest.mark.asyncio
async def test_one_party_unknown_callee_announces():
    """No readable number on the call: the callee can't be placed → announce."""
    row = {**_BASE_ROW, "default_consent_mode": CONSENT_ONE_PARTY}
    svc = RecordingPolicyService(_FakePool(row))
    decision = await svc.decide(tenant_id="11111111-1111-4111-8111-111111111111")
    assert decision.should_record is True
    assert decision.announcement_required is True


@pytest.mark.asyncio
async def test_one_party_floor_holds_with_an_empty_tenant_list():
    row = {
        **_BASE_ROW,
        "default_consent_mode": CONSENT_ONE_PARTY,
        "two_party_country_codes": [],
    }
    svc = RecordingPolicyService(_FakePool(row))
    tenant = "11111111-1111-4111-8111-111111111111"
    uk = await svc.decide(tenant_id=tenant, callee_country_code="GB")
    pakistan = await svc.decide(tenant_id=tenant, callee_country_code="PK")
    assert uk.announcement_required is True
    assert pakistan.announcement_required is False


@pytest.mark.asyncio
async def test_two_party_decision_ignores_the_callee_country():
    """The callee country only feeds the one-party floor. A two-party tenant
    still announces on every call whose destination is unknown, as before."""
    svc = RecordingPolicyService(_FakePool(dict(_BASE_ROW)))
    decision = await svc.decide(
        tenant_id="11111111-1111-4111-8111-111111111111", callee_country_code="PK"
    )
    assert decision.announcement_required is True
    assert decision.reason == "tenant_policy_two_party_announce"


def test_platform_two_party_list_is_the_policy_table_default():
    """The floor is the default two_party_country_codes of the policy table."""
    import re
    from pathlib import Path

    from app.domain.services.recording_policy_service import PLATFORM_TWO_PARTY_CODES

    sql = (
        Path(__file__).resolve().parents[2]
        / "database" / "migrations" / "20260425_add_tenant_recording_policy.sql"
    ).read_text(encoding="utf-8")
    default = re.search(
        r"two_party_country_codes\s+TEXT\[\] NOT NULL DEFAULT ARRAY\[(.*?)\]::TEXT\[\]",
        sql,
        re.S,
    ).group(1)
    assert set(re.findall(r"'([A-Z-]+)'", default)) == set(PLATFORM_TWO_PARTY_CODES)


@pytest.mark.asyncio
async def test_two_party_with_matching_country_announces():
    svc = RecordingPolicyService(_FakePool(dict(_BASE_ROW)))
    decision = await svc.decide(tenant_id="11111111-1111-4111-8111-111111111111", destination_country_code="DE")
    assert decision.should_record is True
    assert decision.announcement_required is True
    assert decision.announcement_text == _BASE_ROW["announcement_text"]
    assert decision.opt_out_dtmf_digit == "9"


@pytest.mark.asyncio
async def test_two_party_with_subdivision_match():
    """US-CA must match even though the list stores it with the dash."""
    svc = RecordingPolicyService(_FakePool(dict(_BASE_ROW)))
    decision = await svc.decide(tenant_id="11111111-1111-4111-8111-111111111111", destination_country_code="US-CA")
    assert decision.announcement_required is True


@pytest.mark.asyncio
async def test_two_party_with_non_matching_country_skips_announcement():
    """One-party-consent state (e.g. NY) against a two_party list of
    {US-CA, DE, GB} → record, no announcement."""
    svc = RecordingPolicyService(_FakePool(dict(_BASE_ROW)))
    decision = await svc.decide(tenant_id="11111111-1111-4111-8111-111111111111", destination_country_code="US-NY")
    assert decision.should_record is True
    assert decision.announcement_required is False
    assert decision.reason == "tenant_policy_two_party_no_announce"


@pytest.mark.asyncio
async def test_two_party_empty_list_announces_everywhere():
    """Empty two_party list = safe default, announce for every call."""
    row = {**_BASE_ROW, "two_party_country_codes": []}
    svc = RecordingPolicyService(_FakePool(row))
    decision = await svc.decide(tenant_id="11111111-1111-4111-8111-111111111111", destination_country_code="JP")
    assert decision.announcement_required is True


@pytest.mark.asyncio
async def test_two_party_unknown_country_defaults_to_announce():
    """Missing cc = we don't know the jurisdiction → announce to be safe."""
    svc = RecordingPolicyService(_FakePool(dict(_BASE_ROW)))
    decision = await svc.decide(tenant_id="11111111-1111-4111-8111-111111111111", destination_country_code=None)
    assert decision.announcement_required is True


@pytest.mark.asyncio
async def test_db_failure_falls_back_to_safe_default():
    class BrokenPool:
        def acquire(self):
            raise RuntimeError("db down")
    svc = RecordingPolicyService(BrokenPool())
    decision = await svc.decide(tenant_id="11111111-1111-4111-8111-111111111111")
    assert decision.should_record is False
    assert decision.announcement_required is False
    assert decision.reason == "recording_policy_lookup_failed_recording_off"


@pytest.mark.asyncio
async def test_bare_country_against_subdivision_policy_announces():
    """Known location is the bare country "US" while the policy is keyed to
    the subdivision "US-CA".

    We cannot prove the callee is OUTSIDE California, so the coarser known
    country must fail CLOSED and announce. Returning False here silently
    recorded two-party-consent calls with no notice.
    """
    row = {**_BASE_ROW, "two_party_country_codes": ["US-CA"]}
    svc = RecordingPolicyService(_FakePool(row))
    decision = await svc.decide(tenant_id="11111111-1111-4111-8111-111111111111", destination_country_code="US")
    assert decision.announcement_required is True


@pytest.mark.asyncio
async def test_unrelated_bare_country_against_subdivision_policy_unchanged():
    """"GB" shares no country with "US-CA" → still no announcement."""
    row = {**_BASE_ROW, "two_party_country_codes": ["US-CA"]}
    svc = RecordingPolicyService(_FakePool(row))
    decision = await svc.decide(tenant_id="11111111-1111-4111-8111-111111111111", destination_country_code="GB")
    assert decision.announcement_required is False
    assert decision.should_record is True
