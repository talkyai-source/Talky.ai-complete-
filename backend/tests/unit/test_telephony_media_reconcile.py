"""Media-path reconcile: an answered call whose C++ gateway session is gone.

WHY THIS EXISTS: the lifecycle watchdog already reconciles local voice sessions
against Asterisk's live channel list (``_detect_zombie_sessions``), which catches
"local session, no channel". It does NOT catch the mirror failure that actually
produces silent dead air: the SIP channel is still up and billing, but the C++
media gateway's RTP session for it is gone (gateway restart, crash, reaped
session, a start that silently failed). Asterisk still reports the channel as
live, so every existing sweep votes "healthy" while the caller hears nothing.

These tests pin the two pieces of that reconcile:
  * ``AsteriskAdapter.list_active_gateway_session_ids`` — media ground truth,
    with the same None-means-couldn't-check contract as the ARI channel list.
  * ``_detect_dead_media_sessions`` — the debounced diff, mirroring
    ``_detect_zombie_sessions`` so a transient miss can never hang up a live call.
"""

import asyncio

import pytest

from app.domain.services.telephony import lifecycle
from app.infrastructure.telephony.asterisk_adapter import AsteriskAdapter


# --------------------------------------------------------------------------
# Ground truth: what the gateway says it is actually running
# --------------------------------------------------------------------------


def test_list_active_gateway_session_ids_returns_ids_the_gateway_reports():
    adapter = AsteriskAdapter()
    calls: list = []

    async def fake_gateway(method, path, payload=None, ok=(200,)):
        calls.append((method, path))
        return {
            "sessions": [
                {"session_id": "sess-a", "state": "Active"},
                {"session_id": "sess-b", "state": "Buffering"},
            ]
        }

    adapter._gateway = fake_gateway  # type: ignore[assignment]
    adapter._session = object()  # only needs to be non-None

    result = asyncio.run(adapter.list_active_gateway_session_ids())

    assert result == {"sess-a", "sess-b"}
    assert calls == [("GET", "/v1/sessions")]


def test_list_active_gateway_session_ids_excludes_failed_and_stopped_evidence_rows():
    adapter = AsteriskAdapter()

    async def fake_gateway(method, path, payload=None, ok=(200,)):
        return {
            "sessions": [
                {"session_id": "sess-live", "state": "Active"},
                {"session_id": "sess-dead", "state": "Failed"},
                {"session_id": "sess-ended", "state": "Stopped"},
            ]
        }

    adapter._gateway = fake_gateway  # type: ignore[assignment]
    adapter._session = object()

    assert asyncio.run(adapter.list_active_gateway_session_ids()) == {"sess-live"}


def test_list_active_gateway_session_ids_returns_none_when_gateway_unreachable():
    """None (not empty set) — so the caller skips instead of mass-hanging-up.

    A gateway that cannot be queried is indistinguishable from a gateway with
    no sessions, and guessing wrong tears down every live call.
    """
    adapter = AsteriskAdapter()

    async def boom(method, path, payload=None, ok=(200,)):
        raise RuntimeError("connection refused")

    adapter._gateway = boom  # type: ignore[assignment]
    adapter._session = object()

    assert asyncio.run(adapter.list_active_gateway_session_ids()) is None


def test_list_active_gateway_session_ids_returns_none_when_not_connected():
    adapter = AsteriskAdapter()
    adapter._session = None

    assert asyncio.run(adapter.list_active_gateway_session_ids()) is None


def test_gateway_session_map_is_a_copy_not_the_live_dict():
    """The watchdog must not be able to mutate the adapter's routing state."""
    adapter = AsteriskAdapter()
    adapter._gateway_sessions["chan-1"] = "sess-1"

    snapshot = adapter.gateway_session_map()
    snapshot["chan-1"] = "tampered"
    snapshot["chan-2"] = "injected"

    assert adapter._gateway_sessions == {"chan-1": "sess-1"}


# --------------------------------------------------------------------------
# The debounced diff
# --------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_tick_state():
    lifecycle._dead_media_ticks.clear()
    yield
    lifecycle._dead_media_ticks.clear()


def test_dead_media_session_is_reported_only_after_the_tick_threshold():
    session_map = {"chan-1": "sess-1"}

    first = lifecycle._detect_dead_media_sessions(session_map, set(), threshold=2)
    assert first == [], "one missed tick must not tear down a call"

    second = lifecycle._detect_dead_media_sessions(session_map, set(), threshold=2)
    assert second == ["chan-1"]


