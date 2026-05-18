"""Caller-speaks-first answer-path helpers.

Caller-first mode now follows the same greeting path as agent-first,
just with a 2-second pause inserted in lifecycle._on_new_call before
``_send_outbound_greeting`` runs. The previous silence safety net,
predicted-response watcher, audio-RMS gating, and per-persona ack
buffers were retired — the simpler model (always greet, just delayed)
won out in live testing.

What's left in this module is the LLM-pool prewarm that runs during
the new-call lifecycle. Caller-first calls skip the ringing-phase
warmup that agent-first calls get, so without an explicit prewarm the
first user turn pays the cold HTTP/2 + TLS + first-token cost
(~100-200 ms). Firing a tiny ``stream_chat`` here primes the pool so
the cost is paid concurrently with the STT/TTS handshake.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def prewarm_llm_pool(voice_session) -> None:
    """Best-effort LLM warmup for the answer-path slow path.

    Failures are swallowed: a warmup miss is non-fatal — the first real
    turn will still work, just ~150ms slower. Reuses the same minimal
    drain as ``agent_first.warm_llm_stream`` so behaviour matches the
    ringing path.
    """
    try:
        from app.domain.services.telephony.modes.agent_first import warm_llm_stream
        await warm_llm_stream(voice_session)
    except Exception as exc:
        logger.info(
            "user_first_llm_prewarm_skipped call=%s reason=%s",
            getattr(voice_session, "call_id", "-")[:12], exc,
        )
