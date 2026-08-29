"""Drift guards for first-terminal-wins settlement and rollout cutover."""

from __future__ import annotations

import ast
import importlib
import re
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[2]
_MIGRATION = _BACKEND / "Alembic" / "versions" / "0028_call_terminal_settlement_cas.py"
_SCHEMA = _BACKEND / "database" / "complete_schema.sql"
_LIFECYCLE = _BACKEND / "app" / "domain" / "services" / "telephony" / "lifecycle.py"


def _module():
    return importlib.import_module("Alembic.versions.0028_call_terminal_settlement_cas")


def _normalized(sql: str) -> str:
    without_comments = re.sub(r"--[^\n]*", "", sql)
    return re.sub(r"\s+", " ", without_comments).strip().replace("public.", "")


def _schema_function(source: str) -> str:
    start = source.index("CREATE OR REPLACE FUNCTION update_call_status(")
    end = source.index("$$;", start) + 3
    return source[start:end]


def _upgrade_sql(source: str) -> list[str]:
    tree = ast.parse(source)
    upgrade = next(
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "upgrade"
    )
    statements: list[str] = []
    for node in ast.walk(upgrade):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        if not (isinstance(node.func, ast.Name) and node.func.id == "text"):
            continue
        try:
            value = ast.literal_eval(node.args[0])
        except (TypeError, ValueError):
            continue
        if isinstance(value, str):
            statements.append(value)
    return statements


def test_0028_chains_from_request_key_migration() -> None:
    migration = _module()

    assert migration.revision == "0028_call_terminal_settle_cas"
    assert len(migration.revision) <= 32
    assert migration.down_revision == "0027_media_delete_request_keys"


def test_pre_cutover_terminal_outbound_rows_are_marked_before_recovery() -> None:
    """Historical calls must never be replayed into lead/campaign counters."""

    source = _MIGRATION.read_text(encoding="utf-8")
    statements = [_normalized(sql) for sql in _upgrade_sql(source)]
    backfill = next(
        sql for sql in statements if sql.startswith("UPDATE calls SET terminal_settled_at")
    )

    assert "terminal_settled_at = CURRENT_TIMESTAMP" in backfill
    assert "direction = 'outbound'" in backfill
    assert "status = ANY(ARRAY[" in backfill
    assert "terminal_settled_at IS NULL" in backfill

    # New post-cutover rows retain the nullable default. The <=30s scan only
    # selects terminal outbound rows whose marker is still NULL (or whose
    # retry outbox remains pending), so the backfilled historical set is inert.
    lifecycle = _normalized(_LIFECYCLE.read_text(encoding="utf-8"))
    assert "direction='outbound' AND status IN" in lifecycle
    assert "terminal_settled_at IS NULL OR" in lifecycle
    assert "terminal_retry_payload IS NOT NULL" in lifecycle
    assert "terminal_retry_enqueued_at IS NULL" in lifecycle


def test_0028_repairs_the_missing_call_to_dialer_job_link() -> None:
    migration = _normalized(_MIGRATION.read_text(encoding="utf-8"))
    complete = _normalized(_SCHEMA.read_text(encoding="utf-8"))

    for source in (migration, complete):
        assert "ADD COLUMN IF NOT EXISTS dialer_job_id UUID" in source
        assert "REFERENCES dialer_jobs(id) ON DELETE SET NULL" in source
        assert "CREATE INDEX IF NOT EXISTS idx_calls_dialer_job_id" in source
    assert "SELECT DISTINCT ON (call_id) call_id, id FROM dialer_jobs" in migration
    assert "c.dialer_job_id IS NULL" in migration


def test_compatibility_function_is_monotonic_but_never_claims_settlement() -> None:
    function = _normalized(_module()._FIRST_TERMINAL_FUNCTION)

    assert "FOR UPDATE" in function
    assert "SECURITY INVOKER" in function
    assert "SECURITY DEFINER" not in function
    assert "IF v_call.status = ANY(v_terminal_statuses)" in function
    assert "outcome = COALESCE(outcome, p_outcome)" in function
    assert "duration_seconds = COALESCE(duration_seconds, p_duration)" in function
    assert "SET status = 'completed'" in function
    assert "settlement_required" in function
    # The compatibility projection cannot certify application-owned lead,
    # job, campaign or Redis-outbox effects.
    assert "SET terminal_settled_at" not in function
    assert "UPDATE leads" not in function
    assert "UPDATE dialer_jobs" not in function
    assert "UPDATE campaigns" not in function


def test_optional_rpc_roles_cannot_block_a_self_hosted_upgrade() -> None:
    grants = _normalized(_module()._OPTIONAL_RPC_ROLE_GRANTS)

    assert "IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated')" in grants
    assert "IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role')" in grants
    assert "TO authenticated'" in grants
    assert "TO service_role'" in grants

    # A direct multi-role GRANT fails before the application can start when
    # either optional Supabase role is absent from plain PostgreSQL.
    assert "TO authenticated, service_role" not in grants


def test_downgrade_refuses_to_delete_unresolved_terminal_recovery_state() -> None:
    guard = _normalized(_module()._DOWNGRADE_GUARD)

    assert "direction = 'outbound'" in guard
    assert "status = ANY(ARRAY[" in guard
    assert "terminal_settled_at IS NULL" in guard
    assert "terminal_retry_payload IS NOT NULL" in guard
    assert "terminal_retry_enqueued_at IS NULL" in guard
    assert "0028 downgrade refused: unresolved terminal settlement or retry intent exists" in guard


def test_downgrade_refuses_replay_unsafe_rpc_restoration() -> None:
    guard = _normalized(_module()._DOWNGRADE_GUARD)
    source = _MIGRATION.read_text(encoding="utf-8")
    downgrade = source[source.index("def downgrade()") :]

    assert downgrade.index("LOCK TABLE public.calls IN ACCESS EXCLUSIVE MODE") < (
        downgrade.index("_DOWNGRADE_GUARD")
    )
    assert (
        "END IF; RAISE EXCEPTION '0028 downgrade refused: pre-0028 "
        "update_call_status is non-monotonic and replay-unsafe'; END;"
    ) in guard
    assert guard.index("IF EXISTS ( SELECT 1 FROM calls") < guard.index(
        "pre-0028 update_call_status is non-monotonic and replay-unsafe"
    )

    # No automated downgrade path may reinstall the function that overwrote
    # terminal status/outcome and incremented lead.call_attempts on each replay.
    assert "_PREVIOUS_UPDATE_CALL_STATUS_FUNCTION" not in source
    assert "CREATE OR REPLACE FUNCTION" not in downgrade
    for column in (
        "terminal_settled_at",
        "terminal_retry_payload",
        "terminal_retry_enqueued_at",
    ):
        assert f"DROP COLUMN IF EXISTS {column}" not in downgrade


def test_complete_schema_uses_the_same_first_terminal_function() -> None:
    migration = _normalized(_module()._FIRST_TERMINAL_FUNCTION)
    complete = _normalized(_schema_function(_SCHEMA.read_text(encoding="utf-8")))

    assert migration == complete
