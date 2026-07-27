"""Cerebras provider + assistant-routing tests.

The load-bearing property here is that reasoning is switched OFF wherever the
model allows it, and that we NEVER send reasoning_effort="none" to a model that
rejects it. Cerebras validates this parameter per model, so getting it wrong is
a hard API error, not a silent fallback:

    gemma-4-31b   "none" | low | medium | high
    zai-glm-4.7   "none" disables reasoning
    gpt-oss-120b  low | medium | high        <- "none" is NOT accepted

Ref: https://inference-docs.cerebras.ai/api-reference/chat-completions
"""
from __future__ import annotations

import pytest

from app.domain.models.ai_config import (
    CEREBRAS_MIN_REASONING_EFFORT,
    CEREBRAS_MODELS,
    CEREBRAS_REASONING_NONE_SUPPORTED,
    CerebrasModel,
    LLMProvider,
)
from app.domain.models.conversation import Message, MessageRole
from app.infrastructure.assistant.llm_client import (
    _adapt_for_cerebras,
    provider_for_model,
)
from app.infrastructure.llm.cerebras import CerebrasLLMProvider


# ── reasoning selection ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "model,expected",
    [
        (CerebrasModel.GEMMA_4_31B.value, "none"),
        (CerebrasModel.ZAI_GLM_4_7.value, "none"),
        (CerebrasModel.GPT_OSS_120B.value, "low"),
    ],
)
def test_reasoning_effort_per_model(model, expected):
    assert CerebrasLLMProvider._reasoning_effort(model) == expected


def test_gpt_oss_never_gets_none():
    """Regression guard: 'none' is rejected by gpt-oss-120b, so sending it
    would fail every request on the highest-limit model in the menu."""
    assert CerebrasLLMProvider._reasoning_effort(
        CerebrasModel.GPT_OSS_120B.value
    ) != "none"
    assert CerebrasModel.GPT_OSS_120B.value not in CEREBRAS_REASONING_NONE_SUPPORTED


def test_unknown_model_defers_rather_than_guessing():
    """An unrecognised id yields None => omit the param and let the model's own
    default stand. Guessing a value risks sending an unsupported one."""
    assert CerebrasLLMProvider._reasoning_effort("some-future-model") is None


def test_every_catalogued_model_has_a_defined_reasoning_stance():
    """No model may fall through to 'unknown' — each is either off-able or has
    an explicit floor. Catches adding a model without deciding this."""
    for m in CEREBRAS_MODELS:
        assert (
            m.id in CEREBRAS_REASONING_NONE_SUPPORTED
            or m.id in CEREBRAS_MIN_REASONING_EFFORT
        ), f"{m.id} has no reasoning stance declared"


# ── request construction ─────────────────────────────────────────────────


def _build(model: str) -> dict:
    provider = CerebrasLLMProvider()
    return provider._build_request(
        messages=[Message(role=MessageRole.USER, content="hello")],
        system_prompt="be brief",
        temperature=0.5,
        max_tokens=120,
        model=model,
        tools=None,
    )


def test_request_disables_thinking_and_uses_completion_tokens():
    req = _build(CerebrasModel.GEMMA_4_31B.value)
    assert req["reasoning_effort"] == "none"
    # Cerebras takes max_completion_tokens; max_tokens is the older spelling.
    assert req["max_completion_tokens"] == 120
    assert "max_tokens" not in req
    assert req["stream"] is True
    assert req["messages"][0] == {"role": "system", "content": "be brief"}


def test_clear_thinking_only_on_glm():
    """clear_thinking is accepted by zai-glm-4.7 only; without it that model
    replays prior turns' thinking into the prompt and context grows per turn."""
    assert _build(CerebrasModel.ZAI_GLM_4_7.value)["clear_thinking"] is True
    assert "clear_thinking" not in _build(CerebrasModel.GEMMA_4_31B.value)
    assert "clear_thinking" not in _build(CerebrasModel.GPT_OSS_120B.value)


