"""
Voice Demo WebSocket - Talky.ai Voice Agents Demo

Uses the same voice pipeline as live calls and applies the
currently selected AI Options config (LLM + TTS voice/model).

Day 41: Refactored to use VoiceOrchestrator for lifecycle management.
"""
import json
import asyncio
import logging
from typing import Dict, Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from app.domain.models.agent_config import AgentConfig, AgentGoal, ConversationFlow, ConversationRule
from app.domain.models.ai_config import AIProviderConfig, GOOGLE_CHIRP3_VOICES, DEEPGRAM_AURA2_VOICES
from app.infrastructure.tts.elevenlabs_catalog import get_elevenlabs_voices_for_current_key, elevenlabs_enabled
from app.domain.services.global_ai_config import set_global_config
from app.domain.services.voice_orchestrator import VoiceSessionConfig

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Voice Demo"])

# Default demo persona for missing voice metadata.
DEFAULT_AGENT_NAME = "Assistant"
DEFAULT_AGENT_PERSONALITY = "warm, professional, and reassuring"
DEFAULT_COMPANY_NAME = "Talky.ai"

# Talky.ai Product Information — surface-level, concise
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
- 300 call minutes
- 1 AI agent
- Basic analytics
- Email support

**Professional — $79/month** (most popular)
- 1,500 call minutes
- 3 AI agents
- Advanced analytics
- Priority support & custom voices

**Enterprise — $199/month**
- 5,000 call minutes
- 10 AI agents
- Full analytics suite
- 24/7 support, API access, white-label option

### Why Businesses Love Talky
- Sounds natural, not robotic
- Available around the clock
- Setup takes minutes, not weeks
- Scales with your business
"""

def _build_system_prompt(agent_name: str, personality: str) -> str:
    return f"""You are {agent_name}, a friendly product specialist at Talky.ai.

Your personality: {personality}

{TALKY_PRODUCT_INFO}

## Your Role
You are a live product demo on the Talky.ai website. Visitors click the "Ask AI" button to learn about Talky. Your job is to describe what Talky does, explain our packages, and answer product questions — all in a relaxed, informative way.

You are NOT booking appointments. You are NOT making sales calls. You are simply answering questions about the product.

