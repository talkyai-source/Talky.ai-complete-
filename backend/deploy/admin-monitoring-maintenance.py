#!/usr/bin/env python3
"""One production-based monitoring backport; never a general deployment bypass.

Run from /opt/talky/releases/<full SHA> with the existing production venv.
Only API-web is restarted. The full release/drain/migration procedure is unchanged.
Credentials are requested on the terminal and never persisted in the receipt.
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

BASE = "2739eeabf7d091ffa63b7dc0695019da77f972f9"
PREVIOUS_MONITORING = "80f011d40c187afecdaaf84ee7aba6c188e835fc"
BRANCH = "refs/heads/codex/admin-monitoring-live-20261027"
LIVE = Path("/opt/talky")
UNIT = Path("/etc/systemd/system/talky-api-web.service")
DROPIN = Path("/etc/systemd/system/talky-api-web.service.d/20-talky-deps.conf")
DROPIN_SHA = "8d77c469160e8653bd29c6322bed767677fca6de5da4d50a2549221fb4ada6ff"
ORIGIN = "https://talkleeai-admin.vercel.app"
PUBLIC = "https://api.talkleeai.com/api/v1"
DIRECT = "http://127.0.0.1:8001/api/v1"
RUNTIME = {
    "backend/app/api/v1/endpoints/admin/base.py",
    *{"backend/app/api/v1/endpoints/admin/health/" + name + ".py"
      for name in ("_shared", "schemas", "system", "workers", "queues", "database")},
    "backend/app/api/v1/endpoints/admin/health/_pbx_probe.py",
    "backend/app/api/v1/endpoints/health.py",
    "backend/app/infrastructure/metrics/provider_observations.py",
}
OTHER = {
    "backend/deploy/admin-monitoring-maintenance.py",
    "backend/tests/unit/test_admin_monitoring_truth.py",
    "backend/tests/unit/test_provider_observations.py",
    "backend/tests/unit/test_admin_monitoring_backport_contract.py",
    "backend/tests/unit/test_admin_pbx_probe.py",
    "docs/sessions/2026-10-27-admin-monitoring-live.md",
    "docs/sessions/2026-10-27-admin-remaining-limitations.md",
}
SERVICES = ("talky-api", "talky-voice-gateway", "talky-dialer-worker",
            "talky-voice-worker", "talky-reminder-worker", "talky-trunk-status", "asterisk")
PATHS = ("/admin/dashboard/stats", "/admin/system-health", "/admin/health/detailed",
         "/admin/health/workers", "/admin/health/queues", "/admin/health/database")


def need(ok, reason):
    if not ok:
        raise RuntimeError(reason)


def run(*args, cwd=LIVE, timeout=45):
    # Never echo subprocess output on failure: import errors can contain settings.
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    need(result.returncode == 0, "Command failed: " + args[0])
    return result.stdout.strip()


def git(*args, cwd=LIVE):
    return run("git", *args, cwd=cwd)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def identity(unit):
    return run("systemctl", "show", unit, "--property=ActiveState,MainPID,ExecMainStartTimestamp")


def request(base, path, token=None, body=None):
    headers = {"Origin": ORIGIN, "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = None if body is None else json.dumps(body).encode()
    try:
        response = urlopen(Request(base + path, headers=headers, data=data), timeout=15)
    except HTTPError as error:
        response = error
    with response:
        raw = response.read()
        return response.status, response.headers.get("Access-Control-Allow-Origin"), json.loads(raw)


def validate(path, value):
    need(isinstance(value, dict), "Non-object monitoring response")
    required = {
        PATHS[0]: {"active_calls", "active_tenants", "failed_calls_24h", "total_calls_24h", "failed_call_rate_24h", "observed_at"},
        PATHS[1]: {"providers"},
        PATHS[2]: {"uptime_seconds", "memory_usage_mb", "memory_total_mb", "memory_percent", "cpu_usage_percent", "checked_at", "metrics_scope"},
        PATHS[3]: {"workers", "total_workers", "active_workers", "busy_workers", "checked_at", "scope"},
        PATHS[4]: {"queues", "total_pending", "total_processing", "checked_at"},
        PATHS[5]: {"connected", "latency_ms", "pool_size", "active_connections", "available_connections", "database_size_mb", "table_count", "last_check", "scope", "error"},
    }
    need(required[path] <= value.keys(), "Incomplete response: " + path)
    if path == PATHS[0]:
        for key in ("active_calls", "active_tenants", "failed_calls_24h", "total_calls_24h"):
            need(type(value[key]) is int and value[key] >= 0, "Invalid aggregate")
    elif path == PATHS[1]:
        need(bool(value["providers"]), "Missing dependencies")
        need(all({"name", "status", "latency_ms", "latency_display", "scope", "detail", "last_check"} <= row.keys()
                 for row in value["providers"]), "Incomplete dependency observation")
        pbx = next((row for row in value["providers"] if row["name"] == "Telephony"), {})
        need(pbx.get("status") in ("operational", "degraded") and pbx.get("last_check")
             and type(pbx.get("latency_ms")) is int, "Configured PBX/gateway observation did not pass")
    elif path == PATHS[3]:
        need(value["total_workers"] == 3 and value["active_workers"] == 3, "Worker heartbeat unavailable or stale")
        need(all({"age_seconds", "detail", "last_heartbeat"} <= row.keys() for row in value["workers"]), "Incomplete heartbeat")
    elif path == PATHS[4]:
        need(bool(value["queues"]) and all(row.get("status") == "measured" for row in value["queues"]), "Queue collection failed")
    elif path == PATHS[5]:
        need(value["connected"] is True and value["error"] is None
             and value["database_size_mb"] > 0 and value["pool_size"] >= 1, "Database collection failed")


PREFLIGHT = r'''
import asyncio, json, os, pathlib, sys
from dotenv import load_dotenv
load_dotenv('/opt/talky/backend/.env', override=True)
os.environ['TELEPHONY_ENABLED'] = 'false'
sys.path.insert(0, sys.argv[1])
import app, app.main
assert pathlib.Path(app.__file__).resolve().parent == pathlib.Path(sys.argv[1]) / 'app'
# Import all registered legacy incident/alert routes without running app lifespan.
from app.api.v1.endpoints.admin.health import alerts, incidents
import asyncpg
async def check():
    conn = await asyncpg.connect(os.environ['DATABASE_URL'], timeout=5)
    try:
        async with conn.transaction(readonly=True):
            await conn.execute("SET LOCAL statement_timeout = '5s'")
            from urllib.parse import urlsplit, unquote
            configured = urlsplit(os.environ['DATABASE_URL'])
            identity = await conn.fetchrow('SELECT current_database() AS db, current_user AS role')
            assert identity['db'] == unquote(configured.path.lstrip('/'))
            assert identity['role'] == unquote(configured.username)
            required = {'calls': {'status','created_at'}, 'tenants': {'status','subscription_status'},
                        'assistant_actions': {'status','completed_at','duration_ms'}}
            rows = await conn.fetch("SELECT table_name,column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=ANY($1::text[])", list(required))
            assert all(cols <= {r['column_name'] for r in rows if r['table_name']==table} for table,cols in required.items())
            assert await conn.fetchval('SELECT pg_database_size(current_database())') > 0
        from app.api.v1.endpoints.admin.health._pbx_probe import configured_pbx_health
        assert await configured_pbx_health() is True
        print('MONITORING_PREFLIGHT_OK')
    finally:
        await conn.close()
asyncio.run(check())
'''


def switch(target):
    temporary = UNIT.with_suffix(".service.monitoring-new")
    need(not temporary.exists() and not temporary.is_symlink(), "Temporary unit link already exists")
    temporary.symlink_to(target)
    os.replace(temporary, UNIT)
    run("systemctl", "daemon-reload")
    run("systemctl", "restart", "talky-api-web", timeout=330)


def wait_ready():
    for _ in range(45):
        try:
            status, _, _ = request(DIRECT, "/auth/me")
            if status == 401:
                return
        except Exception:
            pass
        time.sleep(1)
    raise RuntimeError("Dashboard worker did not become ready")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    need(os.geteuid() == 0, "Run as root")
    import fcntl
    lock = open("/run/talky-admin-monitoring-maintenance.lock", "w")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    need(re.fullmatch(r"[0-9a-f]{40}", args.commit), "A full immutable commit is required")
    candidate = LIVE / "releases" / args.commit
    need(Path(__file__).resolve() == candidate / "backend/deploy/admin-monitoring-maintenance.py", "Run the committed release script")
    need(git("rev-parse", "HEAD") == BASE, "Production base changed; re-review required")
    need(git("rev-parse", "HEAD", cwd=candidate) == args.commit, "Candidate commit mismatch")
    remote = git("ls-remote", "--exit-code", "origin", BRANCH).split()
    need(remote == [args.commit, BRANCH], "Candidate is not the pushed maintenance branch head")
    for checkout in (LIVE, candidate):
        need(not git("status", "--porcelain", "--untracked-files=no", cwd=checkout), "Tracked checkout is dirty")
    need(not git("ls-files", "--others", "--exclude-standard", "backend/app", cwd=candidate), "Untracked candidate application source")
    changed = set(git("diff", "--name-only", BASE, args.commit, cwd=candidate).splitlines())
    need(RUNTIME <= changed <= RUNTIME | OTHER, "Candidate exceeds the reviewed monitoring-only file set")
    previous_unit = LIVE / "runtime/admin-monitoring" / PREVIOUS_MONITORING / "talky-api-web.service"
    need(UNIT.is_symlink() and UNIT.readlink() == previous_unit, "Unexpected unit link; inspect before maintenance")
    original = UNIT.readlink()
    expected = git("show", BASE + ":backend/systemd/talky-api-web.service")
    needle = "ExecStart=/opt/talky/backend/venv/bin/uvicorn app.main:app "
    previous_expected = "# Monitoring maintenance commit " + PREVIOUS_MONITORING + "\n" + expected.replace(
        needle, needle + "--app-dir " + str(LIVE / "releases" / PREVIOUS_MONITORING / "backend") + " ")
    need(UNIT.read_text().strip() == previous_expected, "Dashboard unit drift")
    need(run("systemctl", "show", "talky-api-web", "--property=DropInPaths", "--value") == str(DROPIN)
         and digest(DROPIN) == DROPIN_SHA, "Unreviewed dashboard unit overrides")
    before = {service: identity(service) for service in SERVICES}
    need(all("ActiveState=active" in value for value in before.values()), "An existing call/worker service is not active")
    before_web = identity("talky-api-web")
    need("ActiveState=active" in before_web, "Existing dashboard API is not active")
    env_hash = digest(LIVE / "backend/.env")
    result = subprocess.run([str(LIVE / "backend/venv/bin/python"), "-c", PREFLIGHT, str(candidate / "backend")],
                            cwd=LIVE / "backend", env={"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", "TELEPHONY_ENABLED": "false"},
                            capture_output=True, text=True, timeout=60)
    need(result.returncode == 0 and "MONITORING_PREFLIGHT_OK" in result.stdout, "Import/schema preflight failed; no service changed")
    print("PASS: production base, source boundary, unit, imports, database columns and configured PBX/gateway read probes", flush=True)
    if args.check_only:
        return
    need(sys.stdin.isatty(), "Credentials require a private terminal")
    password = getpass.getpass("Admin password (hidden): ")
    runtime = LIVE / "runtime/admin-monitoring" / args.commit
    runtime.mkdir(parents=True, exist_ok=False, mode=0o700)
    receipt = {"commit": args.commit, "base": BASE, "before": before, "before_web": before_web,
               "original_unit": str(original), "state": "prepared", "reads": []}
    target = runtime / "talky-api-web.service"
    need(expected.count(needle) == 1, "Unexpected executable")
    target.write_text("# Monitoring maintenance commit " + args.commit + "\n" + expected.replace(
        needle, "Environment=APP_VERSION=" + args.commit[:8] + "\n" + needle + "--app-dir " + str(candidate / "backend") + " ") + "\n")
    target.chmod(0o644)
    run("systemd-analyze", "verify", str(target))
    activated, token = False, None
    try:
        activated = True  # Roll back even if restart fails after link replacement.
        switch(target)
        wait_ready()
        pid = int(run("systemctl", "show", "talky-api-web", "--property=MainPID", "--value"))
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
        need(str(candidate / "backend").encode() in cmdline and b"--app-dir" in cmdline, "Wrong running application source")
        need(Path(f"/proc/{pid}/cwd").resolve() == LIVE / "backend", "Runtime data directory changed")
        need(b"TELEPHONY_ENABLED=false" in Path(f"/proc/{pid}/environ").read_bytes().split(b"\0"), "Telephony role changed")
        status, cors, login = request(PUBLIC, "/auth/login", body={"email": "admin@gmail.com", "password": password})
        password = None
        token = login.get("access_token") if status == 200 else None
        need(status == 200 and cors == ORIGIN and login.get("role") == "platform_admin", "Normal admin login failed")
        need(bool(token), "Normal admin login returned no token")
        for base in (DIRECT, PUBLIC):
            for path in PATHS:
                status, cors, body = request(base, path, token)
                need(status == 200 and cors == ORIGIN, "Monitoring request failed: " + path)
                validate(path, body)
                if path == PATHS[2]:
                    need(body.get("version") == args.commit[:8], "Dashboard version does not identify the running release")
                receipt["reads"].append({"base": base, "path": path, "status": status, "response": body})
                anonymous, _, _ = request(base, path)
                need(anonymous in (401, 403), "Anonymous monitoring access allowed")
        after = {service: identity(service) for service in SERVICES}
        need(after == before, "A protected service identity changed")
        need(digest(LIVE / "backend/.env") == env_hash and git("rev-parse", "HEAD") == BASE, "Live source/configuration changed")
        receipt.update(state="accepted_api", after=after, after_web=identity("talky-api-web"), unit_sha256=digest(target))
        print("PASS: 12 authenticated monitoring reads, 12 anonymous rejections, unchanged call processes", flush=True)
    except BaseException as error:
        # Keep rollback alive if the SSH transport disappears during maintenance.
        for sig in (signal.SIGHUP, signal.SIGTERM):
            signal.signal(sig, signal.SIG_IGN)
        receipt.update(state="failed", error_type=type(error).__name__)
        if activated:
            switch(original)
            wait_ready()
            receipt["state"] = "rolled_back"
        raise
    finally:
        for sig in (signal.SIGHUP, signal.SIGTERM):
            signal.signal(sig, signal.SIG_IGN)
        if token:
            try:
                receipt["logout_status"] = request(PUBLIC, "/auth/logout", token, body={})[0]
                receipt["retired_token_status"] = request(PUBLIC, "/auth/me", token)[0]
            except Exception:
                receipt["logout_error"] = True
            if receipt.get("logout_status") != 200 or receipt.get("retired_token_status") != 401:
                receipt["session_cleanup_pending"] = True
        (runtime / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
        (runtime / "receipt.json").chmod(0o600)
    print("Receipt: " + str(runtime / "receipt.json"), flush=True)
    need(not receipt.get("session_cleanup_pending"), "Monitoring accepted but temporary session cleanup needs attention")


if __name__ == "__main__":
    def interrupted(signum, frame):
        raise InterruptedError("Maintenance interrupted; restoring dashboard service")
    for termination_signal in (signal.SIGHUP, signal.SIGTERM):
        signal.signal(termination_signal, interrupted)
    main()
