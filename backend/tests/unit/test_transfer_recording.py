"""Recording of the person-to-caller conversation after a supervised transfer."""

import asyncio
import io
import logging
import time
import wave
from types import SimpleNamespace

import numpy as np
import pytest

from app.domain.services.telephony import transfer_recording as tr
from app.infrastructure.telephony import asterisk_adapter as aa
from app.infrastructure.telephony.asterisk_adapter import AsteriskAdapter, _AriResponseError
from tests.unit.test_asterisk_outbound_supervised_transfer import (
    DEST,
    SWITCH,
    TRUNK,
    _live_outbound_adapter,
    _register_callbacks,
    _Rig,
)
from tests.unit.test_asterisk_supervised_transfer import _live_inbound_adapter


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def make_wav(rate, channels, seconds, values=None):
    n = int(rate * seconds)
    if values is None:
        base = (np.arange(n) % 200).astype(np.int16) + 100
        data = np.repeat(base[:, None], channels, axis=1)
    else:
        data = np.stack([np.full(n, v, dtype=np.int16) for v in values], axis=1)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(data.astype("<i2").tobytes())
    return buf.getvalue()


def read(data):
    with wave.open(io.BytesIO(data), "rb") as wf:
        ch, rate, n = wf.getnchannels(), wf.getframerate(), wf.getnframes()
        arr = np.frombuffer(wf.readframes(n), dtype="<i2").reshape(-1, ch)
    return arr, rate


# --------------------------------------------------------------------------
# name
# --------------------------------------------------------------------------
def test_recording_name_sanitised_and_cut():
    assert aa._transfer_recording_name("abc-DEF_12") == "talky-xferrec-abc-DEF_12"
    assert aa._transfer_recording_name("a b/c.d:e") == "talky-xferrec-a-b-c-d-e"
    assert len(aa._transfer_recording_name("x" * 500)) == 100
    assert aa._transfer_recording_name("x" * 500).startswith("talky-xferrec-")


# --------------------------------------------------------------------------
# adapter: starting the recording
# --------------------------------------------------------------------------
class _RecRig(_Rig):
    def __init__(self, *a, record_error=None, record_hang=False, **kw):
        self.record_error = record_error
        self.record_hang = record_hang
        super().__init__(*a, **kw)

    async def _ari(self, method, path, *, params=None, **kwargs):
        if path.endswith("/record"):
            self.ari.append((method, path, dict(params or {})))
            if self.record_hang:
                await asyncio.sleep(30)
            if self.record_error is not None:
                raise self.record_error
            return {}
        return await super()._ari(method, path, params=params, **kwargs)


async def _connect_outbound(rig, adapter, parent):
    _register_callbacks(adapter)
    task = await rig.start(trunk_endpoint=TRUNK, ring_seconds=12)
    leg = adapter._transfers_by_parent[parent]
    await adapter._handle_ari_event(
        {"type": "ChannelStateChange", "channel": {"id": leg.target_id, "state": "Up"}}
    )
    return leg, await asyncio.wait_for(task, timeout=6)


@pytest.mark.asyncio
async def test_recording_starts_once_after_person_bridged(monkeypatch, caplog):
    monkeypatch.setenv(SWITCH, "true")
    monkeypatch.delenv("TELEPHONY_TRANSFER_MAX_CALL_DURATION_S", raising=False)
    adapter, parent = _live_outbound_adapter()
    rig = _RecRig(adapter, parent)
    with caplog.at_level(logging.INFO):
        leg, result = await _connect_outbound(rig, adapter, parent)
    assert result["status"] == "completed"
    records = rig.calls("/bridges/bridge-out/record")
    assert len(records) == 1
    method, _, q = records[0]
    assert method == "POST"
    assert q == {
        "name": aa._transfer_recording_name(parent),
        "format": "wav",
        "ifExists": "overwrite",
        "beep": "false",
        "maxDurationSeconds": 3600,
    }
    add = next(i for i, c in enumerate(rig.ari) if c[1] == "/bridges/bridge-out/addChannel")
    rec = next(i for i, c in enumerate(rig.ari) if c[1].endswith("/record"))
    assert add < rec
    assert adapter.transfer_recording_name(parent) == aa._transfer_recording_name(parent)
    assert "transfer_recording_started" in caplog.text
    # The name survives the legs being dropped.
    adapter._drop_transfer_indexes(leg)
    assert adapter.transfer_recording_name(parent) == aa._transfer_recording_name(parent)
    adapter.forget_transfer_recording(parent)
    assert adapter.transfer_recording_name(parent) is None


