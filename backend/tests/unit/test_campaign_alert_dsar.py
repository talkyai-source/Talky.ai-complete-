"""F3-08 privacy coverage: actual export registry plus executable alert SQL.

Existing subject/audit fixtures are reused read-only. The four added relations
execute the service's actual FROM/JOIN/WHERE predicates in SQLite, adapting only
PostgreSQL array binding/JSON extraction; no implicit tenant filter is supplied.
"""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import re
import sqlite3
from uuid import UUID

import pytest

from app.domain.services import dsar_service

_spec = importlib.util.spec_from_file_location("alert_dsar_existing_fixture",
    Path(__file__).with_name("test_round3_dsar_export.py"))
base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(base)
NEW = {"security_events", "campaign_alert_outbox", "campaign_alert_state", "tenant_alert_state"}


def data():
    rows = base.data()
    rows.update({name: [] for name in NEW})
    for number, tenant in ((1, base.TENANT), (2, base.TENANT), (3, base.OTHER)):
        event = base.uid(700 + number)
        rows["security_events"].append({"event_id": event, "tenant_id": tenant,
            "evidence": {"kind": "qualified_lead", "call_id": base.uid(10 + number), "lead_id": base.uid(number)},
            "detection_source": "campaign_alerts", "description": "Masked qualification notice"})
        rows["campaign_alert_outbox"].append({"id": base.uid(800 + number), "tenant_id": tenant,
            "event_id": event, "recipient": f"owner{number}@example.invalid", "body": "Masked lead notice", "status": "accepted"})
        rows["campaign_alert_state"].append({"tenant_id": tenant, "campaign_id": base.uid(20 + number), "failure_count": 1})
    rows["security_events"].append({"event_id": base.uid(704), "tenant_id": base.TENANT,
        "evidence": {"kind": "low_minutes"}, "detection_source": "campaign_alerts", "description": "Unrelated account allowance"})
    rows["campaign_alert_outbox"].append({"id": base.uid(804), "tenant_id": base.TENANT,
        "event_id": base.uid(704), "recipient": "account-owner@example.invalid", "body": "Unrelated account allowance", "status": "pending"})
    rows["tenant_alert_state"] = [{"tenant_id": tenant, "low_minutes_active": True}
                                  for tenant in (base.TENANT, base.OTHER)]
    return rows


