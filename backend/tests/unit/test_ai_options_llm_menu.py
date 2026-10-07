"""The five-model menu, hidden legacy IDs and correct provider routing.

The benchmark previously sent Cerebras model IDs to Groq and failed with 404.
Provider identity must be preserved across catalog, save, test and benchmark.
"""
from __future__ import annotations

import pytest

from app.domain.models.ai_config import AIProviderConfig


@pytest.mark.asyncio
async def test_providers_menu_offers_exactly_the_chosen_models(monkeypatch):
    """Owner decisions 2026-10-01: GPT-OSS 120B (Cerebras), GPT-OSS 20B (Groq),
    from Google Gemini 3.8 Flash only, and GPT-6 Luna on the OpenAI key.
    2026-10-06: plus DeepSeek V4.1 Flash on the DeepSeek key."""
    monkeypatch.setenv("GEMINI_API_KEY", "set-on-prod")
    monkeypatch.setenv("CEREBRAS_API_KEY", "set-on-prod")
    monkeypatch.setenv("GROQ_API_KEY", "set-on-prod")
    monkeypatch.setenv("OPENAI_API_KEY", "set-on-prod")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "set-on-prod")
    from app.api.v1.endpoints.ai_options.providers import list_providers

    response = await list_providers()

    assert sorted(response.llm["providers"]) == ["cerebras", "deepseek", "gemini", "groq", "openai"]
    assert len(response.llm["models"]) == 5
    offered = {(m["provider"], m["id"]) for m in response.llm["models"]}
    assert offered == {
        ("cerebras", "gpt-oss-120b"),
        ("groq", "openai/gpt-oss-20b"),
        ("gemini", "gemini-3.8-flash"),
        ("openai", "gpt-6-luna"),
        ("deepseek", "deepseek-flash"),
    }


@pytest.mark.asyncio
async def test_without_a_gemini_key_google_is_not_offered(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("CEREBRAS_API_KEY", "set-on-prod")
    from app.api.v1.endpoints.ai_options.providers import list_providers

    response = await list_providers()

    assert "gemini" not in response.llm["providers"]


def test_benchmark_uses_cerebras_for_a_cerebras_config(monkeypatch):
    monkeypatch.setenv("CEREBRAS_API_KEY", "cerebras-key")
    monkeypatch.setenv("GROQ_API_KEY", "groq-key")
    from app.api.v1.endpoints.ai_options.benchmark import _select_benchmark_llm
    from app.infrastructure.llm.cerebras import CerebrasLLMProvider

    config = AIProviderConfig(llm_provider="cerebras", llm_model="gpt-oss-120b")
    llm, api_key = _select_benchmark_llm(config)

    assert isinstance(llm, CerebrasLLMProvider)
    assert api_key == "cerebras-key"


def test_benchmark_uses_groq_for_a_groq_config(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "groq-key")
    from app.api.v1.endpoints.ai_options.benchmark import _select_benchmark_llm
    from app.infrastructure.llm.groq import GroqLLMProvider

    config = AIProviderConfig(llm_provider="groq", llm_model="openai/gpt-oss-20b")
    llm, api_key = _select_benchmark_llm(config)

    assert isinstance(llm, GroqLLMProvider)
    assert api_key == "groq-key"


def test_benchmark_refuses_cerebras_without_a_key(monkeypatch):
    monkeypatch.delenv("CEREBRAS_API_KEY", raising=False)
    from fastapi import HTTPException
    from app.api.v1.endpoints.ai_options.benchmark import _select_benchmark_llm

    config = AIProviderConfig(llm_provider="cerebras", llm_model="gpt-oss-120b")
    with pytest.raises(HTTPException) as exc:
        _select_benchmark_llm(config)
    assert exc.value.status_code == 503
    assert "Cerebras" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_without_a_deepseek_key_deepseek_is_not_offered(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("CEREBRAS_API_KEY", "set-on-prod")
    from app.api.v1.endpoints.ai_options.providers import list_providers

    response = await list_providers()

    assert "deepseek" not in response.llm["providers"]
