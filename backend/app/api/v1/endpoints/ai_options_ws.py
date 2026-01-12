"""
Voice Demo WebSocket - Talky.ai Voice Agents Demo

Uses voice pipeline WITHOUT database operations.
Supports multiple voice personas that introduce themselves.
"""
import os
import uuid
import json
import asyncio
import logging
import time
from typing import Optional, Dict
from datetime import datetime
from dotenv import load_dotenv

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.domain.models.session import CallSession, CallState
from app.domain.models.conversation_state import ConversationState, ConversationContext
from app.domain.models.agent_config import AgentConfig, AgentGoal, ConversationFlow, ConversationRule
from app.domain.models.ai_config import AIProviderConfig
from app.domain.models.conversation import Message, MessageRole
from app.domain.services.voice_pipeline_service import VoicePipelineService
from app.infrastructure.stt.deepgram_flux import DeepgramFluxSTTProvider
from app.infrastructure.llm.groq import GroqLLMProvider
# Google TTS imports - commented out due to GCP permission issues
# TODO: Re-enable when gcloud permissions are fixed
# from app.infrastructure.tts.google_tts import GoogleTTSProvider
# try:
#     from app.infrastructure.tts.google_tts_streaming import GoogleTTSStreamingProvider
#     STREAMING_TTS_AVAILABLE = True
# except ImportError:
#     STREAMING_TTS_AVAILABLE = False

# Cartesia TTS disabled - using Google TTS only
# from app.infrastructure.tts.cartesia import CartesiaTTSProvider
from app.infrastructure.telephony.browser_media_gateway import BrowserMediaGateway

load_dotenv()

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Voice Demo"])

# Voice Agent Personas - Using Google Chirp3-HD voices (Cartesia disabled)
VOICE_AGENTS = {
    "sophia": {
        "id": "sophia",
        "name": "Sophia",
        "gender": "female",
        # Katie - stable, realistic female voice (recommended for voice agents)
        "voice_id": "f786b574-daa5-4673-aa0c-cbe3e8534c02",
        "personality": "warm, professional, and reassuring",
        "intro": "Hi there! I'm Sophia. I help businesses connect with their customers through natural phone conversations. What would you like to know about how I can help your business?",
        "description": "Warm & Professional"
    },
    "emma": {
        "id": "emma", 
        "name": "Emma",
        "gender": "female",
        # Tessa - expressive, emotive female voice (good for friendly personas)
        "voice_id": "6ccbfb76-1fc6-48f7-b71d-91ac6298247b",
        "personality": "energetic, friendly, and enthusiastic",
        "intro": "Hey! I'm Emma, nice to meet you! I love helping businesses reach out to customers and make every conversation count. How can I help you today?",
        "description": "Energetic & Friendly"
    },
    "alex": {
        "id": "alex",
        "name": "Alex",
        "gender": "male",
        # Kiefer - stable, realistic male voice (recommended for voice agents)
        "voice_id": "228fca29-3a0a-435c-8728-5cb483251068",
        "personality": "confident, clear, and trustworthy",
        "intro": "Hello! I'm Alex. I specialize in professional outreach calls - from appointment confirmations to customer follow-ups. What can I tell you about Talky?",
        "description": "Confident & Clear"
    }
}

# Talky.ai Product Information (no technical jargon)
TALKY_PRODUCT_INFO = """
## About Talky.ai

Talky.ai provides intelligent voice agents that make phone calls on behalf of businesses. Our agents sound natural and can handle real conversations.

### What We Do
- Make outbound calls to customers automatically
- Confirm appointments and send reminders
- Follow up with leads and customers
- Conduct surveys and gather feedback
- Handle payment reminders professionally

### Pricing Plans

**Starter - $99/month**
- 500 call minutes
- 1 voice agent
- Basic reporting

**Growth - $299/month**  
- 2,000 call minutes
- 3 voice agents
- Priority support
- Advanced analytics

**Enterprise - Custom**
- Unlimited minutes available
- Unlimited agents
- Dedicated support
- Custom integrations

### Why Choose Talky
- Sounds natural, not robotic
- Available 24/7
- Scales with your business
- Easy to set up
- Works with your existing tools
"""


