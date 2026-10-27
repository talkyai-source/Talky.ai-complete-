"""Bounded operational observations for the existing authenticated /metrics.

Each scrape owns its registry: failed reads omit measurements instead of
retaining a previous healthy value. No tenant, job, payload or path labels.
"""
from __future__ import annotations

import asyncio
import json
import math
import os
from pathlib import Path
import stat
import time

from prometheus_client import CollectorRegistry, Gauge, generate_latest

from app.api.v1.endpoints.health import read_worker_heartbeats
from app.domain.services.queue_factory import resolve_queue_backend
from app.domain.services.queue_service import DialerQueueService as Queue
from app.domain.services.streams_queue_service import GROUP_NAME, STREAM_NORMAL, STREAM_PRIORITY

READ_TIMEOUT = 3.0
MAX_SCAN_PAGES = 25
MAX_QUEUE_KEYS = 5000
BACKUP_STATUS_DIR = "/var/lib/talky/monitoring"


def _count(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("Invalid counter")
    return value


def _timestamp(value, now):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Invalid timestamp")
    if not math.isfinite(value) or not 0 < value <= now:
        raise ValueError("Invalid timestamp")
    return float(value)


async def _list_depth(redis):
    cursor, keys = 0, set()
    for _ in range(MAX_SCAN_PAGES):
        cursor, found = await redis.scan(cursor, match="dialer:tenant:*:queue", count=200)
        keys.update(found)
        if len(keys) > MAX_QUEUE_KEYS:
            raise ValueError("Queue inventory exceeds observation bound")
        if int(cursor) == 0:
            break
    else:
        raise ValueError("Queue inventory did not finish")
    keys = [Queue.PRIORITY_QUEUE, *keys]
    pending = 0
    # Limit outstanding commands even at the inventory bound.
    for index in range(0, len(keys), 100):
        values = await asyncio.gather(*(redis.llen(key) for key in keys[index:index + 100]))
        pending += sum(_count(value) for value in values)
    return {"ready": pending, "processing": _count(await redis.zcard(Queue.PROCESSING_ZSET))}


async def _stream_depth(redis):
    ready, processing = 0, 0
    for stream in (STREAM_PRIORITY, STREAM_NORMAL):
        if await redis.type(stream) in ("none", b"none"):
            continue
        groups = await redis.xinfo_groups(stream)
        group = next((item for item in groups if item.get("name", item.get(b"name"))
                      in (GROUP_NAME, GROUP_NAME.encode())), None)
        if group is None:
            raise ValueError("Missing consumer group")
        ready += _count(group.get("lag", group.get(b"lag")))
        processing += _count(group.get("pending", group.get(b"pending")))
    return {"ready": ready, "processing": processing}


async def _scheduled(redis, now):
    # Read the earliest due score, not job.created_at or retained stream length.
    # Due work may still be deliberately deferred by campaign/quota policy.
    pending = _count(await redis.zcard(Queue.SCHEDULED_ZSET))
    first = await redis.zrange(Queue.SCHEDULED_ZSET, 0, 0, withscores=True)
    if not first:
        if pending:
            raise ValueError("Queue changed during observation")
        return {"pending": 0, "overdue_seconds": 0.0}
    if not pending or len(first) != 1:
        raise ValueError("Queue changed during observation")
    score = first[0][1]
    if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score) or score <= 0:
        raise ValueError("Invalid due score")
    return {"pending": pending, "overdue_seconds": max(0.0, now - score)}


def _read_small_file(path):
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("Status must be a regular file")
        data = stream.read(1025)
    if len(data) > 1024:
        raise ValueError("Status exceeds bound")
    return data


def _backup_status(directory, now):
    status = json.loads(_read_small_file(Path(directory) / "backup-attempt.json"))
    if not isinstance(status, dict) or set(status) != {"started_at", "completed_at", "result"}:
        raise ValueError("Invalid backup receipt")
    start = _timestamp(status["started_at"], now)
    outcome = status["result"]
    if outcome not in {"running", "succeeded", "failed"}:
        raise ValueError("Invalid backup outcome")
    end = status["completed_at"]
    if outcome == "running":
        if end is not None:
            raise ValueError("Running backup has completion")
    elif _timestamp(end, now) < start:
        raise ValueError("Invalid backup completion")
    try:
        last_success = _timestamp(float(_read_small_file(Path(directory) / "backup-success.timestamp")), now)
    except FileNotFoundError:
        last_success = None
    # Success publication precedes attempt publication. Never accept a complete
    # successful attempt without its matching durable timestamp.
    if outcome == "succeeded" and last_success != end:
        raise ValueError("Backup success receipt is incomplete")
    return {"started_at": start, "completed_at": end, "result": outcome, "last_success": last_success}


