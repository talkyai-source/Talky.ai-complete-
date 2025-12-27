"""
Cartesia TTS Provider Implementation
Ultra-low latency TTS using Cartesia Sonic 3
"""
import os
import asyncio
from typing import AsyncIterator, List, Dict, Optional
from cartesia import AsyncCartesia
from app.domain.interfaces.tts_provider import TTSProvider
from app.domain.models.conversation import AudioChunk


class CartesiaTTSProvider(TTSProvider):
    """Cartesia Sonic 3 TTS provider with ultra-low latency (90ms)"""
    
    def __init__(self):
        self._client: Optional[AsyncCartesia] = None
        self._config: Dict = {}
        self._model_id: str = "sonic-3"
        self._voice_id: str = ""
        self._sample_rate: int = 16000
    
    async def initialize(self, config: dict) -> None:
        """Initialize Cartesia client with configuration"""
        self._config = config
        api_key = config.get("api_key") or os.getenv("CARTESIA_API_KEY")
        
        if not api_key:
            raise ValueError("Cartesia API key not found in config or environment")
        
        # Initialize async client
        self._client = AsyncCartesia(api_key=api_key)
        
        # Configuration
        self._model_id = config.get("model_id", "sonic-3")
        self._voice_id = config.get("voice_id", "6ccbfb76-1fc6-48f7-b71d-91ac6298247b")
        self._sample_rate = config.get("sample_rate", 16000)
    
    async def stream_synthesize(
        self,
        text: str,
        voice_id: str,
        sample_rate: int = 16000,
        **kwargs
    ) -> AsyncIterator[AudioChunk]:
        """
        Stream synthesized audio using Cartesia Sonic 3
        
        Args:
            text: Text to synthesize
            voice_id: Voice identifier (uses configured default if not provided)
            sample_rate: Audio sample rate (8000, 16000, 22050, 24000, 44100)
            **kwargs: Additional parameters (language, emotion, speed, volume)
        
        Yields:
            AudioChunk: Streaming audio chunks with ultra-low latency
        """
        if not self._client:
            raise RuntimeError("Cartesia client not initialized. Call initialize() first.")
        
        # Use provided voice_id or fall back to configured default
        selected_voice_id = voice_id or self._voice_id
        
        # Extract additional parameters
        language = kwargs.get("language", "en")
        container = kwargs.get("container", "raw")
        encoding = kwargs.get("encoding", "pcm_f32le")
        
        # Validate sample rate
        valid_rates = [8000, 16000, 22050, 24000, 44100]
        if sample_rate not in valid_rates:
            raise ValueError(f"Invalid sample rate {sample_rate}. Must be one of {valid_rates}")
        
        try:
            # Use SSE streaming for lower latency
            # SDK v1.0.4: tts.sse() returns a coroutine, must await it first
            sse_stream = await self._client.tts.sse(
                model_id=self._model_id,
                transcript=text,
                voice_id=selected_voice_id,
                language=language,
                output_format={
                    "container": "raw",
                    "sample_rate": sample_rate,
                    "encoding": "pcm_f32le"
                },
                stream=True
            )
            
            # Now iterate over the async stream
            async for chunk in sse_stream:
                if chunk:
                    # SDK returns dict with 'audio' key containing bytes
                    audio_data = chunk.get('audio') if isinstance(chunk, dict) else chunk
                    if audio_data:
                        yield AudioChunk(
                            data=audio_data,
                            sample_rate=sample_rate,
                            channels=1
                        )
        
        except Exception as e:
            raise RuntimeError(f"Cartesia TTS synthesis failed: {str(e)}")
    
    async def get_available_voices(self) -> List[Dict]:
        """Get list of available Cartesia voices"""
        if not self._client:
            raise RuntimeError("Cartesia client not initialized")
        
        try:
            voices = self._client.voices.list()
            
            voice_list = []
            for voice in voices:
                voice_list.append({
                    "id": voice.id,
                    "name": voice.name if hasattr(voice, 'name') else voice.id,
                    "language": voice.language if hasattr(voice, 'language') else "en",
                    "description": voice.description if hasattr(voice, 'description') else ""
                })
            
            return voice_list
        
        except Exception as e:
            raise RuntimeError(f"Failed to fetch Cartesia voices: {str(e)}")
    
    async def cleanup(self) -> None:
        """Release resources"""
        if self._client:
            # Cartesia async client doesn't require explicit cleanup
            # but we'll set it to None for garbage collection
            self._client = None
    
    @property
    def name(self) -> str:
        """Provider name"""
        return "cartesia"
    
    def __repr__(self) -> str:
        return f"CartesiaTTSProvider(model={self._model_id}, voice={self._voice_id})"
