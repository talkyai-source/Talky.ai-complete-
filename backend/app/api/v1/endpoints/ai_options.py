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
)
from app.infrastructure.llm.groq import GroqLLMProvider
from app.infrastructure.tts.cartesia import CartesiaTTSProvider
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
            "providers": ["cartesia"],
            "models": [model.model_dump() for model in CARTESIA_MODELS]
        }
    )


@router.get("/voices", response_model=List[VoiceInfo])
async def list_voices():
    """
    Get all available TTS voices from Cartesia.
    
    Fetches voices dynamically from Cartesia API.
    
    Returns:
        List of VoiceInfo with voice details
    """
    api_key = os.getenv("CARTESIA_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Cartesia API key not configured"
        )
    
    try:
        tts = CartesiaTTSProvider()
        await tts.initialize({"api_key": api_key})
        
        voices_raw = await tts.get_available_voices()
        
        voices = []
        for v in voices_raw:
            voices.append(VoiceInfo(
                id=v.get("id", ""),
                name=v.get("name", "Unknown"),
                language=v.get("language", "en"),
                description=v.get("description", "")
            ))
        
        await tts.cleanup()
        
        return voices
    
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch voices: {str(e)}"
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
    api_key = os.getenv("CARTESIA_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Cartesia API key not configured"
        )
    
    try:
        tts = CartesiaTTSProvider()
        await tts.initialize({
            "api_key": api_key,
            "model_id": request.model,
            "sample_rate": request.sample_rate
        })
        
        start_time = time.time()
        first_audio_time: Optional[float] = None
        audio_chunks = []
        
        async for chunk in tts.stream_synthesize(
            text=request.text,
            voice_id=request.voice_id,
            sample_rate=request.sample_rate
        ):
            if first_audio_time is None:
                first_audio_time = time.time()
            audio_chunks.append(chunk.data)
        
        end_time = time.time()
        
        await tts.cleanup()
        
        # Combine audio chunks
        combined_audio = b''.join(audio_chunks)
        audio_base64 = base64.b64encode(combined_audio).decode('utf-8')
        
        # Calculate duration (assuming pcm_f32le at given sample rate)
        # Each sample is 4 bytes (float32)
        bytes_per_sample = 4
        duration_seconds = len(combined_audio) / (request.sample_rate * bytes_per_sample)
        
        first_audio_ms = ((first_audio_time or end_time) - start_time) * 1000
        total_latency_ms = (end_time - start_time) * 1000
        
        return TTSTestResponse(
            audio_base64=audio_base64,
            latency_ms=total_latency_ms,
            first_audio_ms=first_audio_ms,
            duration_seconds=duration_seconds,
            model=request.model,
            voice_id=request.voice_id
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
    tenant_id = current_user.tenant_id or "default"
    
    if tenant_id in _config_cache:
        return _config_cache[tenant_id]
    
    # Return default config if none saved
    return AIProviderConfig()


@router.post("/config", response_model=AIProviderConfig)
async def save_config(
    config: AIProviderConfig,
    current_user = Depends(get_current_user)
):
    """
    Save AI provider configuration for the user's tenant.
    
    This configuration will be used for actual phone calls.
    
    Args:
        config: AIProviderConfig with desired settings
    
    Returns:
        Saved AIProviderConfig
    """
    tenant_id = current_user.tenant_id or "default"
    
    # Validate LLM model
    valid_llm_models = [m.id for m in GROQ_MODELS]
    if config.llm_model not in valid_llm_models:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid LLM model. Must be one of: {valid_llm_models}"
        )
    
    # Validate STT model
    valid_stt_models = [m.id for m in DEEPGRAM_MODELS]
    if config.stt_model not in valid_stt_models:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid STT model. Must be one of: {valid_stt_models}"
        )
    
    # Validate TTS model
    valid_tts_models = [m.id for m in CARTESIA_MODELS]
    if config.tts_model not in valid_tts_models:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid TTS model. Must be one of: {valid_tts_models}"
        )
    
    # Save to cache (in production, save to database)
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
    groq_key = os.getenv("GROQ_API_KEY")
    cartesia_key = os.getenv("CARTESIA_API_KEY")
    
    if not groq_key or not cartesia_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="API keys not configured"
        )
    
    try:
        # Initialize providers
        llm = GroqLLMProvider()
        await llm.initialize({
            "api_key": groq_key,
            "model": config.llm_model,
            "temperature": config.llm_temperature,
            "max_tokens": config.llm_max_tokens
        })
        
        tts = CartesiaTTSProvider()
        await tts.initialize({
            "api_key": cartesia_key,
            "model_id": config.tts_model,
            "sample_rate": config.tts_sample_rate
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
            voice_id=config.tts_voice_id,
            sample_rate=config.tts_sample_rate
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
