#!/usr/bin/env python3
"""Read four bounded release counters; NEVER freeze traffic or approve a drain.

Run with the backend venv and an explicit service --env-file. JSON stdout contains
only counts, timestamps and fixed error categories. Exit 0 means all observations
were obtained and internally consistent, NOT that they were zero or traffic was
frozen. Exit 1 means at least one observation is unknown/inconsistent. These reads
are non-atomic; even four zeros cannot replace the existing drain-owner manifest.
No app lifespan, adapter ownership, provider mutations or Redis repair is started.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime
import json
import logging
from pathlib import Path
import sys
from urllib.parse import parse_qs, quote, unquote, urlsplit

TIMEOUT_SECONDS = 10
MAX_BODY_BYTES = 1024 * 1024
MAX_ITEMS = 10000
MAX_SCAN_PAGES = 1000
LEASE_PREFIX = "telephony:lease:"
ACTIVE_SET = "telephony:active_call_ids"


class ObservationError(ValueError):
    """Only fixed, non-sensitive categories may enter the report."""


def timestamp():
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def count(value):
    if type(value) is not int or value < 0:
        raise ObservationError("invalid_response")
    return value


def gateway_count(payload):
    if not isinstance(payload, dict):
        raise ObservationError("invalid_response")
    return {"count": count(payload.get("active_sessions"))}


def ari_count(payload):
    if not isinstance(payload, list):
        raise ObservationError("invalid_response")
    if len(payload) > MAX_ITEMS:
        raise ObservationError("limit_exceeded")
    ids = [row.get("id") if isinstance(row, dict) else None for row in payload]
    if any(not isinstance(value, str) or not value for value in ids) or len(set(ids)) != len(ids):
        raise ObservationError("invalid_response")
    return {"count": len(ids)}  # No channel identity is retained in evidence.


def http_targets(config):
    import httpx

    names = ("ASTERISK_ARI_HOST", "ASTERISK_ARI_PORT", "ASTERISK_ARI_USER",
             "ASTERISK_ARI_PASSWORD", "ASTERISK_GATEWAY_BASE_URL")
    if config.get("TELEPHONY_ADAPTER", "").lower() != "asterisk" or any(not config.get(k) for k in names):
        raise ObservationError("configuration_missing")
    host, port, user, password, gateway = [config[k] for k in names]
    try:
        if any(c in host for c in "/?#@\\") or any(c.isspace() for c in host) or not 1 <= int(port) <= 65535:
            raise ValueError
        ari = httpx.URL(scheme="http", host=host, port=int(port), path="/ari/channels")
        gateway = httpx.URL(gateway.rstrip("/") + "/stats")
        if gateway.scheme not in {"http", "https"} or not gateway.host or gateway.username or gateway.password or gateway.query or gateway.fragment:
            raise ValueError
    except (ValueError, httpx.InvalidURL):
        raise ObservationError("configuration_invalid") from None
    return ari, gateway, httpx.BasicAuth(user, password)


async def http_observation(config, source):
    import httpx

    ari, gateway, auth = http_targets(config)
    async with httpx.AsyncClient(timeout=3, follow_redirects=False, trust_env=False) as client:
        async with client.stream("GET", ari if source == "ari" else gateway,
                                 auth=auth if source == "ari" else None) as response:
            if response.status_code != 200:
                raise ObservationError("http_rejected")
            body = bytearray()
            async for chunk in response.aiter_bytes():
                if len(body) + len(chunk) > MAX_BODY_BYTES:
                    raise ObservationError("limit_exceeded")
                body.extend(chunk)
            try:
                payload = json.loads(body)
            except (ValueError, UnicodeError):
                raise ObservationError("invalid_response") from None
    return ari_count(payload) if source == "ari" else gateway_count(payload)


async def scan_ids(client, *, members):
    cursor, found = 0, set()
    for _ in range(MAX_SCAN_PAGES):
        if members:
            cursor, values = await client.sscan(ACTIVE_SET, cursor=cursor, count=100)
        else:
            cursor, values = await client.scan(cursor=cursor, match=LEASE_PREFIX + "*", count=100)
        for value in values:
            if not isinstance(value, str) or not value or len(value) > 256:
                raise ObservationError("invalid_response")
            if not members:
                if not value.startswith(LEASE_PREFIX) or value == LEASE_PREFIX:
                    raise ObservationError("invalid_response")
                value = value[len(LEASE_PREFIX):]
            found.add(value)
            if len(found) > MAX_ITEMS:
                raise ObservationError("limit_exceeded")
        if cursor == 0:
            return found
    raise ObservationError("limit_exceeded")


async def redis_counts(client):
    before = count(await client.scard(ACTIVE_SET))
    if before > MAX_ITEMS:
        raise ObservationError("limit_exceeded")
    members = await scan_ids(client, members=True)
    leases = await scan_ids(client, members=False)
    missing, persistent = 0, 0
    for identifier in members | leases:
        ttl = await client.pttl(LEASE_PREFIX + identifier)
        if type(ttl) is not int or ttl < -2:
            raise ObservationError("invalid_response")
        missing += ttl == -2
        persistent += ttl == -1
    after = count(await client.scard(ACTIVE_SET))
    consistent = before == after == len(members) and members == leases and missing == persistent == 0
    return {"count": len(leases), "active_set_count_before": before, "active_set_count_after": after,
            "scanned_active_members": len(members), "members_without_scanned_lease": len(members - leases),
            "leases_without_scanned_member": len(leases - members), "expired_during_read": missing,
            "leases_without_expiry": persistent, "consistent": consistent}


async def redis_observation(config):
    import redis.asyncio as redis

    # SCAN must cover the entire selected primary. Do not mistake a cluster shard
    # or a replica for fleet-wide state; other topologies need their own observer.
    if config.get("REDIS_MODE", "single").lower() != "single":
        raise ObservationError("unsupported_topology")
    url = config.get("REDIS_URL")
    if not url:
        if not config.get("REDIS_HOST"):
            raise ObservationError("configuration_missing")
        password = config.get("REDIS_PASSWORD", "").strip()
        auth = ":" + quote(password, safe="") + "@" if password else ""
        url = f"redis://{auth}{config['REDIS_HOST']}:{config.get('REDIS_PORT', '6379')}/{config.get('REDIS_DB', '0')}"
    client = redis.from_url(url, decode_responses=True, socket_timeout=2,
                            socket_connect_timeout=2, max_connections=1)
    try:
        info = await client.info("replication")
        cluster = await client.info("cluster")
        if info.get("role") != "master" or type(cluster.get("cluster_enabled")) is not int or cluster["cluster_enabled"] != 0:
            raise ObservationError("unsupported_topology")
        return await redis_counts(client)
    finally:
        await client.aclose()


async def database_counts(pool):
    from app.core.db_utils import acquire_with_tenant
    from app.domain.services.dialer.job_states import LIVE_CALL_STATUSES

    async with acquire_with_tenant(pool, None, timeout=3) as conn:
        if (await conn.fetchval("SHOW transaction_read_only")) != "on":
            raise ObservationError("read_only_required")
        if await conn.fetchval("SELECT pg_is_in_recovery()"):
            raise ObservationError("unsupported_topology")
        value = await conn.fetchval("SELECT count(*) FROM public.calls WHERE status = ANY($1::text[])",
                                    list(LIVE_CALL_STATUSES))
    return {"count": count(value)}


def database_dsn(config):
    dsn = config.get("DATABASE_URL")
    if not dsn:
        raise ObservationError("configuration_missing")
    try:
        parsed = urlsplit(dsn)
        # Require one complete target: asyncpg otherwise consults inherited PG*
        # variables for missing host, port, database or user. Query target aliases
        # and multi-host lists are deliberately unsupported by this observer.
        allowed_query = {"ssl", "sslmode", "sslrootcert", "sslcert", "sslkey", "sslcrl"}
        if (parsed.scheme not in {"postgres", "postgresql"} or not parsed.hostname
                or "," in unquote(parsed.hostname) or parsed.port is None
                or not 1 <= parsed.port <= 65535 or not parsed.username
                or not parsed.path.startswith("/") or not unquote(parsed.path[1:])
                or "/" in unquote(parsed.path[1:]) or parsed.fragment
                or not set(parse_qs(parsed.query, keep_blank_values=True)) <= allowed_query):
            raise ValueError
    except ValueError:
        raise ObservationError("configuration_invalid") from None
    return dsn


async def database_observation(config):
    import asyncpg

    pool = await asyncpg.create_pool(database_dsn(config), min_size=1, max_size=1,
        timeout=3, command_timeout=3, server_settings={"default_transaction_read_only": "on",
                                                     "statement_timeout": "3000"})
    try:
        return await database_counts(pool)
    finally:
        # Avoid waiting indefinitely on close; terminate only this tool's pool.
        try:
            await asyncio.wait_for(pool.close(), timeout=1)
        except BaseException:
            pool.terminate()
            raise


async def observe(reader):
    started = timestamp()
    try:
        async with asyncio.timeout(TIMEOUT_SECONDS):
            result = await reader()
        category = None if result.get("consistent", True) else "inconsistent_observation"
    except ObservationError as exc:
        result, category = {"count": None}, str(exc)
    except TimeoutError:
        result, category = {"count": None}, "timeout"
    except Exception:
        result, category = {"count": None}, "unavailable"
    return {"started_at": started, "finished_at": timestamp(), **result, "error": category}


async def collect(config):
    started = timestamp()
    names = ("gateway_sessions", "asterisk_legs", "redis_leases", "db_live_calls")
    readers = (lambda: http_observation(config, "gateway"), lambda: http_observation(config, "ari"),
               lambda: redis_observation(config), lambda: database_observation(config))
    observations = dict(zip(names, await asyncio.gather(*(observe(reader) for reader in readers))))
    complete = all(item["error"] is None for item in observations.values())
    return {"kind": "read_only_non_atomic_observation", "started_at": started, "finished_at": timestamp(),
            "complete": complete, "all_zero_observed": complete and all(item["count"] == 0 for item in observations.values()),
            "traffic_freeze_verified": False, "deployment_authorized": False, "observations": observations}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", required=True, type=Path)
    args = parser.parse_args()
    # Explicit file only, no implicit .env loading or inherited destination URLs.
    # Interpolation is disabled so unrelated shell variables cannot redirect reads.
    logging.disable(logging.CRITICAL)
    try:
        from dotenv import dotenv_values

        with args.env_file.open(encoding="utf-8") as stream:
            config = {k: v for k, v in dotenv_values(stream=stream, interpolate=False).items() if v is not None}
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        report = asyncio.run(collect(config))
    except Exception:
        print(json.dumps({"kind": "read_only_non_atomic_observation", "complete": False,
                          "error": "configuration_unavailable", "deployment_authorized": False}))
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0 if report["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
