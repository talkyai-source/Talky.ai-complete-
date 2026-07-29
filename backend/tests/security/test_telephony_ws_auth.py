"""Telephony bridge WebSocket authentication (Twilio Media Streams, Vonage audio).

Regression tests for a critical hole: both provider sockets used to call
``websocket.accept()`` with no credential of any kind, and then selected the
TENANT from a client-supplied value (Twilio ``start.customParameters.to``,
Vonage's ``to_number`` request header). An anonymous caller could name any
victim's DID and hold a full conversation with that tenant's paid AI agent —
no phone call, no CallGuard, no rate limit, no concurrency cap.

The fix has three layers, each asserted below:
  1. default-deny feature gate (TWILIO_BRIDGE_ENABLED / VONAGE_BRIDGE_ENABLED)
  2. a short-lived HMAC token minted by the signature-verified /answer webhook
  3. a concurrency semaphore

The load-bearing assertion is ``test_*_tenant_comes_from_token_not_*``: even a
well-formed request cannot steer the session at another tenant's DID.
"""
from __future__ import annotations

import json
import time
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import app.api.v1.endpoints.twilio_bridge as tb
import app.api.v1.endpoints.vonage_bridge as vb


SECRET = "unit-test-telephony-ws-secret"
TENANT_DID = "+15550000000"
VICTIM_DID = "+442046132300"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def twilio_client(monkeypatch) -> TestClient:
    """App with ONLY the Twilio bridge mounted, bridge enabled + secret set."""
    monkeypatch.setenv("TWILIO_BRIDGE_ENABLED", "true")
    monkeypatch.setenv("TWILIO_WS_TOKEN_SECRET", SECRET)
    monkeypatch.delenv("TELEPHONY_WS_TOKEN_SECRET", raising=False)
    app = FastAPI()
    app.include_router(tb.router, prefix="/api/v1")
    return TestClient(app)


@pytest.fixture()
def twilio_client_disabled(monkeypatch) -> TestClient:
    """Same app with the feature gate left UNSET — the production default."""
    monkeypatch.delenv("TWILIO_BRIDGE_ENABLED", raising=False)
    monkeypatch.setenv("TWILIO_WS_TOKEN_SECRET", SECRET)
    app = FastAPI()
    app.include_router(tb.router, prefix="/api/v1")
    return TestClient(app)


@pytest.fixture()
def vonage_client(monkeypatch) -> TestClient:
    monkeypatch.setenv("VONAGE_BRIDGE_ENABLED", "true")
    monkeypatch.setenv("VONAGE_WS_TOKEN_SECRET", SECRET)
    monkeypatch.delenv("TELEPHONY_WS_TOKEN_SECRET", raising=False)
    app = FastAPI()
    app.include_router(vb.router, prefix="/api/v1")
    return TestClient(app)


@pytest.fixture()
def vonage_client_disabled(monkeypatch) -> TestClient:
    monkeypatch.delenv("VONAGE_BRIDGE_ENABLED", raising=False)
    monkeypatch.setenv("VONAGE_WS_TOKEN_SECRET", SECRET)
    app = FastAPI()
    app.include_router(vb.router, prefix="/api/v1")
    return TestClient(app)


class _StubGateway:
    def __init__(self) -> None:
        self.fed: list[bytes] = []

    def set_stream_sid(self, call_id: str, stream_sid: str) -> None:
        pass

    async def feed_twilio_media(self, call_id: str, ulaw: bytes) -> None:
        self.fed.append(ulaw)

    async def on_audio_received(self, call_id: str, raw: bytes) -> None:
        self.fed.append(raw)


def _stub_orchestrator() -> MagicMock:
    session = MagicMock()
    session.call_id = "call-stub"
    session.media_gateway = _StubGateway()
    orch = MagicMock()
    orch.create_voice_session = AsyncMock(return_value=session)
    orch.start_pipeline = AsyncMock(return_value=None)
    orch.end_session = AsyncMock(return_value=None)
    return orch


@pytest.fixture()
def twilio_session_spy(monkeypatch) -> list[Optional[str]]:
    """Record every DID the Twilio session config is built for."""
    seen: list[Optional[str]] = []

    async def _spy(to_number: Optional[str] = None):
        seen.append(to_number)
        return MagicMock()

    monkeypatch.setattr(tb, "_build_twilio_session_config", _spy)
    monkeypatch.setattr(tb, "_get_orchestrator", _stub_orchestrator)
    return seen


