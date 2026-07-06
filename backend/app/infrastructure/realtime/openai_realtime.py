"""OpenAI gpt-realtime-2 speech-to-speech WebSocket bridge (Realtime mode).

ONE OpenAI Realtime session collapses the classic three-stage cascaded
pipeline (STT → LLM → TTS) into a single duplex WebSocket: caller μ-law
audio in, model μ-law audio out, the model's own voice, server-side
semantic VAD (turn-taking) and function calling.

This module is INTENTIONALLY separate from the cascaded pipeline. It does
NOT import or invoke compose_prompt / compliance_floor / prompt_builder or
the ElevenLabs/Cartesia audio-tag blocks. The `instructions` string it is
handed is built by `app.services.scripts.realtime_instructions` — a clean,
voice-appropriate composer written for a speech-to-speech model.

Audio format
------------
Both directions use μ-law 8 kHz — exactly the telephony gateway's native
format ({"type": "audio/pcmu"}). NO resampling anywhere. μ-law is
1 byte/sample @ 8 kHz = 160 bytes per 20 ms frame. Over the wire the
Realtime API carries audio as base64 of the raw μ-law bytes.

Discipline (we just fixed a class of leaks in the gateway — do not regress)
--------------------------------------------------------------------------
* Exactly ONE background task (the receive loop). It is cancelled on close().
* The outbound audio queue is BOUNDED; on overflow we drop the OLDEST frame
  (stale audio is worthless on a live call) rather than grow without bound.
* Every await is guarded; teardown is wrapped so cleanup can never raise.
* Fail-soft: any connection/stream error logs and ends the session cleanly.
  A realtime failure must NEVER crash a call.

Barge-in / interruption
-----------------------
semantic_vad means the SERVER owns turn detection. When the caller starts
talking mid-response the server emits `input_audio_buffer.speech_started`
and (once it truncates the model turn) `response.done` with a cancelled/
interrupted status. On EITHER signal we bump a monotonically increasing
`_response_epoch` and FLUSH the outbound audio queue, so any model audio
deltas still buffered from the now-abandoned turn are dropped and the agent
stops talking over the caller. Deltas that arrive tagged to a stale epoch
are ignored.

Reconnect policy (Phase 1)
--------------------------
One connection per call. On an unexpected drop we end the session cleanly
(the receive loop exits, `closed()` becomes true, the gateway ends the
call). A future Phase 1b reconnect would live in `_recv_loop`'s
ConnectionClosed handler: re-run connect() and replay the last session.update
before resuming — see the marked TODO there.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

import websockets

logger = logging.getLogger(__name__)

# ── Wire constants ──────────────────────────────────────────────────────────
_REALTIME_URL_TMPL = "wss://api.openai.com/v1/realtime?model={model}"

# Bound the outbound (model→gateway) audio queue. At 20 ms/frame, 200 frames
# is ~4 s of speech buffered ahead of the gateway's playout — generous head
# room while still bounded. On overflow we evict the OLDEST frame.
_OUTBOUND_AUDIO_MAXLEN = 200

# How long connect() waits for session.created / session.updated before it
# gives up and fails soft.
_CONNECT_HANDSHAKE_TIMEOUT_S = 15.0

# Default turn-detection / noise-reduction / transcription, overridable via
# the `settings` dict passed to __init__ (from AIProviderConfig.realtime_settings).
_DEFAULT_TURN_DETECTION = {"type": "semantic_vad", "eagerness": "high"}
_DEFAULT_NOISE_REDUCTION = {"type": "far_field"}
_DEFAULT_TRANSCRIPTION_MODEL = "gpt-realtime-whisper"


@dataclass
class RealtimeFunctionCall:
    """A tool/function-call request surfaced by the model.

    Feed `output` back with `session.send_function_result(call_id, output)`.
    """
    call_id: str
    name: str
    arguments: str  # raw JSON string as emitted by the model

    def parsed_arguments(self) -> Dict[str, Any]:
        try:
            return json.loads(self.arguments) if self.arguments else {}
        except (ValueError, TypeError):
            logger.warning("realtime: could not parse function args for %s", self.name)
            return {}


@dataclass
class RealtimeEvent:
    """A normalised event yielded by `OpenAIRealtimeSession.events()`.

    Exactly one payload field is populated per event, keyed by `kind`:
      "audio"              -> audio: μ-law bytes to send back to the caller
      "agent_transcript"   -> text:  incremental words the agent is speaking
      "caller_transcript"  -> text:  incremental caller transcription
      "function_call"      -> function_call: a RealtimeFunctionCall to fulfil
      "response_done"      -> (no payload) the model finished a response turn
      "interrupted"        -> (no payload) caller barged in; audio was flushed
      "error"              -> text:  human-readable error string
    """
    kind: str
    text: Optional[str] = None
    audio: Optional[bytes] = None
    function_call: Optional[RealtimeFunctionCall] = None
    raw: Optional[Dict[str, Any]] = None
    # True on the terminal transcript event for a turn (agent transcript "done"
    # / caller transcription "completed"), carrying the FULL turn text. The
    # bridge persists only these finals so incremental deltas never double-count.
    is_final: bool = False


@dataclass
class _RealtimeStats:
    audio_frames_in: int = 0
    audio_frames_out: int = 0
    audio_frames_dropped_stale: int = 0
    audio_frames_dropped_overflow: int = 0
    function_calls: int = 0
    errors: int = 0

    def to_dict(self) -> dict:
        return {
            "realtime_audio_frames_in": self.audio_frames_in,
            "realtime_audio_frames_out": self.audio_frames_out,
            "realtime_frames_dropped_stale": self.audio_frames_dropped_stale,
            "realtime_frames_dropped_overflow": self.audio_frames_dropped_overflow,
            "realtime_function_calls": self.function_calls,
            "realtime_errors": self.errors,
        }


class OpenAIRealtimeSession:
    """Single-call bridge to OpenAI gpt-realtime-2.

    Lifecycle:
        session = OpenAIRealtimeSession(api_key=..., voice="marin",
                                        instructions=..., tools=[...])
        await session.connect()
        await session.send_caller_audio(mulaw_20ms_frame)   # repeatedly
        async for ev in session.events():                    # consume output
            ...
        await session.close()

    All public coroutines are fail-soft: on a dead/closed socket they log and
    return instead of raising, so a realtime hiccup can't crash the call.
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "gpt-realtime-2",
        voice: str = "marin",
        instructions: str = "",
        tools: Optional[List[Dict[str, Any]]] = None,
        settings: Optional[Dict[str, Any]] = None,
        call_id: Optional[str] = None,
    ) -> None:
        if not api_key:
            raise ValueError("OpenAIRealtimeSession requires an api_key")
        self._api_key = api_key
        self._model = model or "gpt-realtime-2"
        self._voice = voice or "marin"
        self._instructions = instructions or ""
        self._tools = list(tools or [])
        self._settings = dict(settings or {})
        self._call_id = call_id or "realtime"

        self._ws: Optional[Any] = None
        self._recv_task: Optional[asyncio.Task] = None
        self._closed = asyncio.Event()

        # Bounded model→gateway audio + normalised-event stream. maxsize is a
        # hard cap; the receive loop evicts the oldest frame on overflow.
        self._event_queue: asyncio.Queue = asyncio.Queue(maxsize=_OUTBOUND_AUDIO_MAXLEN)

        # Barge-in epoch. Bumped on every speech_started / interruption; audio
        # deltas from a superseded response are discarded.
        self._response_epoch = 0
        self._active_response_epoch = 0

        # Active-response tracking for the function-call→continue flow. The
        # Realtime API rejects a `response.create` while a response is already
        # active ("conversation already has an active response"), so we only
        # create when idle, and otherwise defer until the current response ends.
        self._response_active = False
        self._pending_response_create = False

        # Per-turn latency instrumentation. T0 = the caller stopped talking
        # (server VAD `speech_stopped`); we log the delta to the FIRST model
        # audio delta of the turn (T1). This is the same "caller-stopped →
        # first agent audio" metric the cascaded latency_tracker reports as
        # total_latency_ms, so realtime vs cascaded is an apples-to-apples
        # compare from the logs. None between turns / for the agent-first
        # greeting (which has no preceding caller speech).
        self._t_speech_stopped: Optional[float] = None

        self.stats = _RealtimeStats()

    # ── Properties ───────────────────────────────────────────────────────
    @property
    def call_id(self) -> str:
        return self._call_id

    def closed(self) -> bool:
        return self._closed.is_set()

    # ── Connect / handshake ──────────────────────────────────────────────
    async def connect(self) -> bool:
        """Open the WS, wait for session.created, send session.update, wait
        for session.updated. Returns True on success, False on fail-soft.

        Never raises — on any failure it logs, tears down, and returns False.
        """
        url = _REALTIME_URL_TMPL.format(model=self._model)
        # websockets==13.1 default connect() is the LEGACY client → extra_headers.
        # (Matches the Deepgram STT idiom in app/infrastructure/stt/deepgram_flux.py.)
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "User-Agent": "TalkyAI-VoiceAgent/1.0",
        }
        try:
            self._ws = await asyncio.wait_for(
                websockets.connect(url, extra_headers=headers, max_size=None),
                timeout=_CONNECT_HANDSHAKE_TIMEOUT_S,
            )
        except Exception as exc:  # noqa: BLE001 — fail-soft on any connect error
            logger.error("realtime connect failed call=%s err=%s", self._call_id, exc)
            self._closed.set()
            return False

        try:
            # 1. Server greets with session.created.
            created = await asyncio.wait_for(
                self._recv_json(), timeout=_CONNECT_HANDSHAKE_TIMEOUT_S
            )
            if not created or created.get("type") != "session.created":
                logger.error(
                    "realtime: expected session.created, got %s call=%s",
                    (created or {}).get("type"), self._call_id,
                )
                await self._teardown()
                return False

            # 2. We send our session.update.
            await self._ws.send(json.dumps(self._build_session_update()))

            # 3. Server confirms with session.updated. (Ignore any interleaved
            #    non-terminal events while we wait for it.)
            updated = None
            for _ in range(10):
                msg = await asyncio.wait_for(
                    self._recv_json(), timeout=_CONNECT_HANDSHAKE_TIMEOUT_S
                )
                if not msg:
                    break
                if msg.get("type") == "session.updated":
                    updated = msg
                    break
                if msg.get("type") == "error":
                    logger.error("realtime handshake error call=%s: %s",
                                 self._call_id, msg.get("error"))
                    await self._teardown()
                    return False
            if updated is None:
                logger.error("realtime: never received session.updated call=%s",
                             self._call_id)
                await self._teardown()
                return False
        except Exception as exc:  # noqa: BLE001
            logger.error("realtime handshake failed call=%s err=%s",
                         self._call_id, exc)
            await self._teardown()
            return False

        # Handshake done — start the single receive task.
        self._recv_task = asyncio.create_task(
            self._recv_loop(), name=f"realtime-recv-{self._call_id}"
        )
        logger.info(
            "realtime session ready call=%s model=%s voice=%s tools=%d",
            self._call_id, self._model, self._voice, len(self._tools),
        )
        return True

    def _build_session_update(self) -> Dict[str, Any]:
        """The session.update payload. audio/pcmu in+out (no resampling),
        our voice, our clean instructions, semantic VAD, noise reduction,
        and the tool list."""
        # Accept both the full API objects and the simple values the AI-Options
        # frontend sends (eagerness "low|medium|high"; noise "near_field|
        # far_field|none"). Normalise to the wire shape.
        td = self._settings.get("turn_detection")
        if isinstance(td, str):
            turn_detection = {"type": "semantic_vad", "eagerness": td}
        elif isinstance(td, dict):
            turn_detection = {"type": "semantic_vad", **td}
        else:
            turn_detection = _DEFAULT_TURN_DETECTION

        nr = self._settings.get("noise_reduction")
        if isinstance(nr, str):
            # "none" disables noise reduction (omit the block).
            noise_reduction = None if nr == "none" else {"type": nr}
        elif isinstance(nr, dict):
            noise_reduction = nr
        else:
            noise_reduction = _DEFAULT_NOISE_REDUCTION

        transcription_model = (
            self._settings.get("transcription_model") or _DEFAULT_TRANSCRIPTION_MODEL
        )

        # Optional voice speed (audio.output.speed). Only sent when the operator
        # set it — an unset value keeps today's exact payload so live calls are
        # unaffected. Clamped to the API's documented 0.25–1.5 window.
        output_block: Dict[str, Any] = {
            "format": {"type": "audio/pcmu"},
            "voice": self._voice,
        }
        speed = self._settings.get("speed")
        if speed is not None:
            try:
                output_block["speed"] = max(0.25, min(1.5, float(speed)))
            except (TypeError, ValueError):
                pass

        session: Dict[str, Any] = {
            "type": "realtime",
            "instructions": self._instructions,
            "audio": {
                "input": {
                    "format": {"type": "audio/pcmu"},
                    "transcription": {"model": transcription_model},
                    "turn_detection": turn_detection,
                    # noise_reduction omitted when the caller chose "none".
                    **({"noise_reduction": noise_reduction} if noise_reduction else {}),
                },
                "output": output_block,
            },
            "output_modalities": ["audio"],
        }

        # Optional generation controls — again only sent when explicitly set, so
        # the default payload is byte-for-byte unchanged.
        #   temperature       — sampling temperature (0.6–1.2 typical for realtime)
        #   max_output_tokens — cap per model response ("inf" or an int)
        temperature = self._settings.get("temperature")
        if temperature is not None:
            try:
                session["temperature"] = float(temperature)
            except (TypeError, ValueError):
                pass
        max_output_tokens = self._settings.get("max_output_tokens")
        if max_output_tokens is not None:
            session["max_output_tokens"] = max_output_tokens

        if self._tools:
            session["tools"] = self._tools
        return {"type": "session.update", "session": session}

    # ── Caller → model ───────────────────────────────────────────────────
    async def send_caller_audio(self, mulaw_bytes: bytes) -> None:
        """Append one μ-law frame (or several concatenated) from the caller.

        Fail-soft: no-op once the socket is closed. Semantic VAD means we do
        NOT commit the buffer or trigger responses manually — the server
        decides when the caller's turn ends.
        """
        if not mulaw_bytes or self._ws is None or self._closed.is_set():
            return
        try:
            b64 = base64.b64encode(mulaw_bytes).decode("ascii")
            await self._ws.send(json.dumps(
                {"type": "input_audio_buffer.append", "audio": b64}
            ))
            self.stats.audio_frames_in += 1
        except websockets.exceptions.ConnectionClosed:
            self._closed.set()
        except Exception as exc:  # noqa: BLE001
            logger.warning("realtime send_caller_audio failed call=%s err=%s",
                           self._call_id, exc)

    async def send_function_result(self, call_id: str, output: Any) -> None:
        """Return a tool/function result to the model and ask it to continue.

        `output` is JSON-serialised if not already a string.
        """
        if self._ws is None or self._closed.is_set():
            return
        payload_out = output if isinstance(output, str) else json.dumps(output)
        try:
            await self._ws.send(json.dumps({
                "type": "conversation.item.create",
                "item": {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": payload_out,
                },
            }))
            # Nudge the model to speak its answer — but ONLY if no response is
            # currently active. The Realtime API rejects a response.create while
            # one is in flight ("conversation already has an active response").
            # If active, defer: response.done will fire the create for us.
            if self._response_active:
                self._pending_response_create = True
            else:
                await self._create_response()
        except websockets.exceptions.ConnectionClosed:
            self._closed.set()
        except Exception as exc:  # noqa: BLE001
            logger.warning("realtime send_function_result failed call=%s err=%s",
                           self._call_id, exc)

    async def _create_response(self) -> None:
        """Send response.create, tolerating the benign 'active response' race
        (in case the flag lagged a concurrent server-side response)."""
        if self._ws is None or self._closed.is_set():
            return
        try:
            await self._ws.send(json.dumps({"type": "response.create"}))
        except websockets.exceptions.ConnectionClosed:
            self._closed.set()
        except Exception as exc:  # noqa: BLE001
            logger.debug("realtime response.create send failed call=%s err=%s",
                         self._call_id, exc)

    async def trigger_greeting(self) -> None:
        """Make the agent speak first (agent-first outbound): request an
        initial response so the model greets per its instructions, without any
        caller input. No-op if a response is already active."""
        if self._response_active:
            return
        await self._create_response()

    async def send_text(self, text: str, *, create_response: bool = True) -> None:
        """Inject a user *text* turn (used by the text-in test harness and any
        future system-initiated prompt). Not used on the live audio path."""
        if not text or self._ws is None or self._closed.is_set():
            return
        try:
            await self._ws.send(json.dumps({
                "type": "conversation.item.create",
                "item": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": text}],
                },
            }))
            if create_response:
                await self._ws.send(json.dumps({"type": "response.create"}))
        except Exception as exc:  # noqa: BLE001
            logger.warning("realtime send_text failed call=%s err=%s",
                           self._call_id, exc)

    # ── Model → caller (normalised event stream) ─────────────────────────
    async def events(self):
        """Async iterator over normalised RealtimeEvents until the session
        closes. Consume this to get model audio, transcripts, and function
        calls. Exits cleanly when the session ends."""
        while True:
            if self._closed.is_set() and self._event_queue.empty():
                return
            try:
                ev = await asyncio.wait_for(self._event_queue.get(), timeout=0.25)
            except asyncio.TimeoutError:
                continue
            if ev is None:  # sentinel: stream finished
                return
            yield ev

    # ── Receive loop (the single background task) ────────────────────────
    async def _recv_loop(self) -> None:
        try:
            async for raw in self._ws:
                try:
                    data = json.loads(raw)
                except (ValueError, TypeError):
                    continue
                await self._handle_server_event(data)
        except websockets.exceptions.ConnectionClosed:
            logger.info("realtime WS closed call=%s", self._call_id)
            # PHASE 1b RECONNECT HOOK: to add mid-call reconnect, re-run
            # connect() here (re-sending session.update) and `continue` the
            # loop instead of falling through to teardown. Phase 1 ends cleanly.
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — never let the loop crash a call
            logger.error("realtime recv loop error call=%s err=%s",
                         self._call_id, exc)
        finally:
            self._closed.set()
            # Wake any consumer blocked in events().
            self._offer_event(None)

    async def _handle_server_event(self, data: Dict[str, Any]) -> None:
        etype = data.get("type", "")

        # ---- Model audio out (μ-law base64) --------------------------------
        if etype == "response.output_audio.delta":
            # Drop deltas from a superseded (barged-over) response.
            resp_epoch = self._active_response_epoch
            if resp_epoch < self._response_epoch:
                self.stats.audio_frames_dropped_stale += 1
                return
            b64 = data.get("delta")
            if not b64:
                return
            try:
                audio = base64.b64decode(b64)
            except Exception:  # noqa: BLE001
                return
            self.stats.audio_frames_out += 1
            # T1: first model audio of this turn. Log the caller-stopped → first-
            # audio latency (the perceived "how long till it answers" number) and
            # disarm so we only log once per turn.
            if self._t_speech_stopped is not None:
                ms = int((time.monotonic() - self._t_speech_stopped) * 1000)
                self._t_speech_stopped = None
                logger.info(
                    "realtime_turn_latency call=%s speech_end_to_first_audio_ms=%d",
                    self._call_id, ms,
                    extra={"call_id": self._call_id,
                           "realtime_speech_end_to_first_audio_ms": ms},
                )
            self._offer_event(RealtimeEvent(kind="audio", audio=audio, raw=data))
            return

        # ---- Agent's spoken words (text) -----------------------------------
        if etype == "response.output_audio_transcript.delta":
            delta = data.get("delta")
            if delta:
                self._offer_event(RealtimeEvent(kind="agent_transcript", text=delta))
            return

        # Terminal agent transcript for the turn — full text, persisted.
        if etype == "response.output_audio_transcript.done":
            text = data.get("transcript")
            if text:
                self._offer_event(RealtimeEvent(
                    kind="agent_transcript", text=text, is_final=True))
            return

        # ---- Caller transcription (incremental) ----------------------------
        if etype == "conversation.item.input_audio_transcription.delta":
            delta = data.get("delta")
            if delta:
                self._offer_event(RealtimeEvent(kind="caller_transcript", text=delta))
            return

        # Terminal caller transcript for the turn — full text, persisted.
        if etype == "conversation.item.input_audio_transcription.completed":
            text = data.get("transcript")
            if text:
                self._offer_event(RealtimeEvent(
                    kind="caller_transcript", text=text, is_final=True))
            return

        # ---- Barge-in: caller started talking ------------------------------
        if etype == "input_audio_buffer.speech_started":
            self._on_interruption("speech_started")
            return

        # ---- Caller stopped talking (server VAD end-of-speech) = T0 --------
        # Latency mark only: the model's response follows; we time from here to
        # its first audio delta above. No behaviour change — turn-taking is
        # server-owned.
        if etype == "input_audio_buffer.speech_stopped":
            self._t_speech_stopped = time.monotonic()
            return

        # ---- A new model response begins -----------------------------------
        if etype == "response.created":
            # Tag audio deltas that follow to the current epoch so a later
            # barge-in can invalidate exactly this turn's buffered audio.
            self._active_response_epoch = self._response_epoch
            self._response_active = True
            return

        # ---- Function / tool call ready to fulfil --------------------------
        if etype == "response.function_call_arguments.done":
            call_id = data.get("call_id") or ""
            name = data.get("name") or ""
            args = data.get("arguments") or ""
            if call_id and name:
                self.stats.function_calls += 1
                self._offer_event(RealtimeEvent(
                    kind="function_call",
                    function_call=RealtimeFunctionCall(call_id, name, args),
                    raw=data,
                ))
            return

        # ---- Response finished ---------------------------------------------
        if etype == "response.done":
            self._response_active = False
            resp = data.get("response") or {}
            status = resp.get("status")
            # A cancelled/interrupted response means the caller barged in and
            # the server truncated the turn — flush any stale queued audio.
            if status in ("cancelled", "incomplete") and \
                    resp.get("status_details", {}).get("reason") in (
                        "turn_detected", "interruption", "cancelled"):
                self._on_interruption("response_done_cancelled")
            # A function-call result was submitted while this response was
            # active — now that it has ended, ask the model to speak the answer.
            if self._pending_response_create:
                self._pending_response_create = False
                await self._create_response()
            self._offer_event(RealtimeEvent(kind="response_done", raw=data))
            return

        # ---- Errors --------------------------------------------------------
        if etype == "error":
            err = data.get("error") or data
            # Benign race: a response.create landed while one was already
            # active. We already guard against this, but tolerate the server
            # echo without surfacing it as a call-ending error.
            msg = str(err).lower()
            if "active response" in msg or "already has an active response" in msg:
                logger.debug("realtime benign active-response race call=%s: %s",
                             self._call_id, err)
                return
            self.stats.errors += 1
            logger.warning("realtime server error call=%s: %s", self._call_id, err)
            self._offer_event(RealtimeEvent(kind="error", text=str(err), raw=data))
            return

        # Everything else (session.updated echoes, buffer commits, rate-limit
        # notices, etc.) is intentionally ignored in Phase 1.

    # ── Barge-in handling ────────────────────────────────────────────────
    def _on_interruption(self, reason: str) -> None:
        """Caller took the floor: invalidate the in-flight response and FLUSH
        any model audio still queued, so the agent stops mid-sentence instead
        of talking over the caller."""
        self._response_epoch += 1
        dropped = self._flush_audio_events()
        logger.debug(
            "realtime barge-in call=%s reason=%s flushed=%d epoch=%d",
            self._call_id, reason, dropped, self._response_epoch,
        )
        self._offer_event(RealtimeEvent(kind="interrupted", raw={"reason": reason}))

    def _flush_audio_events(self) -> int:
        """Drain queued 'audio' events (stale, superseded by barge-in).
        Non-audio events (transcripts, function calls) are preserved."""
        kept: List[Any] = []
        dropped = 0
        while True:
            try:
                item = self._event_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if item is not None and getattr(item, "kind", None) == "audio":
                dropped += 1
            else:
                kept.append(item)
        for item in kept:
            try:
                self._event_queue.put_nowait(item)
            except asyncio.QueueFull:
                break
        return dropped

    def _offer_event(self, ev: Optional[RealtimeEvent]) -> None:
        """Enqueue an event without ever blocking the receive loop. On a full
        queue evict the OLDEST *audio* event (stale audio is worthless live)
        while preserving function_calls / transcripts / control events. If the
        queue somehow holds no audio (shouldn't happen), fall back to dropping
        the oldest item so we never block."""
        try:
            self._event_queue.put_nowait(ev)
            return
        except asyncio.QueueFull:
            pass
        if self._evict_one_audio():
            self.stats.audio_frames_dropped_overflow += 1
        else:
            try:
                self._event_queue.get_nowait()
                self.stats.audio_frames_dropped_overflow += 1
            except asyncio.QueueEmpty:
                pass
        try:
            self._event_queue.put_nowait(ev)
        except asyncio.QueueFull:
            logger.warning("realtime event dropped — queue full call=%s", self._call_id)

    def _evict_one_audio(self) -> bool:
        """Remove the single OLDEST 'audio' event from the queue, preserving
        the order of everything else. Returns True if one was removed."""
        items: List[Any] = []
        removed = False
        while True:
            try:
                item = self._event_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if (not removed and item is not None
                    and getattr(item, "kind", None) == "audio"):
                removed = True
                continue
            items.append(item)
        for item in items:
            try:
                self._event_queue.put_nowait(item)
            except asyncio.QueueFull:
                break
        return removed

    # ── Teardown ─────────────────────────────────────────────────────────
    async def close(self) -> None:
        """Idempotent, exception-proof teardown: cancel the receive task and
        close the socket. Safe to call multiple times."""
        await self._teardown()

    async def _teardown(self) -> None:
        self._closed.set()
        # Cancel the single background task.
        task = self._recv_task
        self._recv_task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        # Close the socket.
        ws = self._ws
        self._ws = None
        if ws is not None:
            try:
                await ws.close()
            except Exception:  # noqa: BLE001 — cleanup must never raise
                pass
        # Wake any consumer blocked in events().
        try:
            self._event_queue.put_nowait(None)
        except Exception:  # noqa: BLE001
            pass

    # ── Helpers ──────────────────────────────────────────────────────────
    async def _recv_json(self) -> Optional[Dict[str, Any]]:
        if self._ws is None:
            return None
        raw = await self._ws.recv()
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            return None


# ── Tool schema helper ──────────────────────────────────────────────────────
def knowledge_lookup_tool() -> Dict[str, Any]:
    """The single Phase-1 tool: a company-facts knowledge lookup, in the
    Realtime API's function-tool shape. The gateway fulfils it by calling
    `retrieve_knowledge(...)` and returning the text via send_function_result.
    """
    return {
        "type": "function",
        "name": "knowledge_lookup",
        "description": (
            "Look up a specific company fact (pricing, hours, policies, "
            "product details, service areas) before stating it. Use this "
            "whenever the caller asks about the company and you are not "
            "certain of the answer. Never guess company facts — look them up."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What to look up, in a few words.",
                },
            },
            "required": ["query"],
        },
    }
