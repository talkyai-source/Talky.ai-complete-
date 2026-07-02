"""Unit tests for the pure per-tenant INBOUND routing decision (Phase C).

Offline — exercises the decision core + helpers (context parse, DID
normalisation) with no DB / ARI. Isolation is the point: context→tenant,
DID→tenant, agreement→route, disagreement→reject, unknown DID gated by the
strict flag.
"""
from __future__ import annotations

import pytest

from app.domain.services.telephony.inbound_router import (
    decide_inbound_route,
    normalize_did,
    parse_tenant_from_context,
)

TENANT_A = "11111111-1111-1111-1111-111111111111"
TENANT_B = "22222222-2222-2222-2222-222222222222"
CAMP = "33333333-3333-3333-3333-333333333333"


def _decide(**over):
    base = dict(
        context_tenant_id=None,
        did_tenant_id=None,
        campaign_id=None,
        strict=False,
    )
    base.update(over)
    return decide_inbound_route(**base)


# --- context parsing ---------------------------------------------------

def test_parse_tenant_from_valid_context():
    assert parse_tenant_from_context(f"from-tenant-{TENANT_A}") == TENANT_A


def test_parse_tenant_case_insensitive_prefix():
    assert parse_tenant_from_context(f"From-Tenant-{TENANT_A}") == TENANT_A


def test_parse_tenant_rejects_non_uuid_suffix():
    assert parse_tenant_from_context("from-tenant-not-a-uuid") is None


def test_parse_tenant_ignores_other_contexts():
    assert parse_tenant_from_context("from-blazedigitel") is None
    assert parse_tenant_from_context("") is None
    assert parse_tenant_from_context(None) is None


# --- DID normalisation -------------------------------------------------

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("+1 (555) 123-4567", "+15551234567"),
        ("15551234567", "15551234567"),
        ("sip:+15551234567@carrier.example", "+15551234567"),
        ("tel:+441234567890", "+441234567890"),
        ("+1-555.123.4567;user=phone", "+15551234567"),
    ],
)
def test_normalize_did(raw, expected):
    assert normalize_did(raw) == expected


def test_normalize_did_rejects_garbage():
    assert normalize_did("") is None
    assert normalize_did(None) is None
    assert normalize_did("anonymous") is None
    assert normalize_did("+12") is None  # too short


# --- decision core -----------------------------------------------------

def test_context_only_resolves_tenant():
    route = _decide(context_tenant_id=TENANT_A, campaign_id=CAMP)
    assert route.resolved and not route.rejected and not route.fallback
    assert route.tenant_id == TENANT_A
    assert route.campaign_id == CAMP
    assert route.reason == "routed"


def test_did_only_resolves_tenant():
    route = _decide(did_tenant_id=TENANT_A, campaign_id=CAMP)
    assert route.resolved
    assert route.tenant_id == TENANT_A


def test_context_and_did_agree_routes():
    route = _decide(context_tenant_id=TENANT_A, did_tenant_id=TENANT_A, campaign_id=CAMP)
    assert route.resolved
    assert route.tenant_id == TENANT_A


def test_context_and_did_disagree_is_rejected():
    route = _decide(context_tenant_id=TENANT_A, did_tenant_id=TENANT_B, campaign_id=CAMP)
    assert route.rejected
    assert not route.resolved and not route.fallback
    assert route.tenant_id is None
    assert route.reason == "tenant_conflict"


def test_resolved_without_campaign_still_routes_to_tenant():
    route = _decide(context_tenant_id=TENANT_A, campaign_id=None)
    assert route.resolved
    assert route.tenant_id == TENANT_A
    assert route.campaign_id is None
    assert route.reason == "routed_no_campaign"


def test_unknown_did_strict_off_falls_back():
    route = _decide(strict=False)
    assert route.fallback
    assert not route.resolved and not route.rejected
    assert route.tenant_id is None
    assert route.reason == "unknown_did_fallback"


def test_unknown_did_strict_on_is_rejected():
    route = _decide(strict=True)
    assert route.rejected
    assert not route.fallback and not route.resolved
    assert route.reason == "unknown_did_strict"


def test_conflict_beats_strict_flag():
    # A conflict is always a reject regardless of strict mode.
    route = _decide(context_tenant_id=TENANT_A, did_tenant_id=TENANT_B, strict=False)
    assert route.rejected
    assert route.reason == "tenant_conflict"


# --- DID → campaign resolution (metadata.campaign_id then fallback) -----

class _FakeConn:
    """Minimal asyncpg-conn stand-in for _resolve_campaign."""
    def __init__(self, *, valid_ids=(), fallback_id=None):
        self._valid = set(valid_ids)
        self._fallback = fallback_id

    async def fetchrow(self, query, *args):
        if "id = $1 AND tenant_id = $2" in query:
            cid = args[0]
            return {"id": cid} if cid in self._valid else None
        if "ORDER BY" in query:  # the fallback query
            return {"id": self._fallback} if self._fallback else None
        return None


@pytest.mark.asyncio
async def test_resolve_campaign_prefers_metadata_campaign_id():
    from app.domain.services.telephony.inbound_router import _resolve_campaign
    conn = _FakeConn(valid_ids={CAMP}, fallback_id="fallback-should-not-be-used")
    out = await _resolve_campaign(conn, TENANT_A, {"campaign_id": CAMP})
    assert out == CAMP


@pytest.mark.asyncio
async def test_resolve_campaign_falls_back_when_metadata_campaign_not_owned():
    from app.domain.services.telephony.inbound_router import _resolve_campaign
    # metadata points at a campaign the tenant does NOT own → fall back.
    conn = _FakeConn(valid_ids=set(), fallback_id=CAMP)
    out = await _resolve_campaign(conn, TENANT_A, {"campaign_id": TENANT_B})
    assert out == CAMP


@pytest.mark.asyncio
async def test_resolve_campaign_falls_back_on_bad_metadata_uuid():
    from app.domain.services.telephony.inbound_router import _resolve_campaign
    conn = _FakeConn(fallback_id=CAMP)
    out = await _resolve_campaign(conn, TENANT_A, {"campaign_id": "not-a-uuid"})
    assert out == CAMP


@pytest.mark.asyncio
async def test_resolve_campaign_fallback_when_no_metadata():
    from app.domain.services.telephony.inbound_router import _resolve_campaign
    conn = _FakeConn(fallback_id=CAMP)
    out = await _resolve_campaign(conn, TENANT_A, None)
    assert out == CAMP


@pytest.mark.asyncio
async def test_resolve_campaign_none_when_tenant_has_no_campaigns():
    from app.domain.services.telephony.inbound_router import _resolve_campaign
    conn = _FakeConn(fallback_id=None)
    out = await _resolve_campaign(conn, TENANT_A, {})
    assert out is None
