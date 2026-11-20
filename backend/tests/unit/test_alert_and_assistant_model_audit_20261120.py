"""Alert acknowledge/resolve and assistant-model change write a transactional audit record.

Behavioural fake (not PostgreSQL evidence): rows and receipts are staged in a
working copy that is committed only if the handler leaves the transaction
without an exception.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints import alerts as alerts_mod
from app.api.v1.endpoints import assistant_config as assistant_mod

TENANT = "11111111-1111-4111-8111-111111111111"
OTHER_TENANT = "22222222-2222-4222-8222-222222222222"
ACTOR = "33333333-3333-4333-8333-333333333333"
ALERT = "55555555-5555-4555-8555-555555555555"
NOW = datetime(2026, 11, 20, tzinfo=timezone.utc)
MODELS = sorted(assistant_mod.ALLOWED_ASSISTANT_MODEL_IDS)
OLD_MODEL, NEW_MODEL = MODELS[0], MODELS[1]


def _user(tenant=TENANT):
    return SimpleNamespace(id=ACTOR, tenant_id=tenant, role="tenant_admin")


def _event(**kw):
    row = {
        "event_id": ALERT, "tenant_id": TENANT, "created_at": NOW, "severity": "HIGH",
        "status": "open", "title": "t", "description": "d", "evidence": None,
        "assigned_to": None, "resolved_at": None, "resolution_notes": None,
        "alert_type": "Campaign", "detection_source": "manual",
    }
    row.update(kw)
    return row


class AuditUnavailable(RuntimeError):
    pass


class Db:
    def __init__(self, events=None, model=OLD_MODEL, audit_fails=False):
        self.data = {"events": {e["event_id"]: e for e in (events or [])},
                     "tenants": {TENANT: {"assistant_model": model}}, "receipts": []}
        self.audit_fails = audit_fails
        self.order: list[str] = []
        self.work = None
        self.pool = object()

    @asynccontextmanager
    async def tx(self, pool, tenant):
        self.work = deepcopy(self.data)
        self.order = []
        try:
            yield Conn(self)
        except BaseException:
            self.work = None  # rolled back
            raise
        else:
            self.data = self.work
            self.work = None


class Conn:
    def __init__(self, db):
        self.db = db

    def _ev(self, event_id, tenant):
        row = self.db.work["events"].get(event_id)
        return row if row and row["tenant_id"] == tenant else None

    async def fetchrow(self, sql, *a):
        s = " ".join(sql.split())
        if s.startswith("UPDATE security_events SET status = 'investigating'"):
            ev = self._ev(a[0], a[1])
            if ev and ev["status"] == "open" and ev["detection_source"] != "telephony_rate_limiter":
                self.db.order.append("update")
                ev.update(status="investigating", assigned_to=a[2])
                return dict(ev)
            return None
        if s.startswith("UPDATE security_events SET status = 'resolved'"):
            ev = self._ev(a[0], a[1])
            if ev and ev["status"] != "resolved" and ev["detection_source"] != "telephony_rate_limiter":
                self.db.order.append("update")
                ev.update(status="resolved", resolved_at=NOW, resolution_notes=a[2])
                return dict(ev)
            return None
        if s.startswith("SELECT detection_source FROM security_events"):
            ev = self._ev(a[0], a[1])
            return {"detection_source": ev["detection_source"]} if ev else None
        if s.startswith("SELECT event_id"):
            ev = self._ev(a[0], a[1])
            return dict(ev) if ev else None
        if s.startswith("SELECT assistant_model FROM tenants"):
            t = self.db.work["tenants"].get(a[0])
            return dict(t) if t else None
        raise AssertionError(f"unexpected fetchrow: {s}")

    async def execute(self, sql, *a):
        s = " ".join(sql.split())
        if s.startswith("UPDATE tenants SET assistant_model"):
            self.db.order.append("update")
            self.db.work["tenants"][a[0]]["assistant_model"] = a[1]
            return
        if s.startswith("UPDATE security_events SET resolution_notes"):
            ev = self._ev(a[0], a[2])
            ev["resolution_notes"] = a[1]
            return
        raise AssertionError(f"unexpected execute: {s}")


class Audit:
    def __init__(self, db):
        self.db = db

    async def lock_transaction(self, conn):
        self.db.order.append("lock")

    async def log_in_transaction(self, conn, **kw):
        self.db.order.append("audit")
        if self.db.audit_fails:
            raise AuditUnavailable("audit down")
        self.db.work["receipts"].append(kw)


def _wire(monkeypatch, db):
    monkeypatch.setattr(alerts_mod, "acquire_with_tenant", db.tx)
    monkeypatch.setattr(assistant_mod, "acquire_with_tenant", db.tx)
    return SimpleNamespace(pool=db.pool), Audit(db)


async def _ack(client, audit, tenant=TENANT, note=None):
    return await alerts_mod.ack_alert(ALERT, alerts_mod.AckRequest(note=note), _user(tenant), client, audit)


async def _resolve(client, audit, tenant=TENANT):
    return await alerts_mod.resolve_alert(
        ALERT, alerts_mod.ResolveRequest(resolution_notes="done"), _user(tenant), client, audit)


async def _model(client, audit, model=NEW_MODEL):
    return await assistant_mod.set_assistant_model(
        assistant_mod.ModelUpdateRequest(model=model), _user(), client, audit)


def _check_receipt(db, action, resource_type, resource_id):
    assert len(db.data["receipts"]) == 1
    r = db.data["receipts"][0]
    assert (r["action"], r["resource_type"], str(r["resource_id"])) == (action, resource_type, resource_id)
    assert r["actor_id"] == ACTOR and str(r["tenant_id"]) == TENANT
    assert db.order[:1] == ["lock"] and db.order.index("update") < db.order.index("audit")


# ----------------------------------------------------------------- (a) success

async def test_ack_writes_one_receipt_in_same_transaction(monkeypatch):
    db = Db([_event()])
    client, audit = _wire(monkeypatch, db)
    await _ack(client, audit, note="looking")
    assert db.data["events"][ALERT]["status"] == "investigating"
    _check_receipt(db, "alert.acknowledge", "security_alert", ALERT)
    assert db.data["receipts"][0]["after_state"] == {"status": "investigating"}


async def test_resolve_writes_one_receipt_in_same_transaction(monkeypatch):
    db = Db([_event()])
    client, audit = _wire(monkeypatch, db)
    await _resolve(client, audit)
    assert db.data["events"][ALERT]["status"] == "resolved"
    _check_receipt(db, "alert.resolve", "security_alert", ALERT)
    assert db.data["receipts"][0]["after_state"] == {"status": "resolved"}


async def test_model_change_writes_one_receipt_with_old_and_new(monkeypatch):
    db = Db()
    client, audit = _wire(monkeypatch, db)
    await _model(client, audit)
    assert db.data["tenants"][TENANT]["assistant_model"] == NEW_MODEL
    _check_receipt(db, "assistant.model.update", "tenant_assistant_model", TENANT)
    r = db.data["receipts"][0]
    assert r["before_state"] == {"assistant_model": OLD_MODEL}
    assert r["after_state"] == {"assistant_model": NEW_MODEL}


# ------------------------------------------------------- (b) audit failure rolls back

async def test_ack_rolls_back_when_audit_fails(monkeypatch):
    db = Db([_event()], audit_fails=True)
    client, audit = _wire(monkeypatch, db)
    with pytest.raises(AuditUnavailable):
        await _ack(client, audit)
    assert db.data["events"][ALERT]["status"] == "open" and db.data["receipts"] == []


async def test_resolve_rolls_back_when_audit_fails(monkeypatch):
    db = Db([_event()], audit_fails=True)
    client, audit = _wire(monkeypatch, db)
    with pytest.raises(AuditUnavailable):
        await _resolve(client, audit)
    assert db.data["events"][ALERT]["status"] == "open" and db.data["receipts"] == []


async def test_model_change_rolls_back_when_audit_fails(monkeypatch):
    db = Db(audit_fails=True)
    client, audit = _wire(monkeypatch, db)
    with pytest.raises(AuditUnavailable):
        await _model(client, audit)
    assert db.data["tenants"][TENANT]["assistant_model"] == OLD_MODEL and db.data["receipts"] == []


# --------------------------------------------- (c) refused cases write no receipt

@pytest.mark.parametrize("fn", [_ack, _resolve])
async def test_platform_raised_alert_403_no_receipt(monkeypatch, fn):
    db = Db([_event(detection_source="telephony_rate_limiter")])
    client, audit = _wire(monkeypatch, db)
    with pytest.raises(HTTPException) as ei:
        await fn(client, audit)
    assert ei.value.status_code == 403
    assert db.data["receipts"] == [] and db.data["events"][ALERT]["status"] == "open"


@pytest.mark.parametrize("fn", [_ack, _resolve])
async def test_missing_or_other_tenant_alert_404_no_receipt(monkeypatch, fn):
    db = Db([_event(tenant_id=OTHER_TENANT)])
    client, audit = _wire(monkeypatch, db)
    with pytest.raises(HTTPException) as ei:
        await fn(client, audit)
    assert ei.value.status_code == 404
    assert db.data["receipts"] == []


async def test_unknown_model_400_no_receipt(monkeypatch):
    db = Db()
    client, audit = _wire(monkeypatch, db)
    with pytest.raises(HTTPException) as ei:
        await _model(client, audit, model="not-a-model")
    assert ei.value.status_code == 400
    assert db.data["receipts"] == [] and db.data["tenants"][TENANT]["assistant_model"] == OLD_MODEL
