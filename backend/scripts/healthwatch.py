#!/usr/bin/env python3
"""Bounded host/dependency observations for the existing healthwatch timer.

Reads process configuration only. Never probes a provider, changes a database
row, repairs the clock, or reads backup contents. Missing evidence is explicit.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from uuid import UUID

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.v1.endpoints.health import _WORKER_HEARTBEAT_KEYS, read_worker_heartbeats
from app.core.db_utils import acquire_with_tenant
from app.domain.services.telephony.trunk_runtime import evaluate_trunk_runtime
from app.infrastructure.metrics.provider_observations import read_recent

IO_TIMEOUT_SECONDS = 0.5
FAILING_KEY = "talky:healthwatch:failing"  # read by app.core.operational_alerts
_MAX_TRUNKS = 1000
_UNAVAILABLE = {"status": "unavailable", "reason": "observation_unavailable"}
_TRUNK_STATE = """
local now = tonumber(ARGV[1])
if ARGV[2] ~= 'unhealthy' then redis.call('DEL', KEYS[1]); return 0 end
local first = tonumber(redis.call('HGET', KEYS[1], 'first'))
local last = tonumber(redis.call('HGET', KEYS[1], 'last'))
if not first or not last or first > now or last > now or now-last > 300 then first=now end
redis.call('HSET', KEYS[1], 'first', first, 'last', now)
redis.call('EXPIRE', KEYS[1], 900)
if now-first >= 300 then return 1 end
return 0
"""


def check_resources(*, disk_path="/", meminfo_path=Path("/proc/meminfo")):
    result = {"disk": dict(_UNAVAILABLE), "memory": dict(_UNAVAILABLE)}
    try:
        disk = shutil.disk_usage(disk_path)
        if disk.total <= 0 or not 0 <= disk.used <= disk.total:
            raise ValueError("Invalid disk evidence")
        used = disk.used / disk.total * 100
        result["disk"] = {"status": "alert" if used > 85 else "ok", "used_percent": used}
    except Exception:
        pass
    try:
        with Path(meminfo_path).open(encoding="ascii") as stream:
            values = dict(re.findall(r"^(MemTotal|MemAvailable):\s+(\d+) kB$", stream.read(16384), re.M))
        total, available = int(values["MemTotal"]), int(values["MemAvailable"])
        if total <= 0 or not 0 <= available <= total:
            raise ValueError("Invalid memory evidence")
        used = (total-available) / total * 100
        result["memory"] = {"status": "alert" if used > 90 else "ok", "used_percent": used}
    except Exception:
        pass
    return result


def _command(args):
    return subprocess.run(args, capture_output=True, text=True, timeout=2,
                          env=dict(os.environ, LC_ALL="C"), shell=False)


def check_ntp():
    try:
        sync = _command(["timedatectl", "show", "--property=NTPSynchronized", "--value"])
        if sync.returncode != 0 or sync.stdout.strip() not in {"yes", "no"}:
            return dict(_UNAVAILABLE)
        if sync.stdout.strip() == "no":
            return {"status": "alert", "reason": "ntp_not_synchronised"}
        offset = None
        try:
            tracking = _command(["chronyc", "tracking"])
            match = re.search(r"System time\s*:\s*([+-]?[0-9.]+) seconds (fast|slow)", tracking.stdout)
            if tracking.returncode == 0 and match and re.search(r"Leap status\s*:\s*Normal", tracking.stdout):
                offset = float(match[1]) * (-1 if match[2] == "slow" else 1)
        except (OSError, subprocess.SubprocessError):
            pass
        if offset is None:
            tracking = _command(["timedatectl", "timesync-status", "--no-pager"])
            match = re.search(r"Offset:\s*([+-]?[0-9.]+)\s*(us|µs|ms|s)\b", tracking.stdout)
            if tracking.returncode != 0 or not match:
                return {"status": "unavailable", "reason": "ntp_offset_unavailable"}
            offset = float(match[1]) * {"us": 0.000001, "µs": 0.000001, "ms": 0.001, "s": 1}[match[2]]
        if not math.isfinite(offset):
            return dict(_UNAVAILABLE)
        return {"status": "alert" if abs(offset) > 1 else "ok", "offset_seconds": offset}
    except Exception:
        return dict(_UNAVAILABLE)


def check_certificates(paths, *, now):
    if not paths:
        return {"status": "unconfigured", "reason": "certificate_paths_not_configured"}
    if len(paths) > 8:
        return {"status": "unavailable", "reason": "certificate_limit_exceeded"}
    earliest = None
    try:
        for path in paths:
            result = _command(["openssl", "x509", "-in", str(path), "-noout", "-enddate"])
            if result.returncode != 0 or not result.stdout.startswith("notAfter="):
                raise ValueError("Invalid certificate evidence")
            expires = datetime.strptime(result.stdout.strip().removeprefix("notAfter="), "%b %d %H:%M:%S %Y GMT").replace(tzinfo=timezone.utc)
            remaining = (expires-now).total_seconds()
            earliest = remaining if earliest is None else min(earliest, remaining)
        return {"status": "alert" if earliest < 14*86400 else "ok", "minimum_days_remaining": earliest/86400}
    except Exception:
        return dict(_UNAVAILABLE)


async def check_redis_capacity(redis):
    try:
        info = await asyncio.wait_for(redis.info("memory"), IO_TIMEOUT_SECONDS)
        used, limit = int(info["used_memory"]), int(info["maxmemory"])
        if used < 0 or limit <= 0:
            raise ValueError("Redis has no usable capacity bound")
        return {"status": "alert" if used/limit > 0.90 else "ok", "used_bytes": used, "limit_bytes": limit}
    except Exception:
        return dict(_UNAVAILABLE)


async def check_workers(redis, *, now):
    workers = await read_worker_heartbeats(redis, now=now.timestamp())
    return {"status": "ok" if all(row["healthy"] for row in workers) else "alert", "workers": workers}


async def check_provider_observations(redis):
    records = await read_recent(redis)
    if records is None:
        observation = dict(_UNAVAILABLE)
    elif not records:
        observation = {"status": "no_recent_evidence"}
    else:
        failed = sum(record.outcome == "failed" for record in records)
        observation = {"status": "alert" if failed else "ok", "failed_last_observations": failed,
                       "observed_scopes": len(records)}
    return {"provider_observations": observation,
            "provider_error_rate": {"status": "unavailable", "reason": "no_request_denominator"}}


async def check_trunks(rows, redis, *, now):
    if len(rows) > _MAX_TRUNKS:
        return {"status": "unavailable", "reason": "trunk_limit_exceeded"}
    pending, alerts = 0, 0
    unavailable = False
    try:
        for row in rows:
            # Inbound-only IP-auth trunks need loaded evidence; outbound/both
            # require a qualified contact or a valid registration.
            readiness = evaluate_trunk_runtime(row, now=now, require_inbound=row.get("direction") == "inbound")
            stale = readiness.code in {"status_missing", "status_stale", "status_clock_skew"}
            ignored = not row.get("is_active") or row.get("deleted_at") is not None
            mode = "clear" if readiness.ready or stale or ignored else "unhealthy"
            key = f"talky:healthwatch:trunk:{UUID(str(row['tenant_id']))}:{UUID(str(row['id']))}"
            result = await asyncio.wait_for(redis.eval(_TRUNK_STATE, 1, key, now.timestamp(), mode), IO_TIMEOUT_SECONDS)
            if type(result) is not int or result not in {0, 1}:
                raise ValueError("Invalid monitor state")
            unavailable |= stale
            if mode == "unhealthy":
                alerts += result
                pending += not result
        if unavailable:
            return {"status": "unavailable", "reason": "trunk_runtime_missing_or_stale", "alert_count": alerts}
        return {"status": "alert" if alerts else "pending" if pending else "ok",
                "alert_count": alerts, "pending_count": pending, "checked": len(rows)}
    except Exception:
        return dict(_UNAVAILABLE)


async def check_database(pool, redis, *, now, max_tenants=1000):
    if type(max_tenants) is not int or not 1 <= max_tenants <= 10000:
        raise ValueError("Invalid tenant inventory bound")
    result = {"payments": dict(_UNAVAILABLE), "trunks": dict(_UNAVAILABLE)}
    try:
        async with asyncio.timeout(10):
            # Deliberate platform monitoring aggregates: payment receipts have
            # platform-only RLS and may have no tenant before reconciliation.
            # Only a count is read; never payloads, recipients or event IDs.
            async with acquire_with_tenant(pool, None, timeout=1) as conn:
                count = await conn.fetchval("SELECT count(*) FROM processed_webhook_events WHERE state='needs_review'")
                tenants = await conn.fetch("SELECT id FROM tenants ORDER BY id LIMIT $1", max_tenants+1)
            result["payments"] = {"status": "alert" if count else "ok", "needs_review": count}
            if len(tenants) > max_tenants:
                result["trunks"] = {"status": "unavailable", "reason": "tenant_limit_exceeded"}
                return result
            rows = []
            for tenant in tenants:
                tenant_id = str(UUID(str(tenant["id"])))
                async with acquire_with_tenant(pool, tenant_id, timeout=1) as conn:
                    # Include disabled/deleted rows to clear any prior unhealthy
                    # continuity marker instead of counting that interval later.
                    owned = await conn.fetch("""SELECT id,tenant_id,is_active,deleted_at,direction,
                        jsonb_build_object('register',metadata->'register') AS metadata,
                        live_registration_status,live_status_checked_at FROM tenant_sip_trunks
                        WHERE tenant_id=$1::uuid
                        ORDER BY id LIMIT $2""", tenant_id, _MAX_TRUNKS+1-len(rows))
                if any(str(row["tenant_id"]) != tenant_id for row in owned):
                    raise ValueError("Trunk ownership did not match")
                rows.extend(dict(row) for row in owned)
                if len(rows) > _MAX_TRUNKS:
                    result["trunks"] = {"status": "unavailable", "reason": "trunk_limit_exceeded"}
                    return result
            result["trunks"] = await check_trunks(rows, redis, now=now)
    except Exception:
        pass
    return result


# check_workers reads every worker heartbeat at once, so the pool must admit one
# connection per registered worker. A pool of two refused the third read and
# reported a live reminder worker as heartbeat_read_unavailable (prod, 14 Nov 2026).
REDIS_MAX_CONNECTIONS = len(_WORKER_HEARTBEAT_KEYS) + 1


async def _redis_client():
    from app.core.container import _build_cluster_client, _build_redis_url, _build_sentinel_client
    import redis.asyncio as redis
    mode = (os.getenv("REDIS_MODE") or "single").lower()
    if mode == "sentinel":
        return await _build_sentinel_client()
    if mode == "cluster":
        return await _build_cluster_client()
    if mode != "single":
        raise ValueError("Unsupported Redis mode")
    return redis.from_url(_build_redis_url(), decode_responses=True,
                          socket_timeout=1, socket_connect_timeout=1, max_connections=REDIS_MAX_CONNECTIONS)


async def _record_failing_checks(redis, result):
    """Name the failing checks for the failure alert (talky-notify@), which
    alerts again at once when this set changes. Names only, never values."""
    failing = sorted(key for key, value in result.items()
                     if key != "provider_error_rate" and value["status"] in {"alert", "unavailable", "unconfigured"})
    try:
        if failing:
            await asyncio.wait_for(redis.set(FAILING_KEY, ",".join(failing), ex=600), IO_TIMEOUT_SECONDS)
        else:
            await asyncio.wait_for(redis.delete(FAILING_KEY), IO_TIMEOUT_SECONDS)
    except Exception:
        pass


async def run():
    pool, redis = None, None
    result = {}
    now = datetime.now(timezone.utc)
    try:
        async with asyncio.timeout(20):
            result.update(await asyncio.to_thread(check_resources, disk_path=os.getenv("TALKY_MONITOR_DISK_PATH", "/")))
            result["ntp"] = await asyncio.to_thread(check_ntp)
            paths = [Path(value.strip()) for value in os.getenv("TALKY_MONITOR_CERTIFICATE_PATHS", "").split(",") if value.strip()]
            if not paths or len(paths) > 8:
                result["certificates"] = check_certificates(paths, now=now)
            else:
                certificates = []
                for path in paths:
                    # At most one two-second subprocess remains when the
                    # overall deadline cancels this coroutine.
                    certificates.append(await asyncio.to_thread(check_certificates, [path], now=now))
                if any(item["status"] == "unavailable" for item in certificates):
                    result["certificates"] = dict(_UNAVAILABLE)
                else:
                    minimum = min(item["minimum_days_remaining"] for item in certificates)
                    result["certificates"] = {"status": "alert" if minimum < 14 else "ok", "minimum_days_remaining": minimum}
            try:
                async with asyncio.timeout(2):
                    redis = await _redis_client()
            except Exception:
                pass
            result["redis"] = await check_redis_capacity(redis)
            result["workers"] = await check_workers(redis, now=now)
            result.update(await check_provider_observations(redis))
            try:
                dsn = os.environ.get("DATABASE_URL")
                if dsn:
                    pool = await asyncpg.create_pool(dsn.replace("postgresql+asyncpg://", "postgresql://", 1),
                        min_size=1, max_size=1, timeout=2, command_timeout=2, statement_cache_size=0)
            except Exception:
                pass
            result.update(await check_database(pool, redis, now=now))
            await _record_failing_checks(redis, result)
    except TimeoutError:
        result["monitor"] = {"status": "unavailable", "reason": "monitor_deadline_exceeded"}
    finally:
        if redis is not None:
            try:
                await asyncio.wait_for(redis.aclose(), 1)
            except Exception:
                pass
        if pool is not None:
            try:
                await asyncio.wait_for(pool.close(), 1)
            except Exception:
                pool.terminate()
    print(json.dumps(result, sort_keys=True), flush=True)
    # Explicit, documented missing producer-rate coverage is not a fabricated
    # outage. All other unreadable/unconfigured required checks fail visibly.
    return 1 if any(value["status"] in {"alert", "unavailable", "unconfigured"}
                    for key, value in result.items() if key != "provider_error_rate") else 0


def main():
    try:
        return asyncio.run(run())
    except Exception as exc:
        print(f"healthwatch unavailable: {type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
