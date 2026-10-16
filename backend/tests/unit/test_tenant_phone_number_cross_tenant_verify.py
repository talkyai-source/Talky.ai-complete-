"""Packet W6 item 5: one verified number cannot belong to two tenants.

Production finding (2026-10-16 outbound audit): +442046132300 is a verified
``tenant_phone_numbers`` row for BOTH tenant 790ca2db (its real UK line) and
tenant 1845a165. Ownership checks (``is_verified_for_tenant``) are per tenant,
so 1845a165 could present 790ca2db's number as its caller ID.

``TenantPhoneNumberService.mark_verified`` (platform-admin path) must refuse to
verify a number another tenant already holds as ``verified``, comparing the
normalised E.164 across tenants. The cross-tenant read has to bypass RLS (the
policy hides other tenants' rows from a tenant-scoped connection), so the fake
below simulates that policy: a tenant-scoped connection only sees its own rows.
Existing rows are never changed by this check.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

import pytest

from app.domain.models.tenant_phone_number import VerificationMethod
from app.domain.services.tenant_phone_number_service import (
    TenantPhoneNumberConflictError,
    TenantPhoneNumberError,
    TenantPhoneNumberService,
)

TENANT_A = "790ca2db-0000-4000-8000-000000000001"  # the real UK line owner
TENANT_B = "1845a165-0000-4000-8000-000000000002"  # the tenant asking to verify it
DID_A = "aaaaaaaa-0000-4000-8000-00000000000a"
DID_B = "bbbbbbbb-0000-4000-8000-00000000000b"
NUMBER = "+442046132300"


def _row(did_id, tenant_id, e164, status):
    return {
        "id": did_id,
        "tenant_id": tenant_id,
        "e164": e164,
        "provider": "manual_admin",
        "status": status,
        "verification_method": None,
        "verification_sent_at": None,
        "verified_at": None,
        "verified_by": None,
        "stir_shaken_token": None,
        "label": None,
        "metadata": {},
        "created_at": datetime(2026, 10, 1, tzinfo=timezone.utc),
        "updated_at": None,
    }


class _Table:
    def __init__(self, rows):
        self.rows = rows
        self.audit_events = []
        self.cross_tenant_args = []
        self.events = []


class _Tx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None


class _Conn:
    """Simulates the tenant_phone_numbers RLS policy: unless the connection set
    ``app.bypass_rls`` it only sees rows of ``app.current_tenant_id``."""

    def __init__(self, table):
        self.table = table
        self.bypass = False
        self.tenant = None

    def transaction(self):
        return _Tx()

    def _visible(self):
        return [r for r in self.table.rows if self.bypass or r["tenant_id"] == self.tenant]

    async def execute(self, sql, *args):
        if "app.bypass_rls" in sql:
            self.bypass = True
        match = re.search(r"app\.current_tenant_id = '([^']+)'", sql)
        if match:
            self.tenant = match.group(1)
        if "inbound_audit_events" in sql:
            self.table.audit_events.append(args)
        if "pg_advisory_xact_lock" in sql:
            self.table.events.append(("lock", args))
        return "OK"

    async def fetchrow(self, sql, *args):
        if "<>" in sql:  # the cross-tenant holder lookup
            self.table.cross_tenant_args.append(args)
            self.table.events.append(("lookup", args))
            exclude_tenant, digits = args[0], args[1]
            for r in self._visible():
                if (
                    r["tenant_id"] != exclude_tenant
                    and r["status"] == "verified"
                    and re.sub(r"\D", "", r["e164"]) == digits
                ):
                    return {"tenant_id": r["tenant_id"]}
            return None
        if "FOR UPDATE" in sql:
            tenant_id, did_id = args
            for r in self._visible():
                if r["tenant_id"] == tenant_id and r["id"] == did_id:
                    return dict(r)
            return None
        if sql.lstrip().startswith("UPDATE tenant_phone_numbers"):
            tenant_id, did_id, method, verified_by, _token = args
            for r in self._visible():
                if r["tenant_id"] == tenant_id and r["id"] == did_id:
                    r["status"] = "verified"
                    r["verification_method"] = method
                    r["verified_by"] = verified_by
                    return dict(r)
            return None
        raise AssertionError(f"unexpected fetchrow: {sql}")


class _AcquireCtx:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return None


class _Pool:
    def __init__(self, table):
        self.table = table

    def acquire(self, *_a, **_k):
        return _AcquireCtx(_Conn(self.table))


def _service(*rows):
    table = _Table([dict(r) for r in rows])
    return TenantPhoneNumberService(_Pool(table)), table


async def _verify(svc, tenant_id=TENANT_B, did_id=DID_B):
    return await svc.mark_verified(
        tenant_id=tenant_id,
        did_id=did_id,
        method=VerificationMethod.MANUAL_ADMIN,
        verified_by="platform-admin@example.test",
        proof_reference="case-123",
        actor_id="99999999-0000-4000-8000-000000000009",
        actor_role="platform_admin",
    )


async def test_verification_is_refused_when_another_tenant_holds_the_number_verified():
    svc, table = _service(
        _row(DID_A, TENANT_A, NUMBER, "verified"),
        _row(DID_B, TENANT_B, NUMBER, "pending_verification"),
    )

    with pytest.raises(TenantPhoneNumberConflictError) as exc:
        await _verify(svc)

    assert "another tenant" in str(exc.value)
    assert TENANT_A in str(exc.value), "the platform admin needs to know which tenant holds it"
    # nothing was changed or audited, and the existing row is untouched
    statuses = {r["id"]: r["status"] for r in table.rows}
    assert statuses == {DID_A: "verified", DID_B: "pending_verification"}
    assert table.audit_events == []


async def test_the_conflict_is_a_tenant_phone_number_error_the_endpoint_already_handles():
    svc, _ = _service(
        _row(DID_A, TENANT_A, NUMBER, "verified"),
        _row(DID_B, TENANT_B, NUMBER, "pending_verification"),
    )

    with pytest.raises(TenantPhoneNumberError):
        await _verify(svc)


@pytest.mark.parametrize(
    "other_e164",
    ["442046132300", "+44 20 4613 2300", "+44-20-4613-2300", "+44.20.4613.2300"],
)
async def test_the_comparison_is_on_the_normalised_number_not_the_raw_string(other_e164):
    svc, table = _service(
        _row(DID_A, TENANT_A, other_e164, "verified"),
        _row(DID_B, TENANT_B, NUMBER, "pending_verification"),
    )

    with pytest.raises(TenantPhoneNumberConflictError):
        await _verify(svc)

    assert table.rows[1]["status"] == "pending_verification"


@pytest.mark.parametrize("other_status", ["pending_verification", "revoked", "suspended"])
async def test_a_number_the_other_tenant_does_not_hold_verified_can_be_verified(other_status):
    svc, table = _service(
        _row(DID_A, TENANT_A, NUMBER, other_status),
        _row(DID_B, TENANT_B, NUMBER, "pending_verification"),
    )

    result = await _verify(svc)

    assert result.status == "verified"
    assert table.rows[1]["status"] == "verified"
    assert table.rows[0]["status"] == other_status, "the other tenant's row is never modified"
    assert len(table.audit_events) == 1


async def test_a_different_number_is_not_a_conflict():
    svc, table = _service(
        _row(DID_A, TENANT_A, "+442046132301", "verified"),
        _row(DID_B, TENANT_B, NUMBER, "pending_verification"),
    )

    result = await _verify(svc)

    assert result.status == "verified"


async def test_the_tenants_own_other_row_is_not_a_cross_tenant_conflict():
    own_duplicate = _row("cccccccc-0000-4000-8000-00000000000c", TENANT_B, "442046132300", "verified")
    svc, _ = _service(own_duplicate, _row(DID_B, TENANT_B, NUMBER, "pending_verification"))

    result = await _verify(svc)

    assert result.status == "verified"


async def test_the_cross_tenant_lookup_excludes_the_requesting_tenant_explicitly():
    svc, table = _service(_row(DID_B, TENANT_B, NUMBER, "pending_verification"))

    await _verify(svc)

    assert table.cross_tenant_args, "the holder lookup must run"
    exclude_tenant, digits = table.cross_tenant_args[0]
    assert exclude_tenant == TENANT_B
    assert digits == "442046132300"


async def test_re_verifying_a_number_the_tenant_already_holds_still_works():
    svc, _ = _service(_row(DID_B, TENANT_B, NUMBER, "verified"))

    result = await _verify(svc)

    assert result.status == "verified"


async def test_missing_and_revoked_dids_keep_their_existing_errors():
    svc, _ = _service(_row(DID_B, TENANT_B, NUMBER, "revoked"))
    with pytest.raises(TenantPhoneNumberError, match="revoked"):
        await _verify(svc)

    svc, _ = _service()
    with pytest.raises(TenantPhoneNumberError, match="not found"):
        await _verify(svc)


async def test_verifications_of_one_number_are_serialised_before_the_holder_lookup():
    """Two admins verifying the same number for two tenants at once must not
    both pass: an advisory lock keyed on the normalised number is taken in the
    verifying transaction BEFORE the cross-tenant lookup."""
    svc, table = _service(_row(DID_B, TENANT_B, "+44 20 4613 2300", "pending_verification"))

    await _verify(svc)

    kinds = [kind for kind, _ in table.events]
    assert kinds[:2] == ["lock", "lookup"], kinds
    assert table.events[0][1] == ("tpn_verify:442046132300",)
