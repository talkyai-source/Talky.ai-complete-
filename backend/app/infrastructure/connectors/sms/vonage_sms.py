"""
Vonage SMS Provider
SMS implementation using Vonage SMS API.

Day 27: Timed Communication System
"""
import os
import logging
from typing import Optional, Dict, Any
from datetime import datetime

try:
    # New Vonage SDK (v4.x)
    from vonage import Vonage, Auth
    from vonage_sms import Sms, SmsMessage
    VONAGE_V4 = True
except ImportError:
    # Fallback for older SDK or missing packages
    VONAGE_V4 = False
    Vonage = None
    Auth = None
    Sms = None
    SmsMessage = None

from .base import SMSProvider, SMSResult

logger = logging.getLogger(__name__)


class VonageSMSProvider(SMSProvider):
    """
    Vonage SMS provider using the Vonage SMS API.
    
    Uses existing Vonage credentials:
    - VONAGE_API_KEY
    - VONAGE_API_SECRET
    - VONAGE_FROM_NUMBER (SMS sender ID)
    
    Supports both Vonage SDK v4.x (new) and v3.x (legacy).
    """
    
    def __init__(self):
        self._client = None
        self._sms = None
        self._initialized = False
        
        # Configuration from environment
        self._api_key = os.getenv("VONAGE_API_KEY")
        self._api_secret = os.getenv("VONAGE_API_SECRET")
        self._default_from = os.getenv("VONAGE_FROM_NUMBER", os.getenv("VONAGE_SMS_FROM"))
    
    @property
    def provider_name(self) -> str:
        return "vonage"
    
    def is_configured(self) -> bool:
        """Check if Vonage SMS credentials are configured."""
        return bool(self._api_key and self._api_secret)
    
    def _ensure_initialized(self) -> None:
        """Initialize Vonage client if not already done."""
        if self._initialized:
            return
        
        if not self.is_configured():
            logger.warning("Vonage SMS not configured - missing API key or secret")
            return
        
        try:
            if VONAGE_V4 and Vonage and Auth:
                # New Vonage SDK v4.x
                auth = Auth(api_key=self._api_key, api_secret=self._api_secret)
                self._client = Vonage(auth=auth)
                self._sms = self._client.sms
                logger.info("VonageSMSProvider initialized (SDK v4.x)")
            else:
                # SDK not available - will simulate sends
                logger.warning("Vonage SDK not available - SMS sends will be simulated")
            
            self._initialized = True
        except Exception as e:
            logger.error(f"Failed to initialize Vonage SMS client: {e}")
            raise
    
    async def send_sms(
        self,
        to_number: str,
        message: str,
        from_number: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> SMSResult:
        """
        Send an SMS via Vonage SMS API.
        
        Args:
            to_number: Destination phone number
            message: SMS content
            from_number: Sender ID (optional, uses default)
            metadata: Optional tracking metadata
            
        Returns:
            SMSResult with send status
        """
        self._ensure_initialized()
        
        # Normalize phone number
        to_number = self._normalize_number(to_number)
        from_number = from_number or self._default_from
        
        if not from_number:
            return SMSResult(
                success=False,
                provider=self.provider_name,
                to_number=to_number,
                error="No from_number configured. Set VONAGE_FROM_NUMBER environment variable."
            )
        
        if not self._sms:
            # Simulate send if SDK not available
            import uuid
            logger.warning(f"Simulating SMS send to {to_number[:6]}... (SDK not available)")
            return SMSResult(
                success=True,
                message_id=f"sim-{uuid.uuid4().hex[:12]}",
                provider=self.provider_name,
                to_number=to_number,
                sent_at=datetime.utcnow(),
                metadata={"simulated": True, **(metadata or {})}
            )
        
        logger.info(f"Sending SMS via Vonage: {from_number} -> {to_number[:6]}...")
        
        try:
            if VONAGE_V4 and SmsMessage:
                # New SDK v4.x - use SmsMessage object
                sms_message = SmsMessage(
                    to=to_number.lstrip("+"),
                    from_=from_number,
                    text=message
                )
                response = self._sms.send(sms_message)
                
                # v4.x response structure
                if hasattr(response, 'messages') and response.messages:
                    msg = response.messages[0]
                    if hasattr(msg, 'status') and str(msg.status) == "0":
                        message_id = getattr(msg, 'message_id', None) or getattr(msg, 'message-id', 'unknown')
                        cost = float(getattr(msg, 'message_price', 0) or 0)
                        
                        logger.info(f"SMS sent successfully: {message_id}")
                        
                        return SMSResult(
                            success=True,
                            message_id=message_id,
                            provider=self.provider_name,
                            to_number=to_number,
                            sent_at=datetime.utcnow(),
                            cost=cost,
                            metadata=metadata
                        )
                    else:
                        error_text = getattr(msg, 'error_text', 'Unknown error')
                        logger.error(f"Vonage SMS failed: {error_text}")
                        
                        return SMSResult(
                            success=False,
                            provider=self.provider_name,
                            to_number=to_number,
                            error=error_text,
                            metadata=metadata
                        )
                else:
                    # Unexpected response format
                    return SMSResult(
                        success=False,
                        provider=self.provider_name,
                        to_number=to_number,
                        error="Unexpected response format from Vonage",
                        metadata=metadata
                    )
            else:
                # Legacy SDK (shouldn't reach here but just in case)
                return SMSResult(
                    success=False,
                    provider=self.provider_name,
                    to_number=to_number,
                    error="Vonage SMS SDK not properly initialized",
                    metadata=metadata
                )
                
        except Exception as e:
            logger.error(f"Exception sending SMS via Vonage: {e}", exc_info=True)
            return SMSResult(
                success=False,
                provider=self.provider_name,
                to_number=to_number,
                error=str(e),
                metadata=metadata
            )


# Singleton instance
_vonage_provider: Optional[VonageSMSProvider] = None


def get_vonage_sms_provider() -> VonageSMSProvider:
    """Get or create VonageSMSProvider singleton."""
    global _vonage_provider
    if _vonage_provider is None:
        _vonage_provider = VonageSMSProvider()
    return _vonage_provider
