"""
TTS Provider Interface
Abstract base class for Text-to-Speech providers
"""
from abc import ABC, abstractmethod
from typing import AsyncIterator, Optional, List, Dict
from app.domain.models.conversation import AudioChunk


class TTSProvider(ABC):
    """Abstract base class for Text-to-Speech providers"""
    
    @abstractmethod
    async def initialize(self, config: dict) -> None:
        """Initialize the provider with configuration"""
        pass
    
    @abstractmethod
    async def stream_synthesize(
        self,
        text: str,
        voice_id: str,
        sample_rate: int = 16000,
        **kwargs
    ) -> AsyncIterator[AudioChunk]:
        """
        Convert text to streaming audio
        
        Args:
            text: Text to synthesize
            voice_id: Voice identifier
            sample_rate: Audio sample rate in Hz
            
        Yields:
            AudioChunk: Audio data chunks
        """
        pass
    
    @abstractmethod
    async def get_available_voices(self) -> List[Dict]:
        """Get list of available voices"""
        pass
    
    @abstractmethod
    async def cleanup(self) -> None:
        """Release resources"""
        pass
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Provider name"""
        pass
