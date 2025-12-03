"""
Groq LLM Provider Implementation
Ultra-fast inference using Groq LPU architecture
"""
import os
from typing import AsyncIterator, List, Optional
from groq import AsyncGroq
from app.domain.interfaces.llm_provider import LLMProvider
from app.domain.models.conversation import Message, MessageRole


class GroqLLMProvider(LLMProvider):
    """Groq LLM provider with ultra-fast inference (up to 185 tokens/sec)"""
    
    def __init__(self):
        self._client: Optional[AsyncGroq] = None
        self._config: dict = {}
        self._model: str = "llama-3.1-8b-instant"
        self._temperature: float = 0.7
        self._max_tokens: int = 150
    
    async def initialize(self, config: dict) -> None:
        """Initialize Groq client with configuration"""
        self._config = config
        api_key = config.get("api_key") or os.getenv("GROQ_API_KEY")
        
        if not api_key:
            raise ValueError("Groq API key not found in config or environment")
        
        # Initialize async client
        self._client = AsyncGroq(api_key=api_key)
        
        # Configuration
        self._model = config.get("model", "llama-3.1-8b-instant")
        self._temperature = config.get("temperature", 0.7)
        self._max_tokens = config.get("max_tokens", 150)
    
    async def stream_chat(
        self,
        messages: List[Message],
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 150,
        **kwargs
    ) -> AsyncIterator[str]:
        """
        Stream chat completion tokens from Groq
        
        Args:
            messages: Conversation history
            system_prompt: System instructions for the AI
            temperature: Randomness (0.0 - 1.0)
            max_tokens: Maximum response length
            **kwargs: Additional parameters (model override, etc.)
        
        Yields:
            str: Token/chunk of response
        """
        if not self._client:
            raise RuntimeError("Groq client not initialized. Call initialize() first.")
        
        # Build messages array for Groq API
        groq_messages = []
        
        # Add system prompt if provided
        if system_prompt:
            groq_messages.append({
                "role": "system",
                "content": system_prompt
            })
        
        # Add conversation history
        for msg in messages:
            groq_messages.append({
                "role": msg.role.value,
                "content": msg.content
            })
        
        # Get model from kwargs or use configured default
        model = kwargs.get("model", self._model)
        
        # Validate temperature
        if not 0.0 <= temperature <= 2.0:
            raise ValueError(f"Temperature must be between 0.0 and 2.0, got {temperature}")
        
        try:
            # Stream completion using Groq's ultra-fast LPU
            stream = await self._client.chat.completions.create(
                model=model,
                messages=groq_messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
                # Additional Groq-specific optimizations
                top_p=kwargs.get("top_p", 1.0),
                stop=kwargs.get("stop", None)
            )
            
            # Yield tokens as they arrive
            async for chunk in stream:
                if chunk.choices:
                    delta = chunk.choices[0].delta
                    if delta.content:
                        yield delta.content
        
        except Exception as e:
            raise RuntimeError(f"Groq LLM streaming failed: {str(e)}")
    
    async def cleanup(self) -> None:
        """Release resources"""
        if self._client:
            # Groq async client doesn't require explicit cleanup
            # but we'll set it to None for garbage collection
            self._client = None
    
    @property
    def name(self) -> str:
        """Provider name"""
        return "groq"
    
    @property
    def supports_streaming(self) -> bool:
        """Groq supports token streaming"""
        return True
    
    def __repr__(self) -> str:
        return f"GroqLLMProvider(model={self._model}, temp={self._temperature})"
