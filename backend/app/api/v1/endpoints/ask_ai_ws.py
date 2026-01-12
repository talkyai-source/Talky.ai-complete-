"""
Ask AI WebSocket - Simplified Voice Assistant Demo

One-click voice interaction without voice selection.
Uses Google Chirp3-HD with natural voices (Cartesia disabled).

Voice: Leda (en-US-Chirp3-HD-Leda) - Professional female voice
Sample Rate: 24000 Hz (Chirp3-HD optimal)
"""
import os
import uuid
import json
import asyncio
import logging
import time
from datetime import datetime
from dotenv import load_dotenv

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.domain.models.session import CallSession, CallState
from app.domain.models.conversation_state import ConversationState, ConversationContext
from app.domain.models.agent_config import AgentConfig, AgentGoal, ConversationFlow, ConversationRule
from app.domain.models.conversation import Message, MessageRole
from app.domain.services.voice_pipeline_service import VoicePipelineService
from app.infrastructure.stt.deepgram_flux import DeepgramFluxSTTProvider
from app.infrastructure.llm.groq import GroqLLMProvider
# Cartesia disabled - using Google TTS
# from app.infrastructure.tts.cartesia import CartesiaTTSProvider
from app.infrastructure.tts.google_tts_streaming import GoogleTTSStreamingProvider
from app.infrastructure.telephony.browser_media_gateway import BrowserMediaGateway

load_dotenv()

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Ask AI"])

# Fixed configuration for Ask AI - using Google TTS (Cartesia disabled)
ASK_AI_CONFIG = {
    # Google Chirp3-HD Leda voice - professional female
    "voice_id": "en-US-Chirp3-HD-Leda",
    "sample_rate": 24000,  # Chirp3-HD optimal sample rate
    "model_id": "Chirp3-HD",
    # LLM settings
    "llm_model": "llama-3.3-70b-versatile",
    "llm_temperature": 0.6,
    "llm_max_tokens": 150
}

# Talky.ai Product Information for the assistant
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


def create_ask_ai_agent_config() -> AgentConfig:
    """Create agent config optimized for Ask AI."""
    return AgentConfig(
        agent_name="Assistant",
        company_name="Talky.ai",
        business_type="Voice AI Platform",
        goal=AgentGoal.INFORMATION_GATHERING,  # Using valid enum value
        tone="friendly, warm, and helpful",
        flow=ConversationFlow(max_objection_attempts=3),
        rules=ConversationRule(
            do_not_say_rules=[
                "Keep responses brief - 1 to 2 sentences",
                "Be helpful and natural",
                "Never mention technical terms or that you are an AI"
            ]
        ),
        max_conversation_turns=20,  # Max allowed by model
        response_max_sentences=2
    )


