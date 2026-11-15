"""Outbound calls that will not fit are refused BEFORE dialling.

Ringing outbound calls (ringing warmups with no voice session yet) must count
against ``_MAX_TELEPHONY_SESSIONS`` in the pre-dial check and in the inbound
pre-answer check; otherwise the people who answer later are hung up on.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.api.v1.endpoints import telephony_bridge as bridge
from app.domain.services.telephony import lifecycle


class FakeState:
    """Minimal state backend: sessions and ringing warmups only."""

    def __init__(self) -> None:
        self.sessions: dict[str, object] = {}
        self.warmups: dict[str, object] = {}

    def voice_session_count(self) -> int:
        return len(self.sessions)

    def get_voice_session(self, call_id):
        return self.sessions.get(call_id)

    def iter_ringing_warmup_keys(self):
        return list(self.warmups.keys())

    def ringing_warmup_count(self) -> int:
        return len(self.warmups)

    def is_telephony_owner(self) -> bool:
        return True

    async def register_cleanup_obligation(self, _call_id, **_kwargs):
        return None


@pytest.fixture
def state(monkeypatch):
    st = FakeState()
    monkeypatch.setattr(lifecycle, "_state", lambda: st)
    monkeypatch.setattr(bridge, "get_state_backend", lambda: st)
    monkeypatch.setattr(lifecycle, "_MAX_TELEPHONY_SESSIONS", 2)
    monkeypatch.setattr(bridge, "_MAX_TELEPHONY_SESSIONS", 2)
    monkeypatch.setattr(lifecycle, "_inbound_admissions_in_flight", {})
    monkeypatch.setattr(lifecycle, "_inbound_admissions_pending", set())
    from app.core import readiness

    monkeypatch.setattr(readiness, "is_pod_at_capacity", lambda: False)
    return st


def _ring(st: FakeState, call_id: str) -> None:
    st.warmups[call_id] = (object(), None)


def _answer(st: FakeState, call_id: str) -> None:
    st.sessions[call_id] = object()


def _try_dial(st: FakeState, call_id: str) -> bool:
    """Mimic make_call: refuse when full, else register a ringing call."""
    if bridge._outbound_capacity_refusal(call_id) is not None:
        return False
    _ring(st, call_id)
    return True


def test_committed_load_counts_session_ringing_and_pending_inbound(state):
    _answer(state, "live-1")
    _ring(state, "ring-1")
    lifecycle._inbound_admissions_in_flight["in-1"] = {}
    lifecycle._inbound_admissions_pending.add("in-2")
    assert lifecycle.committed_call_load() == 4


def test_committed_load_counts_call_with_warmup_and_session_once(state):
    _ring(state, "both")
    _answer(state, "both")
    lifecycle._inbound_admissions_in_flight["both"] = {}
    assert lifecycle.committed_call_load() == 1


def test_outbound_refused_before_dial_when_live_plus_ringing_fill_limit(state):
    _answer(state, "live-1")
    _ring(state, "ring-1")
    refusal = bridge._outbound_capacity_refusal("+15551234567")
    assert refusal is not None
    assert refusal["error"] == "pod_at_capacity"
    assert refusal["committed_calls"] == 2
    assert refusal["max_sessions"] == 2
    assert refusal["active_sessions"] == 1


def test_n_admitted_then_n_plus_one_refused_then_one_admitted_after_end(state):
    assert _try_dial(state, "c1") is True
    assert _try_dial(state, "c2") is True
    assert _try_dial(state, "c3") is False
    state.warmups.pop("c1")  # ringing call ends unanswered
    assert _try_dial(state, "c4") is True
    assert _try_dial(state, "c5") is False


def test_below_limit_nothing_refused(state):
    assert bridge._outbound_capacity_refusal("+15551234567") is None
    _ring(state, "ring-1")
    assert bridge._outbound_capacity_refusal("+15551234567") is None
    assert lifecycle.committed_call_load() == 1


@pytest.mark.asyncio
async def test_inbound_pre_answer_refuses_when_live_plus_ringing_outbound_fill_limit(state, monkeypatch):
    import app.core.container as container_module

    monkeypatch.setattr(
        container_module,
        "get_container",
        lambda: SimpleNamespace(is_initialized=True, db_pool=object(), redis=object()),
    )
    _answer(state, "live-1")
    _ring(state, "ring-1")
    with pytest.raises(RuntimeError, match="pod capacity reached before inbound answer"):
        await lifecycle._admit_inbound_call(
            "new-inbound",
            {"called_did": "+15551234567", "caller_number": "+15557654321"},
        )
    assert "new-inbound" not in lifecycle._inbound_admissions_pending


# -- through make_call itself -------------------------------------------------
def _call_path(monkeypatch, st: FakeState):
    """The shared make_call fixture, with this file's capacity state in charge."""
    from tests.unit.test_outbound_campaign_boundaries import _campaign, _install_call_path

    path = _install_call_path(monkeypatch, campaign_rows=[_campaign(), _campaign()])
    # _install_call_path installs its own state for the bridge; capacity is
    # read through lifecycle._state, which the `state` fixture already owns.
    monkeypatch.setattr(lifecycle, "_state", lambda: st)
    return path


