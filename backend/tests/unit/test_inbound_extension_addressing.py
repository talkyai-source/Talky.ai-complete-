"""Internal PBX extensions are a second kind of inbound address, not a loose DID.

Before this feature a call to extension ``940003`` reached
``inbound_router.normalize_did``, failed its 7-15 digit rule and was denied
``invalid_did`` by both the router and admission. The tempting fix — widening
the digit range — is the thing these tests exist to prevent: it would turn the
typo ``9400031`` into the *phone number* ``+9400031``.

So the distinction is explicit (``ext:940003`` vs ``+442046132300``), the tag is
minted only by the reconciler's generated dialplan, and every isolation guard a
DID gets, an extension gets too.
"""
from __future__ import annotations

import pytest

from app.domain.services.telephony.inbound_address import (
    EXTENSION_PREFIX,
    address_kind,
    canonical_extension,
    is_extension_address,
    parse_extension,
)
from app.domain.services.telephony.inbound_router import (
    ACTIVE_INBOUND_CAMPAIGN_STATUSES,
    normalize_did,
    resolve_inbound_route,
)
from app.domain.services.telephony.trunk_resolver import (
    TrunkRow,
    choose_outbound_route,
)

TENANT_A = "11111111-1111-1111-1111-111111111111"
TENANT_B = "22222222-2222-2222-2222-222222222222"
CAMPAIGN = "33333333-3333-3333-3333-333333333333"
TRUNK = "44444444-4444-4444-4444-444444444444"
ASSIGNMENT = "55555555-5555-5555-5555-555555555555"
CONFIG = "66666666-6666-6666-6666-666666666666"


# ── the address vocabulary ───────────────────────────────────────────────────

def test_only_a_tagged_extension_is_an_extension():
    assert parse_extension("ext:940003") == "940003"
    assert parse_extension("EXT:940003") == "940003"
    assert canonical_extension("940003") == "ext:940003"
    assert is_extension_address("ext:940003")


@pytest.mark.parametrize(
    "raw",
    [
        "940003",       # untagged digits are NOT an extension — the tag is authority
        "ext:94",       # too short
        "ext:940003111",  # too long: that length is a phone number, not an extension
        "ext:94000a",   # not digits
        "ext:",
        "ext:+442046132300",
        "+442046132300",
        "",
        None,
    ],
)
def test_untagged_or_malformed_input_is_never_an_extension(raw):
    assert parse_extension(raw) is None
    assert is_extension_address(raw) is False


def test_address_kind_separates_the_two_kinds():
    assert address_kind("+442046132300") == "did"
    assert address_kind("ext:940003") == "extension"
    assert address_kind("940003") is None
    assert address_kind("+94000") is None


# ── normalize_did keeps public numbers byte-identical ────────────────────────

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("sip:+1 (555) 123-4567@carrier.example", "+15551234567"),
        ("+442046132300", "+442046132300"),
        ("442046132300", "+442046132300"),
    ],
)
def test_public_number_normalisation_is_unchanged(raw, expected):
    assert normalize_did(raw) == expected


def test_a_tagged_extension_passes_through_canonically():
    assert normalize_did("ext:940003") == "ext:940003"
    assert normalize_did(" EXT:940003 ") == "ext:940003"


@pytest.mark.parametrize("raw", ["940003", "94000", "9400031"])
def test_bare_six_digit_input_is_still_refused_as_a_number(raw):
    """The regression this whole design exists to avoid.

    Widening normalize_did's digit range would make ``9400031`` the phone
    number ``+9400031``. Untagged short digits must stay invalid.
    """
    result = normalize_did(raw)
    assert result != "ext:" + raw
    assert result is None or result.startswith("+")
    if raw == "9400031":
        # 7 digits is a legal E.164 length, so it normalises as a NUMBER —
        # never as extension 9400031. That is the point.
        assert result == "+9400031"
    else:
        assert result is None


# ── the router resolves an extension through its own table ───────────────────

class _FakeConn:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.query = ""
        self.args = ()

    async def fetch(self, query, *args):
        self.query = query
        self.args = args
        return self.rows

    async def execute(self, *_args):
        return "SET"

    def transaction(self):
        return _Transaction()


class _Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


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


