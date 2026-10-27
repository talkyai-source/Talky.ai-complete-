"""Monitor safety tests: standard library only, no database access."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from argparse import Namespace
from unittest.mock import patch
import urllib.error

MODULE = Path(__file__).resolve().parents[1] / "monitor.py"
spec = importlib.util.spec_from_file_location("talklee_monitor", MODULE)
monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitor)
recovery = monitor.recovery


def opts(**extra):
    base = dict(latency_warning_ms=250, local_plaintext=False, skip_role_check=False, capacity_bytes=1000,
                capacity_warning=.8, capacity_critical=.9, connections_warning=.8, connections_critical=.95,
                long_tx_warning_seconds=300, long_tx_critical_seconds=1800, wraparound_warning=.5,
                wraparound_critical=.75, archive_max_age_minutes=30, require_archiving=False)
    base.update(extra)
    return Namespace(**base)


def snapshot(**extra):
    base = dict(ssl=True, superuser=False, bypass_rls=False, create_role=False, create_database=False,
                owned_relations=0, database_bytes=100, connections=5, max_connections=100,
                oldest_xact_seconds=None, max_xid_age=1000, archive_mode="off", failing=False)
    base.update(extra)
    return base


def by_name(checks):
    return {c["name"]: c for c in checks}


class ClassificationTests(unittest.TestCase):
    def test_classify_boundaries(self):
        self.assertEqual(monitor.classify(.79, .8, .9), "ok")
        self.assertEqual(monitor.classify(.8, .8, .9), "warning")
        self.assertEqual(monitor.classify(.9, .8, .9), "critical")

    def test_healthy_snapshot_is_all_ok(self):
        checks = monitor.evaluate(snapshot(), 10, opts())
        self.assertEqual(monitor.worst(checks), "ok")
        self.assertEqual({"connect", "tls", "restricted_role", "database_size", "connections",
                          "long_transactions", "wraparound", "wal_archiving"}, set(by_name(checks)))
        for c in checks:
            self.assertEqual({"name", "status", "value", "threshold", "detail"}, set(c))

    def test_capacity_levels(self):
        for size, expect in ((799, "ok"), (800, "warning"), (900, "critical")):
            self.assertEqual(by_name(monitor.evaluate(snapshot(database_bytes=size), 1, opts()))["database_size"]["status"], expect)

    def test_connections_levels(self):
        for n, expect in ((79, "ok"), (80, "warning"), (95, "critical")):
            self.assertEqual(by_name(monitor.evaluate(snapshot(connections=n), 1, opts()))["connections"]["status"], expect)

    def test_long_transactions_and_wraparound(self):
        for age, expect in ((10, "ok"), (301, "warning"), (1801, "critical")):
            c = by_name(monitor.evaluate(snapshot(oldest_xact_seconds=age), 1, opts()))
            self.assertEqual(c["long_transactions"]["status"], expect)
        for xid, expect in ((2 ** 30 - 1, "ok"), (2 ** 30, "warning"), (int(2 ** 31 * .75), "critical")):
            c = by_name(monitor.evaluate(snapshot(max_xid_age=xid), 1, opts()))
            self.assertEqual(c["wraparound"]["status"], expect)

    def test_latency_and_tls(self):
        self.assertEqual(by_name(monitor.evaluate(snapshot(), 251, opts()))["connect"]["status"], "warning")
        self.assertEqual(by_name(monitor.evaluate(snapshot(ssl=False), 1, opts()))["tls"]["status"], "critical")
        self.assertEqual(by_name(monitor.evaluate(snapshot(ssl=False), 1, opts(local_plaintext=True)))["tls"]["status"], "ok")

    def test_role_checks(self):
        for extra in (dict(superuser=True), dict(bypass_rls=True), dict(create_role=True),
                      dict(create_database=True), dict(owned_relations=2)):
            self.assertEqual(by_name(monitor.evaluate(snapshot(**extra), 1, opts()))["restricted_role"]["status"], "critical")
        c = by_name(monitor.evaluate(snapshot(superuser=True), 1, opts(skip_role_check=True)))
        self.assertEqual(c["restricted_role"]["status"], "ok")

    def test_archiving(self):
        seg = lambda n: "00000001" + "00000000" + format(n, "08X")
        off = by_name(monitor.evaluate(snapshot(), 1, opts()))["wal_archiving"]
        self.assertEqual(off["status"], "ok")
        self.assertIn("not configured", off["detail"])
        req = by_name(monitor.evaluate(snapshot(), 1, opts(require_archiving=True)))["wal_archiving"]
        self.assertEqual(req["status"], "critical")
        base = dict(archive_mode="on", wal_segment_bytes=16 * 1024 * 1024)
        fail = snapshot(**base, failing=True, failed_count=3, current_wal=seg(9), last_archived_wal=seg(8), last_archived_age_seconds=5)
        self.assertEqual(by_name(monitor.evaluate(fail, 1, opts()))["wal_archiving"]["status"], "critical")
        stale = snapshot(**base, current_wal=seg(20), last_archived_wal=seg(8), last_archived_age_seconds=3600)
        self.assertEqual(by_name(monitor.evaluate(stale, 1, opts()))["wal_archiving"]["status"], "warning")
        idle = snapshot(**base, current_wal=seg(9), last_archived_wal=seg(8), last_archived_age_seconds=99999)
        self.assertEqual(by_name(monitor.evaluate(idle, 1, opts()))["wal_archiving"]["status"], "ok")
        fresh = snapshot(**base, current_wal=seg(20), last_archived_wal=seg(19), last_archived_age_seconds=10)
        self.assertEqual(by_name(monitor.evaluate(fresh, 1, opts()))["wal_archiving"]["status"], "ok")

    def test_worst_aggregation(self):
        mk = lambda *s: [{"status": x} for x in s]
        self.assertEqual(monitor.worst(mk("ok", "warning")), "warning")
        self.assertEqual(monitor.worst(mk("warning", "critical", "ok")), "critical")
        self.assertEqual(monitor.worst([]), "ok")

    def test_backup_freshness(self):
        self.assertIsNone(monitor.backup_check(None, 26))
        with patch.object(recovery, "health", side_effect=recovery.RecoveryError("x")):
            self.assertEqual(monitor.backup_check(Path("."), 26)["status"], "critical")
        with patch.object(recovery, "health", return_value={"age_hours": 1.5}):
            self.assertEqual(monitor.backup_check(Path("."), 26)["status"], "ok")


class AlertStateTests(unittest.TestCase):
    def test_state_machine(self):
        d = monitor.decide_alert
        self.assertIsNone(d(None, "ok", 0, 60))
        self.assertEqual(d(None, "critical", 0, 60), "alert")
        self.assertEqual(d({"status": "ok"}, "warning", 0, 60), "alert")
        prev = {"status": "warning", "last_alert_at": 1000}
        self.assertIsNone(d(prev, "warning", 1000 + 59 * 60, 60))
        self.assertEqual(d(prev, "warning", 1000 + 60 * 60, 60), "alert")
        self.assertEqual(d(prev, "critical", 1001, 60), "alert")
        self.assertIsNone(d({"status": "critical", "last_alert_at": 1000}, "warning", 1001, 60))
        self.assertEqual(d(prev, "ok", 1001, 60), "resolved")

    def test_payload_has_names_and_values_only(self):
        report = {"service": "svc", "status": "critical", "checked_at": "t",
                  "checks": [{"name": "database_size", "status": "critical", "value": {"ratio": .95}, "threshold": {}, "detail": "d"},
                             {"name": "tls", "status": "ok", "value": True, "threshold": "t", "detail": "e"}]}
        payload = monitor.alert_payload("alert", report)
        self.assertIn("database_size", payload["text"])
        self.assertEqual(payload["talklee"]["status"], "critical")
        self.assertEqual(len(payload["talklee"]["checks"]), 2)
        self.assertTrue(monitor.alert_payload("resolved", report)["text"].startswith("RESOLVED"))


class PrometheusTests(unittest.TestCase):
    def test_format(self):
        checks = monitor.evaluate(snapshot(database_bytes=850), 1, opts())
        text = monitor.prometheus_text(monitor.build_report(checks, "svc"), True)
        self.assertIn("talklee_db_up 1\n", text)
        self.assertIn('talklee_db_check_status{check="database_size"} 1\n', text)
        self.assertIn("talklee_db_size_bytes 850\n", text)
        self.assertIn("talklee_db_connections 5\n", text)
        self.assertIn("talklee_db_capacity_ratio 0.85\n", text)
        down = monitor.prometheus_text(monitor.build_report(monitor.failed_report(opts()), "svc"), False)
        self.assertIn("talklee_db_up 0\n", down)
        self.assertIn('talklee_db_check_status{check="connect"} 2', down)
        self.assertNotIn("talklee_db_size_bytes", down)


class MainFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.pw = self.root / "secret-pass-file.txt"
        self.pw.write_text("localhost:*:*:u:TOPSECRETPASSWORD\n")
        self.pw.chmod(0o600)
        self.svc = self.root / "services.conf"
        self.svc.write_text(f"[t]\nhost=127.0.0.1\nport=5432\ndbname=talky\nuser=runtime\nsslmode=disable\npassfile={self.pw}\n")
        self.svc.chmod(0o600)
        self.state = self.root / "state.json"

    def tearDown(self):
        self.tmp.cleanup()

    def run_main(self, data=None, fail=False, extra=()):
        argv = ["--service-file", str(self.svc), "--service", "t", "--allow-local-plaintext", "check",
                "--capacity-bytes", "1000", "--alert-webhook-url", "http://127.0.0.1:1/hook",
                "--state-file", str(self.state), *extra]
        sent = []

        def fake_post(url, payload, timeout=10):
            if fail:
                raise recovery.RecoveryError("alert webhook could not be reached")
            sent.append(payload)
        with patch.object(recovery.Postgres, "executable", return_value="psql"), \
                patch.object(recovery.Postgres, "json", return_value=data or snapshot(ssl=False)), \
                patch.object(monitor, "post_webhook", side_effect=fake_post), \
                patch("sys.stdout", new_callable=__import__("io").StringIO) as out, \
                patch("sys.stderr", new_callable=__import__("io").StringIO) as err:
            code = monitor.main(argv)
        return code, sent, out.getvalue(), err.getvalue()

    def test_alert_lifecycle_and_exit_codes(self):
        code, sent, out, _ = self.run_main()
        self.assertEqual((code, len(sent)), (0, 0))
        code, sent, _, _ = self.run_main(snapshot(ssl=False, database_bytes=950))
        self.assertEqual((code, len(sent)), (2, 1))
        code, sent, _, _ = self.run_main(snapshot(ssl=False, database_bytes=950))
        self.assertEqual((code, len(sent)), (2, 0))
        code, sent, _, _ = self.run_main()
        self.assertEqual(code, 0)
        self.assertEqual(len(sent), 1)
        self.assertTrue(sent[0]["text"].startswith("RESOLVED"))
        code, sent, _, _ = self.run_main()
        self.assertEqual(len(sent), 0)

    def test_webhook_failure_keeps_db_status_and_retries(self):
        code, sent, out, err = self.run_main(snapshot(ssl=False, database_bytes=850), fail=True)
        self.assertEqual(code, 1)
        self.assertIn("Alert delivery failed", err)
        self.assertEqual(json.loads(out)["status"], "warning")
        code, sent, _, _ = self.run_main(snapshot(ssl=False, database_bytes=850))
        self.assertEqual(len(sent), 1)

    def test_connect_failure_is_critical(self):
        with patch.object(recovery.Postgres, "executable", return_value="psql"), \
                patch.object(recovery.Postgres, "json", side_effect=recovery.RecoveryError("x")), \
                patch("sys.stdout", new_callable=__import__("io").StringIO) as out:
            code = monitor.main(["--service-file", str(self.svc), "--service", "t", "--allow-local-plaintext",
                                 "check", "--capacity-bytes", "1000"])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out.getvalue())["checks"][0]["status"], "critical")

    def test_config_error_exit_3(self):
        with patch("sys.stderr", new_callable=__import__("io").StringIO):
            code = monitor.main(["--service-file", str(self.svc), "--service", "t", "check", "--capacity-bytes", "1000"])
        self.assertEqual(code, 3)  # plaintext without the explicit local exception

    def test_no_secrets_in_alerts_or_output(self):
        _, sent, out, err = self.run_main(snapshot(ssl=False, database_bytes=950))
        blob = json.dumps(sent) + out + err
        for forbidden in (str(self.pw), "secret-pass-file", "TOPSECRETPASSWORD", "sslmode", "passfile", str(self.svc)):
            self.assertNotIn(forbidden, blob)

    def test_webhook_http_error_is_wrapped(self):
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("boom")):
            with self.assertRaises(recovery.RecoveryError) as ctx:
                monitor.post_webhook("http://127.0.0.1:1/x", {"text": "x"})
        self.assertNotIn("boom", str(ctx.exception))
        with self.assertRaises(recovery.RecoveryError):
            monitor.post_webhook("file:///etc/passwd", {})

    def test_atomic_write_replaces(self):
        target = self.root / "m.prom"
        monitor.atomic_write(target, "a")
        monitor.atomic_write(target, "b")
        self.assertEqual(target.read_text(), "b")
        self.assertEqual([p.name for p in self.root.glob(".m.prom.*")], [])


if __name__ == "__main__":
    unittest.main()