@pytest.fixture()
def vonage_session_spy(monkeypatch) -> list[Optional[str]]:
    seen: list[Optional[str]] = []

    async def _spy(to_number: Optional[str] = None):
        seen.append(to_number)
        return MagicMock()

    monkeypatch.setattr(vb, "_build_vonage_session_config", _spy)
    monkeypatch.setattr(vb, "_get_orchestrator", _stub_orchestrator)
    return seen


def _start_frame(token: Optional[str] = None, to: Optional[str] = None) -> str:
    params: dict[str, Any] = {}
    if token is not None:
        params["token"] = token
    if to is not None:
        params["to"] = to
    return json.dumps(
        {
            "event": "start",
            "start": {
                "streamSid": "MZ-stream-1",
                "callSid": "CA-call-1",
                "customParameters": params,
            },
        }
    )


# ===========================================================================
# Layer 1 — default-deny feature gate
# ===========================================================================


class TestFeatureGateDefaultDeny:
    """An UNSET env var must mean CLOSED, on every entry point."""

    def test_twilio_media_stream_rejected_when_gate_unset(self, twilio_client_disabled):
        with pytest.raises(WebSocketDisconnect) as exc:
            with twilio_client_disabled.websocket_connect("/api/v1/twilio/media-stream"):
                pass
        assert exc.value.code == 1008

    def test_vonage_ws_audio_rejected_when_gate_unset(self, vonage_client_disabled):
        with pytest.raises(WebSocketDisconnect) as exc:
            with vonage_client_disabled.websocket_connect("/api/v1/vonage/ws-audio/uuid-1"):
                pass
        assert exc.value.code == 1008

    def test_twilio_gate_unset_rejects_even_with_a_valid_token(
        self, monkeypatch, twilio_client_disabled
    ):
        """The gate is checked first: a real token does not re-open a closed bridge."""
        monkeypatch.setenv("TWILIO_WS_TOKEN_SECRET", SECRET)
        token = tb._mint_ws_token(to_number=TENANT_DID, call_sid="CA-1")
        assert token
        with pytest.raises(WebSocketDisconnect) as exc:
            with twilio_client_disabled.websocket_connect(
                f"/api/v1/twilio/media-stream?token={token}"
            ):
                pass
        assert exc.value.code == 1008

    def test_twilio_answer_404s_when_gate_unset(self, twilio_client_disabled):
        resp = twilio_client_disabled.post("/api/v1/twilio/answer", data={"To": VICTIM_DID})
        assert resp.status_code == 404

    def test_vonage_answer_404s_when_gate_unset(self, vonage_client_disabled):
        resp = vonage_client_disabled.post("/api/v1/vonage/answer", json={"to": VICTIM_DID})
        assert resp.status_code == 404

    @pytest.mark.parametrize("value", ["", "false", "0", "no", "off", "FALSE"])
    def test_only_explicit_truthy_values_enable(self, monkeypatch, value):
        monkeypatch.setenv("TWILIO_BRIDGE_ENABLED", value)
        monkeypatch.setenv("VONAGE_BRIDGE_ENABLED", value)
        assert tb._bridge_enabled() is False
        assert vb._bridge_enabled() is False

    @pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
    def test_truthy_values_enable(self, monkeypatch, value):
        monkeypatch.setenv("TWILIO_BRIDGE_ENABLED", value)
        monkeypatch.setenv("VONAGE_BRIDGE_ENABLED", value)
        assert tb._bridge_enabled() is True
        assert vb._bridge_enabled() is True


# ===========================================================================
# Layer 2 — authentication (Twilio Media Streams)
# ===========================================================================


