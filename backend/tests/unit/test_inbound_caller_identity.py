"""F5-10: synthetic carrier assertions, actual parser/adapter/admission seams.

No live SIP, provider, database or verifier is used. The protected lifecycle
metadata handoff is deliberately not simulated as delivered end-to-end.
"""
from __future__ import annotations

import base64
import importlib
import importlib.util
import json
import logging
import os
import sys
from pathlib import Path

import pytest


ANI = "+15550001000"
ENDPOINT = "qa-platform-carrier"
TRUST_ENV = "TALKY_CALLER_ID_TRUSTED_ENDPOINTS"
PAI = f'"Synthetic Person" <sip:{ANI}@carrier.example;verstat=TN-Validation-Passed>'


@pytest.fixture(autouse=True)
def synthetic_settings(monkeypatch):
    from app.core import config, dotenv_compat
    import dotenv
    monkeypatch.setattr(dotenv_compat, "load_dotenv", lambda *a, **k: False)
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: False)
    monkeypatch.delenv(TRUST_ENV, raising=False)
    # model_construct does not load environment files. Only this fixture-owned
    # synthetic trust input is read; all remaining configuration uses defaults.
    monkeypatch.setattr(config, "get_settings", lambda: config.Settings.model_construct(
        inbound_caller_id_trusted_endpoints=os.getenv(TRUST_ENV, ""), environment="test"))


@pytest.fixture
def identity():
    return importlib.import_module("app.domain.services.caller_identity")


def headers(pai=PAI, attestation="A"):
    return {"pai": pai, "attestation": attestation}


def parse(identity, raw=None, *, ani=ANI, endpoint=ENDPOINT):
    return identity.caller_identity_from_metadata(
        {"caller_identity_headers": headers() if raw is None else raw},
        caller_ani=ani, ingress_endpoint=endpoint,
    )


def test_no_operator_trust_contract_means_unverified(identity):
    assert parse(identity)["caller_id_verified"] is False


def test_trusted_full_attestation_requires_matching_identity(identity, monkeypatch):
    monkeypatch.setenv(TRUST_ENV, ENDPOINT)
    result = parse(identity)
    assert result["caller_id_verified"] is True
    assert result["attestation"] == "A" and result["source"] == "trusted_carrier"
    assert result["verstat"] == "TN-Validation-Passed" and result["version"] == 1
    assert ANI not in json.dumps(result) and "Synthetic Person" not in json.dumps(result)


@pytest.mark.parametrize("attestation", ["B", "C", None, "", "unknown"])
def test_not_full_attestation_never_proves_number_control(identity, monkeypatch, attestation):
    monkeypatch.setenv(TRUST_ENV, ENDPOINT)
    assert parse(identity, headers(attestation=attestation))["caller_id_verified"] is False


@pytest.mark.parametrize("pai", [
    f"<sip:{ANI}@carrier.example>",
    PAI.replace("TN-Validation-Passed", "TN-Validation-Failed"),
    PAI.replace("TN-Validation-Passed", "No-TN-Validation"),
    PAI.replace(ANI, "+15550002000"),
    f'"verstat=TN-Validation-Passed" <sip:{ANI}@carrier.example>',
    PAI + ",<sip:+15550002000@carrier.example>",
    PAI.replace(";verstat=", ";verstat=TN-Validation-Failed;verstat="),
    PAI + "\r\nX-Forged: yes", PAI + "\x00", "x" * 4097, "\ud800", 42,
], ids=["missing-verstat", "failed", "not-validated", "different-number", "display-name-forgery",
        "multiple-identities", "duplicate-verstat", "newline", "nul", "oversized", "surrogate", "nonstring"])
def test_malformed_ambiguous_or_failed_assertions_fail_closed(identity, monkeypatch, pai):
    monkeypatch.setenv(TRUST_ENV, ENDPOINT)
    assert parse(identity, headers(pai=pai))["caller_id_verified"] is False


@pytest.mark.parametrize("ani", [None, "anonymous", "restricted", "+15550002000"])
def test_asserted_identity_cannot_unmask_or_replace_caller(identity, monkeypatch, ani):
    monkeypatch.setenv(TRUST_ENV, ENDPOINT)
    assert parse(identity, ani=ani)["caller_id_verified"] is False


@pytest.mark.parametrize("configured,endpoint", [
    ("*", ENDPOINT), (ENDPOINT, "anonymous"), (ENDPOINT, "other-carrier"),
    ("trunk-11111111-1111-1111-1111-111111111111", "trunk-11111111-1111-1111-1111-111111111111"),
])
def test_untrusted_or_tenant_managed_endpoint_never_verifies(identity, monkeypatch, configured, endpoint):
    monkeypatch.setenv(TRUST_ENV, configured)
    assert parse(identity, endpoint=endpoint)["caller_id_verified"] is False


