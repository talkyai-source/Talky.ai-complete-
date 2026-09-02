"""The AI Options LLM menu is exactly two entries, and the benchmark drives the
provider the tenant actually selected.

Product decision 2026-09-02: the ONLY selectable LLMs are GPT-OSS 120B on
Cerebras (primary) and GPT-OSS 20B on Groq (fallback). Gemini is not offered
even when GEMINI_API_KEY is configured. Gemini ids already stored by a tenant
remain *accepted* by save_config (hidden, not forbidden) — they are just never
offered.

Live failure the same evening: "Benchmark failed: Groq LLM streaming failed:
Error code: 404 - The model `gpt-oss-120b` does not exist". The benchmark only
knew Gemini-or-Groq, so a Cerebras config was sent to Groq with the Cerebras
model id.
"""
from __future__ import annotations

import pytest

from app.domain.models.ai_config import AIProviderConfig


@pytest.mark.asyncio
async def test_providers_menu_offers_only_cerebras_120b_and_groq_20b(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "set-on-prod")
    monkeypatch.setenv("CEREBRAS_API_KEY", "set-on-prod")
    monkeypatch.setenv("GROQ_API_KEY", "set-on-prod")
    from app.api.v1.endpoints.ai_options.providers import list_providers

    response = await list_providers()

    assert sorted(response.llm["providers"]) == ["cerebras", "groq"]
    offered = {(m["provider"], m["id"]) for m in response.llm["models"]}
    assert offered == {("cerebras", "gpt-oss-120b"), ("groq", "openai/gpt-oss-20b")}


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
