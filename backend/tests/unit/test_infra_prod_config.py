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
