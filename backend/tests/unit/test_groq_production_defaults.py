"""Production Groq defaults must resolve to a model the account can serve."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import yaml

from app.domain.models.ai_config import GroqModel
from app.domain.services.voice_orchestrator import VoiceSessionConfig
from app.infrastructure.llm.groq import GroqLLMProvider
from app.workers import voice_worker


SUPPORTED_DEFAULT = GroqModel.GPT_OSS_20B.value


class _FakeProvider:
    def __init__(self) -> None:
        self.config: dict | None = None
        self.warmed = False

    async def initialize(self, config: dict) -> None:
        self.config = config

    async def warm_up(self) -> None:
        self.warmed = True


@pytest.mark.asyncio
async def test_voice_worker_boot_warmup_uses_account_supported_model(monkeypatch):
    llm = _FakeProvider()
    stt = _FakeProvider()
    tts = _FakeProvider()
    media = _FakeProvider()

    monkeypatch.setattr(voice_worker, "GroqLLMProvider", lambda: llm)
    monkeypatch.setattr(voice_worker, "DeepgramFluxSTTProvider", lambda: stt)
    monkeypatch.setattr(
        voice_worker.TTSFactory,
        "create",
        staticmethod(lambda *_args, **_kwargs: tts),
    )
    monkeypatch.setattr(
        voice_worker.MediaGatewayFactory,
        "create",
        staticmethod(lambda *_args, **_kwargs: media),
    )

    worker = voice_worker.VoicePipelineWorker()
    await worker._initialize_providers()
    await asyncio.sleep(0)  # let the boot warm-up task observe its config

    assert llm.config is not None
    assert llm.config["model"] == SUPPORTED_DEFAULT
    assert llm.warmed is True


@pytest.mark.asyncio
async def test_groq_provider_omitted_model_uses_account_supported_default(monkeypatch):
    monkeypatch.setattr(
        GroqLLMProvider,
        "_client_for",
        lambda _self, _api_key: object(),
    )
    provider = GroqLLMProvider()

    await provider.initialize({"api_key": "test-key"})

    assert provider._model == SUPPORTED_DEFAULT


def test_voice_session_groq_default_is_account_supported():
    config = VoiceSessionConfig()

    assert config.llm_provider_type == "groq"
    assert config.llm_model == SUPPORTED_DEFAULT


def test_providers_yaml_groq_default_is_account_supported():
    config_path = Path(__file__).resolve().parents[2] / "config" / "providers.yaml"
    document = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    assert document["providers"]["llm"]["groq"]["model"] == SUPPORTED_DEFAULT
