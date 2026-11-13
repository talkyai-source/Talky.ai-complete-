"""Offline behavioral contract for the opt-in lead-number repair tool."""
from __future__ import annotations

from contextlib import asynccontextmanager
from copy import deepcopy
from functools import lru_cache
import importlib.util
import json
from pathlib import Path
import re
import sys

import asyncpg
import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "repair_non_e164_leads.py"
TENANT = "00000000-0000-4000-8000-000000000001"
OTHER = "00000000-0000-4000-8000-000000000002"
CAMPAIGN = "00000000-0000-4000-8000-000000000010"
E164 = r"^\+[1-9][0-9]{6,14}$"


@lru_cache
def repair():
    assert SCRIPT.is_file(), "Missing dry-run lead repair tool required by QA F14-03"
    spec = importlib.util.spec_from_file_location("_qa_repair_non_e164_leads", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def row(number, phone, *, tenant=TENANT, status="pending", country="GB", rules=None):
    return {
        "id": f"00000000-0000-4000-8000-{number:012d}",
        "tenant_id": tenant,
        "campaign_id": CAMPAIGN,
        "phone_number": phone,
        "status": status,
        "script_config": {"default_country_code": country} if country else {},
        "calling_rules": rules or {},
    }


class Connection:
    """Small transactional store; exercise actual repair SQL and atomic outcomes."""

    def __init__(self, rows):
        self.rows = deepcopy(rows)
        self.audit_rows = []
        self.updates = []
        self.read_batches = []
        self.transactions = []
        self.depth = 0
        self.audit_locked = False
        self.change_before_update = None

    def is_in_transaction(self):
        return self.depth > 0

    @asynccontextmanager
    async def transaction(self, **kwargs):
        snapshot = deepcopy((self.rows, self.audit_rows, self.updates))
        previous_lock = self.audit_locked
        self.depth += 1
        try:
            yield
        except BaseException:
            self.rows, self.audit_rows, self.updates = snapshot
            self.transactions.append("rollback")
            raise
        else:
            self.transactions.append("commit")
        finally:
            self.depth -= 1
            self.audit_locked = previous_lock

    def _candidates(self, sql, tenant):
        flat = " ".join(sql.lower().split())
        assert re.search(r"(?:\bl\.)?tenant_id\s*=\s*\$1", flat), "Lead query lost tenant predicate"
        assert re.search(r"(?:\bl\.)?status\s*(?:<>|!=)\s*'deleted'", flat), "Deleted leads must be excluded"
        assert re.search(r"(?:\bl\.)?phone_number\s*!~", flat), "Use the owner's exact non-E.164 population"
        assert E164 in sql, "Owner regex must remain exactly equivalent"
        return [r for r in self.rows if r["tenant_id"] == str(tenant)
                and r["status"] is not None and r["status"] != "deleted"
                and not re.fullmatch(E164, r["phone_number"])]

    async def fetchval(self, sql, *args):
        assert "count(" in sql.lower()
        return len(self._candidates(sql, args[0]))

    async def fetch(self, sql, *args):
        tenant, cursor, limit = args
        assert limit > 0
        candidates = sorted(self._candidates(sql, tenant), key=lambda r: r["id"])
        selected = [r for r in candidates if cursor is None or r["id"] > str(cursor)][:limit]
        self.read_batches.append([r["id"] for r in selected])
        # The joins must not source campaign or account settings from another tenant.
        flat = " ".join(sql.lower().split())
        assert "c.tenant_id = l.tenant_id" in flat
        assert "t.id = l.tenant_id" in flat
        assert "script_config" in flat and "calling_rules" in flat
        return deepcopy(selected)

    async def fetchrow(self, sql, *args):
        flat = " ".join(sql.lower().split())
        if flat.startswith("select id from leads"):
            assert re.search(r"tenant_id\s*=\s*\$1", flat)
            assert re.search(r"campaign_id\s*=\s*\$2", flat)
            assert re.search(r"phone_number\s*=\s*\$3", flat)
            assert re.search(r"\bid\s*(?:<>|!=)\s*\$4", flat)
            assert re.search(r"status\s*(?:<>|!=)\s*'deleted'", flat)
            tenant, campaign, number, lead_id = map(str, args)
            return next(({"id": r["id"]} for r in self.rows
                         if r["tenant_id"] == tenant and r["campaign_id"] == campaign
                         and r["phone_number"] == number and r["id"] != lead_id
                         and r["status"] not in (None, "deleted")), None)
        assert flat.startswith("update leads"), "Unexpected mutation/query"
        assert self.is_in_transaction() and self.audit_locked
        assert re.search(r"tenant_id\s*=\s*\$1", flat)
        assert re.search(r"\bid\s*=\s*\$2", flat)
        assert re.search(r"phone_number\s*=\s*\$3", flat), "Concurrent edit must not be overwritten"
        assert re.search(r"status\s*(?:<>|!=)\s*'deleted'", flat)
        tenant, lead_id, before, after = map(str, args)
        current = next((r for r in self.rows if r["tenant_id"] == tenant and r["id"] == lead_id), None)
        if current and self.change_before_update is not None:
            current["phone_number"] = self.change_before_update
            self.change_before_update = None
        if not current or current["phone_number"] != before or current["status"] in (None, "deleted"):
            return None
        if any(r is not current and r["campaign_id"] == current["campaign_id"]
               and r["phone_number"] == after and r["status"] != "deleted" for r in self.rows):
            raise asyncpg.UniqueViolationError("synthetic duplicate phone")
        current["phone_number"] = after
        self.updates.append((tenant, lead_id, before, after))
        return {"id": lead_id}

    async def execute(self, sql, *args):
        # Transaction-local context is permitted; destructive or unrelated SQL is not.
        assert sql.lstrip().upper().startswith(("SET LOCAL", "SELECT SET_CONFIG"))
        return "SELECT 1"


class Audit:
    def __init__(self, fail=False):
        self.fail = fail

    async def lock_transaction(self, conn):
        assert conn.is_in_transaction()
        conn.audit_locked = True

    async def log_in_transaction(self, conn, **entry):
        assert conn.is_in_transaction() and conn.audit_locked
        if self.fail:
            raise RuntimeError("synthetic audit refusal")
        assert str(entry["tenant_id"]) == TENANT
        assert entry["resource_type"] == "lead"
        assert entry["action"]
        conn.audit_rows.append(deepcopy(entry))


def test_cli_is_dry_run_unless_apply_is_explicit():
    parser = repair().build_parser()
    args = parser.parse_args([])
    assert args.apply is False
    assert 1 <= args.batch_size <= 1000
    assert args.sample_limit >= 0
    assert parser.parse_args(["--apply", "--tenant", TENANT]).apply is True


@pytest.mark.parametrize("flag,value", [("--batch-size", "0"), ("--batch-size", "1001"), ("--sample-limit", "-1")])
def test_cli_rejects_unbounded_or_invalid_limits(flag, value):
    with pytest.raises(SystemExit) as caught:
        repair().build_parser().parse_args([flag, value])
    assert caught.value.code == 2


@pytest.mark.parametrize("value,config,rules,expected", [
    ("020 7946 0958", {"default_country_code": "GB"}, {"default_country": "US"}, "+442079460958"),
    ("020 7946 0958", {"campaign_slots": {"default_country_code": "GB"}}, {}, "+442079460958"),
    ("020 7946 0958", {}, {"default_country": "GB"}, "+442079460958"),
    ("415 555 2671", {}, {}, "+14155552671"),
    ("001 4155552671", {"default_country_code": "GB"}, {}, "+14155552671"),
])
def test_plans_only_valid_numbers_using_campaign_account_and_us_precedence(value, config, rules, expected):
    candidate = row(101, value)
    candidate.update(script_config=config, calling_rules=rules)
    plan = repair().plan_row(candidate)
    assert plan["action"] == "repair"
    assert plan["candidate"] == expected


@pytest.mark.parametrize("value", ["12345", "+02079460111", "unknown", "+0123456789"])
def test_unrecoverable_or_already_damaged_country_prefix_is_flagged_not_guessed(value):
    plan = repair().plan_row(row(101, value))
    assert plan["action"] == "invalid"
    assert plan["candidate"] is None


async def test_default_dry_run_counts_exact_population_masks_samples_and_writes_nothing(capsys):
    conn = Connection([
        row(101, "020 7946 0958"), row(102, "12345"),
        row(103, "020 7946 0958", status="deleted"),
        row(104, "+442079460959"), row(105, "020 7946 0958", tenant=OTHER),
        row(106, "020 7946 0958", status=None),
    ])
    before = deepcopy(conn.rows)
    result = await repair().repair_tenant(conn, TENANT, batch_size=1, sample_limit=10)
    assert result["matched"] == 2
    assert result["proposed"] == 1
    assert result["invalid"] == 1
    assert result["updated"] == 0
    assert conn.rows == before and conn.updates == [] and conn.audit_rows == []
    assert max(map(len, conn.read_batches)) == 1
    rendered = json.dumps(result) + capsys.readouterr().out
    for private in ("020 7946 0958", "+442079460958", "12345"):
        assert private not in rendered
    assert result["samples"], "Dry run should expose masked review samples"


async def test_apply_is_tenant_scoped_audited_and_idempotent():
    conn = Connection([
        row(101, "020 7946 0958"), row(102, "020 7946 0959"),
        row(103, "12345"), row(104, "020 7946 0958", tenant=OTHER),
    ])
    result = await repair().repair_tenant(conn, TENANT, apply=True, batch_size=1, audit=Audit())
    assert result["matched"] == 3 and result["updated"] == 2 and result["invalid"] == 1
    assert [r["phone_number"] for r in conn.rows] == ["+442079460958", "+442079460959", "12345", "020 7946 0958"]
    assert len(conn.audit_rows) == 2
    assert {str(entry["resource_id"]) for entry in conn.audit_rows} == {row(101, "")["id"], row(102, "")["id"]}
    assert all(change[0] == TENANT for change in conn.updates)
    assert max(map(len, conn.read_batches)) == 1
    second = await repair().repair_tenant(conn, TENANT, apply=True, batch_size=1, audit=Audit())
    assert second["matched"] == 1 and second["updated"] == 0 and second["invalid"] == 1
    assert len(conn.audit_rows) == 2


async def test_failed_audit_rolls_back_the_same_batch():
    conn = Connection([row(101, "020 7946 0958")])
    before = deepcopy(conn.rows)
    with pytest.raises(RuntimeError, match="synthetic audit refusal"):
        await repair().repair_tenant(conn, TENANT, apply=True, audit=Audit(fail=True))
    assert conn.rows == before and conn.updates == [] and conn.audit_rows == []
    assert "rollback" in conn.transactions


async def test_collision_is_flagged_without_merging_or_deleting_leads():
    conn = Connection([row(101, "020 7946 0958"), row(102, "+442079460958")])
    before = deepcopy(conn.rows)
    dry_run = await repair().repair_tenant(conn, TENANT)
    assert dry_run["matched"] == 1 and dry_run["updated"] == 0 and dry_run["conflicts"] == 1
    assert dry_run["samples"][0]["action"] == "conflict"
    assert conn.rows == before and conn.audit_rows == []
    result = await repair().repair_tenant(conn, TENANT, apply=True, audit=Audit())
    assert result["matched"] == 1 and result["updated"] == 0 and result["conflicts"] == 1
    assert conn.rows == before and conn.audit_rows == []


async def test_dry_run_detects_two_noncanonical_rows_proposing_the_same_number():
    conn = Connection([row(101, "020 7946 0958"), row(102, "020-7946-0958")])
    before = deepcopy(conn.rows)
    result = await repair().repair_tenant(conn, TENANT, batch_size=1)
    assert result["matched"] == 2 and result["proposed"] == 1 and result["conflicts"] == 1
    assert result["updated"] == 0 and conn.rows == before and conn.audit_rows == []


async def test_concurrent_number_edit_is_not_overwritten_or_falsely_audited():
    conn = Connection([row(101, "020 7946 0958")])
    conn.change_before_update = "+442079460999"
    result = await repair().repair_tenant(conn, TENANT, apply=True, audit=Audit())
    assert result["updated"] == 0 and result["conflicts"] == 1
    assert conn.rows[0]["phone_number"] == "+442079460999"
    assert conn.audit_rows == []


async def test_apply_without_audit_refuses_before_writing():
    conn = Connection([row(101, "020 7946 0958")])
    with pytest.raises(ValueError, match="audit"):
        await repair().repair_tenant(conn, TENANT, apply=True)
    assert conn.updates == []
