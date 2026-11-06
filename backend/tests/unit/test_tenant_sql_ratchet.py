"""F2-03: a new unscoped query must fail even if the old total goes down.

The scanner is deliberately loaded from a script, without importing the app or
opening a database. These planted examples exercise query scope separately from
the existing acquisition/GUC guards. Repository debt and justified cross-tenant
exceptions have separate, query-specific entries in the baseline.
"""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import runpy

import pytest


BACKEND = Path(__file__).resolve().parents[2]
SCANNER = BACKEND / "scripts" / "tenant_sql_inventory.py"
BASELINE = Path(__file__).with_name("tenant_sql_baseline.json")
PROTECTED = {"calls", "leads", "recordings", "incidents"}
TENANT_TABLES = {"calls", "leads", "recordings"}


@pytest.fixture(scope="module")
def scanner():
    assert SCANNER.is_file(), (
        "F2-03 has no per-query tenant-predicate scanner; an acquisition/GUC "
        "check cannot reject a new unscoped SQL statement"
    )
    module = runpy.run_path(str(SCANNER))
    for name in ("scan_source", "scan_repository", "audit_findings"):
        assert callable(module.get(name)), f"F2-03 scanner is missing {name}"
    return module


def scan(scanner, source):
    return scanner["scan_source"](
        source,
        relative_path="app/example.py",
        protected_tables=PROTECTED,
        tenant_tables=TENANT_TABLES,
    )


def reader(sql, *, name="read_calls", tenant="tenant_id"):
    return (
        f"async def {name}(pool, tenant_id):\n"
        f"    async with acquire_with_tenant(pool, {tenant}) as conn:\n"
        f"        return await conn.fetch({sql!r}, tenant_id)\n"
    )


def debt_for(findings):
    counts = Counter(row["id"] for row in findings if not row["scoped"])
    return {
        "version": 1,
        "violations": [{"id": key, "count": count} for key, count in counts.items()],
        "exceptions": [],
    }


def test_planted_unscoped_query_fails_despite_valid_tenant_guc(scanner):
    findings = scan(scanner, reader("SELECT id FROM calls"))
    assert len(findings) == 1
    assert findings[0]["tables"] == ["calls"]
    assert findings[0]["scoped"] is False
    assert scanner["audit_findings"](findings, debt_for([])), (
        "A tenant GUC must not grandfather a new query with no tenant predicate"
    )


def test_new_query_fails_even_when_one_old_violation_is_removed(scanner):
    old = scan(scanner, reader("SELECT id FROM calls"))
    replacement = scan(scanner, reader("SELECT id FROM recordings", name="read_recordings"))
    baseline = debt_for(old)
    assert scanner["audit_findings"](old, baseline) == []
    assert len(replacement) == len(old) == 1
    assert scanner["audit_findings"](replacement, baseline), (
        "An unchanged violation count must not admit a different unscoped query"
    )


def test_duplicate_unscoped_statement_cannot_reuse_one_baseline_entry(scanner):
    original = reader("SELECT id FROM calls")
    duplicated = original.replace(
        "        return await conn.fetch",
        "        await conn.fetch('SELECT id FROM calls', tenant_id)\n"
        "        return await conn.fetch",
    )
    old, new = scan(scanner, original), scan(scanner, duplicated)
    assert len(old) == 1 and len(new) == 2
    assert scanner["audit_findings"](new, debt_for(old)), (
        "Baseline identities need multiplicities; adding the same query is growth"
    )


def test_fixed_query_requires_stale_debt_entry_to_be_removed(scanner):
    old = scan(scanner, reader("SELECT id FROM calls"))
    fixed = scan(scanner, reader("SELECT id FROM calls WHERE tenant_id = $1::uuid"))
    assert scanner["audit_findings"](fixed, debt_for(old)), (
        "Leaving fixed debt in the baseline would let it return later"
    )
    assert scanner["audit_findings"](fixed, debt_for([])) == []


@pytest.mark.parametrize("sql", [
    "SELECT id FROM calls WHERE tenant_id = $1::uuid",
    "SELECT c.id FROM calls c WHERE c.tenant_id = $1::uuid",
    "SELECT c.id FROM calls c JOIN leads l ON l.id = c.lead_id "
    "AND l.tenant_id = c.tenant_id WHERE c.tenant_id = $1::uuid",
])
def test_actual_tenant_predicates_are_scoped(scanner, sql):
    findings = scan(scanner, reader(sql))
    assert len(findings) == 1
    assert findings[0]["scoped"] is True
    assert scanner["audit_findings"](findings, debt_for([])) == []


