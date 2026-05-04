"""POST /benchmark — full LLM+TTS pipeline latency probe."""
from __future__ import annotations

import logging
import os
import time
from typing import Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.domain.models.ai_config import AIProviderConfig, GEMINI_MODELS
from app.domain.models.conversation import Message, MessageRole
from app.infrastructure.llm.gemini import GeminiLLMProvider
from app.infrastructure.llm.groq import GroqLLMProvider
from app.infrastructure.tts.deepgram_tts import DeepgramTTSProvider
from app.infrastructure.tts.elevenlabs_tts import ElevenLabsTTSProvider
from app.infrastructure.tts.google_tts_streaming import GoogleTTSStreamingProvider

logger = logging.getLogger(__name__)

router = APIRouter(tags=["AI Options"])


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

    is_gemini = config.llm_provider == "gemini" or config.llm_model in {m.id for m in GEMINI_MODELS}

    if is_gemini:
        llm_key = os.getenv("GEMINI_API_KEY")
        if not llm_key:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Gemini API key not configured. Set GEMINI_API_KEY in .env."
            )
    else:
        llm_key = os.getenv("GROQ_API_KEY")
        if not llm_key:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Groq API key not configured"
            )

    voice_id = config.tts_voice_id
    sample_rate = config.tts_sample_rate

    try:
        # Initialize providers
        llm = GeminiLLMProvider() if is_gemini else GroqLLMProvider()
        await llm.initialize({
            "api_key": llm_key,
            "model": config.llm_model,
            "temperature": config.llm_temperature,
            "max_tokens": config.llm_max_tokens
        })

        if config.tts_provider == "google":
            backend_dir = _os.path.dirname(
                _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.dirname(__file__))))
            )
            creds_path = _os.path.join(backend_dir, "config", "google-service-account.json")
            if not _os.path.exists(creds_path):
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Google service account file not found",
                )
            _os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = creds_path
            tts = GoogleTTSStreamingProvider()
            await tts.initialize({"voice_id": voice_id, "sample_rate": sample_rate})
        elif config.tts_provider == "deepgram":
            if not os.getenv("DEEPGRAM_API_KEY"):
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Deepgram API key not configured",
                )
            tts = DeepgramTTSProvider()
            await tts.initialize({"voice_id": voice_id, "sample_rate": sample_rate})
        elif config.tts_provider == "elevenlabs":
            if not os.getenv("ELEVENLABS_API_KEY"):
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="ElevenLabs API key not configured",
                )
            tts = ElevenLabsTTSProvider()
            await tts.initialize(
                {
                    "voice_id": voice_id,
                    "model_id": config.tts_model or "eleven_flash_v2_5",
                    "sample_rate": sample_rate,
                }
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unsupported TTS provider '{config.tts_provider}' for benchmark",
            )

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
            sample_rate=sample_rate
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
