"""Item 16 operational checks with only synthetic host/DB/Redis inputs."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import UUID

import fakeredis.aioredis
import pytest


BACKEND = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 6, 12, tzinfo=timezone.utc)
TENANT = "11111111-1111-4111-8111-111111111111"
TRUNK = "22222222-2222-4222-8222-222222222222"


@pytest.fixture
def watch():
    path = BACKEND / "scripts/healthwatch.py"
    assert path.is_file(), "F9-01 healthwatch has no bounded operational checks"
    spec = importlib.util.spec_from_file_location("qa_healthwatch", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
async def redis():
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield client
    await client.aclose()


@pytest.mark.parametrize("disk,memory,expected", [(85, 90, "ok"), (85.01, 90.01, "alert")])
def test_disk_and_available_memory_thresholds_are_strict(watch, monkeypatch, tmp_path, disk, memory, expected):
    monkeypatch.setattr(watch.shutil, "disk_usage", lambda _: NS(total=10000, used=int(disk * 100), free=10000-int(disk * 100)))
    meminfo = tmp_path / "meminfo"
    meminfo.write_text(f"MemTotal: 100000 kB\nMemAvailable: {round(100000-memory*1000)} kB\nMemFree: 0 kB\n")
    result = watch.check_resources(disk_path=tmp_path, meminfo_path=meminfo)
    assert result["disk"]["status"] == expected
    assert result["memory"]["status"] == expected
    assert result["memory"]["used_percent"] == pytest.approx(memory)


@pytest.mark.parametrize("text", ["", "MemTotal: 0 kB\nMemAvailable: 0 kB", "MemTotal: 100 kB\nMemAvailable: NaN kB"])
def test_missing_or_invalid_memory_evidence_never_reports_healthy(watch, monkeypatch, tmp_path, text):
    monkeypatch.setattr(watch.shutil, "disk_usage", lambda _: NS(total=100, used=10, free=90))
    meminfo = tmp_path / "meminfo"
    meminfo.write_text(text)
    assert watch.check_resources(disk_path=tmp_path, meminfo_path=meminfo)["memory"]["status"] == "unavailable"


@pytest.mark.parametrize("synced,offset,expected", [("yes", "0.2", "ok"), ("yes", "1.0", "ok"),
                                                   ("yes", "1.001", "alert"), ("no", "0.0", "alert")])
def test_ntp_sync_and_absolute_offset_are_both_checked(watch, monkeypatch, synced, offset, expected):
    commands = []

    def run(args, **kwargs):
        commands.append((args, kwargs))
        assert 0 < kwargs["timeout"] <= 5 and kwargs.get("shell", False) is False
        assert kwargs["env"]["LC_ALL"] == "C"
        stdout = synced if args[0] == "timedatectl" else f"System time     : {offset} seconds slow of NTP time\nLeap status     : Normal\n"
        return NS(returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr(watch.subprocess, "run", run)
    assert watch.check_ntp()["status"] == expected
    assert commands[0][0][0] == "timedatectl"


def test_timesyncd_offset_fallback_understands_microseconds(watch, monkeypatch):
    def run(args, **kwargs):
        if args[0] == "chronyc":
            raise FileNotFoundError("synthetic missing chrony")
        return NS(returncode=0, stdout="yes" if "show" in args else "       Offset: -316us\n", stderr="")
    monkeypatch.setattr(watch.subprocess, "run", run)
    result = watch.check_ntp()
    assert result["status"] == "ok" and result["offset_seconds"] == pytest.approx(-0.000316)


def test_clock_command_timeout_is_unavailable_without_echoing_command_output(watch, monkeypatch):
    def run(*args, **kwargs):
        raise subprocess.TimeoutExpired("synthetic-private-host", 2)
    monkeypatch.setattr(watch.subprocess, "run", run)
    result = watch.check_ntp()
    assert result["status"] == "unavailable"
    assert "synthetic-private-host" not in json.dumps(result)


@pytest.mark.parametrize("days,expected", [(14, "ok"), (13.99, "alert"), (-1, "alert")])
def test_local_certificate_expiry_has_fourteen_day_boundary(watch, monkeypatch, tmp_path, days, expected):
    certificate = tmp_path / "synthetic-cert.pem"
    certificate.write_text("synthetic fixture, openssl is mocked")
    expires = (NOW + timedelta(days=days)).strftime("%b %d %H:%M:%S %Y GMT")

    def run(args, **kwargs):
        assert args[0] == "openssl" and "x509" in args and "-in" in args
        assert "s_client" not in args, "Certificate check must not contact a remote endpoint"
        assert str(certificate) in args and 0 < kwargs["timeout"] <= 5
        return NS(returncode=0, stdout=f"notAfter={expires}\n", stderr="")

    monkeypatch.setattr(watch.subprocess, "run", run)
    assert watch.check_certificates([certificate], now=NOW)["status"] == expected


def test_unconfigured_certificate_monitor_is_not_reported_as_healthy(watch):
    assert watch.check_certificates([], now=NOW)["status"] == "unconfigured"


def trunk(*, status="rejected", register=True, direction="both", age=0):
    return {"id": TRUNK, "tenant_id": TENANT, "is_active": True, "deleted_at": None,
            "direction": direction, "metadata": {"register": register},
            "live_registration_status": status,
            "live_status_checked_at": NOW-timedelta(seconds=age)}


async def observe(watch, redis, row, elapsed):
    observed = NOW + timedelta(seconds=elapsed)
    row = {**row, "live_status_checked_at": observed}
    return await watch.check_trunks([row], redis, now=observed)


async def test_trunk_alert_requires_five_continuous_minutes_and_recovery_resets_it(watch, redis):
    row = trunk()
    for elapsed in (0, 120, 240, 299):
        assert (await observe(watch, redis, row, elapsed))["status"] == "pending"
    assert (await observe(watch, redis, row, 300))["status"] == "alert"
    assert (await observe(watch, redis, trunk(status="registered"), 301))["status"] == "ok"
    assert (await observe(watch, redis, row, 302))["status"] == "pending"


@pytest.mark.parametrize("status,direction", [("reachable", "outbound"), ("loaded", "inbound"), ("registered", "both")])
async def test_healthy_ip_auth_trunks_do_not_require_registration(watch, redis, status, direction):
    assert (await observe(watch, redis, trunk(status=status, register=False, direction=direction), 0))["status"] == "ok"


async def test_monitor_gap_and_stale_runtime_data_are_not_five_minute_registration_proof(watch, redis):
    assert (await observe(watch, redis, trunk(), 0))["status"] == "pending"
    assert (await observe(watch, redis, trunk(), 601))["status"] == "pending"
    stale = await watch.check_trunks([trunk(age=61)], redis, now=NOW)
    assert stale["status"] == "unavailable"
    assert "stale" in stale["reason"]


async def test_trunk_state_store_failure_is_not_reported_as_healthy(watch):
    cache = NS(eval=AsyncMock(side_effect=ConnectionError("synthetic redis credential")))
    result = await watch.check_trunks([trunk()], cache, now=NOW)
    assert result["status"] == "unavailable"
    assert "credential" not in json.dumps(result)


@pytest.mark.parametrize("used,limit,expected", [(90, 100, "ok"), (91, 100, "alert"), (10, 0, "unavailable")])
async def test_redis_capacity_uses_configured_limit_and_never_treats_unbounded_as_safe(watch, used, limit, expected):
    cache = NS(info=AsyncMock(return_value={"used_memory": used, "maxmemory": limit}))
    assert (await watch.check_redis_capacity(cache))["status"] == expected
    cache.info.assert_awaited_once_with("memory")


async def test_hanging_redis_probe_is_cancelled_within_the_probe_budget(watch, monkeypatch):
    cancelled = asyncio.Event()
    async def hang(*args):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
    monkeypatch.setattr(watch, "IO_TIMEOUT_SECONDS", 0.01)
    result = await asyncio.wait_for(watch.check_redis_capacity(NS(info=hang)), 0.5)
    assert result["status"] == "unavailable" and cancelled.is_set()


@pytest.mark.parametrize("age,expected", [(179, "ok"), (180, "alert"), (None, "alert")])
async def test_worker_signal_uses_actual_registered_heartbeat_age(watch, age, expected):
    stamp = str(NOW.timestamp()-age) if age is not None else None
    cache = NS(get=AsyncMock(return_value=stamp))
    result = await watch.check_workers(cache, now=NOW)
    assert result["status"] == expected
    assert {call.args[0] for call in cache.get.call_args_list} == {
        "dialer:heartbeat_ts", "voice:heartbeat_ts", "reminder:heartbeat_ts"}


@pytest.mark.parametrize("observations,expected", [(None, "unavailable"), ([], "no_recent_evidence"),
    ([NS(component="llm", provider="groq", outcome="failed", error_category="rate_limit")], "alert"),
    ([NS(component="stt", provider="deepgram", outcome="recognized", error_category="none")], "ok")])
async def test_last_provider_observations_are_never_fabricated_as_an_error_rate(watch, monkeypatch, observations, expected):
    monkeypatch.setattr(watch, "read_recent", AsyncMock(return_value=observations))
    report = await watch.check_provider_observations(object())
    assert report["provider_observations"]["status"] == expected
    assert report["provider_error_rate"]["status"] == "unavailable"
    assert "no_request_denominator" in report["provider_error_rate"]["reason"]
    assert "error_rate" not in report["provider_observations"]


async def test_database_signals_count_payment_reviews_and_scope_every_trunk_read(watch, monkeypatch, redis):
    reads = []

    class Connection:
        def __init__(self, tenant):
            self.tenant = tenant

        async def fetchval(self, query, *args):
            reads.append((self.tenant, query, args))
            assert "processed_webhook_events" in query and "needs_review" in query
            assert "event_payload" not in query
            return 2

        async def fetch(self, query, *args):
            reads.append((self.tenant, query, args))
            if "FROM tenants" in query:
                assert "LIMIT" in query
                return [{"id": UUID(TENANT)}]
            assert "tenant_sip_trunks" in query
            assert "tenant_id=" in query.replace(" ", "") and "LIMIT" in query
            assert self.tenant == TENANT and any(str(arg) == TENANT for arg in args)
            assert "auth_password" not in query and "auth_username" not in query
            return [trunk(status="registered")]

    @asynccontextmanager
    async def acquire(pool, tenant_id, **kwargs):
        yield Connection(str(tenant_id) if tenant_id else None)

    monkeypatch.setattr(watch, "acquire_with_tenant", acquire)
    report = await watch.check_database(object(), redis, now=NOW, max_tenants=10)
    assert report["payments"]["status"] == "alert" and report["payments"]["needs_review"] == 2
    assert report["trunks"]["status"] == "ok"
    assert len(reads) == 3


async def test_database_failure_is_explicit_and_does_not_echo_dsn(watch, monkeypatch, redis):
    @asynccontextmanager
    async def unavailable(*args, **kwargs):
        raise RuntimeError("postgresql://synthetic-secret@example.invalid/db")
        yield
    monkeypatch.setattr(watch, "acquire_with_tenant", unavailable)
    report = await watch.check_database(object(), redis, now=NOW, max_tenants=10)
    assert report["payments"]["status"] == report["trunks"]["status"] == "unavailable"
    assert "synthetic-secret" not in json.dumps(report)


@pytest.mark.parametrize("limit", [0, -1, 10001])
async def test_database_inventory_limit_is_validated_before_connecting(watch, limit):
    with pytest.raises(ValueError):
        await watch.check_database(None, None, now=NOW, max_tenants=limit)


def test_extended_monitoring_is_on_the_actual_timer_service_path():
    service = (BACKEND / "systemd/talky-healthwatch.service").read_text()
    shell = (BACKEND / "deploy/healthwatch.sh").read_text()
    assert "healthwatch.sh --extended" in service, "Deployed timer still checks only worker heartbeats"
    assert "scripts/healthwatch.py" in shell
    assert "TimeoutStartSec=" in service
    assert "EnvironmentFile=" in service


@pytest.mark.parametrize("worker_code,extended_code,expected", [(200, 0, 0), (200, 1, 1), (503, 0, 1), (503, 1, 1)])
def test_real_shell_runs_additional_checks_even_if_worker_probe_fails(tmp_path, worker_code, extended_code, expected):
    from tests.unit.test_bh_ops_scripts_deploy import _launch, _real_bash, _write
    if _real_bash() is None:
        pytest.skip("no POSIX bash available")
    shim = tmp_path / "shim"
    shim.mkdir()
    operations = tmp_path / "operations.log"
    _write(shim / "curl", '#!/usr/bin/env bash\nprintf "{}\\n%s\\n" "$WORKER_CODE"\n')
    _write(shim / "sleep", '#!/usr/bin/env bash\nexit 94\n')
    _write(shim / "probe", '#!/usr/bin/env bash\nprintf "extended %s\\n" "$1" >> "$OPERATIONS"\nexit "$EXTENDED_CODE"\n')
    result = _launch('TALKY_HEALTHWATCH_PYTHON="$SHIM_DIR/probe" bash deploy/healthwatch.sh --extended',
                     dict(os.environ, OPERATIONS=str(operations), WORKER_CODE=str(worker_code),
                          EXTENDED_CODE=str(extended_code), TALKY_HEALTHWATCH_RETRIES="1",
                          TALKY_PROD_HOST="nobody@example.invalid", TALKY_PROD_KEY="fake-key"), BACKEND, shim)
    assert result.returncode == expected, result.stdout + result.stderr
    lines = operations.read_text().splitlines()
    assert len(lines) == 1 and lines[0].endswith("scripts/healthwatch.py")
