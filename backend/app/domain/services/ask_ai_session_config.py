"""
Shared Ask AI session config.

Keeps the live Ask AI websocket endpoint and provider prewarm path on the
same VoiceSessionConfig so turn-taking settings cannot drift.
"""

from app.domain.models.agent_config import (
    AgentConfig,
    AgentGoal,
    ConversationFlow,
    ConversationRule,
)
from app.domain.services.voice_orchestrator import VoiceSessionConfig

# Fixed configuration for Ask AI - using Google Chirp3-HD Zephyr.
ASK_AI_CONFIG = {
    "tts_provider": "google",
    "voice_id": "en-US-Chirp3-HD-Zephyr",  # Zephyr - Youthful, vibrant female voice
    "model_id": "Chirp3-HD",
    "sample_rate": 24000,
    "llm_model": "llama-3.1-8b-instant",   # 8.1B params — 560 t/s, ultra-low latency
    "llm_temperature": 0.6,
    # 90 tokens covers 4-sentence pricing answers while keeping normal replies short.
    "llm_max_tokens": 90,
}

# Re-export from the constants module (no circular-import risk there).
from app.domain.services.ask_ai_constants import TALKY_PRODUCT_INFO, PRODUCT_KEYWORDS  # noqa: F401

# Lean base prompt — no product info embedded.
# Product info is appended at inference time via keyword detection in the pipeline.
ASK_AI_SYSTEM_PROMPT = (
    "You are Zephyr, a warm and friendly voice assistant for Talky.ai.\n\n"
    "Speak in 1 to 2 short natural sentences. "
    "No markdown, bullets, or headings in your replies. "
    "Go straight to the answer — no openers like \"Sure\", \"Absolutely\", \"Great\", or \"Of course\". "
    "Never say you are an AI or mention technology. "
    "If you don't know something, offer to have someone follow up."
)


def create_ask_ai_agent_config() -> AgentConfig:
    """Create the fixed agent config used by Ask AI demo sessions."""
    return AgentConfig(
        agent_name="Zephyr",
        company_name="Talky.ai",
        business_type="Voice AI Platform",
        goal=AgentGoal.INFORMATION_GATHERING,
        tone="friendly, warm, and helpful",
        flow=ConversationFlow(max_objection_attempts=3),
        rules=ConversationRule(
            do_not_say_rules=[
                "Keep responses brief - 1 to 2 sentences",
                "Be helpful and natural",
                "Never mention technical terms or that you are an AI",
                "Never use markdown, bullet lists, headings, or XML tags in spoken replies",
            ]
        ),
        max_conversation_turns=20,
        response_max_sentences=2,
    )


def build_ask_ai_session_config() -> VoiceSessionConfig:
    """Build the shared Ask AI VoiceSessionConfig."""
    return VoiceSessionConfig(
        stt_provider_type="deepgram_flux",
        llm_provider_type="groq",
        tts_provider_type=ASK_AI_CONFIG["tts_provider"],
        stt_model="flux-general-en",
        stt_sample_rate=16000,
        stt_encoding="linear16",
        stt_eot_threshold=0.7,
        stt_eager_eot_threshold=0.5,
        stt_eot_timeout_ms=3000,
        llm_model=ASK_AI_CONFIG["llm_model"],
        llm_temperature=ASK_AI_CONFIG["llm_temperature"],
        llm_max_tokens=ASK_AI_CONFIG["llm_max_tokens"],
        voice_id=ASK_AI_CONFIG["voice_id"],
        tts_model=ASK_AI_CONFIG["model_id"],
        tts_sample_rate=ASK_AI_CONFIG["sample_rate"],
        gateway_sample_rate=ASK_AI_CONFIG["sample_rate"],
        gateway_input_sample_rate=16000,
        gateway_channels=1,
        gateway_bit_depth=16,
        gateway_target_buffer_ms=40,
        mute_during_tts=False,
        session_type="ask_ai",
        agent_config=create_ask_ai_agent_config(),
        system_prompt=ASK_AI_SYSTEM_PROMPT,
        campaign_id="ask-ai",
        lead_id="demo-user",
    )
