"""The per-call wiring log must say which STT provider the call uses and
whether the Flux-only end-of-turn settings reach it."""
from types import SimpleNamespace

import pytest

from app.domain.services.telephony.prewarm import stt_wiring_for_log


def _cfg(engine, lang="en", aai=None):
    return SimpleNamespace(stt_provider_type=engine, stt_language=lang, assemblyai_settings=aai)


@pytest.mark.parametrize("engine,lang,provider,applied", [
    ("deepgram_flux", "en", "deepgram_flux", True),
    (None, "en", "deepgram_flux", True),
    ("deepgram_nova", "en", "deepgram_nova", False),
    ("nova-3", "en", "deepgram_nova", False),
    ("deepgram_flux", "es", "deepgram_nova", False),
    ("assemblyai", "en", "assemblyai", False),
])
def test_provider_and_applied(engine, lang, provider, applied):
    w = stt_wiring_for_log(_cfg(engine, lang))
    assert (w["provider"], w["applied"]) == (provider, applied)


def test_assemblyai_turn_detection_names_and_numbers_logged():
    w = stt_wiring_for_log(_cfg("assemblyai", aai={"min_turn_silence": 400, "max_turn_silence": 1500}))
    assert w["aai_turn_detection"].get("min_turn_silence") == 400
    assert w["aai_turn_detection"].get("max_turn_silence") == 1500
    assert "api_key" not in str(w)


def test_handed_settings_unchanged_flux_still_gets_eot():
    import asyncio
    from unittest.mock import patch
    from app.domain.services.voice_orchestrator import VoiceOrchestrator, VoiceSessionConfig

    seen = {}

    class Fake:
        async def initialize(self, cfg):
            seen.update(cfg)

    cfg = VoiceSessionConfig(stt_eot_threshold=0.85, stt_eager_eot_threshold=0.7, stt_eot_timeout_ms=1000)
    orch = VoiceOrchestrator.__new__(VoiceOrchestrator)
    with patch("app.infrastructure.stt.deepgram_flux.DeepgramFluxSTTProvider", Fake), \
         patch("app.domain.services.credential_resolver.get_credential_resolver") as r:
        async def _res(*a, **k):
            return None
        r.return_value.resolve_with_metadata = _res
        asyncio.run(orch._create_stt_provider(cfg))
    assert (seen["eot_threshold"], seen["eager_eot_threshold"], seen["eot_timeout_ms"]) == (0.85, 0.7, 1000)
