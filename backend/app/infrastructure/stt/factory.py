"""
STT Provider Factory
Creates STT provider instances based on configuration
"""
from typing import Dict, Type
from app.domain.interfaces.stt_provider import STTProvider


class STTFactory:
    """Factory for creating STT provider instances"""
    
    _providers: Dict[str, Type[STTProvider]] = {}
    
    @classmethod
    def create(cls, provider_name: str, config: dict) -> STTProvider:
        """
        Create and initialize an STT provider
        
        Args:
            provider_name: Name of the provider (e.g., "deepgram-flux")
            config: Provider-specific configuration
            
        Returns:
            Initialized STTProvider instance
            
        Raises:
            ValueError: If provider not found
        """
        if provider_name not in cls._providers:
            available = ", ".join(cls._providers.keys()) if cls._providers else "None"
            raise ValueError(
                f"Unknown STT provider: {provider_name}. "
                f"Available: {available}"
            )
        
        provider_class = cls._providers[provider_name]
        instance = provider_class()
        return instance
    
    @classmethod
    def register(cls, name: str, provider_class: Type[STTProvider]) -> None:
        """Register a custom provider"""
        cls._providers[name] = provider_class
    
    @classmethod
    def list_providers(cls) -> list[str]:
        """Get list of available provider names"""
        return list(cls._providers.keys())


# Auto-register available providers
try:
    from app.infrastructure.stt.deepgram_flux import DeepgramFluxSTTProvider
    STTFactory.register("deepgram-flux", DeepgramFluxSTTProvider)
    STTFactory.register("flux", DeepgramFluxSTTProvider)  # Alias
except ImportError:
    pass  # Deepgram Flux not available

try:
    from app.infrastructure.stt.deepgram import DeepgramSTT
    STTFactory.register("deepgram", DeepgramSTT)
    STTFactory.register("nova-2", DeepgramSTT)  # Alias
except ImportError:
    pass  # Deepgram not available

