"""Regression tests for P1-9 / P1-10 (telephony teardown reliability).

P1-9 — ``asyncio.create_task(_force_end_and_hangup(...))`` on the crashed-
pipeline (``_pipeline_done_cb``) and audio-route-failure
(``_on_audio_received``) paths was fire-and-forget with no retained
reference. asyncio only holds a *weak* reference to a bare
``create_task()`` result; with nothing else referencing it, the task object
can be garbage-collected mid-execution, silently dropping the forced
hangup/lease-release and leaking the call. The fix retains every such task
in a module-level ``_background_tasks`` set (mirroring the ``_track_task``
pattern in ``freeswitch_audio_bridge.py``) until it completes.

P1-10 — the FreeSWITCH WS session-race timeout path in
``_on_ws_session_start`` called bare ``adapter.hangup(call_id)``, bypassing
``_on_call_ended`` (and therefore ``release_lease``). ``_on_new_call``
acquires the global concurrency lease for a call_id BEFORE storing its
VoiceSession, so a session-race timeout can fire with a lease already held;
a bare hangup tears down the PBX channel but leaks that lease until its
~10-minute TTL expires, transiently denying real capacity. The fix routes
through ``_force_end_and_hangup`` (which runs ``_on_call_ended`` — releasing
the lease first — then the same best-effort hangup) instead.
"""
from __future__ import annotations

import asyncio
import gc
from types import SimpleNamespace

import pytest

from app.domain.services.telephony import lifecycle


# ---------------------------------------------------------------------------
# P1-9 — forced-hangup tasks are retained (not GC-able) until completion
# ---------------------------------------------------------------------------

class TestForcedHangupTaskRetention:
    @pytest.mark.asyncio
    async def test_track_task_adds_and_discards_from_background_set(self, monkeypatch):
        """The exact pattern that fixes the bug: a task spawned via
        _track_task must be present in the retained set while running, and
        removed once done — proving it is reachable (non-GC-able) for its
        entire lifetime instead of being a bare, unreferenced create_task()
        result."""
        release_evt = asyncio.Event()
        started_evt = asyncio.Event()

        async def slow_coro():
            started_evt.set()
            await release_evt.wait()

        task = lifecycle._track_task(slow_coro())
        try:
            await asyncio.wait_for(started_evt.wait(), timeout=1.0)
            # While in flight, the task must be retained in the module set.
            assert task in lifecycle._background_tasks

            # Simulate what a GC pass would do to a bare, unreferenced
            # create_task() result: without our set holding a strong
            # reference, del + gc.collect() would be free to collect it.
            # With retention, the task object survives collection.
            gc.collect()
            assert task in lifecycle._background_tasks
            assert not task.done()
        finally:
            release_evt.set()
            await task

        # Done-callback must discard it from the tracking set afterwards
        # (no unbounded growth across the life of the process).
        assert task not in lifecycle._background_tasks

    @pytest.mark.asyncio
    async def test_track_task_logs_but_does_not_swallow_exceptions(self, monkeypatch, caplog):
        async def boom():
            raise RuntimeError("forced hangup blew up")

        task = lifecycle._track_task(boom())
        await asyncio.sleep(0)
        with pytest.raises(RuntimeError):
            await task
        await asyncio.sleep(0)  # let the done-callback (call_soon) run
        assert task not in lifecycle._background_tasks

    @pytest.mark.asyncio
    async def test_pipeline_done_cb_retains_force_end_task(self, monkeypatch):
        """FIX P1-9 — the crashed-pipeline path must schedule
        _force_end_and_hangup through _track_task (retained set), not a bare
        asyncio.create_task()."""
        called_with: list[str] = []
        release_evt = asyncio.Event()

        async def fake_force_end_and_hangup(call_id: str) -> None:
            called_with.append(call_id)
            await release_evt.wait()

        monkeypatch.setattr(lifecycle, "_force_end_and_hangup", fake_force_end_and_hangup)
        monkeypatch.setattr(
            lifecycle, "_state", lambda: SimpleNamespace(get_voice_session=lambda _cid: None)
        )

        async def _boom():
            raise RuntimeError("stt terminally dead")

        task = asyncio.get_event_loop().create_task(_boom())
        try:
            await task
        except RuntimeError:
            pass

        before = set(lifecycle._background_tasks)
        lifecycle._pipeline_done_cb(task, "crashed-call")
        await asyncio.sleep(0)  # let the scheduled task start running

        new_tasks = lifecycle._background_tasks - before
        assert len(new_tasks) == 1, "expected exactly one new tracked task"
        assert called_with == ["crashed-call"]

        release_evt.set()
        await asyncio.gather(*new_tasks)
        assert new_tasks.isdisjoint(lifecycle._background_tasks)

    @pytest.mark.asyncio
    async def test_on_audio_received_retains_force_end_task_on_failure_threshold(self, monkeypatch):
        """FIX P1-9 — the audio-route consecutive-failure path must also go
        through _track_task."""
        call_id = "audio-fail-call"

        async def failing_gateway(*_a, **_k):
            raise RuntimeError("route failed")

        voice_session = SimpleNamespace(
            call_id=call_id,
            media_gateway=SimpleNamespace(on_audio_received=failing_gateway),
        )
        monkeypatch.setattr(
            lifecycle,
            "_state",
            lambda: SimpleNamespace(
                get_voice_session=lambda _cid: voice_session,
                touch_call=lambda _cid: None,
            ),
        )
        monkeypatch.setattr(lifecycle, "_AUDIO_ROUTE_FORCE_END_THRESHOLD", 1, raising=False)
        monkeypatch.setattr(lifecycle, "_AUDIO_ROUTE_LOG_INTERVAL_S", 0, raising=False)

        release_evt = asyncio.Event()
        called_with: list[str] = []

        async def fake_force_end_and_hangup(cid: str) -> None:
            called_with.append(cid)
            await release_evt.wait()

        monkeypatch.setattr(lifecycle, "_force_end_and_hangup", fake_force_end_and_hangup)

        before = set(lifecycle._background_tasks)
        await lifecycle._on_audio_received(call_id, b"\x00\x00")
        await asyncio.sleep(0)  # let the scheduled _track_task task start running

        new_tasks = lifecycle._background_tasks - before
        assert len(new_tasks) == 1
        assert called_with == [call_id]

        release_evt.set()
        await asyncio.gather(*new_tasks)