@pytest.mark.asyncio
async def test_recording_uses_env_limit(monkeypatch):
    monkeypatch.setenv(SWITCH, "true")
    monkeypatch.setenv("TELEPHONY_TRANSFER_MAX_CALL_DURATION_S", "1800")
    adapter, parent = _live_outbound_adapter()
    rig = _RecRig(adapter, parent)
    await _connect_outbound(rig, adapter, parent)
    assert rig.calls("/bridges/bridge-out/record")[0][2]["maxDurationSeconds"] == 1800


@pytest.mark.asyncio
async def test_recording_not_started_for_durable_inbound_leg():
    adapter, parent = _live_inbound_adapter()
    ari = []

    async def fake_ari(method, path, *, params=None, **kwargs):
        ari.append((method, path))
        if path == "/channels/create":
            return {"id": params["channelId"]}
        if method == "DELETE" and kwargs.get("return_status"):
            return 404, {}
        return {}

    async def fake_gw(*a, **k):
        return {}

    async def persist(*a):
        return 1

    adapter._ari = fake_ari
    adapter._gateway = fake_gw
    adapter.set_transfer_answered_persist_callback(persist)
    task = asyncio.create_task(
        adapter.transfer(
            parent, "+15559876543", "blind", provider_leg_id="talky-xfer-00000000000000000001"
        )
    )
    for _ in range(20):
        if adapter._transfers_by_parent.get(parent) is not None:
            break
        await asyncio.sleep(0)
    leg = adapter._transfers_by_parent[parent]
    assert leg.durable
    await adapter._handle_ari_event(
        {"type": "ChannelStateChange", "channel": {"id": leg.target_id, "state": "Up"}}
    )
    assert (await asyncio.wait_for(task, timeout=2))["status"] == "completed"
    assert not any(p.endswith("/record") for _, p in ari)
    assert adapter.transfer_recording_name(parent) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error,expect",
    [
        (_AriResponseError("POST", "/bridges/b/record", 500, "secret body"), "status=500"),
        (_AriResponseError("POST", "/bridges/b/record", 409, "secret body"), "status=409"),
    ],
)
async def test_record_http_failure_keeps_handover(monkeypatch, caplog, error, expect):
    monkeypatch.setenv(SWITCH, "true")
    adapter, parent = _live_outbound_adapter()
    rig = _RecRig(adapter, parent, record_error=error)
    with caplog.at_level(logging.ERROR):
        leg, result = await _connect_outbound(rig, adapter, parent)
    assert result["status"] == "completed"
    assert "transfer_recording_start_failed" in caplog.text
    assert "_AriResponseError" in caplog.text and expect in caplog.text
    assert "secret body" not in caplog.text
    assert adapter.transfer_recording_name(parent) is None


@pytest.mark.asyncio
async def test_record_timeout_keeps_handover(monkeypatch, caplog):
    monkeypatch.setenv(SWITCH, "true")
    adapter, parent = _live_outbound_adapter()
    rig = _RecRig(adapter, parent, record_hang=True)
    start = time.monotonic()
    with caplog.at_level(logging.ERROR):
        leg, result = await _connect_outbound(rig, adapter, parent)
    assert time.monotonic() - start < 5
    assert result["status"] == "completed"
    assert "transfer_recording_start_failed" in caplog.text
    assert "TimeoutError" in caplog.text
    # No answer in time is not a refusal: the phone system may have started
    # recording anyway, so the name is kept and the file is asked for at the
    # end of the call.
    assert adapter.transfer_recording_name(parent) == aa._transfer_recording_name(parent)


