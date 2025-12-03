"""
Telephony Provider Interface
Abstract base class for telephony/VoIP providers
"""
from abc import ABC, abstractmethod
from typing import Optional, Dict, AsyncIterator
from app.domain.models.conversation import AudioChunk


class TelephonyProvider(ABC):
    """Abstract base class for telephony providers"""
    
    @abstractmethod
    async def initialize(self, config: dict) -> None:
        """Initialize the provider with configuration"""
        pass
    
    @abstractmethod
    async def make_call(
        self,
        to_number: str,
        from_number: str,
        webhook_url: str,
        **kwargs
    ) -> str:
        """
        Initiate an outbound call
        
        Args:
            to_number: Destination phone number
            from_number: Caller ID number
            webhook_url: URL for call events
            
        Returns:
            call_id: Unique call identifier
        """
        pass
    
    @abstractmethod
    async def stream_audio(
        self,
        call_id: str,
        audio_stream: AsyncIterator[AudioChunk]
    ) -> None:
        """Stream audio to an active call"""
        pass
    
    @abstractmethod
    async def receive_audio(
        self,
        call_id: str
    ) -> AsyncIterator[AudioChunk]:
        """Receive audio stream from an active call"""
        pass
    
    @abstractmethod
    async def hangup(self, call_id: str) -> None:
        """End an active call"""
        pass
    
    @abstractmethod
    async def get_call_status(self, call_id: str) -> Dict:
        """Get current call status"""
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
