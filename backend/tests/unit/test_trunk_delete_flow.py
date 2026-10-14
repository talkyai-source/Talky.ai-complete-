"""DELETE /telephony/sip/trunks/{id}: a tenant can always remove its own line.

Owner report 2026-10-14 ("I am not able to delete any PBX"). Production 2026-10-13:
the UI deactivates a line (200) and the DELETE then answered 409 "Trunk In Use" for
every line that had ever carried an inbound number, a campaign pin or a route.

The contract tested here:
  * pins / pool allotment / route policies of THIS tenant are cleared, inbound
    numbers on the line are archived, and the row is removed outright unless
    inbound history still references it (then it is retired in place);
  * still refused: a missing or already-deleted line (404), the platform line,
    a line another tenant's pool allotment or campaign still uses, and an
    unfinished call of this tenant created in the last 4 hours;
  * every statement carries an explicit tenant predicate (RLS is not trusted);
  * the PJSIP file is removed before anything is changed, and a failed removal
    changes nothing (503).

The SQL itself is exercised against real PostgreSQL in
tests/integration/test_op04_trunk_lifecycle.py; this fake models only the rows
the handler touches so the control flow, ordering and response shape are fast to
check in the standard suite.
"""
from __future__ import annotations

import copy
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.api.v1.endpoints.telephony_sip import trunks

TENANT = str(uuid4())
OTHER = str(uuid4())
NOW = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)


class FakeDB:
    """In-memory rows for the tables the delete handler reads and writes."""

    def __init__(self):
        self.trunks, self.campaigns, self.policies = {}, [], []
        self.tenants = {TENANT: {}, OTHER: {}}
        self.assignments, self.calls = [], []
        self.configs, self.audit = {}, []
        self.log = []          # (sql, args) of every statement, in order
        self.bypass = "unset"  # current app.bypass_rls

    def add_trunk(self, *, tenant=TENANT, name="my-line", active=False, deleted=False):
        trunk_id = uuid4()
        self.trunks[trunk_id] = {
            "id": trunk_id, "tenant_id": tenant, "trunk_name": name, "is_active": active,
            "deleted_at": NOW if deleted else None, "auth_username": None,
            "auth_password_encrypted": None, "metadata": {}, "sip_domain": "sip.example.com",
            "port": 5060, "transport": "udp", "direction": "both",
        }
        return trunk_id

    def add_assignment(self, trunk_id, *, status="active", tenant=TENANT, config_id=None):
        row = {"id": uuid4(), "tenant_id": tenant, "sip_trunk_id": trunk_id, "status": status,
               "version": 1, "valid_from": NOW - timedelta(days=2), "valid_to": None,
               "config_id": config_id}
        self.assignments.append(row)
        return row

    def add_inbound_campaign(self, *, status="active", campaign_status="running", tenant=TENANT):
        """An inbound routing config and its base campaign."""
        campaign = {"id": uuid4(), "tenant_id": tenant, "calling_config": {},
                    "direction": "inbound", "status": campaign_status}
        self.campaigns.append(campaign)
        config = {"id": uuid4(), "tenant_id": tenant, "campaign_id": campaign["id"],
                  "status": status, "version": 3}
        self.configs[config["id"]] = config
        return config, campaign


