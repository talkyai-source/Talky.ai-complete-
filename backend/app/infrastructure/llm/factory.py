"""
LLM Provider Factory
"""
from typing import Dict, Type
from app.domain.interfaces.llm_provider import LLMProvider


class LLMFactory:
    """Factory for creating LLM provider instances"""
    
    _providers: Dict[str, Type[LLMProvider]] = {}
    
    @classmethod
    def create(cls, provider_name: str, config: dict) -> LLMProvider:
        """Create LLM provider instance"""
        if provider_name not in cls._providers:
            available = ", ".join(cls._providers.keys()) if cls._providers else "None"
            raise ValueError(f"Unknown LLM provider: {provider_name}. Available: {available}")
        
        provider_class = cls._providers[provider_name]
        return provider_class()
    
    @classmethod
    def register(cls, name: str, provider_class: Type[LLMProvider]) -> None:
        """Register a provider"""
        cls._providers[name] = provider_class
    
    @classmethod
    def list_providers(cls) -> list[str]:
        """List available providers"""
        return list(cls._providers.keys())


# Auto-register available providers
try:
    from app.infrastructure.llm.groq import GroqLLMProvider
    LLMFactory.register("groq", GroqLLMProvider)
except ImportError:
    pass  # Groq not available

