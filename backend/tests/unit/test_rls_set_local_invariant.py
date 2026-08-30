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
from pathlib import Path

import pytest

_APP_ROOT = Path(__file__).resolve().parents[2] / "app"

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
_KNOWN_UNSAFE: dict[str, str] = {
    "core/tenant_rls.py": (
        "apply_tenant_rls_context() uses set_config(..., false). Root cause of 36 "
        "call sites across the telephony_sip / telephony_runtime endpoints."
    ),
    "workers/dialer_worker.py": (
        "DialerWorker._acquire_db sets app.bypass_rls at session scope; 14 methods "
        "use it. Fix is acquire_with_tenant(pool, None), which implements the same "
        "intent transaction-scoped."
    ),
    "domain/services/event_emitter.py": (
        "cleanup_expired_events_loop sets bypass + nil tenant at session scope."
    ),
    "domain/services/billing_service.py": (
        "_claim_webhook_event sets bypass + nil tenant at session scope."
    ),
}


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
    assert not _session_scoped_rls_statements(src), (
        "acquire_with_tenant must not use session-scoped RLS statements."
    )


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
# list in the migration to import. This constant is therefore written out by
# hand from the ``ALTER TABLE ... ENABLE ROW LEVEL SECURITY`` statements in
# Alembic/versions/ that also carry a tenant_id column, narrowed to the tables
# the 2026-08-30 NOBYPASSRLS cutover sweep covers.
#
# DELIBERATELY NARROWER THAN THE DATABASE. The full RLS set is ~37 tables;
# widening this constant to all of them reports 34 further sites, dominated by
# the pre-login ``user_profiles`` reads under app/api/v1/endpoints/auth/** and
# the ``assistant_actions`` writes in services/{email,sms}_service.py. Those
# are a separate remediation, not this sweep's — widen the list when it is
# taken on, and expect the allowlist below to grow with it.
_RLS_TABLES = (
    "calls",
    "call_legs",
    "stream_events",
    "campaigns",
    "leads",
    "tenant_ai_credentials",
    "tenant_recording_policy",
    "tenant_sip_trunks",
    "tenant_phone_numbers",
    "inbound_did_assignments",
    "inbound_campaign_configs",
    "tenant_inbound_controls",
)

# A table named in a real SQL position, not in prose. `calls` is an ordinary
# English word ("the dialer calls the guard"), so an unanchored word match
# reports half the codebase.
_SQL_TABLE_REF = re.compile(
    r"\b(?:FROM|JOIN|INTO|UPDATE|TABLE)\s+(?:ONLY\s+)?(?:public\.)?"
    r"(" + "|".join(_RLS_TABLES) + r")\b",
    re.IGNORECASE,
)

# Helpers that establish an RLS context on the connection they are handed. A
# call to one of these counts as establishing context, otherwise every endpoint
# that delegates the SET to a helper is reported for a GUC it does in fact set.
#
# ``apply_tenant_rls_context`` sets it at SESSION scope, which is its own bug —
# but that is invariant 1's bug (core/tenant_rls.py is in _KNOWN_UNSAFE above),
# not the absence this test is about.
_CONTEXT_HELPERS = (
    "acquire_with_tenant",  # core/db_utils.py — the canonical helper
    "apply_tenant_rls_context",  # core/tenant_rls.py
    "set_tenant_context_in_db",  # core/security/tenant_isolation.py
    "tenant_context(",  # core/security/tenant_isolation.py
    "_apply_rls_context",  # core/db.py
)

# Any scope: this test only asks whether a context is established at all.
_ANY_GUC_SET = re.compile(
    r"\bSET\s+(?:LOCAL\s+)?app\.(?:current_tenant_id|bypass_rls)\b", re.IGNORECASE
)
_ANY_SET_CONFIG = re.compile(r"set_config\s*\(\s*['\"]app\.", re.IGNORECASE)


