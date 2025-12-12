"""
Media Gateway Interface
Abstract base class for media gateway implementations
"""
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
import asyncio


class MediaGateway(ABC):
    """
    Abstract base class for media gateway implementations.
    
    Media gateways handle the interface between VoIP providers (Vonage, Twilio, etc.)
    and the AI voice pipeline. They manage audio streaming, session lifecycle,
    and audio format validation.
    
    This follows the same provider pattern as STT, TTS, and LLM providers.
    """
    
    @abstractmethod
    async def initialize(self, config: Dict[str, Any]) -> None:
        """
        Initialize the media gateway with configuration.
        
        Args:
            config: Configuration dictionary with provider-specific settings
        """
        pass
    
    @abstractmethod
    async def on_call_started(
        self,
        call_id: str,
        metadata: Dict[str, Any]
    ) -> None:
        """
        Handle call start event.
        
        Called when a new call is initiated. Sets up audio buffers,
        initializes session state, and prepares for audio streaming.
        
        Args:
            call_id: Unique call identifier
            metadata: Call metadata (campaign_id, lead_id, phone_number, etc.)
        """
        pass
    
    @abstractmethod
    async def on_audio_received(
        self,
        call_id: str,
        audio_chunk: bytes
    ) -> None:
        """
        Handle incoming audio chunk from VoIP provider.
        
        Validates audio format, buffers the chunk, and makes it available
        for the STT pipeline.
        
        Args:
            call_id: Unique call identifier
            audio_chunk: Raw PCM audio data
        """
        pass
    
    @abstractmethod
    async def on_call_ended(
        self,
        call_id: str,
        reason: str
    ) -> None:
        """
        Handle call end event.
        
        Cleans up resources, flushes buffers, and finalizes session state.
        
        Args:
            call_id: Unique call identifier
            reason: Reason for call ending (hangup, error, timeout, etc.)
        """
        pass
    
    @abstractmethod
    async def send_audio(
        self,
        call_id: str,
        audio_chunk: bytes
    ) -> None:
        """
        Send audio chunk to VoIP provider (outbound audio).
        
        Sends synthesized TTS audio back to the user through the
        VoIP provider's audio stream.
        
        Args:
            call_id: Unique call identifier
            audio_chunk: Raw PCM audio data to send
        """
        pass
    
    @abstractmethod
    def get_audio_queue(self, call_id: str) -> Optional[asyncio.Queue]:
        """
        Get the audio input queue for a call.
        
        Returns the queue where incoming audio chunks are buffered
        for consumption by the STT pipeline.
        
        Args:
            call_id: Unique call identifier
            
        Returns:
            Audio queue or None if call not found
        """
        pass
    
    # =========================================================================
    # Recording Buffer Methods (Day 10)
    # =========================================================================
    
    @abstractmethod
    def get_recording_buffer(self, call_id: str):
        """
        Get the recording buffer for a call.
        
        Returns the buffer where audio chunks are accumulated
        for saving as a recording after the call ends.
        
        Args:
            call_id: Unique call identifier
            
        Returns:
            RecordingBuffer or None if call not found
        """
        pass
    
    @abstractmethod
    def clear_recording_buffer(self, call_id: str) -> None:
        """
        Clear the recording buffer for a call.
        
        Called after the recording has been saved to free memory.
        
        Args:
            call_id: Unique call identifier
        """
        pass
    
    @abstractmethod
    async def cleanup(self) -> None:
        """
        Release all resources and clean up.
        
        Called when shutting down the media gateway.
        Closes all active calls and releases resources.
        """
        pass
    
    @property
    @abstractmethod
    def name(self) -> str:
        """
        Provider name.
        
        Returns:
            Name of the media gateway provider (e.g., "vonage", "twilio")
        """
        pass
