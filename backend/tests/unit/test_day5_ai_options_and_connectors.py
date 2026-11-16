"""Day 5 (D5-1, D5-14, D5-15 Outlook row, D5-16): availability for every AI option,
safe ElevenLabs errors, connector status display name + safe message/code."""
from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints import connectors as ce
from app.api.v1.endpoints.ai_options import config as config_endpoint
from app.api.v1.endpoints.ai_options import providers
from app.domain.models.ai_config import AIProviderConfig, ModelInfo, VoiceInfo
from app.domain.services import credential_resolver
from tests.unit.test_connectors_card_keys import FakeDB, _resp, _user

ALL_KEYS = (
    "GROQ_API_KEY", "CEREBRAS_API_KEY", "GEMINI_API_KEY", "DEEPSEEK_API_KEY", "DEEPSEEK_API_KEYS",
    "DEEPGRAM_API_KEY", "CARTESIA_API_KEY", "ELEVENLABS_API_KEY", "GOOGLE_TTS_API_KEY",
    "GOOGLE_SERVICE_ACCOUNT_JSON", "GOOGLE_APPLICATION_CREDENTIALS", "ASSEMBLYAI_API_KEY",
    "ASSEMBLYAI_API_KEYS", "OPENAI_API_KEY",
)
ENV_BY_PROVIDER = {
    "groq": "GROQ_API_KEY", "cerebras": "CEREBRAS_API_KEY", "gemini": "GEMINI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY", "deepgram": "DEEPGRAM_API_KEY", "cartesia": "CARTESIA_API_KEY",
    "google": "GOOGLE_TTS_API_KEY", "elevenlabs": "ELEVENLABS_API_KEY",
}
# Every setting that can make a provider available (pools and alternates too).
ENV_BY_PROVIDER = {
    name: (value,) for name, value in ENV_BY_PROVIDER.items()
} | {
    "deepseek": ("DEEPSEEK_API_KEY", "DEEPSEEK_API_KEYS"),
    "google": ("GOOGLE_TTS_API_KEY", "GOOGLE_SERVICE_ACCOUNT_JSON", "GOOGLE_APPLICATION_CREDENTIALS"),
}


@pytest.fixture
def server(monkeypatch):
    """A server with every key present; tests remove one."""
    from app.realtime import credentials
    for name in ALL_KEYS:
        monkeypatch.setenv(name, "synthetic")
    state = {"openai": "synthetic", "assemblyai": "synthetic"}
    monkeypatch.setattr(credentials, "resolve_openai_key", AsyncMock(side_effect=lambda *_a, **_k: state["openai"]))

    async def resolve(provider, **_kwargs):
        return state.get(provider)

    monkeypatch.setattr(credential_resolver, "get_credential_resolver",
                        lambda: SimpleNamespace(resolve=resolve))
    monkeypatch.setattr(providers, "elevenlabs_enabled", lambda: bool(__import__("os").getenv("ELEVENLABS_API_KEY")))
    monkeypatch.setattr(providers, "get_elevenlabs_tts_models_for_current_key", AsyncMock(return_value=[]))
    return SimpleNamespace(state=state, monkeypatch=monkeypatch)


async def _catalog():
    return await providers.list_providers(SimpleNamespace(tenant_id="A"))


def _all_models(catalog):
    return [*catalog.llm["models"], *catalog.stt["models"], *catalog.tts["models"]]


def _assert_safe(reason):
    assert reason and isinstance(reason, str)
    for bad in ("KEY", "_API", "restart", "env", "Set ", ".env", "token"):
        assert bad not in reason, (bad, reason)
    assert "_" not in reason


async def test_with_every_key_present_every_provider_and_model_is_available(server):
    catalog = await _catalog()
    assert sorted(catalog.llm["providers"]) == ["cerebras", "deepseek", "gemini", "groq", "openai"]
    assert sorted(catalog.tts["providers"]) == ["cartesia", "deepgram", "elevenlabs", "google"]
    for section in (catalog.llm, catalog.stt, catalog.tts):
        for name in section["providers"]:
            assert section["provider_status"][name] == {"available": True, "unavailable_reason": None}, name
    for model in _all_models(catalog):
        assert model["available"] is True and model["unavailable_reason"] is None, model["id"]
    assert catalog.realtime["available"] is True