async def render_operational_signals(redis, *, now=None, backup_directory=None):
    now = time.time() if now is None else now
    registry = CollectorRegistry()

    def metric(name, description, value, **labels):
        # Each metric family is lazily constructed once per request.
        if name not in families:
            families[name] = Gauge(name, description, list(labels), registry=registry)
        gauge = families[name].labels(**labels) if labels else families[name]
        gauge.set(value)

    families = {}
    metric("talky_operational_observation_timestamp_seconds", "Time of this bounded operational observation.", now)
    metric("talky_operational_signal_contract", "Operational signal contract version; not receiver readiness.", 1)

    async def workers():
        rows = await read_worker_heartbeats(redis, now=now)
        for row in rows:
            observed = row["reason"] in {"recent_heartbeat", "heartbeat_stale", "heartbeat_missing"}
            label = {"worker": row["name"]}
            metric("talky_worker_observation_success", "1 when the role heartbeat was read and valid or absent.", int(observed), **label)
            if observed:
                metric("talky_worker_heartbeat_healthy", "Role heartbeat freshness; not per-instance health or job progress.", int(row["healthy"]), **label)
            if row["last_beat_epoch"] is not None:
                metric("talky_worker_heartbeat_timestamp_seconds", "Last measured role heartbeat timestamp.", row["last_beat_epoch"], **label)

    async def queue(name, reader):
        try:
            if redis is None:
                raise ValueError("Redis unavailable")
            result = await asyncio.wait_for(reader(), timeout=READ_TIMEOUT)
        except Exception:
            metric("talky_queue_observation_success", "1 when bounded queue observations completed.", 0, queue=name)
            return
        metric("talky_queue_observation_success", "1 when bounded queue observations completed.", 1, queue=name)
        for state, value in result.items():
            if state == "overdue_seconds":
                metric("talky_scheduled_queue_overdue_seconds", "Age past the earliest scheduled due time; zero for empty or future-only work.", value)
            else:
                metric("talky_queue_jobs", "Observed queue counters; commands are not an atomic snapshot.", value, queue=name, state=state)

    async def backup():
        try:
            result = await asyncio.wait_for(asyncio.to_thread(_backup_status,
                backup_directory or os.getenv("TALKY_BACKUP_STATUS_DIR", BACKUP_STATUS_DIR), now), timeout=1.0)
        except Exception:
            metric("talky_backup_observation_success", "1 when the local backup status receipt is readable and valid.", 0)
            return
        metric("talky_backup_observation_success", "1 when the local backup status receipt is readable and valid.", 1)
        metric("talky_backup_running", "1 when the last backup attempt started but has no completion receipt.", int(result["result"] == "running"))
        metric("talky_backup_attempt_started_timestamp_seconds", "Last local backup attempt start time.", result["started_at"])
        metric("talky_backup_success_recorded", "1 when a successful local backup has a recorded timestamp.", int(result["last_success"] is not None))
        if result["completed_at"] is not None:
            metric("talky_backup_last_attempt_success", "Last completed backup command exit was zero; not restore/off-host proof.", int(result["result"] == "succeeded"))
        if result["last_success"] is not None:
            metric("talky_backup_last_success_timestamp_seconds", "Last zero-exit verified local archive publication; not restore/off-host proof.", result["last_success"])

    tasks = [workers(), queue("dialer_lists", lambda: _list_depth(redis)),
             queue("dialer_scheduled", lambda: _scheduled(redis, now)), backup()]
    if resolve_queue_backend() == "streams":
        tasks.append(queue("dialer_streams", lambda: _stream_depth(redis)))
    await asyncio.gather(*tasks)
    return generate_latest(registry)
