"""The generated dialplan must address an internal extension explicitly.

Production's dialplan had one mapped account (``150001`` -> a public DID) and a
fail-closed catch-all. A call to extension ``940003`` therefore arrived as the
bare digits ``940003`` where a DID was expected and was denied ``invalid_did``.

These tests pin the two halves of the fix:

* a reviewed, bound extension renders its own ``exten =>`` line carrying the
  tagged address ``ext:940003``, and
* the tag is unforgeable — the preserved catch-all strips colons, and every
  agreement an extension route needs is checked before a line is emitted.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from scripts import reconcile_pjsip_configs as reconcile

TRUNK_A = "11111111-1111-4111-8111-111111111111"
TRUNK_B = "22222222-2222-4222-8222-222222222222"
TENANT_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
TENANT_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
EXT_ASSIGNMENT = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
NOW = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)


def _extension_row(
    trunk_id: str = TRUNK_A,
    *,
    extension: str = "940003",
    account: str | None = None,
    role: str | None = "extension",
    direction: str = "both",
    trunk_tenant_id: str = TENANT_A,
    assignment_tenant_id: str = TENANT_A,
    assignment_trunk_id: str | None = None,
    valid_from: datetime | None = None,
    valid_to: datetime | None = None,
) -> dict:
    metadata: dict[str, object] = {"register": True}
    if role is not None:
        metadata["role"] = role
    return {
        "id": trunk_id,
        "tenant_id": trunk_tenant_id,
        "trunk_name": f"blaze-pbx-{extension}",
        "sip_domain": "sip3.example.invalid",
        "port": 5060,
        "transport": "udp",
        "direction": direction,
        "auth_username": account if account is not None else extension,
        "auth_password_encrypted": "encrypted-password",
        "metadata": metadata,
        "ext_assignment_id": EXT_ASSIGNMENT,
        "ext_tenant_id": assignment_tenant_id,
        "ext_trunk_id": assignment_trunk_id or trunk_id,
        "ext_extension": extension,
        "ext_valid_from": valid_from or (NOW - timedelta(days=1)),
        "ext_valid_to": valid_to,
    }


def _build(rows, tmp_path, **kwargs):
    return reconcile.build_candidate_set(
        rows,
        candidate_dir=tmp_path / "candidate",
        decrypt_password=lambda _: "secret",
        observed_at=NOW,
        verified_account_dids=kwargs.pop("verified_account_dids", {}),
        **kwargs,
    )


def test_a_bound_extension_renders_its_own_tagged_route(tmp_path):
    candidate = _build([_extension_row()], tmp_path)

    dialplan = candidate.files[reconcile.DIALPLAN_NAME].decode()
    assert "exten => 940003,1," in dialplan
    assert "Stasis(talky_ai,inbound,ext:940003,${CONTEXT})" in dialplan
    # The fail-closed catch-all is preserved, and it strips colons so untrusted
    # input can never arrive already tagged.
    assert "exten => _.,1," in dialplan
    assert "${FILTER(0-9+,${EXTEN})}" in dialplan

    assert len(candidate.routes) == 1
    assert candidate.routes[0].account == "940003"
    assert candidate.routes[0].did == "ext:940003"
    assert candidate.routes[0].tenant_id == TENANT_A
    # An extension is not a verified carrier DID, so it never appears here.
    assert candidate.unrouted_verified_trunks == ()


def test_an_extension_never_consults_the_public_did_inventory(tmp_path):
    """No reviewed mapping exists for an extension: the account IS the address."""
    candidate = _build(
        [_extension_row()],
        tmp_path,
        verified_account_dids={"150001": "+442046132300"},
    )
    dialplan = candidate.files[reconcile.DIALPLAN_NAME].decode()
    assert "Stasis(talky_ai,inbound,ext:940003,${CONTEXT})" in dialplan
    assert "+442046132300" not in dialplan


def test_a_trunk_not_declared_an_extension_is_refused(tmp_path):
    """metadata.role is the same flag that keeps it out of outbound selection.

    If a binding could point at a trunk that never declared itself an
    extension, the outbound guard and the inbound route would disagree about
    what the trunk is.
    """
    with pytest.raises(reconcile.UnsafeInboundMappingError, match="not .*declared"):
        _build([_extension_row(role=None)], tmp_path)


def test_a_binding_whose_digits_are_not_the_carrier_account_is_refused(tmp_path):
    with pytest.raises(reconcile.UnsafeInboundMappingError, match="does not match"):
        _build([_extension_row(extension="940003", account="940009")], tmp_path)


def test_a_cross_tenant_extension_binding_is_refused(tmp_path):
    """Belt and braces for a database whose composite FK drifted."""
    with pytest.raises(reconcile.UnsafeInboundMappingError, match="tenant conflict"):
        _build([_extension_row(assignment_tenant_id=TENANT_B)], tmp_path)


def test_an_extension_binding_on_an_outbound_only_trunk_is_refused(tmp_path):
    with pytest.raises(reconcile.UnsafeInboundMappingError, match="non-inbound"):
        _build([_extension_row(direction="outbound")], tmp_path)


@pytest.mark.parametrize("bad", ["94", "9400031234", "94000a", ""])
def test_an_unroutable_extension_is_refused(tmp_path, bad):
    with pytest.raises(reconcile.UnsafeInboundMappingError, match="unroutable"):
        _build([_extension_row(extension=bad)], tmp_path)


def test_two_tenants_claiming_one_extension_is_refused(tmp_path):
    """``tenant_sip_trunks`` has no unique index on auth_username, so two
    tenants really can each create a 940003 trunk. The candidate must refuse
    rather than let row order pick a tenant."""
    rows = [
        _extension_row(TRUNK_A, trunk_tenant_id=TENANT_A, assignment_tenant_id=TENANT_A),
        _extension_row(TRUNK_B, trunk_tenant_id=TENANT_B, assignment_tenant_id=TENANT_B),
    ]
    with pytest.raises(reconcile.UnsafeInboundMappingError, match="duplicate"):
        _build(rows, tmp_path)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"valid_from": NOW + timedelta(seconds=1)},
        {"valid_to": NOW - timedelta(seconds=1)},
    ],
    ids=["not-yet-valid", "expired"],
)
def test_an_out_of_window_binding_renders_only_the_catch_all(tmp_path, kwargs):
    candidate = _build([_extension_row(**kwargs)], tmp_path)
    dialplan = candidate.files[reconcile.DIALPLAN_NAME].decode()
    assert "exten => 940003,1," not in dialplan
    assert "exten => _.,1," in dialplan
    assert candidate.routes == ()


def test_a_trunk_with_no_binding_at_all_renders_only_the_catch_all(tmp_path):
    row = _extension_row()
    row["ext_assignment_id"] = None
    candidate = _build([row], tmp_path)
    dialplan = candidate.files[reconcile.DIALPLAN_NAME].decode()
    assert "exten => 940003,1," not in dialplan
    assert "exten => _.,1," in dialplan


def test_an_extension_route_does_not_leak_its_password_into_the_dialplan(tmp_path):
    """The trunk's own pjsip.d file must carry the credential — that is how the
    endpoint authenticates. The dialplan must not: it is the file whose
    contents get echoed into reconciler diffs and operator read-backs."""
    candidate = _build([_extension_row()], tmp_path)
    dialplan = candidate.files[reconcile.DIALPLAN_NAME]
    assert b"secret" not in dialplan
    assert b"940003" in dialplan, "the route itself is still rendered"
    trunk_conf = candidate.files[f"pjsip.d/trunk-{TRUNK_A}.conf"]
    assert b"secret" in trunk_conf, "endpoint auth would be broken otherwise"
