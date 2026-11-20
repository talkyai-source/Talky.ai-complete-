"""Failure paths found by the independent reviews of the recording work.

Each test is one sequence in which a recording could have been lost, saved
twice, kept for ever, or cut short. The pending folder and the live copies
are the real modules on a temporary folder; only the save is replaced.
"""
import asyncio
import io
import json
import logging
import wave
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from aiohttp import web
import aiohttp

from app.domain.services import recording_checkpoint as cp
from app.domain.services import recording_spool as spool
from app.domain.services.telephony import recording as rec
from app.domain.services.telephony import transfer_recording as tr
from app.infrastructure.telephony.asterisk_adapter import AsteriskAdapter

TENANT = "20000000-0000-4000-8000-00000000c0de"


@pytest.fixture(autouse=True)
def folder(tmp_path, monkeypatch):
    monkeypatch.setenv("RECORDING_SPOOL_DIR", str(tmp_path / "pending"))
    finalizer = rec._awaiting_finalizer
    rec._PROCESSING.clear()
    cp._STATE.clear()
    cp._DONE.clear()
    tr._FINALIZING.clear()
    yield tmp_path / "pending"
    rec.set_awaiting_finalizer(finalizer)
    rec._PROCESSING.clear()
    cp._STATE.clear()
    cp._DONE.clear()
    tr._FINALIZING.clear()


def _wav(seconds=1.0, rate=16000, channels=2):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(b"\x01\x02" * int(rate * seconds) * channels)
    return buf.getvalue()


def _entry(key="k-review", *, age_s=0.0, **changes):
    created = datetime.now(timezone.utc) - timedelta(seconds=age_s)
    values = dict(
        key=key, pbx_call_id="pbx-" + key, session_call_id=key, tenant_id=TENANT, campaign_id=None,
        phone_hint=None, sample_rate=16000, duration_seconds=1.0, disclosure_state=None,
        callee_country=None, callee_country_known=False, inbound=False, strict_notice=False,
        awaiting=None, transfer_recording_name=None, transfer_part=None, created_at=created.isoformat(),
    )
    values.update(changes)
    return spool.SpoolEntry(**values)


# ── the pending folder ───────────────────────────────────────────────────────

def test_an_entry_is_its_audio_a_sidecar_alone_is_never_created(folder):
    entry = _entry()
    spool.update_entry(entry)  # nothing was written for this key
    assert not (folder / "k-review.json").exists()
    assert spool.has_audio("k-review") is False
    assert spool.list_entries() == []

    spool.write_entry(entry, _wav())
    entry.attempts = 3
    spool.update_entry(entry)
    assert spool.has_audio("k-review") is True
    assert spool.read_entry("k-review").attempts == 3

    spool.remove_entry("k-review")
    spool.update_entry(entry)  # a late writer must not bring a saved entry back
    assert spool.list_entries() == [] and not (folder / "k-review.json").exists()
    assert spool.has_audio("../escape") is False


# ── the live copy ────────────────────────────────────────────────────────────

def _live_folder(key, *, caller_seconds=2.0, rate=16000):
    live = cp._folder(key)
    import os

    os.makedirs(live, exist_ok=True)
    with open(os.path.join(live, "caller.pcm"), "wb") as fh:
        fh.write(b"\x01\x02" * int(rate * caller_seconds))
    with open(os.path.join(live, "agent.bin"), "wb") as fh:
        fh.write(cp._AGENT_HDR.pack(0, 4) + b"\x05\x06\x07\x08")
    with open(os.path.join(live, "meta.json"), "wb") as fh:
        fh.write(json.dumps(asdict(_entry(key, sample_rate=rate))).encode())
    return live


def test_a_sidecar_without_audio_does_not_make_recovery_throw_the_live_copy_away(folder):
    """Disk full at hangup: the WAV was not written, a sidecar was. The live
    copy is then the only audio left and must become the pending recording."""
    import os

    live = _live_folder("k-full-disk")
    os.makedirs(folder, exist_ok=True)
    (folder / "k-full-disk.json").write_bytes(json.dumps(asdict(_entry("k-full-disk"))).encode())

    status, seconds = cp._recover_sync("k-full-disk")

    assert status == "recovered" and abs(seconds - 2.0) < 0.01
    assert spool.has_audio("k-full-disk") and spool.read_entry("k-full-disk") is not None
    assert not os.path.exists(live)