## Rules
- Keep responses short and naturally spoken
- Use plain spoken sentences only — no markdown, no bullets, no headings, and no XML or HTML tags
- Usually answer in 1 to 2 sentences, but if asked about pricing, plans, or packages you may use up to 4 short sentences so all tiers are covered
- Be mild, informative, and helpful — no hard selling or pressure
- Describe the product and packages when asked
- If someone asks pricing, briefly mention the three tiers
- Be conversational and natural, like chatting with a knowledgeable friend
- Only discuss Talky.ai — product, features, pricing, and use cases
- If asked something unrelated, gently steer back
- Never say you are an AI, a bot, or automated
- Never use technical jargon
- If you don't know something, offer to have a team member follow up
- Present Talky confidently but not aggressively"""


def _resolve_voice_profile(ai_config: AIProviderConfig) -> Dict[str, str]:
    for voice in [*GOOGLE_CHIRP3_VOICES, *DEEPGRAM_AURA2_VOICES]:
        if voice.id == ai_config.tts_voice_id:
            personality = "warm and professional" if (voice.gender or "").lower() == "female" else "professional and confident"
            return {
                "agent_name": voice.name,
                "description": voice.description or "Voice assistant",
                "personality": personality,
                "intro": f"Hi there! I'm {voice.name} from {DEFAULT_COMPANY_NAME}. What would you like to know about our voice agents?",
            }

    return {
        "agent_name": DEFAULT_AGENT_NAME,
        "description": "Voice assistant",
        "personality": DEFAULT_AGENT_PERSONALITY,
        "intro": f"Hi there! I'm {DEFAULT_AGENT_NAME} from {DEFAULT_COMPANY_NAME}. What would you like to know about our voice agents?",
    }


def _is_english_language(language: str | None) -> bool:
    if not language:
        return False
    normalized = language.strip().lower()
    return normalized == "en" or normalized.startswith("en-") or normalized == "english"


def _create_agent_config(agent_name: str, personality: str) -> AgentConfig:
    """Create agent config for product info mode (not appointment booking)."""
    return AgentConfig(
        agent_name=agent_name,
        company_name=DEFAULT_COMPANY_NAME,
        business_type="Voice AI Platform",
        goal=AgentGoal.INFORMATION_GATHERING,
        tone=personality,
        flow=ConversationFlow(max_objection_attempts=3),
        rules=ConversationRule(
            do_not_say_rules=[
                "Keep responses brief - 1 to 2 sentences",
                "Describe the product and packages only",
                "Do not try to book appointments or schedule calls",
                "Never mention technical terms or that you are automated",
                "Never use markdown, bullet lists, headings, or XML tags in spoken replies"
            ]
        ),
        max_conversation_turns=20,
        response_max_sentences=2
    )


def _build_session_config(ai_config: AIProviderConfig, agent_name: str, personality: str) -> VoiceSessionConfig:
    """Build a VoiceSessionConfig for the Voice Demo endpoint."""
    return VoiceSessionConfig(
        stt_provider_type="deepgram_flux",
        llm_provider_type="groq",
        tts_provider_type=ai_config.tts_provider,
        stt_model="flux-general-en",
        stt_sample_rate=16000,
        stt_encoding="linear16",
        llm_model=ai_config.llm_model,
        llm_temperature=ai_config.llm_temperature,
        llm_max_tokens=ai_config.llm_max_tokens,
        voice_id=ai_config.tts_voice_id,
        tts_model=ai_config.tts_model,
        tts_sample_rate=ai_config.tts_sample_rate,
        gateway_sample_rate=ai_config.tts_sample_rate,
        gateway_channels=1,
        gateway_bit_depth=16,
        session_type="voice_demo",
        agent_config=_create_agent_config(agent_name, personality),
        system_prompt=_build_system_prompt(agent_name, personality),
        campaign_id="demo",
        lead_id="demo-user",
    )


@router.get("/voices")
async def get_available_voices():
    """Return available voice agent info including ElevenLabs if configured."""
    static_voices = [
        voice for voice in [*GOOGLE_CHIRP3_VOICES, *DEEPGRAM_AURA2_VOICES]
        if _is_english_language(voice.language)
    ]
    el_voices = await get_elevenlabs_voices_for_current_key() if elevenlabs_enabled() else []
    all_voices = [*static_voices, *el_voices]
    return {
        "voices": [
            {"id": voice.id, "name": voice.name, "gender": voice.gender, "description": voice.description, "provider": voice.provider}
            for voice in all_voices
        ]
    }


@router.websocket("/ws/ai-test/{session_id}")
async def voice_demo_websocket(websocket: WebSocket, session_id: str):
    """
    Voice demo WebSocket using the currently selected AI options config.

    Lifecycle is managed by VoiceOrchestrator; this endpoint only handles
    the WebSocket message loop (transport concern).
    """
    await websocket.accept()
    logger.info(f"Voice demo started: {session_id}")

    # Get orchestrator from DI container
    from app.core.container import get_container
    container = get_container()

    voice_session = None
    receiver_task: Optional[asyncio.Task] = None

    try:
        # Wait for config message
        config_msg = await websocket.receive_json()

        if config_msg.get("type") != "config":
            await websocket.send_json({
                "type": "error",
                "message": "Expected config message first"
            })
            await websocket.close()
            return

        raw_ai_config = config_msg.get("config")
        if not isinstance(raw_ai_config, dict):
            await websocket.send_json({
                "type": "error",
                "message": "Missing or invalid AI config payload"
            })
            await websocket.close()
            return

        try:
            ai_config = AIProviderConfig(**raw_ai_config)
        except ValidationError as exc:
            await websocket.send_json({
                "type": "error",
                "message": f"Invalid AI config: {exc.errors()}"
            })
            await websocket.close()
            return

        # Apply selected config to the active voice pipeline session.
        set_global_config(ai_config)
        voice_profile = _resolve_voice_profile(ai_config)
        logger.info("Starting voice demo with selected AI options config")

        orchestrator = container.voice_orchestrator

        # 1. Create session via orchestrator
        config = _build_session_config(
            ai_config=ai_config,
            agent_name=voice_profile["agent_name"],
            personality=voice_profile["personality"],
        )
        voice_session = await orchestrator.create_voice_session(config)

        # 2. Send ready with voice info
        await websocket.send_json({
            "type": "ready",
            "session_id": session_id,
            "call_id": voice_session.call_id,
            "state": "ready",
            "voice_id": ai_config.tts_voice_id,
            "agent_name": voice_profile["agent_name"],
            "company_name": DEFAULT_COMPANY_NAME,
            "agent_description": voice_profile["description"],
            "sample_rate": ai_config.tts_sample_rate,
            "audio_format": "s16le",
        })

        # 3. Start pipeline (so STT can detect barge-in during intro)
        await orchestrator.start_pipeline(voice_session, websocket)

        call_id = voice_session.call_id
        gateway = voice_session.media_gateway
        greeting_active = True

        async def _receive_messages() -> None:
            """
            Consume websocket frames continuously so mic audio does not backlog
            during the greeting.
            """
            while gateway.is_session_active(call_id):
                try:
                    message = await asyncio.wait_for(websocket.receive(), timeout=30.0)
                    message_type = message.get("type")

                    if message_type == "websocket.disconnect":
                        logger.info(f"Voice demo websocket disconnected: {session_id}")
                        break

                    if message_type != "websocket.receive":
                        continue

                    audio_data = message.get("bytes")
                    if isinstance(audio_data, (bytes, bytearray)):
                        if not audio_data:
                            continue

                        audio_bytes = bytes(audio_data)
                        await gateway.on_audio_received(call_id, audio_bytes)

                        if greeting_active and len(audio_bytes) >= 256:
                            samples = [
                                int.from_bytes(audio_bytes[i:i+2], "little", signed=True)
                                for i in range(0, min(len(audio_bytes), 256), 2)
                            ]
                            energy = sum(abs(sample) for sample in samples) / len(samples)
                            if energy > 300:
                                if voice_session and voice_session.call_session.barge_in_event:
                                    voice_session.call_session.barge_in_event.set()
                                await websocket.send_json({"type": "barge_in"})
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
                    if data.get("type") == "playback_complete":
                        mark_playback_complete = getattr(gateway, "mark_playback_complete", None)
                        if callable(mark_playback_complete):
                            mark_playback_complete(call_id)
                        continue

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
                        logger.info(f"Voice demo websocket closed after disconnect: {session_id}")
                        break
                    raise

        # 4. Start receiver before greeting to avoid stale buffered mic audio.
        receiver_task = asyncio.create_task(_receive_messages())

        # 5. Greeting
        # For linear16 mono audio: bytes/sec = sample_rate * 2.
        # Keep greeting chunks moderate to reduce jitter and avoid large burst playback.
        pcm_bytes_per_second = max(8000, int(ai_config.tts_sample_rate) * 2)
        greeting_first_chunk_bytes = max(1600, pcm_bytes_per_second // 10)   # ~100ms
        greeting_regular_chunk_bytes = max(3200, pcm_bytes_per_second // 5)  # ~200ms
        await orchestrator.send_greeting(
            voice_session,
            voice_profile["intro"],
            websocket,
            first_chunk_bytes=greeting_first_chunk_bytes,
            regular_chunk_bytes=greeting_regular_chunk_bytes,
        )
        greeting_active = False

        # 6. Keep endpoint alive until receiver exits (disconnect/end_call).
        await receiver_task

    except WebSocketDisconnect:
        logger.info(f"Voice demo disconnected: {session_id}")
    except Exception as e:
        logger.error(f"Voice demo error: {e}", exc_info=True)
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
        logger.info(f"Voice demo ended: {session_id}")
