from __future__ import annotations

import pytest

from app.infrastructure.telephony.asterisk_adapter import AsteriskAdapter


def test_gateway_session_digest_is_deterministic_and_covers_configuration():
    payload = {
        "session_id": "asterisk-abc",
        "listen_ip": "127.0.0.1",
        "listen_port": 32000,
        "remote_ip": "127.0.0.1",
        "remote_port": 40000,
        "codec": "pcmu",
        "ptime_ms": 20,
    }

    first = AsteriskAdapter._seal_gateway_session_payload(payload)
    reordered = AsteriskAdapter._seal_gateway_session_payload(dict(reversed(list(payload.items()))))
    changed = AsteriskAdapter._seal_gateway_session_payload({**payload, "remote_port": 40002})

    assert first["config_digest"] == reordered["config_digest"]
    assert len(first["config_digest"]) == 64
    assert first["config_digest"] != changed["config_digest"]
    assert "config_digest" not in payload, "the caller's payload must not be mutated"


def test_gateway_health_contract_requires_protocol_v2_pcmu_and_callback_v2():
    compatible = {
        "status": "ok",
        "io_loop_healthy": True,
        "protocol_version": 2,
        "codecs": ["pcmu"],
        "callback_protocol_versions": [2],
    }
    assert AsteriskAdapter._gateway_health_payload_is_compatible(compatible)

    for broken in (
        {**compatible, "status": "degraded"},
        {**compatible, "io_loop_healthy": False},
        {**compatible, "protocol_version": 1},
        {**compatible, "codecs": ["opus"]},
        {**compatible, "callback_protocol_versions": [1]},
        {},
        None,
    ):
        assert not AsteriskAdapter._gateway_health_payload_is_compatible(broken)


@pytest.mark.asyncio
async def test_start_gateway_session_requires_matching_protocol_v2_ack_in_production(monkeypatch):
    adapter = AsteriskAdapter()
    calls: list[tuple] = []

    async def fake_gateway(method, path, payload=None, ok=(200,)):
        calls.append((method, path, payload, ok))
        return {
            "status": "started",
            "protocol_version": 2,
            "codec": payload["codec"],
            "session_id": payload["session_id"],
            "config_digest": payload["config_digest"],
        }

    adapter._gateway = fake_gateway  # type: ignore[assignment]
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("VOICE_GATEWAY_REQUIRE_CONFIG_ACK", raising=False)
    payload = {
        "session_id": "asterisk-proof",
        "listen_ip": "127.0.0.1",
        "listen_port": 32000,
        "remote_ip": "127.0.0.1",
        "remote_port": 40000,
        "codec": "pcmu",
        "ptime_ms": 20,
    }

    ack = await adapter._start_gateway_session(payload)

    assert ack["status"] == "started"
    assert calls[0][0:2] == ("POST", "/v1/sessions/start")
    assert calls[0][3] == (200,), "409 must not be treated as an unverified success"
    assert calls[0][2]["config_digest"] == ack["config_digest"]


@pytest.mark.asyncio
async def test_start_gateway_session_rejects_mismatched_digest_ack(monkeypatch):
    adapter = AsteriskAdapter()

    async def fake_gateway(method, path, payload=None, ok=(200,)):
        return {
            "status": "already_exists",
            "protocol_version": 2,
            "codec": payload["codec"],
            "session_id": payload["session_id"],
            "config_digest": "0" * 64,
        }

    adapter._gateway = fake_gateway  # type: ignore[assignment]
    monkeypatch.setenv("ENVIRONMENT", "production")

    with pytest.raises(RuntimeError, match="config_digest mismatch"):
        await adapter._start_gateway_session(
            {
                "session_id": "asterisk-conflict",
                "listen_ip": "127.0.0.1",
                "listen_port": 32000,
                "remote_ip": "127.0.0.1",
                "remote_port": 40000,
                "codec": "pcmu",
                "ptime_ms": 20,
            }
        )


@pytest.mark.asyncio
async def test_start_gateway_session_rejects_empty_ack_when_required(monkeypatch):
    adapter = AsteriskAdapter()

    async def fake_gateway(method, path, payload=None, ok=(200,)):
        return {}

    adapter._gateway = fake_gateway  # type: ignore[assignment]
    monkeypatch.setenv("VOICE_GATEWAY_REQUIRE_CONFIG_ACK", "true")

    with pytest.raises(RuntimeError, match="no verifiable acknowledgement"):
        await adapter._start_gateway_session(
            {
                "session_id": "asterisk-no-ack",
                "listen_ip": "127.0.0.1",
                "listen_port": 32000,
                "remote_ip": "127.0.0.1",
                "remote_port": 40000,
                "codec": "pcmu",
                "ptime_ms": 20,
            }
        )


@pytest.mark.asyncio
async def test_start_gateway_session_rejects_wrong_protocol_or_codec(monkeypatch):
    adapter = AsteriskAdapter()
    monkeypatch.setenv("ENVIRONMENT", "production")
    payload = {
        "session_id": "asterisk-protocol",
        "listen_ip": "127.0.0.1",
        "listen_port": 32000,
        "remote_ip": "127.0.0.1",
        "remote_port": 40000,
        "codec": "pcmu",
        "ptime_ms": 20,
    }

    async def wrong_protocol(method, path, payload=None, ok=(200,)):
        return {
            "status": "started",
            "protocol_version": 1,
            "codec": payload["codec"],
            "session_id": payload["session_id"],
            "config_digest": payload["config_digest"],
        }

    adapter._gateway = wrong_protocol  # type: ignore[assignment]
    with pytest.raises(RuntimeError, match="protocol_version mismatch"):
        await adapter._start_gateway_session(payload)

    async def wrong_codec(method, path, payload=None, ok=(200,)):
        return {
            "status": "started",
            "protocol_version": 2,
            "codec": "opus",
            "session_id": payload["session_id"],
            "config_digest": payload["config_digest"],
        }

    adapter._gateway = wrong_codec  # type: ignore[assignment]
    with pytest.raises(RuntimeError, match="codec mismatch"):
        await adapter._start_gateway_session(payload)