def test_a_complete_pending_entry_still_makes_recovery_drop_the_live_copy(folder):
    import os

    live = _live_folder("k-safe")
    spool.write_entry(_entry("k-safe"), _wav())
    assert cp._recover_sync("k-safe")[0] == "already_pending"
    assert not os.path.exists(live)


def test_a_write_that_fails_after_the_call_finished_leaves_no_folder_behind(folder, monkeypatch):
    """Otherwise the next start would turn the leftover into a second recording."""
    import os

    key = "k-late-write"

    def failing_inner(live, reset, caller_new, agent_new, meta_bytes):
        os.makedirs(live, exist_ok=True)
        cp._DONE[key] = None  # the call ended while this write was in flight
        raise OSError("no space left on device")

    monkeypatch.setattr(cp, "_write_files_inner", failing_inner)
    with pytest.raises(OSError):
        cp._write_files(key, key, False, [b"\x00\x00"], [], b"{}")
    assert not os.path.exists(cp._folder(key))


# ── the retry loop ───────────────────────────────────────────────────────────

@pytest.fixture
def saves(monkeypatch):
    seen = []

    async def process(entry, *, wav_bytes=None, voice_session=None):
        seen.append(entry.key)
        return "saved"

    monkeypatch.setattr(rec, "process_spool_entry", process)
    rec.set_awaiting_finalizer(None)
    return seen


@pytest.mark.asyncio
async def test_the_loop_acts_on_what_is_on_disk_now_not_on_its_listing(folder, saves, monkeypatch):
    """Between listing and acting, the end of the call saved and removed it."""
    spool.write_entry(_entry("k-gone", age_s=600), _wav())
    listed = spool.list_entries()
    spool.remove_entry("k-gone")
    monkeypatch.setattr(spool, "list_entries", lambda: listed)

    assert await rec._spool_pass() == 0
    assert saves == []
    assert not (folder / "k-gone.json").exists(), "and it is not brought back"


@pytest.mark.asyncio
async def test_a_pending_recording_does_not_stay_for_ever(folder, saves, caplog, monkeypatch):
    monkeypatch.delenv("RECORDING_PENDING_MAX_AGE_DAYS", raising=False)
    spool.write_entry(_entry("k-old", age_s=31 * 86400, attempts=40, last_error="tenant_unavailable"), _wav())
    spool.write_entry(_entry("k-recent", age_s=29 * 86400, attempts=1), _wav())

    with caplog.at_level(logging.ERROR):
        await rec._spool_pass()

    assert not spool.has_audio("k-old") and spool.read_entry("k-old") is None
    assert "recording_spool_expired call=pbx-k-old" in caplog.text
    assert "last_error=tenant_unavailable" in caplog.text
    assert spool.has_audio("k-recent"), "a month has not passed: it is still retried"


# ── the part recorded after a transfer ───────────────────────────────────────

class _Phone:
    def __init__(self, *, live=None, part=None):
        self.live, self.part, self.calls = live, part, []

    async def transfer_recording_is_live(self, name):
        self.calls.append("is_live")
        return self.live

    async def stop_transfer_recording(self, name):
        self.calls.append("stop")
        self.live = False  # the phone system closes the file once told to stop

    async def fetch_stored_recording(self, name, **kw):
        self.calls.append("fetch")
        return self.part

    async def delete_stored_recording(self, name):
        self.calls.append("delete")

    def forget_transfer_recording(self, call_id):
        self.calls.append("forget")


def _awaiting(key="k-xfer", **changes):
    return _entry(key, awaiting="transfer_part", transfer_recording_name="talky-xferrec-" + key, **changes)


@pytest.mark.asyncio
async def test_after_a_restart_a_recording_that_is_still_running_is_left_alone(folder, saves):
    """The service came back while the person is still talking. Stopping the
    recording now would cut it and label a partial file as complete."""
    entry = _awaiting()
    spool.write_entry(entry, _wav())
    phone = _Phone(live=True, part=_wav(1.0, 8000, 1))

    assert await tr.finalize_transfer_entry(entry, adapter=phone) == "retry"

    assert phone.calls == ["is_live"], "not stopped, not fetched"
    assert spool.read_entry("k-xfer").awaiting == "transfer_part"
    assert saves == []