@pytest.mark.asyncio
async def test_recorder_channel_events_are_ignored(monkeypatch):
    """Asterisk adds a Recorder/... channel to the bridge; its events must not
    be read as the caller or the person leaving."""
    monkeypatch.setenv(SWITCH, "true")
    adapter, parent = _live_outbound_adapter()
    rig = _RecRig(adapter, parent)
    leg, result = await _connect_outbound(rig, adapter, parent)
    assert result["status"] == "completed"
    before_ari = len(rig.ari)
    before_tasks = set(adapter._terminal_cleanup_tasks) if hasattr(adapter, "_terminal_cleanup_tasks") else None
    rec = {"id": "1700000000.99", "name": "Recorder/ARI-talky-xferrec-x"}
    events = [
        {"type": "ChannelEnteredBridge", "channel": rec, "bridge": {"id": "bridge-out"}},
        {"type": "ChannelLeftBridge", "channel": rec, "bridge": {"id": "bridge-out"}},
        {"type": "RecordingStarted", "recording": {"name": "x"}},
        {"type": "RecordingFinished", "recording": {"name": "x"}},
        {"type": "RecordingFailed", "recording": {"name": "x"}},
        {"type": "ChannelHangupRequest", "channel": rec},
        {"type": "StasisEnd", "channel": rec},
        {"type": "ChannelDestroyed", "channel": rec, "cause": 16, "cause_txt": "Normal Clearing"},
    ]
    for ev in events:
        await adapter._handle_ari_event(ev)
    await asyncio.sleep(0)
    assert len(rig.ari) == before_ari
    assert adapter._transfers_by_parent[parent] is leg
    assert not leg.terminal_dispatched
    assert parent in adapter._active_sessions
    if before_tasks is not None:
        assert set(adapter._terminal_cleanup_tasks) == before_tasks


# --------------------------------------------------------------------------
# adapter: stop / fetch / delete
# --------------------------------------------------------------------------
class _FakeAri:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    async def bytes_(self, method, path, **kw):
        self.calls.append((method, path))
        r = self.results.pop(0) if self.results else _AriResponseError(method, path, 404, "")
        if isinstance(r, Exception):
            raise r
        return r


@pytest.mark.asyncio
async def test_fetch_waits_through_404_then_returns_bytes():
    adapter = AsteriskAdapter()
    fake = _FakeAri(
        [
            _AriResponseError("GET", "p", 404, ""),
            _AriResponseError("GET", "p", 403, ""),
            b"RIFFdata",
        ]
    )
    adapter._ari_bytes = fake.bytes_
    assert await adapter.fetch_stored_recording("nm", attempts=5, delay_s=0) == b"RIFFdata"
    assert len(fake.calls) == 3
    assert fake.calls[0] == ("GET", "/recordings/stored/nm/file")


@pytest.mark.asyncio
async def test_fetch_never_appears_gives_none_and_other_errors_stop():
    adapter = AsteriskAdapter()
    fake = _FakeAri([])
    adapter._ari_bytes = fake.bytes_
    assert await adapter.fetch_stored_recording("nm", attempts=3, delay_s=0) is None
    assert len(fake.calls) == 3
    fake = _FakeAri([_AriResponseError("GET", "p", 500, "")])
    adapter._ari_bytes = fake.bytes_
    assert await adapter.fetch_stored_recording("nm", attempts=3, delay_s=0) is None
    assert len(fake.calls) == 1
    fake = _FakeAri([RuntimeError("boom")])
    adapter._ari_bytes = fake.bytes_
    assert await adapter.fetch_stored_recording("nm", attempts=3, delay_s=0) is None


@pytest.mark.asyncio
async def test_stop_and_delete_swallow_404_and_errors():
    adapter = AsteriskAdapter()
    seen = []

    async def ari(method, path, *, ok=(200, 201, 204), **kw):
        seen.append((method, path, ok))
        raise _AriResponseError(method, path, 404, "")

    adapter._ari = ari
    await adapter.stop_transfer_recording("nm")
    await adapter.delete_stored_recording("nm")
    assert seen[0][:2] == ("POST", "/recordings/live/nm/stop")
    assert seen[1][:2] == ("DELETE", "/recordings/stored/nm")
    assert 404 in seen[0][2] and 404 in seen[1][2]

    async def ari_down(*a, **k):
        raise RuntimeError("ari down")

    adapter._ari = ari_down
    await adapter.stop_transfer_recording("nm")
    await adapter.delete_stored_recording("nm")


