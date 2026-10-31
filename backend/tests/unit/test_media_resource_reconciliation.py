"""Internal PBX cleanup requires ownership and absence evidence, not age alone."""
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from app.infrastructure.telephony.media_reconciliation import discover_orphan_media, reconcile_orphan_media

PARENT = "talky-out-11111111-1111-4111-8111-111111111111"
OLD = "2026-07-03T14:56:35.720+0000"
NOW = datetime(2026, 9, 6, tzinfo=timezone.utc)


class PBX:
    def __init__(self):
        self.channels = [{"id": "media", "name": "UnicastRTP/127.0.0.1:32000", "creationtime": OLD}]
        self.bridges = [{"id": "legacy-bridge", "bridge_type": "mixing", "bridge_class": "stasis",
                         "channels": ["media"], "creationtime": OLD}]
        self.parent = PARENT
        self.deleted = []

    async def ari(self, method, path, **kwargs):
        if method == "DELETE":
            self.deleted.append(path)
            identifier = path.rsplit("/", 1)[1]
            self.channels = [c for c in self.channels if c["id"] != identifier]
            self.bridges = [b for b in self.bridges if b["id"] != identifier]
            for b in self.bridges:
                b["channels"] = [c for c in b["channels"] if c != identifier]
            return {}
        if path == "/applications/talky_ai":
            return {"channel_ids": [c["id"] for c in self.channels], "bridge_ids": [b["id"] for b in self.bridges]}
        if path == "/channels":
            return self.channels
        if path == "/bridges":
            return self.bridges
        if path.endswith("/variable"):
            return {"value": self.parent}
        for resource in self.channels + self.bridges:
            if path.endswith("/" + resource["id"]):
                return resource
        return {}  # successful 404 is represented by the ARI adapter as empty


@pytest.mark.asyncio
async def test_legacy_linkedid_proves_media_ownership_and_only_absent_parent_is_candidate():
    pbx = PBX()
    plan = await discover_orphan_media(pbx.ari, "talky_ai", excluded=set(), now=NOW)
    assert len(plan) == 1 and plan[0]["media_ids"] == ["media"]
    pbx.channels.append({"id": PARENT, "name": "PJSIP/carrier", "creationtime": OLD})
    assert await discover_orphan_media(pbx.ari, "talky_ai", excluded=set(), now=NOW) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["foreign_parent", "human_member", "inflight", "young"])
async def test_uncertain_or_active_resources_are_never_candidates(reason):
    pbx = PBX()
    excluded = set()
    if reason == "foreign_parent":
        pbx.parent = "another-app-call"
    elif reason == "human_member":
        pbx.channels[0]["name"] = "PJSIP/human"
    elif reason == "inflight":
        excluded.add(PARENT)
    else:
        pbx.bridges[0]["creationtime"] = NOW.isoformat()
    assert await discover_orphan_media(pbx.ari, "talky_ai", excluded=excluded, now=NOW) == []


@pytest.mark.asyncio
async def test_reconcile_checks_again_then_confirms_exact_resources_removed():
    pbx = PBX()
    result = await reconcile_orphan_media(pbx.ari, "talky_ai", owner=lambda: True, exclusions=lambda: set(), now=NOW)
    assert result == 1
    assert pbx.deleted == ["/bridges/legacy-bridge", "/channels/media"]


@pytest.mark.asyncio
async def test_interrupted_reconciliation_leaves_discoverable_media_not_unowned_empty_bridge():
    pbx = PBX()
    original = pbx.ari

    async def ari(method, path, **kwargs):
        if method == "DELETE" and len(pbx.deleted) == 1:
            raise TimeoutError("maintenance interrupted")
        return await original(method, path, **kwargs)

    with pytest.raises(TimeoutError):
        await reconcile_orphan_media(ari, "talky_ai", owner=lambda: True, exclusions=lambda: set(), now=NOW)
    remaining = await discover_orphan_media(pbx.ari, "talky_ai", excluded=set(), now=NOW)
    assert len(remaining) == 1 and remaining[0]["media_ids"] == ["media"]


@pytest.mark.asyncio
async def test_nonowner_or_inventory_failure_never_deletes():
    pbx = PBX()
    assert await reconcile_orphan_media(pbx.ari, "talky_ai", owner=lambda: False, exclusions=lambda: set(), now=NOW) == 0
    assert not pbx.deleted
    ari = AsyncMock(side_effect=TimeoutError("ARI unavailable"))
    with pytest.raises(TimeoutError):
        await reconcile_orphan_media(ari, "talky_ai", owner=lambda: True, exclusions=lambda: set(), now=NOW)
    assert all(call.args[0] == "GET" for call in ari.await_args_list)


@pytest.mark.asyncio
async def test_parent_appearing_between_inventories_prevents_delete():
    pbx = PBX()
    original = pbx.ari
    reads = 0

    async def ari(method, path, **kwargs):
        nonlocal reads
        if path == "/channels":
            reads += 1
            if reads == 2:
                pbx.channels.append({"id": PARENT, "name": "PJSIP/live", "creationtime": OLD})
        return await original(method, path, **kwargs)

    assert await reconcile_orphan_media(ari, "talky_ai", owner=lambda: True, exclusions=lambda: set(), now=NOW) == 0
    assert not pbx.deleted