class Conn:
    def __init__(self, db: FakeDB):
        self.db = db

    @asynccontextmanager
    async def transaction(self):
        yield self

    def _record(self, sql, args):
        sql = " ".join(sql.split())
        self.db.log.append((sql, args))
        return sql

    async def execute(self, sql, *args):
        sql = self._record(sql, args)
        db = self.db
        if sql.startswith("SET LOCAL app.bypass_rls"):
            db.bypass = sql.rsplit("=", 1)[1].strip(" ';")
            return "SET"
        if sql.startswith("UPDATE tenants"):
            tenant, trunk_text = args[0], args[1]
            rules = db.tenants[tenant]
            if (rules.get("pool_trunk") or {}).get("id") == trunk_text:
                rules.pop("pool_trunk")
            return "UPDATE 1"
        if sql.startswith("UPDATE campaigns"):  # pause the inbound campaigns of paused configs
            tenant, ids = args[0], args[1]
            for campaign in db.campaigns:
                if (campaign["tenant_id"] == str(tenant) and campaign["id"] in ids
                        and campaign.get("direction") == "inbound"
                        and campaign.get("status") in ("running", "active")):
                    campaign["status"] = "paused"
            return "UPDATE"
        if sql.startswith("INSERT INTO inbound_audit_events"):
            tenant, actor, role, trunk_text, config_ids = args
            db.audit += [{"tenant_id": tenant, "config_id": c, "trunk": trunk_text} for c in config_ids]
            return "INSERT"
        if sql.startswith("DELETE FROM tenant_route_policies"):
            tenant, trunk_id = args[0], args[1]
            db.policies[:] = [p for p in db.policies if not (p["tenant_id"] == tenant and p["target_trunk_id"] == trunk_id)]
            return "DELETE"
        if sql.startswith("UPDATE tenant_sip_trunks"):
            tenant, trunk_id = args[0], args[1]
            row = db.trunks[trunk_id]
            assert row["tenant_id"] == str(tenant) and row["deleted_at"] is None
            row.update(deleted_at=NOW, is_active=False, live_registration_status="inactive")
            return "UPDATE 1"
        if sql.startswith("DELETE FROM tenant_sip_trunks"):
            tenant, trunk_id = args[0], args[1]
            assert db.trunks[trunk_id]["tenant_id"] == str(tenant)
            del db.trunks[trunk_id]
            return "DELETE 1"
        raise AssertionError(f"Unexpected execute: {sql}")

    async def fetchrow(self, sql, *args):
        sql = self._record(sql, args)
        if "FROM tenant_sip_trunks" in sql and "FOR UPDATE" in sql:
            tenant, trunk_id = args[0], args[1]
            row = self.db.trunks.get(trunk_id)
            if row is None or row["tenant_id"] != str(tenant) or row["deleted_at"] is not None:
                return None
            return row
        raise AssertionError(f"Unexpected fetchrow: {sql}")

    async def fetch(self, sql, *args):
        sql = self._record(sql, args)
        db = self.db
        if sql.startswith("UPDATE campaigns"):
            tenant, trunk_text = args[0], args[1]
            hit = [c for c in db.campaigns
                   if c["tenant_id"] == str(tenant) and (c["calling_config"].get("trunk") or {}).get("id") == trunk_text]
            for campaign in hit:
                campaign["calling_config"].pop("trunk")
            return [{"id": c["id"]} for c in hit]
        if sql.startswith("UPDATE inbound_campaign_configs"):
            tenant, trunk_id = args[0], args[1]
            live = {a["config_id"] for a in db.assignments
                    if a["tenant_id"] == str(tenant) and a["sip_trunk_id"] == trunk_id and a["status"] != "archived"}
            hit = [c for c in db.configs.values()
                   if c["tenant_id"] == str(tenant) and c["status"] == "active" and c["id"] in live]
            for config in hit:
                config.update(status="paused", version=config["version"] + 1)
            return [{"id": c["id"], "campaign_id": c["campaign_id"]} for c in hit]
        if sql.startswith("UPDATE inbound_did_assignments"):
            tenant, trunk_id = args[0], args[1]
            hit = [a for a in db.assignments
                   if a["tenant_id"] == str(tenant) and a["sip_trunk_id"] == trunk_id and a["status"] != "archived"]
            for assignment in hit:
                assignment.update(status="archived", version=assignment["version"] + 1, valid_to=NOW)
            return [{"id": a["id"]} for a in hit]
        raise AssertionError(f"Unexpected fetch: {sql}")

    async def fetchval(self, sql, *args):
        sql = self._record(sql, args)
        db = self.db
        if "FROM inbound_did_assignments" in sql:  # is anything still referencing the line?
            tenant, trunk_id = args[0], args[1]
            return any(a["tenant_id"] == str(tenant) and a["sip_trunk_id"] == trunk_id for a in db.assignments)
        if "FROM calls" in sql:
            tenant, terminal = args[0], args[1]
            return any(
                c["tenant_id"] == str(tenant) and c["status"] not in terminal
                and c["created_at"] >= NOW - timedelta(hours=4)
                for c in db.calls
            )
        if "pool_trunk" in sql:  # another tenant's allotment or pin (needs the RLS bypass)
            assert db.bypass == "on", "the cross-tenant read must run with the bypass on"
            trunk_text, tenant = args[0], args[1]
            return any(
                (rules.get("pool_trunk") or {}).get("id") == trunk_text
                for owner, rules in db.tenants.items() if owner != str(tenant)
            ) or any(
                (c["calling_config"].get("trunk") or {}).get("id") == trunk_text
                for c in db.campaigns if c["tenant_id"] != str(tenant)
            )
        raise AssertionError(f"Unexpected fetchval: {sql}")


