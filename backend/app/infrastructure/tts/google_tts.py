"""
Google Cloud TTS Provider Implementation
Uses Chirp 3: HD voices with ultra-realistic speech synthesis
"""
import os
import asyncio
import aiohttp
from typing import AsyncIterator, List, Dict, Optional
from app.domain.interfaces.tts_provider import TTSProvider
from app.domain.models.conversation import AudioChunk


# Chirp 3: HD voice options with their characteristics
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


class GoogleTTSProvider(TTSProvider):
    """Google Cloud TTS provider using Chirp 3: HD voices"""
    
    # Google Cloud TTS REST API endpoint
    TTS_API_URL = "https://texttospeech.googleapis.com/v1/text:synthesize"
    
    def __init__(self):
        self._api_key: Optional[str] = None
        self._config: Dict = {}
        self._default_voice: str = "en-US-Chirp3-HD-Orus"
        self._default_language: str = "en-US"
        self._sample_rate: int = 24000  # Chirp 3: HD optimal sample rate
        self._session: Optional[aiohttp.ClientSession] = None
    
    async def initialize(self, config: dict) -> None:
        """Initialize Google TTS client with configuration"""
        self._config = config
        self._api_key = config.get("api_key") or os.getenv("GOOGLE_TTS_API_KEY")
        
        if not self._api_key:
            raise ValueError("Google TTS API key not found in config or GOOGLE_TTS_API_KEY environment variable")
        
        # Configuration options
        self._default_voice = config.get("voice_id", "en-US-Chirp3-HD-Orus")
        self._default_language = config.get("language_code", "en-US")
        self._sample_rate = config.get("sample_rate", 24000)
        
        # Create aiohttp session for API calls
        self._session = aiohttp.ClientSession()
    
    async def stream_synthesize(
        self,
        text: str,
        voice_id: str,
        sample_rate: int = 16000,
        **kwargs
    ) -> AsyncIterator[AudioChunk]:
        """
        Synthesize speech using Google Cloud TTS Chirp 3: HD voices
        
        Args:
            text: Text to synthesize
            voice_id: Voice identifier (e.g., "en-US-Chirp3-HD-Orus" or just "Orus")
            sample_rate: Audio sample rate (default 16000 for browser compatibility)
            **kwargs: Additional parameters (language_code, speaking_rate, pitch)
        
        Yields:
            AudioChunk: Audio data chunks in Float32 format for browser playback
        """
        if not self._session:
            raise RuntimeError("Google TTS client not initialized. Call initialize() first.")
        
        # Normalize voice_id to full format
        selected_voice = self._normalize_voice_id(voice_id)
        language_code = kwargs.get("language_code", self._default_language)
        speaking_rate = kwargs.get("speaking_rate", 1.0)
        pitch = kwargs.get("pitch", 0.0)
        
        # Build the request payload
        request_payload = {
            "input": {
                "text": text
            },
            "voice": {
                "languageCode": language_code,
                "name": selected_voice
            },
            "audioConfig": {
                "audioEncoding": "LINEAR16",  # 16-bit PCM (Int16)
                "sampleRateHertz": sample_rate,
                "speakingRate": speaking_rate,
                "pitch": pitch
            }
        }
        
        # Make API request
        url = f"{self.TTS_API_URL}?key={self._api_key}"
        
        try:
            async with self._session.post(
                url,
                json=request_payload,
                headers={"Content-Type": "application/json"}
            ) as response:
                if response.status != 200:
                    error_text = await response.text()
                    raise RuntimeError(f"Google TTS API error ({response.status}): {error_text}")
                
                result = await response.json()
                
                # Decode base64 audio content (LINEAR16 = Int16 PCM)
                import base64
                import numpy as np
                audio_content = base64.b64decode(result["audioContent"])
                
                # OPTIMIZED: Use numpy for fast vectorized Int16 → Float32 conversion
                # This is 50-100x faster than Python loop
                int16_array = np.frombuffer(audio_content, dtype=np.int16)
                float32_array = (int16_array.astype(np.float32) / 32768.0)
                float32_data = float32_array.tobytes()
                
                # Yield audio in chunks immediately
                # Larger chunks = fewer WebSocket sends = lower overhead
                # 16KB chunks (~256ms of audio at 16kHz) for smooth playback
                chunk_size = 16384  # 16KB = 4096 samples * 4 bytes
                for i in range(0, len(float32_data), chunk_size):
                    chunk_data = float32_data[i:i + chunk_size]
                    yield AudioChunk(
                        data=chunk_data,
                        sample_rate=sample_rate,
                        channels=1
                    )
        
        except aiohttp.ClientError as e:
            raise RuntimeError(f"Google TTS network error: {str(e)}")
        except Exception as e:
            raise RuntimeError(f"Google TTS synthesis failed: {str(e)}")

    
    def _normalize_voice_id(self, voice_id: str) -> str:
        """
        Normalize voice_id to full Google TTS format
        
        Accepts:
            - Full format: "en-US-Chirp3-HD-Orus"
            - Short format: "Orus" (will be expanded to "en-US-Chirp3-HD-Orus")
        """
        if not voice_id:
            return self._default_voice
        
        # Already in full format
        if "Chirp3-HD" in voice_id:
            return voice_id
        
        # Short format - expand to full
        return f"{self._default_language}-Chirp3-HD-{voice_id}"
    
    async def get_available_voices(self) -> List[Dict]:
        """Get list of available Chirp 3: HD voices"""
        return [
            {
                "id": f"{voice['language']}-Chirp3-HD-{voice['id']}",
                "name": voice["name"],
                "language": voice["language"],
                "gender": voice["gender"],
                "description": f"Chirp 3: HD {voice['gender']} voice"
            }
            for voice in CHIRP3_HD_VOICES
        ]
    
    async def cleanup(self) -> None:
        """Release resources"""
        if self._session:
            await self._session.close()
            self._session = None
    
    @property
    def name(self) -> str:
        """Provider name"""
        return "google"
    
    def __repr__(self) -> str:
        return f"GoogleTTSProvider(voice={self._default_voice}, language={self._default_language})"