async def test_existing_model_fields_are_unchanged(server):
    catalog = await _catalog()
    expected = {"id", "name", "description", "speed", "price", "context_window", "is_preview", "provider"}
    for model in _all_models(catalog):
        assert expected <= set(model)
        assert set(model) - expected == {"available", "unavailable_reason"}
    assert [e["id"] for e in catalog.stt["engines"]] == ["deepgram_flux", "assemblyai", "deepgram_nova"]
    assert set(catalog.stt["providers"]) == {"deepgram", "assemblyai"}


@pytest.mark.parametrize("provider", ["groq", "cartesia", "google", "deepgram"])
async def test_listed_provider_without_its_key_is_unavailable_with_a_safe_reason(server, provider):
    for name in ENV_BY_PROVIDER[provider]:
        server.monkeypatch.delenv(name, raising=False)
    catalog = await _catalog()
    seen = 0
    for section in (catalog.llm, catalog.stt, catalog.tts):
        if provider in section["providers"]:
            status = section["provider_status"][provider]
            assert status["available"] is False
            _assert_safe(status["unavailable_reason"])
            seen += 1
    assert seen >= 1
    models = [m for m in _all_models(catalog) if m["provider"] == provider]
    assert models, provider
    for model in models:
        assert model["available"] is False, model["id"]
        _assert_safe(model["unavailable_reason"])
    others = [m for m in _all_models(catalog) if m["provider"] != provider]
    assert others and all(m["available"] for m in others)
    if provider == "deepgram":
        engines = {e["id"]: e for e in catalog.stt["engines"]}
        assert engines["deepgram_flux"]["available"] is False
        assert engines["deepgram_nova"]["available"] is False
        assert engines["assemblyai"]["available"] is True
        _assert_safe(engines["deepgram_flux"]["unavailable_reason"])


async def test_assemblyai_without_a_key_is_unavailable_everywhere_with_a_safe_reason(server):
    server.state["assemblyai"] = None
    catalog = await _catalog()
    status = catalog.stt["provider_status"]["assemblyai"]
    assert status["available"] is False
    _assert_safe(status["unavailable_reason"])
    engines = {e["id"]: e for e in catalog.stt["engines"]}
    assert engines["assemblyai"]["available"] is False
    _assert_safe(engines["assemblyai"]["unavailable_reason"])
    for model in catalog.stt["models"]:
        if model["provider"] == "assemblyai":
            assert model["available"] is False
            _assert_safe(model["unavailable_reason"])


async def test_realtime_reason_is_safe_and_openai_models_follow_the_key(server):
    server.state["openai"] = None
    catalog = await _catalog()
    assert catalog.realtime["available"] is False
    _assert_safe(catalog.realtime["unavailable_reason"])
    assert "openai" not in catalog.llm["providers"]
    assert not [m for m in catalog.llm["models"] if m["provider"] == "openai"]


@pytest.mark.parametrize("provider", ["cerebras", "gemini", "deepseek", "elevenlabs"])
async def test_conditionally_offered_provider_is_not_offered_without_its_key(server, provider):
    for name in ENV_BY_PROVIDER[provider]:
        server.monkeypatch.delenv(name, raising=False)
    catalog = await _catalog()
    for section in (catalog.llm, catalog.tts):
        assert provider not in section["providers"]
        assert provider not in section["provider_status"]
    assert not [m for m in _all_models(catalog) if m["provider"] == provider]