def test_live_gateway_session_resets_the_counter():
    session_map = {"chan-1": "sess-1"}

    lifecycle._detect_dead_media_sessions(session_map, set(), threshold=2)
    lifecycle._detect_dead_media_sessions(session_map, {"sess-1"}, threshold=2)
    again = lifecycle._detect_dead_media_sessions(session_map, set(), threshold=2)

    assert again == [], "a session seen alive must restart the debounce"


def test_none_gateway_ids_is_a_noop_and_advances_no_counter():
    session_map = {"chan-1": "sess-1"}

    assert lifecycle._detect_dead_media_sessions(session_map, None, threshold=2) == []
    assert lifecycle._dead_media_ticks == {}
    # Two real misses are still required afterwards.
    assert lifecycle._detect_dead_media_sessions(session_map, set(), threshold=2) == []
    assert lifecycle._detect_dead_media_sessions(session_map, set(), threshold=2) == ["chan-1"]


def test_counters_are_forgotten_for_channels_that_ended_normally():
    lifecycle._detect_dead_media_sessions({"chan-1": "sess-1"}, set(), threshold=2)
    assert "chan-1" in lifecycle._dead_media_ticks

    lifecycle._detect_dead_media_sessions({}, set(), threshold=2)

    assert lifecycle._dead_media_ticks == {}


def test_channel_with_no_gateway_session_id_is_never_reported():
    """A blank session id means media was never started for that channel —
    that is the warmup path's business, not the media reconcile's."""
    session_map = {"chan-1": ""}

    lifecycle._detect_dead_media_sessions(session_map, set(), threshold=2)
    result = lifecycle._detect_dead_media_sessions(session_map, set(), threshold=2)

    assert result == []


# --------------------------------------------------------------------------
# The wiring. A guard hooked to a signal that never varies is this repo's
# recurring trap, so the sweep seam is tested, not just the pure helper.
# --------------------------------------------------------------------------


class _FakeAdapter:
    name = "asterisk"

    def __init__(self, session_map, live_ids):
        self._session_map = session_map
        self._live_ids = live_ids
        self.list_calls = 0

    def gateway_session_map(self):
        return dict(self._session_map)

    async def list_active_gateway_session_ids(self):
        self.list_calls += 1
        return self._live_ids


def _run_reconcile(adapter, ended):
    async def _force_end(cid):
        ended.append(cid)

    asyncio.run(lifecycle._reconcile_dead_media(adapter, _force_end))


def test_reconcile_force_ends_a_call_whose_media_session_vanished():
    adapter = _FakeAdapter({"chan-1": "sess-1"}, set())
    ended: list = []

    _run_reconcile(adapter, ended)
    assert ended == [], "first miss is debounced"

    _run_reconcile(adapter, ended)
    assert ended == ["chan-1"]


def test_reconcile_leaves_a_call_alone_while_its_media_session_is_alive():
    adapter = _FakeAdapter({"chan-1": "sess-1"}, {"sess-1"})
    ended: list = []

    for _ in range(4):
        _run_reconcile(adapter, ended)

    assert ended == []
    assert adapter.list_calls == 4, "the guard must actually query the gateway"


def test_reconcile_is_a_noop_when_the_gateway_cannot_be_queried():
    adapter = _FakeAdapter({"chan-1": "sess-1"}, None)
    ended: list = []

    for _ in range(4):
        _run_reconcile(adapter, ended)

    assert ended == []


def test_reconcile_skips_non_asterisk_adapters():
    adapter = _FakeAdapter({"chan-1": "sess-1"}, set())
    adapter.name = "vonage"
    ended: list = []

    _run_reconcile(adapter, ended)
    _run_reconcile(adapter, ended)

    assert ended == []
    assert adapter.list_calls == 0


def test_reconcile_skips_when_there_is_no_adapter():
    ended: list = []
    _run_reconcile(None, ended)
    assert ended == []


def test_reconcile_survives_a_force_end_that_raises():
    """One call that fails to tear down must not abort the sweep for the rest."""
    adapter = _FakeAdapter({"chan-1": "sess-1", "chan-2": "sess-2"}, set())
    ended: list = []

    async def _force_end(cid):
        if cid == "chan-1":
            raise RuntimeError("hangup failed")
        ended.append(cid)

    asyncio.run(lifecycle._reconcile_dead_media(adapter, _force_end))
    asyncio.run(lifecycle._reconcile_dead_media(adapter, _force_end))

    assert ended == ["chan-2"]