def create_agent_config(voice_id: str = None) -> AgentConfig:
    """Create agent config using the dynamically selected voice."""
    agent = get_dynamic_agent_info()
    
    return AgentConfig(
        agent_name=agent["name"],
        company_name="Talky.ai",
        business_type="Business Communication Platform",
        goal=AgentGoal.APPOINTMENT_CONFIRMATION,
        tone=agent["personality"],
        flow=ConversationFlow(max_objection_attempts=3),
        rules=ConversationRule(
            do_not_say_rules=[
                "Keep responses brief - 1 to 2 sentences",
                "Be helpful and natural",
                "Never mention technical terms or that you are automated"
            ]
        ),
        max_conversation_turns=20,
        response_max_sentences=2
    )


# Google TTS imports
from app.infrastructure.tts.google_tts_streaming import GoogleTTSStreamingProvider

# Using Cartesia TTS for ultra-low latency (~90ms first audio)
# Ref: https://docs.cartesia.ai/build-with-cartesia/tts-models#voice-selection
from app.infrastructure.tts.cartesia import CartesiaTTSProvider
from app.infrastructure.telephony.browser_media_gateway import BrowserMediaGateway

load_dotenv()

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Voice Demo"])

# Voice Agent Personas - Dynamic based on selected voice
# These are now dynamically generated based on the globally selected voice
def get_dynamic_agent_info():
    """Get agent info dynamically based on selected voice from global config."""
    from app.domain.services.global_ai_config import get_global_config, get_selected_voice_info
    
    config = get_global_config()
    voice_info = get_selected_voice_info()
    
    # Get voice name and gender from the selected voice
    voice_name = voice_info.get("name", "Alex")
    gender = voice_info.get("gender", "male")
    
    # Generate personality based on gender
    if gender == "female":
        personality = "warm, professional, and reassuring"
        intro = f"Hi there! I'm {voice_name}. How can I help you today?"
    else:
        personality = "confident, clear, and trustworthy"
        intro = f"Hello! I'm {voice_name}. What can I help you with?"
    
    return {
        "id": "dynamic",
        "name": voice_name,
        "gender": gender,
        "voice_id": config.tts_voice_id,
        "personality": personality,
        "intro": intro,
        "description": f"{voice_name} - AI Voice Assistant"
    }