@pytest.mark.asyncio
async def test_make_call_refuses_before_warmup_and_dial_when_ringing_fills_the_limit(state, monkeypatch):
    from fastapi import HTTPException

    path = _call_path(monkeypatch, state)
    _answer(state, "live-1")
    _ring(state, "ring-1")
    with pytest.raises(HTTPException) as error:
        await bridge.make_call(path.request, path.body)
    assert error.value.status_code == 503
    assert error.value.detail["error"] == "pod_at_capacity"
    assert error.value.detail["committed_calls"] == 2
    assert "Retry-After" in error.value.headers
    assert "prewarm" not in path.events
    assert "originate" not in path.events


@pytest.mark.asyncio
async def test_make_call_refuses_when_an_inbound_call_takes_the_last_slot_during_warmup(state, monkeypatch):
    from fastapi import HTTPException

    path = _call_path(monkeypatch, state)
    _answer(state, "live-1")  # one slot left when make_call starts
    warm = bridge.prepare_prewarmed_session

    async def warm_while_inbound_arrives(**kwargs):
        result = await warm(**kwargs)
        lifecycle._inbound_admissions_pending.add("inbound-during-warmup")
        return result

    monkeypatch.setattr(bridge, "prepare_prewarmed_session", warm_while_inbound_arrives)
    with pytest.raises(HTTPException) as error:
        await bridge.make_call(path.request, path.body)
    assert error.value.status_code == 503
    assert error.value.detail["error"] == "pod_at_capacity"
    assert "prewarm" in path.events
    assert "originate" not in path.events
    # The warmed-up session is closed, not leaked.
    assert "end_session" in path.events


@pytest.mark.asyncio
async def test_make_call_still_dials_below_the_limit(state, monkeypatch):
    path = _call_path(monkeypatch, state)
    _answer(state, "live-1")  # one slot left, and nothing takes it
    response = await bridge.make_call(path.request, path.body)
    assert response.status_code == 200
    assert "originate" in path.events


def test_inbound_call_with_a_ringing_warmup_is_counted_once(state):
    _ring(state, "inbound-1")
    lifecycle._inbound_admissions_pending.add("inbound-1")
    assert lifecycle.committed_call_load() == 1


# -- drain before a restart ---------------------------------------------------
@pytest.mark.asyncio
async def test_inbound_pre_answer_refuses_while_the_process_is_draining(state, monkeypatch):
    import app.core.container as container_module
    from app.core import readiness

    monkeypatch.setattr(
        container_module,
        "get_container",
        lambda: SimpleNamespace(is_initialized=True, db_pool=object(), redis=object()),
    )
    monkeypatch.setattr(readiness, "is_draining", lambda: True)
    # Plenty of room: the refusal is the drain, not capacity.
    with pytest.raises(RuntimeError, match="pod draining before inbound answer"):
        await lifecycle._admit_inbound_call(
            "new-inbound",
            {"called_did": "+15551234567", "caller_number": "+15557654321"},
        )
    assert "new-inbound" not in lifecycle._inbound_admissions_pending


@pytest.mark.asyncio
async def test_readiness_counts_ringing_calls_once_session_management_is_started(state, monkeypatch):
    from app.core import readiness

    async def _idle():
        return None

    for name in ("_session_watchdog", "_media_session_watchdog", "_account_call_authority_watchdog"):
        monkeypatch.setattr(bridge, name, _idle)
    for name in ("_watchdog_task", "_media_watchdog_task", "_account_authority_watchdog_task"):
        monkeypatch.setattr(bridge, name, None)
    monkeypatch.setattr(readiness, "_active_count_provider", None)
    monkeypatch.setattr(readiness, "_max_capacity_provider", None)
    monkeypatch.setattr(readiness, "_committed_count_provider", None)

    bridge.ensure_session_management_started()
    _answer(state, "live-1")
    _ring(state, "ring-1")
    snapshot = readiness.snapshot()
    assert snapshot["active_sessions"] == 1
    # The drain script waits on this number, so a ringing call must be in it.
    assert snapshot["committed_calls"] == 2
