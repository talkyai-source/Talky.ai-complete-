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
from app.domain.services.voice_pipeline import expressive_caps
from app.services.scripts.prompts.guardrails import ELEVEN_V3_AUDIO_TAGS_INSTRUCTIONS
from app.services.scripts.prompts.accent_fillers import (
    resolve_accent,
    accent_filler_block,
    thinking_filler,
)
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
# 500ms: retrieval runs CONCURRENTLY with the LLM's first-token latency, so a
# larger budget here adds ~0 perceived delay but sharply cuts silent
# "timed-out → answered without knowledge" turns under DB load.
_KNOWLEDGE_RETRIEVE_TIMEOUT_S = float(os.getenv("KNOWLEDGE_RETRIEVE_TIMEOUT_MS", "500")) / 1000.0

# Per-turn knowledge-injection budget. Injecting the full body of k=5 nodes
# (e.g. whole product feature-lists) ballooned the prompt to ~11-12k tokens,
# which pushed Groq llama-3.3-70b to ~7s/turn and made the agent stall
# mid-reply. Best practice (Vapi/LiveKit + general RAG) is to inject only a few
# SHORT, relevant chunks. These caps bound the block to ~1.5k chars (~400
# tokens): top-3 nodes, each trimmed to ~350 chars, total ≤ ~1500 chars.
_KB_MAX_CHUNKS = int(os.getenv("VOICE_KB_MAX_CHUNKS", "3"))
_KB_CHUNK_CHARS = int(os.getenv("VOICE_KB_CHUNK_CHARS", "350"))
_KB_TOTAL_CHARS = int(os.getenv("VOICE_KB_TOTAL_CHARS", "1500"))


def _trim_kb_body(text: str, limit: int) -> str:
    """Trim a knowledge body to ~limit chars on a word boundary (keeps it a
    clean spoken fact, not a cut-off word)."""
    text = (text or "").strip().replace("\n", " ")
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(" ,;:-")
    return (cut or text[:limit]).rstrip() + "…"


async def _knowledge_block_for_turn(session: CallSession, messages: list) -> str:
    """Top-k campaign knowledge for the caller's latest message, formatted for
    the system prompt. Only for retrieve/map_retrieve campaigns (inline already
    baked the whole tree in at pre-warm). Fail-soft: returns "" on anything —
    no container, no pool, no hit, timeout, or error — so it can never break or
    stall a turn.
    """
    try:
        # Primary query = caller's latest message, enriched with the previous
        # caller turn so follow-ups ("can you do that there?", "and the price?")
        # still match the right node. Latest is listed first so it dominates rank.
        user_msgs = [m.content for m in reversed(messages) if m.role == MessageRole.USER]
        last_user = user_msgs[0] if user_msgs else ""
        if not last_user.strip():
            return ""
        query = last_user
        if len(user_msgs) > 1 and user_msgs[1].strip():
            query = f"{last_user} {user_msgs[1]}".strip()

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
                retrieve_knowledge(pool, session.tenant_id, session.campaign_id, query, k=_KB_MAX_CHUNKS),
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
            "## Company knowledge — official answers (authoritative)",
            "Use the facts below to answer the caller. Speak naturally and "
            "conversationally, but stay faithful to this information — it is the "
            "official company answer. Do not contradict it or invent details "
            "beyond it:",
        ]
        # Budget the block: prefer the concise voice_answer, trim each node, and
        # stop once the total budget is hit. Keeps the per-turn prompt small so
        # the LLM answers fast instead of stalling on a 10k-token dump.
        used = 0
        for h in hits:
            # voice_answer is authored for speech (short); summary next; only
            # fall back to full content if neither exists, and trim hard.
            raw = h.get("voice_answer") or h.get("summary") or h.get("content") or ""
            body = _trim_kb_body(raw, _KB_CHUNK_CHARS)
            if not body:
                continue
            entry = f"- {h['heading']}: {body}"
            if used + len(entry) > _KB_TOTAL_CHARS and used > 0:
                break  # budget reached — drop the rest (already ranked best-first)
            lines.append(entry)
            used += len(entry)
        return "\n".join(lines) if len(lines) > 2 else ""
    except Exception as exc:
        logger.warning("KB_DEBUG call=%s error: %s", getattr(session, "call_id", "?")[:8], exc)
        return ""