async def create_voice_pipeline(voice_id: str) -> tuple:
    """Initialize providers using GLOBAL config for LLM and TTS voice.
    
    The LLM model and TTS voice are taken from the global AI config,
    which is set via the AI Options page. This ensures consistency
    across dummy calls, real calls, and all other voice interactions.
    """
    from app.domain.services.global_ai_config import get_global_config
    
    # Get global config (what's selected in AI Options page)
    global_config = get_global_config()
    
    # Update agent config effectively
    # agent = VOICE_AGENTS.get(voice_id, VOICE_AGENTS["sophia"]) 
    # Logic note: voice_id arg might be stale if global config changed, but let's stick to global config for pipeline
    
    stt_provider = DeepgramFluxSTTProvider()
    await stt_provider.initialize({
        "api_key": os.getenv("DEEPGRAM_API_KEY"),
        "model": "flux-general-en",
        "sample_rate": 16000,
        "encoding": "linear16"
    })
    
    # Use LLM model from global config
    llm_provider = GroqLLMProvider()
    await llm_provider.initialize({
        "api_key": os.getenv("GROQ_API_KEY"),
        "model": global_config.llm_model,  # From AI Options
        "temperature": global_config.llm_temperature,
        "max_tokens": global_config.llm_max_tokens
    })
    logger.info(f"Using LLM model from global config: {global_config.llm_model}")
    
    # Use TTS voice from global config
    tts_voice_id = global_config.tts_voice_id
    logger.info(f"Using TTS voice from global config: {tts_voice_id}")
    
    # Always use Google TTS Streaming (Cartesia disabled)
    logger.info(f"Initializing Google TTS Streaming for voice: {tts_voice_id}")
    
    # Determine credentials path - ensure it points to backend/config
    # We need: backend/config/google-service-account.json
    # Current file is in: backend/app/api/v1/endpoints/
    backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))))
    creds_path = os.path.join(backend_dir, "config", "google-service-account.json")
    
    if os.path.exists(creds_path):
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = creds_path
    else:
        logger.warning(f"Google creds not found at {creds_path}, hoping ADC works or env var is set")
    
    # Ensure voice_id is in Google format
    if not tts_voice_id.startswith("en-US-Chirp3-HD"):
        # Convert short name to full format
        tts_voice_id = f"en-US-Chirp3-HD-{tts_voice_id}" if not "Chirp3" in tts_voice_id else tts_voice_id
        # Fallback to default Google voice if not valid
        if not tts_voice_id.startswith("en-US"):
            tts_voice_id = "en-US-Chirp3-HD-Leda"
    
    tts_provider = GoogleTTSStreamingProvider()
    await tts_provider.initialize({
        "voice_id": tts_voice_id,
        "sample_rate": 24000  # Chirp 3 HD uses 24kHz
    })
    
    # Cartesia disabled
    # else:
    #     logger.info(f"Initializing Cartesia TTS for voice: {tts_voice_id}")
    #     tts_provider = CartesiaTTSProvider()
    #     await tts_provider.initialize({
    #         "api_key": os.getenv("CARTESIA_API_KEY"),
    #         "voice_id": tts_voice_id,
    #         "model_id": global_config.tts_model,
    #         "sample_rate": global_config.tts_sample_rate or 24000
    #     })
    
    browser_gateway = BrowserMediaGateway()
    await browser_gateway.initialize({
        "sample_rate": global_config.tts_sample_rate or 24000,  # Match TTS output
        "channels": 1,
        "bit_depth": 16
    })
    
    return stt_provider, llm_provider, tts_provider, browser_gateway


def create_demo_session(call_id: str, agent_config: AgentConfig, voice_id: str = None) -> CallSession:
    """Create demo session with dynamically selected voice persona."""
    agent = get_dynamic_agent_info()
    
    system_prompt = f"""You are {agent['name']}, a voice agent for Talky.ai.

Your personality: {agent['personality']}

{TALKY_PRODUCT_INFO}

## Important Guidelines
- Keep responses SHORT (1-2 sentences max) - this is a phone conversation
- Sound natural and conversational
- Never say you are an "AI" or mention technology
- If interrupted, stop and listen immediately
- Answer questions about Talky naturally
- If you don't know something, offer to have someone follow up
- Be {agent['personality']}"""
    
    return CallSession(
        call_id=call_id,
        campaign_id="demo",
        lead_id="demo-user",
        vonage_call_uuid="demo-session",
        state=CallState.ACTIVE,
        conversation_state=ConversationState.GREETING,
        conversation_context=ConversationContext(),
        agent_config=agent_config,
        system_prompt=system_prompt,
        voice_id=agent["voice_id"],
        started_at=datetime.utcnow(),
        last_activity_at=datetime.utcnow()
    )


@router.get("/voices")
async def get_available_voices():
    """Return current voice agent info."""
    agent = get_dynamic_agent_info()
    return {
        "voices": [
            {
                "id": agent["id"],
                "name": agent["name"],
                "gender": agent["gender"],
                "description": agent["description"]
            }
        ]
    }


