"""A client-supplied `tenant_id` must never widen a caller's scope.

WHY THIS EXISTS (2026-07-31)
----------------------------
Two endpoints resolved their tenant scope as:

    scoped_tenant_id = tenant_id or current_user.get("tenant_id")

The query parameter WINS. Any authenticated caller holding the relevant
tenant-scoped permission could read another tenant's data by passing
`?tenant_id=<victim-uuid>`:

    GET /admin/security-events/events   evidence blobs, IPs, investigation
                                        notes on abuse/fraud cases
    GET /audit-logs/stats/events-by-type  audit event distribution

Production runs Postgres under a BYPASSRLS role, so the application-level
predicate is the ONLY tenant isolation that exists — there is no second line
of defence behind these.

Both files already had the CORRECT pattern elsewhere (`GET /events/{id}` and
`GET /logs` both validate-then-403), so this was drift, not design.

These tests are source-level rather than HTTP-level on purpose: the defect is
a boolean-expression shape, and asserting on the shape catches a reintroduction
even in a route that has no test client wired up.
"""
from __future__ import annotations

from pathlib import Path

import pytest

_ENDPOINTS = Path(__file__).resolve().parents[2] / "app" / "api" / "v1" / "endpoints"

# The exact defect: caller-supplied value on the LEFT of an `or`, so it wins.
_DEFECT = 'tenant_id or current_user.get("tenant_id")'

def _code_only(path: Path) -> str:
    """Source with comment lines and docstring bodies removed.

    The first version of this test matched its own explanatory comment (which
    quotes the defect verbatim) and failed on a file that was already fixed —
    a test that fires on its own documentation is worse than no test, so strip
    prose and assert only on executable code.
    """
    import ast, io, tokenize

    src = path.read_text(encoding="utf-8")
    out = []
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type in (tokenize.COMMENT, tokenize.STRING):
            continue
        out.append(tok.string)
    return " ".join(out)




@pytest.mark.parametrize(
    "relpath",
    ["security_events.py", "audit_logs.py"],
)
def test_client_tenant_id_never_wins(relpath):
    code = _code_only(_ENDPOINTS / relpath)
    # Token-joined, so match on the operative shape rather than spacing.
    assert "or current_user . get" not in code.replace("  ", " "), (
        f"{relpath} resolves tenant scope as `{_DEFECT}` — the CLIENT-supplied "
        "tenant_id overrides the caller's own, which is a cross-tenant read. "
        "Validate the param and 403 on mismatch instead of OR-ing it."
    )


@pytest.mark.parametrize(
    "relpath",
    ["security_events.py", "audit_logs.py"],
)
def test_mismatched_tenant_param_is_rejected(relpath):
    """The param may narrow to the caller's own tenant, never widen."""
    src = (_ENDPOINTS / relpath).read_text(encoding="utf-8")
    assert "Cannot access other tenant" in src, (
        f"{relpath} must 403 when a caller passes a tenant_id that is not "
        "their own"
    )


def test_failed_login_stats_is_tenant_scoped_and_uses_a_real_column():
    """Two bugs in one query.

    `login_attempts` is (id, email, user_id, ip_address, user_agent, success,
    failure_reason, created_at) — verified against the live database. The
    endpoint filtered on `attempted_at`, which does not exist, so it returned
    500 on every call; and it had no tenant predicate at all, so had it worked
    it would have reported platform-wide failed-login counts to every tenant.
    """
    src = (_ENDPOINTS / "audit_logs.py").read_text(encoding="utf-8")
    code = _code_only(_ENDPOINTS / "audit_logs.py")
    stats = src.split("/stats/failed-logins")[1].split("@router")[0]

    assert "attempted_at" not in code, (
        "login_attempts has no `attempted_at` column — the query 500s"
    )
    assert "la.created_at" in stats, "must filter on the real column"
    assert "user_profiles" in stats and "tenant_id" in stats, (
        "failed-login stats must be scoped to the caller's tenant; "
        "login_attempts has no tenant_id, so it joins user_profiles"
    )


def test_the_guard_is_not_vacuous():
    """Proves the defect string would actually be caught if reintroduced."""
    reintroduced = 'scoped_tenant_id = tenant_id or current_user.get("tenant_id")'
    assert _DEFECT in reintroduced
