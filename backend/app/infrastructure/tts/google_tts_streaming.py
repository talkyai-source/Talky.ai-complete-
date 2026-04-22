"""
Google Cloud TTS Streaming Provider Implementation
Uses Chirp 3: HD voices with bidirectional gRPC streaming for ultra-low latency

This provider uses the streaming_synthesize gRPC API which provides:
- 100-300ms first audio latency (vs 1-3s with REST API)
- Bidirectional streaming (send text chunks, receive audio chunks)
- Real-time audio synthesis suitable for voice AI applications

Requirements:
    pip install google-cloud-texttospeech
    
Authentication:
    Set GOOGLE_APPLICATION_CREDENTIALS environment variable to path of service account JSON file
"""
import os
import re
import asyncio
import logging
from typing import AsyncIterator, List, Dict, Optional

import numpy as np

from app.domain.interfaces.tts_provider import TTSProvider
from app.domain.models.conversation import AudioChunk
from app.utils.resilience import CircuitBreaker, CircuitOpenError

logger = logging.getLogger(__name__)

# Import Google Cloud TTS - will be available after pip install
try:
    from google.cloud import texttospeech
    from google.cloud.texttospeech_v1 import TextToSpeechAsyncClient
    from google.cloud.texttospeech_v1.types import (
        StreamingSynthesizeConfig,
        StreamingSynthesizeRequest,
        StreamingSynthesisInput,
        VoiceSelectionParams,
        StreamingAudioConfig,
        AudioEncoding,
    )
    GRPC_AVAILABLE = True
except ImportError:
    GRPC_AVAILABLE = False
    logger.warning("google-cloud-texttospeech not installed. Install with: pip install google-cloud-texttospeech")


# Chirp 3: HD voice options - streaming ONLY works with these voices
CHIRP3_HD_VOICES = [
    {"id": "Orus", "name": "Orus", "gender": "Male", "language": "en-US"},
    {"id": "Charon", "name": "Charon", "gender": "Male", "language": "en-US"},
    {"id": "Fenrir", "name": "Fenrir", "gender": "Male", "language": "en-US"},
    {"id": "Puck", "name": "Puck", "gender": "Male", "language": "en-US"},
    {"id": "Kore", "name": "Kore", "gender": "Female", "language": "en-US"},
    {"id": "Aoede", "name": "Aoede", "gender": "Female", "language": "en-US"},
    {"id": "Leda", "name": "Leda", "gender": "Female", "language": "en-US"},
    {"id": "Zephyr", "name": "Zephyr", "gender": "Female", "language": "en-US"},
]


