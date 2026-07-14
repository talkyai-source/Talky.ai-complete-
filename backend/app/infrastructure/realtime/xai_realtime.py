"""xAI Grok Voice realtime provider adapter (opt-in, config-gated).

Source of truth for the wire-protocol claims below: docs.x.ai (verified
2026-07-09) plus the user's own working playground snippet.

  * Endpoint: ``wss://api.x.ai/v1/realtime?model=grok-voice-think-fast-1.0``
    (or ``?agent_id=...`` to talk to a console-built agent instead of a bare
    model). Auth: ``Authorization: Bearer $XAI_API_KEY`` — same header shape
    OpenAI uses, so `OpenAIRealtimeSession.connect()`'s handshake code is
    reused completely unchanged.
  * Protocol: ~95% OpenAI-Realtime-compatible. session.update,
    input_audio_buffer.append, conversation.item.create, response.create,
    response.output_audio.delta, response.output_audio_transcript.delta,
    response.function_call_arguments.done, response.done, error, etc. are
    IDENTICAL wire shapes — this is why we subclass instead of writing a
    parallel client.
  * Audio: native audio/pcmu (G.711 μ-law) @ 8000 Hz — exactly our
    telephony wire format. Same zero-resample story as OpenAI Realtime; no
    changes needed to RealtimeBridge (app/domain/services/voice_pipeline/
    realtime_bridge.py), which only depends on this session's duck-typed
    interface (events()/send_caller_audio()/trigger_greeting()/
    send_function_result()/close()/closed()), never on OpenAI specifically.

Documented DIFFERENCES from OpenAI Realtime, and how each is handled here:

  1. Cumulative caller transcription. OpenAI streams
     ``conversation.item.input_audio_transcription.delta`` (incremental).
     xAI instead streams ``.updated`` carrying the FULL transcript-so-far for
     the item. We diff each `.updated` payload against the last cumulative
     text we saw for that item_id and emit only the NEW suffix as a
     ``caller_transcript`` RealtimeEvent — so everything downstream
     (RealtimeBridge, transcript accumulation) sees the exact same
     incremental-delta shape it already expects from OpenAI. `.completed`
     (the terminal, full-text, `is_final=True` event) is not documented as
     different, so it is handled by the inherited base-class code unchanged;
     we just clear our per-item cumulative cache on it.

  2. Turn detection. xAI supports ``server_vad`` (threshold 0.1-0.9, default
     0.85, plus ``silence_duration_ms`` / ``prefix_padding_ms``) — NOT
     OpenAI's ``semantic_vad``. We default to server_vad/0.85 instead of the
     base class's semantic_vad default, but an explicit
     ``realtime_settings["turn_detection"]`` override from the operator
     always passes straight through (inherited normalisation logic).

  3. ``output_audio_buffer.clear`` is NOT supported by xAI over WebSocket.
     We never send this event in the first place — neither does
     OpenAIRealtimeSession. Barge-in here (as with OpenAI) is handled purely
     client-side: bump the response epoch and flush the LOCAL outbound audio
     queue (`_on_interruption`, inherited unchanged) so the gateway simply
     stops playing out stale audio. No server-side "clear" call is needed or
     attempted for either provider.

  4. ``force_message``, ``resumption``, ``replace`` are xAI-only protocol
     extensions (force an immediate model utterance, resume a dropped
     session, replace a conversation item). They are NOT wired into the
     voice pipeline yet — Phase 1 of this adapter only needs the standard
     turn-taking flow the cascaded/realtime bridge already drives. Recognised
     here as a documented gap, not silently ignored.

Everything else (connect()/handshake retry, send_caller_audio, function-call
flow, barge-in epoch/queue-flush machinery, stats, teardown discipline) is
100% inherited from OpenAIRealtimeSession — see openai_realtime.py's module
docstring for the discipline that code follows (bounded queues, fail-soft,
exactly-one-background-task). We do not re-implement or duplicate any of it.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.infrastructure.realtime.openai_realtime import (
    OpenAIRealtimeSession,
    RealtimeEvent,
)

logger = logging.getLogger(__name__)

# ── Wire constants ──────────────────────────────────────────────────────────
_XAI_URL_TMPL_MODEL = "wss://api.x.ai/v1/realtime?model={model}"
_XAI_URL_TMPL_AGENT = "wss://api.x.ai/v1/realtime?agent_id={agent_id}"

XAI_DEFAULT_MODEL = "grok-voice-think-fast-1.0"

# xAI docs: server_vad threshold is 0.1-0.9, default 0.85 — noticeably more
# conservative than OpenAI's semantic_vad "medium" default. Tunable per-call
# via realtime_settings["turn_detection"] (any explicit override, of either
# shape, passes straight through — see OpenAIRealtimeSession._build_session_update).
_XAI_DEFAULT_TURN_DETECTION: Dict[str, Any] = {"type": "server_vad", "threshold": 0.85}


class XAIRealtimeSession(OpenAIRealtimeSession):
    """Single-call bridge to xAI Grok Voice (grok-voice-think-fast-1.0).

    Drop-in replacement for OpenAIRealtimeSession wherever the caller wants
    the xAI provider instead — same constructor shape (plus an optional
    ``agent_id``), same public API, same RealtimeEvent stream. See module
    docstring for the documented protocol differences and how each is
    handled.

    Lifecycle (identical to the base class):
        session = XAIRealtimeSession(api_key=..., instructions=..., tools=[...])
        await session.connect()
        await session.send_caller_audio(mulaw_20ms_frame)   # repeatedly
        async for ev in session.events():                    # consume output
            ...
        await session.close()
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str = XAI_DEFAULT_MODEL,
        agent_id: Optional[str] = None,
        voice: str = "",
        instructions: str = "",
        tools: Optional[List[Dict[str, Any]]] = None,
        settings: Optional[Dict[str, Any]] = None,
        call_id: Optional[str] = None,
    ) -> None:
        super().__init__(
            api_key=api_key,
            model=model or XAI_DEFAULT_MODEL,
            # Base class defaults a falsy voice to OpenAI's "marin", which is
            # not an xAI voice name. Pass through as-is here; we restore the
            # caller's exact (possibly empty) value right after and treat
            # "no voice chosen" as "omit the field" in _build_session_update
            # below, rather than silently sending an OpenAI voice ID to xAI.
            voice=voice or "unset",
            instructions=instructions,
            tools=tools,
            settings=settings,
            call_id=call_id,
        )
        self._voice = (voice or "").strip()
        # When set, connect() targets ?agent_id=... (a console-built xAI
        # agent) instead of ?model=... — the model still doubles as the
        # display/log label in that case.
        self._agent_id = (agent_id or "").strip() or None

        # Per-item cumulative caller-transcript cache: item_id -> last full
        # text we've seen, so `.updated` events can be diffed into the same
        # incremental-delta shape RealtimeBridge already expects from
        # OpenAI's `.delta`. Entries are cleared on `.completed`.
        self._caller_transcript_cumulative: Dict[str, str] = {}

    # ── URL (the one hard difference connect() needs) ───────────────────
    def _build_url(self) -> str:
        if self._agent_id:
            return _XAI_URL_TMPL_AGENT.format(agent_id=self._agent_id)
        return _XAI_URL_TMPL_MODEL.format(model=self._model)

    # ── Session config: xAI default turn-detection is server_vad ────────
    def _build_session_update(self, *, include_reasoning: bool = True) -> Dict[str, Any]:
        payload = super()._build_session_update(include_reasoning=include_reasoning)
        # Only substitute OUR default when the operator did NOT explicitly
        # set turn_detection — an explicit override (bare eagerness string,
        # server_vad dict, or otherwise) already passed straight through via
        # the inherited normalisation and must not be clobbered here.
        if self._settings.get("turn_detection") is None:
            payload["session"]["audio"]["input"]["turn_detection"] = dict(
                _XAI_DEFAULT_TURN_DETECTION
            )
        # No voice explicitly chosen: drop the OpenAI-defaulted "voice" key
        # entirely rather than sending an OpenAI voice ID ("marin") to xAI —
        # let the model/agent's own default voice speak.
        if not self._voice:
            payload["session"]["audio"]["output"].pop("voice", None)
        return payload

    # ── Event mapping: intercept the cumulative-transcript difference ───
    async def _handle_server_event(self, data: Dict[str, Any]) -> None:
        etype = data.get("type", "")

        if etype == "conversation.item.input_audio_transcription.updated":
            self._handle_cumulative_caller_transcript(data)
            return

        if etype == "conversation.item.input_audio_transcription.completed":
            item_id = data.get("item_id") or data.get("id") or ""
            self._caller_transcript_cumulative.pop(item_id, None)
            await super()._handle_server_event(data)
            return

        # Every other event type — audio deltas, agent transcript, barge-in
        # (speech_started/speech_stopped), function calls, response.done,
        # errors — is the same wire shape as OpenAI Realtime, so delegate to
        # the shared, already-hardened implementation unchanged.
        await super()._handle_server_event(data)

    def _handle_cumulative_caller_transcript(self, data: Dict[str, Any]) -> None:
        """xAI's `.updated` event carries the FULL transcript-so-far for the
        item (not an incremental delta like OpenAI's `.delta`). Diff against
        the last cumulative text we saw for this item_id and emit only the
        NEW suffix as a `caller_transcript` event.

        Fail-soft: if the new text doesn't extend the previous one (e.g. the
        model revised earlier words instead of only appending), emit the
        full new text rather than raising — a rare correction rendered twice
        beats a crashed transcript pump.
        """
        item_id = data.get("item_id") or data.get("id") or ""
        full_text = data.get("transcript")
        if full_text is None:
            return
        prev = self._caller_transcript_cumulative.get(item_id, "")
        delta = full_text[len(prev):] if full_text.startswith(prev) else full_text
        self._caller_transcript_cumulative[item_id] = full_text
        if delta:
            self._offer_event(RealtimeEvent(kind="caller_transcript", text=delta))
