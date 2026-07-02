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

from datetime import datetime, timedelta

from app.domain.services.telephony.trunk_resolver import (
    DidRow,
    TrunkRow,
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


def test_no_active_trunk_falls_back_to_default():
    # inactive own trunk must NOT be routed to.
    trunks = [TrunkRow(id="t9", trunk_name="my-byo", is_active=False)]
    route = _route(trunks, [DidRow("+15550000000", "verified", "tok")])
    assert route.endpoint == ENV_ENDPOINT
    assert route.is_default is True
    assert route.reason == "no_active_trunk"


def test_empty_trunk_list_falls_back_to_default():
    route = _route([])
    assert route.endpoint == ENV_ENDPOINT
    assert route.reason == "no_active_trunk"


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


def test_multiple_own_trunks_picks_most_recently_updated():
    now = datetime(2026, 7, 1, 12, 0, 0)
    trunks = [
        TrunkRow(id="old", trunk_name="byo-old", is_active=True, updated_at=now),
        TrunkRow(
            id="new", trunk_name="byo-new", is_active=True,
            updated_at=now + timedelta(hours=1),
        ),
    ]
    route = _route(trunks)
    assert route.endpoint == "trunk-new"


def test_own_trunk_with_no_dialable_number_yields_no_caller_id_override():
    trunks = [TrunkRow(id="own", trunk_name="byo", is_active=True)]
    route = _route(trunks, [], is_production=True)
    assert route.endpoint == "trunk-own"
    assert route.caller_id is None  # graceful: caller keeps its caller-ID


# --- caller-ID selection gate ------------------------------------------

def test_caller_id_prod_requires_verified_and_attestation():
    numbers = [
        DidRow("+1111", "pending_verification", "tok"),
        DidRow("+2222", "verified", None),          # verified but no token
        DidRow("+3333", "verified", "tok"),          # dialable
    ]
    assert _select_caller_id(numbers, is_production=True) == "+3333"


def test_caller_id_prod_none_when_no_attested_number():
    numbers = [DidRow("+2222", "verified", None)]
    assert _select_caller_id(numbers, is_production=True) is None


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


def test_flag_off_own_trunk_no_did_falls_back_to_trunk_caller_id():
    trunks = [
        TrunkRow(id="own", trunk_name="byo", is_active=True, caller_id="+441234567890"),
    ]
    route = _route(trunks, [], is_production=True, shared_default_enabled=False)
    assert route.refused is False
    assert route.endpoint == "trunk-own"
    assert route.caller_id == "+441234567890"


def test_flag_off_own_trunk_no_did_no_trunk_caller_id_is_refused():
    trunks = [TrunkRow(id="own", trunk_name="byo", is_active=True)]
    route = _route(trunks, [], is_production=True, shared_default_enabled=False)
    assert route.refused is True
    assert route.endpoint is None
    assert route.trunk_id == "own"
    assert route.reason == "no_caller_id"


def test_flag_off_did_preferred_over_trunk_caller_id():
    trunks = [
        TrunkRow(id="own", trunk_name="byo", is_active=True, caller_id="+440000000000"),
    ]
    route = _route(
        trunks,
        [DidRow("+15559998888", "verified", "tok")],
        is_production=True,
        shared_default_enabled=False,
    )
    assert route.caller_id == "+15559998888"


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
