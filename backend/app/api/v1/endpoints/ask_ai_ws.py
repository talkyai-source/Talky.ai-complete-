"""
Ask AI WebSocket - Simplified Voice Assistant Demo

One-click voice interaction without voice selection.
Uses Deepgram Aura-2 TTS (Google Chirp3-HD commented out for future switching).

Voice: Andromeda (aura-2-andromeda-en) - Customer service optimized
Sample Rate: 24000 Hz (Deepgram recommended for streaming TTS)

Day 41: Refactored to use VoiceOrchestrator for lifecycle management.
"""
import json
import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.domain.models.agent_config import AgentConfig, AgentGoal, ConversationFlow, ConversationRule
from app.domain.services.voice_orchestrator import VoiceSessionConfig

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Ask AI"])

# Fixed configuration for Ask AI - using Deepgram TTS (Google commented out for switching)
# Deepgram best practices:
# - Voice: Andromeda (aura-2-andromeda-en) - Casual, Expressive, Comfortable for customer service
# - Sample rate: 24000 Hz (Deepgram streaming default for best quality)
# - Text chunking: Enabled at sentence boundaries for natural speech
ASK_AI_CONFIG = {
    # Deepgram Aura-2 Andromeda voice - optimized for customer service/IVR
    # Voice characteristics: Casual, Expressive, Comfortable
    "voice_id": "aura-2-andromeda-en",
    "sample_rate": 24000,  # Deepgram recommended for streaming TTS (best quality)
    "model_id": "aura-2",
    # LLM settings — use gpt-oss-120b for accurate responses
    "llm_model": "openai/gpt-oss-120b",
    "llm_temperature": 0.6,
    "llm_max_tokens": 150
}

# GOOGLE TTS CONFIGURATION (commented out - for switching back if needed)
# ASK_AI_CONFIG_GOOGLE = {
#     # Google Chirp3-HD Leda voice - professional female
#     "voice_id": "en-US-Chirp3-HD-Leda",
#     "sample_rate": 16000,  # Standard 16kHz for voice (lower latency, compatible with WebRTC)
#     "model_id": "Chirp3-HD",
#     # LLM settings — use gpt-oss-120b for accurate responses
#     "llm_model": "openai/gpt-oss-120b",
#     "llm_temperature": 0.6,
#     "llm_max_tokens": 150
# }