@pytest.mark.asyncio
async def test_delete_200_without_inventory_absence_is_not_completion():
    pbx = PBX()

    async def ari(method, path, **kwargs):
        return {} if method == "DELETE" else await pbx.ari(method, path, **kwargs)

    with pytest.raises(RuntimeError, match="unconfirmed"):
        await reconcile_orphan_media(ari, "talky_ai", owner=lambda: True, exclusions=lambda: set(), now=NOW)


@pytest.mark.asyncio
async def test_foreign_bridge_reference_prevents_media_deletion():
    pbx = PBX()
    pbx.bridges.append({"id": "foreign", "channels": ["media", "human"], "creationtime": OLD})
    assert await discover_orphan_media(pbx.ari, "talky_ai", excluded=set(), now=NOW) == []


@pytest.mark.asyncio
async def test_empty_legacy_talky_bridge_needs_quiescence_and_unmarked_bridge_is_kept():
    pbx = PBX()
    pbx.channels = []
    pbx.bridges[0]["channels"] = []
    assert await discover_orphan_media(pbx.ari, "talky_ai", excluded=set(), now=NOW) == []
    pbx.bridges[0]["id"] = "talky-inbound-bridge-known"
    assert len(await discover_orphan_media(pbx.ari, "talky_ai", excluded=set(), now=NOW)) == 1
    pbx.channels.append({"id": "some-other-call", "name": "PJSIP/live", "creationtime": OLD})
    assert await discover_orphan_media(pbx.ari, "talky_ai", excluded=set(), now=NOW) == []


@pytest.mark.asyncio
async def test_watchdog_wiring_invokes_internal_media_reconciliation(monkeypatch):
    from types import SimpleNamespace
    from app.domain.services.telephony import lifecycle
    reconcile = AsyncMock()
    adapter = SimpleNamespace(name="asterisk", reconcile_orphaned_media=reconcile)
    state = SimpleNamespace(is_telephony_owner=lambda: True, recover_orphans=AsyncMock(return_value=[]))
    monkeypatch.setattr(lifecycle, "_state", lambda: state)
    monkeypatch.setattr(lifecycle, "get_adapter", lambda: adapter)
    monkeypatch.setattr(lifecycle, "_register_unknown_asterisk_cleanup_candidates", AsyncMock(return_value=0))
    monkeypatch.setattr(lifecycle, "_load_termination_pending_candidates", AsyncMock(return_value=[]))
    await lifecycle.recover_orphaned_calls()
    reconcile.assert_awaited_once()
    assert reconcile.await_args.kwargs["owner_check"] is state.is_telephony_owner


@pytest.mark.asyncio
async def test_adapter_reconciliation_is_bounded_throttled_and_preserves_local_resources(monkeypatch):
    from types import SimpleNamespace
    from app.infrastructure.telephony import media_reconciliation
    from app.infrastructure.telephony import asterisk_adapter

    adapter = SimpleNamespace(
        _session=object(), _ari=AsyncMock(), _app_name="talky_ai",
        _bridges={"live-root": "live-bridge"},
        _pending_outbound={"ringing": {"bridge_id": "ringing-bridge"}},
        _outbound_answer_setup_resources={"answering": {"ext_channel_id": "starting-media"}},
    )
    observed = []

    async def reconcile(*args, **kwargs):
        observed.append(kwargs["exclusions"]())
        return 1

    async def bounded(awaitable, *, timeout):
        assert timeout == 2.0
        return await awaitable

    monkeypatch.setattr(media_reconciliation, "reconcile_orphan_media", reconcile)
    monkeypatch.setattr(asterisk_adapter.asyncio, "wait_for", bounded)
    monkeypatch.setattr(asterisk_adapter.time, "monotonic", lambda: 1000.0)
    method = asterisk_adapter.AsteriskAdapter.reconcile_orphaned_media
    kwargs = {"owner_check": lambda: True, "exclusions": lambda: {"admitting-root"}}
    assert await method(adapter, **kwargs) == 1
    assert await method(adapter, **kwargs) == 0
    assert observed == [{"live-bridge", "ringing-bridge", "starting-media", "admitting-root"}]


@pytest.mark.asyncio
async def test_new_tagged_inbound_media_can_be_recovered_without_an_outbound_linkedid():
    from app.infrastructure.telephony.media_reconciliation import media_bridge_name
    pbx = PBX()
    pbx.channels[0]["id"] = "talky-inbound-media-new"
    pbx.bridges[0].update(id="talky-inbound-bridge-new", channels=["talky-inbound-media-new"],
                          name=media_bridge_name("talky_ai", "carrier-inbound-parent"))
    original = pbx.ari

    async def ari(method, path, **kwargs):
        if path.endswith("/variable"):
            return {"value": {"TALKY_MEDIA_OWNER": "talky_ai", "TALKY_MEDIA_PARENT": "carrier-inbound-parent"}.get(kwargs["params"]["variable"], "")}
        return await original(method, path, **kwargs)

    plan = await discover_orphan_media(ari, "talky_ai", excluded=set(), now=NOW)
    assert len(plan) == 1 and plan[0]["parents"] == ["carrier-inbound-parent"]
    assert await discover_orphan_media(ari, "talky_ai", excluded={"carrier-inbound-parent"}, now=NOW) == []
