"""Second-review fixes of the recording work (26 Nov 2026), P2-1 to P2-3.

P2-1: the transfer join reads the pending agent part BEFORE it touches the phone
system, so a read error is a plain "retry" that costs no stop and no fetch.
P2-2: when the teardown could not write the pending copy, an unavailable
"already saved" check must not stop the save (the memory copy is the only one).
P2-3: a check that keeps failing says so with an ERROR from the 3rd attempt.
"""
import builtins
import io
import logging
import uuid
import wave
from types import SimpleNamespace

import pytest

from app.domain.services import recording_service as rs
from app.domain.services import recording_spool as spool
from app.domain.services.telephony import recording as tr
from app.domain.services.telephony import transfer_recording as xfer
from tests.unit.test_recording_never_shortened_20261126 import (  # noqa: F401 - svc is a fixture
    buf_for,
    mk_wav,
    on_disk_seconds,
    svc,
)

ENTRY_NAME = "call-review-1"
TENANT = "20000000-0000-4000-8000-000000000004"


def _wav(seconds, rate=8000, channels=2):
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x01\x00" * channels * int(seconds * rate))
    return out.getvalue()


def _entry(**kw):
    base = dict(
        key=ENTRY_NAME, pbx_call_id="pbx-1", session_call_id=ENTRY_NAME, tenant_id=TENANT,
        campaign_id=None, phone_hint="x", sample_rate=8000, duration_seconds=2.0,
        disclosure_state=None, callee_country=None, callee_country_known=False,
        inbound=False, strict_notice=False, awaiting="transfer_part",
        transfer_recording_name="talky-xferrec-q", transfer_part=None,
        created_at=spool.utc_now_iso(),
    )
    base.update(kw)
    return spool.SpoolEntry(**base)


class Adapter:
    def __init__(self, part=None):
        self.calls = []
        self.part = part if part is not None else _wav(1.0, channels=1)

    async def transfer_recording_is_live(self, name):
        self.calls.append("is_live")
        return False

    async def stop_transfer_recording(self, name):
        self.calls.append("stop")

    async def fetch_stored_recording(self, name, **kw):
        self.calls.append("fetch")
        return self.part

    async def delete_stored_recording(self, name):
        self.calls.append("delete")

    def forget_transfer_recording(self, pbx_id):
        self.calls.append("forget")


@pytest.fixture
def pending(tmp_path, monkeypatch):
    monkeypatch.setenv("RECORDING_SPOOL_DIR", str(tmp_path / "spool"))
    tr._PROCESSING.clear()
    xfer._FINALIZING.clear()
    return tmp_path


# ------------------------------------------------------------------ P2-1

async def test_a_persistent_read_error_never_touches_the_phone_system(pending, monkeypatch):
    spool.write_entry(_entry(), _wav(2.0))
    real_open = builtins.open

    def deny(file, mode="r", *a, **k):
        if str(file).endswith(ENTRY_NAME + ".wav") and "r" in mode and "w" not in mode:
            raise PermissionError(13, "denied")
        return real_open(file, mode, *a, **k)

    monkeypatch.setattr(builtins, "open", deny)
    adapter = Adapter()
    for _ in range(3):
        current = spool.read_entry(ENTRY_NAME)
        result = await xfer.finalize_transfer_entry(current, adapter=adapter, call_ended=False)
        assert result == "retry"
    monkeypatch.setattr(builtins, "open", real_open)
    assert adapter.calls == [], "the phone system was stopped or asked while the pending part was unreadable"
    assert spool.read_entry(ENTRY_NAME).awaiting == "transfer_part"
    assert spool.has_audio(ENTRY_NAME)


async def test_a_normal_join_is_unchanged(pending, monkeypatch):
    ai = _wav(2.0)
    part = _wav(1.0, channels=1)
    spool.write_entry(_entry(), ai)
    saved = []

    async def process(entry, *, wav_bytes=None, voice_session=None):
        saved.append(wav_bytes)
        return "saved"

    monkeypatch.setattr(tr, "process_spool_entry", process)
    adapter = Adapter(part=part)
    result = await xfer.finalize_transfer_entry(spool.read_entry(ENTRY_NAME), adapter=adapter, call_ended=True)
    assert result == "saved"
    expected, _seconds = xfer.append_transfer_part(ai, part)
    assert saved == [bytes(expected)]
    assert adapter.calls == ["stop", "is_live", "fetch", "delete", "forget"]


# ------------------------------------------------------------------ P2-2

async def test_not_spooled_and_the_check_fails_the_full_capture_is_stored(svc, caplog):
    service, fdb, _ = svc
    call, tenant, campaign = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    fdb.saved_check_error = ConnectionResetError("x")
    with caplog.at_level(logging.WARNING, logger=rs.logger.name):
        out = await service.save_and_link_outcome(
            call, buf_for(mk_wav(5.0)), tenant, campaign, proceed_if_check_unavailable=True,
        )
    assert out.status == rs.SAVE_SAVED
    assert on_disk_seconds(fdb.rows[0]["s3_key"]) == 5.0
    [line] = [r for r in caplog.records if "recording_saved_check_unavailable" in r.getMessage()]
    assert line.levelno == logging.WARNING and "proceeding=no_durable_copy" in line.getMessage()