@pytest.mark.asyncio
async def test_at_the_end_of_the_call_the_recording_is_stopped_and_joined(folder, saves):
    entry = _awaiting()
    spool.write_entry(entry, _wav(2.0))
    phone = _Phone(live=True, part=_wav(1.0, 8000, 1))

    assert await tr.finalize_transfer_entry(entry, adapter=phone, call_ended=True) == "saved"

    assert phone.calls == ["stop", "is_live", "fetch", "delete", "forget"]  # fetch only once it is closed
    assert saves == ["k-xfer"]
    stored = spool.read_entry("k-xfer")
    assert stored.transfer_part == "included" and stored.awaiting is None and stored.sample_rate == 8000
    with wave.open(io.BytesIO(bytes(spool.read_wav("k-xfer"))), "rb") as wf:
        assert (wf.getnchannels(), wf.getframerate(), wf.getsampwidth()) == (2, 8000, 2)
        assert abs(wf.getnframes() / 8000 - 3.0) < 0.05


@pytest.mark.asyncio
async def test_a_join_that_fails_is_not_tried_again_for_ever(folder, saves, monkeypatch, caplog):
    """Every new try would download the whole file again."""
    def out_of_memory(ai_wav, part_wav):
        raise MemoryError()

    monkeypatch.setattr(tr, "append_transfer_part", out_of_memory)
    entry = _awaiting()
    spool.write_entry(entry, _wav())
    phone = _Phone(part=_wav(1.0, 8000, 1))

    with caplog.at_level(logging.ERROR):
        assert await tr.finalize_transfer_entry(entry, adapter=phone, call_ended=True) == "saved"

    assert "transfer_recording_join_failed call=pbx-k-xfer err=MemoryError" in caplog.text
    stored = spool.read_entry("k-xfer")
    assert stored.transfer_part == "missing" and stored.awaiting is None
    assert "delete" not in phone.calls, "the phone system keeps its copy for a person to recover"
    assert saves == ["k-xfer"], "the agent's part is saved"


def test_the_joined_file_is_one_buffer_and_a_valid_wav():
    joined, seconds = tr.append_transfer_part(_wav(2.0, 16000, 2), _wav(3.0, 8000, 1))
    assert isinstance(joined, (bytes, bytearray)) and abs(seconds - 5.0) < 0.02
    assert len(joined) == 44 + int(round(seconds * 8000)) * 4
    with wave.open(io.BytesIO(bytes(joined)), "rb") as wf:
        assert (wf.getnchannels(), wf.getframerate(), wf.getsampwidth()) == (2, 8000, 2)
        frames = wf.readframes(wf.getnframes())
    tail = frames[-8:]
    assert tail[0:2] == tail[2:4], "after the handover both channels carry the same conversation"


def test_the_longest_transferred_call_is_two_hours(monkeypatch):
    monkeypatch.setenv("TELEPHONY_TRANSFER_MAX_CALL_DURATION_S", "999999")
    assert tr.transfer_max_duration_seconds() == 7200


# ── the raw reader ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_reply_without_a_declared_length_is_bounded_while_it_is_read():
    async def endless(request):
        response = web.StreamResponse()
        await response.prepare(request)
        for _ in range(64):
            await response.write(b"\x00" * 4096)
        await response.write_eof()
        return response

    app = web.Application()
    app.router.add_get("/ari/recordings/stored/x/file", endless)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    adapter = AsteriskAdapter()
    adapter._ari_host, adapter._ari_port = "127.0.0.1", site._server.sockets[0].getsockname()[1]
    adapter._session = aiohttp.ClientSession()
    try:
        whole = await adapter._ari_bytes("GET", "/recordings/stored/x/file")
        assert len(whole) == 64 * 4096
        with pytest.raises(ValueError):
            await adapter._ari_bytes("GET", "/recordings/stored/x/file", max_bytes=10_000)
    finally:
        await adapter._session.close()
        await runner.cleanup()
