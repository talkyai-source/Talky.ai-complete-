"""preflight.py: every readiness rule, with no database (standard library only)."""
from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import preflight  # noqa: E402
from recovery import RecoveryError  # noqa: E402


def target(**overrides):
    base = {"server_version_num": 160004, "server_version": "16.4", "tls": True, "tls_version": "TLSv1.3",
            "superuser": False, "create_role": True, "create_database": True, "max_connections": 200,
            "superuser_reserved_connections": 3, "reserved_connections": 0, "connections_in_use": 4,
            "encoding": "UTF8", "collate": "en_US.UTF-8", "ctype": "en_US.UTF-8", "timezone": "UTC",
            "databases": ["postgres", "template0", "template1"], "roles": [],
            "extensions": {"pgcrypto": True, "uuid-ossp": True, "pg_trgm": True, "postgis": False}}
    base.update(overrides)
    return base


def source(**overrides):
    base = {"server_version_num": 150017, "database_bytes": 2 * 1024 ** 3, "encoding": "UTF8",
            "collate": "en_US.utf8", "ctype": "en_US.utf8", "timezone": "UTC",
            "extensions": ["pg_trgm", "pgcrypto", "uuid-ossp"], "tables": 109,
            "rls_not_forced": [], "tenant_tables_without_rls": []}
    base.update(overrides)
    return base


FAST = {"samples": 50, "p50_ms": 0.31, "p95_ms": 0.52, "max_ms": 0.9}


def run(t=None, s=None, rtt=FAST, client=16, **options):
    settings = {"database": "talkyai", "budget": 50, "headroom": 20,
                "required_extensions": ["pg_trgm", "pgcrypto", "uuid-ossp"], "capacity_bytes": 40 * 1024 ** 3,
                "max_p95_ms": 2.0}
    settings.update(options)
    checks, recommendations = preflight.evaluate(t or target(), s if s is not None else source(), rtt, client, **settings)
    return {name: check["status"] for name, check in checks.items()}, checks, recommendations


class EvaluateTests(unittest.TestCase):
    def test_a_ready_target_passes_every_check(self):
        statuses, _, recommendations = run()
        self.assertEqual(set(statuses.values()), {"ok"}, statuses)
        self.assertEqual(recommendations["runtime_connection_limit"], 197 - 10)

    def test_version_rules(self):
        self.assertEqual(run(target(server_version_num=140010, server_version="14.10"))[0]["target_version"], "fail")
        self.assertEqual(run(target(server_version_num=150004), source(server_version_num=160002))[0]["target_version"], "fail")

    def test_tls_and_administrator_rights(self):
        self.assertEqual(run(target(tls=False))[0]["target_tls"], "fail")
        self.assertEqual(run(target(create_role=False))[0]["administrator"], "fail")
        self.assertEqual(run(target(superuser=True))[0]["administrator"], "warning")

    def test_connection_capacity_counts_reserved_slots_and_headroom(self):
        small = target(max_connections=25, superuser_reserved_connections=3)   # a 1 GB managed plan
        statuses, checks, _ = run(small)
        self.assertEqual(statuses["connection_capacity"], "fail")
        self.assertIn("22 usable connections", checks["connection_capacity"]["detail"])
        self.assertEqual(run(target(max_connections=80, superuser_reserved_connections=3, reserved_connections=2))[0]
                         ["connection_capacity"], "warning")

    def test_extensions_must_exist_and_be_trusted_for_a_non_superuser(self):
        self.assertEqual(run(target(extensions={"pgcrypto": True, "pg_trgm": True}))[0]["extensions"], "fail")
        untrusted = target(extensions={"pgcrypto": True, "uuid-ossp": False, "pg_trgm": True})
        self.assertEqual(run(untrusted)[0]["extensions"], "fail")
        self.assertEqual(run(dict(untrusted, superuser=True))[0]["extensions"], "ok")

    def test_encoding_collation_and_time_zone(self):
        self.assertEqual(run(target(encoding="SQL_ASCII"))[0]["encoding_and_collation"], "fail")
        self.assertEqual(run(target(collate="C", ctype="C"))[0]["encoding_and_collation"], "warning")
        self.assertEqual(run(target(timezone="Europe/Berlin"))[0]["time_zone"], "warning")

    def test_target_database_must_not_exist_and_existing_roles_are_flagged(self):
        statuses = run(target(databases=["postgres", "talkyai"], roles=["talklee_app"]))[0]
        self.assertEqual((statuses["target_database_absent"], statuses["existing_roles"]), ("fail", "warning"))

    def test_latency_thresholds(self):
        self.assertEqual(run(rtt=dict(FAST, p95_ms=12.0))[0]["round_trip_latency"], "fail")
        self.assertEqual(run(rtt=dict(FAST, p95_ms=1.4))[0]["round_trip_latency"], "warning")

    def test_client_tools_must_be_able_to_dump_the_source(self):
        self.assertEqual(run(client=14)[0]["client_tools"], "fail")
        self.assertEqual(run(client=15)[0]["client_tools"], "warning")

    def test_storage_capacity_thresholds(self):
        self.assertEqual(run(capacity_bytes=3 * 1024 ** 3)[0]["storage_capacity"], "fail")
        self.assertEqual(run(capacity_bytes=6 * 1024 ** 3)[0]["storage_capacity"], "warning")
        self.assertEqual(run(s={}, capacity_bytes=6 * 1024 ** 3)[0]["storage_capacity"], "warning")  # not compared

    def test_unforced_row_level_security_in_the_source_fails_before_the_window(self):
        statuses, checks, _ = run(s=source(rls_not_forced=["tenant_provider_cost_events"],
                                           tenant_tables_without_rls=["legacy_notes"]))
        self.assertEqual((statuses["row_level_security"], statuses["tenant_tables_without_rls"]), ("fail", "warning"))
        self.assertIn("tenant_provider_cost_events", checks["row_level_security"]["detail"])


