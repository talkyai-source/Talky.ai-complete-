"""Recording save workflow under acquisition-only failures; no external storage."""

import asyncio
import errno
from contextlib import asynccontextmanager
import re
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.core.db import DatabasePoolTimeoutError
from app.domain.services import recording_policy_service as policy
from app.domain.services import recording_service as recording

# A realistic mixed-hex UUID: the global privacy filter treats an all-numeric
# synthetic UUID as phone-like text. Keep the complete call-ID log assertion.
CALL = "a1b2c3d4-e5f6-47a8-8b9c-d0e1f2a3b4c5"
TENANT = "20000000-0000-4000-8000-000000000004"
CAMPAIGN = "30000000-0000-4000-8000-000000000004"
RECORDING = UUID("40000000-0000-4000-8000-000000000004")


class Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    async def sleep(self, delay):
        assert 0 < delay <= 5
        self.sleeps.append(delay)
        self.now += delay


class Connection:
    def __init__(self):
        self.owned = True
        self.mode = "one_party"
        self.inserts = []
        self.rows = []
        self.links = []
        self.failed_rows = []
        self.unexpected = []
        self.insert_error = None
        self.ambiguous_commit = False
        self.commit_error = None
        self.transaction_depth = 0

    @asynccontextmanager
    async def transaction(self):
        self.transaction_depth += 1
        try:
            yield self
            if self.transaction_depth == 1 and self.rows and self.commit_error is not None:
                error, self.commit_error = self.commit_error, None
                raise error
        finally:
            self.transaction_depth -= 1

    async def execute(self, sql, *args):
        query = " ".join(sql.split())
        limit = re.fullmatch(r"SET LOCAL (statement_timeout|lock_timeout) = '([1-9][0-9]*)ms'", query)
        if limit:
            assert not args and 1 <= int(limit.group(2)) <= 2_147_483_647
            return "SET"
        if query == f"SET LOCAL app.current_tenant_id = '{TENANT}'":
            assert args == ()
            return "SET"
        if query == "SELECT set_config('app.current_tenant_id', $1, true)":
            assert args == (TENANT,)
            return "SELECT 1"
        if query == (
            "INSERT INTO recordings_s3 ( call_id, tenant_id, campaign_id, "
            "s3_bucket, s3_key, s3_region, status ) "
            "VALUES ($1,$2,$3,$4,$5,$6,'failed') ON CONFLICT DO NOTHING"
        ):
            assert args == (
                UUID(CALL), UUID(TENANT), UUID(CAMPAIGN),
                "local", f"failed/{CALL}.wav", "local",
            )
            self.failed_rows.append(args)
            return "INSERT 0 1"
        if query == "UPDATE calls SET recording_url = $1, updated_at = NOW() WHERE id = $2":
            assert args == (f"/api/v1/recordings/{RECORDING}/stream", UUID(CALL))
            self.links.append(args)
            return "UPDATE 1"
        self.unexpected.append(query)
        raise AssertionError(f"Unexpected SQL: {query}")

    async def fetchrow(self, sql, *args):
        query = " ".join(sql.split())
        if query.startswith("SELECT id FROM calls WHERE tenant_id = $1 "):
            assert args == (UUID(TENANT), UUID(CALL), CALL)
            return {"id": UUID(CALL)} if self.owned else None
        if query == "SELECT * FROM tenant_recording_policy WHERE tenant_id = $1":
            assert args == (TENANT,)
            return {
                "default_consent_mode": self.mode, "retention_days": 90,
                "announcement_text": "Synthetic recording notice", "opt_out_dtmf_digit": None,
                "two_party_country_codes": [],
            }
        if query.startswith("INSERT INTO recordings_s3 (") and query.endswith("RETURNING id"):
            assert args[:3] == (UUID(CALL), UUID(TENANT), UUID(CAMPAIGN))
            self.inserts.append(args)
            if self.ambiguous_commit:
                self.rows.append(args)
            if self.insert_error is not None:
                raise self.insert_error
            self.rows.append(args)
            return {"id": RECORDING}
        self.unexpected.append(query)
        raise AssertionError(f"Unexpected SQL: {query}")

    async def fetchval(self, sql, *args):
        query = " ".join(sql.split())
        if query == (
            "SELECT EXISTS ( SELECT 1 FROM recordings_s3 "
            "WHERE tenant_id = $1 AND s3_bucket = $2 AND s3_key = $3 )"
        ):
            assert args[0] == UUID(TENANT)
            return bool(self.rows)
        self.unexpected.append(query)
        raise AssertionError(f"Unexpected SQL: {query}")


class Pool:
    def __init__(self, conn, clock):
        self.conn, self.clock = conn, clock
        self.fail_at = set()
        self.fail_from = None
        self.cancel = None
        self.attempts = 0
        self.leases = 0
        self.releases = 0
        self.attempt_timeout = 10.0

    @asynccontextmanager
    async def acquire(self):
        self.attempts += 1
        if self.cancel is not None:
            raise self.cancel
        if self.attempts in self.fail_at or (
            self.fail_from is not None and self.attempts >= self.fail_from
        ):
            # Virtual acquisition elapsed time, not a wall-clock sleep. The
            # production acquire_with_tenant context still handles this error.
            self.clock.now += self.attempt_timeout
            raise DatabasePoolTimeoutError("Synthetic saturated recording pool")
        self.leases += 1
        try:
            yield self.conn
        finally:
            self.releases += 1


