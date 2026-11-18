"""0097 removes the unbacked "Automatic campaign wizard" plan line (D5-2)."""
import importlib
import json
from pathlib import Path

from alembic.script import ScriptDirectory

SCRIPTS = ScriptDirectory(str(Path(__file__).resolve().parents[2] / "Alembic"))
REVISION = "0097_remove_wizard_plan_line"
LINE = "Automatic campaign wizard"


def _module():
    return importlib.import_module("Alembic.versions.0097_remove_campaign_wizard_plan_line")


def _plans():
    """The three stored rows that carry the line, as 0071 / 0083 wrote them."""
    m71 = importlib.import_module("Alembic.versions.0071_paid_only_billing")
    m83 = importlib.import_module("Alembic.versions.0083_pay_as_you_go")
    return {
        "basic": {"features": json.loads(m71.BASIC_FEATURES), "not_included": json.loads(m71.BASIC_NOT_INCLUDED)},
        "business": {"features": json.loads(m71.BUSINESS_FEATURES), "not_included": []},
        "payg": {"features": json.loads(m83.FEATURES), "not_included": []},
    }


def test_0097_sits_on_top_of_0096_under_one_head():
    assert SCRIPTS.get_heads() == ["0103_recording_notice_state"]
    assert SCRIPTS.get_revision("0098_calls_changed_since_index").down_revision == REVISION
    assert SCRIPTS.get_revision(REVISION).down_revision == "0096_contact_list_permission"
    assert len(REVISION) <= 32 and _module().revision == REVISION


def test_without_line_removes_only_that_line_and_keeps_order():
    m = _module()
    assert m.without_line(["a", LINE, "b", LINE]) == ["a", "b"]
    assert m.without_line(["a", "b"]) == ["a", "b"]
    assert m.without_line([]) == []


def test_transformation_on_the_real_values_touches_only_the_wizard_line():
    m = _module()
    for plan_id, columns in _plans().items():
        for column, items in columns.items():
            after = m.without_line(items)
            assert after == [i for i in items if i != LINE], (plan_id, column)
            assert LINE not in after
    plans = _plans()
    assert m.without_line(plans["basic"]["not_included"]) == ["CRM connectors"]
    assert len(m.without_line(plans["business"]["features"])) == len(plans["business"]["features"]) - 1


def test_second_run_changes_nothing():
    m = _module()
    for columns in _plans().values():
        for items in columns.values():
            once = m.without_line(items)
            assert m.without_line(once) == once


def test_downgrade_restores_each_plan_exactly():
    m = _module()
    for plan_id, columns in _plans().items():
        for column, items in columns.items():
            removed = m.without_line(items)
            restored = m.restore_line(plan_id, column, removed)
            assert restored == items, (plan_id, column)
            assert m.restore_line(plan_id, column, restored) == restored  # idempotent


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
        self.plans = {k: {c: list(v) for c, v in cols.items()} for k, cols in plans.items()}
        self.statements = []

    def execute(self, statement, params=None):
        sql = " ".join(str(statement).split())
        self.statements.append(sql)
        if sql.startswith("SELECT"):
            rows = [{"id": k, "features": json.dumps(v["features"]), "not_included": json.dumps(v["not_included"])}
                    for k, v in self.plans.items()
                    if "id IN" in sql or LINE in v["features"] or LINE in v["not_included"]]
            return _Result(rows)
        if sql.startswith("UPDATE"):
            column = "features" if "SET features" in sql else "not_included"
            self.plans[params["id"]][column] = json.loads(params["value"])
        return _Result([])


def _run(monkeypatch, plans, operation):
    m = _module()
    bind = _Bind(plans)
    monkeypatch.setattr(m.op, "get_bind", lambda: bind)
    getattr(m, operation)()
    return bind


def test_upgrade_updates_only_rows_that_carry_the_line_and_is_idempotent(monkeypatch):
    bind = _run(monkeypatch, _plans(), "upgrade")
    assert all(LINE not in v["features"] and LINE not in v["not_included"] for v in bind.plans.values())
    assert bind.plans["basic"]["not_included"] == ["CRM connectors"]
    assert "SET LOCAL lock_timeout" in " ".join(bind.statements)
    assert "SET LOCAL statement_timeout" in " ".join(bind.statements)
    updates = [s for s in bind.statements if s.startswith("UPDATE")]
    assert all("price" not in s and "concurrent_calls" not in s and "WHERE id = :id" in s for s in updates)
    before = _Bind(bind.plans).plans
    again = _run(monkeypatch, bind.plans, "upgrade")
    assert again.plans == before and not [s for s in again.statements if s.startswith("UPDATE")]


def test_downgrade_puts_the_line_back_where_each_plan_had_it(monkeypatch):
    original = _plans()
    up = _run(monkeypatch, original, "upgrade")
    down = _run(monkeypatch, up.plans, "downgrade")
    assert down.plans == original
