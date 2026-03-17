"""
Groq LLM Provider Implementation
Ultra-fast inference using Groq LPU architecture

Following Groq's official prompting guidelines:
- https://console.groq.com/docs/prompting
- Role channels (system, user, assistant)
- Parameter tuning for voice AI use case
- Stop sequences for cleaner outputs

Available models are defined in app/domain/models/ai_config.py (GROQ_MODELS)
and exposed via the AI Options UI at /api/v1/ai-options/providers

Day 17: Added timeout handling and deterministic mode for QA.
"""
import os
import asyncio
import logging
from typing import AsyncIterator, List, Optional
from groq import AsyncGroq
from app.domain.interfaces.llm_provider import LLMProvider
from app.domain.models.conversation import Message, MessageRole
from app.utils.resilience import CircuitBreaker, CircuitOpenError

logger = logging.getLogger(__name__)

# Default timeout for LLM responses (seconds)
DEFAULT_LLM_TIMEOUT = 10.0

# Retry configuration for transient Groq failures
_LLM_MAX_RETRIES = 2
_LLM_RETRY_BASE_DELAY = 0.3  # 300ms — fast first retry for voice latency budget


class LLMTimeoutError(Exception):
    """Raised when LLM response times out"""
    pass


class GroqLLMProvider(LLMProvider):
    """
    Groq LLM provider with ultra-fast inference
    
    Production Models (available in AI Options):
    - llama-3.3-70b-versatile: 280 t/s - Best quality/speed balance (default)
    - llama-3.1-8b-instant: 560 t/s - Fastest, ideal for real-time
    - openai/gpt-oss-120b: 500 t/s - OpenAI flagship with reasoning
    - openai/gpt-oss-20b: 1000 t/s - Fast and efficient
    
    Preview Models (evaluation only):
    - meta-llama/llama-4-maverick-17b-128e-instruct: 600 t/s - Complex reasoning
    - meta-llama/llama-4-scout-17b-16e-instruct: 750 t/s - Fast variant
    - qwen/qwen3-32b: 400 t/s - Multilingual
    - moonshotai/kimi-k2-instruct-0905: 200 t/s - Large context (262K)
    
    Model selection is configured via AI Options UI (/ai-options page).
    See app/domain/models/ai_config.py for full model specifications.
    """
    
    # Default stop sequences to prevent rambling
    DEFAULT_STOP_SEQUENCES = ["User:", "Human:", "\n\n\n"]

    @staticmethod
    def _is_gpt_oss_model(model: str) -> bool:
        """GPT-OSS models on Groq use the reasoning-specific request contract."""
        return model.startswith("openai/gpt-oss-")

    @staticmethod
    def _is_qwen3_model(model: str) -> bool:
        """Qwen 3 supports explicit thinking / non-thinking modes on Groq."""
        return model.startswith("qwen/qwen3-")

    @classmethod
    def _default_top_p_for_model(cls, model: str) -> float:
        """
        Use Groq-documented defaults per model family instead of forcing one
        sampling profile across all selectable AI Options models.
        """
        if cls._is_gpt_oss_model(model):
            return 0.95
        if cls._is_qwen3_model(model):
            return 0.8
        return 1.0

    @staticmethod
    def _inject_instructions_for_reasoning_model(
        *,
        system_prompt: Optional[str],
        messages: List[dict],
    ) -> List[dict]:
        """
        Groq recommends avoiding system prompts for GPT-OSS models and placing
        instructions in a user message instead.
        """
        if not system_prompt:
            return messages

        instruction_block = (
            "Conversation instructions:\n"
            f"{system_prompt.strip()}\n\n"
            "Apply these instructions to every reply in this conversation."
        )

        merged_messages: List[dict] = []
        injected = False
        for message in messages:
            if not injected and message.get("role") == "user":
                merged_messages.append({
                    **message,
                    "content": (
                        f"{instruction_block}\n\n"
                        f"Current user message:\n{message.get('content', '')}"
                    ),
                })
                injected = True
                continue
            merged_messages.append(message)

        if not injected:
            merged_messages.insert(0, {
                "role": "user",
                "content": instruction_block,
            })

        return merged_messages
    
    def __init__(self):
        self._client: Optional[AsyncGroq] = None
        self._config: dict = {}
        self._model: str = "llama-3.3-70b-versatile"  # Best balance for voice AI
        self._temperature: float = 0.6  # Slightly lower for more consistent responses
        self._max_tokens: int = 100  # Voice responses should be concise
        # Deterministic mode settings (Day 17)
        self._deterministic_mode: bool = False
        self._deterministic_seed: Optional[int] = None
        # Circuit breaker: trips after 5 consecutive failures, re-probes after 30s
        self._circuit = CircuitBreaker(
            name="groq-llm",
            failure_threshold=5,
            recovery_timeout=30.0,
            success_threshold=2,
            excluded_exceptions={ValueError, LLMTimeoutError},
        )
    
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
        - Some reasoning-capable models have model-specific defaults/recommendations
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
        
        # Get model from kwargs or use configured default
        model = kwargs.get("model", self._model)

        # Build messages array for Groq API using role channels
        groq_messages = []
        
        # Groq reasoning docs recommend placing GPT-OSS instructions in a user
        # message instead of a system message for best adherence.
        if system_prompt and not self._is_gpt_oss_model(model):
            groq_messages.append({
                "role": "system",
                "content": system_prompt
            })
        
        # User/Assistant channels: Conversation history
        for msg in messages:
            # Skip empty messages - they can cause issues with the LLM
            if not msg.content or not msg.content.strip():
                logger.warning(f"[GROQ DEBUG] Skipping empty {msg.role.value} message in conversation history")
                continue
                
            groq_messages.append({
                "role": msg.role.value,
                "content": msg.content
            })
        
        if self._is_gpt_oss_model(model):
            groq_messages = self._inject_instructions_for_reasoning_model(
                system_prompt=system_prompt,
                messages=groq_messages,
            )
        
        # Validate temperature (Groq accepts 0.0-2.0)
        if not 0.0 <= temperature <= 2.0:
            raise ValueError(f"Temperature must be between 0.0 and 2.0, got {temperature}")
        
        # Stop sequences - use provided or defaults for voice AI
        stop_sequences = kwargs.get("stop", self.DEFAULT_STOP_SEQUENCES)
        
        try:
            # Log what we're sending to Groq
            logger.debug(f"Sending to Groq: model={model}, temp={temperature}, max_tokens={max_tokens}")
            logger.debug(f"Messages count: {len(groq_messages)}")
            logger.debug(f"Stop sequences: {stop_sequences}")
            
            # Log each message for debugging (truncated)
            for i, msg in enumerate(groq_messages):
                content_preview = msg['content'][:100] if msg['content'] else '<EMPTY>'
                logger.debug(f"Message {i}: role={msg['role']}, content='{content_preview}...'")
            
            top_p = kwargs.get("top_p")
            if top_p is None:
                top_p = self._default_top_p_for_model(model)

            request_kwargs = {
                "model": model,
                "messages": groq_messages,
                "temperature": temperature,
                "max_completion_tokens": max_tokens,
                "stream": True,
                "top_p": top_p,
                "stop": stop_sequences,
                # Optional: seed for deterministic outputs (useful for testing)
                "seed": kwargs.get("seed", None),
            }

            reasoning_format = kwargs.get("reasoning_format")
            include_reasoning = kwargs.get("include_reasoning")
            reasoning_effort = kwargs.get("reasoning_effort")
            if self._is_gpt_oss_model(model):
                if include_reasoning is None:
                    include_reasoning = False
                request_kwargs["include_reasoning"] = include_reasoning
                if reasoning_format is not None:
                    logger.warning(
                        "Ignoring reasoning_format=%s for GPT-OSS model %s; "
                        "Groq documents include_reasoning for these models instead.",
                        reasoning_format,
                        model,
                    )
                if reasoning_effort is not None:
                    request_kwargs["reasoning_effort"] = reasoning_effort
            elif self._is_qwen3_model(model):
                # Groq recommends non-thinking mode for general dialogue.
                if reasoning_effort is None:
                    reasoning_effort = "none"
                if reasoning_format is None:
                    reasoning_format = "hidden"
                request_kwargs["reasoning_effort"] = reasoning_effort
                request_kwargs["reasoning_format"] = reasoning_format
                if include_reasoning is not None:
                    logger.warning(
                        "Ignoring include_reasoning=%s for Qwen model %s; "
                        "Groq documents reasoning_format/reasoning_effort for this family.",
                        include_reasoning,
                        model,
                    )
            elif reasoning_format is not None:
                request_kwargs["reasoning_format"] = reasoning_format
                if reasoning_effort is not None:
                    request_kwargs["reasoning_effort"] = reasoning_effort

            # Stream completion using Groq's ultra-fast LPU
            # Wrapped with circuit breaker + retry for transient failures
            import random as _rand

            last_err = None
            for _attempt in range(_LLM_MAX_RETRIES + 1):
                try:
                    async with self._circuit:
                        stream = await self._client.chat.completions.create(**request_kwargs)

                        # Yield tokens as they arrive
                        token_count = 0
                        async for chunk in stream:
                            if chunk.choices:
                                delta = chunk.choices[0].delta
                                if delta.content:
                                    token_count += 1
                                    yield delta.content

                        logger.debug(f"Stream completed, yielded {token_count} tokens")

                        if token_count == 0:
                            logger.warning("Zero tokens received from Groq")
                    # Success — break out of retry loop
                    break

                except CircuitOpenError:
                    raise  # Don't retry when circuit is open

                except Exception as e:
                    last_err = e
                    if _attempt < _LLM_MAX_RETRIES:
                        _delay = min(
                            _LLM_RETRY_BASE_DELAY * (2 ** _attempt),
                            5.0,
                        ) * (0.5 + _rand.random())
                        logger.warning(
                            f"Groq retry {_attempt + 1}/{_LLM_MAX_RETRIES} "
                            f"after {_delay:.2f}s — {e}"
                        )
                        await asyncio.sleep(_delay)
                    else:
                        logger.error(f"Groq LLM streaming failed after retries: {e}")
                        raise RuntimeError(f"Groq LLM streaming failed: {e}")

        except CircuitOpenError as co:
            logger.error(f"Groq circuit breaker open: {co}")
            raise RuntimeError(f"LLM provider unavailable: {co}")
        except Exception as e:
            if not isinstance(e, RuntimeError):
                logger.error(f"Groq LLM streaming failed: {str(e)}")
                raise RuntimeError(f"Groq LLM streaming failed: {str(e)}"
            )
            raise
    
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
