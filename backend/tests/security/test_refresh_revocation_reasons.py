"""The invariant that was missing: every `refresh_tokens.revoked_reason`
value the application can write MUST be permitted by the CHECK constraint.

Background — this suite exists because the constraint drifted from the code
and nobody noticed until it 500'd in production. `refresh_tokens_revoked_reason_check`
was created inline with the table in Alembic/versions/0002_add_refresh_tokens.py:36-38
and permitted only ('rotated','reuse_detected','logout','admin','expired').
Four code paths grew up writing values outside that set:

  app/api/v1/endpoints/auth/password.py:134         "password_change"
  app/api/v1/endpoints/mfa/status.py:135            "mfa_disabled"
  app/core/security/refresh_tokens.py:149           'expired_with_subsequent_use'
  app/api/v1/endpoints/auth/password_reset.py:147   "password_reset"

Each raises asyncpg.CheckViolationError -> HTTP 500 the moment the UPDATE
matches a row. database/migrations/20260729_widen_refresh_tokens_revoked_reason.sql
widens the constraint; these tests make sure it can never fall behind again.

The tests below do NOT touch a database. They re-derive, from source:

  * the permitted set, by parsing the migration's ADD CONSTRAINT, and
  * the used set, by static analysis of app/ — both the hardcoded SQL
    literals and the `reason=` kwarg at every call site of the two revoke
    helpers (plus those helpers' own parameter defaults),

and assert used ⊆ permitted. Add a new reason in app/ without widening the
migration and this suite goes red.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

# tests/security/test_refresh_revocation_reasons.py -> repo root
_REPO_ROOT = Path(__file__).resolve().parents[2]
_APP_DIR = _REPO_ROOT / "app"
_MIGRATION = (
    _REPO_ROOT
    / "database"
    / "migrations"
    / "20260729_widen_refresh_tokens_revoked_reason.sql"
)

# The helpers whose `reason=` kwarg lands in refresh_tokens.revoked_reason.
# Defined in app/core/security/refresh_tokens.py.
_REVOKE_HELPERS = frozenset(
    {"revoke_all_user_refresh_tokens", "revoke_family_by_token"}
)


# ===========================================================================
# Extraction — the migration's permitted set
# ===========================================================================


def _strip_sql_line_comments(sql: str) -> str:
    """Drop whole-line `--` comments.

    This is what makes the migration's commented-out ROLLBACK section
    invisible to the parser below: every one of its lines is a comment, so
    only the live ADD CONSTRAINT survives.
    """
    return "\n".join(
        line for line in sql.splitlines() if not line.lstrip().startswith("--")
    )


_ADD_CONSTRAINT_RE = re.compile(
    r"ADD\s+CONSTRAINT\s+refresh_tokens_revoked_reason_check\s+"
    r"CHECK\s*\(.*?ARRAY\s*\[(?P<body>.*?)\]",
    re.DOTALL | re.IGNORECASE,
)


def permitted_reasons(sql: str | None = None) -> frozenset[str]:
    """Parse the permitted set out of the migration's live ADD CONSTRAINT.

    Deliberately anchored to the ARRAY[...] of the ADD CONSTRAINT rather
    than "every quoted string in the file" — the COMMENT ON CONSTRAINT at
    the end of the migration also mentions several reason names in prose,
    and must not be mistaken for the constraint itself.
    """
    if sql is None:
        sql = _MIGRATION.read_text(encoding="utf-8")
    match = _ADD_CONSTRAINT_RE.search(_strip_sql_line_comments(sql))
    if match is None:
        raise AssertionError(
            f"could not find a live ADD CONSTRAINT "
            f"refresh_tokens_revoked_reason_check in {_MIGRATION}"
        )
    return frozenset(re.findall(r"'([^']+)'", match.group("body")))


# ===========================================================================
# Extraction — the reasons app/ actually writes
# ===========================================================================

# `UPDATE refresh_tokens ... SET ... revoked_reason = 'literal'`. Scoped to
# the refresh_tokens statement so that literal writes to the unrelated
# connector_accounts.revoked_reason / tenant_secrets.revoked_reason columns
# (different tables, different vocabularies) are not swept in.
_SQL_UPDATE_RE = re.compile(r"UPDATE\s+refresh_tokens\b", re.IGNORECASE)
_SQL_LITERAL_RE = re.compile(r"revoked_reason\s*=\s*'([^']*)'", re.IGNORECASE)


def _sql_literal_reasons(source: str) -> list[tuple[str, int]]:
    """Reasons hardcoded into `UPDATE refresh_tokens` SQL. -> [(reason, line)]"""
    found: list[tuple[str, int]] = []
    for stmt in _SQL_UPDATE_RE.finditer(source):
        # Look ahead only as far as the end of this SQL string literal.
        tail = source[stmt.end() : stmt.end() + 600]
        for lit in _SQL_LITERAL_RE.finditer(tail):
            line = source.count("\n", 0, stmt.end() + lit.start()) + 1
            found.append((lit.group(1), line))
    return found


def _called_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _module_str_constants(tree: ast.Module) -> dict[str, str]:
    """Module-level `NAME = "literal"` bindings.

    Needed because password_reset.py passes its reasons via the constants
    _RESET_REVOKE_REASON / _RESET_REVOKE_REASON_FALLBACK rather than inline.
    """
    consts: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            if isinstance(node.value.value, str):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        consts[target.id] = node.value.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.value, ast.Constant):
            if isinstance(node.value.value, str) and isinstance(node.target, ast.Name):
                consts[node.target.id] = node.value.value
    return consts


def _helper_default_reasons(tree: ast.Module) -> list[tuple[str, int]]:
    """`reason: str = "logout"` defaults on the revoke helpers themselves —
    a call site may omit the kwarg entirely and still write a value."""
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name not in _REVOKE_HELPERS:
            continue
        args = node.args
        pairs = list(zip(args.kwonlyargs, args.kw_defaults))
        pairs += list(
            zip(args.args[len(args.args) - len(args.defaults) :], args.defaults)
        )
        for arg, default in pairs:
            if arg.arg == "reason" and isinstance(default, ast.Constant):
                if isinstance(default.value, str):
                    found.append((default.value, default.lineno))
    return found


def reasons_used_in_app(
    root: Path | None = None,
) -> tuple[dict[str, set[str]], list[str]]:
    """Static-analyse `root` (default: app/) for revoked_reason values.

    Returns ``(reason -> {"file:line", ...}, unresolved)``. ``unresolved``
    lists `reason=` arguments that are not statically a string constant;
    those are reported as failures rather than silently ignored, so a
    dynamic reason cannot slip past this guard.
    """
    root = root or _APP_DIR
    used: dict[str, set[str]] = {}
    unresolved: list[str] = []

    def record(reason: str, path: Path, line: int) -> None:
        rel = path.relative_to(root.parent if root == _APP_DIR else root)
        used.setdefault(reason, set()).add(f"{rel.as_posix()}:{line}")

    for path in sorted(root.rglob("*.py")):
        source = path.read_text(encoding="utf-8", errors="replace")
        if "revoked_reason" not in source and not any(
            h in source for h in _REVOKE_HELPERS
        ):
            continue

        for reason, line in _sql_literal_reasons(source):
            record(reason, path, line)

        try:
            tree = ast.parse(source)
        except SyntaxError:  # pragma: no cover - app/ must always parse
            continue

        consts = _module_str_constants(tree)

        for reason, line in _helper_default_reasons(tree):
            record(reason, path, line)

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if _called_name(node) not in _REVOKE_HELPERS:
                continue
            for kw in node.keywords:
                if kw.arg != "reason":
                    continue
                if isinstance(kw.value, ast.Constant) and isinstance(
                    kw.value.value, str
                ):
                    record(kw.value.value, path, node.lineno)
                elif isinstance(kw.value, ast.Name) and kw.value.id in consts:
                    record(consts[kw.value.id], path, node.lineno)
                else:
                    unresolved.append(f"{path.name}:{node.lineno}")

    return used, unresolved


# ===========================================================================
# The invariant
# ===========================================================================


class TestConstraintCoversEveryReasonTheCodeWrites:
    def test_migration_file_exists(self):
        assert _MIGRATION.is_file(), (
            f"{_MIGRATION.name} is missing. It is the only thing standing "
            "between the four out-of-vocabulary reasons and a production 500."
        )

    def test_every_reason_used_in_app_is_permitted(self):
        """THE invariant. Adding a reason in app/ without widening the
        migration fails here."""
        permitted = permitted_reasons()
        used, unresolved = reasons_used_in_app()

        assert not unresolved, (
            "revoke helper called with a non-literal reason= at "
            f"{unresolved}; this guard cannot verify it. Use a module-level "
            "string constant so the permitted set stays checkable."
        )

        offenders = {r: sorted(w) for r, w in used.items() if r not in permitted}
        assert not offenders, (
            "these revoked_reason values are written by app/ but are NOT "
            f"permitted by {_MIGRATION.name}: {offenders}. Postgres will "
            "raise CheckViolationError (HTTP 500). Widen the constraint."
        )

    def test_the_four_defect_reasons_are_now_permitted(self):
        """The specific values that were 500ing in production."""
        permitted = permitted_reasons()
        for reason in (
            "password_change",
            "mfa_disabled",
            "expired_with_subsequent_use",
            "password_reset",
        ):
            assert reason in permitted, f"{reason} still rejected by the CHECK"

    def test_original_five_values_are_retained(self):
        """Widening only — narrowing would fail validation against existing
        production rows and break out-of-band operator revocation."""
        permitted = permitted_reasons()
        assert {
            "rotated",
            "reuse_detected",
            "logout",
            "admin",
            "expired",
        } <= permitted

    def test_permitted_set_is_exactly_the_expected_nine(self):
        """Pins the migration so an unreviewed edit to the ARRAY is caught."""
        assert permitted_reasons() == {
            "rotated",
            "reuse_detected",
            "logout",
            "admin",
            "expired",
            "password_change",
            "password_reset",
            "mfa_disabled",
            "expired_with_subsequent_use",
        }

    def test_rollback_section_is_not_parsed_as_live_sql(self):
        """The migration ends with a commented-out 5-value rollback. If the
        parser picked that up instead, every test above would silently
        invert."""
        sql = _MIGRATION.read_text(encoding="utf-8")
        assert "ROLLBACK / DOWN" in sql, "migration lost its rollback section"
        assert "password_change" in permitted_reasons(sql), (
            "parser latched onto the commented-out rollback constraint"
        )


# ===========================================================================
# Non-vacuity — prove the extractors actually extract, and the guard bites
# ===========================================================================


class TestGuardIsNonVacuous:
    """A structural test that finds nothing always passes. These prove the
    scanner sees real values and that an out-of-vocabulary one fails."""

    def test_app_scan_is_not_empty(self):
        used, _ = reasons_used_in_app()
        assert used, "scanner found NO revoked_reason values in app/ — broken"

    @pytest.mark.parametrize(
        "reason, expected_file",
        [
            ("password_change", "app/api/v1/endpoints/auth/password.py"),
            ("mfa_disabled", "app/api/v1/endpoints/mfa/status.py"),
            ("expired_with_subsequent_use", "app/core/security/refresh_tokens.py"),
            ("password_reset", "app/api/v1/endpoints/auth/password_reset.py"),
            ("reuse_detected", "app/core/security/refresh_tokens.py"),
            ("logout", "app/api/v1/endpoints/auth/sessions.py"),
        ],
    )
    def test_scanner_finds_each_known_real_call_site(self, reason, expected_file):
        """Each of these is a real, verified write site. If one is deleted or
        moved to another module this fails loudly, rather than the guard
        quietly going blind.

        Attribution is per-FILE, not per-line: pinning line numbers would
        turn every unrelated edit above a call site into a red test, and the
        module is the part that actually matters here.
        """
        used, _ = reasons_used_in_app()
        assert reason in used, f"scanner missed {reason} entirely"
        assert any(site.startswith(expected_file + ":") for site in used[reason]), (
            f"{reason} no longer written by {expected_file}; "
            f"found at {sorted(used[reason])}"
        )

    def test_scanner_covers_all_three_extraction_strategies(self):
        """kwarg-literal, module-constant, and raw-SQL extraction must each
        contribute — otherwise a whole class of write site is unguarded."""
        used, _ = reasons_used_in_app()
        # kwarg literal (password.py), module constant (password_reset.py),
        # raw SQL (refresh_tokens.py)
        assert "password_change" in used
        assert "password_reset" in used
        assert "expired_with_subsequent_use" in used

    def test_a_new_kwarg_reason_would_fail_the_invariant(self, tmp_path):
        """Simulate the exact regression this suite exists to catch: someone
        adds a call site with a reason nobody widened the constraint for."""
        (tmp_path / "rogue.py").write_text(
            "async def f(conn, uid):\n"
            "    await revoke_all_user_refresh_tokens(\n"
            "        conn, uid, reason='account_deleted')\n",
            encoding="utf-8",
        )
        used, _ = reasons_used_in_app(tmp_path)

        assert "account_deleted" in used, "scanner failed to see the new reason"
        assert "account_deleted" not in permitted_reasons(), (
            "fixture value unexpectedly in the constraint"
        )
        offenders = {r for r in used if r not in permitted_reasons()}
        assert offenders == {"account_deleted"}

    def test_a_new_sql_literal_reason_would_fail_the_invariant(self, tmp_path):
        """Same, via hardcoded SQL rather than the helper."""
        (tmp_path / "rogue_sql.py").write_text(
            'SQL = """\n'
            "    UPDATE refresh_tokens\n"
            "    SET revoked_at = NOW(), revoked_reason = 'gdpr_erasure'\n"
            "    WHERE user_id = $1\n"
            '"""\n',
            encoding="utf-8",
        )
        used, _ = reasons_used_in_app(tmp_path)

        assert "gdpr_erasure" in used
        assert "gdpr_erasure" not in permitted_reasons()

    def test_unrelated_tables_are_not_swept_in(self, tmp_path):
        """connector_accounts.revoked_reason has its own vocabulary
        ('user_requested','security',...) and no CHECK. Flagging it would
        make this guard cry wolf — see
        app/services/connector_revocation_service.py:289."""
        (tmp_path / "connector.py").write_text(
            'SQL = """\n'
            "    UPDATE connector_accounts\n"
            "    SET revoked_reason = 'user_requested'\n"
            '"""\n',
            encoding="utf-8",
        )
        used, _ = reasons_used_in_app(tmp_path)

        assert "user_requested" not in used

    def test_dynamic_reason_is_reported_not_ignored(self, tmp_path):
        """A non-literal reason= can't be verified statically; it must be
        surfaced, never silently skipped."""
        (tmp_path / "dynamic.py").write_text(
            "async def f(conn, uid, why):\n"
            "    await revoke_all_user_refresh_tokens(conn, uid, reason=why)\n",
            encoding="utf-8",
        )
        _, unresolved = reasons_used_in_app(tmp_path)

        assert unresolved, "dynamic reason= slipped past the guard"

    def test_permitted_parser_rejects_a_migration_without_the_constraint(self):
        """If the ADD CONSTRAINT is ever deleted, fail loudly rather than
        returning an empty set (which would make every subset check pass)."""
        with pytest.raises(AssertionError):
            permitted_reasons("-- migration with no constraint at all\n")