@pytest.mark.parametrize("sql,scoped", [
    ("UPDATE calls SET snapshot = jsonb_set(COALESCE(snapshot, '{}'::jsonb), "
     "'{speaker}', to_jsonb($2::text), true) WHERE tenant_id = $1::uuid AND id = $3::uuid RETURNING id", True),
    ("UPDATE calls SET snapshot = COALESCE(snapshot, '{}'::jsonb) WHERE COALESCE(tenant_id = $1, true)", False),
    ("UPDATE calls SET snapshot = COALESCE(snapshot, '{}'::jsonb) WHERE tenant_id = $1 OR true", False),
    ("UPDATE calls SET snapshot = COALESCE(snapshot, '{}'::jsonb)", False),
    ("UPDATE calls SET snapshot = (SELECT snapshot FROM calls WHERE tenant_id = $1) WHERE id = $2", False),
])
def test_update_value_functions_do_not_change_predicate_scope(scanner, sql, scoped):
    findings = scan(scanner, reader(sql))
    assert len(findings) == 1
    assert findings[0]["scoped"] is scoped


@pytest.mark.parametrize("sql", [
    "SELECT tenant_id, id FROM calls",
    "SELECT id FROM calls -- WHERE tenant_id = $1\n",
    "SELECT 'WHERE tenant_id = $1' AS note, id FROM calls",
    "SELECT id FROM calls WHERE tenant_id = $1 OR 1 = 1",
    "SELECT c.id FROM calls c JOIN leads l ON l.id = c.lead_id "
    "WHERE c.tenant_id = $1::uuid",
])
def test_mentions_or_partial_join_scope_do_not_hide_unscoped_rows(scanner, sql):
    findings = scan(scanner, reader(sql))
    assert len(findings) == 1
    assert findings[0]["scoped"] is False
    assert scanner["audit_findings"](findings, debt_for([]))


def test_different_column_ending_in_tenant_id_does_not_establish_scope(scanner):
    findings = scan(scanner, reader("SELECT id FROM calls WHERE owner_tenant_id = $1::uuid"))
    assert len(findings) == 1
    assert findings[0]["scoped"] is False
    assert scanner["audit_findings"](findings, debt_for([]))


@pytest.mark.parametrize("source", [
    reader("SELECT id FROM calls", tenant="None"),
    "async def read_calls(pool):\n"
    "    async with acquire_connection(pool) as conn:\n"
    "        async with conn.transaction():\n"
    "            await conn.execute(\"SET LOCAL app.bypass_rls = 'on'\")\n"
    "            return await conn.fetch('SELECT id FROM calls')\n",
])
def test_bypass_is_attached_to_the_unscoped_query(scanner, source):
    findings = scan(scanner, source)
    assert len(findings) == 1
    assert findings[0]["bypass"] is True
    assert findings[0]["scoped"] is False


def test_bypass_on_another_connection_is_not_attributed_to_every_query(scanner):
    source = (
        "async def read_calls(pool, tenant_id):\n"
        "    async with acquire_with_tenant(pool, None) as system_conn:\n"
        "        await system_conn.fetch('SELECT id FROM calls')\n"
        "    async with acquire_with_tenant(pool, tenant_id) as tenant_conn:\n"
        "        await tenant_conn.fetch('SELECT id FROM recordings')\n"
    )
    findings = scan(scanner, source)
    assert len(findings) == 2
    by_table = {row["tables"][0]: row for row in findings}
    assert by_table["calls"]["bypass"] is True
    assert by_table["recordings"]["bypass"] is False


def test_assigned_sql_literal_is_not_lost(scanner):
    findings = scan(scanner,
        "async def read_calls(pool, tenant_id):\n"
        "    query = 'SELECT id FROM calls'\n"
        "    async with acquire_with_tenant(pool, tenant_id) as conn:\n"
        "        return await conn.fetch(query)\n"
    )
    assert len(findings) == 1
    assert findings[0]["tables"] == ["calls"]
    assert findings[0]["scoped"] is False


def test_unresolved_executed_sql_is_reported_instead_of_silently_excluded(scanner):
    findings = scan(scanner,
        "async def read_calls(pool, tenant_id, query):\n"
        "    async with acquire_with_tenant(pool, tenant_id) as conn:\n"
        "        return await conn.fetch(query)\n"
    )
    assert len(findings) == 1
    assert findings[0]["unresolved"] is True
    assert findings[0]["scoped"] is False
    assert scanner["audit_findings"](findings, debt_for([]))


def test_module_query_registry_change_cannot_reuse_unresolved_function_debt(scanner):
    source = (
        "QUERIES = {'current': 'SELECT id FROM calls WHERE tenant_id=$1'}\n"
        "async def read_calls(pool, tenant_id):\n"
        "    async with acquire_with_tenant(pool, tenant_id) as conn:\n"
        "        for query in QUERIES.values():\n"
        "            await conn.fetch(query, tenant_id)\n"
    )
    original = scan(scanner, source)
    assert len(original) == 1 and original[0]["unresolved"]
    changed = scan(scanner, source.replace("SELECT id FROM calls WHERE tenant_id=$1", "SELECT id FROM calls"))
    assert scanner["audit_findings"](changed, debt_for(original)), (
        "Changing module-level SQL behind an unchanged dynamic executor must require new review"
    )


def test_docstrings_and_unexecuted_examples_are_not_queries(scanner):
    findings = scan(scanner,
        '"""SELECT * FROM calls"""\n'
        "EXAMPLE = 'SELECT * FROM recordings'\n"
        "async def unrelated():\n"
        "    return 'SELECT * FROM leads'\n"
    )
    assert findings == []


