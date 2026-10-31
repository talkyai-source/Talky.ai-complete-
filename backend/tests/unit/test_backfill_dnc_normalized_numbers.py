"""The pure planning half of ``scripts/backfill_dnc_normalized_numbers.py``.

The script itself needs a database; the decision of *what* to write to each
``dnc_entries`` row does not, and that decision is the whole risk: get it
wrong and you either leave a Do-Not-Call number dialable or you overwrite a
row that was already correct.  So the planner is a pure function over row
dicts and it is tested here.

``scripts/`` is not an importable package, so the module is loaded by path.
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

_SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "backfill_dnc_normalized_numbers.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("_backfill_dnc", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # @dataclass resolves annotations through sys.modules[cls.__module__],
    # so the module has to be registered before it is executed.
    sys.modules["_backfill_dnc"] = module
    spec.loader.exec_module(module)
    return module


backfill = _load()

OLD = datetime(2026, 1, 1, 12, 0, 0)
NEW = OLD + timedelta(days=30)


def _row(row_id, number, *, tenant="t1", raw=None, created_at=OLD):
    row = {
        "id": row_id,
        "tenant_id": tenant,
        "normalized_number": number,
        "created_at": created_at,
    }
    if raw is not None:
        row["phone_number"] = raw
    return row


# ---------------------------------------------------------------------------
# canonical_dnc_number -- one row, no duplicate reasoning
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "stored,expected",
    [
        # THE bug: libphonenumber with region=None dropped the +1 on a bare
        # 10-digit US number, so the row never matched the guard's lookup.
        ("+4155551234", "+14155551234"),
        ("+2125551234", "+12125551234"),
        # Already canonical -- must be left exactly alone (idempotent re-runs).
        ("+14155551234", "+14155551234"),
        ("+447700900123", "+447700900123"),
        ("+4915112345678", "+4915112345678"),
        # Stored without the leading '+' (older admin paths).
        ("14155551234", "+14155551234"),
        ("4155551234", "+14155551234"),
        ("(415) 555-1234", "+14155551234"),
    ],
)
def test_canonical_dnc_number(stored: str, expected: str):
    assert backfill.canonical_dnc_number(stored) == expected


@pytest.mark.parametrize("junk", ["+", "+0", "+00", "", "   ", "+07700900123"])
def test_canonical_dnc_number_leaves_unrepairable_junk_alone(junk: str):
    """Never guess. A row we cannot make into E.164 is reported, not rewritten."""
    assert backfill.canonical_dnc_number(junk) is None


def test_raw_column_wins_over_a_damaged_normalized_value():
    """When the table kept the raw input, re-normalise THAT.

    Re-normalising the damaged value alone cannot recover a dropped country
    code -- ``normalize_e164_digits('+4155551234')`` is ``'+4155551234'``.
    """
    assert backfill.canonical_dnc_number("+4155551234", raw="(415) 555-1234") == "+14155551234"
    assert backfill.canonical_dnc_number("+447700900123", raw="+44 7700 900123") == "+447700900123"


def test_swiss_looking_number_is_not_mangled_into_a_us_one():
    """The +1 repair only fires when the stored value is invalid E.164 AND
    the +1 form is valid -- a real 10-digit-after-+ number stays put."""
    assert backfill.canonical_dnc_number("+31612345678") == "+31612345678"


# ---------------------------------------------------------------------------
# plan_rows -- duplicates and the unique constraint
# ---------------------------------------------------------------------------

def test_plan_reports_changed_and_unchanged():
    plan = backfill.plan_rows([
        _row("a", "+4155551234"),
        _row("b", "+14155559999"),
    ])
    by_id = {p.row_id: p for p in plan}
    assert by_id["a"].action == "update"
    assert by_id["a"].new == "+14155551234"
    assert by_id["b"].action == "unchanged"


def test_plan_flags_unrepairable_rows():
    plan = backfill.plan_rows([_row("a", "+"), _row("b", "+07700900123")])
    assert {p.action for p in plan} == {"unrepairable"}


def test_plan_keeps_the_older_row_on_a_collision():
    """Re-normalising can make two rows collide on (tenant_id, number).

    The unique constraint means only one can hold the value: keep the OLDER
    row (it is the one the customer's opt-out actually dates from) and report
    the other rather than silently dropping either.
    """
    plan = backfill.plan_rows([
        _row("newer", "+4155551234", created_at=NEW),
        _row("older", "+14155551234", created_at=OLD),
    ])
    by_id = {p.row_id: p for p in plan}
    assert by_id["older"].action == "unchanged"
    assert by_id["newer"].action == "duplicate"
    assert by_id["newer"].duplicate_of == "older"


def test_plan_collision_between_two_rows_that_both_need_updating():
    plan = backfill.plan_rows([
        _row("newer", "4155551234", created_at=NEW),
        _row("older", "+4155551234", created_at=OLD),
    ])
    by_id = {p.row_id: p for p in plan}
    assert by_id["older"].action == "update"
    assert by_id["older"].new == "+14155551234"
    assert by_id["newer"].action == "duplicate"


def test_plan_does_not_collide_across_tenants():
    """The unique constraint is (tenant_id, normalized_number) -- two tenants
    may each hold the same number, and a global (NULL) row is separate again."""
    plan = backfill.plan_rows([
        _row("t1row", "+4155551234", tenant="t1"),
        _row("t2row", "+4155551234", tenant="t2"),
        _row("globalrow", "+4155551234", tenant=None),
    ])
    assert {p.action for p in plan} == {"update"}


def test_plan_is_idempotent():
    """Running the backfill twice must be a no-op the second time."""
    rows = [_row("a", "+4155551234"), _row("b", "+14155559999")]
    first = backfill.plan_rows(rows)
    applied = [
        _row(p.row_id, p.new or rows[i]["normalized_number"])
        for i, p in enumerate(first)
    ]
    second = backfill.plan_rows(applied)
    assert {p.action for p in second} == {"unchanged"}


def test_summarise_counts_per_tenant():
    plan = backfill.plan_rows([
        _row("a", "+4155551234", tenant="t1"),
        _row("b", "+14155559999", tenant="t1"),
        _row("c", "+2125551234", tenant="t2"),
        _row("d", "+", tenant="t2"),
    ])
    summary = backfill.summarise(plan)
    assert summary["total"] == 4
    assert summary["update"] == 2
    assert summary["unchanged"] == 1
    assert summary["unrepairable"] == 1
    assert summary["by_tenant"]["t1"]["update"] == 1
    assert summary["by_tenant"]["t2"]["update"] == 1
    assert summary["by_tenant"]["t2"]["unrepairable"] == 1