def _adapter():
    from app.infrastructure.telephony.asterisk_adapter import AsteriskAdapter
    adapter = object.__new__(AsteriskAdapter)
    adapter._inbound_debug_dumped = False
    return adapter


def _encoded(name, value):
    return name + "=" + base64.b64encode(value.encode()).decode()


def _event(extra):
    return {"args": ["inbound", "+15551234567", "from-talky-inbound", "legacy-agent-slot", *extra],
            "channel": {"name": "PJSIP/" + ENDPOINT + "-00000001", "caller": {"number": ANI}}}


def test_adapter_retains_named_headers_without_changing_legacy_route_or_logging_identity(caplog):
    event = _event([_encoded("cid_pai_b64", PAI), _encoded("cid_attestation_b64", "A")])
    with caplog.at_level(logging.INFO):
        meta = _adapter()._extract_inbound_meta(event)
    assert meta["caller_identity_headers"] == headers()
    assert meta["called_did"] == "+15551234567" and meta["context"] == "from-talky-inbound"
    assert meta["caller_number"] == ANI
    assert PAI not in caplog.text and ANI not in caplog.text
    assert meta.get("caller_id_verified") is not True, "Adapter transport is not a trust decision"


@pytest.mark.parametrize("extra", [[], ["cid_pai_b64=%%%"],
    [_encoded("cid_pai_b64", PAI), _encoded("cid_pai_b64", PAI)],
    [_encoded("cid_pai_b64", "x" * 4097)],
])
def test_adapter_missing_or_invalid_envelope_never_manufactures_verified_identity(extra):
    meta = _adapter()._extract_inbound_meta(_event(extra))
    assert meta["caller_identity_headers"] == {}
    assert meta.get("caller_id_verified") is not True


def test_managed_dialplan_transports_encoded_pai_and_attestation_without_interpolating_raw_headers(monkeypatch):
    path = Path(__file__).resolve().parents[2] / "scripts/reconcile_pjsip_configs.py"
    spec = importlib.util.spec_from_file_location("qa_identity_reconcile", path)
    reconcile = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, reconcile)
    spec.loader.exec_module(reconcile)
    template = reconcile.DEFAULT_DIALPLAN_TEMPLATE.read_text(encoding="utf-8")
    route = reconcile.InboundRoute("940003", "+15551234567", "t", "s", "a")
    rendered = reconcile.render_inbound_dialplan([route], template=template).decode()
    stasis = [line for line in rendered.splitlines() if "Stasis(talky_ai,inbound," in line]
    assert len(stasis) == 2  # generated route and preserved catch-all
    for line in stasis:
        assert "cid_pai_b64=${BASE64_ENCODE(${PJSIP_HEADER(read,P-Asserted-Identity)})}" in line
        assert "cid_attestation_b64=${BASE64_ENCODE(${PJSIP_HEADER(read,P-Attestation-Indicator)})}" in line


@pytest.mark.parametrize("supplied,verified", [(False, False), (True, True)])
async def test_actual_admission_persists_safe_identity_in_existing_call_snapshot(monkeypatch, supplied, verified):
    from tests.unit import test_inbound_admission as old
    conn = old._AllowedConn()
    monkeypatch.setenv(TRUST_ENV, ENDPOINT)
    monkeypatch.setattr(old.admission_module, "acquire_with_tenant", lambda *_: old._acquire(conn))
    metadata = {"ingress_endpoint": ENDPOINT}
    if supplied:
        metadata["caller_identity_headers"] = headers()
    decision = await old.InboundAdmissionService(object(), old._Limiter()).admit(old.InboundAdmissionRequest(
        provider="asterisk", provider_call_id="synthetic-call-id", called_did="+15551234567",
        caller_ani=ANI, metadata=metadata, reservation_seconds=90,
    ))
    assert decision.allowed is True
    stored = json.loads(conn.call_insert_args[15])
    assert stored["caller_identity"]["caller_id_verified"] is verified
    assert decision.config_snapshot["caller_identity"] == stored["caller_identity"]
    assert "Synthetic Person" not in json.dumps(stored) and PAI not in json.dumps(stored)
    assert conn.call_insert_args[1] == old.TENANT


def test_missing_protected_handoff_is_explicitly_default_unverified(identity, monkeypatch):
    monkeypatch.setenv(TRUST_ENV, ENDPOINT)
    result = identity.caller_identity_from_metadata(
        {"ingress_endpoint": ENDPOINT, "linked_id": "synthetic"},
        caller_ani=ANI, ingress_endpoint=ENDPOINT,
    )
    assert result["caller_id_verified"] is False and result["attestation"] is None
