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
