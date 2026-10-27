"""Real exposition, bounded Redis ports and backup files; no external calls."""
import asyncio
import json
import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from prometheus_client.parser import text_string_to_metric_families

from app.infrastructure.metrics import operational_signals as signals

NOW = 1_790_000_000.0


def sample(body, name, **labels):
    return next((item.value for family in text_string_to_metric_families(body.decode())
                 for item in family.samples if item.name == name and item.labels == labels), None)


def redis_port(**overrides):
    values = dict(get=AsyncMock(return_value=str(NOW - 10)),
                  scan=AsyncMock(return_value=(0, [])), llen=AsyncMock(return_value=0),
                  zcard=AsyncMock(return_value=0), zrange=AsyncMock(return_value=[]),
                  type=AsyncMock(return_value="none"), xinfo_groups=AsyncMock(return_value=[]))
    values.update(overrides)
    return SimpleNamespace(**values)


def backup(directory, *, outcome="succeeded", start=NOW - 120, end=NOW - 10, success=NOW - 10):
    (directory / "backup-attempt.json").write_text(json.dumps({
        "started_at": start, "completed_at": end, "result": outcome, "last_success_at": success,
    }))


@pytest.fixture(autouse=True)
def list_backend(monkeypatch):
    monkeypatch.setenv("DIALER_QUEUE_BACKEND", "list")


@pytest.mark.parametrize("heartbeat,observed,healthy,timestamp", [
    (str(NOW - 10), 1, 1, NOW - 10),
    (str(NOW - 180), 1, 0, NOW - 180),
    (None, 1, 0, None),
    ("nan", 0, None, None),
    (str(NOW + 1), 0, None, None),
    ("invalid", 0, None, None),
])
async def test_worker_fresh_stale_absent_and_invalid_are_distinct(tmp_path, heartbeat, observed, healthy, timestamp):
    result = await signals.render_operational_signals(redis_port(get=AsyncMock(return_value=heartbeat)), now=NOW, backup_directory=tmp_path)
    for role in ("dialer", "voice", "reminder"):
        assert sample(result, "talky_worker_observation_success", worker=role) == observed
        assert sample(result, "talky_worker_heartbeat_healthy", worker=role) == healthy
        assert sample(result, "talky_worker_heartbeat_timestamp_seconds", worker=role) == timestamp


async def test_unavailable_source_never_reuses_previous_healthy_values(tmp_path):
    healthy = await signals.render_operational_signals(redis_port(), now=NOW, backup_directory=tmp_path)
    missing = await signals.render_operational_signals(None, now=NOW, backup_directory=tmp_path)
    assert sample(healthy, "talky_worker_heartbeat_healthy", worker="dialer") == 1
    assert sample(missing, "talky_worker_observation_success", worker="dialer") == 0
    assert sample(missing, "talky_worker_heartbeat_healthy", worker="dialer") is None
    assert sample(missing, "talky_queue_observation_success", queue="dialer_lists") == 0
    assert sample(missing, "talky_queue_jobs", queue="dialer_lists", state="ready") is None
    assert sample(missing, "talky_scheduled_queue_overdue_seconds") is None


async def test_queue_depths_are_actual_deduplicated_counts_without_private_labels(tmp_path):
    redis = redis_port(scan=AsyncMock(side_effect=[(1, ["tenant-private"]), (0, ["tenant-private"])]),
                       llen=AsyncMock(side_effect=[2, 3]), zcard=AsyncMock(return_value=4),
                       zrange=AsyncMock(return_value=[("private-job-payload", NOW - 601)]))
    body = await signals.render_operational_signals(redis, now=NOW, backup_directory=tmp_path)
    assert sample(body, "talky_queue_jobs", queue="dialer_lists", state="ready") == 5
    assert sample(body, "talky_queue_jobs", queue="dialer_lists", state="processing") == 4
    assert sample(body, "talky_queue_jobs", queue="dialer_scheduled", state="pending") == 4
    assert sample(body, "talky_scheduled_queue_overdue_seconds") == 601
    assert b"private" not in body
    assert redis.llen.await_count == 2


@pytest.mark.parametrize("pending,first,age", [(0, [], 0), (1, [("hidden", NOW + 600)], 0),
    (1, [], None), (0, [("hidden", NOW - 1)], None), (1, [("hidden", float("nan"))], None)])
async def test_scheduled_future_empty_racy_and_invalid(tmp_path, pending, first, age):
    body = await signals.render_operational_signals(redis_port(zcard=AsyncMock(return_value=pending),
        zrange=AsyncMock(return_value=first)), now=NOW, backup_directory=tmp_path)
    assert sample(body, "talky_scheduled_queue_overdue_seconds") == age
    assert sample(body, "talky_queue_observation_success", queue="dialer_scheduled") == int(age is not None)


@pytest.mark.parametrize("case", ["scan_bound", "key_bound", "negative", "bool", "timeout", "error"])
async def test_bounded_queue_failure_does_not_publish_zero(tmp_path, monkeypatch, case):
    redis = redis_port()
    if case == "scan_bound":
        redis.scan.return_value = (1, [])
    elif case == "key_bound":
        redis.scan.return_value = (0, [str(i) for i in range(signals.MAX_QUEUE_KEYS + 1)])
    elif case in {"negative", "bool"}:
        redis.llen.return_value = -1 if case == "negative" else True
    elif case == "timeout":
        monkeypatch.setattr(signals, "READ_TIMEOUT", 0.01)
        async def stall(*args, **kwargs):
            await asyncio.sleep(5)
        redis.scan.side_effect = stall
    else:
        redis.scan.side_effect = RuntimeError("private connection details")
    body = await signals.render_operational_signals(redis, now=NOW, backup_directory=tmp_path)
    assert sample(body, "talky_queue_observation_success", queue="dialer_lists") == 0
    assert sample(body, "talky_queue_jobs", queue="dialer_lists", state="ready") is None
    assert b"private" not in body
    assert redis.scan.await_count <= signals.MAX_SCAN_PAGES