@pytest.fixture
def env(monkeypatch):
    from app.infrastructure.telephony import pjsip_config_generator

    db = FakeDB()
    snapshot = {}

    @asynccontextmanager
    async def acquire(_pool, tenant_id, **_kwargs):
        assert str(tenant_id) == TENANT
        snapshot["state"] = copy.deepcopy(
            (db.trunks, db.campaigns, db.policies, db.tenants, db.assignments, db.configs, db.audit))
        try:
            yield Conn(db)
        except BaseException:
            # The real connection rolls the whole transaction back on an exception.
            (db.trunks, db.campaigns, db.policies, db.tenants, db.assignments,
             db.configs, db.audit) = snapshot["state"]
            raise

    remove = AsyncMock()
    stored = AsyncMock()
    monkeypatch.setattr(trunks, "acquire_with_tenant", acquire)
    monkeypatch.setattr(trunks, "_claim_idempotency", AsyncMock(return_value=("new", None, None)))
    monkeypatch.setattr(trunks, "_store_idempotency_result", stored)
    monkeypatch.setattr(trunks, "_store_error_idempotency_result", AsyncMock())
    monkeypatch.setattr(trunks, "_enforce_ws_i_quota", AsyncMock(return_value=None))
    monkeypatch.setattr(pjsip_config_generator, "remove_trunk_config", remove)
    monkeypatch.delenv("PLATFORM_SIP_TRUNK_NAME", raising=False)
    return SimpleNamespace(db=db, remove=remove, stored=stored)


async def delete(trunk_id, key="delete-key"):
    request = Request({"type": "http", "method": "DELETE", "path": f"/trunks/{trunk_id}", "headers": []})
    user = SimpleNamespace(tenant_id=TENANT, id=str(uuid4()))
    try:
        return await trunks.delete_sip_trunk(trunk_id, request, key, user, object())
    except HTTPException as exc:
        return exc


def status_of(result):
    return getattr(result, "status_code", 200)


@pytest.mark.asyncio
async def test_unused_inactive_line_is_removed_outright(env):
    trunk = env.db.add_trunk()
    result = await delete(trunk)
    assert result == {
        "id": str(trunk), "deleted": True, "mode": "removed",
        "archived_inbound_numbers": 0, "paused_inbound_campaigns": 0, "unpinned_campaigns": 0,
    }
    assert trunk not in env.db.trunks
    env.remove.assert_awaited_once()
    # The stored receipt is the response the caller got, so a replay says the same.
    assert env.stored.await_args.kwargs["response_body"] == result


@pytest.mark.asyncio
async def test_line_with_active_and_archived_inbound_numbers_is_retired_not_blocked(env):
    trunk = env.db.add_trunk()
    live = env.db.add_assignment(trunk, status="active")
    history = env.db.add_assignment(trunk, status="archived")
    result = await delete(trunk)
    assert status_of(result) == 200
    assert result == {
        "id": str(trunk), "deleted": True, "mode": "archived",
        "archived_inbound_numbers": 1, "paused_inbound_campaigns": 0, "unpinned_campaigns": 0,
    }
    assert (live["status"], live["version"]) == ("archived", 2)
    assert (history["status"], history["version"]) == ("archived", 1), "history is left as it was"
    row = env.db.trunks[trunk]
    assert row["deleted_at"] is not None and row["is_active"] is False
    # The history rows are never deleted.
    assert len(env.db.assignments) == 2
    assert not any(sql.startswith("DELETE FROM tenant_sip_trunks") for sql, _ in env.db.log)


