"""Retention job for number-request documents: keep by default, delete only what the rule covers."""
from __future__ import annotations

import copy
import logging
import os
import re
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import pytest

from app.domain.services.number_requests.store import DocumentStore
from app.workers import number_request_retention as nrr

NOW = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)
OLD = NOW - timedelta(days=100)
RECENT = NOW - timedelta(days=5)
SECRET_NAME = "passport-of-jane-doe.pdf"


class FakeDB:
    """In-memory tables. The SELECT filters are read from the real SQL text."""

    def __init__(self):
        self.tenants: list[str] = []
        self.requests: dict[str, dict] = {}
        self.docs: dict[str, dict] = {}
        self.fail_tenants: set[str] = set()
        self.proof: object = True  # what the account-setting proof statement answers
        self.unproven: set[str] = set()  # accounts whose proof answers False
        self.calls: list[tuple[str, str | None]] = []  # (sql head, tenant arg)

    def add(self, tenant, *, status, reviewed_at=None, released_at=None, store=None, files=True):
        if tenant not in self.tenants:
            self.tenants.append(tenant)
        rid = str(uuid.uuid4())
        self.requests[rid] = dict(id=rid, tenant_id=tenant, status=status, reviewed_at=reviewed_at,
                                  number_released_at=released_at, files_deleted_at=None)
        keys = []
        for kind in ("business_registration", "business_photo_id"):
            did = str(uuid.uuid4())
            key = f"{tenant}/{did}.tnr"
            self.docs[did] = dict(id=did, tenant_id=tenant, request_id=rid, storage_key=key, kind=kind,
                                  files_deleted_at=None, wrapped_key=b"k", file_name=SECRET_NAME)
            if files and store is not None:
                write_file(store, key)
            keys.append(key)
        return rid, keys


def write_file(store: DocumentStore, key: str, mtime: datetime | None = None):
    path = store.path_for(key)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(b"TNR1" + b"x" * 40)
    when = (mtime or NOW).timestamp()  # explicit, so the real clock never matters
    os.utime(path, (when, when))
    return path


class Conn:
    def __init__(self, db: FakeDB):
        self.db = db

    async def fetch(self, sql, *args):
        db = self.db
        self.db.calls.append((sql[:40], str(args[0]) if args and sql is not nrr.T0 else None))
        if sql == nrr.T0:
            after = args[0]
            return [{"id": t} for t in sorted(db.tenants) if after is None or t > str(after)][: args[1]]
        tenant = str(args[0])
        if tenant in db.fail_tenants:
            raise RuntimeError("boom " + SECRET_NAME)
        if "FROM telephony_number_requests" in sql:
            cutoff, limit = args[1], args[2]
            status = re.search(r"status = '(\w+)'", sql).group(1)
            col = "number_released_at" if "number_released_at" in sql else "reviewed_at"
            dated = f"{col} < $2" in sql
            rows = [r for r in db.requests.values()
                    if r["tenant_id"] == tenant and r["status"] == status
                    and ("files_deleted_at IS NULL" not in sql or r["files_deleted_at"] is None)
                    and (not dated or (r[col] is not None and r[col] < cutoff))]
            rows.sort(key=lambda r: str(r[col]))
            return [{"id": r["id"]} for r in rows[:limit]]
        if sql == nrr.R3:
            return [dict(d) for d in db.docs.values() if d["tenant_id"] == tenant
                    and d["request_id"] == str(args[1]) and d["files_deleted_at"] is None]
        if sql == nrr.R6:
            ids = {str(i) for i in args[1]}
            return [{"id": d["id"]} for d in db.docs.values()
                    if d["tenant_id"] == tenant and d["files_deleted_at"] is None and d["id"] in ids]
        raise AssertionError("unexpected fetch")

    async def fetchval(self, sql, *args):
        assert sql in (nrr.P1, nrr.P2)
        self.db.calls.append((sql[:40], str(args[0])))
        if sql == nrr.P2:
            return str(args[0]) in self.db.tenants
        if str(args[0]) in self.db.unproven:
            return False
        if isinstance(self.db.proof, Exception):
            raise self.db.proof
        return self.db.proof

    async def execute(self, sql, *args):
        db = self.db
        self.db.calls.append((sql[:40], str(args[0])))
        tenant = str(args[0])
        if sql == nrr.R4:
            for d in db.docs.values():
                if d["tenant_id"] == tenant and d["request_id"] == str(args[1]) and d["files_deleted_at"] is None:
                    d.update(files_deleted_at=NOW, wrapped_key=None, file_name=None)
            return
        if sql == nrr.R5:
            r = db.requests[str(args[1])]
            assert r["tenant_id"] == tenant
            if r["files_deleted_at"] is None:
                r["files_deleted_at"] = NOW
            return
        raise AssertionError("unexpected execute")


