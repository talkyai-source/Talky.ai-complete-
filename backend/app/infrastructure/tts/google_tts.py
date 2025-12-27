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
                import struct
                audio_content = base64.b64decode(result["audioContent"])
                
                # Convert LINEAR16 (Int16) to Float32 for browser AudioContext
                # LINEAR16 is 16-bit signed little-endian PCM
                num_samples = len(audio_content) // 2  # 2 bytes per sample
                float32_samples = []
                
                for i in range(num_samples):
                    # Unpack 16-bit signed integer (little-endian)
                    int16_sample = struct.unpack_from('<h', audio_content, i * 2)[0]
                    # Normalize to -1.0 to 1.0 range
                    float32_sample = int16_sample / 32768.0
                    float32_samples.append(float32_sample)
                
                # Pack as Float32 little-endian
                float32_data = struct.pack('<' + 'f' * len(float32_samples), *float32_samples)
                
                # Simulate streaming by yielding audio in chunks
                # Each Float32 sample is 4 bytes, use ~1024 samples per chunk
                chunk_size = 4096  # ~1024 samples * 4 bytes = 4KB chunks
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
