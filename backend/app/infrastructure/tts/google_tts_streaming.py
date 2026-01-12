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
import asyncio
import logging
from typing import AsyncIterator, List, Dict, Optional

from app.domain.interfaces.tts_provider import TTSProvider
from app.domain.models.conversation import AudioChunk

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
    
    def __init__(self):
        self._client: Optional[TextToSpeechAsyncClient] = None
        self._config: Dict = {}
        self._default_voice: str = "en-US-Chirp3-HD-Leda"
        self._default_language: str = "en-US"
        self._sample_rate: int = 24000  # Chirp 3: HD optimal sample rate
        self._speaking_rate: float = 1.0
        self._initialized: bool = False
    
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
        
        # Normalize voice_id to full format
        selected_voice = self._normalize_voice_id(voice_id)
        language_code = kwargs.get("language_code", self._default_language)
        speaking_rate = kwargs.get("speaking_rate", self._speaking_rate)
        
        logger.debug(f"Streaming TTS: voice={selected_voice}, text_length={len(text)}")
        
        # Configure the streaming synthesis
        streaming_config = StreamingSynthesizeConfig(
            voice=VoiceSelectionParams(
                name=selected_voice,
                language_code=language_code,
            ),
            streaming_audio_config=StreamingAudioConfig(
                audio_encoding=AudioEncoding.PCM,  # Raw PCM audio (Float32 compatible)
                sample_rate_hertz=sample_rate,
                speaking_rate=speaking_rate,
            ),
        )
        
        # Create request generator
        async def request_generator():
            # First request must contain config only
            yield StreamingSynthesizeRequest(streaming_config=streaming_config)
            
            # Split text into sentences for optimal streaming
            # Each sentence becomes a separate streaming request
            sentences = self._split_into_sentences(text)
            
            for sentence in sentences:
                if sentence.strip():
                    yield StreamingSynthesizeRequest(
                        input=StreamingSynthesisInput(text=sentence)
                    )
        
        try:
            # Call streaming synthesis and yield audio chunks as they arrive
            response_stream = await self._client.streaming_synthesize(
                requests=request_generator()
            )
            
            chunk_count = 0
            async for response in response_stream:
                if response.audio_content:
                    chunk_count += 1
                    
                    # Convert to Float32 for browser playback
                    import numpy as np
                    
                    # The streaming API returns PCM audio (Int16)
                    int16_array = np.frombuffer(response.audio_content, dtype=np.int16)
                    float32_array = (int16_array.astype(np.float32) / 32768.0)
                    float32_data = float32_array.tobytes()
                    
                    yield AudioChunk(
                        data=float32_data,
                        sample_rate=sample_rate,
                        channels=1
                    )
            
            logger.debug(f"Streaming TTS complete: {chunk_count} chunks yielded")
        
        except Exception as e:
            logger.error(f"Streaming TTS error: {e}")
            raise RuntimeError(f"Google TTS streaming synthesis failed: {str(e)}")
    
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
        import re
        
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
