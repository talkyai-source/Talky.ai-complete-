"""
LLM Provider Interface
Abstract base class for Language Model providers
"""
from abc import ABC, abstractmethod
from typing import AsyncIterator, Optional, List
from app.domain.models.conversation import Message


class LLMProvider(ABC):
    """Abstract base class for Language Model providers"""
    
    @abstractmethod
    async def initialize(self, config: dict) -> None:
        """Initialize the provider with configuration"""
        pass
    
    @abstractmethod
    async def stream_chat(
        self,
        messages: List[Message],
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 150,
        **kwargs
    ) -> AsyncIterator[str]:
        """
        Stream chat completion tokens
        
        Args:
            messages: Conversation history
            system_prompt: System instructions
            temperature: Randomness (0.0 - 1.0)
            max_tokens: Max response length
            
        Yields:
            str: Token/chunk of response
        """
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
    
    @property
    @abstractmethod
    def supports_streaming(self) -> bool:
        """Whether provider supports token streaming"""
        pass