@pytest.fixture
def env(monkeypatch, tmp_path):
    for name in ("NUMBER_REQUEST_REJECTED_RETENTION_DAYS", "NUMBER_REQUEST_RELEASED_RETENTION_DAYS"):
        monkeypatch.delenv(name, raising=False)
    db = FakeDB()
    store = DocumentStore(str(tmp_path / "docs"))

    @asynccontextmanager
    async def acquire(_pool, tenant_id, **_kw):
        snapshot = copy.deepcopy((db.requests, db.docs))
        try:
            yield Conn(db)
        except BaseException:
            db.requests, db.docs = snapshot  # roll back
            raise

    monkeypatch.setattr(nrr, "acquire_with_tenant", acquire)
    return db, store


async def run(db, store, **kw):
    kw.setdefault("dry_run", False)
    return await nrr.purge_number_request_documents(object(), now=NOW, store=store, **kw)


def exists(store, key):
    return os.path.exists(store.path_for(key))


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [None, "", "  ", "keep", "KEEP"])
async def test_no_time_set_reads_and_deletes_nothing(env, monkeypatch, value):
    db, store = env
    if value is not None:
        monkeypatch.setenv("NUMBER_REQUEST_REJECTED_RETENTION_DAYS", value)
        monkeypatch.setenv("NUMBER_REQUEST_RELEASED_RETENTION_DAYS", value)
    t = str(uuid.uuid4())
    _, rej_keys = db.add(t, status="rejected", reviewed_at=OLD, store=store)
    _, app_keys = db.add(t, status="approved", reviewed_at=OLD, released_at=OLD, store=store)
    result = await run(db, store)
    assert result["rules"] == "none" and result["requests"] == 0
    assert all(exists(store, k) for k in rej_keys + app_keys)
    assert all(r["files_deleted_at"] is None for r in db.requests.values())
    assert all(d["wrapped_key"] is not None and d["file_name"] for d in db.docs.values())
    assert db.calls == []  # not even the account list was read


@pytest.mark.asyncio
async def test_rejected_rule_deletes_old_rejected_only(env, monkeypatch):
    db, store = env
    monkeypatch.setenv("NUMBER_REQUEST_REJECTED_RETENTION_DAYS", "30")
    t = str(uuid.uuid4())
    old, old_keys = db.add(t, status="rejected", reviewed_at=OLD, store=store)
    new, new_keys = db.add(t, status="rejected", reviewed_at=RECENT, store=store)
    sub, sub_keys = db.add(t, status="submitted", store=store)
    app, app_keys = db.add(t, status="approved", reviewed_at=OLD, store=store)
    result = await run(db, store)
    assert result["rules"] == "rejected" and result["requests"] == 1 and result["files_removed"] == 2
    assert not any(exists(store, k) for k in old_keys)
    assert all(exists(store, k) for k in new_keys + sub_keys + app_keys)
    assert db.requests[old]["files_deleted_at"] is not None
    for d in db.docs.values():
        if d["request_id"] == old:
            assert d["files_deleted_at"] and d["wrapped_key"] is None and d["file_name"] is None
    for rid in (new, sub, app):
        assert db.requests[rid]["files_deleted_at"] is None
    assert all(d["wrapped_key"] is not None for d in db.docs.values() if d["request_id"] != old)