class GoogleTTSStreamingProvider(TTSProvider):
    """
    Google Cloud TTS provider using bidirectional gRPC streaming.
    
    This provider delivers ultra-low latency TTS by using the streaming_synthesize
    gRPC method which returns audio chunks as soon as they're generated, rather
    than waiting for the complete audio like the REST API.
    
    Key differences from REST API:
    - First audio in ~100-300ms vs 1-3 seconds
    - Audio streams continuously as text is processed
    - Requires Service Account authentication (not API key)
    - Only works with Chirp 3: HD voices
    """
    
    # Retry/circuit breaker constants for gRPC streaming
    _TTS_MAX_RETRIES = 2
    _TTS_RETRY_BASE_DELAY = 0.3

    def __init__(self):
        self._client: Optional[TextToSpeechAsyncClient] = None
        self._config: Dict = {}
        self._default_voice: str = "en-US-Chirp3-HD-Leda"
        self._default_language: str = "en-US"
        self._sample_rate: int = 24000  # Chirp 3: HD optimal sample rate
        self._speaking_rate: float = 1.0
        self._initialized: bool = False
        # Per-chunk response read timeout. If Google's stream stalls on a
        # chunk longer than this, abort promptly so the REST fallback can
        # take over before the caller hears dead air. See
        # docs/stability/google_tts_connection_hardening.md.
        self._response_read_timeout_s: float = 8.0
        # Circuit breaker: trips after 5 consecutive gRPC failures
        self._circuit = CircuitBreaker(
            name="google-tts",
            failure_threshold=5,
            recovery_timeout=30.0,
            success_threshold=2,
        )
    
    async def initialize(self, config: dict) -> None:
        """
        Initialize the gRPC streaming client.
        
        Authentication options (in order of precedence):
        1. GOOGLE_SERVICE_ACCOUNT_JSON env var with raw JSON content
        2. GOOGLE_APPLICATION_CREDENTIALS env var pointing to service account JSON file
        3. Application Default Credentials (ADC) via 'gcloud auth application-default login'
        
        For the simplest setup, paste your service account JSON into GOOGLE_SERVICE_ACCOUNT_JSON.
        """
        if not GRPC_AVAILABLE:
            raise RuntimeError(
                "google-cloud-texttospeech not installed. "
                "Install with: pip install google-cloud-texttospeech"
            )
        
        self._config = config
        self._default_voice = config.get("voice_id", "en-US-Chirp3-HD-Leda")
        self._default_language = config.get("language_code", "en-US")
        self._sample_rate = config.get("sample_rate", 24000)
        self._speaking_rate = config.get("speaking_rate", 1.0)
        self._response_read_timeout_s = float(
            config.get("response_read_timeout_s", 8.0)
        )
        
        # Initialize the async gRPC client
        # Check for service account JSON in environment variable first
        try:
            service_account_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
            
            if service_account_json:
                # Parse JSON credentials from environment variable
                import json
                from google.oauth2 import service_account
                
                try:
                    credentials_info = json.loads(service_account_json)
                    credentials = service_account.Credentials.from_service_account_info(
                        credentials_info,
                        scopes=["https://www.googleapis.com/auth/cloud-platform"]
                    )
                    self._client = TextToSpeechAsyncClient(credentials=credentials)
                    logger.info("GoogleTTSStreamingProvider initialized with service account JSON from env")
                except json.JSONDecodeError as e:
                    raise RuntimeError(
                        f"GOOGLE_SERVICE_ACCOUNT_JSON is not valid JSON: {e}"
                    )
            else:
                # Fall back to file-based credentials or ADC
                # The client automatically uses:
                # 1. GOOGLE_APPLICATION_CREDENTIALS if set
                # 2. Application Default Credentials (ADC) otherwise
                self._client = TextToSpeechAsyncClient()
                logger.info("GoogleTTSStreamingProvider initialized with file-based or ADC credentials")
            
            self._initialized = True
            logger.info(f"GoogleTTSStreamingProvider ready with voice: {self._default_voice}")
        except Exception as e:
            # Provide helpful error message for authentication issues
            error_msg = str(e)
            if "credentials" in error_msg.lower() or "authentication" in error_msg.lower():
                raise RuntimeError(
                    "Google Cloud authentication failed. Options:\n"
                    "  1. Set GOOGLE_SERVICE_ACCOUNT_JSON with your service account JSON content\n"
                    "  2. Set GOOGLE_APPLICATION_CREDENTIALS to point to your service account file\n"
                    "  3. Run: gcloud auth application-default login"
                ) from e
            raise
    
    async def stream_synthesize(
        self,
        text: str,
        voice_id: str,
        sample_rate: int = 24000,
        **kwargs
    ) -> AsyncIterator[AudioChunk]:
        """
        Synthesize speech using bidirectional gRPC streaming.
        
        This method streams audio chunks as they're generated, providing
        dramatically lower latency than the REST API.
        
        Args:
            text: Text to synthesize
            voice_id: Voice identifier (e.g., "en-US-Chirp3-HD-Leda" or just "Leda")
            sample_rate: Audio sample rate (default 24000 for Chirp 3: HD)
            **kwargs: Additional parameters (language_code, speaking_rate)
        
        Yields:
            AudioChunk: Audio data chunks in PCM format as they're generated
        """
        if not self._initialized or not self._client:
            raise RuntimeError(
                "GoogleTTSStreamingProvider not initialized. Call initialize() first."
            )

        selected_voice = self._normalize_voice_id(voice_id)
        language_code = kwargs.get("language_code", self._default_language)
        speaking_rate = kwargs.get("speaking_rate", self._speaking_rate)

        logger.debug(
            "Streaming TTS: voice=%s, text_length=%d", selected_voice, len(text)
        )

        first_chunk_yielded = False
        streaming_err: Optional[Exception] = None

        try:
            async with self._circuit:
                async for chunk in self._streaming_attempt(
                    text, selected_voice, language_code, sample_rate, speaking_rate
                ):
                    first_chunk_yielded = True
                    yield chunk
                return  # streaming finished cleanly

        except CircuitOpenError as co:
            logger.error("Google TTS circuit breaker open: %s", co)
            raise RuntimeError(f"TTS provider unavailable: {co}") from co

        except Exception as e:
            streaming_err = e
            if first_chunk_yielded:
                logger.warning(
                    "google_tts_streaming: streaming failed post-first-chunk — "
                    "raising (no replay): %s",
                    e,
                )
                raise RuntimeError(
                    f"Google TTS streaming interrupted after first chunk: {e}"
                ) from e

        # Pre-first-chunk streaming failure: fall back to unary synthesis.
        # Runs outside the circuit breaker — the fallback is the escape
        # hatch, and counting its failures would double-count against the
        # streaming path that already failed.
        logger.warning(
            "google_tts_streaming: streaming failed pre-first-chunk — "
            "falling back to REST for sentence (%d chars): %s",
            len(text), streaming_err,
        )
        async for chunk in self._rest_fallback_attempt(
            text, selected_voice, language_code, sample_rate, speaking_rate
        ):
            yield chunk

    async def _streaming_attempt(
        self,
        text: str,
        selected_voice: str,
        language_code: str,
        sample_rate: int,
        speaking_rate: float,
    ) -> AsyncIterator[AudioChunk]:
        """
        Single streaming pass with per-chunk read timeout. Raises on any
        failure. Yields AudioChunks in Float32 format.
        """
        streaming_config = StreamingSynthesizeConfig(
            voice=VoiceSelectionParams(
                name=selected_voice,
                language_code=language_code,
            ),
            streaming_audio_config=StreamingAudioConfig(
                audio_encoding=AudioEncoding.PCM,
                sample_rate_hertz=sample_rate,
                speaking_rate=speaking_rate,
            ),
        )

        async def _request_generator():
            yield StreamingSynthesizeRequest(streaming_config=streaming_config)
            for sentence in self._split_into_sentences(text):
                if sentence.strip():
                    yield StreamingSynthesizeRequest(
                        input=StreamingSynthesisInput(text=sentence)
                    )

        response_stream = await self._client.streaming_synthesize(
            requests=_request_generator()
        )

        aiter_stream = response_stream.__aiter__()
        chunk_count = 0
        while True:
            try:
                response = await asyncio.wait_for(
                    aiter_stream.__anext__(),
                    timeout=self._response_read_timeout_s,
                )
            except StopAsyncIteration:
                logger.debug("Streaming TTS complete: %d chunks yielded", chunk_count)
                return
            except asyncio.TimeoutError:
                logger.warning(
                    "google_tts_streaming: chunk read stall >%.1fs — "
                    "aborting stream for REST fallback",
                    self._response_read_timeout_s,
                )
                raise

            if not response.audio_content:
                continue

            int16_array = np.frombuffer(response.audio_content, dtype=np.int16)
            float32_data = (int16_array.astype(np.float32) / 32768.0).tobytes()
            chunk_count += 1
            yield AudioChunk(
                data=float32_data,
                sample_rate=sample_rate,
                channels=1,
            )

    async def _rest_fallback_attempt(
        self,
        text: str,
        selected_voice: str,
        language_code: str,
        sample_rate: int,
        speaking_rate: float,
    ) -> AsyncIterator[AudioChunk]:
        """
        Unary SynthesizeSpeech fallback. Runs when the streaming path has
        failed before emitting any audio. Returns the entire buffer in one
        RPC, sliced into the same AudioChunk framing as the streaming path
        so the media gateway is unaware which path produced the audio.
        """
        from google.cloud.texttospeech_v1.types import (
            SynthesizeSpeechRequest,
            SynthesisInput,
            AudioConfig,
        )

        request = SynthesizeSpeechRequest(
            input=SynthesisInput(text=text),
            voice=VoiceSelectionParams(
                name=selected_voice,
                language_code=language_code,
            ),
            audio_config=AudioConfig(
                audio_encoding=AudioEncoding.LINEAR16,
                sample_rate_hertz=sample_rate,
                speaking_rate=speaking_rate,
            ),
        )

        response = await self._client.synthesize_speech(request=request)
        audio_bytes = response.audio_content or b""
        # SynthesizeSpeech with LINEAR16 wraps PCM in a 44-byte RIFF/WAV
        # header; strip it so the output matches the streaming path.
        if len(audio_bytes) >= 44 and audio_bytes[:4] == b"RIFF":
            audio_bytes = audio_bytes[44:]

        if not audio_bytes:
            return

        int16_array = np.frombuffer(audio_bytes, dtype=np.int16)
        float32_data = (int16_array.astype(np.float32) / 32768.0).tobytes()

        chunk_size = 16384
        for i in range(0, len(float32_data), chunk_size):
            yield AudioChunk(
                data=float32_data[i:i + chunk_size],
                sample_rate=sample_rate,
                channels=1,
            )
    
    def _normalize_voice_id(self, voice_id: str) -> str:
        """
        Normalize voice_id to full Google TTS format.
        
        Accepts:
            - Full format: "en-US-Chirp3-HD-Leda"
            - Short format: "Leda" (will be expanded)
        """
        if not voice_id:
            return self._default_voice
        
        # Already in full format
        if "Chirp3-HD" in voice_id:
            return voice_id
        
        # Short format - expand to full
        return f"{self._default_language}-Chirp3-HD-{voice_id}"
    
    def _split_into_sentences(self, text: str) -> List[str]:
        """
        Split text into sentences for optimal streaming.
        
        Streaming TTS works best when text is sent in complete sentences,
        as this allows the model to apply proper prosody and intonation.
        """
        # Split on sentence-ending punctuation
        sentences = re.split(r'(?<=[.!?])\s+', text)
        
        # If no sentences found, return the whole text as one chunk
        if not sentences or (len(sentences) == 1 and not sentences[0].strip()):
            return [text]
        
        return [s.strip() for s in sentences if s.strip()]
    
    async def get_available_voices(self) -> List[Dict]:
        """Get list of available Chirp 3: HD voices that support streaming."""
        return [
            {
                "id": f"{voice['language']}-Chirp3-HD-{voice['id']}",
                "name": voice["name"],
                "language": voice["language"],
                "gender": voice["gender"],
                "description": f"Chirp 3: HD {voice['gender']} voice (streaming enabled)"
            }
            for voice in CHIRP3_HD_VOICES
        ]
    
    async def cleanup(self) -> None:
        """Release resources."""
        if self._client:
            try:
                # Close the gRPC channel properly
                transport = self._client.transport
                if hasattr(transport, 'close'):
                    close_result = transport.close()
                    # If close returns a coroutine, await it
                    if hasattr(close_result, '__await__'):
                        await close_result
            except Exception as e:
                logger.warning(f"Error closing Google TTS transport: {e}")
            finally:
                self._client = None
        self._initialized = False
        logger.debug("GoogleTTSStreamingProvider cleaned up")

    
    @property
    def name(self) -> str:
        """Provider name."""
        return "google-streaming"
    
    def __repr__(self) -> str:
        return f"GoogleTTSStreamingProvider(voice={self._default_voice}, streaming=True)"