@pytest.fixture
def fixture(monkeypatch, tmp_path):
    clock, conn = Clock(), Connection()
    pool = Pool(conn, clock)
    original_acquire = recording.acquire_with_tenant

    def observe_acquire(db_pool, tenant, **kwargs):
        assert db_pool is pool and tenant == TENANT
        pool.attempt_timeout = kwargs.get("timeout", 10.0)
        return original_acquire(db_pool, tenant, **kwargs)

    monkeypatch.setattr(recording, "acquire_with_tenant", observe_acquire)
    monkeypatch.setattr(recording, "time", SimpleNamespace(monotonic=clock.monotonic))
    monkeypatch.setattr(recording.asyncio, "sleep", clock.sleep)
    monkeypatch.setattr(recording, "encode_recording_audio", lambda raw: (raw, ".wav", "audio/wav"))
    monkeypatch.setenv("LOCAL_RECORDINGS_DIR", str(tmp_path))
    uploads, deletes = [], []
    s3 = SimpleNamespace(
        is_available=lambda: True, bucket="synthetic", region="synthetic",
        upload=lambda *args, **kwargs: uploads.append((args, kwargs)),
        delete_permanently=lambda *args: deletes.append(args),
    )
    service = recording.RecordingService(pool, s3_client=s3)
    buffer = recording.RecordingBuffer(CALL)
    buffer.add_chunk(b"\0\0" * 80)
    policy.clear_disclosure_state(CALL)
    # F12-04 is now in the round-3 base: an unknown callee requires a
    # notice even for a one-party tenant. Positive fixtures explicitly
    # establish a known one-party country; tests below cover notice gates.
    policy.record_callee_country("PK", CALL)
    yield SimpleNamespace(
        clock=clock, conn=conn, pool=pool, uploads=uploads, deletes=deletes,
        service=service, buffer=buffer, directory=tmp_path,
    )
    policy.clear_disclosure_state(CALL)
    assert conn.unexpected == []
    assert pool.leases == pool.releases


async def save(fx, entry="cloud"):
    method = fx.service._save_local if entry == "local" else fx.service.save_recording
    return await method(CALL, fx.buffer, TENANT, CAMPAIGN)


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["cloud", "local"])
@pytest.mark.parametrize("failure_at", [1, 3, 5, 6], ids=["admission", "recheck", "insert", "link"])
async def test_c4_second_acquisition_saves_and_links_once(fixture, entry, failure_at):
    fixture.pool.fail_at = {failure_at}
    assert await save(fixture, entry) == str(RECORDING)
    assert len(fixture.clock.sleeps) == 1
    assert len(fixture.conn.inserts) == len(fixture.conn.rows) == len(fixture.conn.links) == 1
    assert fixture.deletes == []
    if entry == "cloud":
        assert len(fixture.uploads) == 1
    else:
        assert len(list(fixture.directory.rglob("*.wav"))) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_from", [1, 5, 6], ids=["admission", "insert", "link"])
async def test_c4_exhaustion_is_bounded_and_logs_call_id(fixture, caplog, failure_from):
    fixture.pool.fail_from = failure_from
    result = await save(fixture)
    assert result == (str(RECORDING) if failure_from == 6 else None)
    assert 50 <= fixture.clock.now <= 60.01
    assert any(
        item.levelname == "ERROR" and "recording_db_pool_retry_exhausted" in item.message
        and CALL in item.message for item in caplog.records
    )
    assert len(fixture.uploads) <= 1
    assert len(fixture.conn.inserts) <= 1
    assert fixture.deletes == []  # no unverified orphan deletion during outage


@pytest.mark.asyncio
async def test_c4_admission_and_insert_share_one_retry_budget(fixture, caplog):
    fixture.pool.fail_at = {1, 2, 3, 8, 9, 10}
    assert await save(fixture) is None
    assert 50 <= fixture.clock.now <= 60.01
    assert len(fixture.uploads) == 1
    assert fixture.conn.inserts == []
    assert any("recording_db_pool_retry_exhausted" in item.message and CALL in item.message for item in caplog.records)


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [
    TimeoutError("Synthetic statement timeout"),
    OSError("Synthetic uncertain insert"),
    DatabasePoolTimeoutError("Synthetic typed error from SQL, not admission"),
])
async def test_c4_query_failure_never_replays_upload_or_ambiguous_insert(fixture, error):
    fixture.conn.insert_error = error
    fixture.conn.ambiguous_commit = True
    assert await save(fixture) is None
    assert fixture.clock.sleeps == []
    assert len(fixture.uploads) == len(fixture.conn.inserts) == len(fixture.conn.rows) == 1
    assert fixture.conn.links == []
    assert fixture.deletes == []


