"""Core-infra step 3 (2026-10-07): memory limits, a designed Postgres
connection budget, swappiness, and the trunk-status updater as one
long-running loop instead of a fresh interpreter every 15 seconds.
"""
from __future__ import annotations

import asyncio
import importlib.util
import re
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]
SYSTEMD = BACKEND / "systemd"
POOLED_UNITS = (
    "talky-api.service",
    "talky-dialer-worker.service",
    "talky-voice-worker.service",
    "talky-reminder-worker.service",
)
MAX_CONNECTIONS = 100  # Postgres default, unchanged in deploy/infra/compose.prod.yml


def _unit(name: str) -> str:
    return (SYSTEMD / name).read_text(encoding="utf-8")


def _setting(text: str, key: str) -> str:
    match = re.search(rf"^{re.escape(key)}=(\S+)", text, re.M)
    assert match, key
    return match.group(1)


def test_every_pooled_service_has_memory_limits_and_an_explicit_pool():
    for name in POOLED_UNITS:
        text = _unit(name)
        assert _setting(text, "MemoryHigh"), name
        assert _setting(text, "MemoryMax"), name
        assert re.search(r"^Environment=PG_POOL_MAX_SIZE=\d+$", text, re.M), name
        assert re.search(r"^Environment=PG_POOL_MIN_SIZE=\d+$", text, re.M), name


def test_the_connection_budget_leaves_headroom_for_jobs_and_admin():
    total = sum(
        int(re.search(r"^Environment=PG_POOL_MAX_SIZE=(\d+)$", _unit(n), re.M).group(1))
        for n in POOLED_UNITS
    )
    # Was 4 x 20 = 80 by default. Leave room for migrations, pg_dump, the trunk
    # updater, cleanup and an operator's psql.
    assert total == 43
    assert total <= MAX_CONNECTIONS // 2


def test_swappiness_is_lowered_for_a_latency_sensitive_host():
    text = (BACKEND / "deploy" / "infra" / "sysctl" / "99-talky.conf").read_text(encoding="utf-8")
    assert re.search(r"^vm\.swappiness = 10$", text, re.M)


def test_trunk_status_is_a_long_running_service_not_a_timer():
    text = _unit("talky-trunk-status.service")
    assert "Type=simple" in text and "Type=oneshot" not in text
    assert "trunk_live_status_updater.py --loop 10" in text
    assert "Restart=always" in text and "StartLimitIntervalSec=0" in text
    assert "WantedBy=multi-user.target" in text
    assert "postgresql.service" not in text  # Postgres runs under Docker
    # The retired definition keeps installed symlinks resolvable during an
    # upgrade. It must be disabled; only the service is enabled afterward.
    installer = (SYSTEMD / "install-services.sh").read_text(encoding="utf-8")
    assert "systemctl disable --now talky-trunk-status.timer" in installer
    assert "systemctl enable talky-trunk-status.timer" not in installer


def _updater_module():
    path = BACKEND / "scripts" / "trunk_live_status_updater.py"
    spec = importlib.util.spec_from_file_location("trunk_live_status_updater_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Stop(BaseException):
    """Ends the otherwise infinite loop in the test."""


def test_the_loop_survives_a_failed_pass_and_keeps_going(monkeypatch):
    mod = _updater_module()
    calls = []

    async def fake_main():
        calls.append(len(calls))
        if len(calls) == 1:
            raise SystemExit(2)          # main()'s zero-row refusal
        if len(calls) == 2:
            raise RuntimeError("asterisk cli timed out")
        raise _Stop()

    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(mod, "main", fake_main)
    monkeypatch.setattr(mod.asyncio, "sleep", fake_sleep)
    with pytest.raises(_Stop):
        asyncio.run(mod.run_forever(15))
    assert len(calls) == 3                      # carried on after both failures
    assert len(slept) == 2 and all(1.0 <= s <= 15 for s in slept)


def test_loop_flag_parsing():
    mod = _updater_module()
    assert mod._loop_interval([]) is None
    assert mod._loop_interval(["--loop", "15"]) == 15.0