class Connection(base.Connection):
    def __init__(self, rows):
        super().__init__(rows)
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        CREATE TABLE security_events(event_id TEXT,tenant_id TEXT,evidence TEXT,detection_source TEXT,description TEXT);
        CREATE TABLE campaign_alert_outbox(id TEXT,tenant_id TEXT,event_id TEXT,recipient TEXT,body TEXT,status TEXT);
        CREATE TABLE campaign_alert_state(tenant_id TEXT,campaign_id TEXT,failure_count INTEGER);
        CREATE TABLE tenant_alert_state(tenant_id TEXT,low_minutes_active INTEGER);
        CREATE TABLE calls(id TEXT,tenant_id TEXT);
        CREATE TABLE leads(id TEXT,tenant_id TEXT);
        """)
        for table in ("calls", "leads"):
            self.db.executemany(f"INSERT INTO {table} VALUES(?,?)",
                                [(row["id"], row["tenant_id"]) for row in rows[table]])
        for table in NEW:
            for row in rows[table]:
                names = list(row)
                params = [json.dumps(row[name]) if isinstance(row[name], dict) else row[name] for name in names]
                self.db.execute(f"INSERT INTO {table}({','.join(names)}) VALUES({','.join('?' for _ in names)})", params)

    def _select(self, sql, args):
        match = re.search(r"FROM public\.(\w+) AS r", sql)
        if not match or match[1] not in NEW:
            return super()._select(sql, args)
        table = match[1]
        assert table not in self.absent
        assert self.audit_locked and self.is_in_transaction()
        assert len(args) == 7 and str(args[0]) == base.TENANT
        assert re.search(r"r\.tenant_id\s*=\s*\$1", sql)
        self.reads.append(table)
        if self.fail_table == table:
            raise RuntimeError("synthetic permission failure")
        # Discard only PostgreSQL JSON row projection/typed singleton CTE. Keep
        # the real joins and all predicates/limit so unscoped SQL leaks fixtures.
        query = "SELECT r.* FROM " + table + " AS r" + sql.split(f"FROM public.{table} AS r", 1)[1]
        query = query.replace("CROSS JOIN subject", "").replace("public.", "")
        query = re.sub(r"=\s*ANY\(\$(\d+)::uuid\[\](?:::text\[\])?\)",
                       lambda m: f" IN (SELECT value FROM json_each(${m[1]}))", query)
        query = re.sub(r"([re])\.evidence\s*->>\s*'(call_id|lead_id)'",
                       lambda m: f"json_extract({m[1]}.evidence,'$.{m[2]}')", query)
        query = re.sub(r"::(?:uuid|text|boolean|integer)(?:\[\])?", "", query)
        bound = []
        def parameter(match):
            value = args[int(match[1]) - 1]
            if isinstance(value, UUID):
                value = str(value)
            elif isinstance(value, (list, tuple)):
                value = json.dumps([str(item) for item in value])
            bound.append(value)
            return "?"
        query = re.sub(r"\$(\d+)", parameter, query)
        fetched = [dict(row) for row in self.db.execute(query, bound)]
        for row in fetched:
            if "evidence" in row:
                row["evidence"] = json.loads(row["evidence"])
        return [{"data": json.dumps(row), "total_count": len(fetched)} for row in fetched]


@pytest.fixture
def harness(monkeypatch):
    conn = Connection(data())
    audit = base.Audit()
    async def authority(connection, actor):
        assert connection is conn and conn.audit_locked and conn.is_in_transaction()
        assert str(actor) == base.ACTOR
    monkeypatch.setattr(dsar_service, "require_platform_admin_mutation", authority)
    service = dsar_service.DsarService(base.Pool(conn), audit=audit, artifacts=base.Artifacts())
    yield service, conn
    conn.db.close()


@pytest.mark.asyncio
async def test_whole_tenant_archive_includes_all_four_alert_relations(harness, tmp_path):
    service, conn = harness
    before = deepcopy(conn.rows)
    result = await service.export(dsar_service.DsarScope(tenant_id=base.TENANT, whole_tenant=True),
                                  actor_id=base.ACTOR, output=tmp_path / "tenant-alerts.zip")
    manifest, rows, _ = base.read_rows(result.path)
    assert NEW <= rows.keys(), "New durable alert relations must not silently disappear from whole-tenant exports"
    assert {name: len(rows[name]) for name in NEW} == {
        "security_events": 3, "campaign_alert_outbox": 3, "campaign_alert_state": 2, "tenant_alert_state": 1}
    assert all(row["tenant_id"] == base.TENANT for table in NEW for row in rows[table])
    assert manifest["destructive_apply_supported"] is False
    assert manifest["full_dsar_complete"] is False and conn.rows == before


@pytest.mark.asyncio
@pytest.mark.parametrize("selector", [{"phone": base.PHONE}, {"email": base.EMAIL}])
async def test_person_archive_uses_owned_call_lead_identity_without_account_alerts(harness, tmp_path, selector):
    service, _ = harness
    result = await service.export(dsar_service.DsarScope(tenant_id=base.TENANT, **selector),
                                  actor_id=base.ACTOR, output=tmp_path / "person-alerts.zip")
    manifest, rows, _ = base.read_rows(result.path)
    assert NEW <= rows.keys()
    assert [row["event_id"] for row in rows["security_events"]] == [base.uid(701)]
    assert [row["id"] for row in rows["campaign_alert_outbox"]] == [base.uid(801)]
    assert rows["campaign_alert_state"] == rows["tenant_alert_state"] == []
    assert "Unrelated account allowance" not in json.dumps({name: rows[name] for name in NEW})
    assert manifest["counts"]["campaign_alert_outbox"] == 1


@pytest.mark.asyncio
async def test_person_outbox_cannot_join_foreign_event_with_matching_subject_id(harness, tmp_path):
    service, conn = harness
    foreign_event = base.uid(703)
    conn.db.execute("UPDATE security_events SET evidence=? WHERE event_id=?",
                    (json.dumps({"call_id": base.uid(11), "lead_id": base.uid(1)}), foreign_event))
    # Legacy-corrupt fixture deliberately violates the new composite FK. Query
    # ownership must still reject the foreign parent instead of relying on RLS.
    conn.db.execute("INSERT INTO campaign_alert_outbox VALUES(?,?,?,?,?,?)",
                    (base.uid(899), base.TENANT, foreign_event, "foreign@example.invalid", "FOREIGN EVENT", "accepted"))
    # Existing subject resolution may encounter a legacy-corrupt owned capture
    # pointing at a foreign call. Membership in its accumulated IDs alone is
    # not proof that the evidence parent belongs to this tenant.
    conn.rows["call_lead_details"].append({
        "id": base.uid(697), "tenant_id": base.TENANT, "call_id": base.uid(13),
        "lead_id": None, "field_type": "phone", "field_key": "phone",
        "normalized_value": base.PHONE, "confirmed": True, "validation_status": "confirmed",
    })
    conn.db.execute("INSERT INTO security_events VALUES(?,?,?,?,?)",
                    (base.uid(698), base.TENANT, json.dumps({"call_id": base.uid(13)}),
                     "campaign_alerts", "FOREIGN CALL VIA CAPTURE"))
    conn.db.execute("INSERT INTO campaign_alert_outbox VALUES(?,?,?,?,?,?)",
                    (base.uid(898), base.TENANT, base.uid(698), "owner@example.invalid", "FOREIGN CALL", "accepted"))
    result = await service.export(dsar_service.DsarScope(tenant_id=base.TENANT, phone=base.PHONE),
                                  actor_id=base.ACTOR, output=tmp_path / "no-foreign-alerts.zip")
    _, rows, _ = base.read_rows(result.path)
    assert "campaign_alert_outbox" in rows
    assert [row["id"] for row in rows["campaign_alert_outbox"]] == [base.uid(801)]
    assert [row["event_id"] for row in rows["security_events"]] == [base.uid(701)]


@pytest.mark.asyncio
async def test_missing_alert_event_parent_marks_export_incomplete(harness):
    service, conn = harness
    conn.absent.add("security_events")
    manifest = await service.preview(dsar_service.DsarScope(tenant_id=base.TENANT, phone=base.PHONE), actor_id=base.ACTOR)
    assert "security_events" in manifest["missing_tables"]
    assert manifest["counts"]["campaign_alert_outbox"] is None
    assert {"code": "missing_dependency", "table": "campaign_alert_outbox"} in manifest["issues"]
    assert manifest["complete"] is False
    assert "campaign_alert_outbox" not in conn.reads
