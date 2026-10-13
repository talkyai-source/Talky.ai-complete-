"""Pass delivered agent speech to STT providers that accept conversation context."""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


async def update_stt_agent_context(provider, call_id: str, text: str) -> None:
    """Best-effort vocabulary context, never a reason to fail audio playback.

    Call only after a complete utterance was submitted successfully. This is
    recognition context, not evidence that a caller heard or accepted anything.
    Interrupted, cancelled, and failed TTS must not send the unspoken full text.
    """
    update = getattr(provider, "update_agent_context", None)
    if not callable(update) or not text.strip():
        return
    try:
        await asyncio.wait_for(update(call_id, text), timeout=0.5)
    except Exception as exc:
        # Provider exceptions may contain URLs, credentials, or spoken PII.
        logger.warning("stt_agent_context_unavailable type=%s", type(exc).__name__)
