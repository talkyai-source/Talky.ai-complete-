"""Formal white-label parent/hold authority at actual call admission."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import asyncpg
import pytest

from app.domain.services.call_guard import CallGuard
from app.domain.services.telephony.call_authority import (
    CallAuthorityUnavailable,
    evaluate_call_authority,
    read_call_authority_rows,
    read_formal_account_authority,
)


TENANT = "00000000-0000-0000-0000-000000000501"
PARENT = "00000000-0000-0000-0000-000000000502"
ACTOR = "00000000-0000-0000-0000-000000000503"
NOW = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)


def authority_row(**overrides):
    return {
        "id": TENANT,
        "status": "active",
        "subscription_status": "active",
        "suspended_at": None,
        "suspended_by": None,
        "suspension_reason": None,
        "white_label_partner_id": None,
        "formal_partner_id": None,
        "formal_partner_status": None,
        "active_holds": [],
        "authority_scope_ok": True,
        **overrides,
    }


def hold(**overrides):
    return {
        "suspension_id": str(uuid4()),
        "target_type": "tenant",
        "target_id": TENANT,
        "suspended_at": NOW.isoformat(),
        "audited": True,
        "policy_version": 1,
        "immediate": True,
        "affected": True,
        "tenant_state": None,
        **overrides,
    }


class Context:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *args):
        return None


class Connection:
    def __init__(self, row, parent_error=None):
        self.row = row
        self.parent_error = parent_error
        self.statements = []

    def transaction(self):
        return Context(self)

    async def execute(self, query, *args):
        self.statements.append((query, args))

    async def fetchrow(self, query, *args):
        self.statements.append((query, args))
        if self.parent_error and "white_label_partners" in query:
            raise self.parent_error
        return self.row


class Pool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self, **kwargs):
        return Context(self.conn)


@pytest.mark.parametrize(
    "case",
    ["parent_suspended", "parent_missing", "parent_read_error", "tenant_hold", "partner_hold"],
)
async def test_active_child_cannot_bypass_formal_parent_or_active_hold(case):
    row = authority_row(
        white_label_partner_id=PARENT, formal_partner_id=PARENT, formal_partner_status="active"
    )
    error = None
    if case == "parent_suspended":
        row["formal_partner_status"] = "suspended"
    elif case == "parent_missing":
        row["formal_partner_id"] = None
    elif case == "parent_read_error":
        error = asyncpg.ConnectionDoesNotExistError("parent read unavailable")
    elif case == "tenant_hold":
        row["active_holds"] = [hold()]
    else:
        row["active_holds"] = [hold(target_type="partner", target_id=PARENT)]
    result = await CallGuard(Pool(Connection(row, error)))._check_tenant_active(TENANT)
    assert result.passed is False


def deferred_row():
    prior = {
        "status": "active",
        "suspended_at": None,
        "suspended_by": None,
        "suspension_reason": None,
    }
    held = {
        "status": "suspended",
        "suspended_at": NOW.isoformat(),
        "suspended_by": ACTOR,
        "suspension_reason": "reviewed hold",
    }
    return authority_row(
        **held, active_holds=[hold(immediate=False, tenant_state={"prior": prior, "held": held})]
    )


@pytest.mark.parametrize("parent", [False, True])
async def test_active_account_reads_current_formal_authority_under_service_scope(parent):
    row = authority_row()
    if parent:
        row.update(
            white_label_partner_id=PARENT, formal_partner_id=PARENT, formal_partner_status="active"
        )
    conn = Connection(row)
    assert (await CallGuard(Pool(conn))._check_tenant_active(TENANT)).passed is True
    assert any("SET LOCAL app.bypass_rls = 'on'" in sql for sql, args in conn.statements)
    sql, args = conn.statements[-1]
    assert "p.id = t.white_label_partner_id" in sql
    assert "WHERE t.id = $1::uuid" in sql and str(args[0]) == TENANT
    assert "partner_id = $1" not in sql


@pytest.mark.parametrize(
    "error",
    [
        asyncpg.UndefinedTableError("missing schema"),
        asyncpg.UndefinedColumnError("missing field"),
        TimeoutError("read deadline"),
        PermissionError("authority denied"),
    ],
)
async def test_unknown_or_unmigrated_authority_is_deferred_not_allowed(error):
    result = await CallGuard(Pool(Connection(authority_row(), error)))._check_tenant_active(TENANT)
    assert result.passed is False and result.reason.startswith("check_error_blocked")


def test_missing_tenant_is_denied():
    assert evaluate_call_authority(None).allowed is False


@pytest.mark.parametrize(
    "missing",
    ["white_label_partner_id", "active_holds", "authority_scope_ok", "subscription_status"],
)
def test_partial_authority_projection_cannot_allow(missing):
    row = authority_row()
    row.pop(missing)
    with pytest.raises(CallAuthorityUnavailable):
        evaluate_call_authority(row)


@pytest.mark.parametrize("value", [False, None, "true", 1])
def test_tenant_scoped_or_malformed_scope_cannot_hide_parent_or_snapshot_holds(value):
    with pytest.raises(CallAuthorityUnavailable):
        evaluate_call_authority(authority_row(authority_scope_ok=value))


@pytest.mark.parametrize("holds", [None, {}, "bad json", "null", [None]])
def test_unreadable_hold_projection_never_means_no_hold(holds):
    with pytest.raises(CallAuthorityUnavailable):
        evaluate_call_authority(authority_row(active_holds=holds))


def test_jsonb_codec_string_has_same_hold_semantics():
    import json

    assert (
        evaluate_call_authority(authority_row(active_holds=json.dumps([hold()]))).allowed is False
    )


def test_only_existing_pre_hold_call_is_exempt_from_proven_non_immediate_hold():
    row = deferred_row()
    assert evaluate_call_authority(row).allowed is False
    assert (
        evaluate_call_authority(
            row, purpose="existing_call", existing_call_started_at=NOW - timedelta(seconds=1)
        ).allowed
        is True
    )


def test_remaining_audited_hold_can_retain_original_older_owned_marker():
    row = deferred_row()
    row["active_holds"][0]["suspended_at"] = (NOW + timedelta(seconds=5)).isoformat()
    assert (
        evaluate_call_authority(
            row, purpose="existing_call", existing_call_started_at=NOW - timedelta(seconds=1)
        ).allowed
        is True
    )
    # A purported marker from after this hold's creation proves no ownership.
    row["active_holds"][0]["suspended_at"] = (NOW - timedelta(seconds=5)).isoformat()
    assert (
        evaluate_call_authority(
            row, purpose="existing_call", existing_call_started_at=NOW - timedelta(seconds=6)
        ).allowed
        is False
    )


@pytest.mark.parametrize(
    "started", [None, NOW, NOW + timedelta(seconds=1), NOW.replace(tzinfo=None), "not a date"]
)
def test_missing_ambiguous_or_post_hold_start_does_not_qualify(started):
    assert (
        evaluate_call_authority(
            deferred_row(), purpose="existing_call", existing_call_started_at=started
        ).allowed
        is False
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("audited", False),
        ("audited", None),
        ("policy_version", "1"),
        ("policy_version", True),
        ("policy_version", 2),
        ("immediate", True),
        ("immediate", "false"),
        ("immediate", None),
        ("affected", False),
        ("target_type", "partner"),
        ("target_id", PARENT),
        ("suspended_at", None),
    ],
)
def test_legacy_untrusted_or_nonmatching_policy_cannot_exempt(field, value):
    row = deferred_row()
    row["active_holds"][0][field] = value
    assert (
        evaluate_call_authority(
            row, purpose="existing_call", existing_call_started_at=NOW - timedelta(seconds=1)
        ).allowed
        is False
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("status", "active"),
        ("suspended_at", NOW + timedelta(seconds=1)),
        ("suspended_by", PARENT),
        ("suspension_reason", "independent restriction"),
    ],
)
def test_non_immediate_policy_does_not_own_changed_account_markers(field, value):
    row = deferred_row()
    row[field] = value
    assert (
        evaluate_call_authority(
            row, purpose="existing_call", existing_call_started_at=NOW - timedelta(seconds=1)
        ).allowed
        is False
    )


@pytest.mark.parametrize(
    "case",
    [
        "prior_inactive",
        "overlapping_hold",
        "parent_suspended",
    ],
)
def test_non_immediate_exception_cannot_override_independent_restriction(case):
    row = deferred_row()
    if case == "prior_inactive":
        row["active_holds"][0]["tenant_state"]["prior"]["status"] = "suspended"
    elif case == "overlapping_hold":
        row["active_holds"].append(hold())
    elif case == "parent_suspended":
        row.update(
            white_label_partner_id=PARENT,
            formal_partner_id=PARENT,
            formal_partner_status="suspended",
        )
    assert (
        evaluate_call_authority(
            row, purpose="existing_call", existing_call_started_at=NOW - timedelta(seconds=1)
        ).allowed
        is False
    )


def test_original_audited_partner_snapshot_hold_blocks_reparented_child():
    row = authority_row(active_holds=[hold(target_type="partner", target_id=PARENT)])
    assert evaluate_call_authority(row).allowed is False
    assert (
        evaluate_call_authority(
            row, purpose="existing_call", existing_call_started_at=NOW - timedelta(seconds=1)
        ).allowed
        is False
    )


async def test_batch_read_is_bounded_deduplicated_explicit_and_preserves_missing_rows():
    class BatchConnection:
        async def fetch(self, sql, tenants):
            assert "WHERE t.id = ANY($1::uuid[])" in sql
            assert tuple(str(t) for t in tenants) == (TENANT, PARENT)
            return [authority_row()]

    rows = await read_call_authority_rows(BatchConnection(), [TENANT, TENANT, PARENT])
    assert set(rows) == {TENANT}
    assert evaluate_call_authority(rows.get(PARENT)).allowed is False
    with pytest.raises(ValueError):
        await read_call_authority_rows(BatchConnection(), [str(uuid4()) for _ in range(201)])
    assert await read_call_authority_rows(BatchConnection(), []) == {}


@pytest.mark.parametrize("status", ["suspended", "cancelled", "past_due", "unpaid"])
@pytest.mark.parametrize("deferred_hold", [False, True])
def test_existing_formal_enforcement_does_not_add_ordinary_billing_hangup_semantics(
    status, deferred_hold
):
    row = deferred_row() if deferred_hold else authority_row()
    row["subscription_status"] = status
    assert (
        evaluate_call_authority(
            row, purpose="existing_call", existing_call_started_at=NOW - timedelta(seconds=1)
        ).allowed
        is True
    )


def test_existing_mode_is_explicit_and_missing_time_does_not_become_fresh_admission():
    row = authority_row(status="suspended")
    assert evaluate_call_authority(row).allowed is False
    assert evaluate_call_authority(row, purpose="principal").allowed is False
    assert evaluate_call_authority(row, purpose="existing_call").allowed is True
    held = deferred_row()
    assert (
        evaluate_call_authority(held, existing_call_started_at=NOW - timedelta(seconds=1)).allowed
        is False
    )
    assert evaluate_call_authority(held, purpose="existing_call").allowed is False


@pytest.mark.parametrize("purpose", ["admission", "principal", "existing_call"])
def test_formal_parent_restriction_remains_in_each_explicit_mode(purpose):
    row = authority_row(
        white_label_partner_id=PARENT, formal_partner_id=PARENT, formal_partner_status="suspended"
    )
    assert evaluate_call_authority(row, purpose=purpose).reason == "formal_partner_inactive"


async def test_principal_wrapper_excludes_billing_but_not_active_holds():
    row = authority_row(subscription_status="cancelled")
    conn = Connection(row)
    assert (await read_formal_account_authority(conn, TENANT)).allowed is True
    row["active_holds"] = [hold()]
    assert (await read_formal_account_authority(conn, TENANT)).allowed is False


async def test_unexpected_authority_check_failure_cannot_escape_outer_guard(monkeypatch):
    from unittest.mock import AsyncMock
    from app.domain.services.call_guard import (
        CheckResult,
        GuardCheck,
        GuardDecision,
        TenantCallLimits,
    )

    guard = CallGuard(Pool(Connection(authority_row())), fail_open=True)
    monkeypatch.setattr(guard, "_get_tenant_limits", AsyncMock(return_value=TenantCallLimits()))
    monkeypatch.setattr(guard, "_get_partner_limits", AsyncMock(return_value=None))
    monkeypatch.setattr(guard, "_get_partner_id", AsyncMock(return_value=None))
    monkeypatch.setattr(
        guard,
        "_check_platform_calls_enabled",
        AsyncMock(return_value=CheckResult(GuardCheck.PLATFORM_CALLS_ENABLED, True)),
    )
    monkeypatch.setattr(
        guard,
        "_check_tenant_active",
        AsyncMock(side_effect=ConnectionError("authority unavailable")),
    )
    monkeypatch.setattr(guard, "_log_decision", AsyncMock())
    result = await guard.evaluate(TENANT, "+15551234567")
    assert result.decision == GuardDecision.BLOCK
    assert result.failed_checks == [GuardCheck.TENANT_ACTIVE]
    assert result.check_results[-1].reason.startswith("check_error_blocked")
