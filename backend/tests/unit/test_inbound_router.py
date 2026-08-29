"""Fail-closed inbound DID routing tests.

The router must never derive a tenant or campaign from context, metadata, row
order, or a "latest campaign" fallback. Only one fully eligible, active DID
assignment may resolve.
"""

from __future__ import annotations

import pytest

from app.domain.services.telephony.inbound_router import (
    ACTIVE_INBOUND_CAMPAIGN_STATUSES,
    decide_inbound_route,
    normalize_did,
    parse_tenant_from_context,
    redact_did,
    resolve_inbound_route,
    strict_inbound_enabled,
)


TENANT_A = "11111111-1111-1111-1111-111111111111"
TENANT_B = "22222222-2222-2222-2222-222222222222"
CAMPAIGN = "33333333-3333-3333-3333-333333333333"
TRUNK = "44444444-4444-4444-4444-444444444444"
ASSIGNMENT = "55555555-5555-5555-5555-555555555555"
CONFIG = "66666666-6666-6666-6666-666666666666"
PHONE = "77777777-7777-7777-7777-777777777777"


def _bound_decision(**overrides):
    values = {
        "context_tenant_id": None,
        "did_tenant_id": TENANT_A,
        "campaign_id": CAMPAIGN,
        "sip_trunk_id": TRUNK,
        "inbound_campaign_id": ASSIGNMENT,
        "config_id": CONFIG,
        "called_did_id": PHONE,
        "route_version": 3,
        "config_version": 7,
    }
    values.update(overrides)
    return decide_inbound_route(**values)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("+1 (555) 123-4567", "+15551234567"),
        ("15551234567", "+15551234567"),
        ("sip:+15551234567@carrier.example", "+15551234567"),
        ("tel:+441234567890", "+441234567890"),
        ("+1-555.123.4567;user=phone", "+15551234567"),
    ],
)
def test_normalize_did_produces_one_canonical_e164_form(raw, expected):
    assert normalize_did(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "anonymous", "+12", "+1234567890123456"])
def test_normalize_did_rejects_invalid_values(raw):
    assert normalize_did(raw) is None


def test_context_can_only_confirm_never_choose_route():
    assert parse_tenant_from_context(f"From-Tenant-{TENANT_A}") == TENANT_A
    assert parse_tenant_from_context("from-tenant-not-a-uuid") is None
    assert strict_inbound_enabled() is True

    unknown = decide_inbound_route(
        context_tenant_id=TENANT_A,
        did_tenant_id=None,
        campaign_id=CAMPAIGN,
        strict=False,
    )
    assert unknown.rejected is True
    assert unknown.resolved is False
    assert unknown.fallback is False
    assert unknown.tenant_id is None
    assert unknown.reason == "unknown_did"


def test_exact_complete_binding_routes_with_versions():
    route = _bound_decision(context_tenant_id=TENANT_A)
    assert route.resolved is True
    assert route.rejected is False
    assert route.fallback is False
    assert route.tenant_id == TENANT_A
    assert route.campaign_id == CAMPAIGN
    assert route.sip_trunk_id == TRUNK
    assert route.inbound_campaign_id == ASSIGNMENT
    assert route.config_id == CONFIG
    assert route.called_did_id == PHONE
    assert route.route_version == 3
    assert route.config_version == 7


def test_conflict_and_incomplete_binding_fail_closed():
    assert _bound_decision(context_tenant_id=TENANT_B).reason == "tenant_conflict"
    incomplete = _bound_decision(sip_trunk_id=None)
    assert incomplete.rejected is True
    assert incomplete.reason == "incomplete_binding"


def test_redacted_did_is_stable_and_never_contains_raw_digits():
    first = redact_did("+15551234567")
    assert first == redact_did("sip:+1 (555) 123-4567@example.test")
    assert first.startswith("did_")
    assert "15551234567" not in first


class _FakeConn:
    def __init__(self, rows=(), *, fail: Exception | None = None):
        self.rows = list(rows)
        self.fail = fail
        self.query = ""
        self.args = ()

    async def fetch(self, query, *args):
        self.query = query
        self.args = args
        if self.fail:
            raise self.fail
        return self.rows

    async def execute(self, *_args):
        return "SET"

    def transaction(self):
        return _Transaction()


class _Acquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *_args):
        return False


class _Pool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        return _Acquire(self.conn)


class _Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


def _binding(tenant_id=TENANT_A):
    return {
        "inbound_campaign_id": ASSIGNMENT,
        "tenant_id": tenant_id,
        "campaign_id": CAMPAIGN,
        "sip_trunk_id": TRUNK,
        "called_did_id": PHONE,
        "config_id": CONFIG,
        "route_version": 3,
        "config_version": 7,
    }


@pytest.mark.asyncio
async def test_resolver_queries_actual_assignment_tables_and_exact_canonical_did():
    conn = _FakeConn([_binding()])
    route = await resolve_inbound_route(
        _Pool(conn),
        called_did="sip:+1 (555) 123-4567@carrier.example",
        context=f"from-tenant-{TENANT_A}",
        environment="production",
    )
    assert route.resolved is True
    assert "FROM inbound_did_assignments" in conn.query
    assert "JOIN inbound_campaign_configs" in conn.query
    assert "JOIN tenant_phone_numbers" in conn.query
    assert "COALESCE(tic.inbound_enabled, FALSE) = TRUE" in conn.query
    assert "c.status = ANY($2::text[])" in conn.query
    assert "LIMIT 2" in conn.query
    assert conn.args == (
        ["+15551234567"],
        list(ACTIVE_INBOUND_CAMPAIGN_STATUSES),
    )


@pytest.mark.asyncio
async def test_resolver_never_trusts_context_without_a_valid_did():
    conn = _FakeConn([_binding()])
    route = await resolve_inbound_route(
        _Pool(conn),
        called_did=None,
        context=f"from-tenant-{TENANT_A}",
        environment="production",
    )
    assert route.rejected is True
    assert route.reason == "invalid_did"
    assert conn.query == ""


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("rows", "reason"),
    [([], "unknown_did"), ([_binding(), _binding(TENANT_B)], "ambiguous_did")],
)
async def test_resolver_rejects_missing_or_ambiguous_routes(rows, reason):
    route = await resolve_inbound_route(
        _Pool(_FakeConn(rows)),
        called_did="+15551234567",
        context=None,
        environment="production",
    )
    assert route.rejected is True
    assert route.fallback is False
    assert route.reason == reason


@pytest.mark.asyncio
async def test_resolver_rejects_dependency_failure_without_fallback():
    route = await resolve_inbound_route(
        _Pool(_FakeConn(fail=RuntimeError("database unavailable"))),
        called_did="+15551234567",
        context=None,
        environment="production",
    )
    assert route.rejected is True
    assert route.reason == "routing_dependency_unavailable"
