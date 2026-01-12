"""
SMS Provider Base Classes
Abstract base class for SMS providers.

Day 27: Timed Communication System
"""
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any
from dataclasses import dataclass
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


@dataclass
class SMSResult:
    """Result of an SMS send operation."""
    success: bool
    message_id: Optional[str] = None
    provider: str = ""
    to_number: str = ""
    error: Optional[str] = None
    sent_at: Optional[datetime] = None
    cost: Optional[float] = None
    metadata: Optional[Dict[str, Any]] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "success": self.success,
            "message_id": self.message_id,
            "provider": self.provider,
            "to_number": self.to_number,
            "error": self.error,
            "sent_at": self.sent_at.isoformat() if self.sent_at else None,
            "cost": self.cost,
            "metadata": self.metadata
        }


class SMSProvider(ABC):
    """
    Abstract base class for SMS providers.
    
    All SMS providers must implement:
    - send_sms(): Send a single SMS message
    - is_configured(): Check if provider is properly configured
    """
    
    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Provider identifier (e.g., 'vonage', 'twilio')."""
        pass
    
    @abstractmethod
    async def send_sms(
        self,
        to_number: str,
        message: str,
        from_number: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> SMSResult:
        """
        Send an SMS message.
        
        Args:
            to_number: Destination phone number (E.164 format preferred)
            message: Message content (max ~160 chars for single SMS)
            from_number: Optional sender ID (uses default if not provided)
            metadata: Optional metadata for tracking
            
        Returns:
            SMSResult with success status and message_id
        """
        pass
    
    @abstractmethod
    def is_configured(self) -> bool:
        """Check if the provider has valid configuration."""
        pass
    
    def _normalize_number(self, number: str) -> str:
        """
        Normalize phone number to E.164 format.
        
        Args:
            number: Phone number in various formats
            
        Returns:
            Normalized number (E.164 format)
        """
        # Remove common formatting characters
        number = number.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
        
        # Add + if missing
        if not number.startswith("+"):
            # Assume it's already in international format without +
            if len(number) >= 10:
                number = "+" + number
        
        return number
