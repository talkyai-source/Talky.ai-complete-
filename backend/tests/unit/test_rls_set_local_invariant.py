"""
Lock the RLS session-variable invariant permanently.

Postgres RLS here is driven by the GUCs ``app.current_tenant_id`` and
``app.bypass_rls``. There are two ways to set them and only one is safe:

    SET app.bypass_rls = 'on'                      -- SESSION scope. Unsafe.
    SELECT set_config('app.current_tenant_id',$1,false)  -- also session. Unsafe.

    SET LOCAL app.bypass_rls = 'on'                -- transaction scope. Safe.
    SELECT set_config('app.current_tenant_id',$1,true)   -- transaction scope. Safe.

Why session scope is unsafe here:

  * Under **PgBouncer transaction pooling** (the intended production topology --
    see ``infra/pgbouncer/pgbouncer.ini``) a bare ``SET`` runs in its own implicit
    transaction, lands on whichever server connection PgBouncer picks for it, and
    is then released. The *next* statement may be routed to a different server
    connection entirely -- so the GUC both fails to apply where it was meant to
    and pollutes a connection that other tenants will use.
  * asyncpg's own pool issues ``RESET ALL`` on release (``pool.py`` ->
    ``Connection.reset()``), so on the *native* pool session GUCs do not leak
    between borrowers. That is why this is currently latent rather than live --
    and precisely why it needs a test: the protection comes from a library
    implementation detail, not from this codebase, and it disappears the moment
    PgBouncer is introduced.

The canonical safe helper is ``app.core.db_utils.acquire_with_tenant``.

This test is modelled on ``test_no_domain_api_imports.py``: parse each file with
``ast`` rather than grepping raw text, so comments and docstrings cannot produce
false positives and reformatting cannot defeat the check.

See ``docs/v2/rls-set-audit.md`` (ticket TKT-009) for the full inventory.
"""

from __future__ import annotations

import ast
import re
import runpy
from pathlib import Path

import pytest

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_APP_ROOT = _BACKEND_ROOT / "app"

# A bare `SET app.<guc>` -- session scope. `SET LOCAL app.<guc>` is fine.
_SESSION_SET = re.compile(r"\bSET\s+(?!LOCAL\b)app\.", re.IGNORECASE)

# set_config('app.<guc>', <value>, false) -- the third argument is is_local.
_SESSION_SET_CONFIG = re.compile(
    r"set_config\s*\(\s*['\"]app\.[^'\"]+['\"]\s*,[^,]*,\s*false\s*\)",
    re.IGNORECASE,
)

# Known-unsafe sites, tracked as F-24 in docs/v2/09-known-issues.md.
#
# These are NOT approved. They are recorded here so the invariant can be enforced
# for all new code while the existing debt is paid down deliberately -- 42 call
# sites across 12 files, with real behaviour change, is not a drive-by edit.
#
# Remove an entry the moment its file is fixed. `test_allowlist_has_no_stale_entries`
# will fail if an entry stops being needed, so this list cannot silently rot into
# permanent permission.
_KNOWN_UNSAFE: dict[str, str] = {}


def _python_files() -> list[Path]:
    return sorted(p for p in _APP_ROOT.rglob("*.py") if p.is_file())


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """Ids of Constant nodes that are docstrings, so prose can't trip the check."""
    found: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if not body:
                continue
            first = body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                if isinstance(first.value.value, str):
                    found.add(id(first.value))
    return found


def _session_scoped_rls_statements(source: str) -> list[str]:
    """Return every SQL string literal in `source` that sets an RLS GUC at session scope."""
    tree = ast.parse(source)
    skip = _docstring_nodes(tree)
    hits: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if id(node) in skip:
            continue
        text = node.value
        if _SESSION_SET.search(text) or _SESSION_SET_CONFIG.search(text):
            hits.append(" ".join(text.split())[:120])
    return hits


@pytest.mark.parametrize(
    "path",
    _python_files(),
    ids=lambda p: str(p.relative_to(_APP_ROOT)).replace("\\", "/"),
)
def test_no_session_scoped_rls_set(path: Path) -> None:
    rel = str(path.relative_to(_APP_ROOT)).replace("\\", "/")
    hits = _session_scoped_rls_statements(path.read_text(encoding="utf-8"))

    if rel in _KNOWN_UNSAFE:
        assert hits, (
            f"{rel} is in the known-unsafe allowlist but no session-scoped RLS "
            f"statement was found. If it has been fixed, remove it from "
            f"_KNOWN_UNSAFE — see docs/v2/rls-set-audit.md."
        )
        return

    assert not hits, (
        f"{rel} sets an RLS session variable at SESSION scope: {hits}\n\n"
        f"Use `SET LOCAL` inside an explicit `async with conn.transaction():`, or "
        f"the canonical helper `app.core.db_utils.acquire_with_tenant(pool, tenant_id)`.\n"
        f"Session scope survives the statement and, under PgBouncer transaction "
        f"pooling, reaches other tenants' connections.\n"
        f"Background: docs/v2/rls-set-audit.md"
    )


