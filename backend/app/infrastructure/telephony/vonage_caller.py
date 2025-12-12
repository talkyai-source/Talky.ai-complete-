"""
Vonage Call Origination Service
Handles outbound call initiation via Vonage Voice API
"""
import os
import logging
from typing import Optional, Dict, Any
from datetime import datetime

import vonage

logger = logging.getLogger(__name__)


class VonageCaller:
    """
    Vonage Voice API client for outbound call origination.
    
    Responsibilities:
    - Initialize outbound calls via Vonage Voice API
    - Configure NCCO for WebSocket connection
    - Handle call origination errors
    
    Requirements:
    - VONAGE_API_KEY and VONAGE_API_SECRET environment variables
    - VONAGE_APP_ID and private key for Voice API
    """
    
    def __init__(self):
        self._client: Optional[vonage.Client] = None
        self._voice: Optional[vonage.Voice] = None
        self._initialized = False
        
        # Configuration
        self._app_id = os.getenv("VONAGE_APP_ID")
        self._private_key_path = os.getenv("VONAGE_PRIVATE_KEY_PATH", "./config/private.key")
        self._default_from_number = os.getenv("VONAGE_FROM_NUMBER")
        self._api_base_url = os.getenv("API_BASE_URL", "http://localhost:8000")
    
    async def initialize(self) -> None:
        """Initialize Vonage client."""
        if self._initialized:
            return
        
        api_key = os.getenv("VONAGE_API_KEY")
        api_secret = os.getenv("VONAGE_API_SECRET")
        
        if not api_key or not api_secret:
            logger.warning("Vonage credentials not configured - calls will be simulated")
            self._initialized = True
            return
        
        try:
            # Load private key if exists
            private_key = None
            if os.path.exists(self._private_key_path):
                with open(self._private_key_path, 'r') as f:
                    private_key = f.read()
            
            # Create Vonage client
            self._client = vonage.Client(
                key=api_key,
                secret=api_secret,
                application_id=self._app_id,
                private_key=private_key
            )
            
            self._voice = vonage.Voice(self._client)
            self._initialized = True
            
            logger.info("VonageCaller initialized successfully")
            
        except Exception as e:
            logger.error(f"Failed to initialize Vonage client: {e}")
            raise
    
    async def make_call(
        self,
        to_number: str,
        from_number: Optional[str] = None,
        webhook_url: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Initiate an outbound call.
        
        Args:
            to_number: Destination phone number (E.164 format)
            from_number: Caller ID (optional, uses default if not provided)
            webhook_url: URL for answer webhook (optional)
            metadata: Additional metadata to pass to webhooks
            
        Returns:
            Call UUID
        """
        if not self._initialized:
            await self.initialize()
        
        # Normalize phone number
        to_number = self._normalize_number(to_number)
        from_number = from_number or self._default_from_number
        
        if not from_number:
            raise ValueError("No from_number provided and VONAGE_FROM_NUMBER not set")
        
        # Build answer URL with metadata
        answer_url = webhook_url or f"{self._api_base_url}/api/v1/webhooks/vonage/answer"
        event_url = f"{self._api_base_url}/api/v1/webhooks/vonage/event"
        
        # Add metadata as query params if provided
        if metadata:
            import urllib.parse
            params = urllib.parse.urlencode(metadata)
            answer_url = f"{answer_url}?{params}"
        
        logger.info(f"Initiating call: {from_number} -> {to_number}")
        
        # Check if we have a real Vonage client
        if self._voice is None:
            # Simulate call for development/testing
            import uuid
            call_uuid = str(uuid.uuid4())
            logger.warning(f"Vonage not configured - simulating call with UUID: {call_uuid}")
            return call_uuid
        
        try:
            # Create the call via Vonage API
            response = self._voice.create_call({
                "to": [{"type": "phone", "number": to_number}],
                "from": {"type": "phone", "number": from_number},
                "answer_url": [answer_url],
                "event_url": [event_url]
            })
            
            call_uuid = response.get("uuid")
            
            if not call_uuid:
                raise ValueError("No UUID returned from Vonage")
            
            logger.info(f"Call initiated: UUID={call_uuid}")
            
            return call_uuid
            
        except Exception as e:
            logger.error(f"Failed to initiate call: {e}")
            raise
    
    async def hangup(self, call_uuid: str) -> bool:
        """
        Hang up an active call.
        
        Args:
            call_uuid: Call UUID to hang up
            
        Returns:
            True if successful
        """
        if self._voice is None:
            logger.warning(f"Vonage not configured - simulating hangup for {call_uuid}")
            return True
        
        try:
            self._voice.update_call(call_uuid, action="hangup")
            logger.info(f"Call hung up: {call_uuid}")
            return True
        except Exception as e:
            logger.error(f"Failed to hang up call {call_uuid}: {e}")
            return False
    
    async def get_call_status(self, call_uuid: str) -> Optional[Dict[str, Any]]:
        """
        Get status of a call.
        
        Args:
            call_uuid: Call UUID
            
        Returns:
            Call details or None if not found
        """
        if self._voice is None:
            return {
                "uuid": call_uuid,
                "status": "simulated",
                "direction": "outbound"
            }
        
        try:
            response = self._voice.get_call(call_uuid)
            return response
        except Exception as e:
            logger.error(f"Failed to get call status for {call_uuid}: {e}")
            return None
    
    def _normalize_number(self, number: str) -> str:
        """
        Normalize phone number to E.164 format.
        
        Args:
            number: Phone number in various formats
            
        Returns:
            Normalized number
        """
        # Remove common formatting characters
        number = number.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
        
        # Add country code if missing (assuming US/Canada)
        if not number.startswith("+"):
            if len(number) == 10:
                number = "+1" + number
            elif len(number) == 11 and number.startswith("1"):
                number = "+" + number
            else:
                number = "+" + number
        
        return number
    
    async def cleanup(self) -> None:
        """Clean up resources."""
        self._client = None
        self._voice = None
        self._initialized = False
