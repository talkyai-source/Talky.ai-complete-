"""Selected audio settings reach cloud session boundaries; transport rates stay explicit."""
from unittest.mock import AsyncMock

import pytest

from app.domain.models.ai_config import AIProviderConfig
from app.domain.services.voice_tuning import VoiceTuning, VoiceTuningResolver


@pytest.mark.parametrize("bridge,rate", [("twilio", 8000), ("vonage", 16000)])
@pytest.mark.parametrize("engine,language,expected", [("deepgram_nova", "es", "deepgram_nova"), ("deepgram_flux", "en", "deepgram_flux"), ("deepgram_flux", "es", "deepgram_nova")])
@pytest.mark.asyncio
async def test_cloud_bridge_keeps_selected_engine_language_and_tuning(monkeypatch, bridge, rate, engine, language, expected):
    from importlib import import_module
    from app.domain.services import tenant_ai_config_resolver, voice_tuning
    cfg = AIProviderConfig(stt_engine=engine, stt_language=language, tts_sample_rate=48000)
    monkeypatch.setattr(tenant_ai_config_resolver, "resolve_ai_config_for_did", AsyncMock(return_value=("tenant-a", cfg)))
    tuning = VoiceTuning(stt_eot_timeout_ms=900, stt_eot_threshold=.8, stt_eager_eot_threshold=.6, turn_0_min_confidence=.55)
    resolver = VoiceTuningResolver()
    monkeypatch.setattr(resolver, "for_tenant_async", AsyncMock(return_value=tuning))
    monkeypatch.setattr(voice_tuning, "get_voice_tuning_resolver", lambda: resolver)
    module = import_module(f"app.api.v1.endpoints.{bridge}_bridge")
    result = await getattr(module, f"_build_{bridge}_session_config")("synthetic-did")
    assert result.stt_provider_type == expected
    assert result.stt_language == language
    assert result.stt_model == ("nova-3" if expected == "deepgram_nova" else "flux-general-en")
    assert result.stt_eot_timeout_ms == 900
    assert result.stt_eot_threshold == .8
    assert result.stt_eager_eot_threshold == .6
    assert result.turn_0_min_confidence == .55
    assert result.stt_sample_rate == result.tts_sample_rate == result.gateway_sample_rate == rate
    resolver.for_tenant_async.assert_awaited_once_with("tenant-a", require_available=True)


@pytest.mark.parametrize("key,value", [
    ("stt_eot_threshold", 4), ("stt_eot_threshold", float("nan")),
    ("stt_eager_eot_threshold", float("inf")), ("stt_eot_timeout_ms", 499),
    ("stt_eot_timeout_ms", 60001), ("stt_eot_timeout_ms", 1000.5),
    ("stt_eot_threshold", True), ("turn_0_min_confidence", -1),
    ("turn_0_min_alpha_chars", 0), ("turn_0_min_alpha_chars", 11),
])
def test_user_tuning_rejects_invalid_values(key, value):
    with pytest.raises(ValueError):
        VoiceTuningResolver().coerce_user_partial({key: value})


def test_user_tuning_rejects_reversed_thresholds():
    with pytest.raises(ValueError, match="eager"):
        VoiceTuningResolver().coerce_user_partial({"stt_eot_threshold": .6, "stt_eager_eot_threshold": .8})


def test_user_tuning_preserves_supported_values_and_explicit_eager_disable():
    data = {"stt_eot_threshold": .8, "stt_eager_eot_threshold": None, "stt_eot_timeout_ms": "1500"}
    assert VoiceTuningResolver().coerce_user_partial(data) == {**data, "stt_eot_timeout_ms": 1500}


def test_legacy_out_of_range_tuning_does_not_reach_session(monkeypatch):
    resolver = VoiceTuningResolver()
    monkeypatch.setattr(resolver, "_load_default_dict", lambda: {**VoiceTuning().__dict__})
    resolver._cached_overrides = {"tenant-a": resolver._coerce_partial({"stt_eot_threshold": float("nan"), "stt_eot_timeout_ms": -1}, scope="fixture")}
    assert resolver.for_tenant("tenant-a") == VoiceTuning()


def test_partial_user_threshold_compares_to_effective_env_partner():
    resolver = VoiceTuningResolver()
    resolver._cached_defaults = dict(VoiceTuning().__dict__)
    resolver._cached_overrides = {"tenant-a": {"stt_eot_threshold": .9}}
    assert resolver.validate_user_partial_for_tenant({"stt_eager_eot_threshold": .9}, "tenant-a") == {"stt_eager_eot_threshold": .9}
    with pytest.raises(ValueError, match="eager"):
        resolver.validate_user_partial_for_tenant({"stt_eager_eot_threshold": .9}, "tenant-b")
    with pytest.raises(ValueError, match="eager"):
        resolver.validate_user_partial_for_tenant({"stt_eot_threshold": .5}, "tenant-a")


