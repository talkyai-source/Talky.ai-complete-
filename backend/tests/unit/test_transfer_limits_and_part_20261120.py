"""20 Nov 2026 inspector findings on transfer: account cap, unfinished WAV,
false alarm, and a lost person's part. One test group per item."""

import asyncio
import logging
from types import SimpleNamespace

import pytest

from app.domain.services.telephony import lifecycle
from app.domain.services.telephony import transfer_recording as tr
from tests.unit.test_transfer_call_limits import _fake_join, _joins_finished, make_vs, wired  # noqa: F401
from tests.unit.test_transfer_recording import _FakeAdapter, make_entry, make_wav, read, spool  # noqa: F401


# --------------------------------------------------------------------------
# Item 1: the account's own maximum call length always stands
# --------------------------------------------------------------------------
def _fake_account_cap(monkeypatch, value):
    class _Conn:
        async def fetchval(self, *a):
            return value

    class _Ctx:
        async def __aenter__(self):
            return _Conn()

        async def __aexit__(self, *a):
            return False

    import app.core.container as container
    import app.core.db_utils as db_utils

    monkeypatch.setattr(container, "get_container", lambda: SimpleNamespace(db_pool=object()))
    monkeypatch.setattr(db_utils, "acquire_with_tenant", lambda pool, tenant: _Ctx())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "account_cap,expected",
    [(900, 900), (3599, 3599), (5000, 3600), (None, 3600), (300, 300)],
)
async def test_account_cap_limits_a_transferred_call(wired, monkeypatch, account_cap, expected):  # noqa: F811
    _fake_account_cap(monkeypatch, account_cap)
    vs = make_vs(max_s=600)
    vs._dialer_tenant_id = "t1"
    wired.vs = vs
    await lifecycle._apply_account_call_cap(vs)
    await lifecycle._on_transfer_connected("pbx-1", "target-1")
    assert vs._max_call_duration_seconds == expected


@pytest.mark.asyncio
async def test_account_cap_read_after_the_handover_still_limits(monkeypatch):
    _fake_account_cap(monkeypatch, 900)
    vs = make_vs(max_s=3600)
    vs._dialer_tenant_id = "t1"
    vs._transfer_connected = True
    await lifecycle._apply_account_call_cap(vs)
    assert vs._max_call_duration_seconds == 900


@pytest.mark.asyncio
async def test_account_cap_between_600_and_3599_leaves_a_normal_call_at_600(monkeypatch):
    _fake_account_cap(monkeypatch, 900)
    vs = make_vs(max_s=600)
    vs._dialer_tenant_id = "t1"
    await lifecycle._apply_account_call_cap(vs)
    assert vs._max_call_duration_seconds == 600  # meaning for normal calls unchanged


# --------------------------------------------------------------------------
# Item 2: a transfer part that Asterisk is still closing is never "joined" as 0 s
# --------------------------------------------------------------------------
def _zero_frame_header(wav: bytes) -> bytes:
    """What Asterisk's file looks like before it is closed: data size 0, audio follows."""
    b = bytearray(wav)
    b[40:44] = b"\x00\x00\x00\x00"
    return bytes(b)


class _SeqAdapter(_FakeAdapter):
    def __init__(self, parts, live=None, order=None):
        super().__init__(part=None, order=order)
        self.parts = list(parts)
        self.live = list(live or [])

    async def transfer_recording_is_live(self, name):
        self.order.append(("live", name))
        return self.live.pop(0) if self.live else False

    async def fetch_stored_recording(self, name, **kw):
        self.order.append(("fetch", name))
        return self.parts.pop(0) if len(self.parts) > 1 else self.parts[0]


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    monkeypatch.setattr(tr, "_LIVE_POLL_DELAY_S", 0.0, raising=False)
    monkeypatch.setattr(tr, "_NOT_READY_REFETCH_DELAY_S", 0.0, raising=False)


@pytest.mark.asyncio
async def test_a_zero_frame_header_is_refetched_until_readable(spool):  # noqa: F811
    good = make_wav(8000, 1, 1.0)
    adapter = _SeqAdapter([_zero_frame_header(good), good], order=spool.order)
    entry = make_entry()
    assert await tr.finalize_transfer_entry(entry, adapter=adapter, call_ended=True) == "saved"
    assert entry.transfer_part == "included"
    assert abs(entry.duration_seconds - 3.0) < 0.02  # 2 s agent part + 1 s person part
    assert [o[0] for o in spool.order].count("fetch") == 2
    assert any(o[0] == "delete" for o in spool.order)