@pytest.mark.asyncio
async def test_an_inbound_campaign_on_the_line_is_paused_not_left_active(env):
    """Its number is archived, so it can no longer answer calls, and the inbound
    lifecycle refuses an archived assignment: left "active" it could be neither
    paused nor archived again. The delete pauses it, as a reassignment does."""
    db = env.db
    trunk = db.add_trunk(active=True)
    config, campaign = db.add_inbound_campaign()
    already_paused, untouched_campaign = db.add_inbound_campaign(status="paused", campaign_status="paused")
    elsewhere, elsewhere_campaign = db.add_inbound_campaign()
    db.add_assignment(trunk, config_id=config["id"])
    db.add_assignment(trunk, status="paused", config_id=already_paused["id"])
    db.add_assignment(db.add_trunk(name="other-line"), config_id=elsewhere["id"])
    result = await delete(trunk)
    assert result["paused_inbound_campaigns"] == 1 and result["archived_inbound_numbers"] == 2
    assert (config["status"], config["version"]) == ("paused", 4)
    assert campaign["status"] == "paused"
    assert (already_paused["status"], already_paused["version"]) == ("paused", 3)
    assert untouched_campaign["status"] == "paused"
    assert (elsewhere["status"], elsewhere_campaign["status"]) == ("active", "running"), "another line's campaign is untouched"
    assert [(a["config_id"], a["trunk"]) for a in db.audit] == [(config["id"], str(trunk))]
    # Configs are locked before their assignments, the order every inbound path uses.
    order = [sql.split(" SET ")[0] for sql, _ in db.log if sql.startswith("UPDATE inbound_")]
    assert order == ["UPDATE inbound_campaign_configs", "UPDATE inbound_did_assignments"]


@pytest.mark.asyncio
async def test_campaign_pin_pool_allotment_and_route_policy_are_cleared(env):
    db = env.db
    trunk = db.add_trunk()
    db.campaigns += [
        {"id": uuid4(), "tenant_id": TENANT, "calling_config": {"trunk": {"id": str(trunk)}, "keep": 1}},
        {"id": uuid4(), "tenant_id": TENANT, "calling_config": {"trunk": {"id": str(uuid4())}}},
    ]
    db.tenants[TENANT]["pool_trunk"] = {"id": str(trunk)}
    db.policies.append({"tenant_id": TENANT, "target_trunk_id": trunk})
    result = await delete(trunk)
    assert result["deleted"] is True and result["mode"] == "removed"
    assert result["unpinned_campaigns"] == 1
    assert db.campaigns[0]["calling_config"] == {"keep": 1}
    assert "trunk" in db.campaigns[1]["calling_config"], "a pin to a different line is untouched"
    assert "pool_trunk" not in db.tenants[TENANT]
    assert db.policies == []
    assert trunk not in db.trunks


@pytest.mark.asyncio
@pytest.mark.parametrize("dependency", ["pool", "campaign"])
async def test_a_line_another_tenant_still_uses_is_refused_and_nothing_changes(env, dependency):
    db = env.db
    trunk = db.add_trunk()
    live = db.add_assignment(trunk)
    mine = {"id": uuid4(), "tenant_id": TENANT, "calling_config": {"trunk": {"id": str(trunk)}}}
    db.campaigns.append(mine)
    if dependency == "pool":
        db.tenants[OTHER]["pool_trunk"] = {"id": str(trunk)}
    else:
        db.campaigns.append({"id": uuid4(), "tenant_id": OTHER, "calling_config": {"trunk": {"id": str(trunk)}}})
    result = await delete(trunk)
    assert status_of(result) == 409
    env.remove.assert_not_awaited()
    assert trunk in db.trunks and db.trunks[trunk]["deleted_at"] is None
    assert live["status"] == "active" and "trunk" in mine["calling_config"], "no partial cleanup"
    # The answer must not reveal who uses it.
    assert str(OTHER) not in result.body.decode()


@pytest.mark.asyncio
@pytest.mark.parametrize("age,status,expected", [
    (timedelta(hours=5), "in_progress", 200),
    (timedelta(hours=3, minutes=59), "in_progress", 409),
    (timedelta(minutes=1), "ringing", 409),
    (timedelta(minutes=1), "completed", 200),
    (timedelta(minutes=1), "no_answer", 200),
])
async def test_only_a_recent_unfinished_call_of_this_tenant_blocks(env, age, status, expected):
    trunk = env.db.add_trunk()
    env.db.calls.append({"tenant_id": TENANT, "status": status, "created_at": NOW - age})
    assert status_of(await delete(trunk)) == expected


@pytest.mark.asyncio
async def test_another_tenants_open_call_does_not_block(env):
    trunk = env.db.add_trunk()
    env.db.calls.append({"tenant_id": OTHER, "status": "in_progress", "created_at": NOW})
    assert status_of(await delete(trunk)) == 200