@pytest.mark.asyncio
async def test_approved_without_release_date_is_never_deleted(env, monkeypatch):
    db, store = env
    monkeypatch.setenv("NUMBER_REQUEST_REJECTED_RETENTION_DAYS", "1")
    monkeypatch.setenv("NUMBER_REQUEST_RELEASED_RETENTION_DAYS", "1")
    t = str(uuid.uuid4())
    no_date, keys_a = db.add(t, status="approved", reviewed_at=OLD, released_at=None, store=store)
    released, keys_b = db.add(t, status="approved", reviewed_at=OLD, released_at=OLD, store=store)
    result = await run(db, store)
    assert all(exists(store, k) for k in keys_a)
    assert db.requests[no_date]["files_deleted_at"] is None
    assert not any(exists(store, k) for k in keys_b)
    assert db.requests[released]["files_deleted_at"] is not None
    assert result["requests"] == 1


@pytest.mark.asyncio
async def test_safe_to_run_twice(env, monkeypatch):
    db, store = env
    monkeypatch.setenv("NUMBER_REQUEST_REJECTED_RETENTION_DAYS", "30")
    t = str(uuid.uuid4())
    db.add(t, status="rejected", reviewed_at=OLD, store=store)
    first = await run(db, store)
    second = await run(db, store)
    assert first["requests"] == 1
    assert second["requests"] == 0 and second["files_removed"] == 0 and second["error"] is None


