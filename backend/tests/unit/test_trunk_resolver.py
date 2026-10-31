"""Unit tests for the pure per-tenant outbound trunk resolver (Phase A).

These exercise ``choose_outbound_route`` + ``_select_caller_id`` offline —
no DB, no Asterisk. The guarantees under test:

  * a tenant on the seeded platform-default trunk resolves to the global
    env endpoint with caller-ID UNCHANGED (byte-for-byte back-compat);
  * a tenant with an active own trunk resolves to ``trunk-<id>``;
  * safe fallback when there is no active trunk;
  * caller-ID selection honours the verified + attestation gate in prod and
    falls back gracefully outside prod.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import pytest

from app.domain.services.telephony.trunk_resolver import (
    DidRow,
    TrunkRow,
    _resolve_campaign_trunk,
    _resolve_pool_assignment,
    _select_caller_id,
    choose_outbound_route,
)

ENV_ENDPOINT = "blazedigitel-endpoint"
PLATFORM_NAME = "platform-default"


def _route(active_trunks, dialable=(), *, is_production=False, shared_default_enabled=True):
    return choose_outbound_route(
        active_trunks=active_trunks,
        dialable_numbers=dialable,
        env_default_endpoint=ENV_ENDPOINT,
        platform_default_trunk_name=PLATFORM_NAME,
        is_production=is_production,
        shared_default_enabled=shared_default_enabled,
    )


# --- default / platform-default path -----------------------------------

def test_platform_default_only_resolves_to_env_endpoint_unchanged():
    trunks = [TrunkRow(id="t1", trunk_name="platform-default", is_active=True)]
    route = _route(trunks, [DidRow("+15551230000", "verified", "tok")])
    assert route.endpoint == ENV_ENDPOINT
    assert route.caller_id is None  # caller keeps its existing caller-ID
    assert route.is_default is True
    assert route.trunk_id is None
    assert route.reason == "platform_default"


def test_platform_default_name_match_is_case_insensitive():
    trunks = [TrunkRow(id="t1", trunk_name="Platform-Default", is_active=True)]
    route = _route(trunks)
    assert route.is_default is True
    assert route.endpoint == ENV_ENDPOINT


def test_no_active_trunk_refuses_unproven_default():
    # inactive own trunk must NOT be routed to.
    trunks = [TrunkRow(id="t9", trunk_name="my-byo", is_active=False)]
    route = _route(trunks, [DidRow("+15550000000", "verified", "tok")])
    assert route.endpoint is None
    assert route.refused is True
    assert route.reason == "no_ready_trunk"


def test_empty_trunk_list_refuses_unproven_default():
    route = _route([])
    assert route.endpoint is None
    assert route.refused is True
    assert route.reason == "no_ready_trunk"


# --- own-trunk path ----------------------------------------------------

def test_own_active_trunk_resolves_to_namespaced_endpoint():
    trunks = [TrunkRow(id="abc-123", trunk_name="acme-byo", is_active=True)]
    route = _route(
        trunks,
        [DidRow("+15557654321", "verified", "tok")],
        is_production=True,
    )
    assert route.endpoint == "trunk-abc-123"
    assert route.is_default is False
    assert route.trunk_id == "abc-123"
    assert route.reason == "own_trunk"
    assert route.caller_id == "+15557654321"


def test_own_trunk_wins_over_active_platform_default():
    trunks = [
        TrunkRow(id="plat", trunk_name="platform-default", is_active=True),
        TrunkRow(id="own", trunk_name="acme-byo", is_active=True),
    ]
    route = _route(trunks)
    assert route.endpoint == "trunk-own"
    assert route.is_default is False


def test_multiple_own_trunks_picks_newest_created_not_most_recently_updated():
    # The status updater rewrites updated_at on every trunk every ~10 s (a
    # BEFORE UPDATE trigger), so "most recently updated" picked an arbitrary
    # line that flipped between calls. created_at is stable.
    now = datetime(2026, 7, 1, 12, 0, 0)
    trunks = [
        TrunkRow(
            id="old", trunk_name="byo-old", is_active=True,
            created_at=now, updated_at=now + timedelta(hours=5),
        ),
        TrunkRow(
            id="new", trunk_name="byo-new", is_active=True,
            created_at=now + timedelta(hours=1), updated_at=now,
        ),
    ]
    route = _route(trunks)
    assert route.endpoint == "trunk-new"
    # ...and rewriting updated_at (the status churn) does not change the pick.
    churned = [
        TrunkRow(
            id=t.id, trunk_name=t.trunk_name, is_active=True,
            created_at=t.created_at, updated_at=now + timedelta(days=1),
        )
        for t in reversed(trunks)
    ]
    assert _route(churned).endpoint == "trunk-new"


def test_own_trunk_choice_is_independent_of_input_order_and_breaks_ties_by_id():
    created = datetime(2026, 7, 1, 12, 0, 0)
    trunks = [
        TrunkRow(id="trunk-a", trunk_name="a", is_active=True, created_at=created),
        TrunkRow(id="trunk-c", trunk_name="c", is_active=True, created_at=created),
        TrunkRow(id="trunk-b", trunk_name="b", is_active=True, created_at=created),
    ]
    picks = {
        _route(list(order)).endpoint
        for order in (trunks, trunks[::-1], [trunks[1], trunks[2], trunks[0]])
    }
    assert picks == {"trunk-trunk-c"}


def test_trunk_without_created_at_is_treated_as_oldest_and_never_crashes():
    created = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)
    trunks = [
        TrunkRow(id="unknown-age", trunk_name="u", is_active=True),
        TrunkRow(id="dated", trunk_name="d", is_active=True, created_at=created),
    ]
    assert _route(trunks).endpoint == "trunk-dated"
    assert _route(trunks[::-1]).endpoint == "trunk-dated"


def test_a_ready_own_trunk_is_not_shadowed_by_a_newer_not_ready_or_inbound_only_one():
    # An inbound-only / rejected / stale line has runtime_ready False. Picking
    # the newest line first and checking readiness afterwards let it shadow a
    # working older line, refusing every call with selected_trunk_not_ready.
    created = datetime(2026, 7, 1, 12, 0, 0)
    trunks = [
        TrunkRow(id="working", trunk_name="w", is_active=True, created_at=created),
        TrunkRow(
            id="inbound-only", trunk_name="i", is_active=True,
            created_at=created + timedelta(days=3), runtime_ready=False,
        ),
        TrunkRow(
            id="rejected", trunk_name="r", is_active=True,
            created_at=created + timedelta(days=9), runtime_ready=False,
        ),
    ]
    dids = [DidRow("+15550001111", "verified", "tok")]  # own-trunk-only mode needs a caller ID
    for shared in (True, False):
        route = _route(trunks, dids, shared_default_enabled=shared)
        assert route.refused is False
        assert route.endpoint == "trunk-working"
        assert route.trunk_id == "working"
        assert route.reason == "own_trunk"


def test_among_ready_own_trunks_the_newest_created_wins():
    created = datetime(2026, 7, 1, 12, 0, 0)
    trunks = [
        TrunkRow(id="ready-old", trunk_name="a", is_active=True, created_at=created),
        TrunkRow(
            id="ready-new", trunk_name="b", is_active=True,
            created_at=created + timedelta(days=1),
        ),
        TrunkRow(
            id="newest-down", trunk_name="c", is_active=True,
            created_at=created + timedelta(days=2), runtime_ready=False,
        ),
    ]
    assert _route(trunks).endpoint == "trunk-ready-new"


def test_when_no_own_trunk_is_ready_the_refusal_names_the_newest_created_one():
    created = datetime(2026, 7, 1, 12, 0, 0)
    trunks = [
        TrunkRow(
            id="down-old", trunk_name="a", is_active=True,
            created_at=created, runtime_ready=False,
        ),
        TrunkRow(
            id="down-new", trunk_name="b", is_active=True,
            created_at=created + timedelta(days=1), runtime_ready=False,
        ),
    ]
    for shared in (True, False):
        route = _route(trunks, shared_default_enabled=shared)
        assert route.refused is True
        assert route.endpoint is None
        assert route.reason == "selected_trunk_not_ready"
        assert route.trunk_id == "down-new"


def test_own_trunk_with_no_dialable_number_yields_no_caller_id_override():
    trunks = [TrunkRow(id="own", trunk_name="byo", is_active=True)]
    route = _route(trunks, [], is_production=True)
    assert route.endpoint == "trunk-own"
    assert route.caller_id is None  # graceful: caller keeps its caller-ID


# --- caller-ID selection gate ------------------------------------------

def test_caller_id_prod_requires_verified_without_static_token_preference():
    numbers = [
        DidRow("+1111", "pending_verification", "tok"),
        DidRow("+2222", "verified", None),          # verified but no token
        DidRow("+3333", "verified", "tok"),          # dialable
    ]
    assert _select_caller_id(numbers, is_production=True) == "+2222"


def test_caller_id_prod_accepts_verified_without_static_token():
    numbers = [DidRow("+2222", "verified", None)]
    assert _select_caller_id(numbers, is_production=True) == "+2222"


def test_caller_id_nonprod_prefers_verified_without_token():
    numbers = [
        DidRow("+9999", "pending_verification", None),
        DidRow("+4444", "verified", None),
    ]
    assert _select_caller_id(numbers, is_production=False) == "+4444"


def test_caller_id_nonprod_falls_back_to_any_number():
    numbers = [DidRow("+7777", "pending_verification", None)]
    assert _select_caller_id(numbers, is_production=False) == "+7777"


def test_caller_id_selection_is_deterministic():
    numbers = [
        DidRow("+3000", "verified", "tok"),
        DidRow("+1000", "verified", "tok"),
        DidRow("+2000", "verified", "tok"),
    ]
    # lowest E.164 wins, stable across calls.
    assert _select_caller_id(numbers, is_production=True) == "+1000"


# --- own-trunk-only mode (TELEPHONY_SHARED_DEFAULT_TRUNK=off) -----------

def test_flag_off_own_trunk_with_did_routes():
    trunks = [TrunkRow(id="own", trunk_name="byo", is_active=True)]
    route = _route(
        trunks,
        [DidRow("+15550001111", "verified", "tok")],
        is_production=True,
        shared_default_enabled=False,
    )
    assert route.refused is False
    assert route.endpoint == "trunk-own"
    assert route.caller_id == "+15550001111"


def test_flag_off_no_own_trunk_is_refused():
    # Only the seeded platform-default is active; own-trunk-only refuses it.
    trunks = [TrunkRow(id="plat", trunk_name="platform-default", is_active=True)]
    route = _route(trunks, shared_default_enabled=False)
    assert route.refused is True
    assert route.endpoint is None
    assert route.is_default is False
    assert route.reason == "no_own_trunk"


def test_flag_off_empty_trunks_is_refused():
    route = _route([], shared_default_enabled=False)
    assert route.refused is True
    assert route.reason == "no_own_trunk"


def test_flag_off_unverified_trunk_caller_id_is_refused():
    trunks = [
        TrunkRow(id="own", trunk_name="byo", is_active=True, caller_id="+441234567890"),
    ]
    route = _route(trunks, [], is_production=True, shared_default_enabled=False)
    assert route.refused is True
    assert route.endpoint is None
    assert route.caller_id is None
    assert route.reason == "trunk_caller_id_not_verified"


def test_flag_off_own_trunk_no_did_no_trunk_caller_id_is_refused():
    trunks = [TrunkRow(id="own", trunk_name="byo", is_active=True)]
    route = _route(trunks, [], is_production=True, shared_default_enabled=False)
    assert route.refused is True
    assert route.endpoint is None
    assert route.trunk_id == "own"
    assert route.reason == "no_caller_id"


def test_verified_alternative_did_does_not_replace_selected_trunk_caller_id():
    trunks = [
        TrunkRow(id="own", trunk_name="byo", is_active=True, caller_id="+440000000000"),
    ]
    route = _route(
        trunks,
        [DidRow("+15559998888", "verified", "tok")],
        is_production=True,
        shared_default_enabled=False,
    )
    assert route.refused is True
    assert route.endpoint is None
    assert route.caller_id is None
    assert route.reason == "trunk_caller_id_not_verified"


def test_flag_on_own_trunk_no_caller_id_still_routes_not_refused():
    # Back-compat: with the shared default ON, an own trunk with no DID and
    # no configured caller-ID routes (caller keeps its caller-ID) — never
    # a new refusal.
    trunks = [TrunkRow(id="own", trunk_name="byo", is_active=True)]
    route = _route(trunks, [], is_production=True, shared_default_enabled=True)
    assert route.refused is False
    assert route.endpoint == "trunk-own"
    assert route.caller_id is None


def test_flag_on_no_own_trunk_unchanged_default_fallback():
    trunks = [TrunkRow(id="plat", trunk_name="platform-default", is_active=True)]
    route = _route(trunks, shared_default_enabled=True)
    assert route.refused is False
    assert route.is_default is True
    assert route.endpoint == ENV_ENDPOINT


def test_unhealthy_own_trunk_is_never_selected():
    trunks = [
        TrunkRow(
            id="broken",
            trunk_name="byo",
            is_active=True,
            runtime_ready=False,
        )
    ]
    route = _route(trunks, shared_default_enabled=False)
    assert route.refused is True
    assert route.endpoint is None
    assert route.reason == "selected_trunk_not_ready"


class _AssignedConn:
    def __init__(self, snapshot, row):
        self.snapshot = snapshot
        self.row = row

    async def fetchval(self, *_args):
        return self.snapshot

    async def execute(self, *_args):
        return "SELECT 1"

    async def fetchrow(self, *_args):
        return self.row


def _runtime_row(**overrides):
    row = {
        "id": "11111111-1111-1111-1111-111111111111",
        "tenant_id": "22222222-2222-2222-2222-222222222222",
        "trunk_name": "tenant-byo",
        "is_active": True,
        "direction": "both",
        "metadata": {"register": True, "pool": True},
        "live_registration_status": "registered",
        "live_status_detail": None,
        "live_status_checked_at": datetime.now(timezone.utc),
    }
    row.update(overrides)
    return row


@pytest.mark.asyncio
async def test_explicit_campaign_assignment_refuses_unhealthy_trunk(monkeypatch):
    from app.core import db_utils

    conn = _AssignedConn(
        {"id": "11111111-1111-1111-1111-111111111111", "endpoint": "stale"},
        _runtime_row(live_registration_status="rejected"),
    )

    @asynccontextmanager
    async def acquire(*_args, **_kwargs):
        yield conn

    monkeypatch.setattr(db_utils, "acquire_with_tenant", acquire)
    route = await _resolve_campaign_trunk(
        object(),
        campaign_id="33333333-3333-3333-3333-333333333333",
        tenant_id="22222222-2222-2222-2222-222222222222",
    )
    assert route is not None
    assert route.refused is True
    assert route.reason == "campaign_assigned_trunk_not_ready"


@pytest.mark.asyncio
async def test_pool_assignment_uses_current_health_not_stale_snapshot(monkeypatch):
    from app.core import db_utils

    conn = _AssignedConn(
        {
            "id": "11111111-1111-1111-1111-111111111111",
            "endpoint": "trunk-stale",
            "caller_id": "+15551234567",
        },
        _runtime_row(),
    )

    @asynccontextmanager
    async def acquire(*_args, **_kwargs):
        yield conn

    monkeypatch.setattr(db_utils, "acquire_with_tenant", acquire)
    route = await _resolve_pool_assignment(
        object(),
        tenant_id="22222222-2222-2222-2222-222222222222",
        is_production=True,
    )
    assert route is not None
    assert route.refused is False
    assert route.endpoint == "trunk-11111111-1111-1111-1111-111111111111"


def test_trunk_caller_id_that_is_a_verified_tenant_number_wins():
    """Dojo-PC, 2026-09-30: the trunk "17789249977" presents 17789249977, a
    verified number of the tenant, but only +442046132300 has a STIR/SHAKEN
    token -- so every call on the 9977 account presented ...300 and the
    carrier rejected it. The number pinned on the trunk, once verified, wins."""
    trunks = [
        TrunkRow(id="own", trunk_name="17789249977", is_active=True, caller_id="17789249977"),
    ]
    dids = [
        DidRow("+442046132300", "verified", "tok"),
        DidRow("+17789249977", "verified", None),
    ]
    for shared in (True, False):
        route = _route(trunks, dids, is_production=True, shared_default_enabled=shared)
        assert route.endpoint == "trunk-own"
        assert route.caller_id == "+17789249977"


def test_unverified_canadian_caller_id_is_not_silently_replaced_by_uk_number():
    trunks = [
        TrunkRow(id="own", trunk_name="byo", is_active=True, caller_id="17789249977"),
    ]
    dids = [
        DidRow("+442046132300", "verified", "tok"),
        DidRow("+17789249977", "pending", None),
    ]
    route = _route(trunks, dids, is_production=True, shared_default_enabled=False)
    assert route.refused is True
    assert route.endpoint is None
    assert route.caller_id is None
    assert route.reason == "trunk_caller_id_not_verified"


@pytest.mark.asyncio
@pytest.mark.parametrize("assignment", ["campaign", "pool"])
@pytest.mark.parametrize("foreign", [False, True])
async def test_assigned_route_uses_current_number_and_requires_trunk_ownership(monkeypatch, assignment, foreign):
    from app.core import db_utils

    tenant = "22222222-2222-2222-2222-222222222222"
    row = _runtime_row(metadata={"register": True, "pool": True, "caller_id": "+14165550123"})
    if foreign:
        row["tenant_id"] = "44444444-4444-4444-4444-444444444444"
    conn = _AssignedConn({"id": row["id"], "caller_id": "+442055501234"}, row)

    @asynccontextmanager
    async def acquire(*_args, **_kwargs):
        yield conn

    monkeypatch.setattr(db_utils, "acquire_with_tenant", acquire)
    if assignment == "campaign":
        route = await _resolve_campaign_trunk(object(), tenant_id=tenant, campaign_id="33333333-3333-3333-3333-333333333333")
    else:
        route = await _resolve_pool_assignment(object(), tenant_id=tenant, is_production=True)
    assert route is not None
    assert route.refused is foreign
    if foreign:
        assert route.reason == "trunk_not_authorized"
        assert route.endpoint is None
    else:
        assert route.caller_id == "+14165550123"


# --- resolve_outbound_trunk: DB projection + logging --------------------

TENANT = "22222222-2222-2222-2222-222222222222"
OLD_TRUNK = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
NEW_TRUNK = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


class _TrunkListConn:
    """Serves the tenant_sip_trunks / tenant_phone_numbers SELECTs."""

    def __init__(self, trunk_rows, did_rows=()):
        self.trunk_rows = trunk_rows
        self.did_rows = list(did_rows)
        self.trunk_sql = None

    async def fetch(self, query, *args):
        normalized = " ".join(query.split())
        if "FROM tenant_sip_trunks" in normalized:
            self.trunk_sql = normalized
            assert args == (TENANT,), "the trunk SELECT must carry the tenant predicate"
            return self.trunk_rows
        if "FROM tenant_phone_numbers" in normalized:
            return self.did_rows
        raise AssertionError(f"unexpected query: {normalized}")


def _trunk_row(trunk_id, *, created_at, updated_at, status="registered", direction="both"):
    return {
        "id": trunk_id,
        "trunk_name": f"byo-{trunk_id[:4]}",
        "is_active": True,
        "created_at": created_at,
        "updated_at": updated_at,
        "direction": direction,
        "metadata": {"register": True},
        "live_registration_status": status,
        "live_status_detail": None,
        "live_status_checked_at": datetime.now(timezone.utc),
    }


async def _no_assignment(*_args, **_kwargs):
    return None


async def _resolve_with(monkeypatch, conn):
    from app.core import db_utils
    from app.domain.services.telephony import trunk_resolver

    @asynccontextmanager
    async def acquire(*_args, **_kwargs):
        yield conn

    monkeypatch.setattr(db_utils, "acquire_with_tenant", acquire)
    monkeypatch.setattr(trunk_resolver, "_resolve_campaign_trunk", _no_assignment)
    monkeypatch.setattr(trunk_resolver, "_resolve_pool_assignment", _no_assignment)
    return await trunk_resolver.resolve_outbound_trunk(
        object(), tenant_id=TENANT, environment="test"
    )


@pytest.mark.asyncio
async def test_resolver_selects_created_at_and_prefers_the_ready_line(monkeypatch):
    t0 = datetime(2026, 9, 1, tzinfo=timezone.utc)
    conn = _TrunkListConn(
        [
            # the newest line was just touched by the status updater but is rejected
            _trunk_row(
                NEW_TRUNK, created_at=t0 + timedelta(days=20),
                updated_at=datetime.now(timezone.utc), status="rejected",
            ),
            _trunk_row(OLD_TRUNK, created_at=t0, updated_at=t0, status="registered"),
        ]
    )

    route = await _resolve_with(monkeypatch, conn)

    assert "created_at" in conn.trunk_sql
    assert route.refused is False
    assert route.trunk_id == OLD_TRUNK
    assert route.endpoint == f"trunk-{OLD_TRUNK}"


@pytest.mark.asyncio
async def test_resolver_inbound_only_line_cannot_shadow_an_outbound_one(monkeypatch):
    t0 = datetime(2026, 9, 1, tzinfo=timezone.utc)
    conn = _TrunkListConn(
        [
            _trunk_row(
                NEW_TRUNK, created_at=t0 + timedelta(days=1),
                updated_at=t0, direction="inbound",
            ),
            _trunk_row(OLD_TRUNK, created_at=t0, updated_at=t0, direction="outbound"),
        ]
    )

    route = await _resolve_with(monkeypatch, conn)

    assert route.refused is False
    assert route.trunk_id == OLD_TRUNK


@pytest.mark.asyncio
async def test_resolver_logs_the_real_reason_and_refusal_for_assigned_routes(
    monkeypatch, caplog
):
    import logging

    from app.domain.services.telephony import trunk_resolver
    from app.domain.services.telephony.trunk_resolver import OutboundTrunkRoute

    campaign_refused = OutboundTrunkRoute(
        None, None, "t1", False, "campaign_assigned_trunk_not_ready", True
    )
    pool_refused = OutboundTrunkRoute(
        None, None, "t3", False, "pool_assigned_trunk_not_ready", True
    )
    pool_ok = OutboundTrunkRoute("trunk-t2", None, "t2", False, "pool_assigned", False)
    caplog.set_level(logging.INFO, logger=trunk_resolver.logger.name)

    async def campaign_route(*_a, **_k):
        return campaign_refused

    async def pool_route_refused(*_a, **_k):
        return pool_refused

    async def pool_route_ok(*_a, **_k):
        return pool_ok

    # A refused campaign route used to be logged as reason=campaign_assigned.
    monkeypatch.setattr(trunk_resolver, "_resolve_campaign_trunk", campaign_route)
    await trunk_resolver.resolve_outbound_trunk(
        object(), tenant_id=TENANT, environment="test",
        campaign_id="cccccccc-cccc-4ccc-8ccc-cccccccccccc",
    )
    assert "reason=campaign_assigned_trunk_not_ready" in caplog.text
    assert "refused=True" in caplog.text
    assert "reason=campaign_assigned " not in caplog.text

    # Same for a refused pool route.
    caplog.clear()
    monkeypatch.setattr(trunk_resolver, "_resolve_campaign_trunk", _no_assignment)
    monkeypatch.setattr(trunk_resolver, "_resolve_pool_assignment", pool_route_refused)
    await trunk_resolver.resolve_outbound_trunk(object(), tenant_id=TENANT, environment="test")
    assert "reason=pool_assigned_trunk_not_ready" in caplog.text
    assert "refused=True" in caplog.text
    assert "reason=pool_assigned " not in caplog.text

    # An accepted route still logs its own reason and refused=False.
    caplog.clear()
    monkeypatch.setattr(trunk_resolver, "_resolve_pool_assignment", pool_route_ok)
    await trunk_resolver.resolve_outbound_trunk(object(), tenant_id=TENANT, environment="test")
    assert "reason=pool_assigned" in caplog.text
    assert "refused=False" in caplog.text