@router.websocket("/ws/ai-test/{session_id}")
async def voice_demo_websocket(websocket: WebSocket, session_id: str):
    """Voice demo WebSocket with dynamic voice support."""
    await websocket.accept()
    logger.info(f"Voice demo started: {session_id}")
    
    stt_provider = None
    llm_provider = None
    tts_provider = None
    browser_gateway = None
    pipeline = None
    call_session = None
    barge_in_event = asyncio.Event()

    
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
        
        # CRITICAL: Read the full config object and set it globally
        # Frontend sends: {type: "config", config: {llm_model, tts_voice_id, ...}}
        from app.domain.services.global_ai_config import set_global_config, get_global_config
        from app.domain.models.ai_config import AIProviderConfig
        
        config_data = config_msg.get("config", {})
        if config_data:
            # Create AIProviderConfig from frontend data
            ai_config = AIProviderConfig(
                llm_model=config_data.get("llm_model", "llama-3.3-70b-versatile"),
                llm_temperature=config_data.get("llm_temperature", 0.6),
                llm_max_tokens=config_data.get("llm_max_tokens", 150),
                tts_model=config_data.get("tts_model", "sonic-3"),
                tts_voice_id=config_data.get("tts_voice_id", "f786b574-daa5-4673-aa0c-cbe3e8534c02"),
                tts_sample_rate=config_data.get("tts_sample_rate", 24000),  # Official Cartesia: 24kHz
                tts_provider=config_data.get("tts_provider", "cartesia")
            )
            # Set as global config so pipeline uses it
            set_global_config(ai_config)
            logger.info(f"Global config set: LLM={ai_config.llm_model}, Voice={ai_config.tts_voice_id}")
        
        # Now get the global config (which we just set)
        global_config = get_global_config()
        
        # Get dynamic agent info based on selected voice
        agent = get_dynamic_agent_info()
        agent_config = create_agent_config()
        
        # Initialize voice pipeline - this now uses global config
        stt_provider, llm_provider, tts_provider, browser_gateway = await create_voice_pipeline(None)
        
        pipeline = VoicePipelineService(
            stt_provider=stt_provider,
            llm_provider=llm_provider,
            tts_provider=tts_provider,
            media_gateway=browser_gateway
        )
        
        call_id = str(uuid.uuid4())
        call_session = create_demo_session(call_id, agent_config)
        
        await browser_gateway.on_call_started(call_id, {"websocket": websocket})
        
        # Send ready with voice info
        await websocket.send_json({
            "type": "ready",
            "session_id": session_id,
            "call_id": call_id,
            "voice_id": agent["voice_id"],
            "agent_name": agent["name"],
            "agent_description": agent["description"]
        })
        
        # Send voice introduction (pre-written, not generated)
        await send_voice_introduction(tts_provider, agent, websocket, barge_in_event)

        
        # Start pipeline
        logger.info(f"Starting voice pipeline for {call_id}...")
        pipeline_task = asyncio.create_task(
            pipeline.start_pipeline(call_session, websocket)
        )
        logger.info(f"Voice pipeline task created for {call_id}")
        
        # Main message loop
        while browser_gateway.is_session_active(call_id):
            try:
                message = await asyncio.wait_for(
                    websocket.receive(),
                    timeout=30.0
                )
                
                if "bytes" in message:
                    audio_data = message["bytes"]
                    await browser_gateway.on_audio_received(call_id, audio_data)
                    
                    # Log audio receipt periodically
                    audio_count = getattr(browser_gateway.get_session(call_id), 'chunks_received', 0)
                    if audio_count % 50 == 0 and audio_count > 0:
                        logger.info(f"Audio chunks received: {audio_count}")
                    
                    # Barge-in detection - LOW threshold for instant response
                    if len(audio_data) >= 256:  # Reduced from 512 for faster detection
                        samples = [int.from_bytes(audio_data[i:i+2], 'little', signed=True) 
                                   for i in range(0, min(len(audio_data), 256), 2)]
                        energy = sum(abs(s) for s in samples) / len(samples)
                        if energy > 300:  # Lowered from 500 for faster barge-in
                            barge_in_event.set()
                            await websocket.send_json({"type": "barge_in"})
                
                elif "text" in message:
                    data = json.loads(message["text"])
                    msg_type = data.get("type")
                    
                    if msg_type == "end_call":
                        await browser_gateway.on_call_ended(call_id, "user_ended")
                        break

            
            except asyncio.TimeoutError:
                await websocket.send_json({"type": "heartbeat"})
                continue
            
            except WebSocketDisconnect:
                break
        
        pipeline_task.cancel()
        try:
            await pipeline_task
        except asyncio.CancelledError:
            pass
    
    except WebSocketDisconnect:
        logger.info(f"Voice demo disconnected: {session_id}")
    
    except Exception as e:
        logger.error(f"Voice demo error: {e}", exc_info=True)
        try:
            await websocket.send_json({"type": "error", "message": str(e)})
        except:
            pass
    
    finally:
        if browser_gateway and call_session:
            await browser_gateway.on_call_ended(call_session.call_id, "session_ended")
        if stt_provider:
            await stt_provider.cleanup()
        if llm_provider:
            await llm_provider.cleanup()
        if tts_provider:
            await tts_provider.cleanup()
        if browser_gateway:
            await browser_gateway.cleanup()
        logger.info(f"Voice demo ended: {session_id}")


