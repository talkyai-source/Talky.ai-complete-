"""
STT Provider Interface
Abstract base class for Speech-to-Text providers
"""
from abc import ABC, abstractmethod
from typing import AsyncIterator, Optional, Callable
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
        context: Optional[str] = None,
        call_id: Optional[str] = None,
        on_eager_end_of_turn: Optional[Callable[[str], None]] = None
    ) -> AsyncIterator[TranscriptChunk]:
        """
        Stream audio and receive real-time transcriptions
        
        Args:
            audio_stream: Async iterator of audio chunks
            language: Language code (ISO 639-1)
            context: Optional context for better accuracy
            call_id: Call ID for tracking eager turn state
            on_eager_end_of_turn: Callback for EagerEndOfTurn events (speculative LLM)
            
        Yields:
            TranscriptChunk: Partial or final transcripts
        """
        pass
    
    def detect_turn_end(self, transcript_chunk: TranscriptChunk) -> bool:
        """
        Detect if the user has finished speaking.
        Default implementation: empty final chunk = EndOfTurn
        
        Args:
            transcript_chunk: Transcript chunk to check
            
        Returns:
            True if turn has ended
        """
        return transcript_chunk.is_final and not transcript_chunk.text
    
    @abstractmethod
    async def cleanup(self) -> None:
        """Release resources"""
        pass
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Provider name"""
        pass
