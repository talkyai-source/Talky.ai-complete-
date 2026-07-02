"""Unit tests for AsteriskAdapter inbound DID/context extraction (Phase C).

Verifies the DEFENSIVE field reading of a StasisStart event — the exact ARI
field carrying the DID varies by trunk config, so extraction tries
dialplan.exten, then connected.number, then args. (The live carrier leg still
needs the one-time debug dump to confirm which field is populated.)
"""
from __future__ import annotations

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
