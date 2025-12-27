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
from app.infrastructure.tts.google_tts import GoogleTTSProvider
from app.infrastructure.telephony.browser_media_gateway import BrowserMediaGateway

load_dotenv()

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Voice Demo"])

# Voice Agent Personas
VOICE_AGENTS = {
    "sophia": {
        "id": "sophia",
        "name": "Sophia",
        "gender": "female",
        "voice_id": "en-US-Chirp3-HD-Leda",  # Female voice
        "personality": "warm, professional, and reassuring",
        "intro": "Hi there! I'm Sophia. I help businesses connect with their customers through natural phone conversations. What would you like to know about how I can help your business?",
        "description": "Warm & Professional"
    },
    "emma": {
        "id": "emma", 
        "name": "Emma",
        "gender": "female",
        "voice_id": "en-US-Chirp3-HD-Zephyr",  # Female voice
        "personality": "energetic, friendly, and enthusiastic",
        "intro": "Hey! I'm Emma, nice to meet you! I love helping businesses reach out to customers and make every conversation count. How can I help you today?",
        "description": "Energetic & Friendly"
    },
    "alex": {
        "id": "alex",
        "name": "Alex",
        "gender": "male",
        "voice_id": "en-US-Chirp3-HD-Orus",  # Male voice
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


def create_agent_config(voice_id: str) -> AgentConfig:
    """Create agent config for the selected voice."""
    agent = VOICE_AGENTS.get(voice_id, VOICE_AGENTS["sophia"])
    
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


async def create_voice_pipeline(voice_id: str) -> tuple:
    """Initialize providers with selected voice."""
    agent = VOICE_AGENTS.get(voice_id, VOICE_AGENTS["sophia"])
    
    stt_provider = DeepgramFluxSTTProvider()
    await stt_provider.initialize({
        "api_key": os.getenv("DEEPGRAM_API_KEY"),
        "model": "flux-general-en",
        "sample_rate": 16000,
        "encoding": "linear16"
    })
    
    llm_provider = GroqLLMProvider()
    await llm_provider.initialize({
        "api_key": os.getenv("GROQ_API_KEY"),
        "model": "llama-3.3-70b-versatile",
        "temperature": 0.6,  # Slightly lower for faster, more focused responses
        "max_tokens": 80  # Reduced from 150 for faster response time
    })
    
    tts_provider = GoogleTTSProvider()
    await tts_provider.initialize({
        "api_key": os.getenv("GOOGLE_TTS_API_KEY"),
        "voice_id": agent["voice_id"],
        "language_code": "en-US",
        "sample_rate": 16000
    })
    
    browser_gateway = BrowserMediaGateway()
    await browser_gateway.initialize({
        "sample_rate": 16000,
        "channels": 1,
        "bit_depth": 16
    })
    
    return stt_provider, llm_provider, tts_provider, browser_gateway


def create_demo_session(call_id: str, agent_config: AgentConfig, voice_id: str) -> CallSession:
    """Create demo session with selected voice persona."""
    agent = VOICE_AGENTS.get(voice_id, VOICE_AGENTS["sophia"])
    
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
    """Return list of available voice agents."""
    return {
        "voices": [
            {
                "id": v["id"],
                "name": v["name"],
                "gender": v["gender"],
                "description": v["description"]
            }
            for v in VOICE_AGENTS.values()
        ]
    }


@router.websocket("/ws/ai-test/{session_id}")
async def voice_demo_websocket(websocket: WebSocket, session_id: str):
    """Voice demo WebSocket with multiple voice support."""
    await websocket.accept()
    logger.info(f"Voice demo started: {session_id}")
    
    stt_provider = None
    llm_provider = None
    tts_provider = None
    browser_gateway = None
    pipeline = None
    call_session = None
    barge_in_event = asyncio.Event()
    current_voice_id = "sophia"  # Default voice
    
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
        
        # Get selected voice from config
        current_voice_id = config_msg.get("voice_id", "sophia")
        if current_voice_id not in VOICE_AGENTS:
            current_voice_id = "sophia"
        
        agent = VOICE_AGENTS[current_voice_id]
        agent_config = create_agent_config(current_voice_id)
        
        # Initialize voice pipeline
        stt_provider, llm_provider, tts_provider, browser_gateway = await create_voice_pipeline(current_voice_id)
        
        pipeline = VoicePipelineService(
            stt_provider=stt_provider,
            llm_provider=llm_provider,
            tts_provider=tts_provider,
            media_gateway=browser_gateway
        )
        
        call_id = str(uuid.uuid4())
        call_session = create_demo_session(call_id, agent_config, current_voice_id)
        
        await browser_gateway.on_call_started(call_id, {"websocket": websocket})
        
        # Send ready with voice info
        await websocket.send_json({
            "type": "ready",
            "session_id": session_id,
            "call_id": call_id,
            "voice_id": current_voice_id,
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
                    
                    elif msg_type == "switch_voice":
                        # Switch to different voice
                        new_voice_id = data.get("voice_id", "sophia")
                        if new_voice_id in VOICE_AGENTS and new_voice_id != current_voice_id:
                            current_voice_id = new_voice_id
                            agent = VOICE_AGENTS[current_voice_id]
                            
                            # Cleanup old TTS
                            await tts_provider.cleanup()
                            
                            # Initialize new TTS with new voice
                            tts_provider = GoogleTTSProvider()
                            await tts_provider.initialize({
                                "api_key": os.getenv("GOOGLE_TTS_API_KEY"),
                                "voice_id": agent["voice_id"],
                                "language_code": "en-US",
                                "sample_rate": 16000
                            })
                            
                            # Update session
                            call_session.voice_id = agent["voice_id"]
                            call_session.agent_config.agent_name = agent["name"]
                            
                            await websocket.send_json({
                                "type": "voice_switched",
                                "voice_id": current_voice_id,
                                "agent_name": agent["name"]
                            })
                            
                            # New voice introduces itself
                            await send_voice_introduction(tts_provider, agent, websocket, barge_in_event)
                    
                    elif msg_type == "voice_selected":
                        # User selected this voice - acknowledge
                        await websocket.send_json({
                            "type": "voice_confirmed",
                            "voice_id": current_voice_id,
                            "agent_name": agent["name"],
                            "message": "Voice selected. I'm listening..."
                        })
                        logger.info(f"Voice selected: {agent['name']}")
            
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
    tts_provider: GoogleTTSProvider,
    agent: Dict,
    websocket: WebSocket,
    barge_in_event: asyncio.Event
):
    """Send the voice agent's pre-written introduction."""
    intro_text = agent["intro"]
    
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
        async for audio_chunk in tts_provider.stream_synthesize(
            text=intro_text,
            voice_id=agent["voice_id"],
            sample_rate=16000
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