@pytest.mark.asyncio
async def test_missing_file_still_marks_row_and_is_logged(env, monkeypatch, caplog):
    db, store = env
    monkeypatch.setenv("NUMBER_REQUEST_REJECTED_RETENTION_DAYS", "30")
    t = str(uuid.uuid4())
    rid, keys = db.add(t, status="rejected", reviewed_at=OLD, store=store)
    os.unlink(store.path_for(keys[0]))
    with caplog.at_level(logging.INFO, logger="number_request_retention"):
        result = await run(db, store)
    assert db.requests[rid]["files_deleted_at"] is not None
    assert result["already_missing"] == 1 and result["files_removed"] == 1
    assert any("number_request_file_already_missing" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_interrupted_after_marking_is_completed_by_next_run(env, monkeypatch):
    """Rows committed, file removal crashed: the key is gone and the sweep removes the file."""
    db, store = env
    monkeypatch.setenv("NUMBER_REQUEST_REJECTED_RETENTION_DAYS", "30")
    t = str(uuid.uuid4())
    rid, keys = db.add(t, status="rejected", reviewed_at=OLD, store=store)
    real_remove = store.remove

    def crash(key):
        raise OSError("disk went away")

    store.remove = crash
    first = await run(db, store)
    assert first["remove_failed"] == 2 and exists(store, keys[0])
    assert db.requests[rid]["files_deleted_at"] is not None  # marked, never "file gone, row unmarked"
    assert all(d["wrapped_key"] is None for d in db.docs.values())
    store.remove = real_remove
    for k in keys:  # files are now older than the grace
        old = NOW - timedelta(hours=2)
        os.utime(store.path_for(k), (old.timestamp(), old.timestamp()))
    second = await run(db, store)
    assert second["orphans_removed"] == 2
    assert not any(exists(store, k) for k in keys)


@pytest.mark.asyncio
async def test_failure_before_commit_rolls_back_and_removes_no_file(env, monkeypatch):
    db, store = env
    monkeypatch.setenv("NUMBER_REQUEST_REJECTED_RETENTION_DAYS", "30")
    t = str(uuid.uuid4())
    rid, keys = db.add(t, status="rejected", reviewed_at=OLD, store=store)
    orig = Conn.execute

    async def bad(self, sql, *a):
        if sql == nrr.R5:
            raise RuntimeError("db down")
        return await orig(self, sql, *a)

    monkeypatch.setattr(Conn, "execute", bad)
    result = await run(db, store)
    assert result["account_failures"] == 1 and result["error"] is None
    assert all(exists(store, k) for k in keys)
    assert db.requests[rid]["files_deleted_at"] is None
    assert all(d["wrapped_key"] is not None for d in db.docs.values())


@pytest.mark.asyncio
async def test_one_account_failing_does_not_touch_or_block_another(env, monkeypatch):
    db, store = env
    monkeypatch.setenv("NUMBER_REQUEST_REJECTED_RETENTION_DAYS", "30")
    a, b = sorted([str(uuid.uuid4()), str(uuid.uuid4())])
    ra, ka = db.add(a, status="rejected", reviewed_at=OLD, store=store)
    rb, kb = db.add(b, status="rejected", reviewed_at=OLD, store=store)
    db.fail_tenants.add(a)
    result = await run(db, store)
    assert result["account_failures"] == 1
    assert all(exists(store, k) for k in ka) and db.requests[ra]["files_deleted_at"] is None
    assert not any(exists(store, k) for k in kb) and db.requests[rb]["files_deleted_at"] is not None


@pytest.mark.asyncio
async def test_every_statement_carries_its_own_account(env, monkeypatch):
    db, store = env
    monkeypatch.setenv("NUMBER_REQUEST_REJECTED_RETENTION_DAYS", "30")
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    db.add(a, status="rejected", reviewed_at=OLD, store=store)
    rb, _ = db.add(b, status="rejected", reviewed_at=OLD, store=store)
    await run(db, store)
    for sql in (nrr.R1, nrr.R2, nrr.R3, nrr.R4, nrr.R5, nrr.R6):
        assert "tenant_id = $1" in sql
    assert all(d["files_deleted_at"] for d in db.docs.values())


@pytest.mark.asyncio
async def test_leftover_temp_files_older_than_grace_removed_newer_kept(env):
    db, store = env
    incoming = store.incoming_dir()
    os.makedirs(incoming)
    old = os.path.join(incoming, "a" * 32 + ".part")
    new = os.path.join(incoming, "b" * 32 + ".part")
    for p, when in ((old, NOW - timedelta(hours=2)), (new, NOW - timedelta(minutes=10))):
        open(p, "wb").write(b"x")
        os.utime(p, (when.timestamp(), when.timestamp()))
    result = await run(db, store)
    assert not os.path.exists(old) and os.path.exists(new)
    assert result["orphans_removed"] == 1 and result["rules"] == "none"


@pytest.mark.asyncio
async def test_orphan_sweep_keeps_young_and_live_files_removes_old_orphans(env, caplog):
    db, store = env
    t = str(uuid.uuid4())
    _, live_keys = db.add(t, status="submitted", store=store, files=False)
    two_hours = NOW - timedelta(hours=2)
    for k in live_keys:
        write_file(store, k, two_hours)  # old but live
    young = f"{t}/{uuid.uuid4()}.tnr"
    orphan = f"{t}/{uuid.uuid4()}.tnr"
    write_file(store, young, NOW - timedelta(minutes=5))
    write_file(store, orphan, two_hours)
    with caplog.at_level(logging.INFO, logger="number_request_retention"):
        result = await run(db, store)
    assert exists(store, young) and all(exists(store, k) for k in live_keys)
    assert not exists(store, orphan) and result["orphans_removed"] == 1


@pytest.mark.asyncio
async def test_orphan_sweep_never_deletes_when_the_row_check_fails(env):
    db, store = env
    t = str(uuid.uuid4())
    key = f"{t}/{uuid.uuid4()}.tnr"
    db.tenants.append(t)
    write_file(store, key, NOW - timedelta(hours=3))
    db.fail_tenants.add(t)
    result = await run(db, store)
    assert exists(store, key) and result["account_failures"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("proof", [False, None, RuntimeError("setting lost")])
async def test_sweep_deletes_nothing_when_account_setting_is_not_proven(env, caplog, proof):
    """A connection whose account setting did not take effect reads no rows: not proof of orphans."""
    db, store = env
    t = str(uuid.uuid4())
    key = f"{t}/{uuid.uuid4()}.tnr"
    db.tenants.append(t)
    write_file(store, key, NOW - timedelta(hours=3))
    db.proof = proof  # R6 would return no rows
    with caplog.at_level(logging.INFO, logger="number_request_retention"):
        result = await run(db, store)
    assert exists(store, key)
    assert result["orphans_removed"] == 0 and result["account_failures"] == 1
    assert any("number_request_orphan_check_unproven tenant=" + t in r.getMessage()
               and r.levelno == logging.ERROR for r in caplog.records)


@pytest.mark.asyncio
async def test_sweep_with_proof_true_and_no_row_removes_old_orphan_keeps_live(env):
    db, store = env
    t = str(uuid.uuid4())
    _, live_keys = db.add(t, status="submitted", store=store, files=False)
    orphan = f"{t}/{uuid.uuid4()}.tnr"
    for k in (*live_keys, orphan):
        write_file(store, k, NOW - timedelta(hours=3))
    db.proof = True
    result = await run(db, store)
    assert not exists(store, orphan) and all(exists(store, k) for k in live_keys)
    assert result["orphans_removed"] == 1 and result["account_failures"] == 0


@pytest.mark.asyncio
async def test_missing_root_is_skipped(env):
    db, store = env
    result = await run(db, store)
    assert result["error"] is None and result["orphans_removed"] == 0


@pytest.mark.asyncio
async def test_logs_carry_ids_only_never_a_file_name(env, monkeypatch, caplog):
    db, store = env
    monkeypatch.setenv("NUMBER_REQUEST_REJECTED_RETENTION_DAYS", "30")
    a, b = sorted([str(uuid.uuid4()), str(uuid.uuid4())])
    db.add(a, status="rejected", reviewed_at=OLD, store=store)
    db.add(b, status="rejected", reviewed_at=OLD, store=store)
    db.fail_tenants.add(b)  # the failure message itself contains the secret name
    orphan = f"{a}/{uuid.uuid4()}.tnr"
    write_file(store, orphan, NOW - timedelta(hours=3))
    with caplog.at_level(logging.DEBUG):
        await run(db, store)
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "number_request_files_deleted" in text and "number_request_retention rules=rejected" in text
    assert "number_request_orphan_removed" in text
    assert SECRET_NAME not in text and "jane" not in text.lower() and ".tnr" not in text


@pytest.mark.asyncio
async def test_job_never_raises_into_the_nightly_cleanup(env, monkeypatch, caplog):
    db, store = env
    monkeypatch.setenv("NUMBER_REQUEST_REJECTED_RETENTION_DAYS", "30")

    async def boom(_pool):
        raise RuntimeError("no database " + SECRET_NAME)

    monkeypatch.setattr(nrr, "_account_ids", boom)
    with caplog.at_level(logging.INFO):
        result = await run(db, store)
    assert result["error"] == "RuntimeError"
    assert SECRET_NAME not in "\n".join(r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_limit_caps_requests_per_run(env, monkeypatch):
    db, store = env
    monkeypatch.setenv("NUMBER_REQUEST_REJECTED_RETENTION_DAYS", "30")
    t = str(uuid.uuid4())
    for _ in range(3):
        db.add(t, status="rejected", reviewed_at=OLD, store=store)
    result = await run(db, store, limit=2)
    assert result["requests"] == 2


def test_cleanup_worker_calls_the_job_inside_its_try():
    src = open(os.path.join(os.path.dirname(__file__), "..", "..", "app", "workers", "cleanup_worker.py")).read()
    call = 'summary["number_request_documents"] = await purge_number_request_documents(pool, dry_run=dry_run)'
    assert call in src
    assert src.index('summary["recordings_retention"]') < src.index(call) < src.index("except Exception as exc:\n        logger.error(\"cleanup_failed")


# --- D1: the clean-up's dry-run setting -------------------------------------------


def _seed_dry_run_world(db, store, monkeypatch):
    monkeypatch.setenv("NUMBER_REQUEST_REJECTED_RETENTION_DAYS", "30")
    a, b = sorted([str(uuid.uuid4()), str(uuid.uuid4())])
    _, ka = db.add(a, status="rejected", reviewed_at=OLD, store=store)
    _, kb = db.add(b, status="rejected", reviewed_at=OLD, store=store)
    orphan = f"{a}/{uuid.uuid4()}.tnr"
    write_file(store, orphan, NOW - timedelta(hours=3))
    incoming = store.incoming_dir()
    os.makedirs(incoming)
    temp = os.path.join(incoming, "c" * 32 + ".part")
    open(temp, "wb").write(b"x")
    os.utime(temp, ((NOW - timedelta(hours=2)).timestamp(),) * 2)
    return ka + kb, orphan, temp


@pytest.mark.asyncio
async def test_dry_run_changes_nothing_and_counts_what_it_would_do(env, monkeypatch, caplog):
    db, store = env
    keys, orphan, temp = _seed_dry_run_world(db, store, monkeypatch)
    before = copy.deepcopy((db.requests, db.docs))
    with caplog.at_level(logging.INFO, logger="number_request_retention"):
        result = await run(db, store, dry_run=True)
    assert all(exists(store, k) for k in keys) and exists(store, orphan) and os.path.exists(temp)
    assert (db.requests, db.docs) == before
    assert not [c for c in db.calls if c[0].lstrip().upper().startswith("UPDATE")]
    assert result["dry_run"] is True
    assert (result["would_retire"], result["would_remove_files"],
            result["would_remove_orphans"], result["would_remove_temps"]) == (2, 4, 1, 1)
    assert (result["requests"], result["files_removed"], result["orphans_removed"]) == (0, 0, 0)
    assert any(r.getMessage().startswith("number_request_retention_dry_run ")
               and "would_retire=2" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_not_dry_run_deletes_as_before(env, monkeypatch):
    db, store = env
    keys, orphan, temp = _seed_dry_run_world(db, store, monkeypatch)
    result = await run(db, store, dry_run=False)
    assert result["dry_run"] is False and result["requests"] == 2
    assert not any(exists(store, k) for k in keys) and not exists(store, orphan)
    assert not os.path.exists(temp)
    assert (result["would_retire"], result["would_remove_files"]) == (0, 0)


@pytest.mark.asyncio
async def test_purge_without_dry_run_raises_type_error(env):
    db, store = env
    with pytest.raises(TypeError):
        await nrr.purge_number_request_documents(object(), now=NOW, store=store)


@pytest.mark.asyncio
@pytest.mark.parametrize("setting,expected", [(None, True), ("false", False), ("true", True)])
async def test_cleanup_worker_main_passes_its_own_dry_run(monkeypatch, setting, expected):
    import app.core.db as core_db
    from app.workers import cleanup_worker

    seen = {}

    async def init():
        return "pool"

    async def close():
        return None

    async def telemetry(pool, **_kw):
        return {}

    async def recordings(pool, **_kw):
        return {}

    async def purge(pool, **kw):
        seen.update(kw)
        return {}

    monkeypatch.setattr(core_db, "init_db_pool", init)
    monkeypatch.setattr(core_db, "close_db_pool", close)
    monkeypatch.setattr(cleanup_worker, "cleanup_old_records", telemetry)
    monkeypatch.setattr(cleanup_worker, "purge_expired_recordings", recordings)
    monkeypatch.setattr(cleanup_worker, "purge_number_request_documents", purge)
    if setting is None:
        monkeypatch.delenv("CLEANUP_DRY_RUN", raising=False)
    else:
        monkeypatch.setenv("CLEANUP_DRY_RUN", setting)
    assert await cleanup_worker.main() == 0
    assert seen["dry_run"] is expected


# --- D2: the account must exist; the brake ------------------------------------------


def _old_files(store, tenant, n):
    keys = [f"{tenant}/{uuid.uuid4()}.tnr" for _ in range(n)]
    for k in keys:
        write_file(store, k, NOW - timedelta(hours=3))
    return keys


@pytest.mark.asyncio
async def test_sweep_deletes_nothing_for_an_account_not_in_this_database(env, caplog):
    db, store = env
    t = str(uuid.uuid4())  # never registered: the database does not know this account
    keys = _old_files(store, t, 2)
    with caplog.at_level(logging.INFO, logger="number_request_retention"):
        result = await run(db, store)
    assert all(exists(store, k) for k in keys)
    assert result["orphans_removed"] == 0 and result["account_failures"] == 1
    assert any(r.levelno == logging.ERROR
               and "number_request_orphan_check_unknown_account tenant=" + t in r.getMessage()
               for r in caplog.records)


@pytest.mark.asyncio
async def test_brake_21_candidates_across_accounts_removes_none(env, caplog):
    db, store = env
    tenants = [str(uuid.uuid4()) for _ in range(3)]
    db.tenants.extend(tenants)
    keys = [k for t in tenants for k in _old_files(store, t, 7)]
    with caplog.at_level(logging.INFO, logger="number_request_retention"):
        result = await run(db, store)
    assert len(keys) == 21 and all(exists(store, k) for k in keys)
    assert result["error"] == "sweep_brake" and result["orphans_removed"] == 0
    assert any(r.levelno == logging.ERROR
               and "number_request_sweep_brake candidates=21 limit=20" in r.getMessage()
               for r in caplog.records)


@pytest.mark.asyncio
async def test_brake_20_candidates_are_all_removed(env):
    db, store = env
    tenants = [str(uuid.uuid4()) for _ in range(2)]
    db.tenants.extend(tenants)
    keys = _old_files(store, tenants[0], 10) + _old_files(store, tenants[1], 10)
    result = await run(db, store)
    assert not any(exists(store, k) for k in keys)
    assert result["orphans_removed"] == 20 and result["error"] is None


@pytest.mark.asyncio
async def test_unproven_account_is_not_a_candidate_for_the_brake(env):
    db, store = env
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    db.tenants.extend([a, b])
    db.unproven.add(b)
    ka, kb = _old_files(store, a, 15), _old_files(store, b, 15)
    result = await run(db, store)
    assert not any(exists(store, k) for k in ka) and all(exists(store, k) for k in kb)
    assert result["orphans_removed"] == 15 and result["account_failures"] == 1
    assert result["error"] is None


def test_brake_constant_is_20():
    from app.domain.services.number_requests import config
    assert config.SWEEP_MAX_REMOVALS_PER_RUN == 20


# --- D3: a mistyped retention time --------------------------------------------------

NAME = "NUMBER_REQUEST_REJECTED_RETENTION_DAYS"


@pytest.mark.parametrize("raw,days,problem", [
    ("30d", None, True), ("1e3", None, True), ("1_000", None, True), ("+7", None, True),
    ("-1", None, True), ("1000000", None, True), ("36501", None, True),
    ("٣٠", None, True), (" 30 ", 30, False), ("30", 30, False), ("36500", 36500, False),
    ("1", 1, False), ("0", None, False), ("keep", None, False), ("KEEP", None, False),
    ("", None, False), ("   ", None, False), (None, None, False),
])
def test_retention_setting_parsing(monkeypatch, raw, days, problem):
    from app.domain.services.number_requests import config
    monkeypatch.delenv("NUMBER_REQUEST_RELEASED_RETENTION_DAYS", raising=False)
    if raw is None:
        monkeypatch.delenv(NAME, raising=False)
    else:
        monkeypatch.setenv(NAME, raw)
    assert config.rejected_retention_days() == days
    assert config.retention_setting_problems() == ([NAME] if problem else [])


@pytest.mark.asyncio
async def test_overflowing_time_does_not_stop_the_sweep_and_is_reported(env, monkeypatch, caplog):
    db, store = env
    monkeypatch.setenv(NAME, "1000000")
    t = str(uuid.uuid4())
    db.tenants.append(t)
    orphan = _old_files(store, t, 1)[0]
    with caplog.at_level(logging.INFO, logger="number_request_retention"):
        result = await run(db, store)
    assert result["error"] is None and result["rules"] == "none"
    assert result["invalid_settings"] == [NAME]
    assert not exists(store, orphan)  # the sweep still ran
    msgs = [r for r in caplog.records if "number_request_retention_setting_invalid name=" + NAME in r.getMessage()]
    assert msgs and msgs[0].levelno == logging.ERROR and "1000000" not in msgs[0].getMessage()


# --- D4: failures are visible ---------------------------------------------------------


def _final(caplog):
    lines = [r for r in caplog.records if r.getMessage().startswith("number_request_retention rules=")]
    assert len(lines) == 1
    return lines[0]


@pytest.mark.asyncio
async def test_final_line_is_info_when_all_is_well(env, caplog):
    db, store = env
    with caplog.at_level(logging.INFO, logger="number_request_retention"):
        await run(db, store)
    rec = _final(caplog)
    assert rec.levelno == logging.INFO
    for key in ("account_failures=0", "error=none", "remove_failed=0", "already_missing=0"):
        assert key in rec.getMessage()


@pytest.mark.asyncio
async def test_final_line_is_error_on_account_failure(env, caplog):
    db, store = env
    _old_files(store, str(uuid.uuid4()), 1)  # unknown account -> account failure
    with caplog.at_level(logging.INFO, logger="number_request_retention"):
        await run(db, store)
    rec = _final(caplog)
    assert rec.levelno == logging.ERROR and "account_failures=1" in rec.getMessage()


@pytest.mark.asyncio
async def test_final_line_is_error_on_error_set(env, caplog):
    db, store = env
    t = str(uuid.uuid4())
    db.tenants.append(t)
    _old_files(store, t, 21)  # brake -> error=sweep_brake
    with caplog.at_level(logging.INFO, logger="number_request_retention"):
        await run(db, store)
    rec = _final(caplog)
    assert rec.levelno == logging.ERROR and "error=sweep_brake" in rec.getMessage()


@pytest.mark.asyncio
async def test_final_line_is_error_on_remove_failed(env, caplog):
    db, store = env
    t = str(uuid.uuid4())
    db.tenants.append(t)
    key = _old_files(store, t, 1)[0]

    def fail(_key):
        raise OSError("x")

    store.remove = fail
    with caplog.at_level(logging.INFO, logger="number_request_retention"):
        result = await run(db, store)
    assert result["remove_failed"] == 1 and exists(store, key)
    rec = _final(caplog)
    assert rec.levelno == logging.ERROR and "remove_failed=1" in rec.getMessage()


@pytest.mark.asyncio
async def test_final_line_is_error_on_invalid_setting(env, monkeypatch, caplog):
    db, store = env
    monkeypatch.setenv(NAME, "30d")
    with caplog.at_level(logging.INFO, logger="number_request_retention"):
        await run(db, store)
    rec = _final(caplog)
    assert rec.levelno == logging.ERROR and "invalid_settings=" + NAME in rec.getMessage()