# --------------------------------------------------------------------------
# append_transfer_part
# --------------------------------------------------------------------------
def test_append_joins_ai_and_part_at_8k_stereo():
    ai = make_wav(16000, 2, 2.0, values=(1000, -2000))
    part = make_wav(8000, 1, 3.0, values=(500,))
    out, dur = tr.append_transfer_part(ai, part)
    arr, rate = read(out)
    assert rate == 8000 and arr.shape[1] == 2
    assert abs(dur - 5.0) < 0.02
    assert abs(arr.shape[0] / 8000 - 5.0) < 0.02
    ai_frames = 16000  # 2 s at 8 kHz
    mid = arr[2000 : ai_frames - 2000]
    assert abs(int(mid[:, 0].mean()) - 1000) < 30
    assert abs(int(mid[:, 1].mean()) + 2000) < 30  # channels stay separate
    tail = arr[ai_frames + 100 :]
    assert np.array_equal(tail[:, 0], tail[:, 1])
    assert abs(int(tail[:, 0].mean()) - 500) < 5


def test_append_part_at_other_rate_and_stereo_part_is_averaged():
    ai = make_wav(8000, 2, 1.0, values=(0, 0))
    part = make_wav(16000, 2, 2.0, values=(1000, 3000))
    out, dur = tr.append_transfer_part(ai, part)
    arr, rate = read(out)
    assert rate == 8000 and abs(dur - 3.0) < 0.02
    tail = arr[8000 + 400 : -400]
    assert abs(int(tail[:, 0].mean()) - 2000) < 20


@pytest.mark.parametrize("ai", [None, b"", b"not a wav"])
def test_append_missing_or_bad_ai_gives_part_only(ai):
    part = make_wav(8000, 1, 1.5)
    out, dur = tr.append_transfer_part(ai, part)
    arr, rate = read(out)
    assert rate == 8000 and abs(dur - 1.5) < 0.02 and arr.shape[1] == 2
    assert np.array_equal(arr[:, 0], arr[:, 1])


def test_append_garbage_part_raises_value_error():
    with pytest.raises(ValueError):
        tr.append_transfer_part(make_wav(8000, 2, 1.0), b"garbage")
    with pytest.raises(ValueError):
        tr.append_transfer_part(None, b"")


def test_append_thirty_minute_pair_is_fast():
    ai = make_wav(16000, 2, 1800.0)
    part = make_wav(8000, 1, 1800.0)
    start = time.monotonic()
    out, dur = tr.append_transfer_part(ai, part)
    assert time.monotonic() - start < 5.0
    assert abs(dur - 3600.0) < 0.02


# --------------------------------------------------------------------------
# finalize_transfer_entry / finalize_awaiting_entry
# --------------------------------------------------------------------------
class _FakeAdapter:
    def __init__(self, part=None, order=None):
        self.part = part
        self.order = order if order is not None else []
        self.forgotten = []

    async def stop_transfer_recording(self, name):
        self.order.append(("stop", name))

    async def fetch_stored_recording(self, name, **kw):
        self.order.append(("fetch", name))
        return self.part

    async def delete_stored_recording(self, name):
        self.order.append(("delete", name))

    def forget_transfer_recording(self, pbx_id):
        self.forgotten.append(pbx_id)


def make_entry(name="talky-xferrec-p"):
    return SimpleNamespace(
        key="k1",
        pbx_call_id="pbx-parent-1",
        session_call_id="sess-1",
        sample_rate=16000,
        duration_seconds=2.0,
        awaiting="transfer_part",
        transfer_recording_name=name,
        transfer_part=None,
        created_at="2026-11-18T00:00:00+00:00",
    )


@pytest.fixture
def spool(monkeypatch):
    from app.domain.services import recording_spool as rs
    from app.domain.services.telephony import recording as rec

    state = SimpleNamespace(
        ai=make_wav(16000, 2, 2.0, values=(10, 20)),
        written=[],
        updated=[],
        processed=[],
        order=[],
        write_error=None,
        result="saved",
    )

    def read_wav(key):
        return state.ai

    def write_entry(entry, wav):
        if state.write_error:
            raise state.write_error
        state.order.append(("write", entry.awaiting, entry.transfer_part))
        state.written.append((entry, wav))

    def update_entry(entry):
        state.updated.append(entry)

    async def process(entry, *, wav_bytes=None, voice_session=None):
        state.order.append(("process",))
        state.processed.append((entry, wav_bytes, voice_session))
        return state.result

    monkeypatch.setattr(rs, "read_wav", read_wav)
    monkeypatch.setattr(rs, "write_entry", write_entry)
    monkeypatch.setattr(rs, "update_entry", update_entry)
    monkeypatch.setattr(rec, "process_spool_entry", process)
    return state


