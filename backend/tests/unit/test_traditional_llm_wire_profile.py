"""Selected traditional profiles are measured at the synthetic SDK/HTTP boundary."""

import json
import logging
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import httpx
import pytest

from app.domain.models.conversation import Message, MessageRole
from app.infrastructure.llm.cerebras import CerebrasLLMProvider
from app.infrastructure.llm.gemini import GeminiLLMProvider
from app.infrastructure.llm.groq import GroqLLMProvider
from app.infrastructure.llm.openai import OpenAILLMProvider
from tests.unit.test_gemini_tools import rich_genai  # noqa: F401

pytestmark = pytest.mark.usefixtures("rich_genai")

MESSAGES = [Message(role=MessageRole.USER, content="Synthetic profile check")]


def _profiles(caplog):
    return [json.loads(record.args[0]) for record in caplog.records
            if record.name == "app.infrastructure.llm.request_profile"]


@pytest.mark.parametrize("name,model,effort", [
    ("groq", "openai/gpt-oss-20b", "low"),
    ("cerebras", "gpt-oss-120b", "low"),
    ("gemini", "gemini-3.8-flash", "low"),
])
async def test_offered_reasoning_models_preserve_temperature_and_add_shared_headroom(
    name, model, effort, monkeypatch, caplog,
):
    from app.infrastructure.llm import cerebras, gemini, groq

    modules = {"groq": groq, "cerebras": cerebras, "gemini": gemini}
    monkeypatch.setattr(modules[name], "_THINKING_RESERVE_TOKENS", 1024)
    providers = {"groq": GroqLLMProvider, "cerebras": CerebrasLLMProvider, "gemini": GeminiLLMProvider}
    provider = providers[name]()
    provider._model = model
    provider._temperature = 0.6
    provider._max_tokens = 90
    caplog.set_level(logging.INFO, logger="app.infrastructure.llm.request_profile")

    async def stream():
        if name == "gemini":
            yield NS(text="Ready.", candidates=[NS(finish_reason="STOP")])
        else:
            yield NS(choices=[NS(delta=NS(content="Ready.", tool_calls=None), finish_reason="stop")])

    create = AsyncMock(side_effect=lambda **_: stream())
    provider._client = (NS(aio=NS(models=NS(generate_content_stream=create))) if name == "gemini"
                        else NS(chat=NS(completions=NS(create=create))))
    assert [token async for token in provider.stream_chat(MESSAGES)] == ["Ready."]
    wire = create.await_args.kwargs
    assert wire["model"] == model
    config = wire["config"] if name == "gemini" else NS(**wire)
    assert config.temperature == 0.6
    assert (config.max_output_tokens if name == "gemini" else config.max_completion_tokens) == 1114
    assert (config.thinking_config.thinking_level if name == "gemini" else config.reasoning_effort) == effort
    profile, = _profiles(caplog)
    assert profile["model"] == wire["model"]
    assert profile["configured_temperature"] == profile["effective_temperature"] == config.temperature
    assert profile["configured_visible_token_target"] == 90
    assert profile["wire_token_ceiling"] == 1114
    if name == "groq":
        assert wire["include_reasoning"] is False  # Hiding reasoning does not disable it.
        assert wire["top_p"] == 0.95


async def test_groq_qa_override_is_visible_on_wire_without_mutating_saved_temperature(caplog):
    caplog.set_level(logging.INFO, logger="app.infrastructure.llm.request_profile")
    async def stream():
        yield NS(choices=[NS(delta=NS(content="Ready."), finish_reason="stop")])
    provider = GroqLLMProvider()
    provider._temperature = 0.6
    provider.set_deterministic_mode(True, seed=42)
    create = AsyncMock(side_effect=lambda **_: stream())
    provider._client = NS(chat=NS(completions=NS(create=create)))
    _ = [token async for token in provider.stream_chat(MESSAGES, temperature=0.8)]
    assert create.await_args.kwargs["temperature"] == 0
    assert create.await_args.kwargs["seed"] == 42
    assert provider._temperature == 0.6
    profile, = _profiles(caplog)
    assert profile["configured_temperature"] == 0.8
    assert profile["effective_temperature"] == 0
    assert profile["seed"] == 42


