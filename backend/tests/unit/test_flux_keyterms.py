"""Unit tests for Deepgram Flux keyterm prompting wiring.

Keyterm prompting biases Flux toward expected vocabulary (email domains, the
words callers say while spelling). These tests pin the parsing rules and the
URL-encoding so a refactor can't silently drop keyterms from the request.
"""
import pytest

from app.infrastructure.stt.deepgram_flux import DeepgramFluxSTTProvider


@pytest.mark.asyncio
async def test_keyterms_from_config_list():
    p = DeepgramFluxSTTProvider()
    await p.initialize({"api_key": "k", "keyterms": ["gmail.com", "dot"]})
    assert p._keyterms == ["gmail.com", "dot"]


@pytest.mark.asyncio
async def test_keyterms_from_comma_string_and_dedupe():
    p = DeepgramFluxSTTProvider()
    # comma string (env-var shape) + duplicate (case-insensitive) + blanks
    await p.initialize({"api_key": "k", "keyterms": "gmail.com, dot , Gmail.com,"})
    assert p._keyterms == ["gmail.com", "dot"]


@pytest.mark.asyncio
async def test_env_override_wins(monkeypatch):
    monkeypatch.setenv("DEEPGRAM_FLUX_KEYTERMS", "outlook.com,at sign")
    p = DeepgramFluxSTTProvider()
    await p.initialize({"api_key": "k", "keyterms": ["gmail.com"]})
    assert p._keyterms == ["outlook.com", "at sign"]


@pytest.mark.asyncio
async def test_no_keyterms_yields_no_params():
    p = DeepgramFluxSTTProvider()
    await p.initialize({"api_key": "k"})
    assert p._keyterms == []
    assert p._keyterm_params() == []


@pytest.mark.asyncio
async def test_keyterm_params_are_url_encoded():
    p = DeepgramFluxSTTProvider()
    await p.initialize({"api_key": "k", "keyterms": ["dot com", "at sign"]})
    params = p._keyterm_params()
    # spaces must be percent-encoded so the query string stays valid
    assert params == [("keyterm", "dot%20com"), ("keyterm", "at%20sign")]


def test_orchestrator_loads_keyterms_from_providers_yaml():
    """The orchestrator default (used by BOTH telephony and ask-AI) is sourced
    from providers.yaml, so the configured list is live on every session."""
    from app.domain.services import voice_orchestrator as vo

    vo._FLUX_KEYTERMS_CACHE = None  # force a fresh read
    terms = vo._default_flux_keyterms()
    assert isinstance(terms, list) and terms, "expected non-empty keyterms"
    assert any("gmail.com" == t.lower() for t in terms)


def test_voice_session_config_defaults_to_empty_keyterms():
    """Per-session keyterms default empty so the providers.yaml list is used."""
    from app.domain.services.voice_orchestrator import VoiceSessionConfig

    assert VoiceSessionConfig().stt_keyterms == []


@pytest.mark.asyncio
async def test_mip_opt_out_default_on():
    p = DeepgramFluxSTTProvider()
    await p.initialize({"api_key": "k"})
    assert p._mip_opt_out is True
    assert ("mip_opt_out", "true") in p._meta_params()


@pytest.mark.asyncio
async def test_mip_opt_out_can_be_disabled():
    p = DeepgramFluxSTTProvider()
    await p.initialize({"api_key": "k", "mip_opt_out": False})
    assert all(k != "mip_opt_out" for k, _ in p._meta_params())


@pytest.mark.asyncio
async def test_tags_include_static_and_per_call():
    p = DeepgramFluxSTTProvider()
    await p.initialize({"api_key": "k", "tags": ["tenant:t1", "campaign:c1"]})
    params = p._meta_params(call_id="abc 123")
    tags = [v for k, v in params if k == "tag"]
    assert "tenant:t1" in tags
    assert "campaign:c1" in tags
    # per-call tag appended and URL-encoded
    assert "call:abc%20123" in tags