def test_empty_messages_are_dropped():
    provider = CerebrasLLMProvider()
    req = provider._build_request(
        messages=[
            Message(role=MessageRole.USER, content="   "),
            Message(role=MessageRole.USER, content="real"),
        ],
        system_prompt=None,
        temperature=None,
        max_tokens=None,
        model=CerebrasModel.GEMMA_4_31B.value,
        tools=None,
    )
    assert [m["content"] for m in req["messages"]] == ["real"]


@pytest.mark.asyncio
async def test_initialize_without_key_raises(monkeypatch):
    monkeypatch.delenv("CEREBRAS_API_KEY", raising=False)
    with pytest.raises(ValueError, match="Cerebras API key not found"):
        await CerebrasLLMProvider().initialize({})


# ── assistant routing ────────────────────────────────────────────────────


def test_assistant_routes_models_to_the_right_vendor():
    assert provider_for_model("llama-3.1-8b-instant") == "groq"
    assert provider_for_model("qwen/qwen3.6-27b") == "groq"
    assert provider_for_model(None) == "groq"
    for m in CEREBRAS_MODELS:
        assert provider_for_model(m.id) == "cerebras"


def test_assistant_adapter_matches_provider_rules():
    out = _adapt_for_cerebras(
        {"model": CerebrasModel.ZAI_GLM_4_7.value, "max_tokens": 2000}
    )
    assert out["max_completion_tokens"] == 2000 and "max_tokens" not in out
    assert out["reasoning_effort"] == "none"
    assert out["clear_thinking"] is True

    oss = _adapt_for_cerebras(
        {"model": CerebrasModel.GPT_OSS_120B.value, "max_tokens": 2000}
    )
    assert oss["reasoning_effort"] == "low"
    assert "clear_thinking" not in oss


def test_adapter_does_not_mutate_caller_dict():
    """The assistant reuses its completion_args across tool-loop iterations."""
    original = {"model": CerebrasModel.GEMMA_4_31B.value, "max_tokens": 2000}
    _adapt_for_cerebras(original)
    assert original == {"model": CerebrasModel.GEMMA_4_31B.value, "max_tokens": 2000}


# ── registration / menus ─────────────────────────────────────────────────


def test_provider_is_registered_and_enumerated():
    from app.infrastructure.llm.factory import LLMFactory

    assert "cerebras" in LLMFactory.list_providers()
    assert LLMProvider.CEREBRAS.value == "cerebras"


def test_ai_options_validation_accepts_cerebras_models():
    """The save-config validator rejects any model outside its union, so a
    missing entry here would make Cerebras unselectable in global config."""
    import app.api.v1.endpoints.ai_options.config as cfg

    src = cfg.__file__
    with open(src, encoding="utf-8") as fh:
        body = fh.read()
    assert '"cerebras": [m.id for m in CEREBRAS_MODELS]' in body


def test_assistant_allows_cerebras_ids_even_without_key(monkeypatch):
    """Validation must not depend on the key: otherwise rotating the key out
    silently downgrades a tenant's chosen model to the default."""
    monkeypatch.delenv("CEREBRAS_API_KEY", raising=False)
    import importlib

    import app.infrastructure.assistant.model_config as mc

    importlib.reload(mc)
    for m in CEREBRAS_MODELS:
        assert m.id in mc.ALLOWED_ASSISTANT_MODEL_IDS
        assert mc.normalize_model(m.id) == m.id
    # ...but the picker only offers them when the key is present.
    assert all(e["id"] not in {m.id for m in CEREBRAS_MODELS}
               for e in mc.available_models())

    monkeypatch.setenv("CEREBRAS_API_KEY", "test-key")
    importlib.reload(mc)
    offered = {e["id"] for e in mc.available_models()}
    assert {m.id for m in CEREBRAS_MODELS} <= offered
    importlib.reload(mc)
