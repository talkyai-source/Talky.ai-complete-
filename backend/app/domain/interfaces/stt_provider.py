"""
STT Provider Interface
Abstract base class for Speech-to-Text providers
"""
from abc import ABC, abstractmethod
from typing import AsyncIterator, Optional
from app.domain.models.conversation import TranscriptChunk, AudioChunk


class STTProvider(ABC):
    """Abstract base class for Speech-to-Text providers"""
    
    @abstractmethod
    async def initialize(self, config: dict) -> None:
        """Initialize the provider with configuration"""
        pass
    
    @abstractmethod
    async def stream_transcribe(
        self, 
        audio_stream: AsyncIterator[AudioChunk],
        language: str = "en",
        context: Optional[str] = None
    ) -> AsyncIterator[TranscriptChunk]:
        """
        Stream audio and receive real-time transcriptions
        
        Args:
            audio_stream: Async iterator of audio chunks
            language: Language code (ISO 639-1)
            context: Optional context for better accuracy
            
        Yields:
            TranscriptChunk: Partial or final transcripts
        """
        pass
    
    @abstractmethod
    async def detect_turn_end(self) -> bool:
        """Detect if the user has finished speaking"""
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