# Talky.ai Product Information for the assistant
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
- Keep responses SHORT (1-2 sentences max) - this is a voice conversation
- Sound natural and conversational, like talking to a friend
- Never say you are an "AI" or mention technology
- If interrupted, stop and listen immediately
- Answer questions about Talky naturally
- If you don't know something, offer to have someone follow up
- Be genuinely helpful and curious about what the user needs"""


def _create_ask_ai_agent_config() -> AgentConfig:
    """Create agent config optimized for Ask AI."""
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
                "Never mention technical terms or that you are an AI"
            ]
        ),
        max_conversation_turns=20,
        response_max_sentences=2
    )


def _build_session_config() -> VoiceSessionConfig:
    """Build a VoiceSessionConfig for the Ask AI endpoint."""
    return VoiceSessionConfig(
        stt_provider_type="deepgram_flux",
        llm_provider_type="groq",
        # TTS Provider: "deepgram" or "google" (switch here)
        tts_provider_type="deepgram",
        stt_model="flux-general-en",
        stt_sample_rate=16000,
        stt_encoding="linear16",
        stt_eot_threshold=0.7,
        stt_eager_eot_threshold=None,  # EndOfTurn-only mode for reliability
        stt_eot_timeout_ms=5000,
        llm_model=ASK_AI_CONFIG["llm_model"],
        llm_temperature=ASK_AI_CONFIG["llm_temperature"],
        llm_max_tokens=ASK_AI_CONFIG["llm_max_tokens"],
        voice_id=ASK_AI_CONFIG["voice_id"],
        tts_sample_rate=ASK_AI_CONFIG["sample_rate"],
        gateway_sample_rate=ASK_AI_CONFIG["sample_rate"],
        gateway_channels=1,
        gateway_bit_depth=16,
        session_type="ask_ai",
        agent_config=_create_ask_ai_agent_config(),
        system_prompt=ASK_AI_SYSTEM_PROMPT,
        campaign_id="ask-ai",
        lead_id="demo-user",
    )

# GOOGLE TTS SESSION CONFIG (commented out - for switching back if needed)
# def _build_session_config_google() -> VoiceSessionConfig:
#     """Build a VoiceSessionConfig using Google TTS."""
#     return VoiceSessionConfig(
#         stt_provider_type="deepgram_flux",
#         llm_provider_type="groq",
#         tts_provider_type="google",
#         stt_model="flux-general-en",
#         stt_sample_rate=16000,
#         stt_encoding="linear16",
#         llm_model=ASK_AI_CONFIG_GOOGLE["llm_model"],
#         llm_temperature=ASK_AI_CONFIG_GOOGLE["llm_temperature"],
#         llm_max_tokens=ASK_AI_CONFIG_GOOGLE["llm_max_tokens"],
#         voice_id=ASK_AI_CONFIG_GOOGLE["voice_id"],
#         tts_sample_rate=ASK_AI_CONFIG_GOOGLE["sample_rate"],
#         gateway_sample_rate=ASK_AI_CONFIG_GOOGLE["sample_rate"],
#         gateway_channels=1,
#         gateway_bit_depth=16,
#         session_type="ask_ai",
#         agent_config=_create_ask_ai_agent_config(),
#         system_prompt=ASK_AI_SYSTEM_PROMPT,
#         campaign_id="ask-ai",
#         lead_id="demo-user",
#     )


@router.websocket("/ws/ask-ai/{session_id}")
async def ask_ai_websocket(websocket: WebSocket, session_id: str):
    """
    Ask AI WebSocket — one-click voice assistant.

    Lifecycle is managed by VoiceOrchestrator; this endpoint only handles
    the WebSocket message loop (transport concern).
    """
    await websocket.accept()
    logger.info(f"Ask AI session started: {session_id}")

    # Get orchestrator from DI container
    from app.core.container import get_container
    container = get_container()

    voice_session = None
    barge_in_event = asyncio.Event()
    receiver_task: Optional[asyncio.Task] = None

    try:
        orchestrator = container.voice_orchestrator

        # 1. Create session via orchestrator
        config = _build_session_config()
        voice_session = await orchestrator.create_voice_session(config)

        # 2. Send ready message
        await websocket.send_json({
            "type": "ready",
            "session_id": session_id,
            "call_id": voice_session.call_id,
            "sample_rate": ASK_AI_CONFIG["sample_rate"],
        })

        call_id = voice_session.call_id
        gateway = voice_session.media_gateway

        async def _receive_messages() -> None:
            """
            Continuously consume websocket frames.

            Running this concurrently with greeting prevents stale mic audio
            buildup and keeps audio flow real-time.
            """
            while gateway.is_session_active(call_id):
                try:
                    message = await asyncio.wait_for(websocket.receive(), timeout=30.0)
                    message_type = message.get("type")

                    # Starlette emits explicit disconnect frames; stop reading immediately.
                    if message_type == "websocket.disconnect":
                        logger.info(f"Ask AI websocket disconnected: {session_id}")
                        break

                    if message_type != "websocket.receive":
                        continue

                    audio_data = message.get("bytes")
                    if isinstance(audio_data, (bytes, bytearray)):
                        if not audio_data:
                            continue

                        await gateway.on_audio_received(call_id, bytes(audio_data))
                        continue

                    text_data = message.get("text")
                    if not text_data:
                        continue
                    try:
                        data = json.loads(text_data)
                    except json.JSONDecodeError:
                        logger.debug(
                            f"Ignoring non-JSON websocket text frame: {text_data[:120]}"
                        )
                        continue
                    if data.get("type") == "end_call":
                        await gateway.on_call_ended(call_id, "user_ended")
                        break

                except asyncio.TimeoutError:
                    try:
                        await websocket.send_json({"type": "heartbeat"})
                    except (WebSocketDisconnect, RuntimeError):
                        break
                    continue
                except WebSocketDisconnect:
                    break
                except RuntimeError as e:
                    if "disconnect message has been received" in str(e):
                        logger.info(f"Ask AI websocket closed after disconnect: {session_id}")
                        break
                    raise

        # 3. Start pipeline before greeting so STT queue is active immediately.
        await orchestrator.start_pipeline(voice_session, websocket)

        # 4. Start frame receiver before greeting to avoid buffered stale audio.
        receiver_task = asyncio.create_task(_receive_messages())

        # 5. Greeting (always play full intro before listening)
        await orchestrator.send_greeting(
            voice_session,
            "Hi there! How can I help you today?",
            websocket,
            barge_in_event,
        )

        # 6. Keep endpoint alive until receiver exits (disconnect/end_call).
        await receiver_task

    except WebSocketDisconnect:
        logger.info(f"Ask AI disconnected: {session_id}")
    except Exception as e:
        logger.error(f"Ask AI error: {e}", exc_info=True)
        try:
            await websocket.send_json({"type": "error", "message": str(e)})
        except Exception:
            pass
    finally:
        if receiver_task and not receiver_task.done():
            receiver_task.cancel()
            try:
                await receiver_task
            except asyncio.CancelledError:
                pass
        if voice_session:
            await container.voice_orchestrator.end_session(voice_session)
        logger.info(f"Ask AI session ended: {session_id}")
