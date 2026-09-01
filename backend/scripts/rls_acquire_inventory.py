#!/usr/bin/env python3
"""Every pooled DB acquisition in backend/app, and whether it sets an RLS context.

    python scripts/rls_acquire_inventory.py                     # human table, worst first
    python scripts/rls_acquire_inventory.py --json              # machine output
    python scripts/rls_acquire_inventory.py --fail-on-needs-tenant   # CI gate

WHY THIS EXISTS
---------------
On 2026-08-30 the production app role lost SUPERUSER and BYPASSRLS::

    ALTER ROLE talkyai NOSUPERUSER NOBYPASSRLS;

That was required — ``app/core/inbound_startup.py`` refuses to boot production
inbound on a superuser role. Alembic ``0013_canonical_rls_policies`` had already
installed FORCE ROW LEVEL SECURITY with this policy on every tenant-scoped
table::

    COALESCE(NULLIF(current_setting('app.bypass_rls', TRUE),'')::boolean, FALSE)
    OR tenant_id = NULLIF(current_setting('app.current_tenant_id', TRUE),'')::uuid
    [OR tenant_id IS NULL]

``current_setting(..., TRUE)`` returns NULL when the GUC is unset, so an unset
GUC makes the whole expression false. A pooled connection that never sets a
tenant GUC therefore matches ZERO ROWS — and reports success. Writes are
rejected by the same expression as WITH CHECK. Proven on production that day::

    SELECT count(*) FROM calls  ->     0   with no GUC
                                ->  1041   with SET app.bypass_rls='true'

This is the failure mode that is invisible in code review: a bare
``async with pool.acquire() as conn`` looks exactly like a correct one. Eight
such sites were found by hand. This tool exists so the operator can see the
whole surface instead of the eight we happened to trip over.

THE FIX AT EACH SITE (already used elsewhere in this codebase)
-------------------------------------------------------------
    from app.core.db_utils import acquire_with_tenant

    async with acquire_with_tenant(pool, tenant_id) as conn:   # tenant-scoped
    async with acquire_with_tenant(pool, None) as conn:        # platform/cross-tenant

``acquire_with_tenant`` (``app/core/db_utils.py``) opens a transaction and uses
SET LOCAL, so the GUC is dropped when the connection returns to the pool. An
explicit ``SET LOCAL app.bypass_rls`` inside a transaction (as
``api/v1/endpoints/calls.py`` does) is equally acceptable and is scored ``ok``.

WHAT IT REPORTS, AND HOW MUCH TO TRUST IT
-----------------------------------------
This is a static AST + regex tool. It reads no database and imports nothing from
the app. Table names come from a best-effort regex over the SQL string literals
in the *enclosing function*, so dynamically built SQL and helper-delegated
queries are under-reported; the RLS table set comes from ``ENABLE ROW LEVEL
SECURITY`` / ``CREATE POLICY`` statements in ``database/**.sql`` and
``Alembic/versions/*.py``.

Verdicts:

  ok            The site sets an RLS context: ``acquire_with_tenant(pool, x)``,
                or a bare acquire whose function contains an explicit
                ``SET LOCAL app.*``, or a bare acquire whose parsed tables are
                all outside the RLS set.

  needs_tenant  A bare ``pool.acquire()`` that touches an RLS-protected table
                *and* has a tenant id already in scope. Mechanically fixable:
                wrap it in ``acquire_with_tenant(pool, <that tenant>)``. These
                are what ``--fail-on-needs-tenant`` gates on.

  needs_review  A human has to decide. Either a bare acquire on RLS tables with
                no tenant in scope (is this genuinely platform-wide work, or is
                the tenant id simply not plumbed yet?), or a bare acquire whose
                SQL could not be parsed at all, or an ``acquire_with_tenant(...,
                None)`` written in a function that *does* have a tenant in scope
                — platform bypass may be right there, but it should be
                deliberate, not a shortcut past the plumbing.

A verdict of ``ok`` is evidence about the RLS context, not about correctness.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path
from typing import Iterable, Iterator, Optional

# --------------------------------------------------------------------------
# Which receivers count as a database pool
# --------------------------------------------------------------------------
# `.acquire()` is also how this codebase takes concurrency guards and
# semaphores, which have nothing to do with RLS. Decide on the dotted receiver
# expression, split into word tokens: `db_client.pool`, `self._db_pool`,
# `_c4.db_pool` are pools; `self._guard`, `guard._sem`, `lock` are not.
_POOL_TOKENS = {"pool", "pools", "db", "dbs", "database", "databases", "dbpool"}
_NOT_POOL_TOKENS = {
    "lock",
    "locks",
    "sem",
    "semaphore",
    "guard",
    "guards",
    "mutex",
    "limiter",
    "throttle",
    "slot",
    "slots",
}

# Best-effort SQL table extraction from string literals.
_TABLE_RE = re.compile(
    r"\b(?:from|join|insert\s+into|update|delete\s+from|"
    r"truncate(?:\s+table)?|into)\s+"
    r"(?:only\s+)?(?:public\.)?\"?([a-z_][a-z0-9_]*)\"?",
    re.IGNORECASE,
)

# Words that follow FROM/UPDATE/INTO but are not tables.
_TABLE_STOPWORDS = {
    "select",
    "where",
    "set",
    "values",
    "lateral",
    "unnest",
    "generate_series",
    "jsonb_array_elements",
    "json_array_elements",
    "jsonb_to_recordset",
    "dual",
    "returning",
    "as",
    "table",
    "only",
    "distinct",
    "current_date",
    "now",
}

_SET_LOCAL_RE = re.compile(r"set\s+local\s+app\.(bypass_rls|current_tenant_id)", re.IGNORECASE)
_SET_CONFIG_LOCAL_RE = re.compile(
    r"set_config\s*\(\s*['\"]app\.(?:bypass_rls|current_tenant_id)['\"]\s*," r"[\s\S]*?\btrue\s*\)",
    re.IGNORECASE,
)

# A name that plausibly carries a tenant id.
_TENANT_NAME_RE = re.compile(r"^_?tenant(_id|_uuid)?$|_tenant_id$|^tenant_uuid$", re.I)

# --------------------------------------------------------------------------
# RLS table discovery (from the schema in this repo, not from a live DB)
# --------------------------------------------------------------------------
_ENABLE_RLS_RE = re.compile(
    r"alter\s+table\s+(?:if\s+exists\s+)?(?:only\s+)?(?:public\.)?\"?"
    r"([a-z_][a-z0-9_]*)\"?[^;]*?enable\s+row\s+level\s+security",
    re.IGNORECASE | re.DOTALL,
)
_CREATE_POLICY_RE = re.compile(
    r"create\s+policy\s+[^\s]+\s+on\s+(?:public\.)?\"?([a-z_][a-z0-9_]*)\"?",
    re.IGNORECASE,
)
# `create_table("x", ..., sa.Column("tenant_id", ...))` and CREATE TABLE ... tenant_id
_CREATE_TABLE_SQL_RE = re.compile(
    r"create\s+table\s+(?:if\s+not\s+exists\s+)?(?:public\.)?\"?"
    r"([a-z_][a-z0-9_]*)\"?\s*\((.*?)\n\s*\)\s*;?",
    re.IGNORECASE | re.DOTALL,
)

# Only executable/runtime schema sources are evidence.  Test fixtures, docs,
# generated reports and archived SQL often contain example ``ALTER TABLE`` or
# ``CREATE POLICY`` statements; counting those would make a prose claim satisfy
# the production RLS gate.
_NON_RUNTIME_SCHEMA_PARTS = frozenset(
    {
        "__pycache__",
        "_archive",
        "docs",
        "evidence",
        "fixtures",
        "generated",
        "reports",
        "sessions",
        "temp",
        "tests",
        "tmp",
    }
)


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _literal_string_set(
    node: ast.AST,
    constants: dict[str, set[str]],
) -> set[str]:
    """Resolve a literal table-name collection without importing a migration."""

    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value}
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        values: set[str] = set()
        for item in node.elts:
            values.update(_literal_string_set(item, constants))
        return values
    if isinstance(node, ast.Call):
        name = node.func.id if isinstance(node.func, ast.Name) else None
        if name in {"frozenset", "list", "set", "tuple"} and node.args:
            return _literal_string_set(node.args[0], constants)
    if isinstance(node, ast.Name):
        return set(constants.get(node.id, set()))
    return set()


def _python_schema_evidence(source: str) -> tuple[set[str], set[str]]:
    """Find executable dynamic RLS/table declarations in an Alembic module.

    Regexes intentionally handle literal SQL.  Current migrations also install
    policies through helpers such as ``for table in _RLS_TABLES:
    _canonical_rls(table)`` and create tables through ``op.create_table``.
    Parse those two bounded forms so a Python loop cannot disappear from the
    tenant-boundary inventory.  No migration is imported or executed.
    """

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set(), set()

    constants: dict[str, set[str]] = {}
    for node in tree.body:
        name: Optional[str] = None
        value: Optional[ast.AST] = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name):
                name = target.id
                value = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            name = node.target.id
            value = node.value
        if name is not None and value is not None:
            resolved = _literal_string_set(value, constants)
            if resolved:
                constants[name] = resolved

    def strings_in(node: ast.AST) -> str:
        return "\n".join(
            sub.value
            for sub in ast.walk(node)
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str)
        )

    policy_installers = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and "create policy" in strings_in(node).lower()
    }

    rls_tables: set[str] = set()
    tenant_scoped: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            call_name = (
                node.func.attr
                if isinstance(node.func, ast.Attribute)
                else node.func.id
                if isinstance(node.func, ast.Name)
                else None
            )
            if call_name == "create_table" and node.args:
                table_names = _literal_string_set(node.args[0], constants)
                has_tenant_column = any(
                    isinstance(child, ast.Call)
                    and (
                        child.func.attr
                        if isinstance(child.func, ast.Attribute)
                        else child.func.id
                        if isinstance(child.func, ast.Name)
                        else None
                    )
                    == "Column"
                    and child.args
                    and _literal_string_set(child.args[0], constants) == {"tenant_id"}
                    for child in ast.walk(node)
                )
                if has_tenant_column:
                    tenant_scoped.update(table_names)

            if call_name in policy_installers and node.args:
                rls_tables.update(_literal_string_set(node.args[0], constants))

        if isinstance(node, (ast.For, ast.AsyncFor)):
            body = ast.Module(body=node.body, type_ignores=[])
            body_calls_installer = any(
                isinstance(child, ast.Call)
                and (
                    child.func.id
                    if isinstance(child.func, ast.Name)
                    else child.func.attr
                    if isinstance(child.func, ast.Attribute)
                    else None
                )
                in policy_installers
                for child in ast.walk(body)
            )
            body_has_policy_ddl = "create policy" in strings_in(body).lower()
            if body_calls_installer or body_has_policy_ddl:
                rls_tables.update(_literal_string_set(node.iter, constants))

    return rls_tables, tenant_scoped


def discover_rls_tables(backend_root: Path) -> tuple[set[str], set[str]]:
    """Return (rls_protected_tables, tenant_scoped_tables) found in the repo schema.

    ``rls_protected`` is any table this repo enables row level security on or
    writes a policy for. ``tenant_scoped`` is any CREATE TABLE whose body
    mentions a ``tenant_id`` column — Alembic 0013 discovers its targets
    dynamically (every RLS-enabled public table carrying ``tenant_id``), so a
    tenant-scoped table is a table the policy will attach to the moment RLS is
    switched on for it.
    """
    rls: set[str] = set()
    tenant_scoped: set[str] = set()

    sources: list[Path] = []
    for sub in ("database", "Alembic/versions", "alembic/versions"):
        d = backend_root / sub
        if d.is_dir():
            sources.extend(p for p in d.rglob("*.sql"))
            sources.extend(p for p in d.rglob("*.py"))

    for path in sources:
        # Superseded schemas, fixtures and human/generated evidence are not
        # executable production sources. Reading them would invent protection
        # that does not exist (and could let a test assertion prove itself).
        relative_parts = {part.lower() for part in path.relative_to(backend_root).parts}
        if relative_parts & _NON_RUNTIME_SCHEMA_PARTS:
            continue
        text = _read(path)
        if not text:
            continue
        if path.suffix.lower() == ".py":
            python_rls, python_tenant_scoped = _python_schema_evidence(text)
            rls.update(name.lower() for name in python_rls)
            tenant_scoped.update(name.lower() for name in python_tenant_scoped)
        for m in _ENABLE_RLS_RE.finditer(text):
            rls.add(m.group(1).lower())
        for m in _CREATE_POLICY_RE.finditer(text):
            rls.add(m.group(1).lower())
        for m in _CREATE_TABLE_SQL_RE.finditer(text):
            if "tenant_id" in m.group(2).lower():
                tenant_scoped.add(m.group(1).lower())

    rls.discard("public")
    tenant_scoped.discard("public")
    return rls, tenant_scoped


# --------------------------------------------------------------------------
# AST walk
# --------------------------------------------------------------------------
def _dotted(node: ast.AST) -> Optional[str]:
    """Render a Name/Attribute chain as a dotted string, else None."""
    parts: list[str] = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
    elif isinstance(cur, ast.Call):
        parts.append("()")
    else:
        return None
    return ".".join(reversed(parts))


def _tokens(dotted: str) -> set[str]:
    return {t for t in re.split(r"[._]+", dotted.lower()) if t}


def _is_pool_receiver(dotted: Optional[str]) -> bool:
    if not dotted:
        return False
    toks = _tokens(dotted)
    if toks & _NOT_POOL_TOKENS:
        return False
    return bool(toks & _POOL_TOKENS)


def _iter_strings(node: ast.AST) -> Iterator[str]:
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            yield sub.value
        elif isinstance(sub, ast.JoinedStr):
            # f-string: only the literal segments are knowable statically.
            for value in sub.values:
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    yield value.value


def _tables_in(node: ast.AST) -> set[str]:
    found: set[str] = set()
    for s in _iter_strings(node):
        if len(s) < 6:
            continue
        for m in _TABLE_RE.finditer(s):
            name = m.group(1).lower()
            if name not in _TABLE_STOPWORDS:
                found.add(name)
    return found


def _has_set_local(node: ast.AST) -> bool:
    strings = list(_iter_strings(node))
    if any(_SET_LOCAL_RE.search(s) or _SET_CONFIG_LOCAL_RE.search(s) for s in strings):
        return True
    # core.db.get_read_db establishes a read-only transaction and delegates
    # its transaction-local SETs to this private helper. Keep this deliberately
    # narrow: apply_tenant_rls_context historically used session scope and is
    # not accepted as proof here.
    return any(
        isinstance(sub, ast.Call)
        and (
            (isinstance(sub.func, ast.Name) and sub.func.id == "_apply_rls_context")
            or (isinstance(sub.func, ast.Attribute) and sub.func.attr == "_apply_rls_context")
        )
        for sub in ast.walk(node)
    )


def _tenant_source(func: Optional[ast.AST]) -> Optional[str]:
    """Best-effort: is a tenant id already in scope in this function?

    Ranked so the report names the most actionable source: a parameter first
    (nothing to plumb), then a local binding, then an attribute read.
    """
    if func is None:
        return None

    if isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
        args = func.args
        for a in (
            list(args.posonlyargs)
            + list(args.args)
            + list(args.kwonlyargs)
            + ([args.vararg] if args.vararg else [])
            + ([args.kwarg] if args.kwarg else [])
        ):
            if a is not None and _TENANT_NAME_RE.match(a.arg):
                return f"param:{a.arg}"

    local: Optional[str] = None
    attr: Optional[str] = None
    for sub in ast.walk(func):
        if isinstance(sub, ast.Name):
            if _TENANT_NAME_RE.match(sub.id):
                if isinstance(sub.ctx, ast.Store):
                    local = local or f"local:{sub.id}"
                else:
                    local = local or f"name:{sub.id}"
        elif isinstance(sub, ast.Attribute):
            if _TENANT_NAME_RE.match(sub.attr):
                dotted = _dotted(sub)
                attr = attr or f"attr:{dotted or sub.attr}"
    return local or attr


class _Collector(ast.NodeVisitor):
    """Walk one module, recording every pooled acquisition with its context."""

    def __init__(self, relpath: str) -> None:
        self.relpath = relpath
        self.stack: list[str] = []
        self.func_stack: list[ast.AST] = []
        self.hits: list[dict] = []

    # -- scope tracking -------------------------------------------------
    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def _visit_func(self, node: ast.AST) -> None:
        self.stack.append(node.name)  # type: ignore[attr-defined]
        self.func_stack.append(node)
        self.generic_visit(node)
        self.func_stack.pop()
        self.stack.pop()

    visit_FunctionDef = _visit_func  # type: ignore[assignment]
    visit_AsyncFunctionDef = _visit_func  # type: ignore[assignment]

    # -- the thing we are looking for -----------------------------------
    def visit_Call(self, node: ast.Call) -> None:
        record = self._classify_call(node)
        if record is not None:
            self.hits.append(record)
        self.generic_visit(node)

    def _classify_call(self, node: ast.Call) -> Optional[dict]:
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)

        form: Optional[str] = None
        receiver: Optional[str] = None
        tenant_arg: Optional[str] = None

        if name == "acquire_with_tenant":
            receiver = _dotted(func)
            tenant_arg = self._second_arg(node)
            form = (
                "acquire_with_tenant(None)"
                if tenant_arg in ("None", None)
                else "acquire_with_tenant"
            )
        elif name == "acquire" and isinstance(func, ast.Attribute):
            receiver = _dotted(func.value)
            if not _is_pool_receiver(receiver):
                return None
            form = "bare_acquire"
        else:
            return None

        enclosing = self.func_stack[-1] if self.func_stack else None
        qualname = ".".join(self.stack) if self.stack else "<module>"

        scope = enclosing if enclosing is not None else None
        tables = sorted(_tables_in(scope)) if scope is not None else []
        set_local = _has_set_local(scope) if scope is not None else False
        tenant_src = _tenant_source(scope)

        return {
            "file": self.relpath,
            "line": node.lineno,
            "function": qualname,
            "form": form,
            "receiver": receiver,
            "tenant_arg": tenant_arg,
            "explicit_set_local": set_local,
            "tables": tables,
            "tenant_source": tenant_src,
        }

    @staticmethod
    def _second_arg(node: ast.Call) -> Optional[str]:
        if len(node.args) >= 2:
            return ast.unparse(node.args[1])
        for kw in node.keywords:
            if kw.arg == "tenant_id":
                return ast.unparse(kw.value)
        return None


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------
# The helper itself must call the bare pool.acquire() — that is its whole job.
_HELPER_SITES = {("app/core/db_utils.py", "acquire_with_tenant")}


def score(rec: dict, rls_tables: set[str], tenant_scoped: set[str]) -> dict:
    rls_hits = sorted(t for t in rec["tables"] if t in rls_tables)
    scoped_hits = sorted(t for t in rec["tables"] if t in tenant_scoped and t not in rls_tables)
    rec["rls_tables_touched"] = rls_hits
    rec["tenant_scoped_tables_touched"] = scoped_hits
    rec["rls_protected"] = bool(rls_hits)

    if (rec["file"], rec["function"]) in _HELPER_SITES:
        rec["form"] = "helper_definition"
        rec["verdict"] = "ok"
        rec["reason"] = "acquire_with_tenant's own implementation; it sets the GUC here"
        return rec

    if rec["form"] == "acquire_with_tenant":
        rec["verdict"] = "ok"
        rec["reason"] = f"tenant-scoped via acquire_with_tenant({rec['tenant_arg']})"
        return rec

    if rec["form"] == "acquire_with_tenant(None)":
        if rec["tenant_source"]:
            rec["verdict"] = "needs_review"
            rec["reason"] = (
                "platform bypass, but a tenant id is in scope "
                f"({rec['tenant_source']}) - confirm this is deliberate"
            )
        else:
            rec["verdict"] = "ok"
            rec["reason"] = "platform bypass with no tenant in scope"
        return rec

    # bare_acquire from here down
    if rec["explicit_set_local"]:
        rec["verdict"] = "ok"
        rec["reason"] = "function sets an explicit SET LOCAL app.* context"
        return rec

    if rls_hits:
        if rec["tenant_source"]:
            rec["verdict"] = "needs_tenant"
            rec["reason"] = (
                f"bare acquire on RLS table(s) {', '.join(rls_hits)}; "
                f"tenant available in scope ({rec['tenant_source']}) - "
                "wrap in acquire_with_tenant(pool, <tenant>)"
            )
        else:
            rec["verdict"] = "needs_review"
            rec["reason"] = (
                f"bare acquire on RLS table(s) {', '.join(rls_hits)} with no tenant "
                "in scope - plumb a tenant id, or acquire_with_tenant(pool, None) "
                "if this is genuinely platform work"
            )
        return rec

    if scoped_hits:
        rec["verdict"] = "needs_review"
        rec["reason"] = (
            f"bare acquire on tenant-scoped table(s) {', '.join(scoped_hits)} "
            "with no policy found in this repo's schema - verify against the live DB"
        )
        return rec

    if not rec["tables"]:
        rec["verdict"] = "needs_review"
        rec["reason"] = "bare acquire; no SQL literal in this function to parse"
        return rec

    rec["verdict"] = "ok"
    rec["reason"] = "bare acquire, but no RLS-protected table parsed in this function"
    return rec


# --------------------------------------------------------------------------
# Driver
# --------------------------------------------------------------------------
_VERDICT_ORDER = {"needs_tenant": 0, "needs_review": 1, "ok": 2}


def collect(app_root: Path, backend_root: Path) -> list[dict]:
    records: list[dict] = []
    for path in sorted(app_root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        source = _read(path)
        if "acquire" not in source:
            continue
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError as exc:  # a file mid-edit shouldn't kill the report
            print(f"skip (SyntaxError): {path} line {exc.lineno}", file=sys.stderr)
            continue
        rel = path.relative_to(backend_root).as_posix()
        collector = _Collector(rel)
        collector.visit(tree)
        records.extend(collector.hits)
    return records


def _truncate(text: str, width: int) -> str:
    # ASCII only: this gets read on a Windows console with a cp1252 codepage.
    return text if len(text) <= width else text[: width - 3] + "..."


def print_human(records: list[dict], rls_tables: set[str], summary: dict) -> None:
    print("pooled DB acquisitions in backend/app - worst first")
    print(
        f"(RLS-protected tables known to this repo: {len(rls_tables)}; "
        "an unset tenant GUC returns zero rows since 2026-08-30)"
    )
    print()
    header = (
        f"{'VERDICT':<13} {'FORM':<24} {'FILE:LINE':<58} "
        f"{'FUNCTION':<40} {'TENANT SOURCE':<26} RLS TABLES"
    )
    print(header)
    print("-" * len(header))
    for rec in records:
        loc = f"{rec['file']}:{rec['line']}"
        print(
            f"{rec['verdict']:<13} "
            f"{_truncate(rec['form'], 24):<24} "
            f"{_truncate(loc, 58):<58} "
            f"{_truncate(rec['function'], 40):<40} "
            f"{_truncate(rec['tenant_source'] or '-', 26):<26} "
            f"{_truncate(', '.join(rec['rls_tables_touched']) or '-', 60)}"
        )
    print()
    print("summary:")
    for key in ("total", "ok", "needs_tenant", "needs_review"):
        print(f"  {key:<14} {summary[key]}")
    print("  by form:")
    for form, count in sorted(summary["by_form"].items(), key=lambda kv: -kv[1]):
        print(f"    {form:<26} {count}")


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Inventory every pooled DB acquisition in backend/app and report "
            "whether it establishes an RLS tenant context."
        ),
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="backend/ directory (default: the parent of this script's directory)",
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument(
        "--only",
        choices=("ok", "needs_tenant", "needs_review"),
        help="show only this verdict (the summary still counts everything)",
    )
    parser.add_argument(
        "--fail-on-needs-tenant",
        action="store_true",
        help=(
            "CI gate: exit 1 if any needs_tenant site remains. This is also the "
            "default exit behaviour; the flag states the intent explicitly."
        ),
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    backend_root = (args.root or Path(__file__).resolve().parent.parent).resolve()
    app_root = backend_root / "app"
    if not app_root.is_dir():
        print(f"no app/ directory under {backend_root}", file=sys.stderr)
        return 2

    rls_tables, tenant_scoped = discover_rls_tables(backend_root)
    records = [score(rec, rls_tables, tenant_scoped) for rec in collect(app_root, backend_root)]
    records.sort(key=lambda r: (_VERDICT_ORDER[r["verdict"]], r["file"], r["line"]))

    by_form: dict[str, int] = {}
    for rec in records:
        by_form[rec["form"]] = by_form.get(rec["form"], 0) + 1
    summary = {
        "total": len(records),
        "ok": sum(1 for r in records if r["verdict"] == "ok"),
        "needs_tenant": sum(1 for r in records if r["verdict"] == "needs_tenant"),
        "needs_review": sum(1 for r in records if r["verdict"] == "needs_review"),
        "by_form": by_form,
    }

    shown = [r for r in records if not args.only or r["verdict"] == args.only]

    if args.json:
        json.dump(
            {
                "why": (
                    "the app role lost BYPASSRLS on 2026-08-30; a pooled "
                    "connection with an unset tenant GUC now matches zero rows "
                    "and reports success"
                ),
                "backend_root": backend_root.as_posix(),
                "rls_protected_tables": sorted(rls_tables),
                "tenant_scoped_tables_without_policy": sorted(tenant_scoped - rls_tables),
                "summary": summary,
                "acquisitions": shown,
            },
            sys.stdout,
            indent=2,
        )
        sys.stdout.write("\n")
    else:
        print_human(shown, rls_tables, summary)

    # Exit 0 when clean, 1 when any needs_tenant remains.
    failing = summary["needs_tenant"]
    if args.fail_on_needs_tenant:
        # Make the gate legible in a CI log, where only the tail is ever read.
        stream = sys.stderr if args.json else sys.stdout
        print(
            f"GATE {'FAIL' if failing else 'PASS'}: {failing} needs_tenant site(s) "
            "- a bare pool.acquire() on an RLS-protected table with a tenant id "
            "already in scope returns zero rows in production.",
            file=stream,
        )
    return 1 if failing else 0


if __name__ == "__main__":
    raise SystemExit(main())