async def test_voices_carry_availability_and_elevenlabs_errors_are_safe(server, caplog):
    raw = "401 Unauthorized: invalid xi-api-key ELEVENLABS_API_KEY; restart the server"
    voices = [
        VoiceInfo(id="v-c", name="C", provider="cartesia"),
        VoiceInfo(id="v-e", name="E", provider="elevenlabs"),
    ]
    server.monkeypatch.delenv("CARTESIA_API_KEY", raising=False)
    server.monkeypatch.setattr(providers, "_get_all_tts_voices", AsyncMock(return_value=voices))
    server.monkeypatch.setattr(providers, "filter_tenant_voices", AsyncMock(side_effect=lambda _p, _t, v: v))
    server.monkeypatch.setattr(providers, "get_elevenlabs_last_error", lambda: raw)
    with caplog.at_level(logging.WARNING):
        out = await providers.list_voices(SimpleNamespace(tenant_id="A"), SimpleNamespace(pool=object()))
    by_id = {v["id"]: v for v in out["voices"]}
    assert by_id["v-c"]["available"] is False
    _assert_safe(by_id["v-c"]["unavailable_reason"])
    assert by_id["v-e"]["available"] is True and by_id["v-e"]["unavailable_reason"] is None
    assert by_id["v-c"]["name"] == "C"
    assert out["elevenlabs_error_code"] == "voice_catalog_unavailable"
    blob = json.dumps(out)
    for leaked in ("401", "Unauthorized", "xi-api-key", "ELEVENLABS_API_KEY", "restart"):
        assert leaked not in blob
    assert "elevenlabs_error" in out and out["elevenlabs_error"]
    assert "invalid xi-api-key" in caplog.text, "raw provider text stays in the server log"


async def test_no_elevenlabs_error_means_no_error_keys(server):
    server.monkeypatch.setattr(providers, "_get_all_tts_voices", AsyncMock(return_value=[]))
    server.monkeypatch.setattr(providers, "filter_tenant_voices", AsyncMock(side_effect=lambda _p, _t, v: v))
    server.monkeypatch.setattr(providers, "get_elevenlabs_last_error", lambda: None)
    out = await providers.list_voices(SimpleNamespace(tenant_id="A"), SimpleNamespace(pool=object()))
    assert "elevenlabs_error" not in out and "elevenlabs_error_code" not in out


# ---------------------------------------------------------------- save route

def _save_stubs(monkeypatch, resolve):
    monkeypatch.setattr(credential_resolver, "get_credential_resolver",
                        lambda: SimpleNamespace(resolve=resolve))
    persisted = AsyncMock()
    monkeypatch.setattr(config_endpoint, "_upsert_tenant_config", persisted)
    return persisted


async def test_save_refuses_deepgram_options_when_deepgram_is_not_available(monkeypatch):
    persisted = _save_stubs(monkeypatch, AsyncMock(side_effect=lambda p, **_k: None if p == "deepgram" else "k"))
    monkeypatch.setenv("GROQ_API_KEY", "x")
    cfg = AIProviderConfig(llm_provider="groq", llm_model="openai/gpt-oss-20b")
    with pytest.raises(HTTPException) as caught:
        await config_endpoint.save_config(cfg, SimpleNamespace(tenant_id="A"), SimpleNamespace(pool=object()))
    assert caught.value.status_code == 422
    _assert_safe(caught.value.detail)
    persisted.assert_not_awaited()


async def test_save_refuses_google_tts_when_google_is_not_available(monkeypatch):
    persisted = _save_stubs(monkeypatch, AsyncMock(return_value="k"))
    for name in ("GOOGLE_TTS_API_KEY", "GOOGLE_SERVICE_ACCOUNT_JSON", "GOOGLE_APPLICATION_CREDENTIALS"):
        monkeypatch.delenv(name, raising=False)
    cfg = AIProviderConfig(llm_provider="groq", llm_model="openai/gpt-oss-20b",
                           stt_engine="assemblyai", tts_provider="google",
                           tts_model="chirp3-hd", tts_voice_id="en-US-Chirp3-HD-Leda")
    with pytest.raises(HTTPException) as caught:
        await config_endpoint.save_config(cfg, SimpleNamespace(tenant_id="A"), SimpleNamespace(pool=object()))
    assert caught.value.status_code == 422
    _assert_safe(caught.value.detail)
    persisted.assert_not_awaited()


# ---------------------------------------------------------------- connectors

async def _status(rows, account=None):
    responses = {("connectors", "select"): [_resp(rows)]}
    if account is not None:
        responses[("connector_accounts", "select")] = [account]
    out = await ce.list_connector_statuses(current_user=_user(), db_client=FakeDB(responses))
    return {item.type: item for item in out.items}


