"""Unit tests for the pre-hangup WRAP-UP NUDGE in telephony/lifecycle.py.

Before this, ``_collect_expired_sessions`` + ``_force_end_and_hangup``
enforced the per-session max duration by silently tearing the call down —
the caller was cut off mid-sentence with no warning at all.

The nudge injects ONE system-level instruction into the live conversation
``CALL_WRAP_UP_LEAD_SECONDS`` (default 20) before the *effective* deadline,
reusing the injection channel each pipeline mode already has:

  * cascaded  — a ``MessageRole.SYSTEM`` entry appended to
    ``call_session.conversation_history`` (the same list every mid-call
    steering append in the pipeline writes to; ``turn_streamer`` feeds it
    straight to the LLM as a role-tagged message).
  * realtime  — ``realtime_session.send_text(..., create_response=False)``,
    the ``conversation.item.create`` path documented for "any future
    system-initiated prompt". ``create_response=False`` adds the guidance as
    context without forcing the model to speak over the caller.

These tests pin: fires exactly once, at the right moment, never for a
session that's actively closing, env clamping, and that an injection
failure can never escape into the watchdog loop.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.domain.services.telephony import lifecycle


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _call_session(*, age_s: float, history: list | None = None):
    """Minimal CallSession stand-in: the duration accessor the classifier
    reads plus the conversation_history the cascaded channel appends to."""
    started_at = datetime.utcnow() - timedelta(seconds=age_s)

    class _CS:
        def __init__(self) -> None:
            self.conversation_history = history if history is not None else []

        def is_stale(self, timeout_seconds: int) -> bool:
            return False

        def get_duration_seconds(self) -> float:
            return (datetime.utcnow() - started_at).total_seconds()

    return _CS()


def _cascaded_session(*, age_s: float, **extra):
    history: list = []
    return SimpleNamespace(
        call_session=_call_session(age_s=age_s, history=history),
        realtime_bridge=None,
        realtime_session=None,
        **extra,
    )


class _FakeRealtime:
    def __init__(self) -> None:
        self.sent: list[tuple[str, bool]] = []

    async def send_text(self, text: str, *, create_response: bool = True) -> None:
        self.sent.append((text, create_response))


def _realtime_session(*, age_s: float, **extra):
    rt = _FakeRealtime()
    return SimpleNamespace(
        call_session=_call_session(age_s=age_s),
        realtime_bridge=object(),
        realtime_session=rt,
        **extra,
    )


@pytest.fixture
def collected_tasks(monkeypatch):
    """Capture the fire-and-forget coroutines _dispatch_wrap_up_nudges spawns
    instead of scheduling them, so a test can await them deterministically."""
    coros: list = []

    def _fake_track(coro):
        coros.append(coro)
        return None

    monkeypatch.setattr(lifecycle, "_track_task", _fake_track)
    yield coros
    for c in coros:
        if hasattr(c, "close"):
            c.close()


async def _drain(coros: list) -> None:
    pending, coros[:] = list(coros), []
    for c in pending:
        await c


# ---------------------------------------------------------------------------
# Lead-seconds env parsing / clamping
# ---------------------------------------------------------------------------

def test_wrap_up_lead_defaults_to_20_when_unset(monkeypatch):
    monkeypatch.delenv("CALL_WRAP_UP_LEAD_SECONDS", raising=False)
    assert lifecycle._wrap_up_lead_seconds() == 20


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("30", 30),
        ("5", 5),
        ("60", 60),
        ("1", 5),        # below the floor → clamped up
        ("0", 5),
        ("-10", 5),
        ("600", 60),     # above the ceiling → clamped down
        ("61", 60),
        ("banana", 20),  # unparseable → default
        ("", 20),
        ("  25  ", 25),
    ],
)
def test_wrap_up_lead_is_bounded_5_to_60(monkeypatch, raw, expected):
    monkeypatch.setenv("CALL_WRAP_UP_LEAD_SECONDS", raw)
    assert lifecycle._wrap_up_lead_seconds() == expected


# ---------------------------------------------------------------------------
# Candidate selection (pure)
# ---------------------------------------------------------------------------

def test_fires_inside_the_lead_window_of_the_soft_cap():
    vs = _cascaded_session(age_s=290)  # soft cap 300 → 10s remaining
    got = lifecycle._collect_wrap_up_candidates(
        [("call-a", vs)], max_duration_s=600, soft_cap_s=300, lead_s=20,
    )
    assert [cid for cid, _, _ in got] == ["call-a"]
    assert 0 < got[0][2] <= 20


def test_does_not_fire_at_deadline_minus_40():
    vs = _cascaded_session(age_s=260)  # soft cap 300 → 40s remaining
    got = lifecycle._collect_wrap_up_candidates(
        [("call-a", vs)], max_duration_s=600, soft_cap_s=300, lead_s=20,
    )
    assert got == []


def test_does_not_fire_for_a_session_already_closing():
    vs = _cascaded_session(age_s=290, _deal_closing=True)
    got = lifecycle._collect_wrap_up_candidates(
        [("call-a", vs)], max_duration_s=600, soft_cap_s=300, lead_s=20,
    )
    assert got == []


def test_closing_session_is_nudged_relative_to_the_hard_ceiling_instead():
    """The soft cap no longer applies to a closing call — and the requirement
    is that a closing session is never nudged, so even sitting right on the
    soft cap it stays silent."""
    vs = _cascaded_session(age_s=299, _deal_closing=True)
    got = lifecycle._collect_wrap_up_candidates(
        [("call-a", vs)], max_duration_s=600, soft_cap_s=300, lead_s=20,
    )
    assert got == []


def test_soft_cap_disabled_uses_the_hard_ceiling_as_the_deadline():
    near_ceiling = _cascaded_session(age_s=590)
    mid_call = _cascaded_session(age_s=310)
    got = lifecycle._collect_wrap_up_candidates(
        [("near", near_ceiling), ("mid", mid_call)],
        max_duration_s=600, soft_cap_s=0, lead_s=20,
    )
    assert [cid for cid, _, _ in got] == ["near"]


def test_per_session_overrides_win_over_the_module_defaults():
    vs = _cascaded_session(
        age_s=110, _soft_call_cap_seconds=120, _max_call_duration_seconds=200,
    )
    got = lifecycle._collect_wrap_up_candidates(
        [("call-a", vs)], max_duration_s=600, soft_cap_s=300, lead_s=20,
    )
    assert [cid for cid, _, _ in got] == ["call-a"]


def test_already_past_the_deadline_is_left_to_the_teardown_path():
    vs = _cascaded_session(age_s=320)  # soft cap 300 → already overlong
    got = lifecycle._collect_wrap_up_candidates(
        [("call-a", vs)], max_duration_s=600, soft_cap_s=300, lead_s=20,
    )
    assert got == []


def test_a_session_without_a_call_session_is_skipped():
    vs = SimpleNamespace(call_session=None)
    got = lifecycle._collect_wrap_up_candidates(
        [("call-a", vs)], max_duration_s=600, soft_cap_s=300, lead_s=20,
    )
    assert got == []


def test_an_already_nudged_session_is_not_a_candidate_again():
    vs = _cascaded_session(age_s=290)
    vs._wrap_up_nudged = True
    got = lifecycle._collect_wrap_up_candidates(
        [("call-a", vs)], max_duration_s=600, soft_cap_s=300, lead_s=20,
    )
    assert got == []


# ---------------------------------------------------------------------------
# Dispatch — idempotency + non-blocking
# ---------------------------------------------------------------------------

async def test_dispatch_injects_a_system_message_on_the_cascaded_path(
    collected_tasks,
):
    vs = _cascaded_session(age_s=290)
    nudged = lifecycle._dispatch_wrap_up_nudges(
        [("call-a", vs)], max_duration_s=600, soft_cap_s=300, lead_s=20,
    )
    assert nudged == ["call-a"]
    await _drain(collected_tasks)

    history = vs.call_session.conversation_history
    assert len(history) == 1
    assert history[0].role.value == "system"
    assert "20 seconds" in history[0].content
    assert "goodbye" in history[0].content.lower()


async def test_dispatch_fires_exactly_once_per_session(collected_tasks):
    vs = _cascaded_session(age_s=290)
    first = lifecycle._dispatch_wrap_up_nudges(
        [("call-a", vs)], max_duration_s=600, soft_cap_s=300, lead_s=20,
    )
    second = lifecycle._dispatch_wrap_up_nudges(
        [("call-a", vs)], max_duration_s=600, soft_cap_s=300, lead_s=20,
    )
    assert first == ["call-a"]
    assert second == []
    await _drain(collected_tasks)
    assert len(vs.call_session.conversation_history) == 1


def test_dispatch_marks_the_session_before_the_task_runs(collected_tasks):
    """The idempotency flag must be set synchronously, not inside the
    fire-and-forget task — otherwise the next 30s watchdog tick could double
    fire while the first injection is still in flight."""
    vs = _cascaded_session(age_s=290)
    lifecycle._dispatch_wrap_up_nudges(
        [("call-a", vs)], max_duration_s=600, soft_cap_s=300, lead_s=20,
    )
    assert getattr(vs, "_wrap_up_nudged", False) is True
    assert vs.call_session.conversation_history == []  # not injected yet


async def test_dispatch_uses_the_realtime_send_text_channel(collected_tasks):
    vs = _realtime_session(age_s=290)
    lifecycle._dispatch_wrap_up_nudges(
        [("call-a", vs)], max_duration_s=600, soft_cap_s=300, lead_s=20,
    )
    await _drain(collected_tasks)

    assert len(vs.realtime_session.sent) == 1
    text, create_response = vs.realtime_session.sent[0]
    assert "20 seconds" in text
    # Must NOT force a response — that would talk over the live caller.
    assert create_response is False


async def test_dispatch_reads_the_env_lead_when_none_is_passed(
    monkeypatch, collected_tasks,
):
    monkeypatch.setenv("CALL_WRAP_UP_LEAD_SECONDS", "45")
    vs = _cascaded_session(age_s=270)  # 30s remaining — inside a 45s lead
    nudged = lifecycle._dispatch_wrap_up_nudges(
        [("call-a", vs)], max_duration_s=600, soft_cap_s=300,
    )
    assert nudged == ["call-a"]
    await _drain(collected_tasks)
    assert "45 seconds" in vs.call_session.conversation_history[0].content


# ---------------------------------------------------------------------------
# Failure containment
# ---------------------------------------------------------------------------

async def test_injection_failure_never_raises(caplog):
    class _Boom:
        async def send_text(self, text, *, create_response=True):
            raise RuntimeError("socket gone")

    vs = SimpleNamespace(
        call_session=_call_session(age_s=290),
        realtime_bridge=object(),
        realtime_session=_Boom(),
    )
    with caplog.at_level(logging.WARNING):
        ok = await lifecycle._inject_wrap_up_nudge("call-a", vs, 10.0, "wrap up")
    assert ok is False
    assert any("wrap_up_nudge" in r.message for r in caplog.records)


async def test_no_injection_channel_is_a_logged_no_op(caplog):
    vs = SimpleNamespace(
        call_session=SimpleNamespace(),  # no conversation_history
        realtime_bridge=None,
        realtime_session=None,
    )
    with caplog.at_level(logging.WARNING):
        ok = await lifecycle._inject_wrap_up_nudge("call-a", vs, 10.0, "wrap up")
    assert ok is False
    assert any(
        "wrap_up_nudge_no_channel" in r.message for r in caplog.records
    )


async def test_dispatch_survives_a_broken_session_object(collected_tasks):
    class _Explodes:
        @property
        def call_session(self):
            raise RuntimeError("state backend blew up")

    good = _cascaded_session(age_s=290)
    nudged = lifecycle._dispatch_wrap_up_nudges(
        [("bad", _Explodes()), ("good", good)],
        max_duration_s=600, soft_cap_s=300, lead_s=20,
    )
    assert nudged == ["good"]
    await _drain(collected_tasks)
    assert len(good.call_session.conversation_history) == 1


async def test_successful_injection_logs_call_id_and_seconds_remaining(caplog):
    vs = _cascaded_session(age_s=290)
    with caplog.at_level(logging.INFO):
        ok = await lifecycle._inject_wrap_up_nudge("call-abcdef123456", vs, 12.0, "x")
    assert ok is True
    line = next(r.getMessage() for r in caplog.records if "wrap_up_nudge" in r.message)
    assert "call-abcdef" in line
    assert "12" in line
