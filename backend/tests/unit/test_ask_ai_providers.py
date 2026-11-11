"""The website demo hears with AssemblyAI, answers with DeepSeek and speaks with one voice.

Owner decision 12 Nov 2026: AssemblyAI Universal-3.6 Pro for speech recognition,
DeepSeek V4.1 Flash (thinking off) for the replies, and Cartesia Tessa, the
platform's default voice, for speech. The live socket and the startup prewarm
both build this config, so they cannot drift apart.
"""
from app.domain.models.assemblyai_config import ASSEMBLYAI_MODEL, AssemblyAISettings
from app.domain.services.ask_ai_session_config import ASK_AI_CONFIG, build_ask_ai_session_config
from app.domain.services.voice_orchestrator import VoiceOrchestrator


def test_the_demo_hears_with_assemblyai_in_english():
    config = build_ask_ai_session_config()
    assert config.stt_provider_type == "assemblyai"
    assert config.stt_model == ASSEMBLYAI_MODEL == "universal-3-6-pro"
    assert config.stt_language == "en"
    # Settings the AssemblyAI provider accepts as they are.
    assert AssemblyAISettings.model_validate(config.assemblyai_settings)
    # The browser sends 16 kHz PCM, which AssemblyAI receives as is.
    assert (config.stt_sample_rate, config.stt_encoding) == (16000, "linear16")


def test_the_demo_answers_with_deepseek_flash():
    config = build_ask_ai_session_config()
    assert config.llm_provider_type == "deepseek"
    assert config.llm_model == "deepseek-flash"
    assert VoiceOrchestrator._LLM_API_KEY_ENV["deepseek"] == "DEEPSEEK_API_KEY"


def test_the_demo_speaks_with_one_voice():
    config = build_ask_ai_session_config()
    assert config.tts_provider_type == "cartesia" == ASK_AI_CONFIG["tts_provider"]
    assert config.voice_id == "6ccbfb76-1fc6-48f7-b71d-91ac6298247b"  # Tessa, the platform default
    assert config.tts_model == "sonic-3"
    assert config.session_type == "ask_ai" and config.campaign_id == "ask-ai"