def test_allowlist_has_no_stale_entries() -> None:
    """An allowlist entry that is no longer needed must be removed, not left to rot."""
    stale = []
    for rel in _KNOWN_UNSAFE:
        path = _APP_ROOT / rel
        if not path.exists():
            stale.append(f"{rel} (file no longer exists)")
            continue
        if not _session_scoped_rls_statements(path.read_text(encoding="utf-8")):
            stale.append(f"{rel} (fixed — delete this entry)")
    assert not stale, (
        "Stale _KNOWN_UNSAFE entries in test_rls_set_local_invariant.py: "
        f"{stale}. Remove them so the allowlist keeps meaning what it says."
    )


def test_canonical_helper_is_transaction_scoped() -> None:
    """The helper everything should migrate to must itself stay correct."""
    src = (_APP_ROOT / "core" / "db_utils.py").read_text(encoding="utf-8")
    assert "conn.transaction()" in src, (
        "acquire_with_tenant must open an explicit transaction — that is the whole "
        "reason it is the canonical helper."
    )
    assert not _session_scoped_rls_statements(
        src
    ), "acquire_with_tenant must not use session-scoped RLS statements."


# ---------------------------------------------------------------------------
# Invariant 2 (2026-08-30): a pooled connection with NO tenant context at all.
#
# Tonight production's app role lost its escape hatch:
#
#     ALTER ROLE talkyai NOSUPERUSER NOBYPASSRLS;
#
# (required — ``core/inbound_startup.py`` refuses to boot production inbound on
# a superuser role). Migration 0013 had already installed FORCE ROW LEVEL
# SECURITY plus one canonical policy per tenant-scoped table:
#
#     COALESCE(NULLIF(current_setting('app.bypass_rls', TRUE),'')::boolean, FALSE)
#     OR tenant_id = NULLIF(current_setting('app.current_tenant_id', TRUE),'')::uuid
#     [OR tenant_id IS NULL]
#
# ``current_setting(..., TRUE)`` yields NULL when the GUC was never set, so an
# UNSET GUC makes the whole expression FALSE. A ``pool.acquire()`` that sets
# neither GUC therefore matches ZERO ROWS — and reports success. The same
# expression is the WITH CHECK, so writes are silently rejected too. Proven on
# production 2026-08-30:
#
#     SELECT count(*) FROM calls  ->     0   with no GUC
#                                 ->  1041   with SET app.bypass_rls='true'
#
# This is a DIFFERENT failure from invariant 1 above. Invariant 1 is about
# setting the GUC at the WRONG SCOPE; this one is about not setting it AT ALL.
# A site that sets a GUC at session scope is therefore *not* reported here — it
# is already owned by ``test_no_session_scoped_rls_set``, and reporting it in
# both places would only make both allowlists lie.
#
# Scope discipline: a blanket ban on ``.acquire()`` would flag every Redis-ish
# and lock-ish acquisition in the tree and be deleted within a week. So a site
# is only reported when the enclosing function actually names an RLS-protected
# table in a SQL string it contains.
# ---------------------------------------------------------------------------

