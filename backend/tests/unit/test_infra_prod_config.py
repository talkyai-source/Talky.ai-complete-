"""Production infrastructure definition (core-infra review 2026-10-07).

These files are configuration, so the guards check the properties that
production depends on: the data volumes and container names are the existing
ones, images are pinned, Redis can never drop queued jobs, Postgres is tuned,
and the Python services wait for the database and never stop restarting.
"""
from __future__ import annotations

from pathlib import Path

import yaml

INFRA = Path(__file__).resolve().parents[2] / "deploy" / "infra"


def _compose() -> dict:
    return yaml.safe_load((INFRA / "compose.prod.yml").read_text(encoding="utf-8"))


def test_only_the_data_services_are_defined():
    services = _compose()["services"]
    assert sorted(services) == ["postgres", "redis"]  # no backend: systemd runs it


def test_existing_container_names_and_volumes_are_adopted_not_replaced():
    doc = _compose()
    assert doc["services"]["postgres"]["container_name"] == "talky-postgres-1"
    assert doc["services"]["redis"]["container_name"] == "talky-redis-1"
    assert doc["volumes"]["postgres_data"] == {"external": True, "name": "talky_postgres_data"}
    assert doc["volumes"]["redis_data"] == {"external": True, "name": "talky_redis_data"}


def test_images_are_pinned_by_digest():
    for svc in _compose()["services"].values():
        assert "@sha256:" in svc["image"], svc["image"]


def test_ports_are_bound_to_localhost_only():
    for svc in _compose()["services"].values():
        assert all(p.startswith("127.0.0.1:") for p in svc["ports"]), svc["ports"]


def test_redis_has_a_cap_and_never_evicts():
    cmd = _compose()["services"]["redis"]["command"]
    assert cmd[cmd.index("--maxmemory-policy") + 1] == "noeviction"
    assert cmd[cmd.index("--maxmemory") + 1] == "256mb"
    assert "allkeys-lru" not in cmd
    assert cmd[cmd.index("--appendonly") + 1] == "yes"


def test_postgres_is_tuned_and_shuts_down_cleanly():
    svc = _compose()["services"]["postgres"]
    flags = {c.split("=", 1)[0]: c.split("=", 1)[1].split()[0] for c in svc["command"] if "=" in c}
    assert flags["shared_buffers"] == "512MB"
    assert flags["random_page_cost"] == "1.1"
    assert flags["idle_in_transaction_session_timeout"] == "300s"
    assert flags["log_min_duration_statement"] == "500"
    assert svc["shm_size"] == "256m"
    assert svc["stop_grace_period"] == "60s"


def test_both_services_have_memory_limits():
    services = _compose()["services"]
    assert services["postgres"]["mem_limit"] == "1536m"
    assert services["redis"]["mem_limit"] == "384m"


def test_python_services_wait_for_the_database_and_never_give_up():
    text = (INFRA / "systemd" / "talky-deps.conf").read_text(encoding="utf-8")
    assert "After=docker.service" in text
    assert "StartLimitIntervalSec=0" in text
    assert "ExecStartPre=/opt/talky-infra/bin/wait-for-deps" in text
    assert "Restart=always" in text


def test_gateway_and_nginx_come_back_after_a_crash():
    gateway = (INFRA / "systemd" / "talky-restart.conf").read_text(encoding="utf-8")
    nginx = (INFRA / "systemd" / "nginx-restart.conf").read_text(encoding="utf-8")
    assert "Restart=always" in gateway and "StartLimitIntervalSec=0" in gateway
    assert "Restart=on-failure" in nginx and "StartLimitIntervalSec=0" in nginx


def test_wait_for_deps_checks_both_containers_with_a_deadline():
    text = (INFRA / "wait-for-deps").read_text(encoding="utf-8")
    assert "talky-postgres-1" in text and "talky-redis-1" in text
    assert "WAIT_SECONDS" in text and "exit 1" in text
    assert "\r" not in text  # runs under /bin/sh on Linux


# --- deliverable 4: the database may live on a separate server -------------
import os as _os
import shutil as _shutil
import subprocess as _subprocess

import pytest as _pytest


def _run_wait_for_deps(tmp_path, database_url, postgres_health="healthy", redis_health="healthy"):
    """Run the real script under sh with a stub `docker` that reports health."""
    sh, python3 = _shutil.which("sh"), _shutil.which("python3")
    if not sh or not python3:
        _pytest.skip("needs sh and python3 on PATH")
    stub = tmp_path / "bin"
    stub.mkdir(parents=True)
    docker = stub / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        f'case "$*" in *talky-postgres-1*) echo {postgres_health};; *talky-redis-1*) echo {redis_health};; esac\n',
        encoding="utf-8", newline="\n")
    docker.chmod(0o755)
    env = dict(_os.environ, PATH=str(stub) + _os.pathsep + _os.environ.get("PATH", ""),
               DATABASE_URL=database_url, WAIT_SECONDS="1")
    script = tmp_path / "wait-for-deps"
    script.write_bytes((INFRA / "wait-for-deps").read_bytes())
    return _subprocess.run([sh, str(script)], env=env, capture_output=True, text=True, timeout=60)


def test_wait_for_deps_uses_the_local_container_for_a_loopback_database(tmp_path):
    ok = _run_wait_for_deps(tmp_path, "postgresql://talkyai:x@127.0.0.1:5432/talkyai")
    assert ok.returncode == 0, ok.stderr
    down = _run_wait_for_deps(tmp_path / "b", "postgresql://talkyai:x@localhost/talkyai", postgres_health="starting")
    assert down.returncode == 1 and "postgres=starting" in down.stderr


def test_wait_for_deps_waits_for_a_separate_database_server_not_a_container(tmp_path):
    # The local container is irrelevant (even unhealthy) once the database is
    # remote; an unreachable server is reported by host and port, password-free.
    result = _run_wait_for_deps(
        tmp_path, "postgresql://talklee_app:s3cret@db.invalid:6543/talkyai?sslmode=verify-full",
        postgres_health="unhealthy")
    assert result.returncode == 1
    assert "database db.invalid:6543 not accepting connections" in result.stderr
    assert "postgres=" not in result.stderr and "s3cret" not in result.stderr
