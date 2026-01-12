"""
AI Options Endpoint

Provides API for:
- Listing available LLM, STT, TTS providers and models
- Testing providers with latency measurement
- Saving/loading provider configuration

This endpoint is SEPARATE from the existing voice pipeline.
The selected configuration is used for actual phone calls.
"""
import os
import time
import base64
import asyncio
from typing import Optional, List
from dotenv import load_dotenv
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

# Load environment variables from .env file
load_dotenv()

from app.domain.models.ai_config import (
    AIProviderConfig,
    ProviderListResponse,
    LLMTestRequest,
    LLMTestResponse,
    TTSTestRequest,
    TTSTestResponse,
    VoiceInfo,
    GROQ_MODELS,
    DEEPGRAM_MODELS,
    CARTESIA_MODELS,
    GOOGLE_TTS_MODELS,
)
from app.infrastructure.llm.groq import GroqLLMProvider
# Cartesia disabled - using Google TTS only
# from app.infrastructure.tts.cartesia import CartesiaTTSProvider
from app.infrastructure.tts.google_tts_streaming import GoogleTTSStreamingProvider
from app.domain.models.conversation import Message, MessageRole
from app.api.v1.endpoints.auth import get_current_user


router = APIRouter(prefix="/ai-options", tags=["AI Options"])


# In-memory config storage (per-tenant in production, use database)
_config_cache: dict[str, AIProviderConfig] = {}


@router.get("/providers", response_model=ProviderListResponse)
async def list_providers():
    """
    Get all available AI providers and their models.
    
    Returns:
        ProviderListResponse with LLM, STT, and TTS options
    """
    return ProviderListResponse(
        llm={
            "providers": ["groq"],
            "models": [model.model_dump() for model in GROQ_MODELS]
        },
        stt={
            "providers": ["deepgram"],
            "models": [model.model_dump() for model in DEEPGRAM_MODELS]
        },
        tts={
            "providers": ["google"],  # Cartesia disabled
            "models": [model.model_dump() for model in GOOGLE_TTS_MODELS]
        }
    )


@router.get("/voices", response_model=List[VoiceInfo])
async def list_voices():
    """
    Get all available TTS voices (curated list for voice agents).
    
    Returns curated voices optimized for voice AI agents.
    These voices are pre-selected for clarity, naturalness, and
    suitability for business calls.
    
    Includes:
    - Cartesia Sonic 3 voices (ultra-low latency ~90ms)
    - Google Chirp 3 HD voices (high quality ~200ms, gRPC streaming)
    
    Returns:
        List of VoiceInfo with voice details and preview info
    """
    from app.domain.models.ai_config import GOOGLE_CHIRP3_VOICES
    # Cartesia disabled - only return Google voices
    return GOOGLE_CHIRP3_VOICES


class VoicePreviewRequest(BaseModel):
    """Request for voice preview"""
    voice_id: str
    text: str = "Hello, I am your AI voice assistant. How can I help you today?"


class VoicePreviewResponse(BaseModel):
    """Response with voice preview audio"""
    voice_id: str
    voice_name: str
    audio_base64: str
    duration_seconds: float
    latency_ms: float


