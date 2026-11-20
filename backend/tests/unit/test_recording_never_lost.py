"""A mixed call recording is on disk before anything can fail, and stays until
it is safely saved or definitely refused (owner decision 2026-11-18)."""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.domain.services import recording_policy_service as policy
from app.domain.services import recording_service as rs
from app.domain.services import recording_spool as spool
from app.domain.services.telephony import recording as tr

TENANT = "20000000-0000-4000-8000-000000000004"
SESSION = "a1b2c3d4-e5f6-47a8-8b9c-d0e1f2a3b4c5"
INTERNAL = "b1b2c3d4-e5f6-47a8-8b9c-d0e1f2a3b4c5"
PBX = "pbx-chan-1"


class Conn:
    def __init__(self, owner):
        self.owner = owner

    async def fetchrow(self, sql, *args):
        if sql.startswith("SELECT id, tenant_id, campaign_id FROM calls"):
            if self.owner.lookup_error:
                raise self.owner.lookup_error
            if self.owner.row:
                return {"id": UUID(INTERNAL), "tenant_id": UUID(TENANT), "campaign_id": None}
            return None
        return {"id": args[0]}

    async def execute(self, sql, *args):
        if self.owner.stub_error:
            raise self.owner.stub_error
        self.owner.stubs.append(args)


class Harness:
    def __init__(self):
        self.row = True
        self.lookup_error = None
        self.stub_error = None
        self.stubs = []
        self.saves = []
        self.outcomes = [rs.SaveOutcome(rs.SAVE_SAVED, "rec-1")]
        self.save_error = None
        self.ledger_at_save = None
        self.save_delay = 0.0
        self.clears = []
        self.initialized = True

    def service(self, _pool):
        harness = self

        class Svc:
            async def save_and_link_outcome(self, **kwargs):
                harness.saves.append(kwargs)
                harness.ledger_at_save = (
                    policy.get_disclosure_state(kwargs["call_id"]),
                    policy.get_callee_country(kwargs["call_id"]),
                )
                if harness.save_delay:
                    await asyncio.sleep(harness.save_delay)
                if harness.save_error:
                    raise harness.save_error
                return harness.outcomes.pop(0) if harness.outcomes else rs.SaveOutcome(rs.SAVE_RETRY, None, "x")

        return Svc()

    def voice(self, tenant=TENANT, **extra):
        gateway = SimpleNamespace(
            get_recording_buffer=lambda _: [b"\x01\x00" * 16000],
            get_tts_recording_buffer=lambda _: [],
            clear_recording_buffer=lambda cid: self.clears.append(cid),
            _sample_rate=16000,
        )
        return SimpleNamespace(
            call_id=SESSION, media_gateway=gateway, _dialer_tenant_id=tenant,
            config=SimpleNamespace(tenant_id=tenant, campaign_id=None, lead_id=None),
            call_session=None, **extra,
        )


@pytest.fixture
def h(tmp_path, monkeypatch):
    harness = Harness()
    monkeypatch.setenv("RECORDING_SPOOL_DIR", str(tmp_path / "spool"))
    pool = object()
    monkeypatch.setattr("app.core.container.get_container", lambda: SimpleNamespace(
        is_initialized=harness.initialized, db_pool=pool, db_client=SimpleNamespace(pool=pool),
    ))

    @asynccontextmanager
    async def acquire(*args, **kwargs):
        yield Conn(harness)

    monkeypatch.setattr(rs, "_acquire_recording_connection", acquire)
    monkeypatch.setattr(rs, "RecordingService", harness.service)
    tr._PROCESSING.clear()
    # The call lifecycle registers its own finalizer at import; put back
    # whatever was there so other test files still see it.
    finalizer_before = tr._awaiting_finalizer
    tr.set_awaiting_finalizer(None)
    policy.clear_disclosure_state(SESSION, INTERNAL, PBX)
    yield harness
    tr.set_awaiting_finalizer(finalizer_before)
    policy.clear_disclosure_state(SESSION, INTERNAL, PBX)


def _entry(**kw):
    base = dict(
        key=SESSION, pbx_call_id=PBX, session_call_id=SESSION, tenant_id=TENANT,
        campaign_id=None, phone_hint="x", sample_rate=16000, duration_seconds=1.0,
        disclosure_state=None, callee_country=None, callee_country_known=False,
        inbound=False, strict_notice=False, awaiting=None, transfer_recording_name=None,
        transfer_part=None, created_at=spool.utc_now_iso(),
    )
    base.update(kw)
    return spool.SpoolEntry(**base)


