"""
Telephony Provider Factory
"""
from typing import Dict, Type
from app.domain.interfaces.telephony_provider import TelephonyProvider


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
