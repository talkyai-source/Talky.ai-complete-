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

# Fixed configuration for Ask AI - using Deepgram TTS.
ASK_AI_CONFIG = {
    "voice_id": "aura-2-andromeda-en",
    "sample_rate": 24000,
    "model_id": "aura-2",
    "llm_model": "openai/gpt-oss-20b",
    "llm_temperature": 0.6,
    # 90 tokens covers 4-sentence pricing answers while keeping normal replies short.
    "llm_max_tokens": 90,
}

TALKY_PRODUCT_INFO = """
## About Talky.ai

Talky.ai is a voice AI platform that lets businesses automate phone calls with natural-sounding agents. Think of it as hiring a tireless team member who can call hundreds of customers while sounding genuinely human.

### Core Features
- Automated outbound calling — follow-ups, reminders, surveys
- Natural voice conversations — not robotic IVR menus
- Smart lead qualification and appointment booking
- Real-time analytics dashboard
- Works with your existing CRM and tools

### Packages

**Basic — $29/month**
- 300 call minutes, 1 AI agent, basic analytics

**Professional — $79/month** (most popular)
- 1,500 call minutes, 3 AI agents, advanced analytics, custom voices

**Enterprise — $199/month**
- 5,000 call minutes, 10 AI agents, full suite, API access

### Why Businesses Love Talky
- Sounds natural, not robotic
- Available around the clock
- Setup takes minutes, not weeks
- Scales with your business
"""

ASK_AI_SYSTEM_PROMPT = f"""You are a friendly voice assistant for Talky.ai.

Your personality: friendly, warm, and helpful. You're genuinely curious and positive.

{TALKY_PRODUCT_INFO}

## Important Guidelines
- Keep responses short and spoken naturally
- Use plain spoken sentences only - no markdown, no bullets, no headings, no XML or HTML tags
- Usually answer in 1 to 2 sentences, but if asked about pricing, plans, or packages you may use up to 4 short sentences so all tiers are covered
- Sound natural and conversational, like talking to a friend
- Never open your response with filler phrases like "Sure thing", "Absolutely", "Certainly", "Of course", "Great", "No problem", "Definitely" — go straight to the answer
- Never say you are an "AI" or mention technology
- If interrupted, stop and listen immediately
- Answer questions about Talky naturally
- If you don't know something, offer to have someone follow up
- Be genuinely helpful and curious about what the user needs"""


def create_ask_ai_agent_config() -> AgentConfig:
    """Create the fixed agent config used by Ask AI demo sessions."""
    return AgentConfig(
        agent_name="Assistant",
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
        tts_provider_type="deepgram",
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
