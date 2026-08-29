"""Graceful shutdown must prove PBX teardown before logical settlement."""
from __future__ import annotations

import asyncio

import pytest

from app import main
from app.domain.services.telephony import lifecycle


class _ShutdownState:
    def __init__(self) -> None:
        self.sessions = {"confirmed": object(), "unconfirmed": object()}

    def iter_voice_session_items(self):
        return list(self.sessions.items())


@pytest.mark.asyncio
async def test_shutdown_routes_each_session_through_confirmation_helper(monkeypatch):
    state = _ShutdownState()
    calls: list[tuple[str, bool]] = []

    async def force_end(call_id: str, *, require_confirmation: bool = False) -> bool:
        calls.append((call_id, require_confirmation))
        if call_id == "confirmed":
            # Model _on_call_ended removing local state only after PBX proof.
            state.sessions.pop(call_id)
            return True
        return False

    monkeypatch.setattr(lifecycle, "_force_end_and_hangup", force_end)

    result = await main._terminate_active_telephony_sessions_for_shutdown(
        state,
        adapter=None,
    )

    assert result == {
        "total": 2,
        "attempted": 2,
        "confirmed": 1,
        "deferred": 1,
        "deferred_call_ids": ["unconfirmed"],
    }
    assert set(calls) == {("confirmed", True), ("unconfirmed", True)}
    assert list(state.sessions) == ["unconfirmed"]


@pytest.mark.asyncio
async def test_shutdown_failure_preserves_session_for_successor(monkeypatch):
    state = _ShutdownState()

    async def force_end(call_id: str, *, require_confirmation: bool = False) -> bool:
        assert require_confirmation is True
        raise RuntimeError(f"PBX unavailable for {call_id}")

    monkeypatch.setattr(lifecycle, "_force_end_and_hangup", force_end)

    result = await main._terminate_active_telephony_sessions_for_shutdown(
        state,
        adapter=None,
    )

    assert result["confirmed"] == 0
    assert result["deferred"] == 2
    assert result["deferred_call_ids"] == ["confirmed", "unconfirmed"]
    assert set(state.sessions) == {"confirmed", "unconfirmed"}


@pytest.mark.asyncio
async def test_shutdown_uses_one_shared_window_for_many_unresponsive_calls(monkeypatch):
    state = _ShutdownState()
    state.sessions = {f"call-{index:02d}": object() for index in range(13)}
    started: list[str] = []

    async def force_end(call_id: str, *, require_confirmation: bool = False) -> bool:
        assert require_confirmation is True
        started.append(call_id)
        await asyncio.sleep(0.08)
        return False

    monkeypatch.setattr(lifecycle, "_force_end_and_hangup", force_end)
    loop = asyncio.get_running_loop()
    start = loop.time()
    result = await main._terminate_active_telephony_sessions_for_shutdown(
        state,
        adapter=None,
        deadline_s=0.2,
    )
    elapsed = loop.time() - start

    assert elapsed < 0.5
    assert len(started) == 13
    assert result["total"] == 13
    assert result["deferred"] == 13


@pytest.mark.asyncio
async def test_shutdown_includes_adapter_owned_pre_lifecycle_channels(monkeypatch):
    class EmptyState:
        def iter_voice_session_items(self):
            return []

    requested: list[str] = []

    class Adapter:
        def owned_call_ids(self):
            return {"ringing-outbound", "preanswer-inbound"}

        async def hangup_confirmed(self, call_id):
            requested.append(call_id)
            return call_id == "preanswer-inbound"

    result = await main._terminate_active_telephony_sessions_for_shutdown(
        EmptyState(),
        adapter=Adapter(),
        deadline_s=0.5,
    )

    assert set(requested) == {"ringing-outbound", "preanswer-inbound"}
    assert result["confirmed"] == 1
    assert result["deferred_call_ids"] == ["ringing-outbound"]