def _extension_binding(tenant_id=TENANT_A):
    return {
        "inbound_campaign_id": ASSIGNMENT,
        "tenant_id": tenant_id,
        "campaign_id": CAMPAIGN,
        "sip_trunk_id": TRUNK,
        "called_did_id": None,  # an extension has no tenant_phone_numbers row
        "config_id": CONFIG,
        "route_version": 2,
        "config_version": 5,
    }


@pytest.mark.asyncio
async def test_extension_resolves_through_the_extension_table_with_every_guard():
    conn = _FakeConn([_extension_binding()])
    route = await resolve_inbound_route(
        _Pool(conn),
        called_did="ext:940003",
        context=f"from-tenant-{TENANT_A}",
        environment="production",
    )

    assert route.resolved is True
    assert route.tenant_id == TENANT_A
    assert route.campaign_id == CAMPAIGN
    assert route.called_did_id is None, "must not stringify NULL into 'None'"

    # It queried the extension table, not the DID table.
    assert "FROM inbound_extension_assignments" in conn.query
    assert "FROM inbound_did_assignments" not in conn.query
    # Ownership is proven by the trunk that registers the extension.
    assert "st.auth_username = $1" in conn.query
    # Every isolation/readiness gate the DID path has.
    assert "AND st.tenant_id = a.tenant_id" in conn.query
    assert "cfg.tenant_id = a.tenant_id" in conn.query
    assert "c.tenant_id = a.tenant_id" in conn.query
    assert "c.direction = 'inbound'" in conn.query
    assert "t.subscription_status IN ('active', 'trialing')" in conn.query
    assert "COALESCE(tic.inbound_enabled, FALSE) = TRUE" in conn.query
    assert "LIMIT 2" in conn.query
    # The bare digits are the parameter — the tag is a transport detail.
    assert conn.args == ("940003", list(ACTIVE_INBOUND_CAMPAIGN_STATUSES))


@pytest.mark.asyncio
async def test_unknown_extension_is_rejected_not_guessed():
    conn = _FakeConn([])
    route = await resolve_inbound_route(
        _Pool(conn),
        called_did="ext:940099",
        context=None,
        environment="production",
    )
    assert route.resolved is False
    assert route.reason == "unknown_did"


@pytest.mark.asyncio
async def test_two_rows_for_one_extension_are_refused_rather_than_ordered():
    """A missing unique index must not become a silent tenant selection."""
    conn = _FakeConn([_extension_binding(TENANT_A), _extension_binding(TENANT_B)])
    route = await resolve_inbound_route(
        _Pool(conn),
        called_did="ext:940003",
        context=None,
        environment="production",
    )
    assert route.resolved is False
    assert route.reason == "ambiguous_did"


@pytest.mark.asyncio
async def test_a_context_from_another_tenant_cannot_claim_an_extension():
    conn = _FakeConn([_extension_binding(TENANT_A)])
    route = await resolve_inbound_route(
        _Pool(conn),
        called_did="ext:940003",
        context=f"from-tenant-{TENANT_B}",
        environment="production",
    )
    assert route.resolved is False
    assert route.reason == "tenant_conflict"


@pytest.mark.asyncio
async def test_a_forged_tag_from_untrusted_input_cannot_route():
    """Defence in depth behind the dialplan's FILTER.

    Even if a colon reached Asterisk, the address the caller controls is the
    bare user part. Bare digits are not an extension address, so nothing is
    looked up at all.
    """
    conn = _FakeConn([_extension_binding()])
    route = await resolve_inbound_route(
        _Pool(conn),
        called_did="940003",
        context=None,
        environment="production",
    )
    assert route.resolved is False
    assert route.reason == "invalid_did"
    assert conn.query == "", "no database lookup for an unaddressable call"


@pytest.mark.asyncio
async def test_a_public_did_still_uses_the_did_table():
    conn = _FakeConn([{**_extension_binding(), "called_did_id": CONFIG}])
    route = await resolve_inbound_route(
        _Pool(conn),
        called_did="+442046132300",
        context=None,
        environment="production",
    )
    assert route.resolved is True
    assert "FROM inbound_did_assignments" in conn.query
    assert "JOIN tenant_phone_numbers" in conn.query
    assert "inbound_extension_assignments" not in conn.query


# ── outbound: an extension trunk must never hijack a tenant's routing ────────

def _trunk(name, *, extension=False, updated=None):
    from datetime import datetime, timezone

    return TrunkRow(
        id=f"{name}-id",
        trunk_name=name,
        is_active=True,
        updated_at=updated or datetime(2026, 9, 20, tzinfo=timezone.utc),
        caller_id=None,
        runtime_ready=True,
        is_internal_extension=extension,
    )