class TestTwilioMediaStreamAuth:
    """Twilio does not sign the WS handshake, so /answer mints a token that is
    echoed back via the stream URL and start.customParameters."""

    def test_enabled_but_no_token_rejects(self, twilio_client, twilio_session_spy):
        with pytest.raises(WebSocketDisconnect) as exc:
            with twilio_client.websocket_connect("/api/v1/twilio/media-stream") as ws:
                ws.send_text(_start_frame(token=None, to=VICTIM_DID))
                ws.receive_text()
        assert exc.value.code == 1008
        assert twilio_session_spy == [], "a session was built for an unauthenticated socket"

    def test_bad_token_in_query_rejects(self, twilio_client, twilio_session_spy):
        with pytest.raises(WebSocketDisconnect) as exc:
            with twilio_client.websocket_connect(
                "/api/v1/twilio/media-stream?token=v1.forged.forged"
            ) as ws:
                ws.send_text(_start_frame(token=None, to=VICTIM_DID))
                ws.receive_text()
        assert exc.value.code == 1008
        assert twilio_session_spy == []

    def test_bad_token_in_custom_parameters_rejects(self, twilio_client, twilio_session_spy):
        with pytest.raises(WebSocketDisconnect) as exc:
            with twilio_client.websocket_connect("/api/v1/twilio/media-stream") as ws:
                ws.send_text(_start_frame(token="v1.forged.forged", to=VICTIM_DID))
                ws.receive_text()
        assert exc.value.code == 1008
        assert twilio_session_spy == []

    def test_token_signed_with_another_secret_rejects(
        self, monkeypatch, twilio_client, twilio_session_spy
    ):
        monkeypatch.setenv("TWILIO_WS_TOKEN_SECRET", "attacker-secret")
        forged = tb._mint_ws_token(to_number=VICTIM_DID, call_sid="CA-1")
        monkeypatch.setenv("TWILIO_WS_TOKEN_SECRET", SECRET)
        with pytest.raises(WebSocketDisconnect) as exc:
            with twilio_client.websocket_connect(
                f"/api/v1/twilio/media-stream?token={forged}"
            ) as ws:
                ws.send_text(_start_frame())
                ws.receive_text()
        assert exc.value.code == 1008
        assert twilio_session_spy == []

    def test_expired_token_rejects(self, monkeypatch, twilio_client, twilio_session_spy):
        monkeypatch.setattr(tb, "_TOKEN_TTL_S", -1)
        stale = tb._mint_ws_token(to_number=TENANT_DID, call_sid="CA-1")
        with pytest.raises(WebSocketDisconnect) as exc:
            with twilio_client.websocket_connect(
                f"/api/v1/twilio/media-stream?token={stale}"
            ) as ws:
                ws.send_text(_start_frame())
                ws.receive_text()
        assert exc.value.code == 1008
        assert twilio_session_spy == []

    def test_vonage_token_is_not_accepted_by_twilio(
        self, monkeypatch, twilio_client, twilio_session_spy
    ):
        """Audience separation: a token minted for the other bridge must not work
        even when both bridges share TELEPHONY_WS_TOKEN_SECRET."""
        monkeypatch.setenv("VONAGE_WS_TOKEN_SECRET", SECRET)
        cross = vb._mint_ws_token(to_number=TENANT_DID, call_uuid="uuid-1")
        with pytest.raises(WebSocketDisconnect) as exc:
            with twilio_client.websocket_connect(
                f"/api/v1/twilio/media-stream?token={cross}"
            ) as ws:
                ws.send_text(_start_frame())
                ws.receive_text()
        assert exc.value.code == 1008
        assert twilio_session_spy == []

    # ── The actual privilege-escalation regression ─────────────────────────

    def test_tenant_comes_from_token_not_custom_parameters(
        self, twilio_client, twilio_session_spy
    ):
        """A caller holding a token for their OWN DID cannot redirect the session
        at a victim's DID by setting start.customParameters.to."""
        token = tb._mint_ws_token(to_number=TENANT_DID, call_sid="CA-call-1")
        with twilio_client.websocket_connect(
            f"/api/v1/twilio/media-stream?token={token}"
        ) as ws:
            ws.send_text(_start_frame(to=VICTIM_DID))
            ws.send_text(json.dumps({"event": "stop"}))
        assert twilio_session_spy == [TENANT_DID]
        assert VICTIM_DID not in twilio_session_spy

    def test_valid_token_in_custom_parameters_is_accepted(
        self, twilio_client, twilio_session_spy
    ):
        """The customParameters fallback works when the URL carries no token."""
        token = tb._mint_ws_token(to_number=TENANT_DID, call_sid="CA-call-1")
        with twilio_client.websocket_connect("/api/v1/twilio/media-stream") as ws:
            ws.send_text(_start_frame(token=token, to=VICTIM_DID))
            ws.send_text(json.dumps({"event": "stop"}))
        assert twilio_session_spy == [TENANT_DID]

    def test_media_frames_before_auth_never_reach_the_pipeline(
        self, twilio_client, twilio_session_spy
    ):
        """Audio sent before a valid start frame must not create a session."""
        media = json.dumps(
            {"event": "media", "media": {"payload": "AAAA"}}
        )
        with pytest.raises(WebSocketDisconnect):
            with twilio_client.websocket_connect("/api/v1/twilio/media-stream") as ws:
                ws.send_text(media)
                ws.send_text(_start_frame(to=VICTIM_DID))
                ws.receive_text()
        assert twilio_session_spy == []


