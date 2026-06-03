"""LLM response generation for the voice pipeline.

Extracted from VoicePipelineService (item 2, slice 2). The streaming +
guardrails + per-turn sentence-budget logic lives here as functions that
receive their collaborators (llm_provider, latency_tracker) explicitly,
so it is testable in isolation. VoicePipelineService keeps
get_llm_response()/_response_max_sentences_for_turn as thin delegators —
every call site and test (including ones that mock the method) is
unchanged.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from app.domain.services.llm_guardrails import get_guardrails
from app.infrastructure.llm.groq import LLMTimeoutError
from app.services.scripts import compose_system_prompt

logger = logging.getLogger(__name__)


def response_max_sentences_for_turn(
    turn_or_session,
    user_input: str = "",
    *,
    has_custom_prompt: bool = False,
) -> Optional[int]:
    """Return the sentence budget for a turn.

    Supports the legacy ``(session, user_input, has_custom_prompt=...)``
    call shape and the newer internal ``(turn_id)`` shape.
    """
    if isinstance(turn_or_session, int):
        return 2 if turn_or_session == 0 else None

    session = turn_or_session
    default_limit = getattr(getattr(session, "agent_config", None), "response_max_sentences", 2) or 2
    text = (user_input or "").lower()
    asks_pricing = any(term in text for term in ("pricing", "price", "plan", "plans", "package", "packages"))
    if has_custom_prompt and asks_pricing:
        return max(default_limit, 4)
    return default_limit


async def generate_llm_response(llm_provider, latency_tracker, session, user_input: str) -> str:
    """Stream an LLM response with guardrails + per-turn sentence budget applied.

    ``user_input`` only drives the sentence budget; the model input is the
    session's conversation history + (slot-composed) system prompt — matching
    the original VoicePipelineService.get_llm_response behaviour exactly.
    """
    call_id = session.call_id
    try:
        guardrails = get_guardrails()

        messages = session.conversation_history[:]
        system_prompt = session.system_prompt
        if session.captured_slots is not None:
            system_prompt = compose_system_prompt(system_prompt, session.captured_slots)

        max_sentences = response_max_sentences_for_turn(
            session,
            user_input,
            has_custom_prompt=bool(session.system_prompt),
        )

        tokens: list[str] = []
        first_token = True
        async for token in llm_provider.stream_chat_with_timeout(
            messages,
            system_prompt=system_prompt,
        ):
            if first_token:
                latency_tracker.mark_llm_first_token(call_id)
                first_token = False
            tokens.append(token)
        response = "".join(tokens)

        sanitized = guardrails.clean_response(response)
        if max_sentences and sanitized:
            parts = re.split(r'(?<=[.!?])\s+', sanitized.strip())
            sanitized = " ".join(parts[:max_sentences])
        return sanitized

    except LLMTimeoutError:
        logger.warning(f"LLM timeout for call {call_id}, using fallback")
        return "I'm sorry, could you repeat that?"
    except Exception as e:
        logger.error(f"LLM error for call {call_id}: {e}", exc_info=True)
        return "I'm sorry, I had trouble processing that. Could you say it again?"
