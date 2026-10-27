#!/usr/bin/env python3
"""Explicit, synthetic local test of monitor.py against one disposable loopback cluster.

Does not use DATABASE_URL, existing servers, real customers or providers. The only
network peer is a webhook receiver bound to 127.0.0.1 on a random port.
"""
from __future__ import annotations

import argparse
import http.server
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import local_postgres_rehearsal as harness  # noqa: E402

recovery = harness.recovery
MONITOR = HERE.parent / "monitor.py"


class Receiver:
    def __init__(self):
        self.messages = []
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                outer.messages.append(json.loads(body))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ok")

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/hook"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def run_monitor(bin_dir, service_file, service, extra, env_extra=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith("PG") and k != "TALKLEE_ALERT_WEBHOOK_URL"}
    flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    result = subprocess.run([sys.executable, str(MONITOR), "--service-file", str(service_file), "--service", service,
                             "--bin-dir", str(bin_dir), "--allow-local-plaintext", "check", "--latency-warning-ms", "20000", *extra],
                            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120, env=env, **flags)
    try:
        report = json.loads(result.stdout)
    except json.JSONDecodeError:
        raise AssertionError("monitor produced no JSON; stderr: " + result.stderr[-500:])
    return result.returncode, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bin-dir", type=Path, required=True)
    parser.add_argument("--run", action="store_true", required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    root = HERE.parents[2]
    output = root / "test-output"
    output.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="monitor-rehearsal-", dir=output)).resolve()
    data = work / "cluster"
    receiver = None
    cluster_started = False
    checks = []
    try:
        receiver = Receiver()
        cluster_started = True  # registered before commands that may leave descendants
        harness.run(args.bin_dir, "initdb", ["-D", str(data), "-U", "fixture_owner", "--auth=trust", "--encoding=UTF8", "--locale=C"])
        port = harness.free_port()
        harness.run(args.bin_dir, "pg_ctl", ["-D", str(data), "-l", str(work / "cluster.log"),
                                            "-o", f"-h 127.0.0.1 -p {port}", "-w", "start"])
        def section(name, user, db="monitor_fixture"):
            return f"[{name}]\nhost=127.0.0.1\nport={port}\ndbname={db}\nuser={user}\nsslmode=disable\n"
        service_file = work / "services.conf"
        service_file.write_text(section("admin_setup", "fixture_owner", "postgres") + "\n" + section("runtime", "monitor_ro")
                                + "\n" + section("overpriv", "monitor_super"), encoding="utf-8")
        service_file.chmod(0o600)
        pg = recovery.Postgres(args.bin_dir, timeout=60)
        setup = recovery.Service(service_file, "admin_setup", True)
        pg.sql(setup, "CREATE DATABASE monitor_fixture;", readonly=False)
        pg.sql(setup, "CREATE ROLE monitor_ro LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE; "
                      "CREATE ROLE monitor_super LOGIN SUPERUSER; GRANT CONNECT ON DATABASE monitor_fixture TO monitor_ro;", readonly=False)
        big = ["--capacity-bytes", str(1024 ** 4)]
        tiny = ["--capacity-bytes", "1000000"]
        state = work / "alert-state.json"
        prom = work / "metrics.prom"
        alert = ["--alert-webhook-url", receiver.url, "--state-file", str(state), "--realert-minutes", "60"]

        code, report = run_monitor(args.bin_dir, service_file, "runtime", [*big, *alert, "--prometheus-textfile", str(prom)])
        statuses = {c["name"]: c["status"] for c in report["checks"]}
        assert code == 0 and report["status"] == "ok", (code, statuses)
        assert not receiver.messages
        checks.append("healthy restricted role: exit 0, every check ok, no alert sent")
        text = prom.read_text(encoding="utf-8")
        assert "talklee_db_up 1" in text and 'talklee_db_check_status{check="database_size"} 0' in text
        assert "talklee_db_size_bytes " in text and "talklee_db_connections " in text and "talklee_db_capacity_ratio " in text
        checks.append("Prometheus textfile written with up, check status, size, connections and capacity ratio")

        code, report = run_monitor(args.bin_dir, service_file, "runtime", [*tiny, *alert])
        statuses = {c["name"]: c["status"] for c in report["checks"]}
        assert code == 2 and statuses["database_size"] == "critical", (code, statuses)
        assert len(receiver.messages) == 1 and receiver.messages[0]["talklee"]["status"] == "critical"
        checks.append("tiny capacity: database_size critical, exit 2, exactly one alert received")

        code, _ = run_monitor(args.bin_dir, service_file, "runtime", [*tiny, *alert])
        assert code == 2 and len(receiver.messages) == 1
        checks.append("second run inside the re-alert window sent no new alert")

        code, report = run_monitor(args.bin_dir, service_file, "runtime", [*big, *alert])
        assert code == 0 and len(receiver.messages) == 2 and receiver.messages[1]["text"].startswith("RESOLVED")
        checks.append("healthy again: exactly one RESOLVED alert")
        code, _ = run_monitor(args.bin_dir, service_file, "runtime", [*big, *alert])
        assert code == 0 and len(receiver.messages) == 2
        checks.append("steady healthy state sends nothing further")

        code, report = run_monitor(args.bin_dir, service_file, "overpriv", big)
        statuses = {c["name"]: c["status"] for c in report["checks"]}
        assert code == 2 and statuses["restricted_role"] == "critical", (code, statuses)
        code, report = run_monitor(args.bin_dir, service_file, "overpriv", [*big, "--skip-role-check"])
        assert code == 0, code
        checks.append("over-privileged role: restricted_role critical (exit 2); --skip-role-check admits it")

        blob = json.dumps(receiver.messages)
        assert str(service_file) not in blob and "sslmode" not in blob
        checks.append("alert payloads contain no service-file path or contents")

        payload = {"status": "passed", "completed_at": recovery.utcnow(), "checks": checks,
                   "alerts_received": len(receiver.messages), "postgres_client": pg.run("psql", ["--version"]),
                   "production_accessed": False,
                   "scope": "synthetic single-cluster loopback rehearsal; webhook is a local 127.0.0.1 receiver"}
        recovery.write_json_new(args.receipt, payload)
        print(json.dumps(payload, indent=2))
        return 0
    finally:
        if receiver:
            receiver.close()
        errors = []
        if cluster_started and (data / "postmaster.pid").exists():
            try:
                harness.run(args.bin_dir, "pg_ctl", ["-D", str(data), "-m", "immediate", "-w", "stop"])
            except Exception as exc:
                errors.append(str(exc))
        if errors:
            raise RuntimeError("owned cluster cleanup failed; retained " + str(work) + ": " + "; ".join(errors))
        if work.parent == output.resolve() and work.name.startswith("monitor-rehearsal-"):
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