@pytest.mark.parametrize("sample_rate", [32000, 48000, 12345])
@pytest.mark.asyncio
async def test_elevenlabs_rejects_unsupported_rate_before_http(sample_rate):
    from tests.unit.test_elevenlabs_partial_audio_retry import _FakeSession, _build_provider
    session = _FakeSession([])
    provider = _build_provider(session)
    with pytest.raises(ValueError, match="sample rate"):
        async for _ in provider.stream_synthesize("Synthetic sample", "voice-1", sample_rate=sample_rate):
            pass
    assert session.post_calls == 0


@pytest.mark.parametrize("sample_rate", [8000, 16000, 22050, 24000, 44100])
@pytest.mark.asyncio
async def test_elevenlabs_wire_format_matches_returned_audio_rate(sample_rate):
    from tests.unit.test_elevenlabs_partial_audio_retry import _FakeSession, _FakeResponse, _FakeChunkStream, _build_provider
    class RecordingSession(_FakeSession):
        def post(self, url, **kwargs):
            self.request = (url, kwargs)
            return super().post(url, **kwargs)
    session = RecordingSession([_FakeResponse(200, _FakeChunkStream([b"\0" * 100]))])
    provider = _build_provider(session)
    provider._model_id = "eleven_multilingual_v2"
    chunks = [c async for c in provider.stream_synthesize("Synthetic sample", "selected-voice", sample_rate=sample_rate)]
    url, request = session.request
    assert "/selected-voice/" in url
    assert request["params"]["output_format"] == f"pcm_{sample_rate}"
    assert request["json"]["model_id"] == "eleven_multilingual_v2"
    assert [c.sample_rate for c in chunks] == [sample_rate]


@pytest.mark.parametrize("rate", [8000, 16000, 22050, 24000, 44100, 48000])
def test_cartesia_payload_preserves_voice_model_and_supported_rate(rate):
    from app.infrastructure.tts.cartesia import CartesiaTTSProvider
    provider = CartesiaTTSProvider()
    provider._model_id = "sonic-2"
    payload = provider._build_payload("Synthetic sample", "selected-voice", rate, "en", None, None)
    assert payload["model_id"] == "sonic-2"
    assert payload["voice"] == {"mode": "id", "id": "selected-voice"}
    assert payload["output_format"] == {"container": "raw", "encoding": "pcm_s16le", "sample_rate": rate}
    assert "generation_config" not in payload


@pytest.mark.parametrize("rate", [32000, 0, 12345])
def test_cartesia_rejects_invalid_pcm_rate_before_building_wire_payload(rate):
    from app.infrastructure.tts.cartesia import CartesiaTTSProvider
    with pytest.raises(ValueError, match="sample rate"):
        CartesiaTTSProvider()._build_payload("Synthetic sample", "selected-voice", rate, "en", None, None)


def test_unknown_engine_cannot_silently_become_flux_or_nova():
    from app.domain.services.telephony_session_config import resolve_stt_selection
    for language in ("en", "es"):
        with pytest.raises(ValueError, match="STT engine"):
            resolve_stt_selection(AIProviderConfig(stt_engine="unknown-engine", stt_language=language))
    assert resolve_stt_selection(AIProviderConfig(stt_engine=""))["stt_provider_type"] == "deepgram_flux"


