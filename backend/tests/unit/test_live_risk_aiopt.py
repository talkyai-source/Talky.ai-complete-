"""Live-risk AI Options findings (aiopt-1, 2, 4, 5, 6)."""
from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError


def _dep_names(route) -> list[str]:
    out = []
    for dep in route.dependant.dependencies:
        out.append(getattr(dep.call, "__qualname__", repr(dep.call)))
    return out


def _route(router, path, method="POST"):
    for r in router.routes:
        if getattr(r, "path", None) == path and method in r.methods:
            return r
    raise AssertionError(f"no route {method} {path}")


# ---- aiopt-1: role gate on mutating / spend routes -------------------------

@pytest.mark.parametrize("module,path", [
    ("config", "/config"),
    ("preview", "/voices/prefetch"),
    ("testing", "/test/llm"),
    ("testing", "/test/tts"),
])
def test_ai_options_mutations_require_tenant_update(module, path):
    import importlib
    mod = importlib.import_module(f"app.api.v1.endpoints.ai_options.{module}")
    names = _dep_names(_route(mod.router, path))
    assert any("permission_checker" in n for n in names), names


# ---- aiopt-6: bounded text + rate limit ------------------------------------

def test_tts_test_request_text_is_bounded():
    from app.domain.models.ai_config import TTSTestRequest
    TTSTestRequest(voice_id="v", text="hello")
    with pytest.raises(ValidationError):
        TTSTestRequest(voice_id="v", text="x" * 5000)


def test_voice_preview_request_text_is_bounded():
    from app.api.v1.endpoints.ai_options.preview import VoicePreviewRequest
    with pytest.raises(ValidationError):
        VoicePreviewRequest(voice_id="v", text="x" * 5000)


def test_llm_test_message_is_bounded():
    from app.domain.models.ai_config import LLMTestRequest
    with pytest.raises(ValidationError):
        LLMTestRequest(message="x" * 20000)


@pytest.mark.parametrize("module,path", [
    ("preview", "/voices/preview"),
    ("preview", "/voices/prefetch"),
    ("testing", "/test/llm"),
    ("testing", "/test/tts"),
])
def test_spend_routes_are_rate_limited(module, path):
    import importlib
    mod = importlib.import_module(f"app.api.v1.endpoints.ai_options.{module}")
    names = _dep_names(_route(mod.router, path))
    assert any("rate_limit" in n for n in names), names


# ---- aiopt-5: preview cache only for the canonical sample text -------------

def test_preview_cache_is_bypassed_for_custom_text(monkeypatch):
    from app.api.v1.endpoints.ai_options import preview
    src = inspect.getsource(preview.preview_voice)
    assert src.count("_preview_text_is_cacheable(request.text)") == 2  # read + write
    assert preview._preview_text_is_cacheable(preview._PREVIEW_SAMPLE_TEXT) is True
    assert preview._preview_text_is_cacheable("attacker sentence") is False


# ---- aiopt-4: save_config refuses providers with no key -------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("provider,model_attr", [("cerebras", "CEREBRAS_MODELS"), ("deepseek", "DEEPSEEK_MODELS"), ("groq", "GROQ_MODELS")])
async def test_save_config_503_when_llm_key_missing(monkeypatch, provider, model_attr):
    from app.api.v1.endpoints.ai_options import config as cfg
    from app.domain.models import ai_config as m
    from app.domain.services import credential_resolver

    resolve = AsyncMock(return_value=None)
    monkeypatch.setattr(credential_resolver, "get_credential_resolver", lambda: SimpleNamespace(resolve=resolve))
    persisted = AsyncMock()
    monkeypatch.setattr(cfg, "_upsert_tenant_config", persisted)
    model = getattr(m, model_attr)[0].id
    body = m.AIProviderConfig(llm_provider=provider, llm_model=model)
    with pytest.raises(HTTPException) as caught:
        await cfg.save_config(body, SimpleNamespace(tenant_id="A"), SimpleNamespace(pool=None))
    assert caught.value.status_code == 503
    assert provider in caught.value.detail.lower()
    persisted.assert_not_awaited()


# ---- aiopt-2: campaign voice from a different engine than the tenant's -----

def test_campaign_voice_of_other_engine_is_not_paired_with_tenant_provider():
    from app.domain.services import telephony_session_config as t
    cartesia_uuid = "a0e99841-438c-4a64-b679-ae501e7d6091"
    assert t.voice_id_provider_hint(cartesia_uuid) == "cartesia"
    assert t.voice_id_provider_hint("en-US-Chirp3-HD-Orus") == "google"
    assert t.voice_id_provider_hint("aura-2-zeus-en") == "deepgram"
    assert t.voice_id_provider_hint("lfPTQbwnu1oXQ9g6V0r4") is None

    # tenant now on elevenlabs, campaign has NULL provider + a Cartesia voice
    assert t.campaign_voice_matches_provider(cartesia_uuid, None, "elevenlabs") is False
    assert t.campaign_voice_matches_provider(cartesia_uuid, None, "cartesia") is True
    # explicit campaign provider always wins (voice validated against it at save)
    assert t.campaign_voice_matches_provider(cartesia_uuid, "cartesia", "elevenlabs") is True
    # unknown shape: keep the historical behaviour
    assert t.campaign_voice_matches_provider("lfPTQbwnu1oXQ9g6V0r4", None, "cartesia") is True


def test_voice_engine_check_reads_the_provider_enum_value():
    """The tenant TTS provider arrives as a TTSProvider enum; comparing str(enum)
    ("TTSProvider.DEEPGRAM") dropped every Deepgram Aura voice as foreign."""
    from app.domain.models.ai_config import TTSProvider
    from app.domain.services.telephony_session_config import campaign_voice_matches_provider

    assert campaign_voice_matches_provider("aura-2-apollo-en", None, TTSProvider.DEEPGRAM)
    assert not campaign_voice_matches_provider("aura-2-apollo-en", None, TTSProvider.CARTESIA)