class TurnStreamer:
    """Streams one turn's LLM tokens and pipelines TTS per sentence."""

    def __init__(self, pipeline) -> None:
        self._p = pipeline

    async def _maybe_speak_filler(
        self, session: CallSession, websocket, accent: str, delay: float
    ) -> None:
        """If the real reply hasn't started producing audio within ``delay``
        seconds, speak a short accent-matched 'thinking' phrase so the caller
        hears a natural hesitation instead of dead air. Serialized against the
        real reply by the caller (which awaits this task before sending its
        first sentence), so the two never overlap on the audio channel."""
        try:
            await asyncio.sleep(delay)
            # Real audio already started, caller barged in, or the turn is
            # emitting a structured (JSON) action — do nothing.
            if getattr(session, "_turn_first_audio", False):
                return
            be = self._p._barge_in_events.get(session.call_id)
            if be and be.is_set():
                return
            phrase = thinking_filler(accent)
            if not phrase:
                return
            session._filler_playing = True
            session.tts_active = True
            await self._p.synthesize_and_send_audio(
                session, phrase, websocket, track_latency=False
            )
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # never let a filler failure affect the turn
            logger.debug("thinking-filler skipped for %s: %s", session.call_id, exc)
        finally:
            session._filler_playing = False

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

        # Emotional audio tags — driven by the capability registry (single
        # source of truth). Only voices that actually PERFORM bracket tags
        # ([laughs]/[sighs]/[pause]) get told they may use them; every other
        # voice both (a) isn't instructed to use them and (b) has any stray tag
        # physically stripped in clean_response below. So tags can never leak as
        # spoken words on a non-supporting engine.
        tags_ok = expressive_caps.supports_audio_tags(expressive_caps.model_id_of(self._p))
        if tags_ok:
            system_prompt = system_prompt + "\n\n" + ELEVEN_V3_AUDIO_TAGS_INSTRUCTIONS

        # Accent-matched fillers: a British voice should say "er"/"erm" and
        # British discourse markers; an American voice "um"/"uh"; etc. Resolved
        # once per call from the selected voice and memoized on the session.
        # Neutral / unknown voices return "" (the generic guardrails apply).
        accent = getattr(session, "_voice_accent", None)
        if accent is None:
            accent = resolve_accent(getattr(session, "voice_id", None))
            try:
                session._voice_accent = accent
            except Exception:
                pass
        accent_block = accent_filler_block(accent)
        if accent_block:
            system_prompt = system_prompt + "\n\n" + accent_block

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

        # Thinking-filler: cover a slow first-audio gap with a short spoken
        # hesitation instead of dead air. Launched concurrently; cancelled (or
        # awaited if mid-utterance) right before the first real sentence so the
        # two never overlap. Tunable via TELEPHONY_FILLER_DELAY_MS (0 disables).
        session._turn_first_audio = False
        session._filler_playing = False
        filler_task = None
        try:
            _filler_delay = float(os.getenv("TELEPHONY_FILLER_DELAY_MS", "700")) / 1000.0
        except (TypeError, ValueError):
            _filler_delay = 0.7
        # Skip for ask-AI/end-session-action turns (may emit a JSON envelope).
        if _filler_delay > 0 and not self._p._supports_llm_end_session_action(session):
            filler_task = asyncio.create_task(
                self._maybe_speak_filler(session, websocket, accent, _filler_delay)
            )

        async def _settle_filler() -> None:
            """Stop the thinking-filler before real audio plays. If the filler
            is mid-utterance, wait for it to finish (so it never overlaps the
            real reply); if it's still waiting, cancel it. Idempotent."""
            if getattr(session, "_turn_first_audio", False):
                return
            session._turn_first_audio = True
            if filler_task is None or filler_task.done():
                return
            if getattr(session, "_filler_playing", False):
                try:
                    await filler_task          # let it finish, then real audio
                except Exception:
                    pass
            else:
                filler_task.cancel()
                try:
                    await filler_task
                except Exception:
                    pass

        all_tokens: list[str] = []
        buf = ""
        first_token = True
        first_sentence = True
        sentences_done = 0
        tts_was_interrupted = False
        suppressed_for_action = False

        t_llm_start = time.monotonic()
        t_tts_first: Optional[float] = None
        t_tts_end: Optional[float] = None

        try:
            async for token in self._p.llm_provider.stream_chat_with_timeout(
                messages,
                system_prompt=system_prompt,
                # Honor the tenant's AI-Options settings per turn. None falls
                # back to the provider's configured default inside stream_chat.
                temperature=getattr(session, "llm_temperature", None),
                max_tokens=getattr(session, "llm_max_tokens", None),
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

                # If the model is emitting the structured end-session action
                # (pure JSON — by contract "no spoken text outside JSON"), do NOT
                # stream it to TTS, or the {"action":...} envelope gets read
                # aloud when the caller says goodbye. Accumulate it instead; it's
                # parsed after the stream and only the farewell is spoken. Detect
                # by the first non-whitespace char being '{'.
                if (
                    self._p._supports_llm_end_session_action(session)
                    and buf.lstrip()[:1] == "{"
                ):
                    suppressed_for_action = True
                    continue

                # Flush each complete sentence (or, for long buffers, the first
                # clause) to TTS as tokens arrive.
                while not (max_sentences and sentences_done >= max_sentences):
                    idx = self._p._find_sentence_end(buf, allow_clause=len(buf) >= 80)
                    if idx < 0:
                        break

                    sentence = guardrails.clean_response(buf[:idx + 1].strip(), preserve_audio_tags=tags_ok)
                    buf = buf[idx + 2:] if idx + 2 <= len(buf) else ""

                    if not sentence or len(sentence) < 6:
                        continue

                    if barge_in_event and barge_in_event.is_set():
                        tts_was_interrupted = True
                        break

                    # Settle the thinking-filler before the first real sentence
                    # so they never overlap on the audio channel.
                    await _settle_filler()

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
        elif suppressed_for_action:
            # We withheld a JSON-looking response from TTS but it didn't parse as
            # a valid end-session action — drop it instead of reading the raw
            # envelope aloud.
            buf = ""

        # TTS any trailing buffer (final sentence without terminal punctuation).
        if not ask_ai_end_action and not tts_was_interrupted and buf.strip():
            if not (barge_in_event and barge_in_event.is_set()):
                if not max_sentences or sentences_done < max_sentences:
                    sentence = guardrails.clean_response(buf.strip(), preserve_audio_tags=tags_ok)
                    if sentence:
                        await _settle_filler()
                        if t_tts_first is None:
                            t_tts_first = time.monotonic()
                            self._p.latency_tracker.mark_tts_start(call_id)
                        session.tts_active = True
                        tts_was_interrupted = await self._p.synthesize_and_send_audio(
                            session, sentence, websocket, track_latency=first_sentence,
                        )
                        first_sentence = False
                        t_tts_end = time.monotonic()

        # Cleanup: ensure the thinking-filler task is never left dangling (e.g.
        # an early barge-in or an action turn produced no real audio).
        await _settle_filler()

        # Anti-silence safety net: the LLM stream completed WITHOUT error but
        # produced no spoken content at all (e.g. a reasoning model burned its
        # whole token budget on internal thinking, or an empty completion).
        # That is NOT an error path, so nothing above caught it — without this
        # the caller just hears dead air. Speak a short recovery line instead.
        if (
            not tts_was_interrupted
            and sentences_done == 0
            and t_tts_first is None
            and not ask_ai_end_action
            and not suppressed_for_action
            and not (barge_in_event and barge_in_event.is_set())
        ):
            recovery = "Sorry, I didn't quite catch that — could you say it again?"
            logger.warning(
                "zero_token_turn call=%s — LLM produced no speech; spoke recovery line",
                call_id,
            )
            session.tts_active = True
            t_tts_first = time.monotonic()
            self._p.latency_tracker.mark_tts_start(call_id)
            tts_was_interrupted = await self._p.synthesize_and_send_audio(
                session, recovery, websocket, track_latency=False,
            )
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
            full_text = guardrails.clean_response(raw_response_text, preserve_audio_tags=tags_ok)

        if not ask_ai_end_action and max_sentences and full_text:
            parts = re.split(r'(?<=[.!?])\s+', full_text.strip())
            full_text = " ".join(parts[:max_sentences])

        return full_text, llm_latency_ms, tts_latency_ms