@pytest.mark.asyncio
async def test_c4_actual_transaction_exit_failure_never_replays_committed_insert(fixture):
    fixture.conn.commit_error = OSError("Synthetic connection lost while COMMIT outcome is unknown")
    assert await save(fixture) is None
    assert fixture.conn.commit_error is None  # error occurred at outer context exit
    assert fixture.conn.transaction_depth == 0
    assert fixture.clock.sleeps == []
    assert len(fixture.uploads) == len(fixture.conn.inserts) == len(fixture.conn.rows) == 1
    assert fixture.conn.links == []
    assert fixture.deletes == []  # a separate lease observes the referenced object
    assert fixture.pool.leases == fixture.pool.releases


@pytest.mark.asyncio
@pytest.mark.parametrize("gate", ["foreign-call", "disabled", "disclosure-missing"])
async def test_c4_retry_preserves_ownership_and_consent_gates(fixture, gate):
    fixture.pool.fail_at = {1}
    if gate == "foreign-call":
        fixture.conn.owned = False
    else:
        fixture.conn.mode = "disabled" if gate == "disabled" else "two_party"
    assert await save(fixture) is None
    assert len(fixture.clock.sleeps) == 1
    assert fixture.uploads == fixture.conn.inserts == fixture.conn.links == []


@pytest.mark.asyncio
async def test_c4_caller_cancellation_is_not_retried_or_swallowed(fixture):
    cancellation = asyncio.CancelledError()
    fixture.pool.cancel = cancellation
    with pytest.raises(asyncio.CancelledError) as raised:
        await save(fixture)
    assert raised.value is cancellation
    assert fixture.pool.attempts == 1 and fixture.clock.sleeps == []
    assert fixture.uploads == []


@pytest.mark.asyncio
async def test_c4_policy_timeout_remains_fail_closed_without_retry(fixture, caplog):
    fixture.pool.fail_at = {2}
    assert await save(fixture) is None
    assert fixture.uploads == fixture.conn.inserts == fixture.conn.links == []
    assert fixture.clock.sleeps == []
    assert "recording_policy_lookup_failed" in caplog.text



@pytest.mark.asyncio
async def test_c4_orphan_check_retries_admission_without_replaying_insert(fixture):
    fixture.conn.insert_error = OSError("Synthetic rejected INSERT")
    fixture.pool.fail_at = {6}
    assert await save(fixture) is None
    assert len(fixture.clock.sleeps) == 1
    assert len(fixture.uploads) == len(fixture.conn.inserts) == 1
    assert fixture.conn.rows == fixture.conn.links == []
    assert len(fixture.deletes) == 1  # deletion follows an actual unreferenced check


@pytest.mark.asyncio
async def test_c4_upload_duration_does_not_consume_acquisition_budget(fixture):
    original_upload = fixture.service._s3.upload

    def upload(*args, **kwargs):
        fixture.clock.now += 120.0
        return original_upload(*args, **kwargs)

    fixture.service._s3.upload = upload
    fixture.pool.fail_at = {5}
    assert await save(fixture) == str(RECORDING)
    assert len(fixture.clock.sleeps) == 1
    assert len(fixture.uploads) == len(fixture.conn.inserts) == len(fixture.conn.links) == 1


@pytest.mark.asyncio
async def test_c4_cancellation_during_backoff_propagates_without_another_attempt(
    fixture, monkeypatch,
):
    cancellation = asyncio.CancelledError("synthetic caller cancellation")

    async def cancelled_sleep(delay):
        raise cancellation

    monkeypatch.setattr(recording.asyncio, "sleep", cancelled_sleep)
    fixture.pool.fail_at = {1}
    with pytest.raises(asyncio.CancelledError) as raised:
        await save(fixture)
    assert raised.value is cancellation
    assert fixture.pool.attempts == 1
    assert fixture.uploads == fixture.conn.inserts == []


@pytest.mark.asyncio
async def test_c4_retry_preserves_local_disk_failure_record(fixture, monkeypatch):
    writes = []

    def full_disk(*args):
        writes.append(args)
        raise OSError(errno.ENOSPC, "Synthetic full disk")

    monkeypatch.setattr(recording, "_write_wav_file", full_disk)
    fixture.pool.fail_at = {1}
    assert await save(fixture, "local") is None
    assert len(fixture.clock.sleeps) == len(writes) == 1
    assert len(fixture.conn.failed_rows) == 1
    assert fixture.uploads == fixture.conn.inserts == fixture.conn.links == []
    assert not list(fixture.directory.rglob("*.wav"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("country", "disclosure", "allowed"),
    [("PK", None, True), ("GB", None, False), (None, None, False), ("GB", "spoken", True)],
)
async def test_c4_retry_preserves_recorded_callee_country_notice_gate(
    fixture, country, disclosure, allowed,
):
    policy.clear_disclosure_state(CALL)
    policy.record_callee_country(country, CALL)
    if disclosure:
        policy.record_disclosure_state(disclosure, CALL)
    fixture.pool.fail_at = {1}
    assert await save(fixture) == (str(RECORDING) if allowed else None)
    assert len(fixture.clock.sleeps) == 1
    assert len(fixture.uploads) == len(fixture.conn.inserts) == len(fixture.conn.links) == int(allowed)
