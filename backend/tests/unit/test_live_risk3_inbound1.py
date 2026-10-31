"""inbound-1: admission checks the PJSIP endpoint the call arrived on."""

from __future__ import annotations

import pytest

import app.domain.services.telephony.inbound_admission as admission_module
from app.domain.services.telephony.inbound_admission import (
    InboundAdmissionRequest,
    InboundAdmissionService,
)
from tests.unit.test_inbound_admission import (
    TENANT,
    TRUNK,
    _acquire,
    _AllowedConn,
    _Limiter,
)

# Endpoint name exactly as setup-asterisk.sh defines the shared carrier.
CARRIER_ENDPOINT = "blazedigitel-endpoint"


async def _admit(monkeypatch, metadata):
    conn = _AllowedConn()
    monkeypatch.setattr(admission_module, "acquire_with_tenant", lambda *_a: _acquire(conn))
    return await InboundAdmissionService(object(), _Limiter()).admit(
        InboundAdmissionRequest(
            provider="asterisk",
            provider_call_id="ep-call",
            called_did="+15551234567",
            reservation_seconds=60,
            metadata=metadata,
        )
    )


@pytest.mark.asyncio
async def test_shared_carrier_endpoint_still_admitted(monkeypatch):
    d = await _admit(monkeypatch, {"ingress_endpoint": CARRIER_ENDPOINT})
    assert d.allowed is True


@pytest.mark.asyncio
async def test_own_trunk_endpoint_admitted(monkeypatch):
    d = await _admit(monkeypatch, {"ingress_endpoint": f"trunk-{TRUNK}"})
    assert d.allowed is True


@pytest.mark.asyncio
async def test_other_tenant_trunk_endpoint_denied(monkeypatch):
    d = await _admit(monkeypatch, {"ingress_endpoint": "trunk-other-tenant"})
    assert d.allowed is False


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["anonymous", "Anonymous"])
async def test_anonymous_endpoint_denied(monkeypatch, name, caplog):
    d = await _admit(monkeypatch, {"ingress_endpoint": name})
    assert d.allowed is False
    assert any("inbound_admission_endpoint_unidentified" in r.message for r in caplog.records)


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [None, "", "  "])
async def test_adapter_reported_no_endpoint_denied(monkeypatch, value):
    d = await _admit(monkeypatch, {"ingress_endpoint": value, "linked_id": "x"})
    assert d.allowed is False


@pytest.mark.asyncio
async def test_no_metadata_at_all_unchanged(monkeypatch):
    # Callers that never carry endpoint metadata keep their prior behaviour.
    d = await _admit(monkeypatch, None)
    assert d.allowed is True


# --- The trunk-<id> endpoint a call is tagged with is only evidence when it
# could not be another line on the same carrier host. pjsip identify matches by
# source host alone, so with several Blaze lines on one carrier IP Asterisk tags
# every inbound INVITE with whichever line it matches first (prod: all Blaze
# endpoints identify by the same IP). Denying every mismatch would refuse real
# calls the moment a second line on that carrier is active.

CARRIER_HOST = "sip3.blazedigitel.com"
OTHER_LINE = "99999999-9999-9999-9999-999999999999"
OTHER_TENANT = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


class _TrunkConn(_AllowedConn):
    def __init__(self, ingress_row, **kwargs):
        super().__init__(
            route_overrides={"trunk_sip_domain": CARRIER_HOST, "trunk_metadata": {}},
            **kwargs,
        )
        self.ingress_row = ingress_row
        self.trunk_lookups = []

    async def fetchrow(self, query, *args):
        if "FROM tenant_sip_trunks" in query:
            self.trunk_lookups.append(args)
            return self.ingress_row
        return await super().fetchrow(query, *args)


async def _admit_on(monkeypatch, ingress_row, endpoint=f"trunk-{OTHER_LINE}"):
    conn = _TrunkConn(ingress_row)
    monkeypatch.setattr(admission_module, "acquire_with_tenant", lambda *_a: _acquire(conn))
    decision = await InboundAdmissionService(object(), _Limiter()).admit(
        InboundAdmissionRequest(
            provider="asterisk",
            provider_call_id="ep-call",
            called_did="+15551234567",
            reservation_seconds=60,
            metadata={"ingress_endpoint": endpoint},
        )
    )
    return decision, conn


@pytest.mark.asyncio
async def test_other_tenants_line_on_the_same_carrier_host_is_admitted(monkeypatch):
    row = {"tenant_id": OTHER_TENANT, "sip_domain": CARRIER_HOST, "metadata": "{}"}
    d, conn = await _admit_on(monkeypatch, row)
    assert d.allowed is True
    assert conn.trunk_lookups == [(OTHER_LINE,)]


@pytest.mark.asyncio
async def test_same_tenants_other_line_is_admitted(monkeypatch):
    row = {"tenant_id": TENANT, "sip_domain": "pbx.example.net", "metadata": None}
    d, _ = await _admit_on(monkeypatch, row)
    assert d.allowed is True


@pytest.mark.asyncio
async def test_source_host_override_is_what_identify_matches(monkeypatch):
    row = {
        "tenant_id": OTHER_TENANT,
        "sip_domain": "registrar.example.net",
        "metadata": {"source_host": CARRIER_HOST.upper() + "."},
    }
    d, _ = await _admit_on(monkeypatch, row)
    assert d.allowed is True


@pytest.mark.asyncio
async def test_other_tenants_line_on_a_different_host_is_denied(monkeypatch, caplog):
    async def no_overlap(_a, _b):
        return False

    monkeypatch.setattr(admission_module, "_hosts_resolve_together", no_overlap)
    row = {"tenant_id": OTHER_TENANT, "sip_domain": "attacker.example.org", "metadata": {}}
    d, _ = await _admit_on(monkeypatch, row)
    assert d.allowed is False
    assert any("inbound_admission_endpoint_mismatch" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_host_name_and_its_ip_count_as_the_same_carrier(monkeypatch):
    async def overlap(a, b):
        return {a, b} == {CARRIER_HOST, "144.76.17.155"}

    monkeypatch.setattr(admission_module, "_hosts_resolve_together", overlap)
    row = {"tenant_id": OTHER_TENANT, "sip_domain": "144.76.17.155", "metadata": {}}
    d, _ = await _admit_on(monkeypatch, row)
    assert d.allowed is True


@pytest.mark.asyncio
async def test_endpoint_for_a_line_with_no_row_is_denied(monkeypatch):
    d, _ = await _admit_on(monkeypatch, None)
    assert d.allowed is False