async def test_connector_items_carry_display_name_and_keep_existing_fields():
    cards = await _status([{"id": "c1", "type": "calendar", "provider": "outlook_calendar",
                            "status": "expired", "created_at": "2026-09-07T10:00:00Z"}])
    item = cards["calendar"]
    assert item.display_name == "Microsoft Outlook"
    assert item.provider == "outlook_calendar" and item.status == "expired"
    assert item.code == "authorization_expired"
    assert item.message and "_" not in item.message
    dumped = item.model_dump()
    assert {"type", "status", "provider", "last_sync", "error_message", "available",
            "unavailable_reason"} <= set(dumped)
    assert cards["email"].display_name is None and cards["email"].code is None and cards["email"].message is None


async def test_connector_error_item_has_safe_message_and_code_and_never_the_raw_text():
    raw = "FATAL: password authentication failed for user talky_app at 10.0.0.7"
    cards = await _status(
        [{"id": "c1", "type": "email", "provider": "gmail", "status": "active", "created_at": "2026-09-07T10:00:00Z"}],
        account=_resp(None, error=raw),
    )
    item = cards["email"]
    assert item.status == "error" and item.display_name == "Gmail"
    assert item.code == "check_unavailable"
    assert item.message and raw not in item.message and "FATAL" not in item.message
    assert "10.0.0.7" not in json.dumps(item.model_dump())


async def test_connected_connector_has_name_and_no_error_fields(monkeypatch):
    monkeypatch.setattr(ce, "_active_account_health", lambda provider, account: ("connected", None))
    cards = await _status(
        [{"id": "c1", "type": "crm", "provider": "hubspot", "status": "active", "created_at": "2026-09-07T10:00:00Z"}],
        account=_resp([{"access_token_encrypted": "x"}]),
    )
    item = cards["crm"]
    assert item.status == "connected" and item.display_name == "HubSpot"
    assert item.code is None and item.message is None


@pytest.mark.parametrize("message", [
    "Connector credentials could not be checked right now.",
    "The connector has no usable active credential. Reconnect it.",
    "The connector has no active credential record. Reconnect it.",
    "The connector has no saved access credential. Reconnect it.",
    "The Gmail authorization cannot be refreshed. Reconnect it.",
    "The saved authorization cannot be used. Reconnect the connector.",
    "The connection is missing required permissions. Connect it again and grant all requested access.",
    "The saved credential expiry is invalid. Reconnect the connector.",
    "Token refresh is unresolved. Wait for completion; if it stays unresolved, authorize the connector again.",
    "The authorization expired and cannot be refreshed. Reconnect it.",
    "something unexpected from a provider: 500 Internal",
])
def test_every_status_message_maps_to_a_stable_code_and_fixed_text(message):
    code, text = ce._connector_error_code_and_message("error", message)
    assert code in {"check_unavailable", "credential_missing", "authorization_cannot_refresh",
                    "permissions_missing", "refresh_unresolved", "authorization_expired",
                    "connector_error"}
    assert text and "provider" not in text and "500" not in text


# ---------------------------------------------------------------- Outlook refresh

async def test_outlook_refresh_failure_never_carries_provider_text(monkeypatch, caplog):
    from app.infrastructure.connectors.calendar import outlook_calendar as oc
    monkeypatch.setenv("MICROSOFT_CLIENT_ID", "id")
    monkeypatch.setenv("MICROSOFT_CLIENT_SECRET", "secret")
    raw = '{"error":"invalid_grant","error_description":"AADSTS70008: refresh token expired, trace 9f1c"}'

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            return SimpleNamespace(status_code=400, text=raw)

    monkeypatch.setattr(oc.httpx, "AsyncClient", lambda *a, **k: _Client())
    connector = oc.OutlookCalendarConnector(tenant_id="t", connector_id="c")
    with caplog.at_level(logging.ERROR):
        with pytest.raises(Exception) as caught:
            await connector.refresh_tokens("rt")
    text = str(caught.value)
    assert text == "The calendar connection has expired. Connect it again."
    assert "AADSTS" not in text and "invalid_grant" not in text
    assert "AADSTS70008" in caplog.text, "provider text stays in the log"


# ---------------------------------------------------------------- key pools