@router.post("/voices/preview", response_model=VoicePreviewResponse)
async def preview_voice(request: VoicePreviewRequest):
    """
    Generate a voice preview audio sample.
    
    Synthesizes the given text with the specified voice
    and returns the audio as base64.
    
    Supports both Cartesia and Google Chirp 3 HD voices.
    
    Args:
        request: VoicePreviewRequest with voice_id and optional text
    
    Returns:
        VoicePreviewResponse with base64 audio data
    """
    from app.domain.models.ai_config import GOOGLE_CHIRP3_VOICES
    import os
    
    # Find voice name from Google voices (Cartesia disabled)
    voice_name = "Unknown Voice"
    
    for voice in GOOGLE_CHIRP3_VOICES:
        if voice.id == request.voice_id:
            voice_name = voice.name
            break
    
    # Ensure voice_id is in Google format
    voice_id = request.voice_id
    if not voice_id.startswith("en-US-Chirp3-HD"):
        # Fallback to default Google voice
        voice_id = "en-US-Chirp3-HD-Leda"
        voice_name = "Leda"
    
    try:
        # Always use Google TTS Streaming (Cartesia disabled)
        # Set credentials path - go up from app/api/v1/endpoints/ to backend/
        # __file__ is in: backend/app/api/v1/endpoints/ai_options.py
        # We need: backend/config/google-service-account.json
        backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))))
        creds_path = os.path.join(backend_dir, "config", "google-service-account.json")
        
        # Verify file exists
        if not os.path.exists(creds_path):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Google service account file not found at: {creds_path}"
            )
        
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = creds_path
        
        tts = GoogleTTSStreamingProvider()
        await tts.initialize({
            "voice_id": voice_id,
            "sample_rate": 24000  # Chirp 3 HD optimal
        })
        
        start_time = time.time()
        audio_chunks = []
        
        async for chunk in tts.stream_synthesize(
            text=request.text,
            voice_id=voice_id,
            sample_rate=24000
        ):
            audio_chunks.append(chunk.data)
        
        end_time = time.time()
        await tts.cleanup()
        
        # Combine audio chunks
        combined_audio = b''.join(audio_chunks)
        audio_base64 = base64.b64encode(combined_audio).decode('utf-8')
        
        # Calculate duration (pcm_f32le at 24kHz, 4 bytes per sample)
        duration_seconds = len(combined_audio) / (24000 * 4)
        latency_ms = (end_time - start_time) * 1000
        
        return VoicePreviewResponse(
            voice_id=request.voice_id,
            voice_name=voice_name,
            audio_base64=audio_base64,
            duration_seconds=duration_seconds,
            latency_ms=latency_ms
        )
    
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Voice preview failed: {str(e)}"
        )


@router.post("/test/llm", response_model=LLMTestResponse)
async def test_llm(request: LLMTestRequest):
    """
    Test LLM with a message and measure latency.
    
    Streams the response and tracks:
    - First token latency
    - Total response time
    - Token count
    
    Args:
        request: LLMTestRequest with model, message, and parameters
    
    Returns:
        LLMTestResponse with response text and latency metrics
    """
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Groq API key not configured"
        )
    
    try:
        llm = GroqLLMProvider()
        await llm.initialize({
            "api_key": api_key,
            "model": request.model,
            "temperature": request.temperature,
            "max_tokens": request.max_tokens
        })
        
        messages = [Message(role=MessageRole.USER, content=request.message)]
        system_prompt = "You are a helpful assistant. Keep responses concise and natural."
        
        start_time = time.time()
        first_token_time: Optional[float] = None
        response_text = ""
        token_count = 0
        
        async for token in llm.stream_chat(
            messages=messages,
            system_prompt=system_prompt,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
            model=request.model
        ):
            if first_token_time is None:
                first_token_time = time.time()
            response_text += token
            token_count += 1
        
        end_time = time.time()
        
        await llm.cleanup()
        
        first_token_ms = ((first_token_time or end_time) - start_time) * 1000
        total_latency_ms = (end_time - start_time) * 1000
        
        return LLMTestResponse(
            response=response_text,
            latency_ms=total_latency_ms,
            first_token_ms=first_token_ms,
            total_tokens=token_count,
            model=request.model
        )
    
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"LLM test failed: {str(e)}"
        )


