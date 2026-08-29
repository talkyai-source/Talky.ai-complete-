"""Unit tests for AsteriskAdapter inbound DID/context extraction (Phase C).

Verifies the DEFENSIVE field reading of a StasisStart event — the exact ARI
field carrying the DID varies by trunk config, so extraction tries
dialplan.exten, then connected.number, then args. (The live carrier leg still
needs the one-time debug dump to confirm which field is populated.)
"""
from __future__ import annotations

import logging

import pytest

from app.infrastructure.telephony.asterisk_adapter import AsteriskAdapter


def _adapter():
    # __init__ only reads env with defaults — no network.
    return AsteriskAdapter()


def test_extract_did_and_context_from_dialplan():
    ad = _adapter()
    event = {
        "type": "StasisStart",
        "args": [],
        "channel": {
            "name": "PJSIP/blazedigitel-00000001",
            "dialplan": {"context": "from-tenant-abc", "exten": "+15551234567"},
            "caller": {"number": "+15559990000"},
            "connected": {"number": ""},
        },
    }
    meta = ad._extract_inbound_meta(event)
    assert meta["called_did"] == "+15551234567"
    assert meta["context"] == "from-tenant-abc"
    assert meta["caller_number"] == "+15559990000"


def test_extract_falls_back_to_connected_then_args():
    ad = _adapter()
    # No exten → falls back to connected.number.
    ev1 = {
        "channel": {
            "dialplan": {"context": "from-blazedigitel", "exten": ""},
            "connected": {"number": "+15551112222"},
            "caller": {"number": "+15553334444"},
        },
    }
    assert ad._extract_inbound_meta(ev1)["called_did"] == "+15551112222"

    # No exten + no connected → falls back to args[0].
    ad2 = _adapter()
    ev2 = {
        "args": ["+15557778888"],
        "channel": {
            "dialplan": {"context": "from-blazedigitel"},
            "connected": {},
            "caller": {},
        },
    }
    assert ad2._extract_inbound_meta(ev2)["called_did"] == "+15557778888"


def test_extracts_canonical_direction_did_and_context_args():
    ad = _adapter()
    event = {
        "args": ["inbound", "+15557778888", "from-opensips"],
        "channel": {
            "name": "PJSIP/carrier-a-00000001",
            "linkedid": "171234.10",
            "dialplan": {"context": "wrong-context", "exten": "999"},
            "caller": {"number": "+15553334444"},
        },
    }

    meta = ad._extract_inbound_meta(event)

    assert meta["direction"] == "inbound"
    assert meta["called_did"] == "+15557778888"
    assert meta["context"] == "from-opensips"
    assert meta["ingress_endpoint"] == "carrier-a"
    assert meta["linked_id"] == "171234.10"


def test_inbound_shape_log_masks_ani_and_did(caplog):
    ad = _adapter()
    did = "+15557778888"
    ani = "+15553334444"

    with caplog.at_level(logging.INFO):
        ad._extract_inbound_meta({
            "args": ["inbound", did, "from-opensips"],
            "channel": {
                "name": "PJSIP/carrier-a-00000001",
                "caller": {"number": ani},
            },
        })

    assert did not in caplog.text
    assert ani not in caplog.text
    assert "inbound_stasis_shape" in caplog.text


def test_extract_tolerates_missing_fields():
    ad = _adapter()
    meta = ad._extract_inbound_meta({"channel": {}})
    assert meta["called_did"] is None
    assert meta["context"] is None
    assert meta["caller_number"] is None


def test_debug_dump_is_one_time():
    ad = _adapter()
    assert ad._inbound_debug_dumped is False
    ad._extract_inbound_meta({"channel": {"dialplan": {"exten": "100"}}})
    assert ad._inbound_debug_dumped is True
    # second call must not re-arm the dump flag
    ad._extract_inbound_meta({"channel": {"dialplan": {"exten": "200"}}})
    assert ad._inbound_debug_dumped is True


@pytest.mark.asyncio
async def test_inverse_inventory_is_application_scoped_and_human_channel_only():
    ad = _adapter()
    ad._session = object()
    calls = []

    async def ari(method, path, **_kwargs):
        calls.append((method, path))
        if path.startswith("/applications/"):
            return {
                "channel_ids": [
                    "human-parent",
                    "external-media",
                    "local-helper",
                    "vanished-before-inventory",
                ]
            }
        if path == "/channels":
            return [
                {"id": "human-parent", "name": "PJSIP/carrier-a-00000001"},
                {"id": "external-media", "name": "UnicastRTP/127.0.0.1-0x1"},
                {"id": "local-helper", "name": "Local/cleanup@talky-0001;1"},
                {"id": "foreign-app", "name": "PJSIP/other-00000002"},
            ]
        raise AssertionError(path)

    ad._ari = ari

    assert await ad.list_recoverable_application_channel_ids() == {"human-parent"}
    assert calls == [
        ("GET", "/applications/talky_ai"),
        ("GET", "/channels"),
    ]


@pytest.mark.asyncio
async def test_inverse_inventory_failure_is_not_an_empty_authoritative_result():
    ad = _adapter()
    ad._session = object()

    async def ari(*_args, **_kwargs):
        raise ConnectionError("ARI unavailable")

    ad._ari = ari

    assert await ad.list_recoverable_application_channel_ids() is None


def test_inverse_inventory_excludes_every_locally_managed_physical_leg():
    ad = _adapter()
    ad._active_sessions["parent"] = {}
    ad._ext_channels["parent"] = "external"
    ad._gateway_sessions["gateway-parent"] = "gateway-session"
    ad._preemptive_up_channels.add("preemptive")
    ad._transfers_by_target["transfer-target"] = object()

    assert {
        "parent",
        "external",
        "gateway-parent",
        "preemptive",
        "transfer-target",
    } <= ad.recovery_excluded_channel_ids()