@pytest.mark.asyncio
async def test_finalize_included_orders_write_before_delete(spool, caplog):
    adapter = _FakeAdapter(part=make_wav(8000, 1, 3.0), order=spool.order)
    entry = make_entry()
    vs = object()
    with caplog.at_level(logging.INFO):
        result = await tr.finalize_transfer_entry(entry, adapter=adapter, voice_session=vs)
    assert result == "saved"
    assert entry.transfer_part == "included"
    assert entry.sample_rate == 8000 and abs(entry.duration_seconds - 5.0) < 0.02
    assert entry.awaiting is None
    assert "transfer_recording_included" in caplog.text
    kinds = [o[0] for o in spool.order]
    assert kinds == ["stop", "fetch", "write", "delete", "process"]
    assert adapter.forgotten == ["pbx-parent-1"]
    _, wav, got_vs = spool.processed[0]
    assert got_vs is vs and read(wav)[1] == 8000
    assert spool.written[0][1] == wav


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["no_name", "unreadable"])
async def test_finalize_missing(spool, caplog, case):
    if case == "no_name":
        entry, adapter = make_entry(name=None), _FakeAdapter(part=None, order=spool.order)
    else:
        entry, adapter = make_entry(), _FakeAdapter(part=b"garbage", order=spool.order)
    with caplog.at_level(logging.ERROR):
        result = await tr.finalize_transfer_entry(entry, adapter=adapter)
    assert result == "saved"
    assert entry.transfer_part == "missing" and entry.awaiting is None
    assert entry.sample_rate == 16000 and entry.duration_seconds == 2.0
    assert f"transfer_recording_missing call=pbx-parent-1 reason={ {'no_name': 'not_started', 'unreadable': 'unreadable'}[case] }" in caplog.text
    assert spool.written[0][1] == spool.ai  # AI part kept as is
    # A part that was not joined is never deleted from the phone system.
    assert not any(o[0] == "delete" for o in spool.order)
    if case == "no_name":
        assert adapter.forgotten == []
        assert not any(o[0] in ("stop", "fetch") for o in spool.order)


@pytest.mark.asyncio
async def test_a_part_that_is_not_there_yet_is_asked_for_again_before_it_is_given_up(spool, caplog):
    """The phone system may still be writing the file when the call ends."""
    adapter = _FakeAdapter(part=None, order=spool.order)
    entry = make_entry()
    with caplog.at_level(logging.WARNING):
        for tries in range(1, tr._FETCH_TRIES):
            assert await tr.finalize_transfer_entry(entry, adapter=adapter) == "retry"
            assert entry.awaiting == "transfer_part" and entry.transfer_part is None
            assert entry.attempts == tries
            assert entry.last_error == "transfer_part_not_found"
    assert len(spool.updated) == tr._FETCH_TRIES - 1
    assert spool.written == [] and spool.processed == []
    assert "transfer_recording_not_ready call=pbx-parent-1 reason=not_found tries=1" in caplog.text

    # The last try gives up: the AI part is saved, marked as missing its
    # transfer part, and the save starts its own count from zero.
    with caplog.at_level(logging.ERROR):
        assert await tr.finalize_transfer_entry(entry, adapter=adapter) == "saved"
    assert entry.transfer_part == "missing" and entry.awaiting is None
    assert entry.attempts == 0 and entry.last_error is None
    assert "transfer_recording_missing call=pbx-parent-1 reason=not_found" in caplog.text
    assert spool.written[0][1] == spool.ai
    assert not any(o[0] == "delete" for o in spool.order)


@pytest.mark.asyncio
async def test_a_part_arriving_on_a_later_try_is_joined(spool):
    adapter = _FakeAdapter(part=None, order=spool.order)
    entry = make_entry()
    assert await tr.finalize_transfer_entry(entry, adapter=adapter) == "retry"
    adapter.part = make_wav(8000, 1, 3.0)
    assert await tr.finalize_transfer_entry(entry, adapter=adapter) == "saved"
    assert entry.transfer_part == "included" and entry.awaiting is None
    assert entry.attempts == 0
    assert [o[0] for o in spool.order][-3:] == ["write", "delete", "process"]


