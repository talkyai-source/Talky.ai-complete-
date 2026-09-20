"""Guards for the extension-binding entry point.

The script writes the two facts that let a call to an internal extension be
answered. Both are tenant-scoped, so what matters is that it refuses rather
than repairs: a trunk on another tenant, an outbound campaign, or a missing
carrier account must stop the write, not be worked around.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from scripts import bind_inbound_extension as binder

BACKEND = Path(__file__).resolve().parents[2]
TENANT = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
OTHER_TENANT = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
ACTOR = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
CAMPAIGN = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
CONFIG = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
TRUNK = "ffffffff-ffff-4fff-8fff-ffffffffffff"
BINDING = "99999999-9999-4999-8999-999999999999"


def test_every_statement_carries_an_explicit_tenant_predicate():
    """RLS alone has been decorative on this database before."""
    for name in ("TRUNK_SQL", "CAMPAIGN_SQL", "MARK_TRUNK_SQL", "ARCHIVE_PRIOR_SQL"):
        sql = getattr(binder, name)
        assert re.search(r"tenant_id\s*=\s*\$\d", sql), name
    assert "tenant_id" in binder.UPSERT_BINDING_SQL


def test_the_trunk_is_marked_with_the_same_flag_outbound_selection_reads():
    """metadata.role='extension' is what keeps it out of choose_outbound_route."""
    from app.domain.services.telephony.trunk_resolver import _is_internal_extension

    assert _is_internal_extension({"role": "extension"}) is True
    assert _is_internal_extension({"role": "extension"}) is True
    assert _is_internal_extension({}) is False
    assert _is_internal_extension(json.dumps({"role": "extension"})) is True


class _Txn:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False


class _Conn:
    def __init__(self, trunk=None, campaign=None, foreign=None):
        self._trunk = trunk
        self._campaign = campaign
        self._foreign = foreign
        self.calls: list[tuple[str, tuple]] = []

    def transaction(self):
        return _Txn()

    async def execute(self, *args):
        self.calls.append(("EXEC", args))
        return "SET"

    async def fetchrow(self, sql, *args):
        self.calls.append((sql, args))
        if sql is binder.TRUNK_SQL:
            return self._trunk
        if sql is binder.CAMPAIGN_SQL:
            return self._campaign
        if sql is binder.FOREIGN_TRUNK_SQL:
            return self._foreign
        if sql is binder.MARK_TRUNK_SQL:
            return {"id": TRUNK, "is_active": True, "metadata": {"role": "extension"}}
        if sql is binder.UPSERT_BINDING_SQL:
            return {"id": BINDING, "status": args[5], "version": 1}
        raise AssertionError(f"unexpected sql: {sql[:40]}")

    async def fetch(self, sql, *args):
        self.calls.append((sql, args))
        return []


def _patch_acquire(monkeypatch, conn):
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def fake_acquire(pool, tenant_id, **_kw):
        fake_acquire.tenants.append(tenant_id)
        fake_acquire.tenant_id = tenant_id
        yield conn

    fake_acquire.tenants = []
    monkeypatch.setattr("app.core.db_utils.acquire_with_tenant", fake_acquire)
    return fake_acquire


def _trunk_row(direction="both", register=True):
    return {
        "id": TRUNK,
        "trunk_name": "blaze-pbx-940003",
        "auth_username": "940003",
        "direction": direction,
        "is_active": False,
        "metadata": {"register": register},
        "live_registration_status": "inactive",
    }


def _campaign_row(direction="inbound", status="running"):
    return {
        "campaign_id": CAMPAIGN,
        "campaign_name": "Reception",
        "campaign_status": status,
        "direction": direction,
        "config_id": CONFIG,
        "config_status": "active",
        "greeting": "Hello",
    }


@pytest.mark.asyncio
async def test_a_happy_binding_writes_both_facts_in_the_tenant_context(monkeypatch):
    conn = _Conn(_trunk_row(), _campaign_row())
    acquire = _patch_acquire(monkeypatch, conn)

    result = await binder.bind_extension(
        object(),
        tenant_id=TENANT,
        actor_user_id=ACTOR,
        extension="940003",
        campaign_id=CAMPAIGN,
        activate=True,
    )

    assert acquire.tenant_id == TENANT, "must run under the tenant's own RLS context"
    assert result["extension"] == "940003"
    assert result["binding_status"] == "active"
    assert result["trunk_active"] is True

    marked = next(c for c in conn.calls if c[0] is binder.MARK_TRUNK_SQL)
    assert json.loads(marked[1][2]) == {"role": "extension"}


@pytest.mark.asyncio
async def test_a_paused_binding_is_the_default(monkeypatch):
    conn = _Conn(_trunk_row(), _campaign_row())
    _patch_acquire(monkeypatch, conn)
    result = await binder.bind_extension(
        object(),
        tenant_id=TENANT,
        actor_user_id=ACTOR,
        extension="940003",
        campaign_id=CAMPAIGN,
        activate=False,
    )
    assert result["binding_status"] == "paused"


@pytest.mark.asyncio
async def test_a_missing_carrier_account_on_this_tenant_is_refused(monkeypatch):
    conn = _Conn(None, _campaign_row())
    _patch_acquire(monkeypatch, conn)
    with pytest.raises(SystemExit, match="no SIP trunk"):
        await binder.bind_extension(
            object(),
            tenant_id=OTHER_TENANT,
            actor_user_id=ACTOR,
            extension="940003",
            campaign_id=CAMPAIGN,
            activate=True,
        )


@pytest.mark.asyncio
async def test_an_outbound_only_trunk_is_refused(monkeypatch):
    conn = _Conn(_trunk_row(direction="outbound"), _campaign_row())
    _patch_acquire(monkeypatch, conn)
    with pytest.raises(SystemExit, match="inbound or both"):
        await binder.bind_extension(
            object(),
            tenant_id=TENANT,
            actor_user_id=ACTOR,
            extension="940003",
            campaign_id=CAMPAIGN,
            activate=True,
        )


@pytest.mark.asyncio
async def test_a_campaign_that_is_not_this_tenants_inbound_one_is_refused(monkeypatch):
    conn = _Conn(_trunk_row(), None)
    _patch_acquire(monkeypatch, conn)
    with pytest.raises(SystemExit, match="no inbound campaign"):
        await binder.bind_extension(
            object(),
            tenant_id=TENANT,
            actor_user_id=ACTOR,
            extension="940003",
            campaign_id=CAMPAIGN,
            activate=True,
        )


@pytest.mark.asyncio
async def test_an_outbound_campaign_cannot_answer_an_extension(monkeypatch):
    conn = _Conn(_trunk_row(), _campaign_row(direction="outbound"))
    _patch_acquire(monkeypatch, conn)
    with pytest.raises(SystemExit, match="only an inbound campaign"):
        await binder.bind_extension(
            object(),
            tenant_id=TENANT,
            actor_user_id=ACTOR,
            extension="940003",
            campaign_id=CAMPAIGN,
            activate=True,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["94", "9400031234", "94000a", ""])
async def test_a_malformed_extension_is_refused_before_any_write(monkeypatch, bad):
    conn = _Conn(_trunk_row(), _campaign_row())
    _patch_acquire(monkeypatch, conn)
    with pytest.raises(SystemExit, match="not a valid internal extension"):
        await binder.bind_extension(
            object(),
            tenant_id=TENANT,
            actor_user_id=ACTOR,
            extension=bad,
            campaign_id=CAMPAIGN,
            activate=True,
        )
    assert conn.calls == [], "nothing may be written for an unaddressable extension"


def test_script_is_executable_by_path_from_backend_directory():
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, str(BACKEND / "scripts" / "bind_inbound_extension.py"), "--help"],
        capture_output=True,
        text=True,
        cwd=str(BACKEND),
    )
    assert result.returncode == 0, result.stderr
    assert "--extension" in result.stdout


@pytest.mark.asyncio
async def test_mark_only_registers_the_account_without_inventing_a_binding(monkeypatch):
    """The step before a campaign exists.

    Marking is what makes activation safe: an extension trunk is excluded from
    outbound selection, so this cannot move a running outbound campaign. With
    no binding the reconciler renders no route and the call is refused on the
    catch-all — which still proves the carrier delivered an INVITE.
    """
    conn = _Conn(_trunk_row(), None)
    _patch_acquire(monkeypatch, conn)

    result = await binder.bind_extension(
        object(),
        tenant_id=TENANT,
        actor_user_id=ACTOR,
        extension="940003",
        campaign_id=None,
        activate=True,
        mark_only=True,
    )

    assert result["trunk_active"] is True
    assert result["binding_status"] == "none"
    assert result["binding_id"] is None
    marked = next(c for c in conn.calls if c[0] is binder.MARK_TRUNK_SQL)
    assert json.loads(marked[1][2]) == {"role": "extension"}
    assert not any(c[0] is binder.UPSERT_BINDING_SQL for c in conn.calls)
    assert not any(c[0] is binder.CAMPAIGN_SQL for c in conn.calls)


@pytest.mark.asyncio
async def test_a_binding_without_a_campaign_is_refused(monkeypatch):
    conn = _Conn(_trunk_row(), _campaign_row())
    _patch_acquire(monkeypatch, conn)
    with pytest.raises(SystemExit, match="campaign-id is required"):
        await binder.bind_extension(
            object(),
            tenant_id=TENANT,
            actor_user_id=ACTOR,
            extension="940003",
            campaign_id=None,
            activate=True,
        )


def _no_public_accounts(monkeypatch):
    monkeypatch.setattr(binder, "_reviewed_public_accounts", lambda: set())


@pytest.mark.asyncio
async def test_a_trunk_with_registration_disabled_is_refused(monkeypatch):
    """Without metadata.register the account can never receive a call, but every
    readiness signal would still report the trunk healthy."""
    conn = _Conn(_trunk_row(register=False), _campaign_row())
    _patch_acquire(monkeypatch, conn)
    _no_public_accounts(monkeypatch)
    with pytest.raises(SystemExit, match="registration disabled"):
        await binder.bind_extension(
            object(), tenant_id=TENANT, actor_user_id=ACTOR,
            extension="940003", campaign_id=CAMPAIGN, activate=True,
        )


@pytest.mark.asyncio
async def test_a_reviewed_public_carrier_account_cannot_be_bound_as_an_extension(monkeypatch):
    conn = _Conn(_trunk_row(), _campaign_row())
    _patch_acquire(monkeypatch, conn)
    monkeypatch.setattr(binder, "_reviewed_public_accounts", lambda: {"940003"})
    with pytest.raises(SystemExit, match="reviewed PUBLIC carrier account"):
        await binder.bind_extension(
            object(), tenant_id=TENANT, actor_user_id=ACTOR,
            extension="940003", campaign_id=CAMPAIGN, activate=True,
        )


@pytest.mark.asyncio
async def test_digits_already_held_by_another_tenant_are_refused(monkeypatch):
    """tenant_sip_trunks has no unique index on auth_username, so this is the
    only thing standing between two tenants and the same carrier account."""
    conn = _Conn(_trunk_row(), _campaign_row(), foreign={"tenant_id": OTHER_TENANT, "trunk_name": "theirs"})
    _patch_acquire(monkeypatch, conn)
    _no_public_accounts(monkeypatch)
    with pytest.raises(SystemExit, match="also held by an active trunk"):
        await binder.bind_extension(
            object(), tenant_id=TENANT, actor_user_id=ACTOR,
            extension="940003", campaign_id=CAMPAIGN, activate=True,
        )


def test_an_unreadable_carrier_inventory_refuses_rather_than_permits(tmp_path, monkeypatch):
    monkeypatch.setattr(binder, "_BACKEND_ROOT", tmp_path / "nope" / "backend")
    with pytest.raises(SystemExit, match="cannot read the reviewed carrier inventory"):
        binder._reviewed_public_accounts()


@pytest.mark.asyncio
async def test_the_cross_tenant_check_never_leaves_rls_bypassed_for_the_writes(monkeypatch):
    """`SET LOCAL app.bypass_rls` persists for the whole transaction.

    Running the cross-tenant lookup inside the tenant transaction would leave
    the trunk UPDATE and the binding INSERT executing with RLS bypassed -- the
    exact isolation this script advertises. The check must therefore happen on
    its own connection, before the tenant transaction opens.
    """
    conn = _Conn(_trunk_row(), _campaign_row(), foreign=None)
    acquire = _patch_acquire(monkeypatch, conn)
    _no_public_accounts(monkeypatch)

    await binder.bind_extension(
        object(), tenant_id=TENANT, actor_user_id=ACTOR,
        extension="940003", campaign_id=CAMPAIGN, activate=True,
    )

    # First acquire is the bypass read (None), second is the tenant's own.
    assert acquire.tenants == [None, TENANT], acquire.tenants
    # Nothing may have turned the bypass GUC on inside the writing transaction.
    assert not any(
        c[0] == "EXEC" and "bypass_rls" in str(c[1]) for c in conn.calls
    ), conn.calls