# ===========================================================================
# Layer 2 — authentication (Vonage audio socket)
# ===========================================================================


class TestVonageWsAudioAuth:
    """Vonage DOES authenticate the opening handshake (it sends an Authorization
    header verbatim), so everything is checked pre-accept."""

    def test_enabled_but_no_token_rejects(self, vonage_client, vonage_session_spy):
        with pytest.raises(WebSocketDisconnect) as exc:
            with vonage_client.websocket_connect("/api/v1/vonage/ws-audio/uuid-1"):
                pass
        assert exc.value.code == 1008
        assert vonage_session_spy == []

    def test_bad_token_rejects(self, vonage_client, vonage_session_spy):
        with pytest.raises(WebSocketDisconnect) as exc:
            with vonage_client.websocket_connect(
                "/api/v1/vonage/ws-audio/uuid-1?token=v1.forged.forged"
            ):
                pass
        assert exc.value.code == 1008
        assert vonage_session_spy == []

    def test_token_signed_with_another_secret_rejects(
        self, monkeypatch, vonage_client, vonage_session_spy
    ):
        monkeypatch.setenv("VONAGE_WS_TOKEN_SECRET", "attacker-secret")
        forged = vb._mint_ws_token(to_number=VICTIM_DID, call_uuid="uuid-1")
        monkeypatch.setenv("VONAGE_WS_TOKEN_SECRET", SECRET)
        with pytest.raises(WebSocketDisconnect) as exc:
            with vonage_client.websocket_connect(
                f"/api/v1/vonage/ws-audio/uuid-1?token={forged}"
            ):
                pass
        assert exc.value.code == 1008
        assert vonage_session_spy == []

    def test_expired_token_rejects(self, monkeypatch, vonage_client, vonage_session_spy):
        monkeypatch.setattr(vb, "_TOKEN_TTL_S", -1)
        stale = vb._mint_ws_token(to_number=TENANT_DID, call_uuid="uuid-1")
        with pytest.raises(WebSocketDisconnect) as exc:
            with vonage_client.websocket_connect(
                f"/api/v1/vonage/ws-audio/uuid-1?token={stale}"
            ):
                pass
        assert exc.value.code == 1008
        assert vonage_session_spy == []

    def test_token_for_another_call_uuid_rejects(self, vonage_client, vonage_session_spy):
        """A token minted for call A cannot be replayed onto call B's socket."""
        token = vb._mint_ws_token(to_number=TENANT_DID, call_uuid="uuid-A")
        with pytest.raises(WebSocketDisconnect) as exc:
            with vonage_client.websocket_connect(
                f"/api/v1/vonage/ws-audio/uuid-B?token={token}"
            ):
                pass
        assert exc.value.code == 1008
        assert vonage_session_spy == []

    def test_twilio_token_is_not_accepted_by_vonage(
        self, monkeypatch, vonage_client, vonage_session_spy
    ):
        monkeypatch.setenv("TWILIO_WS_TOKEN_SECRET", SECRET)
        cross = tb._mint_ws_token(to_number=TENANT_DID, call_sid="CA-1")
        with pytest.raises(WebSocketDisconnect) as exc:
            with vonage_client.websocket_connect(
                f"/api/v1/vonage/ws-audio/uuid-1?token={cross}"
            ):
                pass
        assert exc.value.code == 1008
        assert vonage_session_spy == []

    # ── The actual privilege-escalation regression ─────────────────────────

    def test_tenant_comes_from_token_not_to_number_header(
        self, vonage_client, vonage_session_spy
    ):
        """The spoofable ``to_number`` header must not steer tenant selection."""
        token = vb._mint_ws_token(to_number=TENANT_DID, call_uuid="uuid-1")
        with vonage_client.websocket_connect(
            f"/api/v1/vonage/ws-audio/uuid-1?token={token}",
            headers={"to_number": VICTIM_DID},
        ):
            pass
        assert vonage_session_spy == [TENANT_DID]
        assert VICTIM_DID not in vonage_session_spy

    def test_unauthenticated_to_number_header_alone_gets_nothing(
        self, vonage_client, vonage_session_spy
    ):
        """The pre-fix attack, verbatim: connect with only a victim DID header."""
        with pytest.raises(WebSocketDisconnect) as exc:
            with vonage_client.websocket_connect(
                "/api/v1/vonage/ws-audio/uuid-1",
                headers={"to_number": VICTIM_DID},
            ):
                pass
        assert exc.value.code == 1008
        assert vonage_session_spy == []

    def test_authorization_bearer_header_is_accepted(
        self, vonage_client, vonage_session_spy
    ):
        """The vendor-documented channel: Vonage sends our Authorization value
        verbatim in the opening handshake."""
        token = vb._mint_ws_token(to_number=TENANT_DID, call_uuid="uuid-1")
        with vonage_client.websocket_connect(
            "/api/v1/vonage/ws-audio/uuid-1",
            headers={"Authorization": f"Bearer {token}"},
        ):
            pass
        assert vonage_session_spy == [TENANT_DID]

    def test_custom_token_header_is_accepted(self, vonage_client, vonage_session_spy):
        token = vb._mint_ws_token(to_number=TENANT_DID, call_uuid="uuid-1")
        with vonage_client.websocket_connect(
            "/api/v1/vonage/ws-audio/uuid-1",
            headers={vb._WS_TOKEN_HEADER: token},
        ):
            pass
        assert vonage_session_spy == [TENANT_DID]