# Tables carrying the canonical 0013 policy.
#
# PROVENANCE: 0013 does NOT enumerate them — it discovers them dynamically
# ("every public table that has RLS enabled *and* a tenant_id column", see the
# DO block in Alembic/versions/0013_canonical_rls_policies.py), so there is no
# list in the migration to import. This constant is generated from the schema
# inventory and deliberately covers every RLS-protected table known to this
# repository. Adding a new policy/table requires adding it here in the same
# change; the inventory script independently discovers the schema set and its
# test below prevents drift.
_RLS_TABLES = (
    "abuse_detection_rules",
    "abuse_events",
    "action_plans",
    "admin_media_deletion_intents",
    "admin_media_deletion_request_keys",
    "ai_config_migrations",
    "assistant_actions",
    "assistant_conversations",
    "audit_logs",
    "billing_ledger",
    "call_events",
    "call_feedback",
    "call_guard_decisions",
    "call_lead_details",
    "call_legs",
    "call_velocity_snapshots",
    "calls",
    "campaign_knowledge_nodes",
    "campaign_knowledge_sources",
    "campaign_lead_fields",
    "campaigns",
    "clients",
    "cloned_voices",
    "connector_accounts",
    "connectors",
    "contact_lists",
    "conversation_reviews",
    "conversations",
    "dialer_jobs",
    "dnc_entries",
    "inbound_audit_events",
    "inbound_billing_hold_finalize_approvals",
    "inbound_campaign_configs",
    "inbound_did_assignments",
    "inbound_operation_idempotency",
    "inbound_reassignment_requests",
    "inbound_rejections",
    "inbound_usage_transactions",
    "invoices",
    "leads",
    "meetings",
    "recordings",
    "recordings_s3",
    "refresh_tokens",
    "reminders",
    "review_reward_ledger",
    "secret_access_log",
    "security_events",
    "stream_events",
    "subscriptions",
    "tenant_ai_configs",
    "tenant_ai_credentials",
    "tenant_call_limits",
    "tenant_codec_policies",
    "tenant_inbound_controls",
    "tenant_phone_numbers",
    "tenant_policy_audit_log",
    "tenant_provider_cost_events",
    "tenant_quota_usage",
    "tenant_quotas",
    "tenant_recording_policy",
    "tenant_route_policies",
    "tenant_runtime_policy_events",
    "tenant_runtime_policy_versions",
    "tenant_secrets",
    "tenant_settings",
    "tenant_sip_trunks",
    "tenant_sip_trust_policies",
    "tenant_telephony_concurrency_events",
    "tenant_telephony_concurrency_leases",
    "tenant_telephony_concurrency_policies",
    "tenant_telephony_credentials",
    "tenant_telephony_idempotency",
    "tenant_telephony_quota_events",
    "tenant_telephony_threshold_policies",
    "tenant_users",
    "topup_orders",
    "transcripts",
    "usage_records",
    "user_permissions",
    "user_profiles",
    "webhook_configs",
    "webhook_deliveries",
    "webhook_endpoints",
    "white_label_partners",
)


def test_rls_table_guard_matches_schema_inventory() -> None:
    """A migration cannot add protected surface without extending this guard."""
    inventory = runpy.run_path(str(_BACKEND_ROOT / "scripts" / "rls_acquire_inventory.py"))
    discovered, _tenant_scoped = inventory["discover_rls_tables"](_BACKEND_ROOT)
    assert tuple(sorted(set(_RLS_TABLES))) == _RLS_TABLES, (
        "_RLS_TABLES must remain sorted and duplicate-free so stale entries are "
        "reviewable instead of hidden in a generated-looking list"
    )
    assert set(_RLS_TABLES) == discovered, (
        "RLS table guard drifted from the schema inventory. "
        f"missing={sorted(discovered - set(_RLS_TABLES))}, "
        f"stale={sorted(set(_RLS_TABLES) - discovered)}"
    )


# A table named in a real SQL position, not in prose. `calls` is an ordinary
# English word ("the dialer calls the guard"), so an unanchored word match
# reports half the codebase.
_SQL_TABLE_REF = re.compile(
    r"\b(?:FROM|JOIN|INTO|UPDATE|TABLE)\s+(?:ONLY\s+)?(?:public\.)?"
    r"(" + "|".join(_RLS_TABLES) + r")\b",
    re.IGNORECASE,
)

# Helpers that keep an RLS context alive for the complete connection use. A
# transaction-local SET performed before a bare acquire cannot count: its
# statement transaction ends before the protected query runs.
_CONTEXT_HELPERS = (
    "acquire_with_tenant",  # core/db_utils.py — the canonical helper
    "set_tenant_context_in_db",  # core/security/tenant_isolation.py
    "tenant_context(",  # core/security/tenant_isolation.py
    "_apply_rls_context",  # core/db.py
)

# Any scope: this test only asks whether a context is established at all.
_ANY_GUC_SET = re.compile(
    r"\bSET\s+(?:LOCAL\s+)?app\.(?:current_tenant_id|bypass_rls)\b", re.IGNORECASE
)
_ANY_SET_CONFIG = re.compile(r"set_config\s*\(\s*['\"]app\.", re.IGNORECASE)


# There is intentionally no grandfather list. A new bare pooled acquisition on
# a protected table fails CI in the same change that introduces it.
_KNOWN_NO_GUC: dict[str, str] = {}


