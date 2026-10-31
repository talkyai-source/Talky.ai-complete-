"""Legacy cloud callbacks lack the supported campaign's canonical ownership."""
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.api.v1.endpoints import twilio_bridge, vonage_bridge
from app.core import prod_gate


BRIDGES = [(twilio_bridge, "TWILIO", "/twilio/media-stream"),
           (vonage_bridge, "VONAGE", "/vonage/ws-audio/synthetic-call")]


@pytest.mark.parametrize("bridge,prefix,path", BRIDGES)
@pytest.mark.parametrize("environment", ["production", " Production ", "PRODUCTION"])
@pytest.mark.parametrize("enabled", ["true", "1", "yes", "on"])
def test_production_cannot_enable_ownerless_cloud_bridge(monkeypatch, bridge, prefix, path, environment, enabled):
    monkeypatch.setenv("ENVIRONMENT", environment)
    monkeypatch.setenv(prefix + "_BRIDGE_ENABLED", enabled)
    assert not bridge._bridge_enabled()


@pytest.mark.parametrize("bridge,prefix,path", BRIDGES)
@pytest.mark.parametrize("environment", ["development", "staging", "test"])
def test_explicit_nonproduction_qualification_remains_opt_in(monkeypatch, bridge, prefix, path, environment):
    monkeypatch.setenv("ENVIRONMENT", environment)
    monkeypatch.delenv(prefix + "_BRIDGE_ENABLED", raising=False)
    assert not bridge._bridge_enabled()
    monkeypatch.setenv(prefix + "_BRIDGE_ENABLED", "true")
    assert bridge._bridge_enabled()


@pytest.mark.parametrize("bridge,prefix,path", BRIDGES)
def test_mounted_production_http_routes_are_unavailable_before_auth_or_effects(monkeypatch, bridge, prefix, path):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv(prefix + "_BRIDGE_ENABLED", "true")
    verifier = Mock(side_effect=AssertionError("Production must not reach provider verification"))
    monkeypatch.setattr(bridge, "verify_" + prefix.lower() + "_signature", verifier)
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "synthetic-only-auth-token")
    monkeypatch.setenv("VONAGE_SIGNATURE_SECRET", "synthetic-only-signature-secret")
    app = FastAPI()
    app.include_router(bridge.router, prefix="/api/v1")
    with TestClient(app) as client:
        for route in ("answer", "event"):
            response = client.post(f"/api/v1/{prefix.lower()}/{route}", json={})
            assert response.status_code == 404
    verifier.assert_not_called()


@pytest.mark.parametrize("bridge,prefix,path", BRIDGES)
def test_mounted_production_websocket_rejects_even_valid_local_token(monkeypatch, bridge, prefix, path):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv(prefix + "_BRIDGE_ENABLED", "true")
    monkeypatch.setenv(prefix + "_WS_TOKEN_SECRET", "synthetic-cloud-token-secret")
    if prefix == "TWILIO":
        token = bridge._mint_ws_token(to_number="+15550000000", call_sid="synthetic-call")
    else:
        token = bridge._mint_ws_token(to_number="+15550000000", call_uuid="synthetic-call")
    assert token
    factory = Mock(side_effect=AssertionError("Production must not reach session factory"))
    monkeypatch.setattr(bridge, "_get_orchestrator", factory)
    app = FastAPI()
    app.include_router(bridge.router, prefix="/api/v1")
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect(f"/api/v1{path}?token={token}"):
                pass
        assert exc.value.code == 1008
    factory.assert_not_called()


@pytest.mark.parametrize("prefix", ["TWILIO", "VONAGE"])
def test_production_startup_names_the_unqualified_enabled_bridge(monkeypatch, prefix):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("TWILIO_BRIDGE_ENABLED", raising=False)
    monkeypatch.delenv("VONAGE_BRIDGE_ENABLED", raising=False)
    monkeypatch.setenv(prefix + "_BRIDGE_ENABLED", "true")
    # Exercise the actual startup collector while isolating unrelated accounts.
    for check in ("_check_guard_bypass_flags", "_check_pbx_default_credentials",
                  "_check_required_secrets", "_check_caller_id_enforcement",
                  "_check_inbound_strict_routing"):
        monkeypatch.setattr(prod_gate, check, lambda: [])
    with pytest.raises(prod_gate.ProductionGateError, match=prefix + "_BRIDGE_ENABLED"):
        prod_gate.enforce_production_gate()


@pytest.mark.parametrize("value", [None, "", "0", "false", "no", "off"])
def test_disabled_cloud_flags_add_no_staging_only_violation(monkeypatch, value):
    for name in ("TWILIO_BRIDGE_ENABLED", "VONAGE_BRIDGE_ENABLED"):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    assert not [v for v in prod_gate._check_staging_only_flags() if "BRIDGE_ENABLED" in v.detail]