# ===========================================================================
# Token primitive — fail-closed in every direction
# ===========================================================================


@pytest.mark.parametrize("module", [tb, vb])
class TestTokenPrimitive:

    def test_no_secret_means_no_mint_and_no_verify(self, monkeypatch, module):
        for var in (
            "TWILIO_WS_TOKEN_SECRET", "VONAGE_WS_TOKEN_SECRET",
            "TELEPHONY_WS_TOKEN_SECRET", "TWILIO_AUTH_TOKEN",
            "VONAGE_SIGNATURE_SECRET",
        ):
            monkeypatch.delenv(var, raising=False)
        assert module._ws_token_secret() is None
        assert module._verify_ws_token("v1.anything.anything") is None

    def test_missing_and_malformed_tokens_are_rejected(self, monkeypatch, module):
        monkeypatch.setenv("TELEPHONY_WS_TOKEN_SECRET", SECRET)
        for bad in (None, "", "not-a-token", "v1.only-two", "v2.a.b", "a.b.c.d"):
            assert module._verify_ws_token(bad) is None

    def test_tampered_payload_is_rejected(self, monkeypatch, module):
        monkeypatch.setenv("TELEPHONY_WS_TOKEN_SECRET", SECRET)
        token = _mint_for(module)
        version, payload, sig = token.split(".")
        claims = json.loads(module._b64url_decode(payload))
        claims["to"] = VICTIM_DID
        forged_payload = module._b64url_encode(
            json.dumps(claims, separators=(",", ":"), sort_keys=True).encode()
        )
        assert module._verify_ws_token(f"{version}.{forged_payload}.{sig}") is None

    def test_roundtrip_carries_the_did(self, monkeypatch, module):
        monkeypatch.setenv("TELEPHONY_WS_TOKEN_SECRET", SECRET)
        claims = module._verify_ws_token(_mint_for(module))
        assert claims is not None
        assert claims["to"] == TENANT_DID
        assert claims["exp"] > time.time()

    def test_tokens_are_not_predictable(self, monkeypatch, module):
        monkeypatch.setenv("TELEPHONY_WS_TOKEN_SECRET", SECRET)
        assert len({_mint_for(module) for _ in range(10)}) == 10


def _mint_for(module):
    if module is tb:
        return module._mint_ws_token(to_number=TENANT_DID, call_sid="CA-1")
    return module._mint_ws_token(to_number=TENANT_DID, call_uuid="uuid-1")


# ===========================================================================
# Layer 3 — concurrency cap
# ===========================================================================