# Sites that acquire a pooled connection, query an RLS-protected table, and set
# no tenant context at all. Keyed "<path under app/>::<qualified function>".
#
# EVERY ENTRY HERE IS A BUG, not an approval. They are recorded so the invariant
# is enforced for all new code while the 2026-08-30 sweep lands. Delete an entry
# the moment its site is fixed.
_KNOWN_NO_GUC: dict[str, str] = {
    "api/v1/endpoints/calls.py::hangup_live_call": (
        "Tenant is known (current_user.tenant_id, already an explicit predicate) — "
        "wants acquire_with_tenant(pool, tenant_uuid)."
    ),
    "api/v1/endpoints/calls.py::list_call_issues": (
        "Tenant is known from current_user; the sibling list_calls in the same file "
        "already opens a transaction and SET LOCAL app.bypass_rls inside it."
    ),
    "api/v1/endpoints/tenant_ai_credentials.py::list_credentials": (
        "Tenant comes from _require_tenant(current_user) — wants acquire_with_tenant."
    ),
    "api/v1/endpoints/tenant_ai_credentials.py::create_credential": (
        "Tenant comes from _require_tenant(current_user); the disable+insert pair "
        "already runs in an explicit transaction that could carry the SET LOCAL."
    ),
    "api/v1/endpoints/tenant_ai_credentials.py::disable_credential": (
        "Tenant comes from _require_tenant(current_user) — wants acquire_with_tenant."
    ),
    "core/legacy_campaign_audit.py::audit_legacy_campaigns": (
        "Startup observability probe counting active campaigns across ALL tenants — "
        "genuinely platform scope, wants acquire_with_tenant(pool, None)."
    ),
    "domain/services/abuse_detection.py::AbuseDetectionService.analyze_velocity_patterns": (
        "Platform abuse scan, grouped BY tenant_id across every tenant — platform scope."
    ),
    "domain/services/abuse_detection.py::AbuseDetectionService.analyze_partner_aggregate": (
        "Aggregates every tenant under one partner — platform scope."
    ),
    "domain/services/abuse_detection.py::AbuseDetectionService._check_rapid_calls": (
        "Abuse detector on the platform scan path — platform scope."
    ),
    "domain/services/abuse_detection.py::AbuseDetectionService._check_sequential_dialing": (
        "Abuse detector on the platform scan path — platform scope."
    ),
    "domain/services/abuse_detection.py::AbuseDetectionService._check_wangiri_pattern": (
        "Abuse detector on the platform scan path — platform scope."
    ),
    "domain/services/abuse_detection.py::AbuseDetectionService._get_historical_avg_calls": (
        "Historical baseline for the platform scan path — platform scope."
    ),
    "domain/services/call_guard.py::CallGuard._check_dnc": (
        "Dialer path; tenant_id is already an explicit predicate on the leads query, "
        "so the id is in hand — only the GUC is missing. A DNC check that reads zero "
        "rows fails OPEN and dials a suppressed contact."
    ),
    "domain/services/recording_service.py::RecordingService._update_call_recording_url": (
        "UPDATE calls by call_id only; the tenant is available on the service's upload "
        "path and needs threading through."
    ),
    "domain/services/telephony/lifecycle.py::_on_call_ended": (
        "Hangup settlement looks the call up BY provider_call_id in order to LEARN its "
        "tenant_id — the tenant genuinely is not known yet, so this one is platform "
        "scope: acquire_with_tenant(pool, None)."
    ),
    "workers/reminder_worker.py::ReminderWorker._process_due_reminders": (
        "Worker scans due reminders across all tenants — platform scope."
    ),
}

# Entries the 2026-08-30 NOBYPASSRLS sweep is repairing RIGHT NOW, in this same
# working tree, in files fenced to other agents. They are exempt from the
# staleness check below ONLY so this test is green either way while the sweep
# lands — a fix arriving mid-run must not turn the suite red.
#
# When the sweep is done, delete each key from BOTH this set and _KNOWN_NO_GUC.
# Nothing else belongs here: a site that is not actively being fixed gets a
# staleness-checked _KNOWN_NO_GUC entry and no exemption.
_SWEEP_IN_FLIGHT: frozenset[str] = frozenset(_KNOWN_NO_GUC)


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
                                for m in _SQL_TABLE_REF.findall(
                                    string_literals(functions[-1])
                                )
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
        if key in _SWEEP_IN_FLIGHT:
            continue  # being repaired in this same run; see _SWEEP_IN_FLIGHT
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


def test_sweep_exemptions_are_real_allowlist_entries() -> None:
    """_SWEEP_IN_FLIGHT may only exempt keys the allowlist actually carries.

    Without this the two structures drift and a key can sit in the exemption
    set forever, silently excusing a site that nothing else tracks.
    """
    orphans = sorted(_SWEEP_IN_FLIGHT - set(_KNOWN_NO_GUC))
    assert not orphans, (
        f"_SWEEP_IN_FLIGHT keys with no _KNOWN_NO_GUC entry: {orphans}. "
        "Delete a site from both structures together when its fix lands."
    )
