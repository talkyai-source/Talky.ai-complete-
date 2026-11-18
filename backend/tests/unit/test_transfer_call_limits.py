"""A transferred call is not bound by the AI call cap; other calls are unchanged."""

import asyncio
import logging
from types import SimpleNamespace

import pytest

from app.domain.services.telephony import lifecycle
from app.domain.services.telephony import transfer_recording as tr
from tests.unit.test_asterisk_outbound_supervised_transfer import (
    SWITCH,
    _live_outbound_adapter,
)
from tests.unit.test_transfer_recording import _connect_outbound, _RecRig

ENV = "TELEPHONY_TRANSFER_MAX_CALL_DURATION_S"


# --------------------------------------------------------------------------
# env reader
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, 3600),
        ("", 3600),
        ("abc", 3600),
        ("nan", 3600),
        ("1800", 1800),
        ("1800.9", 1800),
        ("5", 600),
        ("0", 600),
        ("-50", 600),
        ("99999", 14400),
    ],
)
def test_transfer_max_duration_reader(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv(ENV, raising=False)
    else:
        monkeypatch.setenv(ENV, raw)
    assert tr.transfer_max_duration_seconds() == expected


def test_reader_is_read_at_call_time(monkeypatch):
    monkeypatch.setenv(ENV, "1000")
    assert tr.transfer_max_duration_seconds() == 1000
    monkeypatch.setenv(ENV, "2000")
    assert tr.transfer_max_duration_seconds() == 2000


# --------------------------------------------------------------------------
# fakes
# --------------------------------------------------------------------------
class _CallSession:
    def __init__(self, duration, stale=True):
        self.duration = duration
        self.stale = stale
        self.call_id = "sess-1"

    def is_stale(self, _timeout):
        return self.stale

    def get_duration_seconds(self):
        return self.duration


def make_vs(*, direction="outbound", max_s=600, duration=0, stale=True, inbound_admission=None):
    return SimpleNamespace(
        call_id="sess-1",
        call_session=_CallSession(duration, stale),
        config=SimpleNamespace(direction=direction),
        _max_call_duration_seconds=max_s,
        _soft_call_cap_seconds=300,
        _inbound_admission=inbound_admission,
        pipeline=None,
        transcript_service=None,
    )


@pytest.fixture
def wired(monkeypatch):
    """lifecycle wiring for _on_transfer_connected with every collaborator faked."""
    saved = []

    async def fake_save(vs, call_id, **kw):
        saved.append((call_id, kw))

    class _Orch:
        async def end_session(self, vs):
            pass

    holder = SimpleNamespace(vs=None, saved=saved, adapter=None)
    monkeypatch.setattr(lifecycle, "_save_call_recording", fake_save)
    monkeypatch.setattr(lifecycle, "_get_orchestrator", lambda: _Orch())
    monkeypatch.setattr(
        lifecycle, "_state", lambda: SimpleNamespace(get_voice_session=lambda cid: holder.vs)
    )
    monkeypatch.setattr(lifecycle, "get_adapter", lambda: holder.adapter)
    monkeypatch.delenv(ENV, raising=False)
    return holder


# --------------------------------------------------------------------------
# limit raised on handover
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_handover_raises_limit_and_logs(wired, caplog):
    vs = make_vs(max_s=600)
    wired.vs = vs
    with caplog.at_level(logging.INFO):
        await lifecycle._on_transfer_connected("pbx-1", "target-1")
    assert vs._max_call_duration_seconds == 3600
    assert "transfer_call_limit_raised call=pbx-1 max_s=3600" in caplog.text


@pytest.mark.asyncio
async def test_account_cap_of_180_is_replaced_by_transfer_limit(wired):
    vs = make_vs(max_s=180)
    wired.vs = vs
    await lifecycle._on_transfer_connected("pbx-1", "target-1")
    assert vs._max_call_duration_seconds == 3600


@pytest.mark.asyncio
async def test_limit_never_lowered_when_already_higher(wired, monkeypatch):
    monkeypatch.setenv(ENV, "600")
    vs = make_vs(max_s=5000)
    wired.vs = vs
    await lifecycle._on_transfer_connected("pbx-1", "target-1")
    assert vs._max_call_duration_seconds == 5000


@pytest.mark.asyncio
async def test_inbound_session_keeps_its_limit_and_old_save(wired):
    vs = make_vs(direction="inbound", max_s=600, inbound_admission={"allowed": True})
    wired.vs = vs
    await lifecycle._on_transfer_connected("pbx-1", "target-1")
    assert vs._max_call_duration_seconds == 600
    assert wired.saved == [("pbx-1", {})]
    assert vs._recording_saved_at_transfer is True
    assert not getattr(vs, "_recording_awaiting_transfer_part", False)


@pytest.mark.asyncio
async def test_outbound_save_is_parked_awaiting_the_transfer_part(wired):
    vs = make_vs()
    wired.vs = vs
    wired.adapter = SimpleNamespace(transfer_recording_name=lambda cid: f"rec-{cid}")
    await lifecycle._on_transfer_connected("pbx-1", "target-1")
    assert wired.saved == [
        ("pbx-1", {"awaiting_transfer": True, "transfer_recording_name": "rec-pbx-1"})
    ]
    assert vs._recording_saved_at_transfer is True
    assert vs._recording_awaiting_transfer_part is True


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter", [None, object()])
async def test_outbound_save_without_adapter_method_has_no_name(wired, adapter):
    vs = make_vs()
    wired.vs = vs
    wired.adapter = adapter
    await lifecycle._on_transfer_connected("pbx-1", "target-1")
    assert wired.saved[0][1] == {"awaiting_transfer": True, "transfer_recording_name": None}


@pytest.mark.asyncio
async def test_save_failure_is_logged_and_does_not_set_flags(wired, monkeypatch, caplog):
    async def boom(*a, **k):
        raise RuntimeError("x")

    monkeypatch.setattr(lifecycle, "_save_call_recording", boom)
    vs = make_vs()
    wired.vs = vs
    with caplog.at_level(logging.WARNING):
        await lifecycle._on_transfer_connected("pbx-1", "target-1")
    assert "Transfer recording save failed" in caplog.text
    assert not getattr(vs, "_recording_saved_at_transfer", False)
    assert not getattr(vs, "_recording_awaiting_transfer_part", False)


# --------------------------------------------------------------------------
# (a) no inactivity / media watchdog ends a transferred call
# --------------------------------------------------------------------------
def test_inactivity_does_not_end_transferred_call_but_ends_others():
    transferred = make_vs(duration=1000, stale=True, max_s=3600)
    transferred._transfer_connected = True
    ordinary = make_vs(duration=100, stale=True)
    stale, overlong = lifecycle._collect_expired_sessions(
        [("t", transferred), ("o", ordinary)],
        inactivity_timeout_s=300,
        max_duration_s=600,
        soft_cap_s=300,
    )
    assert stale == ["o"]
    assert "t" not in stale and "t" not in overlong


def test_soft_cap_does_not_end_transferred_call():
    vs = make_vs(duration=1000, stale=False, max_s=3600)
    vs._transfer_connected = True
    _, overlong = lifecycle._collect_expired_sessions(
        [("t", vs)], inactivity_timeout_s=300, max_duration_s=600, soft_cap_s=300
    )
    assert overlong == []


@pytest.mark.asyncio
async def test_media_watchdog_ignores_a_handed_over_call(monkeypatch):
    monkeypatch.setenv(SWITCH, "true")
    adapter, parent = _live_outbound_adapter()
    rig = _RecRig(adapter, parent)
    assert parent in adapter.gateway_session_map()
    await _connect_outbound(rig, adapter, parent)
    assert parent not in adapter.gateway_session_map()

    async def live():
        return set()  # the gateway has no session for it: the media is detached

    adapter.list_active_gateway_session_ids = live
    ended = []

    async def force_end(cid):
        ended.append(cid)

    lifecycle._dead_media_ticks.clear()
    for _ in range(5):
        assert await lifecycle._reconcile_dead_media(adapter, force_end) == []
    assert ended == []


# --------------------------------------------------------------------------
# (b) ended at the raised limit, and at the prepaid-balance deadline if earlier
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_call_ends_at_raised_limit_not_at_600(wired):
    vs = make_vs(max_s=600, stale=True)
    wired.vs = vs
    await lifecycle._on_transfer_connected("pbx-1", "target-1")

    def overlong_at(duration):
        vs.call_session.duration = duration
        return lifecycle._collect_expired_sessions(
            [("c", vs)], inactivity_timeout_s=300, max_duration_s=600, soft_cap_s=300
        )[1]

    assert overlong_at(601) == []
    assert overlong_at(1200) == []
    assert overlong_at(3600) == []
    assert overlong_at(3601) == ["c"]


@pytest.mark.asyncio
async def test_prepaid_balance_deadline_still_ends_a_transferred_call(wired):
    vs = make_vs(max_s=600)
    vs._balance_deadline_seconds = 900
    wired.vs = vs
    await lifecycle._on_transfer_connected("pbx-1", "target-1")
    assert vs._balance_deadline_seconds == 900  # untouched

    def overlong_at(duration):
        vs.call_session.duration = duration
        return lifecycle._collect_expired_sessions(
            [("c", vs)], inactivity_timeout_s=300, max_duration_s=600, soft_cap_s=300
        )[1]

    assert overlong_at(899) == []
    assert overlong_at(901) == ["c"]


@pytest.mark.asyncio
async def test_late_account_cap_read_does_not_lower_a_transferred_call(monkeypatch):
    class _Conn:
        async def fetchval(self, *a):
            return 180

    class _Ctx:
        async def __aenter__(self):
            return _Conn()

        async def __aexit__(self, *a):
            return False

    import app.core.container as container
    import app.core.db_utils as db_utils

    monkeypatch.setattr(container, "get_container", lambda: SimpleNamespace(db_pool=object()))
    monkeypatch.setattr(db_utils, "acquire_with_tenant", lambda pool, tenant: _Ctx())

    ordinary = make_vs(max_s=600)
    ordinary._dialer_tenant_id = "t1"
    await lifecycle._apply_account_call_cap(ordinary)
    assert ordinary._max_call_duration_seconds == 180  # normal calls still capped

    moved = make_vs(max_s=3600)
    moved._dialer_tenant_id = "t1"
    moved._transfer_connected = True
    await lifecycle._apply_account_call_cap(moved)
    assert moved._max_call_duration_seconds == 3600


# --------------------------------------------------------------------------
# (c) billed duration
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_billed_duration_is_real_for_a_twenty_minute_transferred_call(wired, monkeypatch):
    monkeypatch.setattr(lifecycle, "_SESSION_MAX_DURATION_S", 600)
    vs = make_vs(max_s=600)
    wired.vs = vs
    # Never transferred: still capped at 600.
    assert (
        lifecycle._confirmed_outbound_duration_seconds(vs, 1200.0, answered_at_monotonic=0.0)
        == 600
    )
    await lifecycle._on_transfer_connected("pbx-1", "target-1")
    assert (
        lifecycle._confirmed_outbound_duration_seconds(vs, 1200.0, answered_at_monotonic=0.0)
        == 1200
    )


def test_never_transferred_call_keeps_600(monkeypatch):
    monkeypatch.setattr(lifecycle, "_SESSION_MAX_DURATION_S", 600)
    vs = make_vs(max_s=600, duration=700, stale=False)
    _, overlong = lifecycle._collect_expired_sessions(
        [("c", vs)], inactivity_timeout_s=300, max_duration_s=600, soft_cap_s=0
    )
    assert overlong == ["c"]
    assert lifecycle._confirmed_outbound_duration_seconds(vs, 1200.0, answered_at_monotonic=0.0) == 600


def test_durable_fallback_stays_capped_at_600(monkeypatch):
    from datetime import datetime, timedelta, timezone

    monkeypatch.setattr(lifecycle, "_SESSION_MAX_DURATION_S", 600)
    start = datetime.now(timezone.utc)
    assert lifecycle._durable_outbound_duration_seconds(start, start + timedelta(seconds=1200)) == 600


# --------------------------------------------------------------------------
# teardown + hook registration
# --------------------------------------------------------------------------
def test_awaiting_finalizer_registered_at_import():
    from app.domain.services.telephony import recording

    assert recording._awaiting_finalizer is tr.finalize_awaiting_entry


@pytest.mark.asyncio
async def test_teardown_helper_finalises_the_matching_entry(monkeypatch):
    entries = [
        SimpleNamespace(awaiting=None, session_call_id="sess-1", pbx_call_id="pbx-1"),
        SimpleNamespace(awaiting="transfer_part", session_call_id="other", pbx_call_id="other"),
        SimpleNamespace(awaiting="transfer_part", session_call_id="sess-1", pbx_call_id="pbx-x"),
    ]
    from app.domain.services import recording_spool

    monkeypatch.setattr(recording_spool, "list_entries", lambda: entries)
    got = []

    async def fin(entry, *, adapter, voice_session):
        got.append((entry, adapter, voice_session))
        return "saved"

    monkeypatch.setattr(lifecycle, "finalize_transfer_entry", fin)
    adapter = object()
    monkeypatch.setattr(lifecycle, "get_adapter", lambda: adapter)
    vs = make_vs()
    await lifecycle._finalize_transfer_recording_at_end(vs, "pbx-1")
    assert got == [(entries[2], adapter, vs)]


@pytest.mark.asyncio
async def test_teardown_helper_missing_entry_and_timeout_never_raise(monkeypatch, caplog):
    from app.domain.services import recording_spool

    monkeypatch.setattr(recording_spool, "list_entries", lambda: [])
    with caplog.at_level(logging.WARNING):
        await lifecycle._finalize_transfer_recording_at_end(make_vs(), "pbx-1")
    assert "transfer_recording_entry_missing call=pbx-1" in caplog.text

    entry = SimpleNamespace(awaiting="transfer_part", session_call_id="sess-1", pbx_call_id="pbx-1")
    monkeypatch.setattr(recording_spool, "list_entries", lambda: [entry])

    async def hang(entry, **kw):
        await asyncio.sleep(60)

    real_wait_for = asyncio.wait_for

    async def quick_wait_for(aw, timeout):
        return await real_wait_for(aw, 0.05)

    monkeypatch.setattr(lifecycle, "finalize_transfer_entry", hang)
    monkeypatch.setattr(lifecycle.asyncio, "wait_for", quick_wait_for)
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        await lifecycle._finalize_transfer_recording_at_end(make_vs(), "pbx-1")
    assert "transfer_recording_finalize_deferred call=pbx-1" in caplog.text
