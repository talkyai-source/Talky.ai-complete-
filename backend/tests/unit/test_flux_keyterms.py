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
