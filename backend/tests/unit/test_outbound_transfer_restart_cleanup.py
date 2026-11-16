"""A transfer leg left behind by a restart is found by the existing recovery.

An outbound answer-checked transfer keeps no database row for its target leg.
These tests pin the two facts that make that safe:

* while this process owns the transfer, the ringing target is on the list of
  channels the recovery scan must not touch;
* a process that starts with no memory of it (after a crash or a restart)
  sees the same channel as an unknown human leg, which the inverse recovery in
  ``lifecycle`` then registers for termination.
"""

import asyncio

import pytest

from app.infrastructure.telephony.asterisk_adapter import AsteriskAdapter
from tests.unit.test_asterisk_outbound_supervised_transfer import (
    DEST,
    LEG_ID,
    SWITCH,
    TRUNK,
    _Rig,
    _live_outbound_adapter,
    _register_callbacks,
)


def _inventory(target_id, parent):
    async def ari(method, path, **_kwargs):
        if path.startswith("/applications/"):
            return {"channel_ids": [parent, target_id, "external-media"]}
        if path == "/channels":
            return [
                {"id": parent, "name": "PJSIP/carrier-uk-00000001"},
                {"id": target_id, "name": f"PJSIP/{TRUNK}-00000002"},
                {"id": "external-media", "name": "UnicastRTP/127.0.0.1-0x1"},
            ]
        raise AssertionError(path)

    return ari


@pytest.mark.asyncio
async def test_ringing_target_is_protected_while_this_process_owns_it(monkeypatch):
    monkeypatch.setenv(SWITCH, "true")
    adapter, parent = _live_outbound_adapter()
    rig = _Rig(adapter, parent)
    _register_callbacks(adapter)

    task = await rig.start(trunk_endpoint=TRUNK, ring_seconds=12)
    target_id = adapter._transfers_by_parent[parent].target_id
    assert LEG_ID.fullmatch(target_id) and not task.done()

    excluded = adapter.recovery_excluded_channel_ids()
    assert target_id in excluded and parent in excluded

    await rig.busy(task)


@pytest.mark.asyncio
async def test_target_left_by_a_restart_is_an_unknown_leg_for_recovery(monkeypatch):
    monkeypatch.setenv(SWITCH, "true")
    old, parent = _live_outbound_adapter()
    rig = _Rig(old, parent)
    _register_callbacks(old)
    task = await rig.start(trunk_endpoint=TRUNK, ring_seconds=12)
    target_id = old._transfers_by_parent[parent].target_id
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    # The successor process starts with empty maps.
    fresh = AsteriskAdapter()
    fresh._session = object()
    fresh._ari = _inventory(target_id, parent)

    recoverable = await fresh.list_recoverable_application_channel_ids()
    assert recoverable == {parent, target_id}
    assert not (recoverable & fresh.recovery_excluded_channel_ids())
    assert DEST not in target_id