async def test_spooled_and_the_check_fails_still_retries_and_writes_nothing(svc):
    service, fdb, root = svc
    call, tenant, campaign = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    fdb.saved_check_error = ConnectionResetError("x")
    out = await service.save_and_link_outcome(call, buf_for(mk_wav(5.0)), tenant, campaign)
    assert (out.status, out.reason) == (rs.SAVE_RETRY, "saved_check_unavailable")
    assert fdb.rows == []
    assert not root.exists() or not any(p.is_file() for p in root.rglob("*"))


class _Gw:
    def __init__(self):
        self.caller = [b"\x01\x00" * 800 for _ in range(20)]
        self.agent = [(0, b"\x10\x00" * 800)]
        self._sample_rate = 8000

    def get_recording_buffer(self, _c):
        return self.caller

    def get_tts_recording_buffer(self, _c):
        return self.agent

    def clear_recording_buffer(self, _c):
        pass


def _vs():
    return SimpleNamespace(
        call_id=ENTRY_NAME, media_gateway=_Gw(), _dialer_tenant_id=TENANT,
        config=SimpleNamespace(tenant_id=TENANT, campaign_id=None, lead_id=None),
        call_session=None, _recording_policy_decided=True,
    )


async def test_only_the_unspooled_teardown_save_passes_the_flag(pending, monkeypatch):
    monkeypatch.setattr("app.core.container.get_container", lambda: SimpleNamespace(is_initialized=False))
    seen = []

    async def spy(entry, **kw):
        seen.append(kw)
        return "saved"

    monkeypatch.setattr(tr, "process_spool_entry", spy)
    await tr._save_call_recording_with_budget(_vs(), "pbx-1")           # spooled
    assert seen == [{"wav_bytes": seen[0]["wav_bytes"], "voice_session": seen[0]["voice_session"]}]
    assert "proceed_if_check_unavailable" not in seen[0]
    seen.clear()

    def broken(entry, wav):
        raise OSError("disk full")

    import app.domain.services.recording_spool as rsp
    monkeypatch.setattr(rsp, "write_entry", broken)
    await tr._save_call_recording_with_budget(_vs(), "pbx-1")           # not spooled
    assert seen[0].get("proceed_if_check_unavailable") is True


async def test_the_pending_loop_and_the_transfer_join_never_pass_the_flag(pending, monkeypatch):
    spool.write_entry(_entry(awaiting=None, transfer_recording_name=None), _wav(2.0))
    seen = []

    async def spy(entry, **kw):
        seen.append(kw)
        return "saved"

    monkeypatch.setattr(tr, "process_spool_entry", spy)
    monkeypatch.setattr(tr, "_age_seconds", lambda iso, now: tr._ORPHAN_AGE_S + 1)
    assert await tr._spool_pass() == 1
    assert seen == [{}]
    seen.clear()
    spool.write_entry(_entry(), _wav(2.0))
    await xfer.finalize_transfer_entry(spool.read_entry(ENTRY_NAME), adapter=Adapter(), call_ended=True)
    assert seen and all("proceed_if_check_unavailable" not in kw for kw in seen)


async def test_process_spool_entry_passes_the_flag_down_only_when_given(pending, monkeypatch):
    got = []

    async def locked(entry, wav, vs, **kw):
        got.append(kw)
        return "saved"

    monkeypatch.setattr(tr, "_process_locked", locked)
    e = _entry(awaiting=None)
    await tr.process_spool_entry(e)
    tr._PROCESSING.clear()
    await tr.process_spool_entry(e, proceed_if_check_unavailable=True)
    assert got == [{}, {"proceed_if_check_unavailable": True}]


# ------------------------------------------------------------------ P2-3

async def test_a_check_that_keeps_failing_errors_from_the_third_attempt(pending, caplog):
    entry = _entry(awaiting=None)
    spool.write_entry(entry, _wav(1.0))
    caplog.set_level(logging.INFO)
    results = []
    for _ in range(4):
        await tr._mark_retry(entry, "saved_check_unavailable")
        results.append([r for r in caplog.records if r.levelno >= logging.ERROR
                        and "recording_saved_check_still_unavailable" in r.getMessage()])
    assert [len(r) for r in results] == [0, 0, 1, 2]
    msg = results[2][0].getMessage()
    assert msg == "recording_saved_check_still_unavailable call=pbx-1 attempts=3"


async def test_other_retry_reasons_never_raise_that_error(pending, caplog):
    entry = _entry(awaiting=None)
    spool.write_entry(entry, _wav(1.0))
    for _ in range(5):
        await tr._mark_retry(entry, "call_lookup_failed")
    assert [r for r in caplog.records if r.levelno >= logging.ERROR] == []
