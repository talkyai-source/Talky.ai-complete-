"""Layered system-prompt package.

Final prompt = GENERIC_GUARDRAILS + PERSONA + CAMPAIGN slots + optional
additional instructions. Assembled by `compose_prompt`. Composition
lives in the domain layer so every LLM provider (Groq, Gemini, future
OpenAI/Anthropic) receives the same fully-composed string — no provider
touches prompt logic.
"""
from __future__ import annotations

from app.services.scripts.prompts.agent_name_rotator import (
    MAX_POOL_SIZE,
    pick_agent_name,
    validate_pool,
)
from app.services.scripts.prompts.composer import (
    PromptCompositionError,
    compose_prompt,
)
from app.services.scripts.prompts.personas import (
    PERSONAS,
    PersonaType,
    REQUIRED_SLOTS_BY_PERSONA,
)

__all__ = [
    "MAX_POOL_SIZE",
    "PERSONAS",
    "PersonaType",
    "PromptCompositionError",
    "REQUIRED_SLOTS_BY_PERSONA",
    "compose_prompt",
    "pick_agent_name",
    "validate_pool",
]