def _age(entry, seconds):
    entry.created_at = (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()
    return entry


def _write(entry):
    spool.write_entry(entry, b"RIFFwav")
    return entry


# ----------------------------------------------------------------- capture

async def test_entry_written_before_any_database_access(h):
    h.lookup_error = RuntimeError("db down with a secret")
    await tr._save_call_recording(h.voice(), PBX)
    [entry] = spool.list_entries()
    assert entry.attempts == 1
    assert entry.last_error == "call_lookup_failed"
    assert "secret" not in (entry.last_error or "")
    assert h.saves == []
    assert spool.read_wav(entry.key)
    assert h.clears == [SESSION]  # audio is on disk, memory freed


async def test_saved_outcome_removes_entry(h):
    await tr._save_call_recording(h.voice(), PBX)
    assert spool.list_entries() == []
    assert len(h.saves) == 1
    assert h.saves[0]["strict_notice"] is False and h.saves[0]["transfer_part"] is None


async def test_refused_outcome_removes_entry(h):
    h.outcomes = [rs.SaveOutcome(rs.SAVE_REFUSED, None, "no_notice")]
    assert await tr.process_spool_entry(_write(_entry())) == "refused"
    assert spool.list_entries() == []


async def test_retry_outcome_keeps_entry_and_counts(h):
    h.outcomes = [rs.SaveOutcome(rs.SAVE_RETRY, None, "storage_down")]
    assert await tr.process_spool_entry(_write(_entry())) == "retry"
    [entry] = spool.list_entries()
    assert (entry.attempts, entry.last_error) == (1, "storage_down")
    assert entry.last_attempt_at


async def test_exception_in_save_keeps_entry_and_does_not_raise(h):
    h.save_error = OSError("path with data")
    assert await tr.process_spool_entry(_write(_entry())) == "retry"
    [entry] = spool.list_entries()
    assert entry.attempts == 1 and entry.last_error == "OSError"


async def test_restart_puts_ledger_back_before_save(h):
    entry = _write(_entry(
        disclosure_state="transmitted", callee_country="PK", callee_country_known=True,
        strict_notice=True, transfer_part="included",
    ))
    policy.clear_disclosure_state(SESSION, INTERNAL, PBX)
    assert await tr.process_spool_entry(entry) == "saved"
    assert h.ledger_at_save == ("transmitted", "PK")
    assert h.saves[0]["call_id"] == INTERNAL
    assert h.saves[0]["strict_notice"] is True and h.saves[0]["transfer_part"] == "included"


async def test_ledger_entry_already_present_is_not_overwritten(h):
    policy.record_disclosure_state("failed", SESSION)
    entry = _write(_entry(disclosure_state="transmitted"))
    await tr.process_spool_entry(entry)
    assert policy.get_disclosure_state(SESSION) == "failed"


async def test_capture_records_ledger_state_in_entry(h):
    policy.record_disclosure_state("spoken", SESSION)
    policy.record_callee_country("GB", SESSION)
    h.outcomes = [rs.SaveOutcome(rs.SAVE_RETRY, None, "later")]
    await tr._save_call_recording(h.voice(), PBX)
    [entry] = spool.list_entries()
    assert entry.disclosure_state == "spoken"
    assert (entry.callee_country, entry.callee_country_known) == ("GB", True)
    assert entry.tenant_id == TENANT and entry.session_call_id == SESSION


@pytest.mark.parametrize("tenant", [None, "default"])
async def test_tenant_missing_is_kept_not_discarded(h, tenant):
    await tr._save_call_recording(h.voice(tenant=tenant), PBX)
    [entry] = spool.list_entries()
    assert entry.last_error == "tenant_unavailable" and entry.attempts == 1
    assert h.saves == []


async def test_container_unavailable_keeps_entry(h):
    h.initialized = False
    await tr._save_call_recording(h.voice(), PBX)
    [entry] = spool.list_entries()
    assert entry.last_error == "container_unavailable"


async def test_stub_insert_failure_keeps_entry(h):
    h.row = False
    h.stub_error = RuntimeError("boom")
    await tr._save_call_recording(h.voice(), PBX)
    [entry] = spool.list_entries()
    assert entry.last_error == "stub_insert_failed"
    assert h.saves == []


async def test_stub_row_path_uses_entry_fields(h):
    h.row = False
    await tr._save_call_recording(h.voice(), PBX)
    assert len(h.stubs) == 1 and h.stubs[0][0] == UUID(SESSION)
    assert h.saves[0]["call_id"] == SESSION


async def test_spool_write_failure_still_saves_from_memory(h, monkeypatch, caplog):
    def broken(entry, wav):
        raise OSError("disk full")

    monkeypatch.setattr("app.domain.services.recording_spool.write_entry", broken)
    await tr._save_call_recording(h.voice(), PBX)
    assert len(h.saves) == 1
    assert "recording_spool_write_failed" in caplog.text
    assert h.clears == [SESSION]  # today's timing: freed after the save


async def test_awaiting_transfer_writes_entry_and_does_not_save(h):
    await tr._save_call_recording(
        h.voice(), PBX, awaiting_transfer=True, transfer_recording_name="xfer-1",
    )
    [entry] = spool.list_entries()
    assert entry.awaiting == "transfer_part" and entry.transfer_recording_name == "xfer-1"
    assert h.saves == []
    assert h.clears == [SESSION]


async def test_inbound_entry_with_closed_live_gate_is_refused_and_removed(h, monkeypatch):
    async def closed(pool):
        return False

    monkeypatch.setattr(
        "app.domain.services.telephony.modes.caller_first._live_inbound_recording_state", closed,
    )
    assert await tr.process_spool_entry(_write(_entry(inbound=True, strict_notice=True))) == "refused"
    assert spool.list_entries() == []
    assert h.saves == []


async def test_missing_audio_is_refused(h):
    entry = _entry()
    spool.write_entry(entry, b"x")
    import os
    os.unlink(os.path.join(spool.spool_dir(), SESSION + ".wav"))
    assert await tr.process_spool_entry(entry) == "refused"
    assert spool.read_entry(SESSION) is None


async def test_concurrent_processing_of_one_key_saves_once(h):
    h.save_delay = 0.05
    entry = _write(_entry())
    first, second = await asyncio.gather(
        tr.process_spool_entry(entry), tr.process_spool_entry(entry),
    )
    assert sorted([first, second]) == ["retry", "saved"]
    assert len(h.saves) == 1
    assert spool.list_entries() == []


# -------------------------------------------------------------------- loop

async def test_loop_skips_young_fresh_entry_and_processes_old_orphan(h):
    _write(_entry(key="young"))
    _write(_age(_entry(key="orphan"), 300))
    assert await tr._spool_pass() == 1
    assert [e.key for e in spool.list_entries()] == ["young"]


async def test_loop_honours_backoff(h):
    h.outcomes = [rs.SaveOutcome(rs.SAVE_RETRY, None, "x")] * 3
    recent = _entry(key="recent", attempts=2)
    recent.last_attempt_at = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
    due = _entry(key="due", attempts=2)
    due.last_attempt_at = (datetime.now(timezone.utc) - timedelta(seconds=300)).isoformat()
    _write(_age(recent, 1000))
    _write(_age(due, 1000))
    assert await tr._spool_pass() == 1
    assert len(h.saves) == 1
    assert spool.read_entry("recent").attempts == 2
    assert spool.read_entry("due").attempts == 3


async def test_awaiting_entry_skipped_then_aged_into_missing_part(h, monkeypatch):
    young = _write(_age(_entry(awaiting="transfer_part"), 600))
    assert await tr._spool_pass() == 0
    assert h.saves == []
    old = _age(spool.read_entry(SESSION), 5000)
    spool.update_entry(old)
    assert await tr._spool_pass() == 1
    assert h.saves[0]["transfer_part"] == "missing"
    assert spool.list_entries() == []
    assert young.key == SESSION


async def test_registered_finalizer_is_called_instead_of_age_rule(h):
    seen = []

    async def finalizer(entry):
        seen.append(entry.key)

    tr.set_awaiting_finalizer(finalizer)
    _write(_age(_entry(key="old", awaiting="transfer_part"), 5000))
    _write(_age(_entry(key="new", awaiting="transfer_part"), 30))
    assert await tr._spool_pass() == 0
    assert seen == ["old"]
    assert h.saves == []
    assert spool.read_entry("old").awaiting == "transfer_part"


async def test_pass_processes_at_most_four_oldest_first(h):
    h.outcomes = [rs.SaveOutcome(rs.SAVE_SAVED, "r")] * 6
    for i in range(6):
        _write(_age(_entry(key=f"k{i}"), 1000 - i * 10))
    assert await tr._spool_pass() == 4
    assert sorted(e.key for e in spool.list_entries()) == ["k4", "k5"]


async def test_loop_survives_an_exception_and_stops_on_event(h, monkeypatch):
    calls = []

    async def failing_pass():
        calls.append(1)
        raise RuntimeError("pass blew up")

    monkeypatch.setattr(tr, "_spool_pass", failing_pass)
    monkeypatch.setattr(tr, "_LOOP_FIRST_DELAY_S", 0.01)
    monkeypatch.setenv("RECORDING_SPOOL_RETRY_INTERVAL_S", "10")
    monkeypatch.setattr(tr, "_env_float", lambda name, default, minimum=0.0: 0.02)
    stop = asyncio.Event()
    task = asyncio.create_task(tr.recording_spool_loop(stop))
    await asyncio.sleep(0.15)
    assert len(calls) >= 2
    stop.set()
    await asyncio.wait_for(task, 1)
