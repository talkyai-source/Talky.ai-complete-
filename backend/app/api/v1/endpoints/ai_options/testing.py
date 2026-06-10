"""Provider test endpoints — direct latency probes that don't touch
tenant config or the voice pipeline.

Endpoints:
  POST /test/llm   - send a single message, measure first-token / total latency
  POST /test/tts   - synthesize a sample, measure first-audio / total latency
"""
from __future__ import annotations

import asyncio
import base64
import logging
import os
import time
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.v1.dependencies import get_current_user

from app.domain.models.ai_config import (
    GEMINI_MODELS,
    LLMTestRequest,
    LLMTestResponse,
    TTSTestRequest,
    TTSTestResponse,
)
from app.domain.models.conversation import Message, MessageRole
from app.infrastructure.llm.gemini import GeminiLLMProvider
from app.infrastructure.llm.groq import GroqLLMProvider
from app.infrastructure.tts.cartesia import CartesiaTTSProvider
from app.infrastructure.tts.deepgram_tts import DeepgramTTSProvider
from app.infrastructure.tts.elevenlabs_tts import ElevenLabsTTSProvider
from app.infrastructure.tts.google_tts_streaming import GoogleTTSStreamingProvider

from ._catalog import (
    _english_deepgram_static_voices,
    _find_elevenlabs_voice,
    _get_deepgram_voices_for_current_key,
    _get_live_cartesia_voices,
    _is_cartesia_voice,
    _is_google_voice,
)
from ._shared import _linear16_to_float32le_bytes

logger = logging.getLogger(__name__)

router = APIRouter(tags=["AI Options"])


@router.post("/test/llm", response_model=LLMTestResponse)
async def test_llm(request: LLMTestRequest, current_user=Depends(get_current_user)):
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
    gemini_model_ids = {m.id for m in GEMINI_MODELS}
    is_gemini = request.model in gemini_model_ids

    if is_gemini:
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Gemini API key not configured. Set GEMINI_API_KEY in .env."
            )
    else:
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Groq API key not configured"
            )

    try:
        llm = GeminiLLMProvider() if is_gemini else GroqLLMProvider()
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
async def test_tts(request: TTSTestRequest, current_user=Depends(get_current_user)):
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
    try:
        tts = None
        voice_id = request.voice_id
        sample_rate = request.sample_rate or 24000
        _, deepgram_voices = await asyncio.gather(
            _get_live_cartesia_voices(),
            _get_deepgram_voices_for_current_key(),
        )
        deepgram_voice_ids = {voice.id for voice in deepgram_voices}
        deepgram_static_voice_ids = {voice.id for voice in _english_deepgram_static_voices()}
        elevenlabs_voice = await _find_elevenlabs_voice(voice_id)

        if _is_cartesia_voice(voice_id):
            if not os.getenv("CARTESIA_API_KEY"):
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Cartesia API key not configured",
                )
            tts = CartesiaTTSProvider()
            await tts.initialize(
                {
                    "voice_id": voice_id,
                    "model_id": request.model or "sonic-3",
                    "sample_rate": sample_rate,
                }
            )
            output_is_linear16 = False
            model_name = request.model or "sonic-3"
        elif _is_google_voice(voice_id):
            import os as _os

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
            output_is_linear16 = False
            model_name = "Chirp3-HD"
        elif voice_id in deepgram_voice_ids or voice_id in deepgram_static_voice_ids:
            if not os.getenv("DEEPGRAM_API_KEY"):
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Deepgram API key not configured",
                )
            tts = DeepgramTTSProvider()
            await tts.initialize({"voice_id": voice_id, "sample_rate": sample_rate})
            output_is_linear16 = True
            model_name = "aura-2"
        elif elevenlabs_voice is not None:
            if not os.getenv("ELEVENLABS_API_KEY"):
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="ElevenLabs API key not configured",
                )
            tts = ElevenLabsTTSProvider()
            await tts.initialize(
                {
                    "voice_id": voice_id,
                    "model_id": request.model or "eleven_flash_v2_5",
                    "sample_rate": sample_rate,
                }
            )
            output_is_linear16 = True
            model_name = request.model or "eleven_flash_v2_5"
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Unknown voice_id",
            )

        start_time = time.time()
        first_audio_time: Optional[float] = None
        audio_chunks: List[bytes] = []

        async for chunk in tts.stream_synthesize(
            text=request.text,
            voice_id=voice_id,
            sample_rate=sample_rate
        ):
            if first_audio_time is None:
                first_audio_time = time.time()
            audio_chunks.append(chunk.data)

        end_time = time.time()

        await tts.cleanup()

        combined_audio = b"".join(audio_chunks)
        if output_is_linear16:
            combined_audio = _linear16_to_float32le_bytes(combined_audio)
        audio_base64 = base64.b64encode(combined_audio).decode("utf-8")

        duration_seconds = len(combined_audio) / (sample_rate * 4)

        first_audio_ms = ((first_audio_time or end_time) - start_time) * 1000
        total_latency_ms = (end_time - start_time) * 1000

        return TTSTestResponse(
            audio_base64=audio_base64,
            latency_ms=total_latency_ms,
            first_audio_ms=first_audio_ms,
            duration_seconds=duration_seconds,
            model=model_name,
            voice_id=voice_id
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"TTS test failed: {str(e)}"
        )
