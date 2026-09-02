"""Regression guards for the production image dependency security contract."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.version import Version

from app.infrastructure.realtime.openai_realtime import OpenAIRealtimeSession
from app.infrastructure.stt.deepgram_flux import DeepgramFluxSTTProvider


BACKEND_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_ROOT.parent


def _exact_pin(manifest: Path, package: str) -> Version:
    for raw_line in manifest.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", "-r ")):
            continue
        requirement = Requirement(line)
        if requirement.name.casefold() != package.casefold():
            continue
        exact = [item.version for item in requirement.specifier if item.operator == "=="]
        assert len(exact) == 1, f"{package} must have one exact pin in {manifest.name}"
        return Version(exact[0])
    raise AssertionError(f"{package} is missing from {manifest.name}")


@pytest.mark.parametrize("manifest_name", ["requirements.in", "requirements.txt"])
def test_trivy_fixed_dependency_floors_are_pinned(manifest_name: str) -> None:
    manifest = BACKEND_ROOT / manifest_name
    assert _exact_pin(manifest, "langsmith") >= Version("0.8.18")
    assert _exact_pin(manifest, "websockets") >= Version("15.0")


def test_container_pins_patched_setuptools_vendor_bundle() -> None:
    dockerfile = (BACKEND_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert '"setuptools==83.0.0"' in dockerfile

    workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )
    assert "--ignore-vuln GHSA-f4xh-w4cj-qxq8" not in workflow


def test_all_direct_websocket_connections_use_the_v15_header_name() -> None:
    realtime = (
        BACKEND_ROOT / "app" / "infrastructure" / "realtime" / "openai_realtime.py"
    ).read_text(encoding="utf-8")
    flux = (
        BACKEND_ROOT / "app" / "infrastructure" / "stt" / "deepgram_flux.py"
    ).read_text(encoding="utf-8")

    assert "extra_headers=" not in realtime
    assert "extra_headers=" not in flux
    assert realtime.count("additional_headers=headers") == 1
    assert flux.count("additional_headers=headers") == 2


class _HandshakeSocket:
    def __init__(self) -> None:
        self._messages = [
            json.dumps({"type": "session.created"}),
            json.dumps({"type": "session.updated"}),
        ]
        self._block = asyncio.Event()

    async def recv(self) -> str:
        if self._messages:
            return self._messages.pop(0)
        await self._block.wait()
        raise AssertionError("unreachable")

    async def send(self, _payload: str) -> None:
        return None

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_openai_realtime_uses_websockets_15_header_contract(monkeypatch) -> None:
    captured: dict[str, object] = {}
    socket = _HandshakeSocket()

    async def connect(
        url: str, *, additional_headers: dict[str, str], max_size: int | None
    ) -> _HandshakeSocket:
        captured.update(
            url=url, additional_headers=additional_headers, max_size=max_size
        )
        return socket

    monkeypatch.setattr(
        "app.infrastructure.realtime.openai_realtime.websockets.connect", connect
    )
    session = OpenAIRealtimeSession(api_key="test-key", call_id="dependency-test")

    assert await session.connect() is True
    await session.close()
    assert captured["additional_headers"] == {
        "Authorization": "Bearer test-key",
        "User-Agent": "TalkyAI-VoiceAgent/1.0",
    }
    assert captured["max_size"] is None


@pytest.mark.asyncio
async def test_flux_preconnect_uses_websockets_15_header_contract(monkeypatch) -> None:
    captured: dict[str, object] = {}
    socket = _HandshakeSocket()

    async def connect(
        url: str, *, additional_headers: dict[str, str]
    ) -> _HandshakeSocket:
        captured.update(url=url, additional_headers=additional_headers)
        return socket

    monkeypatch.setattr(
        "app.infrastructure.stt.deepgram_flux.websockets.connect", connect
    )
    provider = DeepgramFluxSTTProvider()
    await provider.initialize({"api_key": "test-key"})

    await provider.pre_connect("call-1")

    assert provider._pre_connections["call-1"] is socket
    assert captured["additional_headers"] == {
        "Authorization": "Token test-key",
        "User-Agent": "TalkyAI-VoiceAgent/1.0",
    }