@pytest.mark.parametrize("provider,single,pool", [
    ("groq", "GROQ_API_KEY", "GROQ_API_KEYS"),
    ("deepgram", "DEEPGRAM_API_KEY", "DEEPGRAM_API_KEYS"),
    ("cartesia", "CARTESIA_API_KEY", "CARTESIA_API_KEYS"),
])
def test_availability_reads_the_singular_key_or_the_pool(monkeypatch, provider, single, pool):
    from app.domain.services.ai_option_availability import platform_availability
    monkeypatch.delenv(single, raising=False)
    monkeypatch.delenv(pool, raising=False)
    assert platform_availability()[provider] is False
    monkeypatch.setenv(single, "k")
    assert platform_availability()[provider] is True
    monkeypatch.delenv(single)
    monkeypatch.setenv(pool, "k1, k2")
    assert platform_availability()[provider] is True
    for empty in ("", " ", ",", " , ,"):
        monkeypatch.setenv(pool, empty)
        assert platform_availability()[provider] is False, repr(empty)


async def test_catalog_lists_groq_and_deepgram_available_with_only_the_pool(server):
    for name in ("GROQ_API_KEY", "DEEPGRAM_API_KEY"):
        server.monkeypatch.delenv(name, raising=False)
    server.monkeypatch.setenv("GROQ_API_KEYS", "a,b")
    server.monkeypatch.setenv("DEEPGRAM_API_KEYS", "a")
    catalog = await _catalog()
    assert catalog.llm["provider_status"]["groq"]["available"] is True
    assert catalog.stt["provider_status"]["deepgram"]["available"] is True
    assert all(m["available"] for m in _all_models(catalog))


async def test_save_accepts_a_deepgram_option_with_only_the_pool_set(monkeypatch):
    from contextlib import asynccontextmanager
    persisted = _save_stubs(monkeypatch, AsyncMock(side_effect=lambda p, **_k: None if p in ("deepgram", "groq") else "k"))
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    monkeypatch.setenv("DEEPGRAM_API_KEYS", "pool-key")
    monkeypatch.setenv("GROQ_API_KEYS", "pool-key")
    monkeypatch.setattr(config_endpoint, "_get_deepgram_voices_for_current_key",
                        AsyncMock(return_value=[SimpleNamespace(id="aura-2-thalia-en")]))

    @asynccontextmanager
    async def acquire(*_a):
        yield object()

    monkeypatch.setattr(config_endpoint, "acquire_with_tenant", acquire)
    cfg = AIProviderConfig(llm_provider="groq", llm_model="openai/gpt-oss-20b", tts_voice_id="aura-2-thalia-en")
    saved = await config_endpoint.save_config(cfg, SimpleNamespace(tenant_id="A"), SimpleNamespace(pool=object()))
    assert saved.config.tts_provider == "deepgram"
    persisted.assert_awaited_once()


# ---------------------------------------------------------------- no setting names in any body

FORBIDDEN = ("_API_KEY", ".env", "KEY", "restart")
SENTENCE = {"code": "ai_option_unavailable", "message": "This option is not switched on for this account yet."}


@pytest.mark.parametrize("provider", ["groq", "cartesia", "deepgram", "google"])
async def test_no_catalogue_or_voice_body_names_a_setting_for_a_missing_provider(server, provider):
    for name in ENV_BY_PROVIDER[provider]:
        server.monkeypatch.delenv(name, raising=False)
    server.monkeypatch.delenv(f"{provider.upper()}_API_KEYS", raising=False)
    catalog = await _catalog()
    voices = [VoiceInfo(id="v", name="V", provider=provider)]
    server.monkeypatch.setattr(providers, "_get_all_tts_voices", AsyncMock(return_value=voices))
    server.monkeypatch.setattr(providers, "filter_tenant_voices", AsyncMock(side_effect=lambda _p, _t, v: v))
    server.monkeypatch.setattr(providers, "get_elevenlabs_last_error", lambda: None)
    out = await providers.list_voices(SimpleNamespace(tenant_id="A"), SimpleNamespace(pool=object()))
    blob = json.dumps(catalog.model_dump(), default=str) + json.dumps(out)
    for bad in FORBIDDEN:
        assert bad not in blob, (provider, bad)