def create_ask_ai_session(call_id: str, agent_config: AgentConfig) -> CallSession:
    """Create session optimized for Ask AI voice interaction."""
    system_prompt = """You are a friendly voice assistant for Talky.ai.

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

    return CallSession(
        call_id=call_id,
        campaign_id="ask-ai",
        lead_id="demo-user",
        vonage_call_uuid="ask-ai-session",
        state=CallState.ACTIVE,
        conversation_state=ConversationState.GREETING,
        conversation_context=ConversationContext(),
        agent_config=agent_config,
        system_prompt=system_prompt,
        voice_id=ASK_AI_CONFIG["voice_id"],
        started_at=datetime.utcnow(),
        last_activity_at=datetime.utcnow()
    )


async def create_ask_ai_pipeline():
    """Initialize providers with fixed Ask AI configuration."""
    
    # STT Provider - Deepgram Flux
    stt_provider = DeepgramFluxSTTProvider()
    await stt_provider.initialize({
        "api_key": os.getenv("DEEPGRAM_API_KEY"),
        "model": "flux-general-en",
        "sample_rate": 16000,
        "encoding": "linear16"
    })
    
    # LLM Provider - Groq
    llm_provider = GroqLLMProvider()
    await llm_provider.initialize({
        "api_key": os.getenv("GROQ_API_KEY"),
        "model": ASK_AI_CONFIG["llm_model"],
        "temperature": ASK_AI_CONFIG["llm_temperature"],
        "max_tokens": ASK_AI_CONFIG["llm_max_tokens"]
    })
    
    # TTS Provider - Google Chirp3-HD Streaming (Cartesia disabled)
    tts_provider = GoogleTTSStreamingProvider()
    await tts_provider.initialize({
        "voice_id": ASK_AI_CONFIG["voice_id"],
        "sample_rate": ASK_AI_CONFIG["sample_rate"]
    })
    
    # Browser Media Gateway - match TTS sample rate
    browser_gateway = BrowserMediaGateway()
    await browser_gateway.initialize({
        "sample_rate": ASK_AI_CONFIG["sample_rate"],  # 24kHz to match TTS
        "channels": 1,
        "bit_depth": 16  # pcm_s16le is 16-bit
    })
    
    return stt_provider, llm_provider, tts_provider, browser_gateway


@router.websocket("/ws/ask-ai/{session_id}")
async def ask_ai_websocket(websocket: WebSocket, session_id: str):
    """
    Ask AI WebSocket - One-click voice assistant.
    
    Simpler flow than dummy call:
    1. Connect
    2. Send greeting immediately
    3. Start listening for user input
    4. Process and respond
    
    No voice selection needed - uses fixed, natural voice.
    """
    await websocket.accept()
    logger.info(f"Ask AI session started: {session_id}")
    
    stt_provider = None
    llm_provider = None
    tts_provider = None
    browser_gateway = None
    pipeline = None
    call_session = None
    barge_in_event = asyncio.Event()

    try:
        # Initialize pipeline with fixed config
        stt_provider, llm_provider, tts_provider, browser_gateway = await create_ask_ai_pipeline()
        
        pipeline = VoicePipelineService(
            stt_provider=stt_provider,
            llm_provider=llm_provider,
            tts_provider=tts_provider,
            media_gateway=browser_gateway
        )
        
        call_id = str(uuid.uuid4())
        agent_config = create_ask_ai_agent_config()
        call_session = create_ask_ai_session(call_id, agent_config)
        
        await browser_gateway.on_call_started(call_id, {"websocket": websocket})
        
        # Send ready message - no config needed from client
        await websocket.send_json({
            "type": "ready",
            "session_id": session_id,
            "call_id": call_id,
            "sample_rate": ASK_AI_CONFIG["sample_rate"]
        })
        
        # Send greeting with emotion
        await send_ask_ai_greeting(tts_provider, websocket, barge_in_event)
        
        # Start voice pipeline
        logger.info(f"Starting Ask AI pipeline for {call_id}...")
        pipeline_task = asyncio.create_task(
            pipeline.start_pipeline(call_session, websocket)
        )
        
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
                    
                    # Barge-in detection
                    if len(audio_data) >= 256:
                        samples = [int.from_bytes(audio_data[i:i+2], 'little', signed=True) 
                                   for i in range(0, min(len(audio_data), 256), 2)]
                        energy = sum(abs(s) for s in samples) / len(samples)
                        if energy > 300:
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
        logger.info(f"Ask AI disconnected: {session_id}")
    
    except Exception as e:
        logger.error(f"Ask AI error: {e}", exc_info=True)
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
        logger.info(f"Ask AI session ended: {session_id}")


async def send_ask_ai_greeting(
    tts_provider: GoogleTTSStreamingProvider,
    websocket: WebSocket,
    barge_in_event: asyncio.Event
):
    """Send the Ask AI greeting with proper audio buffering to eliminate jitter."""
    
    greeting_text = "Hi there! How can I help you today?"
    
    # Send greeting text to frontend
    await websocket.send_json({
        "type": "llm_response",
        "text": greeting_text,
        "latency_ms": 0
    })
    
    tts_start = time.time()
    was_interrupted = False
    
    # Chunk sizes for 24kHz pcm_f32le (4 bytes per sample)
    # Using larger chunks reduces jitter from WebSocket overhead
    FIRST_CHUNK_BYTES = 48000   # ~500ms for first audio (24kHz * 4 bytes * 0.5s)
    REGULAR_CHUNK_BYTES = 96000  # ~1s for smooth streaming
    audio_buffer = bytearray()
    chunks_sent = 0
    
    try:
        # Synthesize with Google TTS Streaming
        async for audio_chunk in tts_provider.stream_synthesize(
            text=greeting_text,
            voice_id=ASK_AI_CONFIG["voice_id"],
            sample_rate=ASK_AI_CONFIG["sample_rate"]
        ):
            if barge_in_event.is_set():
                was_interrupted = True
                barge_in_event.clear()
                await websocket.send_json({"type": "tts_interrupted", "reason": "barge_in"})
                break
            
            audio_buffer.extend(audio_chunk.data)
            
            target_size = FIRST_CHUNK_BYTES if chunks_sent == 0 else REGULAR_CHUNK_BYTES
            
            if len(audio_buffer) >= target_size:
                await websocket.send_bytes(bytes(audio_buffer))
                audio_buffer = bytearray()
                chunks_sent += 1
        
        # Send remaining audio
        if audio_buffer and not was_interrupted:
            await websocket.send_bytes(bytes(audio_buffer))
            
    except Exception as e:
        logger.error(f"Ask AI greeting TTS error: {e}")
    
    tts_latency = (time.time() - tts_start) * 1000
    
    await websocket.send_json({
        "type": "turn_complete",
        "llm_latency_ms": 0,
        "tts_latency_ms": tts_latency,
        "total_latency_ms": tts_latency,
        "was_interrupted": was_interrupted
    })