# ---------------------------------------------------------------------------
# P1-10 — WS session-race timeout releases the lease instead of a bare hangup
# ---------------------------------------------------------------------------

class TestWsSessionRaceReleasesLease:
    @pytest.mark.asyncio
    async def test_ws_race_timeout_routes_through_force_end_and_hangup(self, monkeypatch):
        """FIX P1-10 — when the FreeSWITCH WS session-race poll times out
        with no VoiceSession found, the call must go through
        _force_end_and_hangup (which releases the global concurrency lease
        via _on_call_ended) instead of a bare adapter.hangup()."""
        call_id = "ws-race-call"

        # No VoiceSession ever appears -> forces the timeout branch.
        monkeypatch.setattr(
            lifecycle, "_state", lambda: SimpleNamespace(get_voice_session=lambda _cid: None)
        )
        # Make the 40 x 50ms poll loop instant for the test. Capture the
        # *original* sleep first — lifecycle.asyncio is the real asyncio
        # module (shared with this test file's own `asyncio` import), so
        # patching its `sleep` attribute and then calling `asyncio.sleep(0)`
        # from inside the replacement would recurse into itself.
        _orig_sleep = asyncio.sleep
        monkeypatch.setattr(lifecycle.asyncio, "sleep", lambda _s: _orig_sleep(0))

        force_end_calls: list[str] = []

        async def fake_force_end_and_hangup(cid: str) -> None:
            force_end_calls.append(cid)

        bare_hangup_calls: list[str] = []
        fake_adapter = SimpleNamespace(
            hangup=lambda cid: bare_hangup_calls.append(cid)
        )
        monkeypatch.setattr(lifecycle, "_bridge", lambda: SimpleNamespace(_adapter=fake_adapter))
        monkeypatch.setattr(lifecycle, "_force_end_and_hangup", fake_force_end_and_hangup)

        await lifecycle._on_ws_session_start(call_id)

        assert force_end_calls == [call_id], (
            "WS-race timeout must release the lease via _force_end_and_hangup"
        )
        assert bare_hangup_calls == [], (
            "must not bypass lease release with a bare adapter.hangup() call"
        )

    @pytest.mark.asyncio
    async def test_force_end_and_hangup_releases_lease_when_no_session_exists(self, monkeypatch):
        """End-to-end proof that routing the WS-race path through
        _force_end_and_hangup actually reaches release_lease, even though no
        VoiceSession was ever stored (pop_voice_session -> None)."""
        call_id = "lease-leak-call"

        release_calls: list[str] = []

        async def fake_release_lease(_redis, *, call_id):
            release_calls.append(call_id)

        # Patch the module release_lease is imported from, since
        # _on_call_ended does a local `from ... import release_lease`.
        import app.domain.services.global_concurrency as global_concurrency
        monkeypatch.setattr(global_concurrency, "release_lease", fake_release_lease)

        fake_container = SimpleNamespace(is_initialized=False, redis=None, db_pool=None)
        monkeypatch.setattr(
            "app.core.container.get_container", lambda: fake_container
        )

        monkeypatch.setattr(
            lifecycle,
            "_state",
            lambda: SimpleNamespace(
                clear_first_speaker=lambda _cid: None,
                pop_voice_session=lambda _cid: None,
                remove_gateway_sessions_for_call=lambda _cid: None,
            ),
        )
        monkeypatch.setattr(lifecycle, "_pop_ringing_warmup", lambda _cid: None)
        monkeypatch.setattr(lifecycle, "_bridge", lambda: SimpleNamespace(_adapter=None))

        await lifecycle._force_end_and_hangup(call_id)

        assert release_calls == [call_id]