@pytest.mark.asyncio
async def test_a_part_that_never_becomes_readable_is_missing_and_never_deleted(spool, caplog):  # noqa: F811
    bad = _zero_frame_header(make_wav(8000, 1, 1.0))
    adapter = _SeqAdapter([bad], order=spool.order)
    entry = make_entry()
    result = None
    with caplog.at_level(logging.ERROR):
        for _ in range(tr._FETCH_TRIES):
            result = await tr.finalize_transfer_entry(entry, adapter=adapter, call_ended=True)
            if result != "retry":
                break
    assert result == "saved"
    assert entry.transfer_part == "missing" and entry.awaiting is None
    assert "transfer_recording_missing call=pbx-parent-1 reason=unreadable" in caplog.text
    assert not any(o[0] == "delete" for o in spool.order)


@pytest.mark.asyncio
async def test_fetch_waits_until_the_recording_is_no_longer_live(spool):  # noqa: F811
    adapter = _SeqAdapter([make_wav(8000, 1, 1.0)], live=[True, True, False], order=spool.order)
    entry = make_entry()
    assert await tr.finalize_transfer_entry(entry, adapter=adapter, call_ended=True) == "saved"
    kinds = [o[0] for o in spool.order]
    assert kinds[:5] == ["stop", "live", "live", "live", "fetch"]


@pytest.mark.asyncio
async def test_an_empty_joined_part_never_deletes_the_phone_systems_copy(spool):  # noqa: F811
    adapter = _SeqAdapter([make_wav(8000, 1, 0.0)], order=spool.order)
    entry = make_entry()
    assert await tr.finalize_transfer_entry(entry, adapter=adapter, call_ended=True) == "saved"
    assert not any(o[0] == "delete" for o in spool.order)


# --------------------------------------------------------------------------
# Item 3: no ERROR for a call that was never going to have an entry
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_recording_disabled_account_logs_no_error_at_the_end(wired, monkeypatch, caplog):  # noqa: F811
    from app.domain.services import recording_spool

    monkeypatch.setattr(recording_spool, "list_entries", lambda: [])
    vs = make_vs()
    vs._recording_refused_by_policy = True
    wired.vs = vs
    wired.adapter = SimpleNamespace(transfer_recording_name=lambda cid: None)
    with caplog.at_level(logging.INFO):
        await lifecycle._on_transfer_connected("pbx-1", "target-1")
        await lifecycle._finalize_transfer_recording_at_end(vs, "pbx-1")
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert not getattr(vs, "_recording_awaiting_transfer_part", False)


@pytest.mark.asyncio
async def test_a_missing_entry_that_should_exist_still_logs_the_error(wired, monkeypatch, caplog):  # noqa: F811
    from app.domain.services import recording_spool

    monkeypatch.setattr(recording_spool, "list_entries", lambda: [])
    vs = make_vs()
    wired.vs = vs
    wired.adapter = SimpleNamespace(transfer_recording_name=lambda cid: None)

    async def parked(vs_, call_id, **kw):
        # the save really parked an entry for this call
        entries.append(
            SimpleNamespace(awaiting="transfer_part", session_call_id="sess-1", pbx_call_id=call_id)
        )

    entries: list = []
    monkeypatch.setattr(recording_spool, "list_entries", lambda: list(entries))
    monkeypatch.setattr(lifecycle, "_save_call_recording", parked)
    await lifecycle._on_transfer_connected("pbx-1", "target-1")
    assert vs._recording_awaiting_transfer_part is True
    entries.clear()  # ... and then it is gone
    with caplog.at_level(logging.ERROR):
        await lifecycle._finalize_transfer_recording_at_end(vs, "pbx-1")
    assert "transfer_recording_entry_missing call=pbx-1" in caplog.text


# --------------------------------------------------------------------------
# Item 4: the handover could not park the file; the person's part must survive
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_failed_pending_write_at_handover_keeps_the_phone_systems_copy(
    wired, spool, monkeypatch, caplog  # noqa: F811
):
    from app.domain.services import recording_spool
    from app.domain.services.telephony import recording

    name = "talky-xferrec-pbx-1"
    adapter = _SeqAdapter([make_wav(8000, 1, 1.0)], order=spool.order)
    adapter.transfer_recording_name = lambda cid: name
    wired.vs = make_vs()
    wired.adapter = adapter
    monkeypatch.setattr(lifecycle, "get_adapter", lambda: adapter)
    monkeypatch.setattr(recording_spool, "list_entries", lambda: [])  # nothing parked
    monkeypatch.setattr(recording, "_build_spool_entry", lambda *a, **k: make_entry(name=name))

    # handover: the pending write failed, the agent's part was saved at once
    await lifecycle._on_transfer_connected("pbx-1", "target-1")
    with caplog.at_level(logging.WARNING):
        await lifecycle._finalize_transfer_recording_at_end(wired.vs, "pbx-1")
        await _joins_finished()
    assert not any(o[0] == "delete" for o in spool.order)
    warnings = [r.getMessage() for r in caplog.records if "transfer_recording_copy_kept" in r.getMessage()]
    assert len(warnings) == 1 and "call=pbx-parent-1" in warnings[0]