async def send_voice_introduction(
    tts_provider,  # TTSProvider (CartesiaTTSProvider or GoogleTTSProvider)
    agent: Dict,
    websocket: WebSocket,
    barge_in_event: asyncio.Event
):
    """Send the voice agent's introduction using global config voice."""
    from app.domain.services.global_ai_config import get_global_config, get_selected_voice_info
    
    config = get_global_config()
    voice_info = get_selected_voice_info()
    
    # Use the selected voice name in the intro
    voice_name = voice_info.get("name", agent["name"])
    intro_text = f"Hi there! I'm {voice_name}. How can I help you today?"
    
    # Send intro text to frontend
    await websocket.send_json({
        "type": "llm_response",
        "text": intro_text,
        "latency_ms": 0
    })
    
    tts_start = time.time()
    was_interrupted = False
    
    # Optimized chunk sizes for smooth, instant playback:
    # First chunk: 12800 bytes (~200ms of Float32 audio) - fast first audio without jitter
    # Regular chunks: 32000 bytes (~500ms) - smooth continuous playback
    FIRST_CHUNK_BYTES = 12800   # ~200ms for fast first audio (prevents jitter)
    REGULAR_CHUNK_BYTES = 32000  # ~500ms for smooth streaming
    audio_buffer = bytearray()
    chunks_sent = 0
    
    try:
        # Use voice_id from global config
        async for audio_chunk in tts_provider.stream_synthesize(
            text=intro_text,
            voice_id=config.tts_voice_id,
            sample_rate=config.tts_sample_rate
        ):
            if barge_in_event.is_set():
                was_interrupted = True
                barge_in_event.clear()
                await websocket.send_json({"type": "tts_interrupted", "reason": "barge_in"})
                break
            
            audio_buffer.extend(audio_chunk.data)
            
            # Use smaller threshold for first chunk (instant start)
            # Then larger threshold for remaining chunks (smooth playback)
            target_size = FIRST_CHUNK_BYTES if chunks_sent == 0 else REGULAR_CHUNK_BYTES
            
            if len(audio_buffer) >= target_size:
                await websocket.send_bytes(bytes(audio_buffer))
                audio_buffer = bytearray()
                chunks_sent += 1
        
        # Send remaining audio
        if audio_buffer and not was_interrupted:
            await websocket.send_bytes(bytes(audio_buffer))
            
    except Exception as e:
        logger.error(f"Intro TTS error: {e}")
    
    tts_latency = (time.time() - tts_start) * 1000
    
    await websocket.send_json({
        "type": "turn_complete",
        "llm_latency_ms": 0,
        "tts_latency_ms": tts_latency,
        "total_latency_ms": tts_latency,
        "was_interrupted": was_interrupted
    })
