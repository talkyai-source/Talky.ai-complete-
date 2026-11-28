"""0109 makes the Basic plan's recording retention line say 90 days (B6)."""
import importlib
import json
from pathlib import Path

from alembic.script import ScriptDirectory

SCRIPTS = ScriptDirectory(str(Path(__file__).resolve().parents[2] / "Alembic"))
REVISION = "0109_plan_retention_line"
OLD = "Recording retention: 30 days"
NEW = "Recording retention: 90 days"


def _module():
    return importlib.import_module("Alembic.versions.0109_plan_retention_line")


def _plans():
    """The stored rows as 0071 / 0083 wrote them (only `features` matters here)."""
    m71 = importlib.import_module("Alembic.versions.0071_paid_only_billing")
    m83 = importlib.import_module("Alembic.versions.0083_pay_as_you_go")
    return {
        "basic": json.loads(m71.BASIC_FEATURES),
        "business": json.loads(m71.BUSINESS_FEATURES),
        "payg": json.loads(m83.FEATURES),
    }


def test_0109_sits_on_top_of_0108_under_one_head():
    assert SCRIPTS.get_heads() == [REVISION]
    assert SCRIPTS.get_revision(REVISION).down_revision == "0108_telephony_number_requests"
    assert len(REVISION) <= 32 and _module().revision == REVISION


def test_the_stored_basic_line_is_the_old_text_and_the_others_say_90():
    plans = _plans()
    assert OLD in plans["basic"] and NEW not in plans["basic"]
    assert NEW in plans["business"] and NEW in plans["payg"]


def test_updated_features_changes_only_the_one_line_and_keeps_order():
    m = _module()
    items = _plans()["basic"]
    after = m.updated_features(items)
    assert after == [NEW if i == OLD else i for i in items]
    assert len(after) == len(items) and after.count(NEW) == 1 and OLD not in after


def test_second_run_changes_nothing():
    m = _module()
    once = m.updated_features(_plans()["basic"])
    assert m.updated_features(once) == once


def test_a_line_staff_changed_is_left_alone():
    m = _module()
    edited = ["a", "Recording retention: 45 days", "b"]
    assert m.updated_features(edited) == edited
    assert m.updated_features(["Recording retention: 30 days (trial)"]) == ["Recording retention: 30 days (trial)"]
    assert m.updated_features([]) == []


def test_restored_features_puts_back_only_the_line_the_upgrade_wrote():
    m = _module()
    items = _plans()["basic"]
    assert m.restored_features(m.updated_features(items)) == items
    edited = ["a", "Recording retention: 45 days"]
    assert m.restored_features(edited) == edited


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def all(self):
        return self.rows


class _Bind:
    """Holds plan rows; applies the UPDATE statements the migration sends."""
    def __init__(self, plans):
        self.plans = {k: list(v) for k, v in plans.items()}
        self.statements = []
        self.params = []

    def execute(self, statement, params=None):
        sql = " ".join(str(statement).split())
        self.statements.append(sql)
        self.params.append(params)
        if sql.startswith("SELECT"):
            wanted = (params or {}).get("line")
            rows = [{"id": k, "features": json.dumps(v)} for k, v in self.plans.items()
                    if "id = 'basic'" in sql and k == "basic" and (wanted is None or json.loads(wanted)[0] in v)]
            return _Result(rows)
        if sql.startswith("UPDATE"):
            self.plans[params["id"]] = json.loads(params["value"])
        return _Result([])


def _run(monkeypatch, plans, operation):
    m = _module()
    bind = _Bind(plans)
    monkeypatch.setattr(m.op, "get_bind", lambda: bind)
    getattr(m, operation)()
    return bind


def test_upgrade_rewrites_only_basic_and_only_that_line(monkeypatch):
    before = _plans()
    bind = _run(monkeypatch, before, "upgrade")
    assert bind.plans["basic"] == [NEW if i == OLD else i for i in before["basic"]]
    assert bind.plans["business"] == before["business"] and bind.plans["payg"] == before["payg"]
    text = " ".join(bind.statements)
    assert "SET LOCAL lock_timeout" in text and "SET LOCAL statement_timeout" in text
    updates = [s for s in bind.statements if s.startswith("UPDATE")]
    assert len(updates) == 1 and "SET features" in updates[0] and "WHERE id = :id" in updates[0]
    assert "price" not in updates[0] and "concurrent_calls" not in updates[0]


def test_upgrade_twice_changes_nothing_the_second_time(monkeypatch):
    first = _run(monkeypatch, _plans(), "upgrade")
    again = _run(monkeypatch, first.plans, "upgrade")
    assert again.plans == first.plans and not [s for s in again.statements if s.startswith("UPDATE")]


def test_upgrade_leaves_a_staff_edited_line_alone(monkeypatch):
    plans = _plans()
    plans["basic"] = [("Recording retention: 45 days" if i == OLD else i) for i in plans["basic"]]
    bind = _run(monkeypatch, plans, "upgrade")
    assert bind.plans == plans and not [s for s in bind.statements if s.startswith("UPDATE")]


def test_downgrade_puts_the_old_line_back(monkeypatch):
    original = _plans()
    up = _run(monkeypatch, original, "upgrade")
    down = _run(monkeypatch, up.plans, "downgrade")
    assert down.plans == original


def test_downgrade_never_touches_a_line_the_upgrade_did_not_write(monkeypatch):
    plans = _plans()
    plans["basic"] = [("Recording retention: 45 days" if i == OLD else i) for i in plans["basic"]]
    bind = _run(monkeypatch, plans, "downgrade")
    assert bind.plans == plans and not [s for s in bind.statements if s.startswith("UPDATE")]