class HelperTests(unittest.TestCase):
    def test_pool_budget_reads_the_repository_units(self):
        budget = preflight.pool_budget(preflight.DEFAULT_SYSTEMD_DIR)
        self.assertEqual(budget["talky-api.service"], 15)
        self.assertEqual(sum(budget.values()), 50)

    def test_pool_budget_needs_at_least_one_declared_pool(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "talky-api.service").write_text("[Service]\nExecStart=/bin/true\n")
            with self.assertRaises(RecoveryError):
                preflight.pool_budget(Path(tmp))

    def test_existing_role_check_names_only_the_managed_roles(self):
        sql = preflight.target_sql("talklee_owner", "talklee_app", "talklee_backup")
        self.assertIn("rolname IN ('talklee_owner', 'talklee_app', 'talklee_backup')", sql)
        self.assertNotIn("__ROLES__", sql)
        with self.assertRaises(RecoveryError):
            preflight.target_sql("talklee_owner", "app'); DROP ROLE x; --", "talklee_backup")

    def test_latency_percentiles(self):
        result = preflight.latency([float(n) for n in range(1, 101)])
        self.assertEqual((result["p50_ms"], result["p95_ms"], result["max_ms"]), (50.5, 95.0, 100.0))

    def test_measure_latency_parses_psql_timing_and_refuses_gaps(self):
        pg, service = MagicMock(), MagicMock()
        service.env.return_value = {}
        pg.run.return_value = "Timing is on.\n" + "1\nTime: 0.412 ms\n" * 9 + "1\nTime: 1001.250 ms (00:01.001)\n"
        result = preflight.measure_latency(pg, service, 10)
        self.assertEqual((result["samples"], result["max_ms"]), (10, 1001.25))
        sent = pg.run.call_args.kwargs["sql"]
        self.assertTrue(sent.startswith("\\timing on\n") and sent.count("SELECT 1;") == 10)
        with self.assertRaises(RecoveryError):
            preflight.measure_latency(pg, service, 11)
        with self.assertRaises(RecoveryError):
            preflight.measure_latency(pg, service, 2)


if __name__ == "__main__":
    unittest.main()