@pytest.mark.asyncio
async def test_no_phone_system_to_ask_counts_as_not_ready(spool):
    entry = make_entry()
    assert await tr.finalize_transfer_entry(entry, adapter=None) == "retry"
    assert entry.awaiting == "transfer_part"
    assert entry.last_error == "transfer_part_no_adapter"


@pytest.mark.asyncio
async def test_one_entry_is_never_finalised_twice_at_once(spool):
    gate = asyncio.Event()

    class Slow(_FakeAdapter):
        async def fetch_stored_recording(self, name, **kw):
            await gate.wait()
            return make_wav(8000, 1, 1.0)

    entry = make_entry()
    first = asyncio.create_task(tr.finalize_transfer_entry(entry, adapter=Slow(order=spool.order)))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert await tr.finalize_transfer_entry(entry, adapter=Slow(order=spool.order)) == "retry"
    gate.set()
    assert await first == "saved"
    assert len(spool.processed) == 1


@pytest.mark.asyncio
async def test_finalize_write_failure_returns_retry_and_keeps_asterisk_copy(spool, caplog):
    spool.write_error = OSError("disk full")
    adapter = _FakeAdapter(part=make_wav(8000, 1, 1.0), order=spool.order)
    entry = make_entry()
    with caplog.at_level(logging.ERROR):
        result = await tr.finalize_transfer_entry(entry, adapter=adapter)
    assert result == "retry"
    assert entry.awaiting == "transfer_part"
    assert not any(o[0] == "delete" for o in spool.order)
    assert adapter.forgotten == []
    assert spool.processed == []
    assert "transfer_recording_finalize_failed call=pbx-parent-1 err=OSError" in caplog.text


@pytest.mark.asyncio
async def test_finalize_propagates_cancellation(spool):
    class Hang(_FakeAdapter):
        async def fetch_stored_recording(self, name, **kw):
            raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await tr.finalize_transfer_entry(make_entry(), adapter=Hang())


@pytest.mark.asyncio
async def test_awaiting_entry_live_call_untouched(monkeypatch):
    called = []

    async def fin(entry, **kw):
        called.append(entry)
        return "saved"

    monkeypatch.setattr(tr, "finalize_transfer_entry", fin)
    monkeypatch.setattr(tr, "_live_session_exists", lambda pbx: True)
    await tr.finalize_awaiting_entry(make_entry())
    assert called == []


@pytest.mark.asyncio
async def test_awaiting_entry_ended_call_finalised(monkeypatch):
    called = []
    adapter = object()

    async def fin(entry, **kw):
        called.append((entry, kw))
        return "saved"

    from app.domain.services.telephony import adapter_registry

    monkeypatch.setattr(tr, "finalize_transfer_entry", fin)
    monkeypatch.setattr(tr, "_live_session_exists", lambda pbx: False)
    monkeypatch.setattr(adapter_registry, "get_adapter", lambda: adapter)
    entry = make_entry()
    await tr.finalize_awaiting_entry(entry)
    assert called == [(entry, {"adapter": adapter})]


@pytest.mark.asyncio
async def test_awaiting_entry_no_adapter_waits_until_old(monkeypatch):
    called = []

    async def fin(entry, **kw):
        called.append(kw)
        return "saved"

    from app.domain.services.telephony import adapter_registry
    from datetime import datetime, timedelta, timezone

    monkeypatch.setattr(tr, "finalize_transfer_entry", fin)
    monkeypatch.setattr(tr, "_live_session_exists", lambda pbx: False)
    monkeypatch.setattr(adapter_registry, "get_adapter", lambda: None)
    monkeypatch.delenv("TELEPHONY_TRANSFER_MAX_CALL_DURATION_S", raising=False)
    young = make_entry()
    young.created_at = (datetime.now(timezone.utc) - timedelta(seconds=100)).isoformat()
    await tr.finalize_awaiting_entry(young)
    assert called == []
    old = make_entry()
    old.created_at = (datetime.now(timezone.utc) - timedelta(seconds=3600 + 901)).isoformat()
    await tr.finalize_awaiting_entry(old)
    assert called == [{"adapter": None}]