def _acquisitions_without_tenant_context(path: Path) -> list[tuple[str, int, list[str]]]:
    """Report ``.acquire()`` sites in `path` that establish no RLS context.

    Returns ``(qualified_function, lineno, tables)`` for every
    ``async with <expr>.acquire(...)`` where:

      * the enclosing function -- or any function enclosing *that* one, since a
        closure inherits the context its owner opened -- neither calls a helper
        from ``_CONTEXT_HELPERS`` nor emits a ``SET``/``set_config`` for an
        ``app.*`` GUC, and
      * the innermost enclosing function names an RLS-protected table in a SQL
        string literal.
    """
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    findings: list[tuple[str, int, list[str]]] = []

    def string_literals(node: ast.AST) -> str:
        return "\n".join(
            n.value
            for n in ast.walk(node)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
        )

    def establishes_context(fn: ast.AST) -> bool:
        segment = ast.get_source_segment(source, fn) or ""
        if any(helper in segment for helper in _CONTEXT_HELPERS):
            return True
        literals = string_literals(fn)
        return bool(_ANY_GUC_SET.search(literals) or _ANY_SET_CONFIG.search(literals))

    def walk(node: ast.AST, names: list[str], functions: list[ast.AST]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                walk(child, names + [child.name], functions + [child])
                continue
            if isinstance(child, ast.ClassDef):
                walk(child, names + [child.name], functions)
                continue
            if isinstance(child, ast.AsyncWith) and functions:
                for item in child.items:
                    call = item.context_expr
                    if (
                        isinstance(call, ast.Call)
                        and isinstance(call.func, ast.Attribute)
                        and call.func.attr == "acquire"
                    ):
                        if any(establishes_context(fn) for fn in functions):
                            continue
                        tables = sorted(
                            {
                                m.lower()
                                for m in _SQL_TABLE_REF.findall(string_literals(functions[-1]))
                            }
                        )
                        if tables:
                            findings.append((".".join(names), child.lineno, tables))
            walk(child, names, functions)

    walk(tree, [], [])
    return findings


@pytest.mark.parametrize(
    "path",
    _python_files(),
    ids=lambda p: str(p.relative_to(_APP_ROOT)).replace("\\", "/"),
)
def test_pooled_acquire_establishes_tenant_context(path: Path) -> None:
    rel = str(path.relative_to(_APP_ROOT)).replace("\\", "/")
    offenders = [
        (fn, lineno, tables)
        for fn, lineno, tables in _acquisitions_without_tenant_context(path)
        if f"{rel}::{fn}" not in _KNOWN_NO_GUC
    ]

    assert not offenders, "\n".join(
        [
            "Pooled connection acquired with NO RLS tenant context:",
            *(
                f"  {rel}:{lineno}  {fn}()  queries: {', '.join(tables)}"
                for fn, lineno, tables in offenders
            ),
            "",
            "Production's app role has been NOSUPERUSER NOBYPASSRLS since 2026-08-30,",
            "and migration 0013 FORCEs a policy that evaluates FALSE when the GUC is",
            "unset. This connection therefore reads ZERO ROWS from those tables and",
            "reports success; writes are rejected by the same expression as WITH CHECK.",
            "",
            "Use the canonical helper (app/core/db_utils.py):",
            "    from app.core.db_utils import acquire_with_tenant",
            "    async with acquire_with_tenant(pool, tenant_id) as conn:   # tenant-scoped",
            "    async with acquire_with_tenant(pool, None) as conn:        # platform-wide",
            "",
            "Pass None ONLY where the tenant genuinely is not known yet, with a comment",
            "saying why that site is a platform responsibility — never just to avoid",
            "plumbing the tenant id through.",
        ]
    )


def test_no_guc_allowlist_has_no_stale_entries() -> None:
    """An entry whose site no longer offends must be deleted, not left to rot."""
    stale: list[str] = []
    for key in _KNOWN_NO_GUC:
        rel, _, qualname = key.partition("::")
        path = _APP_ROOT / rel
        if not path.exists():
            stale.append(f"{key} (file no longer exists)")
            continue
        offending = {fn for fn, _lineno, _tables in _acquisitions_without_tenant_context(path)}
        if qualname not in offending:
            stale.append(f"{key} (fixed — delete this entry)")
    assert not stale, (
        "Stale _KNOWN_NO_GUC entries in test_rls_set_local_invariant.py: "
        f"{stale}. Remove them so the allowlist keeps meaning what it says."
    )
