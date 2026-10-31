"""Conservative reclamation of abandoned ARI media, never billable call roots."""
import json
import logging
import re
from datetime import datetime, timezone
from urllib.parse import quote

logger = logging.getLogger(__name__)
_OUTBOUND_PARENT = re.compile(r"talky-out-[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")
_MEDIA_PREFIXES = ("talky-inbound-media-", "talky-outbound-media-")
_BRIDGE_PREFIXES = ("talky-inbound-bridge-", "talky-outbound-bridge-")
_TAG = "talky-media-v1:"


def media_bridge_name(app: str, parent: str) -> str:
    return _TAG + json.dumps({"app": app, "parent": parent}, separators=(",", ":"))


def _old(resource, now):
    try:
        created = datetime.fromisoformat(resource["creationtime"])
        return created.tzinfo is not None and (now - created).total_seconds() >= 120
    except (KeyError, TypeError, ValueError):
        return False


async def _inventory(ari, app):
    membership = await ari("GET", f"/applications/{quote(app, safe='')}")
    channels = await ari("GET", "/channels")
    bridges = await ari("GET", "/bridges")
    if not isinstance(membership, dict) or not all(isinstance(membership.get(k), list) for k in ("channel_ids", "bridge_ids")):
        raise ValueError("ambiguous ARI application inventory")
    if not all(isinstance(items, list) and all(isinstance(r, dict) and isinstance(r.get("id"), str) and r["id"] for r in items)
               for items in (channels, bridges)):
        raise ValueError("ambiguous ARI resource inventory")
    return membership, {c["id"]: c for c in channels}, {b["id"]: b for b in bridges}


async def _parent(ari, channel, app):
    async def variable(name):
        result = await ari("GET", f"/channels/{quote(channel['id'], safe='')}/variable", params={"variable": name}, ok=(200, 404))
        return result.get("value") if isinstance(result, dict) else None

    if channel["id"].startswith(_MEDIA_PREFIXES):
        if await variable("TALKY_MEDIA_OWNER") == app:
            parent = await variable("TALKY_MEDIA_PARENT")
            if isinstance(parent, str) and parent and parent != channel["id"]:
                return parent
    # Legacy channels carry the bridged root's Asterisk linkedid even though
    # their own IDs were server-generated. Only our exact outbound-ID grammar
    # counts as ownership; another application's linkedid is never reclaimed.
    linked = await variable("CHANNEL(linkedid)")
    return linked if isinstance(linked, str) and _OUTBOUND_PARENT.fullmatch(linked) else None


async def discover_orphan_media(ari, app, *, excluded, now=None):
    now = now or datetime.now(timezone.utc)
    membership, channels, bridges = await _inventory(ari, app)
    own_channels, own_bridges = set(membership["channel_ids"]), set(membership["bridge_ids"])
    media_parents = {}
    for cid in sorted(own_channels & channels.keys()):
        channel = channels[cid]
        if cid not in excluded and channel.get("name", "").startswith("UnicastRTP/") and _old(channel, now):
            parent = await _parent(ari, channel, app)
            if parent and parent not in channels and parent not in excluded:
                media_parents[cid] = parent
    plan = []
    bridged_ids = {cid for b in bridges.values() for cid in b.get("channels", [])}
    for bid in sorted(own_bridges & bridges.keys()):
        b = bridges[bid]
        if bid in excluded or not _old(b, now) or b.get("bridge_type") != "mixing" or b.get("bridge_class") != "stasis":
            continue
        members = b.get("channels")
        if not isinstance(members, list) or len(set(members)) != len(members):
            continue
        if members:
            if not all(cid in media_parents for cid in members):
                continue
            if any(other_id != bid and set(members) & set(other.get("channels", [])) for other_id, other in bridges.items()):
                continue
            parents = sorted({media_parents[cid] for cid in members})
        else:
            if not bid.startswith(_BRIDGE_PREFIXES):
                continue
            try:
                tag = json.loads(str(b.get("name", ""))[len(_TAG):]) if str(b.get("name", "")).startswith(_TAG) else {}
            except (ValueError, TypeError):
                continue
            parent = tag.get("parent") if isinstance(tag, dict) and tag.get("app") == app else None
            if parent:
                if parent in channels or parent in excluded:
                    continue
                parents = [parent]
            else:
                # Old empty Talky-prefixed bridges have no parent tag. Only a
                # quiescent PBX with no local initialization may reclaim them.
                if excluded or any(not c.get("name", "").startswith("UnicastRTP/") for c in channels.values()):
                    continue
                parents = []
        plan.append({"bridge_id": bid, "bridge_created": b["creationtime"], "media_ids": sorted(members),
                     "media_created": {cid: channels[cid]["creationtime"] for cid in members}, "parents": parents})
    for cid in sorted(media_parents.keys() - bridged_ids):
        plan.append({"bridge_id": None, "bridge_created": None, "media_ids": [cid],
                     "media_created": {cid: channels[cid]["creationtime"]}, "parents": [media_parents[cid]]})
    return plan


