"""
Telephony Provider Factory
"""
from typing import Dict, Type
from app.domain.interfaces.telephony_provider import TelephonyProvider
from app.domain.interfaces.media_gateway import MediaGateway


class TelephonyFactory:
    """Factory for creating Telephony provider instances"""
    
    _providers: Dict[str, Type[TelephonyProvider]] = {}
    
    @classmethod
    def create(cls, provider_name: str, config: dict) -> TelephonyProvider:
        """Create Telephony provider instance"""
        if provider_name not in cls._providers:
            available = ", ".join(cls._providers.keys()) if cls._providers else "None"
            raise ValueError(f"Unknown Telephony provider: {provider_name}. Available: {available}")
        
        provider_class = cls._providers[provider_name]
        return provider_class()
    
    @classmethod
    def register(cls, name: str, provider_class: Type[TelephonyProvider]) -> None:
        """Register a provider"""
        cls._providers[name] = provider_class
    
    @classmethod
    def list_providers(cls) -> list[str]:
        """List available providers"""
        return list(cls._providers.keys())


class MediaGatewayFactory:
    """
    Factory for creating Media Gateway instances.
    
    Supports switching between Vonage WebSocket and RTP-based gateways
    via configuration without code changes.
    """
    
    @classmethod
    def create(cls, gateway_type: str, config: dict = None) -> MediaGateway:
        """
        Create media gateway instance.
        
        Args:
            gateway_type: Gateway type ("vonage", "rtp", "sip", or "browser")
            config: Optional configuration dictionary
            
        Returns:
            Configured MediaGateway instance
        """
        config = config or {}
        
        if gateway_type == "vonage":
            from app.infrastructure.telephony.vonage_media_gateway import VonageMediaGateway
            gateway = VonageMediaGateway()
        elif gateway_type == "rtp":
            from app.infrastructure.telephony.rtp_media_gateway import RTPMediaGateway
            gateway = RTPMediaGateway()
        elif gateway_type == "sip":
            from app.infrastructure.telephony.sip_media_gateway import SIPMediaGateway
            gateway = SIPMediaGateway()
        elif gateway_type == "browser":
            from app.infrastructure.telephony.browser_media_gateway import BrowserMediaGateway
            gateway = BrowserMediaGateway()
        else:
            raise ValueError(
                f"Unknown media gateway type: {gateway_type}. "
                f"Available: vonage, rtp, sip, browser"
            )
        
        return gateway
    
    @classmethod
    def list_gateways(cls) -> list[str]:
        """List available media gateway types"""
        return ["vonage", "rtp", "sip", "browser"]