@router.post("/test/tts", response_model=TTSTestResponse)
async def test_tts(request: TTSTestRequest):
    """
    Test TTS with text and measure latency.
    
    Synthesizes audio and tracks:
    - First audio chunk latency
    - Total synthesis time
    - Audio duration
    
    Args:
        request: TTSTestRequest with model, voice_id, text, sample_rate
    
    Returns:
        TTSTestResponse with base64 audio and latency metrics
    """
    # Now using Google TTS (Cartesia disabled)
    import os as _os
    
    # Set credentials path
    backend_dir = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.dirname(__file__)))))
    creds_path = _os.path.join(backend_dir, "config", "google-service-account.json")
    
    if not _os.path.exists(creds_path):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google service account file not found"
        )
    
    _os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = creds_path
    
    # Ensure voice_id is in Google format
    voice_id = request.voice_id
    if not voice_id.startswith("en-US-Chirp3-HD"):
        voice_id = "en-US-Chirp3-HD-Leda"
    
    try:
        tts = GoogleTTSStreamingProvider()
        await tts.initialize({
            "voice_id": voice_id,
            "sample_rate": 24000  # Chirp3-HD
        })
        
        start_time = time.time()
        first_audio_time: Optional[float] = None
        audio_chunks = []
        
        async for chunk in tts.stream_synthesize(
            text=request.text,
            voice_id=voice_id,
            sample_rate=24000
        ):
            if first_audio_time is None:
                first_audio_time = time.time()
            audio_chunks.append(chunk.data)
        
        end_time = time.time()
        
        await tts.cleanup()
        
        # Combine audio chunks
        combined_audio = b''.join(audio_chunks)
        audio_base64 = base64.b64encode(combined_audio).decode('utf-8')
        
        # Calculate duration (pcm_f32le at 24kHz, 4 bytes per sample)
        bytes_per_sample = 4
        duration_seconds = len(combined_audio) / (24000 * bytes_per_sample)
        
        first_audio_ms = ((first_audio_time or end_time) - start_time) * 1000
        total_latency_ms = (end_time - start_time) * 1000
        
        return TTSTestResponse(
            audio_base64=audio_base64,
            latency_ms=total_latency_ms,
            first_audio_ms=first_audio_ms,
            duration_seconds=duration_seconds,
            model="chirp3-hd",
            voice_id=voice_id
        )
    
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"TTS test failed: {str(e)}"
        )


@router.get("/config", response_model=AIProviderConfig)
async def get_config(current_user = Depends(get_current_user)):
    """
    Get current AI provider configuration for the user's tenant.
    
    Returns:
        AIProviderConfig with current settings
    """
    from app.domain.models.ai_config import GoogleTTSModel
    
    tenant_id = current_user.tenant_id or "default"
    
    if tenant_id in _config_cache:
        cached_config = _config_cache[tenant_id]
        
        # Migration: Fix old Cartesia configs to use Google TTS
        if cached_config.tts_provider == "cartesia" or cached_config.tts_model in ["sonic-3", "sonic-2"]:
            cached_config.tts_provider = "google"
            cached_config.tts_model = GoogleTTSModel.CHIRP3_HD.value
            cached_config.tts_voice_id = "en-US-Chirp3-HD-Leda"
            cached_config.tts_sample_rate = 24000
            _config_cache[tenant_id] = cached_config
        
        return cached_config
    
    # Return default config if none saved (now defaults to Google TTS)
    return AIProviderConfig()


@router.post("/config", response_model=AIProviderConfig)
async def save_config(
    config: AIProviderConfig,
    current_user = Depends(get_current_user)
):
    """
    Save AI provider configuration GLOBALLY.
    
    This configuration is used for ALL voice interactions:
    - Dummy calls
    - Real phone calls
    - SIP calls
    - Voice pipeline throughout the application
    
    Args:
        config: AIProviderConfig with desired settings
    
    Returns:
        Saved AIProviderConfig
    """
    from app.domain.services.global_ai_config import set_global_config
    from app.domain.models.ai_config import GoogleTTSModel
    
    tenant_id = current_user.tenant_id or "default"
    
    # Auto-migrate Cartesia configs to Google TTS (Cartesia disabled)
    if config.tts_provider == "cartesia" or config.tts_model in ["sonic-3", "sonic-2"]:
        config.tts_provider = "google"
        config.tts_model = GoogleTTSModel.CHIRP3_HD.value
        config.tts_voice_id = "en-US-Chirp3-HD-Leda"
        config.tts_sample_rate = 24000
    
    # Validate LLM model
    valid_llm_models = [m.id for m in GROQ_MODELS]
    if config.llm_model not in valid_llm_models:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid LLM model. Must be one of: {valid_llm_models}"
        )
    
    # Validate TTS model - Now using Google TTS only (Cartesia disabled)
    valid_tts_models = [m.id for m in GOOGLE_TTS_MODELS]
    if config.tts_model not in valid_tts_models:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid TTS model. Must be one of: {valid_tts_models}"
        )
    
    # Save to global config (applies immediately to all voice interactions)
    set_global_config(config)
    
    # Also save to tenant cache for persistence
    _config_cache[tenant_id] = config
    
    return config