@pytest.mark.asyncio
async def test_active_line_is_deactivated_and_removed_in_one_call(env):
    trunk = env.db.add_trunk(active=True)
    result = await delete(trunk)
    assert result["deleted"] is True and trunk not in env.db.trunks
    env.remove.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_foreign_and_already_deleted_lines_are_404(env):
    mine_deleted = env.db.add_trunk(deleted=True)
    foreign = env.db.add_trunk(tenant=OTHER)
    for trunk_id in (uuid4(), mine_deleted, foreign):
        assert status_of(await delete(trunk_id, key=f"k-{trunk_id}")) == 404
    env.remove.assert_not_awaited()
    assert foreign in env.db.trunks


@pytest.mark.asyncio
async def test_the_platform_line_cannot_be_deleted_by_a_tenant(env):
    trunk = env.db.add_trunk(name=" Platform-Default ")
    assert status_of(await delete(trunk)) == 409
    env.remove.assert_not_awaited()
    assert trunk in env.db.trunks


@pytest.mark.asyncio
async def test_cleanup_that_asterisk_does_not_confirm_changes_nothing(env):
    db = env.db
    trunk = db.add_trunk()
    live = db.add_assignment(trunk)
    pin = {"id": uuid4(), "tenant_id": TENANT, "calling_config": {"trunk": {"id": str(trunk)}}}
    db.campaigns.append(pin)
    env.remove.side_effect = RuntimeError("reload unavailable")
    result = await delete(trunk)
    assert isinstance(result, HTTPException) and result.status_code == 503
    assert trunk in db.trunks and db.trunks[trunk]["deleted_at"] is None
    assert live["status"] == "active" and live["version"] == 1
    assert "trunk" in pin["calling_config"]


@pytest.mark.asyncio
async def test_failed_cleanup_of_an_active_line_puts_its_config_back(env, monkeypatch):
    from app.infrastructure.telephony import pjsip_config_generator

    trunk = env.db.add_trunk(active=True)
    env.remove.side_effect = RuntimeError("reload unavailable")
    restored = AsyncMock()
    reload_ = AsyncMock()
    monkeypatch.setattr(pjsip_config_generator, "apply_trunk_config", restored)
    monkeypatch.setattr(pjsip_config_generator, "request_pjsip_reload", reload_)
    result = await delete(trunk)
    assert isinstance(result, HTTPException) and result.status_code == 503
    # The file was already unlinked; the rolled-back row is still active, so the
    # file is rendered again (without its own reload) and one reload is asked for.
    restored.assert_awaited_once()
    assert restored.await_args.args[0]["id"] == trunk and restored.await_args.kwargs["reload"] is False
    reload_.assert_awaited_once()


@pytest.mark.asyncio
async def test_failed_cleanup_of_an_inactive_line_has_nothing_to_restore(env, monkeypatch):
    from app.infrastructure.telephony import pjsip_config_generator

    trunk = env.db.add_trunk(active=False)
    env.remove.side_effect = RuntimeError("reload unavailable")
    restored = AsyncMock()
    monkeypatch.setattr(pjsip_config_generator, "apply_trunk_config", restored)
    assert (await delete(trunk)).status_code == 503
    restored.assert_not_awaited()


@pytest.mark.asyncio
async def test_every_statement_names_this_tenant_and_the_bypass_is_closed_again(env):
    db = env.db
    trunk = db.add_trunk()
    db.add_assignment(trunk)
    db.campaigns.append({"id": uuid4(), "tenant_id": TENANT, "calling_config": {"trunk": {"id": str(trunk)}}})
    db.policies.append({"tenant_id": TENANT, "target_trunk_id": trunk})
    assert (await delete(trunk))["deleted"] is True
    statements = [(sql, args) for sql, args in db.log if not sql.startswith("SET LOCAL")]
    assert statements, "the handler issued no SQL"
    for sql, args in statements:
        assert "tenant_id" in sql or sql.startswith("UPDATE tenants"), f"no tenant predicate: {sql}"
        assert any(str(arg) == TENANT for arg in args), f"tenant id is not a parameter of: {sql}"
    # Other tenants' rows are read once with the bypass on; everything after it,
    # including every write, runs under this tenant's own policies again.
    switches = [sql for sql, _ in db.log if sql.startswith("SET LOCAL app.bypass_rls")]
    assert switches == ["SET LOCAL app.bypass_rls = 'on'", "SET LOCAL app.bypass_rls = 'off'"]
    first_write = next(i for i, (sql, _) in enumerate(db.log) if sql.startswith(("UPDATE", "DELETE")))
    assert db.log.index((switches[1], ())) < first_write
    assert db.bypass == "off"