@pytest.mark.parametrize("llm,model,env", [
    ("gemini", "gemini-3.8-flash", ("GEMINI_API_KEY",)),
    ("cerebras", "gpt-oss-120b", ("CEREBRAS_API_KEY",)),
    ("groq", "openai/gpt-oss-20b", ("GROQ_API_KEY", "GROQ_API_KEYS")),
])
async def test_save_refusals_use_the_fixed_sentence_and_code_and_log_the_cause(monkeypatch, caplog, llm, model, env):
    persisted = _save_stubs(monkeypatch, AsyncMock(return_value=None))
    for name in env:
        monkeypatch.delenv(name, raising=False)
    cfg = AIProviderConfig(llm_provider=llm, llm_model=model)
    with caplog.at_level(logging.WARNING):
        with pytest.raises(HTTPException) as caught:
            await config_endpoint.save_config(cfg, SimpleNamespace(tenant_id="A"), SimpleNamespace(pool=object()))
    assert caught.value.status_code == 503
    assert caught.value.detail == SENTENCE
    blob = json.dumps(caught.value.detail)
    for bad in FORBIDDEN:
        assert bad not in blob
    assert f"{llm} key missing" in caplog.text
    persisted.assert_not_awaited()


async def test_save_refusals_for_assemblyai_cartesia_deepgram_google_have_no_setting_names(monkeypatch):
    persisted = _save_stubs(monkeypatch, AsyncMock(return_value=None))
    for name in ("CARTESIA_API_KEY", "CARTESIA_API_KEYS", "DEEPGRAM_API_KEYS", "GOOGLE_TTS_API_KEY",
                 "GOOGLE_SERVICE_ACCOUNT_JSON", "GOOGLE_APPLICATION_CREDENTIALS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "x")
    cases = [
        AIProviderConfig(stt_engine="assemblyai"),
        AIProviderConfig(llm_provider="groq", llm_model="openai/gpt-oss-20b", stt_engine="deepgram_flux", tts_provider="deepgram"),
        AIProviderConfig(llm_provider="groq", llm_model="openai/gpt-oss-20b", stt_engine="deepgram_nova",
                         tts_provider="google", tts_model="chirp3-hd", tts_voice_id="en-US-Chirp3-HD-Leda"),
    ]
    for cfg in cases:
        with pytest.raises(HTTPException) as caught:
            await config_endpoint.save_config(cfg, SimpleNamespace(tenant_id="A"), SimpleNamespace(pool=object()))
        blob = json.dumps(caught.value.detail)
        for bad in FORBIDDEN:
            assert bad not in blob, (cfg.tts_provider, bad)
    persisted.assert_not_awaited()


# ---------------------------------------------------------------- calendar sentences

class _Resp:
    status_code = 500
    text = "PROVIDER SECRET TEXT 0xDEAD"

    def json(self):
        return {}


@pytest.mark.parametrize("modname,cls", [
    ("outlook_calendar", "OutlookCalendarConnector"),
    ("google_calendar", "GoogleCalendarConnector"),
])
async def test_calendar_exchange_create_update_failures_use_plain_sentences(monkeypatch, caplog, modname, cls):
    import importlib
    from datetime import datetime, timedelta
    mod = importlib.import_module(f"app.infrastructure.connectors.calendar.{modname}")
    for name in ("MICROSOFT_CLIENT_ID", "MICROSOFT_CLIENT_SECRET", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"):
        monkeypatch.setenv(name, "x")

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            return _Resp()

        async def patch(self, *a, **k):
            return _Resp()

        async def put(self, *a, **k):
            return _Resp()

        async def get(self, *a, **k):
            ok = _Resp()
            ok.status_code = 200  # an existing event, so update reaches its PATCH
            return ok

    monkeypatch.setattr(mod.httpx, "AsyncClient", lambda *a, **k: _Client())
    connector = getattr(mod, cls)(tenant_id="t", connector_id="c")
    await connector.set_access_token("tok")
    now = datetime(2026, 12, 1, 10, 0)
    calls = [
        (lambda: connector.exchange_code("c", "https://x/cb"), "The calendar could not be connected. Try again."),
        (lambda: connector.create_event("T", now, now + timedelta(hours=1)),
         "The meeting could not be added to the calendar."),
        (lambda: connector.update_event("e1", title="T"), "The meeting could not be updated in the calendar."),
    ]
    for call, sentence in calls:
        with caplog.at_level(logging.WARNING), pytest.raises(ValueError) as caught:
            await call()
        assert str(caught.value) == sentence
    assert "PROVIDER SECRET TEXT" in caplog.text
