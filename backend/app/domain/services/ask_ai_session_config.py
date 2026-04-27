"""
Shared Ask AI session config.

Keeps the live Ask AI websocket endpoint and provider prewarm path on the
same VoiceSessionConfig so turn-taking settings cannot drift.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DO NOT MIX WITH THE CAMPAIGN PROMPT COMPOSER.

This module is for Ask AI only — the Talky.ai product's public web
demo receptionist ("Hi, you've reached the Talk-Lee receptionist...").
It uses a fixed, short prompt with product-info keyword injection.

Campaign outbound telephony (lead gen / support / receptionist role)
uses an entirely different code path:
  - entry:    telephony_session_config.build_telephony_session_config()
  - prompts:  app.services.scripts.prompts (guardrails + personas + slots)

Do not import compose_prompt or the PERSONAS registry here, and do not
import ASK_AI_SYSTEM_PROMPT in the telephony path. The two systems
serve different audiences (product demo vs real customer campaigns)
and their prompts intentionally differ in tone, length, and structure.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

from app.domain.models.agent_config import (
    AgentConfig,
    AgentGoal,
    ConversationFlow,
    ConversationRule,
)
from app.domain.services.voice_orchestrator import VoiceSessionConfig

# Fixed configuration for Ask AI — using Cartesia Tessa (Kind Companion) +
# Gemini 2.5 Flash with thinking DISABLED. Thinking tokens are wasted latency
# for short conversational replies; turning them off drops TTFT meaningfully.
ASK_AI_CONFIG = {
    "tts_provider": "cartesia",
    "voice_id": "6ccbfb76-1fc6-48f7-b71d-91ac6298247b",  # Tessa - Kind Companion
    "model_id": "sonic-3",
    "sample_rate": 24000,
    # Switched from Groq llama-3.1-8b to Gemini 2.5 Flash (no thinking) on
    # 2026-04-23. Revert to "llama-3.1-8b-instant" + llm_provider "groq" if
    # Gemini quality regresses — see voice_orchestrator._LLM_API_KEY_ENV.
    "llm_provider": "gemini",
    "llm_model": "gemini-2.5-flash",
    "llm_temperature": 0.6,
    # 90 tokens covers 4-sentence pricing answers while keeping normal replies short.
    "llm_max_tokens": 90,
    # 0 = disable Gemini's internal reasoning ("thinking") tokens entirely.
    # Keeps TTFT low on the conversational hot path. Flip to None if you ever
    # want the model to use its reasoning budget again.
    "llm_thinking_budget": 0,
}

# Re-export from the constants module (no circular-import risk there).
from app.domain.services.ask_ai_constants import TALKY_PRODUCT_INFO, PRODUCT_KEYWORDS  # noqa: F401

# The exact greeting already played to the caller by the client-side audio clip.
# Injected into conversation_history as the first assistant turn so the LLM
# never re-greets and always knows what was already said.
ASK_AI_GREETING = "Hi, you've reached the Talk-Lee receptionist team — how can I help you today?"

# Lean base prompt — no product info embedded.
# Product info is appended at inference time via keyword detection in the pipeline.
ASK_AI_SYSTEM_PROMPT = (
    "You are a voice receptionist for Talk-Lee.\n\n"
    "IMPORTANT: You have already greeted the caller with: "
    f"\"{ASK_AI_GREETING}\" — do NOT greet again. "
    "Jump straight to answering whatever they say next.\n\n"
    "Speak in 1 to 2 short natural sentences. "
    "No markdown, bullets, or headings. "
    "No openers like \"Sure\", \"Absolutely\", \"Great\", or \"Of course\". "
    "Never say you are an AI or mention technology. "
    "If you don't know something, offer to have someone follow up."
)


def create_ask_ai_agent_config() -> AgentConfig:
    """Create the fixed agent config used by Ask AI demo sessions."""
    return AgentConfig(
        agent_name="Tessa",
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
        llm_provider_type=ASK_AI_CONFIG["llm_provider"],
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
        llm_thinking_budget=ASK_AI_CONFIG["llm_thinking_budget"],
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