@pytest.mark.parametrize("groups,ready", [([], None), ([{"name": "dialers", "lag": None, "pending": 2}], None),
    ([{b"name": b"dialers", b"lag": 7, b"pending": 2}], 14)])
async def test_streams_use_group_lag_not_retained_stream_length(tmp_path, monkeypatch, groups, ready):
    monkeypatch.setenv("DIALER_QUEUE_BACKEND", "streams")
    redis = redis_port(type=AsyncMock(return_value="stream"), xinfo_groups=AsyncMock(return_value=groups))
    body = await signals.render_operational_signals(redis, now=NOW, backup_directory=tmp_path)
    assert sample(body, "talky_queue_jobs", queue="dialer_streams", state="ready") == ready
    assert sample(body, "talky_queue_observation_success", queue="dialer_streams") == int(ready is not None)


async def test_backup_receipt_success_failure_and_restart_read(tmp_path):
    backup(tmp_path)
    first = await signals.render_operational_signals(None, now=NOW, backup_directory=tmp_path)
    assert sample(first, "talky_backup_observation_success") == 1
    assert sample(first, "talky_backup_last_attempt_success") == 1
    assert sample(first, "talky_backup_last_success_timestamp_seconds") == NOW - 10
    backup(tmp_path, outcome="failed", start=NOW - 8, end=NOW - 2)
    second = await signals.render_operational_signals(None, now=NOW, backup_directory=tmp_path)
    assert sample(second, "talky_backup_last_attempt_success") == 0
    assert sample(second, "talky_backup_last_success_timestamp_seconds") == NOW - 10


async def test_actual_shared_backup_publisher_matches_metrics_reader(tmp_path):
    path = Path(__file__).resolve().parents[3] / "scripts/database/backup_status.py"
    spec = importlib.util.spec_from_file_location("tested_backup_status_contract", path)
    publisher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(publisher)
    publisher.publish(tmp_path, "succeeded", int(NOW - 120), now=int(NOW - 60))
    publisher.publish(tmp_path, "failed", int(NOW - 30), now=int(NOW - 10))
    body = await signals.render_operational_signals(None, now=NOW, backup_directory=tmp_path)
    assert sample(body, "talky_backup_observation_success") == 1
    assert sample(body, "talky_backup_last_attempt_success") == 0
    assert sample(body, "talky_backup_last_success_timestamp_seconds") == NOW - 60


async def test_running_without_success_never_claims_completion(tmp_path):
    backup(tmp_path, outcome="running", end=None, success=None)
    body = await signals.render_operational_signals(None, now=NOW, backup_directory=tmp_path)
    assert sample(body, "talky_backup_running") == 1
    assert sample(body, "talky_backup_success_recorded") == 0
    assert sample(body, "talky_backup_last_attempt_success") is None
    assert sample(body, "talky_backup_last_success_timestamp_seconds") is None


@pytest.mark.parametrize("case", ["missing", "malformed", "oversized", "future", "incomplete", "directory", "extra", "permission"])
async def test_bad_backup_receipt_is_unknown(tmp_path, monkeypatch, case):
    backup(tmp_path)
    path = tmp_path / "backup-attempt.json"
    if case == "missing":
        path.unlink()
    elif case == "malformed":
        path.write_text("private malformed data")
    elif case == "oversized":
        path.write_text("x" * 1025)
    elif case == "future":
        backup(tmp_path, start=NOW + 1)
    elif case == "incomplete":
        backup(tmp_path, success=None)
    elif case == "directory":
        path.unlink()
        path.mkdir()
    elif case == "extra":
        value = json.loads(path.read_text()); value["secret"] = "private"
        path.write_text(json.dumps(value))
    else:
        monkeypatch.setattr(signals.os, "open", lambda *a, **kw: (_ for _ in ()).throw(PermissionError()))
    body = await signals.render_operational_signals(None, now=NOW, backup_directory=tmp_path)
    assert sample(body, "talky_backup_observation_success") == 0
    assert sample(body, "talky_backup_last_attempt_success") is None
    assert sample(body, "talky_backup_last_success_timestamp_seconds") is None
    assert b"private" not in body


@pytest.mark.skipif(not hasattr(os, "O_NOFOLLOW"), reason="POSIX file-type protection")
def test_backup_reader_rejects_symlink_and_fifo_without_blocking(tmp_path):
    source = tmp_path / "source"
    source.write_text("123")
    linked = tmp_path / "linked"
    linked.symlink_to(source)
    with pytest.raises(OSError):
        signals._read_small_file(linked)
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    with pytest.raises(ValueError):
        signals._read_small_file(fifo)


async def test_authenticated_metrics_exports_signals_after_authorization(monkeypatch, tmp_path):
    from fastapi import FastAPI
    import httpx
    from app.api import operational
    from app.core import container
    monkeypatch.setenv("TELEPHONY_METRICS_TOKEN", "synthetic-test-token")
    monkeypatch.setenv("TALKY_BACKUP_STATUS_DIR", str(tmp_path))
    monkeypatch.setattr(container, "get_container", lambda: SimpleNamespace(is_initialized=False))
    app = FastAPI()
    operational.register_operational_routes(app)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/metrics")).status_code == 401
        response = await client.get("/metrics", headers={"X-Metrics-Token": "synthetic-test-token"})
    assert response.status_code == 200
    assert sample(response.content, "talky_operational_signal_contract") == 1
    assert sample(response.content, "talky_backup_observation_success") == 0

