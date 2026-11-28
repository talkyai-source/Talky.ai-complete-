#!/usr/bin/env python3
"""Bounded host/dependency observations for the existing healthwatch timer.

Reads process configuration only. Never probes a provider, changes a database
row, repairs the clock, or reads backup contents. Missing evidence is explicit.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
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
from app.domain.services import recording_spool
from app.domain.services.telephony.trunk_runtime import evaluate_trunk_runtime
from app.infrastructure.metrics.email_delivery import ERRORS as EMAIL_ERRORS, read_email_outcome
from app.infrastructure.metrics.provider_observations import read_recent

IO_TIMEOUT_SECONDS = 0.5
FAILING_KEY = "talky:healthwatch:failing"  # read by app.core.operational_alerts
LAST_RUN_KEY = "talky:healthwatch:last_run"  # read by GET /healthz/watch
# The watch's own unit is running while this script runs, never failed.
_OWN_UNITS = frozenset({"talky-healthwatch.service"})
_UNIT_NAME = re.compile(r"[A-Za-z0-9@_.:-]{1,200}\Z")
_MAX_TRUNKS = 1000
# how long "this line was seen working" is remembered after it was last ready, or last alerted
_TRUNK_READY_SECONDS = 7 * 86400
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


def check_failed_units():
    """Talky units that systemd holds as failed: the nightly backup, the hourly
    inbound test call, the alert sender itself. Each of them used to fail with
    nobody told, because its own alert went by the email that was down."""
    try:
        done = _command(["systemctl", "list-units", "talky-*", "--state=failed", "--no-legend", "--plain"])
        if done.returncode != 0:
            return dict(_UNAVAILABLE)
        failed = sorted({line.split()[0] for line in done.stdout.splitlines() if line.split()} - _OWN_UNITS)
        if len(failed) > 50 or any(not _UNIT_NAME.match(name) for name in failed):
            return dict(_UNAVAILABLE)
        return {"status": "alert" if failed else "ok", "failed": failed}
    except Exception:
        return dict(_UNAVAILABLE)


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


# A refused login or missing mailbox settings fail every send, so one is
# enough. A time-out or network fault can pass by itself, so it must happen
# twice in a row before it counts.
_EMAIL_FAILS_AT_ONCE = frozenset({"authentication", "unconfigured"})
EMAIL_ALERT_AFTER_FAILURES = 2
EMAIL_STALE_AFTER_HOURS = 72


async def check_email(redis, *, now):
    """The platform mailbox, judged by its last real send (sign-up codes,
    resets, invites, notifications). It failed unseen for 11 days in Nov 2026
    because the alerts used the same mailbox. Nothing is sent from here."""
    try:
        record = await read_email_outcome(redis)
    except Exception:
        return dict(_UNAVAILABLE)
    if not record:
        return {"status": "no_recent_evidence"}
    try:
        at = float(record["at"])
        if not math.isfinite(at):
            raise ValueError("Invalid send time")
        if record["outcome"] == "ok":
            minutes = max(0, round((now.timestamp() - at) / 60))
            # An ok answer older than 72 hours is no proof the mailbox still
            # works (a revoked password leaves the last ok in place). There is
            # no "since when" for an empty record, so no_recent_evidence stays
            # non-failing; stale is failing.
            return {"status": "stale" if minutes > EMAIL_STALE_AFTER_HOURS * 60 else "ok",
                    "last_send_minutes_ago": minutes}
        if record["outcome"] != "failed":
            raise ValueError("Invalid outcome")
        error, count, since = record["error"], int(record["consecutive_failures"]), float(record["failing_since"])
        if error not in EMAIL_ERRORS or count < 1 or not math.isfinite(since):
            raise ValueError("Invalid failure record")
    except (KeyError, TypeError, ValueError):
        return {"status": "unavailable", "reason": "email_outcome_invalid"}
    alert = error in _EMAIL_FAILS_AT_ONCE or count >= EMAIL_ALERT_AFTER_FAILURES
    return {"status": "alert" if alert else "pending", "error": error, "consecutive_failures": count,
            "failing_for_minutes": max(0, round((now.timestamp() - since) / 60))}


async def check_trunks(rows, redis, *, now):
    if len(rows) > _MAX_TRUNKS:
        return {"status": "unavailable", "reason": "trunk_limit_exceeded"}
    pending, alerts, never_ready = 0, 0, 0
    unavailable = False
    try:
        for row in rows:
            # Inbound-only IP-auth trunks need loaded evidence; outbound/both
            # require a qualified contact or a valid registration.
            readiness = evaluate_trunk_runtime(row, now=now, require_inbound=row.get("direction") == "inbound")
            stale = readiness.code in {"status_missing", "status_stale", "status_clock_skew"}
            ignored = not row.get("is_active") or row.get("deleted_at") is not None
            mode = "clear" if readiness.ready or stale or ignored else "unhealthy"
            ids = f"{UUID(str(row['tenant_id']))}:{UUID(str(row['id']))}"
            key = f"talky:healthwatch:trunk:{ids}"
            ready_key = f"talky:healthwatch:trunk_ready:{ids}"
            if readiness.ready and not ignored:
                # Remember that this line worked; only such a line alerts when it goes down.
                if not await asyncio.wait_for(redis.set(ready_key, "1", ex=_TRUNK_READY_SECONDS), IO_TIMEOUT_SECONDS):
                    raise ValueError("Invalid ready marker write")
            result = await asyncio.wait_for(redis.eval(_TRUNK_STATE, 1, key, now.timestamp(), mode), IO_TIMEOUT_SECONDS)
            if type(result) is not int or result not in {0, 1}:
                raise ValueError("Invalid monitor state")
            unavailable |= stale
            if mode == "unhealthy":
                pending += not result
                if result:
                    # One call: true when the marker exists, and it is renewed, so a line
                    # that stays down keeps alerting after the 7 days.
                    was_ready = await asyncio.wait_for(redis.expire(ready_key, _TRUNK_READY_SECONDS), IO_TIMEOUT_SECONDS)
                    if type(was_ready) not in {bool, int} or was_ready not in {0, 1}:
                        raise ValueError("Invalid ready marker")
                    alerts += bool(was_ready)
                    never_ready += not was_ready
        if unavailable:
            return {"status": "unavailable", "reason": "trunk_runtime_missing_or_stale", "alert_count": alerts}
        return {"status": "alert" if alerts else "pending" if pending else "ok",
                "alert_count": alerts, "pending_count": pending, "never_ready_count": never_ready,
                "checked": len(rows)}
    except Exception:
        return dict(_UNAVAILABLE)


# A recording that is not saved yet may wait this long before it counts as stuck;
# one waiting for the transfer part of the call may wait much longer.
_PENDING_STUCK_SECONDS = 900
_PENDING_AWAITING_STUCK_SECONDS = 5400

_CALLS_ANSWERED_SQL = """SELECT count(*) AS answered,
    (SELECT default_consent_mode FROM tenant_recording_policy WHERE tenant_id = $1::uuid) AS mode
    FROM calls
    WHERE tenant_id = $1::uuid AND direction = 'outbound' AND is_test = false AND answered_at <= now()
      AND ended_at >= now() - interval '24 hours' AND ended_at <= now() - interval '15 minutes'
      AND duration_seconds >= 5"""
# "Answered by a person" for the notice alarm = PICKED UP (calls.answered_at, the same column
# `matched` uses) AND NOT A MACHINE (outcome voicemail / no_answer). It is deliberately NOT
# the dashboard's answered set: a call a person picked up but our own agent then failed
# (outcome 'failed') or whose outcome was never saved (NULL) must count, because exactly the
# outage that stops the notice being spoken would otherwise remove those calls from both
# counts and silence the alarm. A call that ends before the agent speaks is recorded with
# notice_state 'unconfirmed' by design, so voicemails stay out.
# About half of answered calls are hung up within seconds, mid-notice, and are 'unconfirmed' by
# design, so a ratio over ALL answered calls flaps at its threshold. Three arms instead:
#  - long calls (the person stayed on 30 s or more, ended_at - answered_at; the number 30 is
#    written in the SQL below): half or more never got the notice. Measured on five calling days
#    of November 2026: 48 answered calls lasted 30 s or more and 2 of them never got a confirmed notice;
#  - nobody gets it over 24 hours: four in five of all answered calls (and at least 15) have no
#    confirmed notice. The share of all answered calls with no confirmed notice was between 32% and
#    61% a day (30 of 57 and 37 of 76 on two of them);
#  - the notice has stopped over 4 hours: 12 or more answered calls and not one confirmed, which
#    the 24-hour share is too slow to show when the agent goes silent in the afternoon.
# The two denominators (recorded_answered, answered_long) count only recordings that need a notice
# ('delivered' or 'unconfirmed'; 'not_required' and empty are left out).
# The arms are the three functions below; fingerprint() uses the last two too.
_RECORDINGS_SQL = """SELECT count(*) FILTER (WHERE r.created_at >= now() - interval '24 hours') AS recorded,
    count(*) FILTER (WHERE r.created_at >= now() - interval '24 hours' AND r.notice_state = 'unconfirmed') AS unconfirmed,
    count(*) FILTER (WHERE r.created_at >= now() - interval '24 hours'
      AND c.answered_at IS NOT NULL AND COALESCE(c.outcome, '') NOT IN ('voicemail', 'no_answer')
      AND r.notice_state IN ('delivered', 'unconfirmed')) AS recorded_answered,
    count(*) FILTER (WHERE r.created_at >= now() - interval '24 hours'
      AND c.answered_at IS NOT NULL AND COALESCE(c.outcome, '') NOT IN ('voicemail', 'no_answer')
      AND r.notice_state = 'unconfirmed') AS unconfirmed_answered,
    count(*) FILTER (WHERE r.created_at >= now() - interval '24 hours'
      AND c.answered_at IS NOT NULL AND COALESCE(c.outcome, '') NOT IN ('voicemail', 'no_answer')
      AND r.notice_state IN ('delivered', 'unconfirmed')
      AND c.ended_at - c.answered_at >= interval '30 seconds') AS answered_long,
    count(*) FILTER (WHERE r.created_at >= now() - interval '24 hours'
      AND c.answered_at IS NOT NULL AND COALESCE(c.outcome, '') NOT IN ('voicemail', 'no_answer')
      AND c.ended_at - c.answered_at >= interval '30 seconds'
      AND r.notice_state = 'unconfirmed') AS unconfirmed_long,
    count(*) FILTER (WHERE r.created_at >= now() - interval '4 hours'
      AND c.answered_at IS NOT NULL AND COALESCE(c.outcome, '') NOT IN ('voicemail', 'no_answer')
      AND r.notice_state IN ('delivered', 'unconfirmed')) AS answered_recent,
    count(*) FILTER (WHERE r.created_at >= now() - interval '4 hours'
      AND c.answered_at IS NOT NULL AND COALESCE(c.outcome, '') NOT IN ('voicemail', 'no_answer')
      AND r.notice_state = 'unconfirmed') AS unconfirmed_recent,
    count(*) FILTER (WHERE r.created_at >= now() - interval '24 hours' AND r.transfer_part = 'missing') AS transfer_missing,
    count(DISTINCT r.call_id) FILTER (WHERE c.direction = 'outbound' AND c.is_test = false AND c.answered_at <= now()
      AND c.ended_at >= now() - interval '24 hours' AND c.ended_at <= now() - interval '15 minutes'
      AND c.duration_seconds >= 5) AS matched
    FROM recordings_s3 r JOIN calls c ON c.id = r.call_id AND c.tenant_id = r.tenant_id
    WHERE r.tenant_id = $1::uuid AND r.status = 'uploaded' AND r.created_at >= now() - interval '25 hours'"""
# Before migration 0103 the notice/transfer columns do not exist: same shape, zero counts.
_RECORDINGS_OLD_SQL = """SELECT count(*) FILTER (WHERE r.created_at >= now() - interval '24 hours') AS recorded,
    0 AS unconfirmed, 0 AS recorded_answered, 0 AS unconfirmed_answered, 0 AS answered_long, 0 AS unconfirmed_long,
    0 AS answered_recent, 0 AS unconfirmed_recent, 0 AS transfer_missing,
    count(DISTINCT r.call_id) FILTER (WHERE c.direction = 'outbound' AND c.is_test = false AND c.answered_at <= now()
      AND c.ended_at >= now() - interval '24 hours' AND c.ended_at <= now() - interval '15 minutes'
      AND c.duration_seconds >= 5) AS matched
    FROM recordings_s3 r JOIN calls c ON c.id = r.call_id AND c.tenant_id = r.tenant_id
    WHERE r.tenant_id = $1::uuid AND r.status = 'uploaded' AND r.created_at >= now() - interval '25 hours'"""

# Incoming calls: recording is governed by ONE platform-wide switch (the table has no
# tenant column, so there is no tenant policy). One statement per tenant returns both counts.
_INBOUND_SWITCH_SQL = "SELECT inbound_recording_enabled FROM platform_runtime_controls WHERE id = 1"
# Counted: answered incoming calls whose notice was spoken and recording allowed
# (consent_status = 'granted'). Knowingly NOT counted: an incoming call recorded
# with an unconfirmed notice ('unknown' also means "never reached the gate").
_INBOUND_SQL = """SELECT count(*) AS answered_inbound,
    count(*) FILTER (WHERE EXISTS (SELECT 1 FROM recordings_s3 r WHERE r.tenant_id = c.tenant_id
      AND r.call_id = c.id AND r.status = 'uploaded' AND r.created_at >= now() - interval '25 hours')) AS matched_inbound
    FROM calls c
    WHERE c.tenant_id = $1::uuid AND c.direction = 'inbound' AND c.is_test = false AND c.answered_at <= now()
      AND c.ended_at >= now() - interval '24 hours' AND c.ended_at <= now() - interval '15 minutes'
      AND c.duration_seconds >= 5 AND c.consent_status = 'granted'"""


def _notice_missed_on_long_calls(unconfirmed_long, answered_long):
    """People stayed on 30 s or more, and half or more of them never got the notice."""
    return unconfirmed_long >= 3 and unconfirmed_long * 2 >= answered_long


def _notice_reaches_nobody(unconfirmed_answered, recorded_answered):
    """Four in five answered calls, and at least 15, have no confirmed notice."""
    return unconfirmed_answered >= 15 and unconfirmed_answered * 5 >= recorded_answered * 4


def _notice_has_stopped(unconfirmed_recent, answered_recent):
    """Twelve or more answered calls in the last 4 hours and not one got a confirmed notice."""
    return answered_recent >= 12 and unconfirmed_recent == answered_recent


def _pending_stuck(now):
    now = now or datetime.now(timezone.utc)
    stuck = 0
    for entry in recording_spool.list_entries():
        created = datetime.fromisoformat(str(entry.created_at).replace("Z", "+00:00"))
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        limit = _PENDING_AWAITING_STUCK_SECONDS if entry.awaiting else _PENDING_STUCK_SECONDS
        if (now - created).total_seconds() > limit:
            stuck += 1
    return stuck


async def check_database(pool, redis, *, now, max_tenants=1000):
    if type(max_tenants) is not int or not 1 <= max_tenants <= 10000:
        raise ValueError("Invalid tenant inventory bound")
    result = {"payments": dict(_UNAVAILABLE), "trunks": dict(_UNAVAILABLE),
              "recordings": dict(_UNAVAILABLE)}
    try:
        async with asyncio.timeout(10):
            # Deliberate platform monitoring aggregates: payment receipts have
            # platform-only RLS and may have no tenant before reconciliation.
            # Only a count is read; never payloads, recipients or event IDs.
            async with acquire_with_tenant(pool, None, timeout=1) as conn:
                count = await conn.fetchval("SELECT count(*) FROM processed_webhook_events WHERE state='needs_review'")
                paypal_count = 0
                if await conn.fetchval("SELECT to_regclass('public.paypal_webhook_events') IS NOT NULL"):
                    paypal_count = await conn.fetchval(
                        "SELECT count(*) FROM paypal_webhook_events WHERE state = 'needs_review' "
                        "OR (state IN ('failed', 'received') AND received_at < NOW() - INTERVAL '1 hour')")
                count = (count or 0) + (paypal_count or 0)
                # Stripe retries a failing event for three days and then stops;
                # a receipt still failed/pending after 6 hours means a paid
                # customer was not credited (processed_webhook_events.state,
                # received_at: migration 0054).
                stuck = await conn.fetchval(
                    "SELECT count(*) FROM processed_webhook_events "
                    "WHERE state IN ('failed', 'pending') AND received_at < NOW() - INTERVAL '6 hours'")
                notice_columns = await conn.fetchval(
                    "SELECT count(*) FROM information_schema.columns WHERE table_schema = 'public' "
                    "AND table_name = 'recordings_s3' AND column_name IN ('notice_state', 'transfer_part')")
                tenants = await conn.fetch("SELECT id FROM tenants ORDER BY id LIMIT $1", max_tenants+1)
                # Read ONCE per run. None = unreadable (never guessed as on or off).
                try:
                    async with conn.transaction():
                        inbound_switch = (await conn.fetchval(_INBOUND_SWITCH_SQL)) is True
                except Exception:
                    inbound_switch = None
            result["payments"] = {"status": "alert" if count or stuck else "ok", "needs_review": count,
                                "paypal_needs_review": paypal_count or 0, "stuck": stuck or 0}
            if len(tenants) > max_tenants:
                result["trunks"] = {"status": "unavailable", "reason": "tenant_limit_exceeded"}
                return result
            rows = []
            missing = unconfirmed = transfer_missing = recorded = recorded_answered = unconfirmed_answered = 0
            answered_long = unconfirmed_long = answered_recent = unconfirmed_recent = 0
            recordings_read = True
            missing_inbound = 0 if inbound_switch is not None else None
            recordings_sql = _RECORDINGS_SQL if notice_columns == 2 else _RECORDINGS_OLD_SQL
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
                    # Recording coverage: same tenant-scoped connection, two statements.
                    # Inside a savepoint: a fault here reports the recording check
                    # as unavailable and leaves the phone-line read above intact.
                    try:
                        async with conn.transaction():
                            calls = await conn.fetchrow(_CALLS_ANSWERED_SQL, tenant_id)
                            if calls["mode"] != "disabled":  # that tenant chose not to record
                                stats = await conn.fetchrow(recordings_sql, tenant_id)
                                missing += max(0, calls["answered"] - stats["matched"])
                                unconfirmed += stats["unconfirmed"]
                                transfer_missing += stats["transfer_missing"]
                                recorded += stats["recorded"]
                                recorded_answered += stats["recorded_answered"]
                                unconfirmed_answered += stats["unconfirmed_answered"]
                                answered_long += stats["answered_long"]
                                unconfirmed_long += stats["unconfirmed_long"]
                                answered_recent += stats["answered_recent"]
                                unconfirmed_recent += stats["unconfirmed_recent"]
                                if inbound_switch and missing_inbound is not None:
                                    # Own savepoint: a fault here makes only the incoming part unreadable.
                                    try:
                                        async with conn.transaction():
                                            inbound = await conn.fetchrow(_INBOUND_SQL, tenant_id)
                                        missing_inbound += max(0, inbound["answered_inbound"] - inbound["matched_inbound"])
                                    except Exception:
                                        missing_inbound = None
                    except Exception:
                        recordings_read = False
                if any(str(row["tenant_id"]) != tenant_id for row in owned):
                    raise ValueError("Trunk ownership did not match")
                rows.extend(dict(row) for row in owned)
                if len(rows) > _MAX_TRUNKS:
                    result["trunks"] = {"status": "unavailable", "reason": "trunk_limit_exceeded"}
                    return result
            result["trunks"] = await check_trunks(rows, redis, now=now)
            try:
                pending = await asyncio.to_thread(_pending_stuck, now)
            except Exception:
                pending = None
            alert = (missing > 0 or (missing_inbound or 0) > 0 or (pending or 0) > 0 or transfer_missing > 0
                     or _notice_missed_on_long_calls(unconfirmed_long, answered_long)
                     or _notice_reaches_nobody(unconfirmed_answered, recorded_answered)
                     or _notice_has_stopped(unconfirmed_recent, answered_recent))
            if recordings_read and pending is None and not alert:
                # The counts are fine but the pending folder could not be read:
                # that is not "ok".
                result["recordings"] = {"status": "unavailable", "reason": "pending_folder_unreadable",
                                        "missing": missing, "missing_inbound": missing_inbound,
                                        "pending_stuck": None,
                                        "transfer_missing": transfer_missing,
                                        "unconfirmed": unconfirmed, "recorded": recorded,
                                        "recorded_answered": recorded_answered,
                                        "unconfirmed_answered": unconfirmed_answered,
                                        "answered_long": answered_long, "unconfirmed_long": unconfirmed_long,
                                        "answered_recent": answered_recent,
                                        "unconfirmed_recent": unconfirmed_recent}
            elif recordings_read:
                result["recordings"] = {"status": "alert" if alert else "ok", "missing": missing,
                                        "missing_inbound": missing_inbound,
                                        "pending_stuck": pending, "transfer_missing": transfer_missing,
                                        "unconfirmed": unconfirmed, "recorded": recorded,
                                        "recorded_answered": recorded_answered,
                                        "unconfirmed_answered": unconfirmed_answered,
                                        "answered_long": answered_long, "unconfirmed_long": unconfirmed_long,
                                        "answered_recent": answered_recent,
                                        "unconfirmed_recent": unconfirmed_recent}
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


async def _record_run(redis, now):
    """Mark that the watch completed a run. An outside caller reads this through
    GET /healthz/watch, so a watch that has stopped is not mistaken for a
    healthy server. Ten minutes covers several missed one-minute runs."""
    try:
        await asyncio.wait_for(redis.set(LAST_RUN_KEY, str(now.timestamp()), ex=600), IO_TIMEOUT_SECONDS)
    except Exception:
        pass


FAILING_STATUSES = frozenset({"alert", "unavailable", "unconfigured", "stale"})


def _count(value):
    return str(value) if type(value) is int and value >= 0 else "0"


def fingerprint(name, value):
    """Evidence of one failing check, for the failure alert (talky-notify@):
    ``name:status`` plus the numbers that say how bad it is, so a 17th phone
    line or a 7th payment is a NEW failure while the check is already red.
    Counts and unit names only, never customer data or free text."""
    status = value["status"]
    base = f"{name}:{status}"
    if status != "alert":
        return base
    try:
        if name == "trunks":
            return f"{base}:{_count(value['alert_count'])}"
        if name == "payments":
            return f"{base}:{_count(value['needs_review'])}+{_count(value['stuck'])}"
        if name == "units":
            failed = sorted(value["failed"])
            if failed and all(_UNIT_NAME.match(unit) and ":" not in unit for unit in failed):
                return f"{base}:{'+'.join(failed)}"
        if name == "recordings":
            text = (f"{base}:{_count(value['missing'])}+{_count(value['pending_stuck'])}"
                    f"+{_count(value['transfer_missing'])}+{_count(value['unconfirmed_long'])}")
            # Added only when an incoming recording is missing, so the text of
            # every other state stays what it was (no repeat notice on release).
            if _count(value.get("missing_inbound")) != "0":
                text += f"+in{_count(value['missing_inbound'])}"
            # The nobody-gets-it arm and the notice-has-stopped arm: a fixed word each ("all",
            # "now"), not a count, which changes with every call. Always AFTER the "in<N>"
            # part, so that part keeps its place.
            every, answered = value.get("unconfirmed_answered"), value.get("recorded_answered")
            if type(every) is int and type(answered) is int and _notice_reaches_nobody(every, answered):
                text += "+all"
            recent, answered_recent = value.get("unconfirmed_recent"), value.get("answered_recent")
            if type(recent) is int and type(answered_recent) is int and _notice_has_stopped(recent, answered_recent):
                text += "+now"
            return text
    except (KeyError, TypeError):
        pass
    return base


async def _record_failing_checks(redis, result):
    """Record the failing checks' fingerprints for the failure alert
    (talky-notify@), which alerts again at once when any fingerprint changes."""
    failing = sorted(fingerprint(key, value) for key, value in result.items()
                     if key != "provider_error_rate" and value["status"] in FAILING_STATUSES)
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
            result["units"] = await asyncio.to_thread(check_failed_units)
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
            result["email"] = await check_email(redis, now=now)
            try:
                dsn = os.environ.get("DATABASE_URL")
                if dsn:
                    pool = await asyncpg.create_pool(dsn.replace("postgresql+asyncpg://", "postgresql://", 1),
                        min_size=1, max_size=1, timeout=2, command_timeout=2, statement_cache_size=0)
            except Exception:
                pass
            result.update(await check_database(pool, redis, now=now))
            await _record_failing_checks(redis, result)
            await _record_run(redis, now)
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
    return 1 if any(value["status"] in FAILING_STATUSES
                    for key, value in result.items() if key != "provider_error_rate") else 0


def main():
    try:
        return asyncio.run(run())
    except Exception as exc:
        print(f"healthwatch unavailable: {type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
