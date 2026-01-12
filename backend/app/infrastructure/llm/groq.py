"""
Groq LLM Provider Implementation
Ultra-fast inference using Groq LPU architecture

Following Groq's official prompting guidelines:
- https://console.groq.com/docs/prompting
- Role channels (system, user, assistant)
- Parameter tuning for voice AI use case
- Stop sequences for cleaner outputs

Day 17: Added timeout handling and deterministic mode for QA.
"""
import os
import asyncio
import logging
from typing import AsyncIterator, List, Optional
from groq import AsyncGroq
from app.domain.interfaces.llm_provider import LLMProvider
from app.domain.models.conversation import Message, MessageRole

logger = logging.getLogger(__name__)

# Default timeout for LLM responses (seconds)
DEFAULT_LLM_TIMEOUT = 10.0


class LLMTimeoutError(Exception):
    """Raised when LLM response times out"""
    pass


class GroqLLMProvider(LLMProvider):
    """
    Groq LLM provider with ultra-fast inference
    
    Recommended models for voice AI (Dec 2025):
    - llama-3.1-8b-instant: 560 t/s - Fastest, ideal for real-time
    - llama-3.3-70b-versatile: 280 t/s - Best quality/speed balance
    - llama-4-scout-17b-16e-instruct: 750 t/s - Preview, very fast
    """
    
    # Default stop sequences to prevent rambling
    DEFAULT_STOP_SEQUENCES = ["User:", "Human:", "\n\n\n"]
    
    def __init__(self):
        self._client: Optional[AsyncGroq] = None
        self._config: dict = {}
        self._model: str = "llama-3.3-70b-versatile"  # Best balance for voice AI
        self._temperature: float = 0.6  # Slightly lower for more consistent responses
        self._max_tokens: int = 100  # Voice responses should be concise
        # Deterministic mode settings (Day 17)
        self._deterministic_mode: bool = False
        self._deterministic_seed: Optional[int] = None
    
    async def initialize(self, config: dict) -> None:
        """Initialize Groq client with configuration"""
        self._config = config
        api_key = config.get("api_key") or os.getenv("GROQ_API_KEY")
        
        if not api_key:
            raise ValueError("Groq API key not found in config or environment")
        
        # Initialize async client
        self._client = AsyncGroq(api_key=api_key)
        
        # Configuration with voice-optimized defaults
        self._model = config.get("model", "llama-3.3-70b-versatile")
        self._temperature = config.get("temperature", 0.6)
        self._max_tokens = config.get("max_tokens", 100)
    
    def set_deterministic_mode(self, enabled: bool = True, seed: int = 42):
        """
        Enable/disable deterministic mode for QA testing.
        
        In deterministic mode:
        - Temperature is set to 0.0 for reproducibility
        - Seed is fixed for consistent outputs
        
        Args:
            enabled: Whether to enable deterministic mode
            seed: Seed value for reproducibility
        """
        self._deterministic_mode = enabled
        self._deterministic_seed = seed if enabled else None
        if enabled:
            logger.info(f"Deterministic mode enabled with seed={seed}")
        else:
            logger.info("Deterministic mode disabled")
    
    async def stream_chat_with_timeout(
        self,
        messages: List[Message],
        timeout_seconds: float = DEFAULT_LLM_TIMEOUT,
        **kwargs
    ) -> AsyncIterator[str]:
        """
        Stream chat completion with timeout enforcement.
        
        Wraps stream_chat with asyncio timeout for graceful degradation.
        
        Args:
            messages: Conversation history
            timeout_seconds: Maximum time to wait for response
            **kwargs: Passed to stream_chat
            
        Yields:
            str: Token/chunk of response
            
        Raises:
            LLMTimeoutError: If response takes longer than timeout
        """
        start_time = asyncio.get_event_loop().time()
        tokens_received = 0
        
        try:
            # Use manual timeout tracking (compatible with Python 3.10+)
            async for token in self.stream_chat(messages, **kwargs):
                # Check if we've exceeded the timeout
                elapsed = asyncio.get_event_loop().time() - start_time
                if elapsed > timeout_seconds:
                    raise asyncio.TimeoutError()
                tokens_received += 1
                yield token
        except asyncio.TimeoutError:
            elapsed = asyncio.get_event_loop().time() - start_time
            logger.error(
                f"LLM timeout after {elapsed:.2f}s (limit: {timeout_seconds}s), "
                f"tokens received: {tokens_received}"
            )
            raise LLMTimeoutError(
                f"LLM response timed out after {timeout_seconds}s"
            )
    
    async def stream_chat(
        self,
        messages: List[Message],
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> AsyncIterator[str]:
        """
        Stream chat completion tokens from Groq
        
        Following Groq's official parameter guidelines:
        - Temperature 0.2-0.4 for factual, 0.6-0.8 for conversational
        - top_p should be 1.0 when using temperature (use one or the other)
        - Stop sequences prevent rambling
        
        Args:
            messages: Conversation history
            system_prompt: System instructions for the AI
            temperature: Randomness (0.0-2.0), defaults to 0.6 for voice
            max_tokens: Maximum response length, defaults to 100 for voice
            **kwargs: Additional parameters (model, stop, top_p, seed)
        
        Yields:
            str: Token/chunk of response
        """
        if not self._client:
            raise RuntimeError("Groq client not initialized. Call initialize() first.")
        
        # Apply deterministic mode settings if enabled
        if self._deterministic_mode:
            temperature = 0.0
            kwargs["seed"] = self._deterministic_seed
        else:
            temperature = temperature if temperature is not None else self._temperature
        
        max_tokens = max_tokens if max_tokens is not None else self._max_tokens
        
        # Build messages array for Groq API using role channels
        groq_messages = []
        
        # System channel: High-level persona & rules
        if system_prompt:
            groq_messages.append({
                "role": "system",
                "content": system_prompt
            })
        
        # User/Assistant channels: Conversation history
        for msg in messages:
            groq_messages.append({
                "role": msg.role.value,
                "content": msg.content
            })
        
        # Get model from kwargs or use configured default
        model = kwargs.get("model", self._model)
        
        # Validate temperature (Groq accepts 0.0-2.0)
        if not 0.0 <= temperature <= 2.0:
            raise ValueError(f"Temperature must be between 0.0 and 2.0, got {temperature}")
        
        # Stop sequences - use provided or defaults for voice AI
        stop_sequences = kwargs.get("stop", self.DEFAULT_STOP_SEQUENCES)
        
        try:
            # Log what we're sending to Groq
            logger.info(f"[GROQ DEBUG] Sending to Groq: model={model}, temp={temperature}, max_tokens={max_tokens}")
            logger.info(f"[GROQ DEBUG] Messages count: {len(groq_messages)}")
            
            # Stream completion using Groq's ultra-fast LPU
            stream = await self._client.chat.completions.create(
                model=model,
                messages=groq_messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
                # Per Groq docs: use temperature OR top_p, not both
                # For voice AI, we use temperature and leave top_p at 1.0
                top_p=kwargs.get("top_p", 1.0),
                stop=stop_sequences,
                # Optional: seed for deterministic outputs (useful for testing)
                seed=kwargs.get("seed", None)
            )
            
            # Yield tokens as they arrive
            token_count = 0
            async for chunk in stream:
                if chunk.choices:
                    delta = chunk.choices[0].delta
                    if delta.content:
                        token_count += 1
                        yield delta.content
            
            logger.info(f"[GROQ DEBUG] Stream completed, yielded {token_count} tokens")
            
            if token_count == 0:
                logger.warning("[GROQ DEBUG] WARNING: Zero tokens received from Groq!")
        
        except Exception as e:
            logger.error(f"Groq LLM streaming failed: {str(e)}")
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
    
    @property
    def is_deterministic(self) -> bool:
        """Check if deterministic mode is enabled"""
        return self._deterministic_mode
    
    def __repr__(self) -> str:
        mode = "deterministic" if self._deterministic_mode else "normal"
        return f"GroqLLMProvider(model={self._model}, temp={self._temperature}, mode={mode})"