@pytest.mark.asyncio
async def test_serialized_audio_profile_manifest(monkeypatch):
    """Capture real serializers behind fake transports, with no keys/content in output."""
    import json
    import os
    from pathlib import Path
    from types import SimpleNamespace
    from urllib.parse import urlparse, parse_qs
    from unittest.mock import Mock
    from app.domain.models.ai_config import CARTESIA_VOICES, GOOGLE_CHIRP3_VOICES, DEEPGRAM_AURA2_VOICES
    from app.infrastructure.stt.deepgram_flux import DeepgramFluxSTTProvider
    from app.infrastructure.stt.deepgram_nova import DeepgramNovaSTTProvider
    from app.infrastructure.tts.cartesia import CartesiaTTSProvider
    from app.infrastructure.tts.deepgram_tts import DeepgramTTSProvider
    from app.infrastructure.tts.google_tts_streaming import GoogleTTSStreamingProvider
    from tests.unit.test_google_tts_streaming_hardening import _FakeResponseStream

    flux = DeepgramFluxSTTProvider()
    for name in ("DEEPGRAM_FLUX_KEYTERMS", "DEEPGRAM_FLUX_CAPTURE_KEYTERMS", "FLUX_NUMERALS"):
        monkeypatch.delenv(name, raising=False)
    await flux.initialize({"api_key": "synthetic-key", "model": "flux-general-en", "sample_rate": 16000, "encoding": "linear16", "eot_threshold": .8, "eager_eot_threshold": .6, "eot_timeout_ms": 900})
    flux_wire = dict(flux._build_connection_params("synthetic-call"))
    assert flux_wire["eot_timeout_ms"] == "900"
    assert flux_wire["sample_rate"] == "16000"

    nova = DeepgramNovaSTTProvider()
    nova._endpointing_ms, nova._utterance_end_ms, nova._numerals = 300, 1000, True
    connect = Mock(side_effect=RuntimeError("synthetic wire boundary"))
    nova._client = SimpleNamespace(listen=SimpleNamespace(v1=SimpleNamespace(connect=connect)))
    async def empty_audio():
        if False:
            yield None
    with pytest.raises(RuntimeError, match="synthetic wire boundary"):
        await anext(nova.stream_transcribe(empty_audio(), language="es"))
    nova_wire = connect.call_args.kwargs
    assert nova_wire["model"] == "nova-3" and nova_wire["language"] == "es"

    cartesia = CartesiaTTSProvider()
    cartesia._model_id = "sonic-3"
    cartesia_wire = cartesia._build_payload("Synthetic fixture", CARTESIA_VOICES[0].id, 16000, "en", None, None)
    for field in ("transcript", "context_id"):
        cartesia_wire.pop(field)

    deepgram = DeepgramTTSProvider()
    connect_tts = AsyncMock(return_value=SimpleNamespace(closed=False))
    deepgram._api_key = "synthetic-key"
    deepgram._session = SimpleNamespace(ws_connect=connect_tts)
    await deepgram._get_connection("aura-2-thalia-en", 16000)
    deepgram_wire = {key: value[0] for key, value in parse_qs(urlparse(connect_tts.call_args.args[0]).query).items()}
    assert deepgram_wire == {"model": "aura-2-thalia-en", "encoding": "linear16", "sample_rate": "16000", "container": "none"}

    class GoogleClient:
        async def streaming_synthesize(self, requests):
            self.requests = [request async for request in requests]
            return _FakeResponseStream([])
    google = GoogleTTSStreamingProvider()
    google._client = GoogleClient()
    assert [chunk async for chunk in google._streaming_attempt("Synthetic fixture", GOOGLE_CHIRP3_VOICES[0].id, "en-US", 16000, 1.0)] == []
    cfg = google._client.requests[0].streaming_config
    google_wire = {"voice": cfg.voice.name, "language": cfg.voice.language_code,
                   "encoding": "PCM", "sample_rate_hz": cfg.streaming_audio_config.sample_rate_hertz,
                   "speaking_rate": cfg.streaming_audio_config.speaking_rate}
    assert google_wire["sample_rate_hz"] == 16000

    artifact = {
        "scope": "Offline synthetic serializers/fake transport only. No provider acceptance, hearing, latency, or deployed configuration proof.",
        "transport_adaptations": [
            {"path": "SIP / campaign browser", "configured_tts_hz": 48000, "effective_stt_tts_hz": 16000, "reason": "Existing mono PCM gateway transport contract"},
            {"path": "Twilio", "configured_tts_hz": 48000, "effective_stt_tts_hz": 8000, "reason": "Existing 8kHz mu-law transport; internal STT decoded linear16"},
            {"path": "Vonage", "configured_tts_hz": 48000, "effective_stt_tts_hz": 16000, "reason": "Existing 16kHz linear16 websocket transport"},
        ],
        "stt_wire": {"flux": flux_wire, "nova": nova_wire},
        "tts_wire": {"cartesia": cartesia_wire, "deepgram": deepgram_wire, "google": google_wire},
        "elevenlabs_wire": {"evidence": "test_elevenlabs_wire_format_matches_returned_audio_rate captures actual HTTP request for each supported rate", "rates_hz": [8000,16000,22050,24000,44100], "model": "eleven_multilingual_v2", "voice": "selected-voice", "format": "pcm_<selected rate>"},
        "static_voice_inventory": {provider: [{"id": voice.id, "name": voice.name, "language": voice.language} for voice in voices]
                                   for provider, voices in (("cartesia_fallback", CARTESIA_VOICES), ("google", GOOGLE_CHIRP3_VOICES), ("deepgram", DEEPGRAM_AURA2_VOICES))},
        "catalog_limits": "Cartesia/Deepgram catalogs may be filtered/refreshed by account; ElevenLabs account catalog and clones are dynamic and were not queried. Static inventory is not a live availability claim.",
    }
    output = os.getenv("AG01_AUDIO_PROFILE_OUTPUT")
    if output:
        Path(output).write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