async def reconcile_orphan_media(ari, app, *, owner, exclusions, now=None, expected_plan=None):
    """Recheck ownership and inventory per group; confirm disappearance after DELETE.

    `owner` is the caller's live ownership check, not a cached startup boolean.
    An operator may pin an independently inspected plan with expected_plan.
    """
    if not owner():
        return 0
    initial = await discover_orphan_media(ari, app, excluded=exclusions(), now=now)
    if expected_plan is not None and initial != expected_plan:
        raise ValueError("orphan-media plan changed since review")
    recovered = 0
    for group in initial[:4]:
        fresh = await discover_orphan_media(ari, app, excluded=exclusions(), now=now)
        if not owner() or group not in fresh:
            continue
        bid = group["bridge_id"]
        if bid:
            # Remove the bridge first. If cleanup is interrupted afterwards,
            # every remaining channel still carries its linkedid/owner tags.
            # Media-first would erase the sole ownership evidence for an old
            # unmarked UUID bridge and strand an undiscoverable empty bridge.
            membership, channels, bridges = await _inventory(ari, app)
            b = bridges.get(bid, {})
            if (not owner() or set(group["parents"] + group["media_ids"] + [bid]) & exclusions()
                    or bid not in membership["bridge_ids"]
                    or sorted(b.get("channels", [])) != group["media_ids"]
                    or b.get("creationtime") != group["bridge_created"]
                    or any(parent in channels for parent in group["parents"])):
                raise RuntimeError("bridge ownership changed before deletion")
            await ari("DELETE", f"/bridges/{quote(bid, safe='')}", ok=(200, 204, 404))
            _, _, remaining = await _inventory(ari, app)
            if bid in remaining:
                raise RuntimeError("media-bridge deletion is unconfirmed")
        for cid in group["media_ids"]:
            membership, channels, bridges = await _inventory(ari, app)
            current = channels.get(cid, {})
            bid = group["bridge_id"]
            references = [b for b in bridges.values() if cid in b.get("channels", [])]
            if (not owner() or set(group["parents"] + [cid, bid]) & exclusions()
                    or any(parent in channels for parent in group["parents"])
                    or any(b["id"] != bid or not set(b.get("channels", [])) <= set(group["media_ids"]) for b in references)
                    or cid not in membership["channel_ids"]
                    or current.get("creationtime") != group["media_created"][cid]
                    or not current.get("name", "").startswith("UnicastRTP/")
                    or await _parent(ari, current, app) not in group["parents"]):
                raise RuntimeError("media ownership changed before deletion")
            await ari("DELETE", f"/channels/{quote(cid, safe='')}", ok=(200, 204, 404))
        _, channels, _ = await _inventory(ari, app)
        if any(cid in channels for cid in group["media_ids"]):
            raise RuntimeError("media-channel deletion is unconfirmed")
        recovered += 1
        logger.warning("orphan_media_reconciled bridge=%s media=%s parents=%s", bid, group["media_ids"], group["parents"])
    return recovered
