"""
TTS Provider Factory
"""
from typing import Dict, Type
from app.domain.interfaces.tts_provider import TTSProvider


class TTSFactory:
    """Factory for creating TTS provider instances"""
    
    _providers: Dict[str, Type[TTSProvider]] = {}
    
    @classmethod
    def create(cls, provider_name: str, config: dict) -> TTSProvider:
        """Create TTS provider instance"""
        if provider_name not in cls._providers:
            available = ", ".join(cls._providers.keys()) if cls._providers else "None"
            raise ValueError(f"Unknown TTS provider: {provider_name}. Available: {available}")
        
        provider_class = cls._providers[provider_name]
        return provider_class()
    
    @classmethod
    def register(cls, name: str, provider_class: Type[TTSProvider]) -> None:
        """Register a provider"""
        cls._providers[name] = provider_class
    
    @classmethod
    def list_providers(cls) -> list[str]:
        """List available providers"""
        return list(cls._providers.keys())


# Auto-register available providers
try:
    from app.infrastructure.tts.cartesia import CartesiaTTSProvider
    TTSFactory.register("cartesia", CartesiaTTSProvider)
except ImportError:
    pass  # Cartesia not available

# Auto-register Google TTS
try:
    from app.infrastructure.tts.google_tts import GoogleTTSProvider
    TTSFactory.register("google", GoogleTTSProvider)
except ImportError:
    pass  # Google TTS not available

# Auto-register Google TTS Streaming (low-latency gRPC)
try:
    from app.infrastructure.tts.google_tts_streaming import GoogleTTSStreamingProvider
    TTSFactory.register("google-streaming", GoogleTTSStreamingProvider)
except ImportError:
    pass  # Google TTS Streaming not available (requires google-cloud-texttospeech)

