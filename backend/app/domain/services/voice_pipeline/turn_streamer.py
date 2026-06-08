"""Per-turn LLM token streaming with sentence-paced TTS.

Extracted from VoicePipelineService._stream_llm_and_tts (item 2, slice 5).
Streams LLM tokens and fires TTS as soon as each complete sentence (or, on
long buffers, the first clause) is ready, so sentence N plays while the LLM
generates N+1. Watches the barge-in event to stop instantly.

Same collaborator pattern as TtsPlayback/TurnRunner: holds the pipeline and
reads its deps (llm_provider / latency_tracker / synthesize_and_send_audio /
_find_sentence_end / _response_max_sentences_for_turn /
_supports_llm_end_session_action / _barge_in_events) at CALL time. The
service keeps _stream_llm_and_tts() as a thin delegator (a test mocks it).

The history-truncation + end-session-tool constants moved here too — they
were only used by this method.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from typing import Optional

from fastapi import WebSocket

from app.domain.models.conversation import MessageRole
from app.domain.models.session import CallSession
from app.domain.services.ask_ai_constants import (
    PRODUCT_KEYWORDS as _ASK_AI_PRODUCT_KEYWORDS,
    TALKY_PRODUCT_INFO as _ASK_AI_PRODUCT_INFO,
)
from app.domain.services.end_session_action import (
    build_end_session_tool_instructions,
    parse_end_session_action,
)
from app.domain.services.llm_guardrails import get_guardrails
from app.infrastructure.llm.groq import LLMTimeoutError
from app.services.scripts import compose_system_prompt

logger = logging.getLogger(__name__)

# Cap conversation history so the Groq context window never overflows (~55
# turns). 20 pairs ≈ 2,500 tokens worst-case, leaving room for system
# prompt + reply. Without truncation an overflow returns HTTP 400 and the
# next turn 400s again → infinite apology loop.
_MAX_HISTORY_PAIRS = int(os.getenv("VOICE_MAX_HISTORY_PAIRS", "20"))

_END_SESSION_TOOL_INSTRUCTIONS = build_end_session_tool_instructions()


def _truncate_history(history: list, max_pairs: int = _MAX_HISTORY_PAIRS) -> list:
    """Return the last max_pairs user/assistant pairs from conversation history."""
    if len(history) <= max_pairs * 2:
        return history[:]
    return history[-(max_pairs * 2):]


# Hard cap on the per-turn knowledge lookup so a slow/contended DB can never add
# more than this to time-to-first-token. On timeout we just skip knowledge for
# the turn — the agent still answers from its persona + history.
_KNOWLEDGE_RETRIEVE_TIMEOUT_S = float(os.getenv("KNOWLEDGE_RETRIEVE_TIMEOUT_MS", "250")) / 1000.0


async def _knowledge_block_for_turn(session: CallSession, messages: list) -> str:
    """Top-k campaign knowledge for the caller's latest message, formatted for
    the system prompt. Only for retrieve/map_retrieve campaigns (inline already
    baked the whole tree in at pre-warm). Fail-soft: returns "" on anything —
    no container, no pool, no hit, timeout, or error — so it can never break or
    stall a turn.
    """
    try:
        last_user = next(
            (m.content for m in reversed(messages) if m.role == MessageRole.USER), "",
        )
        if not last_user.strip():
            return ""

        from app.core.container import get_container
        from app.services.scripts.knowledge.retrieval import retrieve_knowledge

        container = get_container()
        if not getattr(container, "is_initialized", False):
            return ""
        pool = getattr(getattr(container, "db_client", None), "pool", None)
        if pool is None:
            return ""

        _t0 = time.monotonic()
        try:
            hits = await asyncio.wait_for(
                retrieve_knowledge(pool, session.tenant_id, session.campaign_id, last_user, k=2),
                timeout=_KNOWLEDGE_RETRIEVE_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            # TEMP diagnostic (knowledge-not-used investigation): a timeout here
            # means FTS retrieval was too slow and the turn answered WITHOUT
            # knowledge — a prime suspect for "agent isn't using the KB".
            logger.warning(
                "KB_DEBUG call=%s TIMEOUT >%.0fms mode=%s tenant=%s — turn without knowledge",
                session.call_id[:8], _KNOWLEDGE_RETRIEVE_TIMEOUT_S * 1000,
                session.knowledge_mode, str(session.tenant_id)[:8],
            )
            return ""
        _ms = (time.monotonic() - _t0) * 1000.0
        if not hits:
            logger.info(
                "KB_DEBUG call=%s NO_HITS %.0fms q=%r mode=%s tenant=%s",
                session.call_id[:8], _ms, last_user[:60],
                session.knowledge_mode, str(session.tenant_id)[:8],
            )
            return ""
        logger.info(
            "KB_DEBUG call=%s HITS=%d %.0fms q=%r headings=%s",
            session.call_id[:8], len(hits), _ms, last_user[:60],
            [h.get("heading") for h in hits],
        )

        lines = [
            "## Relevant company knowledge for this question",
            "Answer the caller using these facts, in your own words:",
        ]
        for h in hits:
            body = (h.get("voice_answer") or h.get("summary") or h.get("content") or "")
            body = body.strip().replace("\n", " ")
            if body:
                lines.append(f"- {h['heading']}: {body}")
        return "\n".join(lines) if len(lines) > 2 else ""
    except Exception as exc:
        logger.warning("KB_DEBUG call=%s error: %s", getattr(session, "call_id", "?")[:8], exc)
        return ""


class TurnStreamer:
    """Streams one turn's LLM tokens and pipelines TTS per sentence."""

    def __init__(self, pipeline) -> None:
        self._p = pipeline

    async def stream(
        self,
        session: CallSession,
        websocket: Optional[WebSocket] = None,
    ) -> tuple[str, float, float]:
        """
        Stream LLM tokens and pipeline TTS per sentence.

        Returns (full_response_text, llm_latency_ms, tts_latency_ms).
        """
        call_id = session.call_id
        barge_in_event = self._p._barge_in_events.get(call_id)
        guardrails = get_guardrails()

        messages = _truncate_history(session.conversation_history)
        system_prompt = session.system_prompt

        # Ask AI: inject product/pricing info only when the user's message
        # contains relevant keywords (keeps the non-product system prompt small).
        if session.campaign_id == "ask-ai" and messages:
            last_user_text = next(
                (m.content.lower() for m in reversed(messages) if m.role == MessageRole.USER),
                "",
            )
            if any(kw in last_user_text for kw in _ASK_AI_PRODUCT_KEYWORDS):
                system_prompt = system_prompt + "\n\n" + _ASK_AI_PRODUCT_INFO

        # Campaign knowledge (vectorless RAG): for retrieve / map_retrieve
        # campaigns, fetch the node(s) matching the caller's latest message and
        # inject them for THIS turn only. inline campaigns already baked the
        # whole tree into session.system_prompt at pre-warm, so they skip this.
        # The lookup is bounded + fail-soft (see _knowledge_block_for_turn).
        if session.knowledge_mode in ("retrieve", "map_retrieve") and messages:
            kb_block = await _knowledge_block_for_turn(session, messages)
            if kb_block:
                system_prompt = system_prompt + "\n\n" + kb_block

        if self._p._supports_llm_end_session_action(session):
            system_prompt = system_prompt + "\n\n" + _END_SESSION_TOOL_INSTRUCTIONS

        if session.captured_slots is not None:
            system_prompt = compose_system_prompt(system_prompt, session.captured_slots)

        last_user_text_for_limit = next(
            (m.content for m in reversed(messages) if m.role == MessageRole.USER),
            "",
        )
        max_sentences = self._p._response_max_sentences_for_turn(
            session,
            last_user_text_for_limit,
            has_custom_prompt=bool(session.system_prompt),
        )

        all_tokens: list[str] = []
        buf = ""
        first_token = True
        first_sentence = True
        sentences_done = 0
        tts_was_interrupted = False

        t_llm_start = time.monotonic()
        t_tts_first: Optional[float] = None
        t_tts_end: Optional[float] = None

        try:
            async for token in self._p.llm_provider.stream_chat_with_timeout(
                messages,
                system_prompt=system_prompt,
            ):
                if first_token:
                    self._p.latency_tracker.mark_llm_first_token(call_id)
                    # Unblock the frontend audio player immediately on first token
                    # so the jitter buffer can start filling before TTS begins.
                    if websocket:
                        try:
                            await websocket.send_json({"type": "llm_response"})
                        except Exception:
                            pass
                    first_token = False

                if barge_in_event and barge_in_event.is_set():
                    tts_was_interrupted = True
                    break

                all_tokens.append(token)
                buf += token

                # Flush each complete sentence (or, for long buffers, the first
                # clause) to TTS as tokens arrive.
                while not (max_sentences and sentences_done >= max_sentences):
                    idx = self._p._find_sentence_end(buf, allow_clause=len(buf) >= 80)
                    if idx < 0:
                        break

                    sentence = guardrails.clean_response(buf[:idx + 1].strip())
                    buf = buf[idx + 2:] if idx + 2 <= len(buf) else ""

                    if not sentence or len(sentence) < 6:
                        continue

                    if barge_in_event and barge_in_event.is_set():
                        tts_was_interrupted = True
                        break

                    if t_tts_first is None:
                        t_tts_first = time.monotonic()
                        self._p.latency_tracker.mark_tts_start(call_id)

                    session.tts_active = True
                    tts_was_interrupted = await self._p.synthesize_and_send_audio(
                        session, sentence, websocket, track_latency=first_sentence,
                    )
                    first_sentence = False
                    t_tts_end = time.monotonic()
                    sentences_done += 1

                    if tts_was_interrupted:
                        break

                if tts_was_interrupted:
                    break

        except LLMTimeoutError:
            if sentences_done > 0 or t_tts_first is not None:
                # Partial content already sent to TTS — Groq stalled mid-stream.
                logger.warning(
                    "LLM timeout for call %s after %d sentence(s) TTS'd — "
                    "dropping remaining buffer, no fallback", call_id, sentences_done
                )
                buf = ""
            else:
                logger.warning(f"LLM timeout for call {call_id} (no TTS yet), using fallback")
                buf = "I'm sorry, could you repeat that?"
                all_tokens.clear()
                all_tokens.append(buf)
        except Exception as e:
            logger.error(f"LLM streaming error for call {call_id}: {e}", exc_info=True)
            if sentences_done > 0 or t_tts_first is not None:
                logger.warning("LLM error for %s after partial TTS — dropping buffer", call_id)
                buf = ""
            else:
                buf = "I'm sorry, I had trouble processing that. Could you say it again?"
                all_tokens.clear()
                all_tokens.append(buf)

        t_llm_done = time.monotonic()
        self._p.latency_tracker.mark_llm_end(call_id)

        raw_response_text = "".join(all_tokens)
        ask_ai_end_action = (
            parse_end_session_action(raw_response_text)
            if self._p._supports_llm_end_session_action(session)
            else None
        )
        if ask_ai_end_action:
            buf = ""

        # TTS any trailing buffer (final sentence without terminal punctuation).
        if not ask_ai_end_action and not tts_was_interrupted and buf.strip():
            if not (barge_in_event and barge_in_event.is_set()):
                if not max_sentences or sentences_done < max_sentences:
                    sentence = guardrails.clean_response(buf.strip())
                    if sentence:
                        if t_tts_first is None:
                            t_tts_first = time.monotonic()
                            self._p.latency_tracker.mark_tts_start(call_id)
                        session.tts_active = True
                        tts_was_interrupted = await self._p.synthesize_and_send_audio(
                            session, sentence, websocket, track_latency=first_sentence,
                        )
                        first_sentence = False
                        t_tts_end = time.monotonic()

        llm_latency_ms = (t_llm_done - t_llm_start) * 1000
        tts_latency_ms = (
            (t_tts_end - t_tts_first) * 1000
            if t_tts_first is not None and t_tts_end is not None
            else 0.0
        )

        # Build the full response for history / logging.
        if ask_ai_end_action:
            full_text = raw_response_text.strip()
        else:
            full_text = guardrails.clean_response(raw_response_text)

        if not ask_ai_end_action and max_sentences and full_text:
            parts = re.split(r'(?<=[.!?])\s+', full_text.strip())
            full_text = " ".join(parts[:max_sentences])

        return full_text, llm_latency_ms, tts_latency_ms
