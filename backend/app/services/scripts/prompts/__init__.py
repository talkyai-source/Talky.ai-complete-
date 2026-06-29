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
    pick_agent_name_for_voice,
    validate_pool,
)
from app.services.scripts.prompts.build import build_turn_prompt
from app.services.scripts.prompts.composer import (
    PromptCompositionError,
    brand_correction_line,
    compose_prompt,
)
from app.services.scripts.prompts.guardrails import model_prompt_addendum
from app.services.scripts.prompts.live_state import build_live_state_block
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
    "brand_correction_line",
    "build_live_state_block",
    "build_turn_prompt",
    "compose_prompt",
    "model_prompt_addendum",
    "pick_agent_name",
    "pick_agent_name_for_voice",
    "validate_pool",
]