class LatencyBenchmarkResponse(BaseModel):
    """Response from latency benchmark"""
    llm_first_token_ms: float
    llm_total_ms: float
    tts_first_audio_ms: float
    tts_total_ms: float
    total_pipeline_ms: float


@router.post("/benchmark", response_model=LatencyBenchmarkResponse)
async def run_benchmark(config: AIProviderConfig):
    """
    Run a full pipeline latency benchmark.
    
    Tests LLM and TTS with the specified configuration.
    
    Args:
        config: AIProviderConfig to benchmark
    
    Returns:
        LatencyBenchmarkResponse with detailed latency metrics
    """
    import os as _os
    
    groq_key = os.getenv("GROQ_API_KEY")
    
    if not groq_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Groq API key not configured"
        )
    
    # Set Google credentials path
    backend_dir = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.dirname(__file__)))))
    creds_path = _os.path.join(backend_dir, "config", "google-service-account.json")
    
    if not _os.path.exists(creds_path):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google service account file not found"
        )
    
    _os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = creds_path
    
    # Ensure voice_id is in Google format
    voice_id = config.tts_voice_id
    if not voice_id.startswith("en-US-Chirp3-HD"):
        voice_id = "en-US-Chirp3-HD-Leda"
    
    try:
        # Initialize providers
        llm = GroqLLMProvider()
        await llm.initialize({
            "api_key": groq_key,
            "model": config.llm_model,
            "temperature": config.llm_temperature,
            "max_tokens": config.llm_max_tokens
        })
        
        # Using Google TTS (Cartesia disabled)
        tts = GoogleTTSStreamingProvider()
        await tts.initialize({
            "voice_id": voice_id,
            "sample_rate": 24000
        })
        
        # Benchmark LLM
        messages = [Message(role=MessageRole.USER, content="Hello, how are you?")]
        
        llm_start = time.time()
        llm_first_token_time: Optional[float] = None
        llm_response = ""
        
        async for token in llm.stream_chat(
            messages=messages,
            system_prompt="Be brief.",
            model=config.llm_model
        ):
            if llm_first_token_time is None:
                llm_first_token_time = time.time()
            llm_response += token
        
        llm_end = time.time()
        
        llm_first_token_ms = ((llm_first_token_time or llm_end) - llm_start) * 1000
        llm_total_ms = (llm_end - llm_start) * 1000
        
        # Benchmark TTS
        tts_start = time.time()
        tts_first_audio_time: Optional[float] = None
        
        async for chunk in tts.stream_synthesize(
            text=llm_response,
            voice_id=voice_id,
            sample_rate=24000
        ):
            if tts_first_audio_time is None:
                tts_first_audio_time = time.time()
        
        tts_end = time.time()
        
        tts_first_audio_ms = ((tts_first_audio_time or tts_end) - tts_start) * 1000
        tts_total_ms = (tts_end - tts_start) * 1000
        
        # Cleanup
        await llm.cleanup()
        await tts.cleanup()
        
        return LatencyBenchmarkResponse(
            llm_first_token_ms=llm_first_token_ms,
            llm_total_ms=llm_total_ms,
            tts_first_audio_ms=tts_first_audio_ms,
            tts_total_ms=tts_total_ms,
            total_pipeline_ms=llm_total_ms + tts_total_ms
        )
    
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Benchmark failed: {str(e)}"
        )