def test_query_identity_is_stable_when_only_source_lines_move(scanner):
    source = reader("SELECT id FROM calls")
    original = scan(scanner, source)
    moved = scan(scanner, "# unrelated comment\n\n" + source)
    assert original[0]["id"] == moved[0]["id"]
    assert scanner["audit_findings"](moved, debt_for(original)) == []


def test_cross_tenant_exception_requires_an_individual_nonempty_reason(scanner):
    findings = scan(scanner, reader("SELECT id FROM incidents", tenant="None"))
    assert len(findings) == 1 and findings[0]["scoped"] is False
    baseline = debt_for([])
    baseline["exceptions"] = [{"id": findings[0]["id"], "count": 1, "reason": ""}]
    assert scanner["audit_findings"](findings, baseline)
    baseline["exceptions"][0]["reason"] = (
        "Platform incident reconciliation intentionally reads all incidents; "
        "this table has no tenant_id and the operation is platform-only."
    )
    assert scanner["audit_findings"](findings, baseline) == []
    another = scan(scanner, reader("SELECT id FROM calls", tenant="None"))
    assert scanner["audit_findings"](another, baseline), (
        "A reason for one system query must not exempt another query in its file"
    )


def test_invalid_python_cannot_disappear_from_the_inventory(scanner):
    with pytest.raises((SyntaxError, ValueError)):
        scan(scanner, "async def broken(:\n")


def test_keyword_query_arguments_are_inventoried(scanner):
    findings = scan(scanner,
        "async def read_calls(conn):\n"
        "    return await conn.fetch(query='SELECT id FROM calls')\n"
    )
    assert len(findings) == 1
    assert findings[0]["scoped"] is False
    assert scanner["audit_findings"](findings, debt_for([]))


def test_comma_join_cannot_hide_another_tenants_table(scanner):
    findings = scan(scanner, reader(
        "SELECT c.id, r.id FROM calls c, recordings r WHERE c.tenant_id = $1"
    ))
    assert len(findings) == 1
    assert findings[0]["scoped"] is False
    assert scanner["audit_findings"](findings, debt_for([]))


def test_class_local_sql_does_not_replace_a_module_query(scanner):
    findings = scan(scanner,
        "SQL = 'SELECT id FROM calls'\n"
        "class Unrelated:\n"
        "    SQL = 'SELECT id FROM calls WHERE tenant_id = $1'\n"
        "async def read_calls(conn):\n"
        "    return await conn.fetch(SQL)\n"
    )
    assert len(findings) == 1
    assert findings[0]["scoped"] is False
    assert scanner["audit_findings"](findings, debt_for([]))


def test_rebound_module_query_is_not_proved_by_its_earlier_literal(scanner):
    findings = scan(scanner,
        "SQL = 'SELECT id FROM calls WHERE tenant_id = $1'\n"
        "async def read_calls(conn):\n"
        "    return await conn.fetch(SQL)\n"
        "SQL = 'SELECT id FROM calls'\n"
    )
    assert len(findings) == 1
    assert findings[0]["scoped"] is False
    assert scanner["audit_findings"](findings, debt_for([]))


def test_delegated_sql_literal_cannot_hide_behind_an_existing_generic_helper(scanner):
    findings = scan(scanner,
        "async def read_calls(conn):\n"
        "    return await execute_query(conn, 'SELECT id FROM calls')\n"
    )
    assert len(findings) == 1
    assert findings[0]["scoped"] is False
    assert scanner["audit_findings"](findings, debt_for([]))


def test_boolean_expression_cannot_turn_a_tenant_equality_into_its_inverse(scanner):
    findings = scan(scanner, reader(
        "SELECT id FROM calls WHERE (tenant_id = $1::uuid) IS DISTINCT FROM TRUE"
    ))
    assert len(findings) == 1
    assert findings[0]["scoped"] is False
    assert scanner["audit_findings"](findings, debt_for([]))


def test_query_identity_preserves_whitespace_inside_quoted_literals(scanner):
    original = scan(scanner, reader("SELECT id FROM calls WHERE status = 'two  spaces'"))
    changed = scan(scanner, reader("SELECT id FROM calls WHERE status = 'two spaces'"))
    assert original[0]["id"] != changed[0]["id"], (
        "Different SQL literal values must not reuse a reviewed query identity"
    )
    assert scanner["audit_findings"](changed, debt_for(original))
    reformatted = scan(scanner, reader(" SELECT\n id  FROM calls\tWHERE status = 'two  spaces' "))
    assert reformatted[0]["id"] == original[0]["id"]
    assert scanner["audit_findings"](reformatted, debt_for(original)) == []


def test_repository_unscoped_query_inventory_does_not_grow(scanner):
    assert BASELINE.is_file(), "F2-03 needs a reviewed query-specific debt/exception inventory"
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    findings = scanner["scan_repository"](BACKEND)
    assert findings, "The repository SQL scan must not be vacuous"
    errors = scanner["audit_findings"](findings, baseline)
    assert not errors, "\n".join(errors)
