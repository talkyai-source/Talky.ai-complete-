"""F14-02 parity orchestration behavior; SQL execution is a separate PG gate.

Subprocesses are simulated here. These tests do not claim that PostgreSQL can
execute the migration or that the two actual bootstrap schemas are equal.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import re
import subprocess
import sys

import pytest


BACKEND = Path(__file__).resolve().parents[2]
SCRIPT = BACKEND / "scripts" / "verify_schema_parity.py"
REVISION = BACKEND / "Alembic" / "versions" / "0087_bootstrap_manual_parity.py"
SYNTHETIC_PASSWORD = "qa-password-never-log"
ADMIN_DSN = f"postgresql://qa_owner:{SYNTHETIC_PASSWORD}@127.0.0.1:15439/qa_parity_test"


def load(path, name):
    assert path.is_file(), f"F14-02 capability is missing: {path.name}"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def parity():
    return load(SCRIPT, "qa_schema_parity")


def test_missing_object_repair_is_linear_and_cannot_drop_adopted_production_tables(monkeypatch):
    migration = load(REVISION, "qa_bootstrap_manual_parity")
    assert migration.revision == "0087_bootstrap_manual_parity"
    assert migration.down_revision == "0086_call_workflows"
    assert len(migration.revision) <= 32
    issued = []
    monkeypatch.setattr(migration.op, "execute", lambda statement: issued.append(str(statement)))
    # Forward-only is deliberate: this repair can adopt objects that predate
    # Alembic. No provenance exists to justify dropping them on code rollback.
    with pytest.raises(RuntimeError, match="(?i)(forward.only|retain|adopt)"):
        migration.downgrade()
    assert issued == []


def test_dump_normalization_removes_only_volatile_client_metadata(parity):
    before = (
        "-- PostgreSQL database dump\n"
        "-- Dumped from database version 16.14\n"
        "-- Dumped by pg_dump version 16.14\n"
        "\\restrict old_random_client_guard\n"
        "CREATE TABLE public.example (id uuid);\n"
        "COMMENT ON TABLE public.example IS 'kept  verbatim';\n"
        "\\unrestrict old_random_client_guard\n"
    )
    after = before.replace("old_random_client_guard", "new_random_client_guard").replace(
        "-- Dumped by pg_dump version 16.14", "-- Dumped by pg_dump version 16.15"
    )
    assert parity.compare_dumps(before, after) == ""
    normalized = parity.normalize_dump(before)
    assert "kept  verbatim" in normalized
    assert "CREATE TABLE public.example (id uuid);" in normalized


@pytest.mark.parametrize("changed", [
    "CREATE TABLE public.stream_events (id uuid, title text);\n",
    "CREATE UNIQUE INDEX owner_idx ON public.stream_events (id);\n",
    "ALTER TABLE public.stream_events FORCE ROW LEVEL SECURITY;\n",
    "CREATE POLICY owner_scope ON public.stream_events USING (tenant_id = current_setting('app.tenant')::uuid);\n",
    "GRANT SELECT ON public.stream_events TO authenticated;\n",
    "CREATE FUNCTION public.keep_value() RETURNS text LANGUAGE sql AS $$ SELECT 'two  spaces'; $$;\n",
])
def test_parity_keeps_structural_security_and_literal_differences_visible(parity, changed):
    original = "CREATE TABLE public.stream_events (id uuid);\n"
    difference = parity.compare_dumps(original, original + changed)
    assert difference, "A real schema difference must not be normalized away"
    assert changed.strip() in difference


def test_query_literal_whitespace_is_not_normalized_as_formatting(parity):
    first = "CREATE FUNCTION public.f() RETURNS text LANGUAGE sql AS $$ SELECT 'a  b'; $$;\n"
    second = first.replace("'a  b'", "'a b'")
    assert parity.compare_dumps(first, second)


def test_dump_metadata_text_inside_dollar_quoted_function_is_preserved(parity):
    # Both are valid SQL functions with different observable return values.
    # These strings are not pg_dump's own header, despite matching its text.
    first = (
        "-- PostgreSQL database dump\n"
        "CREATE FUNCTION public.metadata_text() RETURNS text LANGUAGE sql AS $$\n"
        "SELECT $value$\n"
        "-- Dumped by pg_dump version 16.14\n"
        "$value$;\n"
        "$$;\n"
    )
    second = first.replace("version 16.14", "version 16.15")
    difference = parity.compare_dumps(first, second)
    assert difference, "A changed function result must never normalize into schema parity"
    assert "-- Dumped by pg_dump version 16.14" in parity.normalize_dump(first)
    assert "-- Dumped by pg_dump version 16.15" in difference


class Processes:
    def __init__(self, *, different=False, fail_stage=None):
        self.different = different
        self.fail_stage = fail_stage
        self.calls = []
        self.created = []
        self.dropped = []
        self.dumped = []
        self.restored = []

    def __call__(self, arguments, **kwargs):
        arguments = [str(value) for value in arguments]
        environment = kwargs["env"]
        assert 0 < kwargs["timeout"] <= 300, "Every external step needs a finite bound"
        assert kwargs.get("shell", False) is False
        assert all(ADMIN_DSN not in argument and SYNTHETIC_PASSWORD not in argument for argument in arguments)
        self.calls.append((arguments, dict(environment)))
        program = Path(arguments[0]).stem.lower()
        stdout = ""
        failed = False
        if program == "psql" and "-c" in arguments:
            command = arguments[arguments.index("-c") + 1]
            created = re.fullmatch(r'CREATE DATABASE "(qa_schema_parity_[a-f0-9]+_(?:clean|lineage))";?', command)
            dropped = re.fullmatch(r'DROP DATABASE(?: IF EXISTS)? "(qa_schema_parity_[a-f0-9]+_(?:clean|lineage))";?', command)
            if created:
                self.created.append(created.group(1))
            elif dropped:
                assert dropped.group(1) in self.created, "Only this invocation's generated databases may be dropped"
                self.dropped.append(dropped.group(1))
        if program == "psql" and "-f" in arguments:
            assert "ON_ERROR_STOP=1" in arguments
            snapshot = Path(arguments[arguments.index("-f") + 1]).name
            self.restored.append(snapshot)
            failed = self.fail_stage == "restore" and snapshot == "baseline_2026-06-02.sql"
        if "alembic" in arguments:
            assert "DATABASE_URL" in environment or "MIGRATION_DATABASE_URL" in environment
            failed = self.fail_stage == "migration" and environment.get("PGDATABASE", "").endswith("_lineage")
        if program == "pg_dump":
            assert "--schema-only" in arguments
            database = environment["PGDATABASE"]
            assert database in self.created
            self.dumped.append(database)
            stdout = "CREATE TABLE public.stream_events (id uuid);\n"
            if self.different and database.endswith("_lineage"):
                stdout += "ALTER TABLE public.stream_events ADD COLUMN missing_from_clean text;\n"
            failed = self.fail_stage == "dump" and database.endswith("_lineage")
        stderr = f"synthetic database failure: {ADMIN_DSN}" if failed else ""
        return subprocess.CompletedProcess(arguments, 1 if failed else 0, stdout, stderr)


def test_equal_routes_are_built_dumped_and_cleaned_without_secret_arguments(parity, tmp_path):
    processes = Processes()
    result = parity.run_parity(
        ADMIN_DSN, backend_root=BACKEND, output_dir=tmp_path, runner=processes,
    )
    assert result["equal"] is True
    assert set(processes.restored) == {"complete_schema.sql", "baseline_2026-06-02.sql"}
    assert len(processes.created) == 2 and len(set(processes.created)) == 2
    assert set(processes.dumped) == set(processes.created)
    assert set(processes.dropped) == set(processes.created)
    assert Path(result["diff_path"]).read_text(encoding="utf-8") == ""
    for arguments, environment in processes.calls:
        assert ADMIN_DSN not in " ".join(arguments)
        assert environment["PGPASSWORD"] == SYNTHETIC_PASSWORD
    migrations = [arguments for arguments, _environment in processes.calls if "alembic" in arguments]
    assert sum("stamp" in arguments and "0008_tenant_voice_tuning" in arguments for arguments in migrations) == 2
    assert sum("upgrade" in arguments and "head" in arguments for arguments in migrations) == 2


def test_different_route_schema_is_reported_and_never_labeled_equal(parity, tmp_path):
    processes = Processes(different=True)
    result = parity.run_parity(
        ADMIN_DSN, backend_root=BACKEND, output_dir=tmp_path, runner=processes,
    )
    assert result["equal"] is False
    assert "missing_from_clean" in Path(result["diff_path"]).read_text(encoding="utf-8")
    assert set(processes.dropped) == set(processes.created)


@pytest.mark.parametrize("stage", ["restore", "migration", "dump"])
def test_external_failure_is_bounded_sanitized_and_cleans_owned_databases(parity, tmp_path, capsys, stage):
    processes = Processes(fail_stage=stage)
    with pytest.raises(parity.ParityError) as failure:
        parity.run_parity(ADMIN_DSN, backend_root=BACKEND, output_dir=tmp_path, runner=processes)
    assert processes.created
    assert set(processes.dropped) == set(processes.created)
    assert SYNTHETIC_PASSWORD not in str(failure.value)
    captured = capsys.readouterr()
    assert SYNTHETIC_PASSWORD not in captured.out + captured.err


@pytest.mark.parametrize("dsn", [
    "postgresql://qa_owner:synthetic@prod.example/qa_test",
    "postgresql://qa_owner:synthetic@127.0.0.1/production",
    "postgresql://qa_owner:synthetic@127.0.0.1/qa_test?host=remote.example",
    "postgresql://qa_owner:synthetic@127.0.0.1/qa_test?database=production",
    "postgresql://qa_owner:synthetic@127.0.0.1/qa_test?dbname=production",
    "sqlite:///qa_test",
])
def test_non_disposable_or_non_loopback_target_is_refused_before_any_process(parity, tmp_path, dsn):
    processes = Processes()
    with pytest.raises(parity.ParityError):
        parity.run_parity(dsn, backend_root=BACKEND, output_dir=tmp_path, runner=processes)
    assert processes.calls == []


ATTVER = "ALTER TABLE ONLY public.example ALTER COLUMN id SET (pg_attver={});\n"


def test_the_per_database_pg_attver_counter_is_not_a_schema_difference(parity):
    """CI's PostgreSQL 15.20 dumps a per-column pg_attver counter (12 Nov 2026)."""
    table = "CREATE TABLE public.example (id uuid);\n"
    assert parity.compare_dumps(table + ATTVER.format(327942592), table + ATTVER.format(338756792)) == ""


def test_a_missing_pg_attver_line_or_a_real_option_beside_it_stays_visible(parity):
    table = "CREATE TABLE public.example (id uuid);\n"
    assert parity.compare_dumps(table + ATTVER.format(1), table)
    with_option = "ALTER TABLE ONLY public.example ALTER COLUMN id SET (n_distinct=1, pg_attver={});\n"
    assert parity.compare_dumps(table + with_option.format(1), table + with_option.format(2))