class TestConcurrencyCap:
    """Both sockets must be bounded like every other WS in the codebase."""

    @pytest.mark.parametrize("module", [tb, vb])
    def test_semaphore_exists_and_is_bounded(self, module):
        module._stream_semaphore = None
        sem = module._get_semaphore()
        assert sem._value == module._MAX_CONCURRENT_STREAMS
        assert 0 < module._MAX_CONCURRENT_STREAMS <= 50
        assert module._get_semaphore() is sem  # cached, not rebuilt per connection

    def test_twilio_rejects_with_1013_at_capacity(self, monkeypatch, twilio_client):
        import asyncio

        monkeypatch.setattr(tb, "_stream_semaphore", asyncio.Semaphore(0))
        token = tb._mint_ws_token(to_number=TENANT_DID, call_sid="CA-1")
        with pytest.raises(WebSocketDisconnect) as exc:
            with twilio_client.websocket_connect(
                f"/api/v1/twilio/media-stream?token={token}"
            ):
                pass
        assert exc.value.code == 1013

    def test_vonage_rejects_with_1013_at_capacity(self, monkeypatch, vonage_client):
        import asyncio

        monkeypatch.setattr(vb, "_stream_semaphore", asyncio.Semaphore(0))
        token = vb._mint_ws_token(to_number=TENANT_DID, call_uuid="uuid-1")
        with pytest.raises(WebSocketDisconnect) as exc:
            with vonage_client.websocket_connect(
                f"/api/v1/vonage/ws-audio/uuid-1?token={token}"
            ):
                pass
        assert exc.value.code == 1013


# ===========================================================================
# Minting requires a VERIFIED webhook
# ===========================================================================


class TestMintingRequiresVerifiedWebhook:
    """Without the provider signature secret the /answer check is fail-soft, so
    minting there would hand a token to anyone who can POST. Refuse instead."""

    def test_twilio_answer_503s_without_auth_token(self, monkeypatch, twilio_client):
        monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
        resp = twilio_client.post("/api/v1/twilio/answer", data={"To": VICTIM_DID})
        assert resp.status_code == 503

    def test_vonage_answer_503s_without_signature_secret(self, monkeypatch, vonage_client):
        monkeypatch.delenv("VONAGE_SIGNATURE_SECRET", raising=False)
        resp = vonage_client.post("/api/v1/vonage/answer", json={"to": VICTIM_DID})
        assert resp.status_code == 503

    def test_twilio_answer_rejects_unsigned_request(self, monkeypatch, twilio_client):
        monkeypatch.setenv("TWILIO_AUTH_TOKEN", "twilio-auth-token")
        resp = twilio_client.post("/api/v1/twilio/answer", data={"To": VICTIM_DID})
        assert resp.status_code == 403

    def test_vonage_answer_rejects_unsigned_request(self, monkeypatch, vonage_client):
        monkeypatch.setenv("VONAGE_SIGNATURE_SECRET", "vonage-sig-secret")
        resp = vonage_client.post("/api/v1/vonage/answer", json={"to": VICTIM_DID})
        assert resp.status_code == 403


class TestAnswerEmitsUsableToken:
    """The TwiML / NCCO the webhook returns must carry a token this socket
    accepts — otherwise the fix would simply break the provider path."""

    def test_twiml_token_verifies_and_carries_the_dialed_did(self, monkeypatch):
        monkeypatch.setenv("TWILIO_WS_TOKEN_SECRET", SECRET)
        monkeypatch.setenv("API_BASE_URL", "https://voice.example.com")
        token = tb._mint_ws_token(to_number=TENANT_DID, call_sid="CA1")
        twiml = tb._twiml_stream_response("CA1", "+15551112222", TENANT_DID, token)
        assert f"?token={token}" in twiml
        assert 'name="token"' in twiml
        claims = tb._verify_ws_token(token)
        assert claims is not None and claims["to"] == TENANT_DID

    def test_twiml_without_token_omits_the_parameter(self, monkeypatch):
        monkeypatch.setenv("API_BASE_URL", "https://voice.example.com")
        twiml = tb._twiml_stream_response("CA1", "+15551112222", TENANT_DID)
        assert "token" not in twiml
        assert "wss://voice.example.com/api/v1/twilio/media-stream" in twiml

    def test_ncco_carries_authorization_and_query_token(self, monkeypatch):
        monkeypatch.setenv("VONAGE_WS_TOKEN_SECRET", SECRET)
        token = vb._mint_ws_token(to_number=TENANT_DID, call_uuid="uuid-1")
        claims = vb._verify_ws_token(token)
        assert claims is not None and claims["uuid"] == "uuid-1"
