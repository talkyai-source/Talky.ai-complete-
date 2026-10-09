"""Per-turn LLM token streaming with sentence-paced TTS.

Extracted from VoicePipelineService._stream_llm_and_tts (item 2, slice 5).
Streams LLM tokens and fires TTS as soon as each complete sentence (or, on
long buffers, the first clause) is ready, so sentence N plays while the LLM
generates N+1. Watches the barge-in event to stop instantly.

Same collaborator pattern as TtsPlayback/TurnRunner: holds the pipeline and
reads its deps (llm_provider / latency_tracker / synthesize_and_send_audio /
_find_sentence_end /
_supports_llm_end_session_action / _barge_in_events) at CALL time. The
service keeps _stream_llm_and_tts() as a thin delegator (a test mocks it).

Context budgeting and end-session-tool instructions live here alongside
the request assembly that uses them.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Optional

from fastapi import WebSocket

from app.domain.models.conversation import MessageRole
from app.domain.models.session import CallSession
from app.domain.services.ask_ai_constants import (
    TALKY_PRODUCT_INFO as _ASK_AI_PRODUCT_INFO,
)
from app.domain.services.end_session_action import (
    build_end_session_tool_instructions,
    parse_end_session_action,
)
from app.domain.services.voice_pipeline.speech_guard import guard_spoken_sentence
from app.domain.services.voice_pipeline.figure_grounding import guard_figures
from app.domain.services.voice_pipeline.end_call import strip_and_flag
from app.domain.services.llm_guardrails import get_guardrails
from app.domain.services.voice_pipeline import expressive_caps
from app.services.scripts.prompts.guardrails import (
    ELEVEN_V3_AUDIO_TAGS_INSTRUCTIONS,
    CARTESIA_LAUGHTER_INSTRUCTIONS,
)
from app.infrastructure.llm.groq import LLMTimeoutError
from app.services.scripts.prompts.build import build_turn_prompt
from app.domain.services.voice_pipeline.grounded_links import grounded_url_hosts
from app.services.scripts.prompts.live_state import build_live_state_block
from app.services.scripts.knowledge.budget import context_window_for, estimate_tokens
from app.domain.services.voice_pipeline.knowledge_tool import (
    KB_TOOL_NAME, knowledge_tools_for, knowledge_system_addendum, run_knowledge_lookup,
    knowledge_navigation_continuation, remember_recent_knowledge,
)
from app.domain.services.voice_pipeline.contact_recording import (
    CONTACT_TOOL_NAME, CONTACT_TOOL_SPEC, record_contact,
)
from app.domain.services.voice_pipeline.action_tools import (
    action_results_for_session,
    action_tool_system_addendum,
    action_tools_for_turn,
    execution_failure_result,
    result_json,
    run_voice_action,
)
from app.domain.services.voice_pipeline.live_structured_state import (
    ToolResultEvidence,
    reduce_cascaded_session_live_state,
    reduce_live_state,
    render_live_state_block,
)

logger = logging.getLogger(__name__)

_HISTORY_OMISSION_NOTICE = (
    "Earlier conversation is outside this request's context budget. Do not claim "
    "to remember omitted details; ask the caller when an earlier detail matters."
)

_END_SESSION_TOOL_INSTRUCTIONS = build_end_session_tool_instructions()


# Matches the START of an internal action envelope inside a longer buffer:
# an opening brace followed, within a short window, by an "action" key. Kept
# tight so an ordinary spoken brace ("the price is {x}") never trips it.
_ACTION_ENVELOPE_RE = re.compile(
    r"""\{\s*["']?\s*action\s*["']?\s*:""", re.IGNORECASE
)


def _find_action_envelope_start(buf: str) -> int:
    """Index of an action envelope inside `buf`, or -1.

    Used to split a turn where the model emitted prose AND the JSON envelope
    together — the prose is spoken, the envelope is swallowed and parsed. The
    envelope reaching TTS is not cosmetic: a caller heard one read aloud in
    production on 2026-07-08.
    """
    m = _ACTION_ENVELOPE_RE.search(buf or "")
    return m.start() if m else -1


def _readback_protected_values(session) -> tuple[str, ...]:
    """Pending/captured core values + their spoken read-back forms, so the output
    leak-scrubber never deletes a sentence that reads one back to the caller
    (issue #3). Fail-soft: returns () if there are no captured slots."""
    slots = getattr(session, "captured_slots", None)
    if slots is None:
        return ()
    from app.services.scripts.spoken_email_normalizer import (
        natural_email_readback,
        natural_phone_readback,
    )
    out: list[str] = []
    email = getattr(slots, "email", None)
    if email:
        out.append(email)
        rb = natural_email_readback(email)
        if rb:
            out.append(rb)
    phone = getattr(slots, "phone", None)
    if phone:
        out.append(phone)
        rb = natural_phone_readback(phone)
        if rb:
            out.append(rb)
    return tuple(out)


def _history_context_budget(*, model: str | None, system_prompt: str,
                            tools: list, max_tokens: int) -> int:
    window = context_window_for(model)
    overhead = estimate_tokens(system_prompt + _HISTORY_OMISSION_NOTICE)
    overhead += estimate_tokens(json.dumps(tools, ensure_ascii=False)) + 1024
    continuation = min(20_000, window // 3) if tools else 0
    return window - overhead - max_tokens - continuation


def _history_for_context(history: list, *, model: str | None, system_prompt: str,
                         tools: list, max_tokens: int) -> tuple[list, int]:
    """Keep verbatim history while it fits; omit whole oldest exchanges only.

    Use the existing model registry and approximate token estimator. Reserve
    space for output, protocol/reasoning overhead and bounded tool continuations.
    This is a context estimate, not an exact provider tokenizer. Never shorten
    the current caller's words or mutate the canonical stored conversation.
    """
    budget = max(0, _history_context_budget(model=model, system_prompt=system_prompt,
                                          tools=tools, max_tokens=max_tokens))
    costs = [estimate_tokens(message.content) + 8 for message in history]
    remaining = sum(costs)
    if remaining <= budget or not history:
        return list(history), 0

    starts = []
    for index, message in enumerate(history):
        if message.role == MessageRole.USER:
            # A runtime result immediately before the caller belongs to this
            # exchange (for example, an opt-out persistence acknowledgement).
            start = index
            while start > 0 and history[start - 1].role == MessageRole.SYSTEM:
                start -= 1
            starts.append(start)
    start = 0
    for boundary in starts:
        if remaining <= budget:
            break
        remaining -= sum(costs[start:boundary])
        start = boundary
    # The newest complete exchange stays intact even if one huge utterance
    # exceeds the estimate. The request preflight asks for a narrower question
    # without dispatching or corrupting the canonical quote/contact.
    return list(history[start:]), start


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
        # What the previous turn read stays visible for a follow-up.
        remember_recent_knowledge(session, getattr(session, "_knowledge_evidence", None))
        # Track the current tool result separately from persistent source history.
        session._knowledge_grounding = []
        session._knowledge_evidence = {"status": "unavailable", "passages": []}
        barge_in_event = self._p._barge_in_events.get(call_id)
        guardrails = get_guardrails()

        messages = list(session.conversation_history)
        contact_turn = getattr(session, "_contact_turn", None)
        last_user_text = next(
            (m.content for m in reversed(messages) if m.role == MessageRole.USER),
            "",
        )
        # Offer connected capabilities; the model decides which request needs one.
        from app.domain.services.voice_pipeline.action_execution import (
            prepare_voice_action_context, enabled_voice_actions,
        )
        potential_actions = action_tools_for_turn(messages, self._p.llm_provider)
        if potential_actions:
            await prepare_voice_action_context(session)
        action_tools = action_tools_for_turn(messages, self._p.llm_provider, session=session)
        legacy_end_action = bool(
            self._p._supports_llm_end_session_action(session) and not action_tools
        )
        # Resolve available tools and runtime facts for the single prompt assembler.

        # The demo's small fact sheet is always available; the model decides relevance.
        ask_ai_block = _ASK_AI_PRODUCT_INFO if session.campaign_id == "ask-ai" else None

        # The conversational model selects sections from its scoped catalog.
        # No literal search, lexical threshold, or recovery model runs before it.
        kb_tools = knowledge_tools_for(session, self._p.llm_provider)
        knowledge_block = knowledge_system_addendum(session) if kb_tools else (
            "Company knowledge cannot be read in this session. Use only supplied "
            "campaign facts and be clear about details you cannot confirm."
        )
        contact_tools = [CONTACT_TOOL_SPEC] if (
            contact_turn is not None and getattr(self._p.llm_provider, "supports_tools", False)
        ) else []
        if kb_tools or contact_tools:
            legacy_end_action = False

        end_session_block = action_tool_system_addendum(enabled_voice_actions(session))
        if legacy_end_action:
            end_session_block += "\n" + _END_SESSION_TOOL_INSTRUCTIONS

        # Emotional audio tags — driven by the capability registry (single
        # source of truth). Only voices that actually PERFORM bracket tags
        # ([laughs]/[sighs]/[pause]) get told they may use them; every other
        # voice both (a) isn't instructed to use them and (b) has any stray tag
        # physically stripped in clean_response below. So tags can never leak as
        # spoken words on a non-supporting engine.
        _tts_model_id = expressive_caps.model_id_of(self._p)
        _expr_profile = expressive_caps.expressive_profile(_tts_model_id)

        # Core-value read-back protection (issue #3): the output leak-scrubber must
        # never delete a sentence that reads the caller's own email/number back to
        # them (e.g. "claude.smith@…" trips a vendor pattern). Pass the pending /
        # captured values + their spoken read-back forms so those sentences are
        # exempted from scrubbing and the confirmation reaches the caller.
        _protected_readback = _readback_protected_values(session)

        # LIVE STATE: a per-turn fact re-anchoring identity + the already-
        # introduced flag (set in turn_runner after the first real reply). This
        # is what stops weaker models re-introducing / drifting their title over
        # a long call. Identity comes off the session's agent_config.
        _agent_cfg = getattr(session, "agent_config", None)

        _structured = reduce_cascaded_session_live_state(session)
        # Callee-local time-of-day so "morning/afternoon/evening" matches the
        # hour where the phone rang (was always "Morning"). Timezone comes from
        # the campaign (calling_config.timezone), stashed on the session; UK
        # campaigns default to Europe/London when unset.
        _tz_name = (
            getattr(session, "_campaign_timezone", None)
            or getattr(_agent_cfg, "timezone", None)
            or "Europe/London"
        )
        try:
            from app.domain.services.voice_pipeline.time_of_day import (
                time_of_day_line as _tod_line,
            )
            _tod = _tod_line(_tz_name)
        except Exception:
            _tod = ""
        live_state_block = build_live_state_block(
            agent_name=(getattr(_agent_cfg, "agent_name", "") or ""),
            company_name=(getattr(_agent_cfg, "company_name", "") or ""),
            has_introduced=bool(getattr(session, "_has_introduced", False)),
            opening_interrupted=bool(getattr(session, "_greeting_bargein_count", 0)),
            direction=getattr(session, "_call_direction", "outbound"),
            time_of_day_line=_tod,
            structured_state_block=render_live_state_block(_structured),
        )

        # Single assembler (prompts folder) owns the block ORDER + the
        # CAPTURED-facts prepend. turn_streamer only feeds it resolved blocks.
        system_prompt = build_turn_prompt(
            session.system_prompt,
            live_state_block=live_state_block,
            ask_ai_block=ask_ai_block,
            knowledge_block=knowledge_block,
            end_session_block=end_session_block,
            audio_tags_block=(
                ELEVEN_V3_AUDIO_TAGS_INSTRUCTIONS if _expr_profile.name == "eleven_v3"
                else CARTESIA_LAUGHTER_INSTRUCTIONS if _expr_profile.name == "cartesia"
                else None
            ),
            captured_slots=session.captured_slots,
            has_callback_executor="schedule_callback" in enabled_voice_actions(session),
        )

        offered_tools = [*(kb_tools or []), *contact_tools, *action_tools]
        provider_model = getattr(self._p.llm_provider, "_model", None)
        model = provider_model if isinstance(provider_model, str) else getattr(session, "llm_model", None)
        output_tokens = getattr(session, "llm_max_tokens", None)
        if type(output_tokens) is not int or output_tokens < 1:
            output_tokens = getattr(self._p.llm_provider, "_max_tokens", 400)
        if type(output_tokens) is not int or output_tokens < 1:
            output_tokens = 400
        llm_messages, omitted = _history_for_context(
            messages, model=model, system_prompt=system_prompt,
            tools=offered_tools, max_tokens=output_tokens,
        )
        if omitted:
            system_prompt += "\n\n" + _HISTORY_OMISSION_NOTICE
            logger.info("voice_history_context_limited call_id=%s omitted_messages=%d retained_messages=%d",
                        call_id, omitted, len(llm_messages))
        remaining_budget = _history_context_budget(model=model, system_prompt=system_prompt,
                                                    tools=offered_tools, max_tokens=output_tokens)
        context_failure = "setup_over_budget" if remaining_budget <= 0 else (
            "current_exchange_over_budget" if sum(estimate_tokens(m.content) + 8 for m in llm_messages)
            > remaining_budget else None
        )
        # Diagnostic only: no contact, caller text, or prompt content is logged.
        session._context_failure = context_failure

        from app.domain.services.voice_pipeline.profile import turn_profile
        logger.info("voice_turn_profile %s", json.dumps(
            turn_profile(session, system_prompt, None if kb_tools else knowledge_block), sort_keys=True,
        ))

        all_tokens: list[str] = []
        buf = ""
        first_token = True
        first_sentence = True
        sentences_done = 0
        tts_was_interrupted = False
        suppressed_for_action = False
        # Source hosts help sentence segmentation keep URLs intact.
        turn_grounding: list[str] = []

        def _figure_sources():
            # Everything the model was given or heard this turn (HAL-2).
            evidence = getattr(session, "_knowledge_evidence", None) or {}
            return (
                system_prompt, evidence.get("text"),
                *getattr(session, "_knowledge_grounding", []), *turn_grounding,
                *(m.content for m in session.conversation_history),
                *(json.dumps(r, default=str) for r in action_results_for_session(session).values()),
            )
        # Canonical history source: TTS submissions that returned without
        # interruption. Raw generation can include unsent text after interruption or a provider error. Submission is not a heard/playback receipt;
        # action confirmation separately requires correlated playout below.
        session._spoken_sentences = []
        # Whether this reply's audio has started: a cancellation before its
        # first sentence finishes still leaves a marker (turn_runner).
        session._reply_audio_started = False
        _action_delivered_sentences = []

        def _record_action_playback(sentence, interrupted):
            if interrupted or not getattr(session, "_tts_playout_completed", False):
                _action_delivered_sentences.clear()
            else:
                _action_delivered_sentences.append(sentence)
            session._voice_action_delivered_text = " ".join(_action_delivered_sentences)
        # 12b (round 2, review of 91b61694, 2026-09-24): reset each turn.
        # tts_playback.py's TtsDeliveryError clause sets this when a mid-turn
        # delivery failure (e.g. "no gateway session" after the caller hung
        # up -- call 6aaeb4dd, 13:10:55.31) ends the turn early with no real
        # barge-in event. It tells the full_text substitution below that
        # `tts_was_interrupted` came from a dead channel, not the caller
        # going silent, so it must not carry over from a previous turn.
        session._tts_delivery_failed = False
        session._tts_failure_reason = None
        # P1: this turn's epoch. A barge-in event that targeted an OLDER turn
        # (stale signal from a previous interruption) must not kill this fresh
        # reply. _barged() below ignores such stale events.
        _my_epoch = getattr(session, "_current_turn_epoch", 0)

        def _barged() -> bool:
            if not (barge_in_event and barge_in_event.is_set()):
                return False
            tgt = self._p._barge_in_epoch.get(call_id)
            # Suppress ONLY a barge-in that demonstrably targeted an older turn;
            # otherwise honor it (fail open so the caller can always interrupt).
            if tgt is not None and _my_epoch and tgt < _my_epoch:
                return False
            return True

        t_llm_start = time.monotonic()
        t_tts_first: Optional[float] = None
        t_tts_end: Optional[float] = None

        # One conversational model owns wording and chooses its available tools.
        if context_failure:
            logger.warning("voice_context_unavailable call_id=%s reason=%s", call_id, context_failure)
            async def _context_recovery():
                yield ("I'm sorry, this conversation is temporarily unavailable. Please try again later."
                       if context_failure == "setup_over_budget" else
                       "That's more than I can process at once. Could you ask one specific question in a shorter message?")
            _token_iter = _context_recovery()
        elif offered_tools:
            async def _voice_tool_runner(_name: str, _args: dict) -> str:
                if _name == KB_TOOL_NAME:
                    result = await run_knowledge_lookup(session, _args)
                    evidence = getattr(session, "_knowledge_evidence", None) or {}
                    status = evidence.get("status", "unavailable")
                    available = status == "available"
                    turn_grounding[:] = getattr(session, "_knowledge_grounding", []) if available else []
                    current = getattr(session, "_live_structured_state", _structured)
                    session._live_structured_state = reduce_live_state(
                        current,
                        ToolResultEvidence(
                            tool_name="knowledge_lookup",
                            success=status in {"available", "catalog"},
                            code=status,
                        ),
                    )
                    return result
                if _name == CONTACT_TOOL_NAME:
                    return result_json(await record_contact(session, _args, turn=contact_turn))
                try:
                    result = await run_voice_action(
                        session,
                        _name,
                        _args,
                        user_text=last_user_text,
                    )
                except Exception:
                    logger.exception(
                        "voice_action_executor_failed call=%s action=%s",
                        call_id[:12],
                        _name,
                    )
                    result = execution_failure_result(session, _name)
                current = getattr(session, "_live_structured_state", _structured)
                session._live_structured_state = reduce_live_state(
                    current,
                    ToolResultEvidence(
                        tool_name=str(result.get("action") or _name),
                        success=bool(result.get("success")),
                        code=str(result.get("status") or "execution_error"),
                    ),
                )
                return result_json(result)

            _token_iter = self._p.llm_provider.stream_chat_with_tools(
                llm_messages,
                system_prompt=system_prompt,
                tools=offered_tools,
                max_tool_rounds=3,
                read_only_tools={KB_TOOL_NAME},
                navigation_round_allowed=knowledge_navigation_continuation(session),
                tool_runner=_voice_tool_runner,
                require_tool_result_before_content=False,
                temperature=getattr(session, "llm_temperature", None),
                max_tokens=getattr(session, "llm_max_tokens", None),
                # Prompt-cache routing hint (Cerebras prompt_cache_key). The
                # campaign, not the call, is the right key: every call in a
                # campaign shares the same static prefix. Previously only the
                # llm_response.py path passed it (2026-09-06 audit, F10).
                campaign_id=getattr(session, "campaign_id", None),
            )
        else:
            _token_iter = self._p.llm_provider.stream_chat_with_timeout(
                llm_messages,
                system_prompt=system_prompt,
                # Honor the tenant's AI-Options settings per turn. None falls
                # back to the provider's configured default inside stream_chat.
                temperature=getattr(session, "llm_temperature", None),
                max_tokens=getattr(session, "llm_max_tokens", None),
                campaign_id=getattr(session, "campaign_id", None),
            )

        try:
            async for token in _token_iter:
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

                if _barged():
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
                if self._p._supports_llm_end_session_action(session):
                    _lead = buf.lstrip()
                    if _lead[:1] == "{":
                        suppressed_for_action = True
                        continue
                    # The contract says "no spoken text outside JSON", but small
                    # models routinely emit a sentence and THEN the envelope.
                    # Checking only the first character missed that entirely, so
                    # the envelope streamed to TTS and was read aloud. Speak the
                    # prose, swallow everything from the brace on.
                    _brace = _find_action_envelope_start(buf)
                    if _brace > 0:
                        buf = buf[:_brace]
                        suppressed_for_action = True

                # Flush each complete sentence (or, for long buffers, the first
                # clause) to TTS as tokens arrive.
                while True:
                    grounded_hosts = grounded_url_hosts([
                        *getattr(session, "_knowledge_grounding", []), *turn_grounding,
                    ]) if "." in buf else ()
                    idx = self._p._find_sentence_end(
                        buf, allow_clause=len(buf) >= 80, known_hosts=grounded_hosts,
                    )
                    if idx < 0:
                        break
                    # A final dot may be the middle of a streamed domain or
                    # address. Wait for one token of lookahead (or normal
                    # stream completion) before link validation and playback.
                    if idx + 1 == len(buf) and buf[idx] == ".":
                        break

                    raw_sentence = buf[:idx + 1].strip()
                    # Skip the separator only when there IS one: at a
                    # missing-space boundary, idx + 2 swallows the first letter
                    # of whatever follows.
                    _skip = 2 if (idx + 1 < len(buf) and buf[idx + 1].isspace()) else 1
                    buf = buf[idx + _skip:] if idx + _skip <= len(buf) else ""

                    # Extract-first (root cause, not a regex patch): pull the
                    # END_CALL sentinel out of the RAW model text before
                    # clean_response's audio-tag stripper ever sees it — that
                    # stripper treats "[[END_CALL]]" as a bracket tag and
                    # erases it, which used to leave the flag unset and the
                    # call never hanging up. See end_call.py module docstring.
                    raw_sentence = strip_and_flag(session, raw_sentence)

                    sentence = guardrails.clean_response(
                        raw_sentence, tts_model_id=_tts_model_id,
                        protected_values=_protected_readback,
                    )
                    # Drop only what cannot be SPOKEN — punctuation or
                    # whitespace left over from cleaning. The test used to be
                    # `len(sentence) < 6`, which also silently deleted every
                    # short real reply: "Yes.", "Okay.", "Sure.", "Got it."
                    # are all under six characters. The caller heard nothing at
                    # all on those turns.
                    #
                    # That got sharply worse on 2026-08-13, from two directions
                    # at once. A guardrail bug was cleaning "Sure thing." down
                    # to a bare "." — which this line then swallowed, so the
                    # agent went silent mid-conversation. And the answer-first
                    # rule added the same week explicitly asks the model to
                    # reply plainly in one short sentence, which is exactly the
                    # shape this discarded.
                    #
                    # A length threshold was always the wrong instrument: the
                    # question is whether there is anything to say, not how
                    # many characters it takes to say it.
                    if not sentence or not any(c.isalnum() for c in sentence):
                        continue
                    # Unbacked "done" claims and denied-relationship claims
                    # never reach the caller (speech_guard.py).
                    sentence = guard_spoken_sentence(session, sentence)
                    if sentence:
                        sentence = guard_figures(session, sentence, _figure_sources)
                    if not sentence:
                        continue

                    if _barged():
                        tts_was_interrupted = True
                        break

                    if t_tts_first is None:
                        t_tts_first = time.monotonic()
                        self._p.latency_tracker.mark_tts_start(call_id)

                    session.tts_active = True
                    session._reply_audio_started = True
                    session._voice_action_delivered_text = ""
                    tts_was_interrupted = await self._p.synthesize_and_send_audio(
                        session, sentence, websocket, track_latency=first_sentence,
                    )
                    _record_action_playback(sentence, tts_was_interrupted)
                    first_sentence = False
                    t_tts_end = time.monotonic()
                    # Count completed sentences for delivery diagnostics.
                    if self._p._find_sentence_end(
                        sentence, allow_clause=False, known_hosts=grounded_hosts,
                    ) >= 0:
                        sentences_done += 1
                    if not tts_was_interrupted:
                        session._spoken_sentences.append(sentence)

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
                # Retain only submitted, non-interrupted sentences. The raw
                # tail was never submitted; it must not enter history or arm
                # a control token in the aggregate pass below. This does not
                # upgrade submission into a heard/playback receipt.
                all_tokens[:] = [" ".join(session._spoken_sentences)]
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
                all_tokens[:] = [" ".join(session._spoken_sentences)]
            else:
                buf = "I'm sorry, I had trouble processing that. Could you say it again?"
                all_tokens.clear()
                all_tokens.append(buf)
        finally:
            # Release the provider stream now. A barge-in breaks the loop above,
            # and an abandoned async generator keeps its HTTP stream and its
            # provider concurrency slot until garbage collection (stability
            # audit 2026-10-02). Closing a finished stream is a no-op.
            _aclose = getattr(_token_iter, "aclose", None)
            if _aclose is not None:
                try:
                    await _aclose()
                except Exception as _close_exc:  # noqa: BLE001
                    logger.debug("llm stream close failed call=%s: %s", call_id[:12], _close_exc)

        t_llm_done = time.monotonic()
        self._p.latency_tracker.mark_llm_end(call_id)

        raw_response_text = "".join(all_tokens)
        # Extract-first on the full aggregate too: all_tokens (unlike buf) was
        # never touched by the per-sentence extraction above, so without this
        # the sentinel would still be sitting in raw_response_text and
        # clean_response would mangle it into a stray "[]" in full_text —
        # which becomes the stored transcript below. Idempotent with the
        # per-sentence/tail calls (harmless no-op where they already caught
        # it); this is the one place that protects the aggregate/history copy.
        raw_response_text = strip_and_flag(session, raw_response_text)
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
            if not _barged():
                # Same extract-first ordering as the per-sentence loop:
                # this trailing tail is the MOST common place the sentinel
                # actually lands (the model's closing line ends in
                # punctuation, which flushes as a full sentence above, and
                # " [[END_CALL]]" is left over as the unterminated tail).
                raw_tail = strip_and_flag(session, buf.strip())
                sentence = guardrails.clean_response(
                    raw_tail, tts_model_id=_tts_model_id,
                    protected_values=_protected_readback,
                )
                sentence = guard_spoken_sentence(session, sentence)
                if sentence:
                    sentence = guard_figures(session, sentence, _figure_sources)
                if sentence:
                    if t_tts_first is None:
                        t_tts_first = time.monotonic()
                        self._p.latency_tracker.mark_tts_start(call_id)
                    session.tts_active = True
                    session._reply_audio_started = True
                    session._voice_action_delivered_text = ""
                    tts_was_interrupted = await self._p.synthesize_and_send_audio(
                        session, sentence, websocket, track_latency=first_sentence,
                    )
                    _record_action_playback(sentence, tts_was_interrupted)
                    first_sentence = False
                    t_tts_end = time.monotonic()
                    if not tts_was_interrupted:
                        session._spoken_sentences.append(sentence)
        # Anti-silence safety net: the LLM stream completed WITHOUT error but
        # produced no spoken content at all (e.g. a reasoning model burned its
        # whole token budget on internal thinking, or an empty completion).
        # That is NOT an error path, so nothing above caught it — without this
        # the caller just hears dead air. Speak a short recovery line instead.
        from app.domain.services.end_session_action import caller_signaled_end, previous_assistant_turn
        caller_finished = caller_signaled_end(last_user_text,
            previous_assistant_text=previous_assistant_turn(messages))
        if (
            not tts_was_interrupted
            and sentences_done == 0
            and t_tts_first is None
            and not ask_ai_end_action
            and (not suppressed_for_action or caller_finished)
            and not getattr(session, "_tts_delivery_failed", False)
            and not _barged()
        ):
            recovery = "Goodbye." if caller_finished else "Sorry, I didn't quite catch that — could you say it again?"
            if caller_finished:
                session._end_call_requested = True
            logger.warning(
                "zero_token_turn call=%s — LLM produced no speech; spoke recovery line",
                call_id,
            )
            session.tts_active = True
            session._reply_audio_started = True
            t_tts_first = time.monotonic()
            self._p.latency_tracker.mark_tts_start(call_id)
            session._voice_action_delivered_text = ""
            tts_was_interrupted = await self._p.synthesize_and_send_audio(
                session, recovery, websocket, track_latency=False,
            )
            _record_action_playback(recovery, tts_was_interrupted)
            t_tts_end = time.monotonic()
            if not tts_was_interrupted:
                session._spoken_sentences.append(recovery)

        llm_latency_ms = (t_llm_done - t_llm_start) * 1000
        tts_latency_ms = (
            (t_tts_end - t_tts_first) * 1000
            if t_tts_first is not None and t_tts_end is not None
            else 0.0
        )

        # Build normal history from the same chunks used by the spoken path.
        # Recounting sentence punctuation here loses early comma/clause flushes
        # and can retain a tail that never reached playback.
        # Legacy action JSON is a control result consumed by the turn finisher.
        if ask_ai_end_action:
            full_text = raw_response_text.strip()
        else:
            full_text = " ".join(session._spoken_sentences).strip()

        # P3: if the caller actually BARGED IN, the history entry must be ONLY
        # completed submissions + an interruption marker — never
        # the full (longer) response, which is what made the model think it said
        # things it never spoke. Gated on _barged() so the LLM-error fallback
        # path (synthesize failure, no real barge-in) still commits normally.
        # The marker (even with no spoken text) preserves user→assistant
        # alternation. The cancellation path is handled in turn_runner.
        if tts_was_interrupted and _barged():
            spoken = " ".join(session._spoken_sentences).strip()
            full_text = (spoken + " [interrupted by caller]") if spoken else "[interrupted by caller]"
        elif tts_was_interrupted and getattr(session, "_tts_delivery_failed", False):
            # 12b (round 2, review of 91b61694, 2026-09-24): a TtsDeliveryError
            # also returns tts_was_interrupted=True, but with no real
            # caller barge-in `_barged()` stayed False above, so full_text
            # fell through to the LLM's raw output -- the undelivered
            # sentence ("Would you like us to call you tomorrow with the
            # appointment details?", call 6aaeb4dd turn 17) was committed to
            # history AND the persisted transcript as if it had been spoken.
            # Only what actually reached _spoken_sentences was delivered; an
            # empty string here means nothing was, and turn_runner.py's
            # `if response_text and response_text.strip():` gate already
            # treats an empty reply as nothing to commit.
            full_text = " ".join(session._spoken_sentences).strip()

        return full_text, llm_latency_ms, tts_latency_ms