async def test_luna_keeps_answer_ceiling_and_disables_reasoning_on_wire(caplog):
    caplog.set_level(logging.INFO, logger="app.infrastructure.llm.request_profile")
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"Ready."},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')

    provider = OpenAILLMProvider()
    provider._temperature, provider._max_tokens = 0.6, 90
    provider._client = httpx.AsyncClient(base_url="https://example.invalid/", transport=httpx.MockTransport(respond))
    try:
        assert [token async for token in provider.stream_chat(MESSAGES)] == ["Ready."]
    finally:
        await provider.cleanup()
    assert len(requests) == 1
    assert requests[0]["model"] == "gpt-6-luna"
    assert requests[0]["temperature"] == 0.6
    assert requests[0]["max_completion_tokens"] == 90
    assert requests[0]["reasoning_effort"] == "none"
    profile, = _profiles(caplog)
    assert profile["configured_visible_token_target"] == profile["wire_token_ceiling"] == 90
    assert profile["reasoning_effort"] == "none"


@pytest.mark.parametrize("invalid", [
    {"model": "unrelated-model"}, {"reasoning_effort": "low"}, {"reasoning_effort": "invalid"},
])
async def test_luna_invalid_model_or_reasoning_never_reaches_transport(invalid):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(500)

    provider = OpenAILLMProvider()
    provider._client = httpx.AsyncClient(base_url="https://example.invalid/", transport=httpx.MockTransport(respond))
    try:
        with pytest.raises(ValueError):
            _ = [token async for token in provider.stream_chat(MESSAGES, **invalid)]
    finally:
        await provider.cleanup()
    assert requests == []


@pytest.mark.parametrize("provider_type,model", [
    ("groq", "gpt-oss-120b"), ("cerebras", "openai/gpt-oss-20b"),
    ("gemini", "gpt-6-luna"), ("openai", "unrelated-model"), ("unknown", "unknown"),
    ("", "openai/gpt-oss-20b"),
])
async def test_invalid_saved_selection_fails_before_credentials_factory_or_fallback(
    provider_type, model, monkeypatch,
):
    from app.domain.services import credential_resolver
    from app.domain.services.voice_orchestrator import VoiceOrchestrator, VoiceSessionConfig
    from app.infrastructure.llm.factory import LLMFactory
    from unittest.mock import Mock

    resolver = Mock()
    factory = Mock()
    monkeypatch.setattr(credential_resolver, "get_credential_resolver", resolver)
    monkeypatch.setattr(LLMFactory, "create", factory)
    monkeypatch.setenv("LLM_FAILOVER_ENABLED", "true")
    config = VoiceSessionConfig(llm_provider_type=provider_type, llm_model=model)
    with pytest.raises(ValueError, match="Invalid LLM"):
        await VoiceOrchestrator()._create_llm_provider(config)
    resolver.assert_not_called()
    factory.assert_not_called()


def test_selected_and_previously_saved_catalog_models_share_one_validator():
    from app.domain.models.ai_config import validate_traditional_llm_selection

    for provider, model in [
        ("groq", "openai/gpt-oss-20b"), ("groq", "openai/gpt-oss-120b"),
        ("cerebras", "gpt-oss-120b"), ("cerebras", "gemma-4-31b"),
        ("cerebras", "zai-glm-4.7"), ("gemini", "gemini-3.8-flash"),
        ("gemini", "gemini-2.5-flash"), ("openai", "gpt-6-luna"),
    ]:
        validate_traditional_llm_selection(provider, model)


@pytest.mark.parametrize("provider_cls,model,effort", [
    (GroqLLMProvider, "openai/gpt-oss-20b", "none"),
    (GroqLLMProvider, "openai/gpt-oss-20b", "invalid"),
    (CerebrasLLMProvider, "gpt-oss-120b", "none"),
    (CerebrasLLMProvider, "gpt-oss-120b", "medium"),
    (GeminiLLMProvider, "gemini-3.8-flash", "none"),
    (GeminiLLMProvider, "gemini-3.8-flash", "low"),
])
async def test_explicit_unsupported_reasoning_is_not_forwarded_or_silently_ignored(
    provider_cls, model, effort,
):
    provider = provider_cls()
    provider._model = model
    create = AsyncMock()
    provider._client = NS(chat=NS(completions=NS(create=create)),
                          aio=NS(models=NS(generate_content_stream=create)))
    with pytest.raises(ValueError, match="reasoning_effort"):
        _ = [token async for token in provider.stream_chat(MESSAGES, reasoning_effort=effort)]
    create.assert_not_awaited()