def _route(trunks):
    return choose_outbound_route(
        active_trunks=trunks,
        dialable_numbers=[],
        env_default_endpoint="blazedigitel-endpoint",
        platform_default_trunk_name="blaze-primary",
        is_production=True,
        shared_default_enabled=True,
    )


def test_activating_an_extension_trunk_does_not_move_existing_outbound():
    """The blocker: provisioning 940003 on a tenant that is mid-campaign.

    Before the fix, any activated own trunk beat the shared platform endpoint,
    so merely creating the extension re-routed a running outbound campaign onto
    a PBX account with no PSTN path and no presentable caller-ID.
    """
    from datetime import datetime, timezone

    before = _route([_trunk("blaze-primary")])
    assert before.endpoint == "blazedigitel-endpoint"
    assert before.is_default is True

    after = _route([
        _trunk("blaze-primary"),
        _trunk(
            "blaze-pbx-940003",
            extension=True,
            updated=datetime(2026, 9, 21, tzinfo=timezone.utc),  # newest
        ),
    ])
    assert after.endpoint == before.endpoint
    assert after.is_default is True
    assert after.reason == before.reason


def test_a_real_byo_trunk_still_wins_over_the_platform_default():
    """The guard must not disable legitimate bring-your-own-trunk routing."""
    route = _route([_trunk("blaze-primary"), _trunk("customer-sbc")])
    assert route.endpoint == "trunk-customer-sbc-id"
    assert route.is_default is False
    assert route.reason == "own_trunk"


def test_an_extension_only_tenant_falls_back_to_the_platform_default():
    route = _route([_trunk("blaze-pbx-940003", extension=True)])
    assert route.is_default is True
    assert route.endpoint == "blazedigitel-endpoint"


def test_extension_flag_defaults_off_so_no_existing_trunk_changes():
    assert TrunkRow(id="x", trunk_name="y", is_active=True).is_internal_extension is False


def test_the_prefix_constant_is_the_single_source_of_truth():
    assert EXTENSION_PREFIX == "ext:"
    assert canonical_extension("940007").startswith(EXTENSION_PREFIX)


# ── caller identity from another extension ───────────────────────────────────

def test_a_caller_on_another_extension_is_not_recorded_as_withheld():
    """A MicroSIP caller presents "940007", not an E.164 number.

    normalize_did rightly refuses that as a phone number, but returning None
    made _private_ani mark the call caller-withheld — recording that the caller
    hid their identity when they did not.
    """
    from app.domain.services.telephony.inbound_admission import _private_ani

    value, private = _private_ani("940007")
    assert value == "ext:940007"
    assert private is False


@pytest.mark.parametrize("raw", ["anonymous", "private", "restricted", "unknown", "unavailable", "", None])
def test_a_genuinely_withheld_caller_is_still_withheld(raw):
    from app.domain.services.telephony.inbound_admission import _private_ani

    assert _private_ani(raw) == (None, True)


def test_a_public_caller_number_is_unchanged():
    from app.domain.services.telephony.inbound_admission import _private_ani

    assert _private_ani("+447700900123") == ("+447700900123", False)
    assert _private_ani("sip:+44 7700 900123@carrier.example") == ("+447700900123", False)


def test_junk_ani_is_still_treated_as_no_identity():
    from app.domain.services.telephony.inbound_admission import _private_ani

    value, private = _private_ani("not-a-number")
    assert value is None and private is True


# ── the DID-only routing config must refuse an extension explicitly ──────────

def test_the_inbound_routing_config_refuses_an_extension_with_a_usable_reason():
    """normalize_did now canonicalises "ext:940003" too, so an extension can
    reach the inbound config service. That service writes a tenant_phone_numbers
    row and an inbound_did_assignments row whose CHECK requires E.164, so it
    must refuse up front — not fail on a constraint three layers down where the
    operator sees a database error instead of an instruction.
    """
    import inspect

    from app.domain.services import inbound_campaign_service as svc

    source = inspect.getsource(svc)
    assert source.count('code="extension_not_a_did"') == 3, (
        "all three DID entry points (create, update, availability) must refuse "
        "an extension address"
    )
    for fn in ("create_campaign", "did_availability"):
        body = inspect.getsource(getattr(svc.InboundCampaignService, fn))
        assert "is_extension_address" in body, fn
