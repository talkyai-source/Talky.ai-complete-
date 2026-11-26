"""A passing read error on the pending wav must never delete the only copy.

``read_wav`` used to return None on ANY OSError and its callers read None as
"the audio is missing": the pending loop then deleted the json and the wav.
Now only a missing file is None; every other error is raised, and the callers
keep the copy and try again later. Real temp dirs, real spool, real callers;
only the one ``open`` of the wav is made to fail, once.
"""
import builtins
import errno
import logging
import os
import wave
import io
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.domain.services import recording_spool as spool
from app.domain.services.telephony import recording as tr
from app.domain.services.telephony import transfer_recording as xfer

ENTRY_NAME = "call-read-err-1"
TENANT = "20000000-0000-4000-8000-000000000004"


def _wav(seconds, rate=8000):
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x01\x00\x02\x00" * int(seconds * rate))
    return out.getvalue()


def _entry(**kw):
    base = dict(
        key=ENTRY_NAME, pbx_call_id="pbx-1", session_call_id=ENTRY_NAME, tenant_id=TENANT,
        campaign_id=None, phone_hint="x", sample_rate=8000, duration_seconds=2.0,
        disclosure_state=None, callee_country=None, callee_country_known=False,
        inbound=False, strict_notice=False, awaiting=None, transfer_recording_name=None,
        transfer_part=None, created_at=spool.utc_now_iso(),
    )
    base.update(kw)
    return spool.SpoolEntry(**base)


@pytest.fixture
def pending(tmp_path, monkeypatch):
    monkeypatch.setenv("RECORDING_SPOOL_DIR", str(tmp_path / "spool"))
    tr._PROCESSING.clear()
    xfer._FINALIZING.clear()
    return tmp_path


def _fail_open_once(monkeypatch, exc):
    """Make the next open() of the pending wav raise ``exc``; everything else is real."""
    real_open = builtins.open
    state = {"hits": 0}

    def fake_open(file, mode="r", *a, **k):
        if str(file).endswith(ENTRY_NAME + ".wav") and "r" in mode and state["hits"] == 0:
            state["hits"] += 1
            raise exc
        return real_open(file, mode, *a, **k)

    monkeypatch.setattr(builtins, "open", fake_open)
    return state


def _on_disk():
    base = spool.spool_dir()
    return os.path.isfile(os.path.join(base, ENTRY_NAME + ".wav")), os.path.isfile(os.path.join(base, ENTRY_NAME + ".json"))


# ------------------------------------------------------------ read_wav itself

def test_read_wav_missing_file_is_none(pending):
    assert spool.read_wav(ENTRY_NAME) is None


@pytest.mark.parametrize("exc", [
    PermissionError(errno.EACCES, "denied"),
    OSError(errno.EIO, "io error"),
    OSError(errno.EMFILE, "too many open files"),
])
def test_read_wav_other_errors_are_raised(pending, monkeypatch, exc):
    spool.write_entry(_entry(), _wav(1))
    _fail_open_once(monkeypatch, exc)
    with pytest.raises(OSError):
        spool.read_wav(ENTRY_NAME)


# ------------------------------------------------------ (a) real pending processing

async def test_a_permission_error_keeps_both_files_through_process_spool_entry(pending, monkeypatch, caplog):
    spool.write_entry(_entry(), _wav(1))
    _fail_open_once(monkeypatch, PermissionError(errno.EACCES, "denied"))
    with caplog.at_level(logging.INFO):
        result = await tr.process_spool_entry(spool.read_entry(ENTRY_NAME))
    assert result == "retry"
    assert _on_disk() == (True, True)
    assert "audio_missing" not in caplog.text
    assert spool.read_entry(ENTRY_NAME).last_error == "PermissionError"


async def test_a_permission_error_keeps_both_files_through_the_pending_pass(pending, monkeypatch, caplog):
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    spool.write_entry(_entry(created_at=old), _wav(1))
    _fail_open_once(monkeypatch, PermissionError(errno.EACCES, "denied"))
    with caplog.at_level(logging.INFO):
        assert await tr._spool_pass() == 1
    assert _on_disk() == (True, True)
    assert "audio_missing" not in caplog.text


# ------------------------------------------------------------ (b) really missing

async def test_b_a_wav_that_does_not_exist_is_still_refused_as_before(pending, caplog):
    entry = _entry()
    spool.write_entry(entry, _wav(1))
    os.unlink(os.path.join(spool.spool_dir(), ENTRY_NAME + ".wav"))
    with caplog.at_level(logging.INFO):
        result = await tr.process_spool_entry(entry)
    assert result == "refused"
    assert "audio_missing" in caplog.text
    assert _on_disk() == (False, False)


# ------------------------------------------------- (c) the transfer join path

class _Adapter:
    def __init__(self, part):
        self.part = part
        self.order = []
        self.forgotten = []

    async def stop_transfer_recording(self, name):
        self.order.append("stop")

    async def fetch_stored_recording(self, name, **kw):
        self.order.append("fetch")
        return self.part

    async def delete_stored_recording(self, name):
        self.order.append("delete")

    def forget_transfer_recording(self, pbx_id):
        self.forgotten.append(pbx_id)


async def test_c_transfer_join_with_unreadable_agent_part_saves_nothing_and_keeps_the_spooled_part(
    pending, monkeypatch
):
    ai = _wav(2.0)
    entry = _entry(awaiting="transfer_part", transfer_recording_name="talky-xferrec-p")
    spool.write_entry(entry, ai)
    wav_path = os.path.join(spool.spool_dir(), ENTRY_NAME + ".wav")
    with open(wav_path, "rb") as fh:
        before = fh.read()

    processed = []

    async def _no_process(*a, **k):
        processed.append(1)
        return "saved"

    monkeypatch.setattr(tr, "process_spool_entry", _no_process)
    _fail_open_once(monkeypatch, PermissionError(errno.EACCES, "denied"))
    adapter = _Adapter(part=_wav(1.0, rate=8000))

    result = await xfer.finalize_transfer_entry(entry, adapter=adapter, call_ended=True)

    assert result == "retry"
    assert processed == []  # no joined recording is saved without the agent's part
    assert "delete" not in adapter.order  # the phone system's copy of the agent part stays
    with open(wav_path, "rb") as fh:
        assert fh.read() == before  # the spooled part is unchanged
    assert entry.awaiting == "transfer_part"
