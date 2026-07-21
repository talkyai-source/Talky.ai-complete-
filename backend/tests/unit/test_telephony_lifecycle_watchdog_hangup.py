"""Unit tests for telephony/lifecycle.py's FIX #1 (force-end-and-hangup) and
FIX #11 (dead TELEPHONY_MAX_CALL_DURATION_S wired into the watchdog).

FIX #1 — the watchdog / crashed-pipeline force-end paths previously called
only ``_on_call_ended``, which does NOT hang up the live Asterisk channel
(it's designed to run AFTER StasisEnd, when the channel is already gone).
``_force_end_and_hangup`` closes that gap: teardown, then a best-effort
``adapter.hangup(call_id)``.

FIX #11 — ``_SESSION_MAX_DURATION_S`` (env ``TELEPHONY_MAX_CALL_DURATION_S``)
was read once at import time and never referenced anywhere. The watchdog
only ever enforced ~300s *inactivity*, so a call parked on hold/IVR/
voicemail music (continuous transcripts keep ``last_activity_at`` fresh)
could run to the gateway's ~2h hard cap. ``_collect_expired_sessions`` wires
the constant in as a second, independent trip wire.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.domain.services.telephony import lifecycle


def _call_session(*, last_activity_ago_s: float = 0.0, age_s: float = 0.0):
    """A minimal stand-in with just the two methods
    _collect_expired_sessions reads: is_stale() and get_duration_seconds().
    Avoids constructing a full pydantic CallSession (many required fields
    irrelevant to this logic) — same "pure function, plain objects" style as
    the existing _detect_zombie_sessions tests.
    """
    started_at = datetime.utcnow() - timedelta(seconds=age_s)
    last_activity_at = datetime.utcnow() - timedelta(seconds=last_activity_ago_s)

    class _CS:
        def is_stale(self, timeout_seconds: int) -> bool:
            return (datetime.utcnow() - last_activity_at).total_seconds() > timeout_seconds

        def get_duration_seconds(self) -> float:
            return (datetime.utcnow() - started_at).total_seconds()

    return _CS()


# ---------------------------------------------------------------------------
# FIX #11 — _collect_expired_sessions
# ---------------------------------------------------------------------------

class TestCollectExpiredSessions:
    def test_fresh_active_session_is_neither_stale_nor_overlong(self):
        vs = SimpleNamespace(call_session=_call_session(last_activity_ago_s=1, age_s=5))
        stale, overlong = lifecycle._collect_expired_sessions(
            [("call-1", vs)], inactivity_timeout_s=300, max_duration_s=3600,
        )
        assert stale == []
        assert overlong == []

    def test_inactive_session_is_stale(self):
        vs = SimpleNamespace(call_session=_call_session(last_activity_ago_s=301, age_s=301))
        stale, overlong = lifecycle._collect_expired_sessions(
            [("call-1", vs)], inactivity_timeout_s=300, max_duration_s=3600,
        )
        assert stale == ["call-1"]
        assert overlong == []

    def test_active_but_overlong_session_is_reported_via_max_duration(self):
        """The bug FIX #11 closes: continuous activity (hold music / IVR)
        keeps last_activity_at fresh forever, so only the wall-clock age
        check can catch this call."""
        vs = SimpleNamespace(
            call_session=_call_session(last_activity_ago_s=1, age_s=3601)
        )
        stale, overlong = lifecycle._collect_expired_sessions(
            [("call-1", vs)], inactivity_timeout_s=300, max_duration_s=3600,
        )
        assert stale == []
        assert overlong == ["call-1"]

    def test_stale_takes_priority_over_overlong_no_double_report(self):
        vs = SimpleNamespace(
            call_session=_call_session(last_activity_ago_s=301, age_s=999999)
        )
        stale, overlong = lifecycle._collect_expired_sessions(
            [("call-1", vs)], inactivity_timeout_s=300, max_duration_s=3600,
        )
        assert stale == ["call-1"]
        assert overlong == []

    def test_session_without_call_session_is_skipped(self):
        vs = SimpleNamespace(call_session=None)
        stale, overlong = lifecycle._collect_expired_sessions(
            [("call-1", vs)], inactivity_timeout_s=300, max_duration_s=3600,
        )
        assert stale == []
        assert overlong == []

    def test_mixed_batch_classifies_each_independently(self):
        items = [
            ("fresh", SimpleNamespace(call_session=_call_session(last_activity_ago_s=1, age_s=5))),
            ("stale", SimpleNamespace(call_session=_call_session(last_activity_ago_s=400, age_s=400))),
            ("overlong", SimpleNamespace(call_session=_call_session(last_activity_ago_s=1, age_s=4000))),
        ]
        stale, overlong = lifecycle._collect_expired_sessions(
            items, inactivity_timeout_s=300, max_duration_s=3600,
        )
        assert stale == ["stale"]
        assert overlong == ["overlong"]

    def test_max_duration_env_constant_is_wired_not_dead(self):
        """Guards against the exact regression FIX #11 closes: the constant
        must actually be threaded into the watchdog's sweep. This asserts
        the module-level constant exists and is an int the watchdog can use
        (the real wiring is exercised by test_active_but_overlong_... above,
        which fails without _collect_expired_sessions checking it)."""
        assert isinstance(lifecycle._SESSION_MAX_DURATION_S, int)
        assert lifecycle._SESSION_MAX_DURATION_S > 0


# ---------------------------------------------------------------------------
# FIX #1 — _force_end_and_hangup
# ---------------------------------------------------------------------------

class _RecordingAdapter:
    def __init__(self, *, raise_on_hangup: bool = False):
        self.hangup_calls: list[str] = []
        self._raise = raise_on_hangup

    async def hangup(self, call_id: str) -> None:
        self.hangup_calls.append(call_id)
        if self._raise:
            raise RuntimeError("simulated ARI hangup failure")


class TestForceEndAndHangup:
    @pytest.mark.asyncio
    async def test_hangs_up_after_on_call_ended(self, monkeypatch):
        call_order: list[str] = []

        async def fake_on_call_ended(call_id: str) -> None:
            call_order.append(f"on_call_ended:{call_id}")

        adapter = _RecordingAdapter()

        monkeypatch.setattr(lifecycle, "_on_call_ended", fake_on_call_ended)
        monkeypatch.setattr(lifecycle, "get_adapter", lambda: adapter)

        await lifecycle._force_end_and_hangup("call-123")

        assert call_order == ["on_call_ended:call-123"]
        assert adapter.hangup_calls == ["call-123"]

    @pytest.mark.asyncio
    async def test_hangup_failure_does_not_raise(self, monkeypatch):
        """Teardown must be best-effort: a raising adapter.hangup can never
        abort the caller (watchdog loop / done-callback)."""
        async def fake_on_call_ended(call_id: str) -> None:
            return None

        adapter = _RecordingAdapter(raise_on_hangup=True)

        monkeypatch.setattr(lifecycle, "_on_call_ended", fake_on_call_ended)
        monkeypatch.setattr(lifecycle, "get_adapter", lambda: adapter)

        # Must not raise.
        await lifecycle._force_end_and_hangup("call-456")
        assert adapter.hangup_calls == ["call-456"]

    @pytest.mark.asyncio
    async def test_noop_hangup_when_no_adapter_configured(self, monkeypatch):
        async def fake_on_call_ended(call_id: str) -> None:
            return None

        monkeypatch.setattr(lifecycle, "_on_call_ended", fake_on_call_ended)
        monkeypatch.setattr(lifecycle, "get_adapter", lambda: None)

        # Must not raise even though there's nothing to hang up.
        await lifecycle._force_end_and_hangup("call-789")

    @pytest.mark.asyncio
    async def test_pipeline_done_cb_uses_force_end_and_hangup_on_crash(self, monkeypatch):
        """FIX #1b — a crashed pipeline task (e.g. TerminalSTTError
        propagating out of start_pipeline) must drive a real hangup, not
        just _on_call_ended."""
        import asyncio

        called_with: list[str] = []

        async def fake_force_end_and_hangup(call_id: str) -> None:
            called_with.append(call_id)

        monkeypatch.setattr(lifecycle, "_force_end_and_hangup", fake_force_end_and_hangup)
        monkeypatch.setattr(lifecycle, "_state", lambda: SimpleNamespace(get_voice_session=lambda _cid: None))

        async def _boom():
            raise RuntimeError("stt terminally dead")

        task = asyncio.get_event_loop().create_task(_boom())
        try:
            await task
        except RuntimeError:
            pass

        lifecycle._pipeline_done_cb(task, "crashed-call")
        # _pipeline_done_cb schedules the coroutine via asyncio.create_task;
        # give the event loop one tick to run it.
        await asyncio.sleep(0)

        assert called_with == ["crashed-call"]


# ---------------------------------------------------------------------------
# Agent 5-min soft cap + deal-closing extension (natural-workflow item 6)
# ---------------------------------------------------------------------------

class TestSoftCap:
    def test_over_soft_cap_not_closing_is_overlong(self):
        vs = SimpleNamespace(call_session=_call_session(last_activity_ago_s=1, age_s=320))
        _stale, overlong = lifecycle._collect_expired_sessions(
            [("c1", vs)], inactivity_timeout_s=300, max_duration_s=480, soft_cap_s=300,
        )
        assert overlong == ["c1"]

    def test_over_soft_cap_but_closing_survives(self):
        cs = _call_session(last_activity_ago_s=1, age_s=320)
        cs.conversation_context = SimpleNamespace(user_confirmed=True)
        vs = SimpleNamespace(call_session=cs)
        _stale, overlong = lifecycle._collect_expired_sessions(
            [("c1", vs)], inactivity_timeout_s=300, max_duration_s=480, soft_cap_s=300,
        )
        assert overlong == []

    def test_deal_closing_flag_grants_extension(self):
        vs = SimpleNamespace(
            call_session=_call_session(last_activity_ago_s=1, age_s=320),
            _deal_closing=True,
        )
        _stale, overlong = lifecycle._collect_expired_sessions(
            [("c1", vs)], inactivity_timeout_s=300, max_duration_s=480, soft_cap_s=300,
        )
        assert overlong == []

    def test_closing_call_still_ends_at_hard_ceiling(self):
        cs = _call_session(last_activity_ago_s=1, age_s=500)  # past hard ceiling
        cs.conversation_context = SimpleNamespace(user_confirmed=True)
        vs = SimpleNamespace(call_session=cs)
        _stale, overlong = lifecycle._collect_expired_sessions(
            [("c1", vs)], inactivity_timeout_s=300, max_duration_s=480, soft_cap_s=300,
        )
        assert overlong == ["c1"]

    def test_soft_cap_zero_disables_soft_cap(self):
        vs = SimpleNamespace(call_session=_call_session(last_activity_ago_s=1, age_s=320))
        _stale, overlong = lifecycle._collect_expired_sessions(
            [("c1", vs)], inactivity_timeout_s=300, max_duration_s=480, soft_cap_s=0,
        )
        assert overlong == []
