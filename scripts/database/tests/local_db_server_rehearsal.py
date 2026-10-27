#!/usr/bin/env python3
"""Explicit, synthetic local test of the rendered dedicated-DB-server config.

Creates a throwaway CA + server certificate, one disposable loopback cluster,
installs the rendered talklee.conf/pg_hba.conf and proves TLS-only access,
verify-full, the CIDR allow-list and wrong-CA rejection. Never touches any
existing server, DATABASE_URL, or production data.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

RENDER = Path(__file__).resolve().parents[1] / "db_server" / "render_config.py"
SECRET = "rehearsal-" + os.urandom(12).hex()  # synthetic, lives only in a 0600 temp file
USER, DB = "talklee_app", "talkyai"


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def run(argv, check=True, env=None, timeout=120):
    flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
    base = {k: v for k, v in os.environ.items() if not k.startswith("PG") and k not in {"DATABASE_URL", "READ_DATABASE_URL"}}
    base.update(env or {})
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        process = subprocess.Popen([str(a) for a in argv], stdin=subprocess.DEVNULL, stdout=out, stderr=err, env=base, **flags)
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
            else:
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=15)
            raise RuntimeError(f"{Path(str(argv[0])).name} timed out; process tree terminated")
        out.seek(0)
        err.seek(0)
        result = subprocess.CompletedProcess(process.args, process.returncode,
                                             out.read().decode("utf-8", "replace"), err.read().decode("utf-8", "replace"))
    if check and result.returncode:
        raise RuntimeError(f"{Path(str(argv[0])).name} failed: {result.stdout[-800:]}{result.stderr[-1500:]}")
    return result


def make_certs(openssl, directory, label, san_names=True):
    ca_key, ca_crt = directory / f"{label}-ca.key", directory / f"{label}-ca.crt"
    run([openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", ca_key, "-out", ca_crt,
         "-days", "2", "-subj", f"/CN=Rehearsal CA {label}"])
    if not san_names:
        return ca_crt, None, None
    key, csr, crt, ext = directory / "server.key", directory / "server.csr", directory / "server.crt", directory / "san.ext"
    ext.write_text("subjectAltName=DNS:localhost,IP:127.0.0.1\nbasicConstraints=CA:FALSE\n", encoding="utf-8")
    run([openssl, "req", "-newkey", "rsa:2048", "-nodes", "-keyout", key, "-out", csr, "-subj", "/CN=localhost"])
    run([openssl, "x509", "-req", "-in", csr, "-CA", ca_crt, "-CAkey", ca_key, "-CAcreateserial",
         "-out", crt, "-days", "2", "-extfile", ext])
    return ca_crt, crt, key


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bin-dir", type=Path, required=True)
    parser.add_argument("--run", action="store_true", required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    exe = lambda n: args.bin_dir / (n + (".exe" if os.name == "nt" else ""))
    openssl = shutil.which("openssl") or ("C:/Program Files/Git/mingw64/bin/openssl.exe" if os.name == "nt" else None)
    if not openssl or not Path(openssl).exists():
        raise SystemExit("openssl not found")
    if args.receipt.exists():
        raise SystemExit("receipt already exists: " + str(args.receipt))
    root = Path(__file__).resolve().parents[3]
    output = root / "test-output"
    output.mkdir(exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="db-server-rehearsal-", dir=output)).resolve()
    data, cfg = work / "data", work / "cfg"
    port = free_port()
    started = False
    checks = []
    passfile = work / "pgpass"
    passfile.write_text(f"*:{port}:*:{USER}:{SECRET}\n", encoding="utf-8")
    passfile.chmod(0o600)

    def psql(sslmode, root_cert=None, host="localhost", hostaddr="127.0.0.1", sql="SELECT 1;", database=DB):
        conn = f"host={host} hostaddr={hostaddr} port={port} dbname={database} user={USER} sslmode={sslmode} connect_timeout=10"
        if root_cert:
            conn += f" sslrootcert='{Path(root_cert).as_posix()}'"
        return run([exe("psql"), "-X", "-qAt", "--no-password", "-v", "ON_ERROR_STOP=1", "--dbname", conn, "-c", sql],
                   check=False, env={"PGPASSFILE": str(passfile)})

    def render(allow, force=False):
        cmd = [sys.executable, RENDER, "--listen-address", "127.0.0.1", "--allow", allow,
               "--ssl-cert-file", (work / "server.crt").as_posix(), "--ssl-key-file", (work / "server.key").as_posix(),
               "--ssl-ca-file", ca.as_posix(), "--database", DB, "--port", str(port), "--memory-mb", "2048", "--out-dir", cfg]
        run(cmd + (["--force"] if force else []))

    def reload_cluster():
        run([exe("pg_ctl"), "-D", data, "-l", work / "server.log", "-w", "restart"])

    try:
        ca, crt, key = make_certs(openssl, work, "good")
        wrong_ca, _, _ = make_certs(openssl, work, "wrong", san_names=False)
        # Cluster bootstrap: trust only for the local setup phase, replaced below.
        run([exe("initdb"), "-D", data, "-U", "bootstrap_admin", "--auth=trust", "--encoding=UTF8", "--locale=C"])
        started = True
        run([exe("pg_ctl"), "-D", data, "-l", work / "server.log", "-o", f"-h 127.0.0.1 -p {port}", "-w", "start"])
        setup = work / "setup.sql"
        setup.write_text(f"SET password_encryption='scram-sha-256'; CREATE ROLE {USER} LOGIN PASSWORD '{SECRET}';\n"
                         f"CREATE DATABASE {DB} OWNER {USER};\n", encoding="utf-8")
        setup.chmod(0o600)
        run([exe("psql"), "-X", "-q", "--no-password", "-v", "ON_ERROR_STOP=1", "--dbname",
             f"host=127.0.0.1 port={port} dbname=postgres user=bootstrap_admin sslmode=disable", "-f", setup])
        setup.unlink()
        # Install rendered configuration.
        render("127.0.0.1/32")
        conf = data / "postgresql.conf"
        with conf.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(f"\ninclude '{(cfg / 'talklee.conf').as_posix()}'\n")
        shutil.copyfile(cfg / "pg_hba.conf", data / "pg_hba.conf")
        reload_cluster()
        checks.append("rendered talklee.conf included and rendered pg_hba.conf installed; cluster restarted with ssl=on")

        r = psql("disable")
        assert r.returncode and "pg_hba.conf rejects" in r.stderr and "no encryption" in r.stderr, r.stderr
        checks.append("(a) sslmode=disable rejected by hostnossl reject rule")

        r = psql("verify-full", ca, sql="SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid();")
        assert r.returncode == 0 and r.stdout.strip() == "t", r.stdout + r.stderr
        checks.append("(b) sslmode=verify-full with the CA succeeds as the SCRAM role; pg_stat_ssl.ssl = true")
        r = psql("verify-full", ca, sql="SELECT rolpassword LIKE 'SCRAM-SHA-256$%' FROM pg_authid WHERE rolname=current_user;")
        # talklee_app is not a superuser: reading pg_authid must be denied, which is the expected outcome.
        assert r.returncode != 0 and "permission denied" in r.stderr, r.stderr
        checks.append("application role is unprivileged (pg_authid read denied)")

        r = psql("verify-full", ca, host="localhost", hostaddr="::1")
        assert r.returncode and ("could not connect" in r.stderr or "Connection refused" in r.stderr or "connection to server" in r.stderr), r.stderr
        checks.append("(c1) ::1 (IPv6) not reachable: server listens on 127.0.0.1 only")

        r = psql("verify-full", wrong_ca)
        assert r.returncode and "certificate verify failed" in r.stderr, r.stderr
        checks.append("(d) wrong CA fails verify-full: certificate verify failed")
        r = psql("verify-full", ca, host="example.invalid")
        assert r.returncode and ("does not match" in r.stderr or "verify failed" in r.stderr or "not match host name" in r.stderr), r.stderr
        checks.append("(d2) host name not in certificate SAN fails verify-full")

        # (c2) Allow-list: only 127.0.0.2 may connect; 127.0.0.1 must now be rejected by pg_hba.
        render("127.0.0.2/32", force=True)
        shutil.copyfile(cfg / "pg_hba.conf", data / "pg_hba.conf")
        run([exe("pg_ctl"), "-D", data, "-w", "reload"])
        r = psql("verify-full", ca)
        assert r.returncode and "pg_hba.conf rejects connection" in r.stderr, r.stderr
        checks.append("(c2) client 127.0.0.1 outside allow-list 127.0.0.2/32 rejected by pg_hba.conf")
        hba = (data / "pg_hba.conf").read_text(encoding="utf-8")
        assert "trust" not in hba
        checks.append("installed pg_hba.conf contains no trust method")

        payload = {"status": "passed", "completed_at": datetime.now(timezone.utc).isoformat(), "checks": checks,
                   "postgres_client": run([exe("psql"), "--version"]).stdout.strip(), "production_accessed": False,
                   "scope": "synthetic loopback rehearsal of rendered TLS/pg_hba config; not a real network, ufw or Ubuntu test"}
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        args.receipt.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(json.dumps(payload, indent=2))
        return 0
    finally:
        errors = []
        if started and (data / "postmaster.pid").exists():
            try:
                run([exe("pg_ctl"), "-D", data, "-m", "immediate", "-w", "stop"])
            except Exception as exc:
                errors.append(str(exc))
        if errors:
            raise RuntimeError("owned cluster cleanup failed; retained " + str(work) + ": " + "; ".join(errors))
        if work.parent == output.resolve() and work.name.startswith("db-server-rehearsal-"):
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
