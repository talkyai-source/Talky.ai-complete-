"""Realtime pipeline bridge: wires an OpenAIRealtimeSession to the media
gateway's caller-audio-in / model-audio-out transport.

This REPLACES the cascaded STT→LLM→TTS middle for calls whose
`pipeline_mode == "realtime"`. It reuses the EXACT same transport the cascaded
path uses:

  * caller audio in  — `media_gateway.get_audio_queue(call_id)` (the same queue
    AudioIngest drains). See audio_ingest.py:95.
  * model audio out  — `media_gateway.send_audio(call_id, pcm)` (the same sink
    TTS uses via synthesize_and_send_audio → gateway.send_audio). See
    telephony_media_gateway.py:390.
  * barge-in         — `media_gateway.clear_output_buffer(call_id)` (the same
    call the cascaded barge-in path uses). See telephony_media_gateway.py:652.

Audio format
------------
OpenAI Realtime speaks μ-law/8kHz both directions (audio/pcmu). The media
gateway's queue/sink speak linear16 PCM at the gateway's INTERNAL sample rate.
So the bridge converts at the boundary:

  caller:  gateway PCM16 @ internal_rate  --(downsample to 8k)--> μ-law  --> OpenAI
  model:   OpenAI μ-law 8k  --> PCM16 8k  --(upsample to internal_rate)--> gateway

When the realtime gateway is configured at internal_rate == 8000 (what the
orchestrator does for realtime sessions), the resample steps are skipped
entirely and only the cheap, unavoidable μ-law codec conversion happens — the
"no resampling" ideal. The resample fallback keeps the bridge correct for any
other internal rate.

Playback and failure handling
-----------------------------
Caller and model pumps remain independent of one bounded playback task.
Complete responses are validated before playback; barge-in cancels playback.
Unexpected provider loss raises to the call lifecycle for explicit teardown.
There is no automatic switch to the traditional voice engine.
"""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
import os
import time
from dataclasses import is_dataclass, replace
from typing import Any, Awaitable, Callable, Optional

from app.domain.services.voice_pipeline.live_structured_state import (
    ConfirmedContactsEvidence,
    IdentityEvidence,
    LiveConversationState,
    ToolResultEvidence,
    evidence_from_transcript,
    reduce_live_state,
    render_live_state_block,
)

logger = logging.getLogger(__name__)

_WIRE_RATE = 8000  # OpenAI Realtime audio/pcmu is μ-law @ 8 kHz

# Per-node char budget for the realtime knowledge function-result. The
# source-first render (full node content) can be large; cap each node on a
# safe boundary so a giant KB section can't balloon the model's tool result
# (and its latency). Env-overridable.
_REALTIME_NODE_CHARS = int(os.getenv("KNOWLEDGE_REALTIME_NODE_CHARS", "2000"))

# Bounded no-match message. Unavailable and superseded lookups carry their
# own explicit status and message so missing evidence is not reported as a
# successful lookup or a verified negative answer.
_NO_KB_INFO = "I don't have specific information on that."
_SUPERSEDED_KB_INFO = "This lookup was superseded by a newer request. Do not use its earlier facts."


# Sections a realtime knowledge lookup returns. Two left the right one out
# whenever a catch-all section also matched (Dojo-PC, 2026-09-30); three puts
# it in on every question measured against the live knowledge.
_REALTIME_KB_K = 3


class RealtimeBridge:
    """Drives one realtime call: gateway <-> OpenAIRealtimeSession.

    Construct with the already-connected session, the media gateway, the
    call_id, the gateway's internal PCM sample rate, and (optionally) a
    knowledge context plus the shared CallSession so knowledge and action tools
    can be fulfilled through the same deterministic contracts.
    """

    def __init__(
        self,
        *,
        call_id: str,
        realtime_session: Any,
        media_gateway: Any,
        internal_sample_rate: int = 8000,
        knowledge_pool: Any = None,
        tenant_id: Optional[str] = None,
        campaign_id: Optional[str] = None,
        lead_id: Optional[str] = None,
        contact_phone_region: Optional[str] = None,
        contact_session: Optional[Any] = None,
        knowledge_snapshot_nodes: Optional[list[dict]] = None,
        session_active: Optional[Any] = None,
        greet_on_start: bool = True,
        barge_in_event: Optional[asyncio.Event] = None,
        transcript_service: Optional[Any] = None,
        talklee_call_id: Optional[str] = None,
        on_connection_lost: Optional[Callable[[], Awaitable[None]]] = None,
        call_direction: str = "outbound",
        action_session: Optional[Any] = None,
        on_end_call: Optional[Callable[[], Awaitable[None]]] = None,
    ) -> None:
        self._call_id = call_id
        self._rt = realtime_session
        self._gw = media_gateway
        self._internal_rate = int(internal_sample_rate or 8000)
        self._knowledge_pool = knowledge_pool
        self._tenant_id = tenant_id
        self._campaign_id = campaign_id
        self._lead_id = lead_id
        self._contact_phone_region = contact_phone_region
        self._knowledge_snapshot_nodes = knowledge_snapshot_nodes
        # Transcript accumulation. The realtime speech-to-speech path produces
        # NO transcript on its own; we feed the model's final agent + caller
        # transcripts into the SAME in-memory TranscriptService buffer the
        # cascaded path uses (class-level, keyed by call_id), so the shared
        # hangup persister (call_transcript_persister) writes them to the calls
        # row exactly like a cascaded call. Optional / fail-soft — a missing
        # service or any accumulation error must never break the call.
        self._transcript_service = transcript_service
        self._talklee_call_id = talklee_call_id
        self._turn_index = 0
        self._live_user_turn_seq = 0
        self._live_state = LiveConversationState()
        # Provider item ownership is bounded metadata, not a second transcript.
        # ASR completion order is not necessarily the caller's speech order.
        self._caller_items: dict[str, dict] = {}
        self._caller_item_counter = 0
        self._caller_item_identity_seen = False
        self._latest_caller_audio_start_ms: Optional[int] = None
        self._current_caller_item_key = None
        self._current_caller_order = 0
        self._current_caller_transcript_index = None
        self._current_contact_baseline = None
        self._current_contact_result = None
        self._current_contact_history = ()
        self._current_contact_readback = None
        self._last_contact_readback = None
        self._transcript_flush_task = None
        self._transcript_flush_pending = False
        self._opt_out_task: Optional[asyncio.Task] = None
        self._pre_current_relationship_state = self._live_state
        self._call_direction = str(call_direction or "outbound").strip().lower()
        # Shared CallSession for deterministic voice-action results. Optional so
        # older construction sites/tests remain compatible; the bridge itself
        # is a safe in-memory fallback for fail-closed unavailable results.
        self._action_session = action_session or self
        self._latest_caller_text = ""
        self._previous_assistant_text = ""
        self._playback_task = None
        self._repair_attempted = False
        self._failure_reason = None
        self._verified_knowledge = []
        self._knowledge_evidence = {"status": "unavailable", "passages": [], "text": ""}
        self._knowledge_lookup_seq = 0
        self._utterance = None
        self._playback_generation = 0
        self._on_end_call = on_end_call
        self._closing_after_generation = None
        self._goodbye_completed = asyncio.Event()
        self._termination_task = None
        self._caller_activity_revision = 0
        self._pending_end_call_revision = None
        self._caller_transcript_pending = False
        self._hangup_started = False
        if transcript_service is not None and talklee_call_id:
            try:
                transcript_service.bind_call_identity(call_id, talklee_call_id)
            except Exception:  # noqa: BLE001
                pass
        # Optional callable returning whether the call is still active; lets the
        # caller pump stop promptly on hangup. Defaults to "always active" —
        # the pumps also stop when the RealtimeSession closes.
        self._session_active = session_active or (lambda: True)
        # Same barge-in event the gateway's send_audio pacing loop watches
        # (set via gateway.set_barge_in_event in the orchestrator). On an
        # "interrupted" event from OpenAI we set() it BEFORE clearing the
        # output buffer so the pacing loop's wait_for(event.wait(), ...)
        # wakes immediately instead of waiting out its sleep window — then
        # clear() it right after so it doesn't stay latched "set" for the
        # next turn. Mirrors the cascaded path's barge_in_event wiring.
        self._barge_in_event = barge_in_event
        # Agent-first: make the model greet immediately on connect. Set False
        # for caller-speaks-first campaigns (let semantic VAD wait for the
        # caller). Defaults True — outbound telephony is agent-first.
        self._greet_on_start = greet_on_start

        # Fix 14 — mid-call connection-loss fallback hook. When the realtime
        # socket dies while the call is still up, run() invokes this exactly
        # once so the lifecycle layer can rebuild the cascaded pipeline on the
        # SAME media gateway. May be sync or async; a None hook (or the env
        # kill-switch not wiring one) preserves today's behaviour. Set either
        # via the constructor or set_on_connection_lost() after construction
        # (the lifecycle layer, which knows the PBX call_id, wires it there).
        self._on_connection_lost: Optional[Callable[[], Any]] = on_connection_lost
        # Armed in run() when an unexpected socket death is detected; consumed
        # in run()'s finally. Separate from _connection_lost_fired so the
        # callback can never be invoked more than once.
        self._connection_lost = False
        self._connection_lost_fired = False

        self._stop = asyncio.Event()
        self._caller_task: Optional[asyncio.Task] = None
        self._model_task: Optional[asyncio.Task] = None
        # Detached knowledge-tool tasks. A tool call does a DB round-trip and
        # MUST NOT block the single model-event pump (that would freeze audio +
        # barge-in for the whole lookup), so each is dispatched as its own task
        # and tracked here for clean cancellation on teardown.
        self._tool_tasks: "set[asyncio.Task]" = set()
        # C3 contact state lives on the SAME CallSession shape the cascaded
        # TurnRunner uses. Realtime only supplies transcript evidence; it does
        # not get a second email/phone implementation.
        if contact_session is None:
            from types import SimpleNamespace

            contact_session = SimpleNamespace(captured_slots=None)
        self._contact_session = contact_session
        self._contact_history: list[Any] = []
        self._contact_tasks: "set[asyncio.Task]" = set()
        self._contact_persist_tail: Optional[asyncio.Task] = None
        self._pending_contact_agent_turn: Optional[str] = None
        self._contact_agent_interrupted = False
        # Identity is delivery evidence, not generated-text evidence. Consume
        # this exactly once when the opening response completes uninterrupted.
        self._identity_opening_pending = bool(greet_on_start)
        self._opening_interrupted = False

    # ── Lifecycle ────────────────────────────────────────────────────────
    def set_on_connection_lost(
        self, cb: Optional[Callable[[], Any]]
    ) -> None:
        """Wire (or clear) the mid-call connection-loss callback after
        construction. The lifecycle layer uses this because it — not the
        orchestrator that builds the bridge — knows the PBX call_id the
        recovery handler needs, and gates the wiring on
        REALTIME_FALLBACK_ENABLED (a None callback = today's behaviour)."""
        self._on_connection_lost = cb

    async def run(self) -> None:
        """Run until the call ends or the realtime session closes. Fail-soft:
        never raises out — a realtime error ends this bridge (and the call)
        cleanly."""
        logger.info("realtime_bridge start call=%s internal_rate=%d wire_rate=%d",
                    self._call_id, self._internal_rate, _WIRE_RATE)
        # Diagnostic guard: the μ-law wire to OpenAI is fixed at 8 kHz. When the
        # gateway internal rate is ALSO 8 kHz (what the orchestrator forces for
        # realtime) the bridge does the ideal zero-resample codec-only path. Any
        # other value means every caller/model frame is resampled — if the
        # gateway's real input rate ever diverges from this single value the
        # caller audio is silently pitch/speed-shifted before the model hears it
        # (the "voice not flowing into the model" failure). Make it visible.
        if self._internal_rate != _WIRE_RATE:
            logger.warning(
                "realtime_bridge call=%s gateway internal_rate=%d != wire %d — "
                "every frame is resampled; verify the gateway feeds caller audio "
                "at exactly this rate or the model hears a speed-shifted caller",
                self._call_id, self._internal_rate, _WIRE_RATE,
            )
        # Tell the gateway this session's output is already real-time-paced by
        # the model, so its send_audio pacing loop skips opportunistic batching
        # (batch=1) — realtime shouldn't pay cascaded's TTS-batching latency.
        try:
            self._gw.set_realtime_output(self._call_id, True)
        except Exception:  # noqa: BLE001 — pacing hint is best-effort
            pass
        try:
            self._caller_task = asyncio.create_task(
                self._pump_caller_audio(), name=f"rt-caller-{self._call_id}"
            )
            self._model_task = asyncio.create_task(
                self._pump_model_events(), name=f"rt-model-{self._call_id}"
            )
            # Agent-first: kick the opening greeting so the caller doesn't hear
            # dead air on pickup. Fail-soft — a greeting error must not stop the
            # bridge.
            if self._greet_on_start:
                try:
                    await self._rt.trigger_greeting()
                except Exception as exc:  # noqa: BLE001
                    logger.debug("realtime_bridge greeting trigger err: %s", exc)
            # Whichever finishes first (call end, socket close, or error) ends
            # the bridge; then we cancel the other.
            done, pending = await asyncio.wait(
                {self._caller_task, self._model_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for t in done:
                exc = t.exception()
                if exc:
                    logger.warning("realtime_bridge task err call=%s: %s",
                                   self._call_id, exc)
            # Detect an unexpected disconnect before stop() sets the stop flag.
            if (
                self._rt.closed()
                and not self._stop.is_set()
                and self._session_active()
            ):
                logger.warning(
                    "realtime_bridge connection_lost call=%s — realtime socket "
                    "died mid-call; ending Realtime session", self._call_id,
                )
                self._connection_lost = True
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — never crash the call worker
            logger.error("realtime_bridge run error call=%s: %s",
                         self._call_id, exc)
        finally:
            # Release pumps and provider resources before notifying the owner.
            await self.stop()
            if self._connection_lost:
                await self._invoke_on_connection_lost()
            logger.info("realtime_bridge end call=%s", self._call_id)
        if self._failure_reason or (self._connection_lost and self._on_connection_lost is None):
            raise RuntimeError(self._failure_reason or "Realtime connection lost; no alternative engine was used")

    async def _invoke_on_connection_lost(self) -> None:
        """Invoke the connection-loss callback exactly once, wrapped so a
        callback error can never crash the bridge. Supports a sync or async
        callback."""
        cb = self._on_connection_lost
        if cb is None or self._connection_lost_fired:
            return
        self._connection_lost_fired = True
        try:
            result = cb()
            if inspect.isawaitable(result):
                await result
        except Exception as exc:  # noqa: BLE001 — callback error must not crash
            logger.error(
                "realtime_bridge on_connection_lost callback err call=%s: %s",
                self._call_id, exc,
            )

    async def stop(self) -> None:
        """Idempotent teardown: stop pumps, cancel any in-flight tool task,
        close the realtime session."""
        self._stop.set()
        tasks = [t for t in (self._caller_task, self._model_task) if t is not None]
        tasks += list(self._tool_tasks)
        if self._playback_task is not None:
            tasks.append(self._playback_task)
        if self._termination_task is not None:
            tasks.append(self._termination_task)
        tasks = [t for t in tasks if t is not asyncio.current_task()]
        for task in tasks:
            if not task.done():
                task.cancel()
        for task in tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._caller_task = None
        self._model_task = None
        self._tool_tasks.clear()
        # Contact audit writes are detached from the audio pump so a database
        # wait never creates audible gaps. Give already-started writes a short
        # drain window on teardown; then cancel rather than hang the call end.
        pending_writes = set(self._contact_tasks)
        if self._transcript_flush_task is not None:
            pending_writes.add(self._transcript_flush_task)
        if self._opt_out_task is not None:
            pending_writes.add(self._opt_out_task)
        if pending_writes:
            _done, pending = await asyncio.wait(
                pending_writes, timeout=2.0
            )
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
        self._contact_tasks.clear()
        self._transcript_flush_task = None
        self._opt_out_task = None
        try:
            await self._rt.close()
        except Exception:  # noqa: BLE001 — cleanup must never raise
            pass

    # ── Caller audio: gateway queue -> OpenAI ────────────────────────────
    async def _pump_caller_audio(self) -> None:
        from app.utils.audio_utils import pcm_to_ulaw, resample_audio

        queue = self._gw.get_audio_queue(self._call_id)
        if queue is None:
            logger.error("realtime_bridge no audio queue call=%s — caller audio "
                         "will not reach the model", self._call_id)
            return
        while not self._stop.is_set() and not self._rt.closed():
            try:
                if not self._session_active():
                    break
                try:
                    chunk = await asyncio.wait_for(queue.get(), timeout=0.05)
                except asyncio.TimeoutError:
                    continue
                if not chunk:
                    continue
                pcm16 = chunk if isinstance(chunk, (bytes, bytearray)) else getattr(chunk, "data", b"")
                if not pcm16:
                    continue
                # Downsample to 8k if the gateway runs at a higher internal rate.
                if self._internal_rate != _WIRE_RATE:
                    try:
                        pcm16 = resample_audio(
                            bytes(pcm16),
                            from_rate=self._internal_rate,
                            to_rate=_WIRE_RATE,
                            channels=1,
                            bit_depth=16,
                            res_type="soxr_mq",
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.debug("realtime_bridge caller resample failed: %s", exc)
                        continue
                mulaw = pcm_to_ulaw(bytes(pcm16))
                await self._rt.send_caller_audio(mulaw)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.debug("realtime_bridge caller pump err call=%s: %s",
                             self._call_id, exc)
                # Transient — keep going; a closed socket ends the loop via closed().
        logger.debug("realtime_bridge caller pump ended call=%s", self._call_id)

    @staticmethod
    def _caller_item_id(raw: Any) -> Optional[str]:
        value = raw.get("item_id") if isinstance(raw, dict) else None
        return value if isinstance(value, str) and value.strip() and len(value) <= 256 else None

    def _register_caller_item(self, raw: Any, *, anchored: bool = True) -> Optional[dict]:
        item_id = self._caller_item_id(raw)
        if item_id is None:
            return None
        if anchored:
            self._caller_item_identity_seen = True
        item = self._caller_items.get(item_id)
        if item is not None:
            return item
        offset = raw.get("audio_start_ms")
        if "audio_start_ms" in raw and (
            isinstance(offset, bool) or not isinstance(offset, int) or offset < 0
            or (self._latest_caller_audio_start_ms is not None
                and offset <= self._latest_caller_audio_start_ms)
        ):
            # OpenAI documents this offset from all input audio in this
            # session. A retired speech anchor cannot become a newer turn.
            # Keep a bounded tombstone so its following commit also fails.
            item = {"order": None, "digest": None, "audio_start_ms": None}
        else:
            self._caller_item_counter += 1
            item = {"order": self._caller_item_counter, "digest": None,
                    "audio_start_ms": offset}
            if offset is not None:
                self._latest_caller_audio_start_ms = offset
        self._caller_items[item_id] = item
        if len(self._caller_items) > 64:
            del self._caller_items[next(iter(self._caller_items))]
        return item

    def _admit_caller_final(self, text: str, raw: Any) -> Optional[tuple[int, str]]:
        item_id = self._caller_item_id(raw)
        item = self._caller_items.get(item_id) if item_id else None
        if item is None:
            if self._caller_item_identity_seen:
                # Missing/retired item ownership cannot become a new current
                # correction just because its ASR finished late.
                logger.debug("realtime caller final ignored call=%s reason=unowned_item", self._call_id)
                return None
            if item_id is None:
                item_id = f"anonymous:{self._caller_item_counter + 1}"
            item = self._register_caller_item({"item_id": item_id}, anchored=False)
        order = item["order"]
        if order is None:
            return None
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if item["digest"] == digest:
            return None
        if order < max(self._current_caller_order, self._caller_item_counter):
            if item["digest"] is not None:
                return None  # an old revision cannot replace a newer turn
            item["digest"] = digest
            return order, "historical"
        replacement = item_id == self._current_caller_item_key and item["digest"] is not None
        item["digest"] = digest
        self._current_caller_item_key = item_id
        self._current_caller_order = order
        return order, "replacement" if replacement else "current"

    @staticmethod
    def _with_relationship(state: LiveConversationState, evidence_state: LiveConversationState) -> LiveConversationState:
        return replace(state, customer_relationship=evidence_state.customer_relationship,
                       relationship_turn_id=evidence_state.relationship_turn_id,
                       relationship_turn_order=evidence_state.relationship_turn_order)

    # ── Model events: OpenAI -> gateway (+ tools, barge-in) ──────────────
    async def _pump_model_events(self) -> None:
        try:
            async for ev in self._rt.events():
                if self._stop.is_set():
                    break
                kind = getattr(ev, "kind", None)

                if kind == "response_candidate":
                    from app.domain.services.llm_guardrails import get_guardrails
                    from app.domain.services.voice_pipeline.action_tools import action_results_for_session
                    from app.domain.services.voice_pipeline.action_execution import enabled_voice_actions
                    valid, reason = get_guardrails().validate_response(
                        ev.text or "", action_results=action_results_for_session(self._action_session),
                        available_actions=set(enabled_voice_actions(self._action_session)))
                    from app.domain.services.voice_pipeline.grounded_figures import ground_spoken_figures
                    grounded, unsupported = ground_spoken_figures(ev.text or "", self._verified_knowledge)
                    if grounded != (ev.text or "") or unsupported:
                        valid, reason = False, "unsupported_company_figure"
                    from app.domain.services.voice_pipeline.grounded_links import ground_spoken_links
                    grounded_links, unsupported_links = ground_spoken_links(ev.text or "", self._verified_knowledge)
                    if grounded_links != (ev.text or "") or unsupported_links:
                        valid, reason = False, "unsupported_company_resource"
                    from app.domain.services.voice_pipeline.conversation_guards import contradicted_customer_claim
                    if contradicted_customer_claim(ev.text or "", relationship=self._live_state.customer_relationship):
                        valid, reason = False, "contradicted_customer_relationship"
                    if not valid:
                        logger.warning("realtime_playout_rejected call=%s reason=%s", self._call_id, reason)
                        if self._repair_attempted:
                            self._failure_reason = "Realtime reply failed validation twice"
                            break
                        self._repair_attempted = True
                        await self._rt.repair_unspoken_response((ev.raw or {}).get("response") or {})
                        continue
                    if self._playback_task and not self._playback_task.done():
                        # A replacement may follow asynchronous transcription or
                        # tool results. Stop the owned utterance before replacing
                        # it, independent of provider generation completion.
                        await self._cancel_playback()
                    self._playback_generation += 1
                    self._utterance = {
                        "id": f"rt-{self._playback_generation}",
                        "generation": self._playback_generation,
                        "raw": ev.raw or {}, "text": ev.text or "", "status": "validated", "receipt": None,
                    }
                    self._playback_task = asyncio.create_task(self._play_validated_response(ev))

                elif kind == "caller_turn":
                    self._register_caller_item(getattr(ev, "raw", None))

                elif kind == "interrupted":
                    raw = getattr(ev, "raw", None) or {}
                    if raw.get("reason") == "speech_started":
                        self._register_caller_item(raw)
                    # Revoke the timer immediately, before ASR supplies the new
                    # words. A delayed tool from the old turn cannot rearm it.
                    if not self._caller_transcript_pending:
                        self._snapshot_caller_question()
                    self._revoke_pending_end_call(awaiting_transcript=True)
                    await self._cancel_playback(getattr(ev, "raw", None))
                    if self._identity_opening_pending:
                        # A later completed answer need not contain the identity
                        # from this cancelled opening. Do not mark it delivered.
                        self._identity_opening_pending = False
                        self._opening_interrupted = True
                        await self._publish_live_state()
                    if bool((getattr(ev, "raw", None) or {}).get("during_response")):
                        self._contact_agent_interrupted = True

                elif kind == "function_call" and ev.function_call:
                    # Do NOT await here — this is the SOLE event pump. The tool
                    # does a knowledge DB round-trip; awaiting it would stall
                    # audio deltas AND barge-in ("interrupted") for the whole
                    # lookup, so the agent goes silent and un-interruptible
                    # mid-turn. Dispatch it detached and keep draining the
                    # socket; send_function_result already sequences the
                    # follow-up response.create safely (app/realtime/openai.py).
                    tool_task = asyncio.create_task(
                        self._handle_function_call(ev.function_call),
                        name=f"rt-tool-{self._call_id}",
                    )
                    self._tool_tasks.add(tool_task)
                    tool_task.add_done_callback(self._tool_tasks.discard)

                elif kind == "caller_transcript" and (ev.text or getattr(ev, "is_final", False)):
                    text = ev.text if isinstance(ev.text, str) else ""
                    # Contact values are high-risk transcript content. Log only
                    # event shape, never the raw caller text.
                    logger.debug(
                        "realtime caller transcript call=%s chars=%d final=%s",
                        self._call_id[:12], len(text),
                        bool(getattr(ev, "is_final", False)),
                    )
                    if getattr(ev, "is_final", False):
                        # Provider-final caller opt-out is monotonic, even if
                        # turn metadata has expired and cannot own actions.
                        self._record_caller_opt_out(text)
                        admission = self._admit_caller_final(text, getattr(ev, "raw", None))
                        if admission is None:
                            continue
                        caller_order, admission_kind = admission
                        evidence = evidence_from_transcript(
                            role="user", text=text,
                            turn_id=f"realtime:{caller_order}", caller_turn_order=caller_order,
                        )
                        if admission_kind == "historical":
                            if evidence is not None:
                                previous = self._live_state
                                self._live_state = self._with_relationship(self._live_state,
                                    reduce_live_state(self._live_state, evidence))
                                # Current-item retraction must restore the
                                # latest prior evidence, including late ASR.
                                self._pre_current_relationship_state = self._with_relationship(
                                    self._pre_current_relationship_state,
                                    reduce_live_state(self._pre_current_relationship_state, evidence))
                                if previous != self._live_state:
                                    await self._invalidate_contradicted_playback()
                                    await self._publish_live_state()
                            self._record_turn("user", text, metadata={
                                "caller_turn_order": caller_order, "late_final": True})
                            continue
                        if admission_kind == "replacement":
                            # A corrected final for the current item replaces
                            # only that item's relationship contribution. Never
                            # roll back intervening tool/contact/delivery facts.
                            corrected = (reduce_live_state(self._pre_current_relationship_state, evidence)
                                         if evidence is not None else self._pre_current_relationship_state)
                            self._live_state = self._with_relationship(self._live_state, corrected)
                            self._latest_caller_text = text
                            # This is the same caller item, but its old ASR can
                            # no longer authorize a pending external action.
                            self._live_user_turn_seq += 1
                            self._action_session._voice_action_user_turn = self._live_user_turn_seq
                            self._revoke_pending_end_call()
                            self._record_caller_revision(caller_order, text)
                            await self._observe_contact_turn(text, getattr(ev, "raw", None), revision=True)
                            await self._invalidate_contradicted_playback()
                            await self._publish_live_state()
                            continue
                        self._pre_current_relationship_state = self._live_state
                        if not self._caller_transcript_pending:
                            self._snapshot_caller_question()
                        self._revoke_pending_end_call()
                        self._repair_attempted = False
                        self._latest_caller_text = text
                        self._live_user_turn_seq += 1
                        self._action_session._voice_action_user_turn = self._live_user_turn_seq
                        # The caller owns close/DNC intent even when the model
                        # produces only a plain goodbye and omits its tool.
                        self._arm_caller_end_call(require_explicit=True)
                        if evidence is not None:
                            self._live_state = reduce_live_state(
                                self._live_state, evidence
                            )
                            await self._invalidate_contradicted_playback()
                            await self._publish_live_state()
                        _tidx = self._turn_index
                        self._current_contact_baseline = getattr(self._contact_session, "captured_slots", None)
                        self._current_contact_history = tuple(self._contact_history)
                        self._current_contact_readback = self._last_contact_readback
                        await self._observe_contact_turn(text, getattr(ev, "raw", None))
                        observed_slots = getattr(self._contact_session, "captured_slots", None)
                        self._current_contact_result = replace(observed_slots) if is_dataclass(observed_slots) else None
                        self._remember_contact_turn("user", text)
                        self._current_caller_transcript_index = self._record_turn("user", text, metadata={
                            "provider_item_id": self._current_caller_item_key,
                            "caller_turn_order": caller_order,
                        })
                        # Real-time voicemail detection on the opening turn(s):
                        # if the callee is an answering machine, hang up now and
                        # end the pump — no message, no conversation.
                        if self._call_direction != "inbound" and _tidx <= 1:
                            from app.domain.services.voice_pipeline.voicemail_detector import (
                                detect_and_hang_up_voicemail,
                            )
                            if await detect_and_hang_up_voicemail(
                                self._call_id, text, _tidx
                            ):
                                break

                elif kind == "error":
                    logger.warning("realtime_bridge model error call=%s: %s",
                                   self._call_id, ev.text)
                    # Report failure through the owning call lifecycle.
                    self._failure_reason = "Realtime provider reported an error"
                    break
                elif kind in {"generation_incomplete", "response_unplayable"}:
                    # Both provider adapters share one retry budget. A reply
                    # withheld for missing text/overflow is not fatal once,
                    # but alternating event names cannot reset that budget.
                    if self._repair_attempted:
                        self._failure_reason = "Realtime generation did not complete after one shorter retry"
                        break
                    self._repair_attempted = True
                    await self._cancel_playback()
                    await self._rt.repair_unspoken_response((ev.raw or {}).get("response") or {})
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error("realtime_bridge model pump err call=%s: %s",
                         self._call_id, exc)
        logger.debug("realtime_bridge model pump ended call=%s", self._call_id)

    async def _send_control_event(self, payload):
        send = getattr(self._gw, "send_control_event", None)
        if callable(send):
            await send(self._call_id, payload)

    async def _invalidate_contradicted_playback(self, owned_utterance=None) -> bool:
        """Stop only an unfinished utterance contradicted by accepted evidence."""
        from app.domain.services.voice_pipeline.conversation_guards import contradicted_customer_claim
        utterance = self._utterance
        if owned_utterance is not None and (
                utterance is not owned_utterance
                or owned_utterance.get("status") == "interrupted"):
            return True
        if (not utterance or utterance.get("status") in {"completed", "interrupted"}
                or not contradicted_customer_claim(utterance.get("text", ""),
                    relationship=self._live_state.customer_relationship)):
            return False
        retry_available = not self._repair_attempted
        self._repair_attempted = True
        await self._cancel_playback()
        if not retry_available:
            self._failure_reason = "Realtime reply failed validation twice"
            self._stop.set()
            close = getattr(self._rt, "close", None)
            if callable(close):
                await close()
            return True
        # Some of this utterance may have reached the transport. Preserve its
        # delivery/truncation evidence; never label it wholly withheld/unheard.
        await self._publish_live_state()
        request = getattr(self._rt, "request_response", None)
        if callable(request) and not self._stop.is_set():
            await request()
        return True

    def _owns_unfinished_playback(self, utterance) -> bool:
        return (self._utterance is utterance
                and utterance.get("status") not in {"completed", "interrupted"}
                and not self._stop.is_set())

    async def _cancel_playback(self, raw=None) -> None:
        """Invalidate exactly the old utterance before a replacement can speak."""
        self._action_session._voice_action_delivered_text = ""
        utterance = self._utterance
        receipt = None
        if utterance is not None:
            receipt = utterance.get("receipt")
            inspect_receipt = getattr(self._gw, "playback_receipt", None)
            if callable(inspect_receipt):
                receipt = inspect_receipt(self._call_id, utterance["id"]) or receipt
            if utterance["status"] not in {"completed", "interrupted"}:
                utterance["status"] = "interrupted"
        task = self._playback_task
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if self._barge_in_event is not None:
            self._barge_in_event.set()
        try:
            await self._send_control_event({"type": "tts_interrupted", "utterance_id": (utterance or {}).get("id")})
            clear = getattr(self._gw, "clear_output_buffer", None)
            if callable(clear):
                await clear(self._call_id)
            truncate = getattr(self._rt, "truncate_response", None)
            if callable(truncate) and not (utterance and utterance["status"] == "completed"):
                played = (receipt or {}).get("played_ms", 0)
                await truncate((utterance or {}).get("raw") or raw or {}, played_ms=played or 0)
            elif callable(truncate) and raw and raw.get("during_response"):
                # A new still-generating response can be interrupted after the
                # preceding utterance has completely played.
                await truncate(raw, played_ms=0)
        finally:
            if self._barge_in_event is not None:
                self._barge_in_event.clear()

    async def _play_validated_response(self, event) -> None:
        """Only approved, complete responses reach the shared audio transport."""
        from app.utils.audio_utils import ulaw_to_pcm, resample_audio
        utterance = self._utterance
        if utterance is None:  # direct callers/tests still get an owned utterance
            self._playback_generation += 1
            utterance = self._utterance = {"id": f"rt-{self._playback_generation}",
                "generation": self._playback_generation, "raw": event.raw or {}, "text": event.text or "",
                "status": "validated", "receipt": None}
        receipt = {"utterance_id": utterance["id"], "status": "unknown", "evidence": "unknown", "played_ms": 0}
        started = time.monotonic()
        try:
            if await self._invalidate_contradicted_playback(utterance):
                return
            self._action_session._voice_action_delivered_text = ""
            await self._send_control_event({"type": "llm_response", "text": event.text})
            if not self._owns_unfinished_playback(utterance):
                return
            begin = getattr(self._gw, "begin_playback", None)
            if callable(begin):
                await begin(self._call_id, utterance["id"])
            else:
                start = getattr(self._gw, "start_playback_tracking", None)
                if callable(start):
                    start(self._call_id)
            if not self._owns_unfinished_playback(utterance):
                return
            utterance["status"] = "playing"
            audio = event.audio or b""
            for offset in range(0, len(audio), 320):
                if self._stop.is_set() or not self._session_active():
                    return
                # Admission precedes each transport submission; ASR may finish
                # while begin_playback/send_audio is awaiting the gateway.
                if await self._invalidate_contradicted_playback(utterance):
                    return
                pcm = ulaw_to_pcm(audio[offset:offset + 320])
                if self._internal_rate != _WIRE_RATE:
                    pcm = resample_audio(pcm, from_rate=_WIRE_RATE, to_rate=self._internal_rate,
                                         channels=1, bit_depth=16, res_type="soxr_mq")
                utterance["submission_started"] = True
                await self._gw.send_audio(self._call_id, pcm)
                if not self._owns_unfinished_playback(utterance):
                    return
                if offset == 0:
                    logger.info("realtime_playout call=%s utterance=%s validation_to_first_submission_ms=%d",
                                self._call_id, utterance["id"], (time.monotonic() - started) * 1000)
            flush = getattr(self._gw, "flush_audio_buffer", None) or getattr(self._gw, "flush_tts_buffer", None)
            if callable(flush):
                await flush(self._call_id)
            if not self._owns_unfinished_playback(utterance):
                return
            finish = getattr(self._gw, "finish_playback", None)
            if callable(finish):
                receipt = await finish(self._call_id, utterance["id"])
            else:
                await self._send_control_event({"type": "tts_audio_complete"})
                if not self._owns_unfinished_playback(utterance):
                    return
                # Legacy transports cannot prove which utterance they completed.
                wait = getattr(self._gw, "wait_for_playback_complete", None)
                if callable(wait):
                    await wait(self._call_id)
            if not self._owns_unfinished_playback(utterance):
                return
            utterance["receipt"] = receipt
            self._record_turn("assistant", event.text, metadata={"delivery": receipt})
            utterance["transcript_recorded"] = True
            # Generated text and queue acceptance are not contact confirmation.
            # Only a transport with explicit playback acknowledgement may advance
            # the contact readback state; other transports retain pending details.
            if (receipt.get("utterance_id") == utterance["id"]
                    and receipt.get("status") == "completed"
                    and receipt.get("evidence") == "transport_played"):
                from app.domain.services.voice_pipeline.contact_capture import ContactReadback
                self._last_contact_readback = ContactReadback(utterance_id=utterance["id"])
                utterance["status"] = "completed"
                self._observe_contact_agent_turn(event.text)
                self._remember_contact_turn("assistant", event.text)
                self._action_session._voice_action_delivered_text = event.text
                if self._identity_opening_pending:
                    self._live_state = reduce_live_state(self._live_state, IdentityEvidence(introduced=True))
                    self._identity_opening_pending = False
                    await self._publish_live_state()
            else:
                utterance["status"] = receipt.get("status", "unknown")
            if self._utterance is not utterance or utterance.get("status") == "interrupted":
                return
            if (self._closing_after_generation is not None
                    and utterance["generation"] > self._closing_after_generation):
                self._goodbye_completed.set()
            await self._send_control_event({"type": "turn_complete"})
        except asyncio.CancelledError:
            if utterance.get("status") != "completed":
                utterance["status"] = "interrupted"
            raise
        except Exception:
            logger.exception("realtime_playout_failed call=%s", self._call_id)
            await self._rt.close()
        finally:
            # A gateway may swallow task cancellation after accepting bytes.
            # Retain uncertain partial delivery even when that await returns.
            if (utterance.get("status") == "interrupted"
                    and utterance.get("submission_started")
                    and not utterance.get("transcript_recorded")):
                self._record_turn("assistant", event.text, metadata={"delivery": {
                    "utterance_id": utterance["id"], "status": "interrupted", "evidence": "unknown"}})
                utterance["transcript_recorded"] = True

    def _remember_contact_turn(self, role: str, text: str) -> None:
        from app.domain.models.conversation import Message, MessageRole

        self._contact_history.append(
            Message(
                role=(
                    MessageRole.ASSISTANT
                    if role == "assistant"
                    else MessageRole.USER
                ),
                content=text,
            )
        )
        # The read-back gate only needs the latest few turns; bound retained
        # contact-bearing text independently of transcript persistence.
        if len(self._contact_history) > 12:
            del self._contact_history[:-12]

    def _observe_contact_agent_turn(self, text: str) -> None:
        from app.services.scripts.call_state_tracker import (
            CallState,
            update_state_from_agent_turn,
        )

        slots = getattr(self._contact_session, "captured_slots", None)
        if slots is None or not is_dataclass(slots):
            slots = CallState()
        self._contact_session.captured_slots = update_state_from_agent_turn(
            slots,
            text,
        )

    @staticmethod
    def _contact_evidence(raw: Any) -> tuple[Optional[float], tuple[str, ...], bool]:
        if not isinstance(raw, dict):
            return None, (), False
        confidence = raw.get("confidence")
        alternatives_raw = raw.get("alternatives") or raw.get(
            "transcript_alternatives"
        ) or ()
        alternatives: list[str] = []
        if isinstance(alternatives_raw, (list, tuple)):
            for item in alternatives_raw:
                value = (
                    item.get("transcript") or item.get("text")
                    if isinstance(item, dict)
                    else item
                )
                if value:
                    alternatives.append(str(value))
        try:
            parsed_confidence = float(confidence) if confidence is not None else None
        except (TypeError, ValueError):
            parsed_confidence = None
        return (
            parsed_confidence,
            tuple(alternatives),
            bool(raw.get("contact_reask")),
        )

    async def _observe_contact_turn(self, text: str, raw: Any = None, *, revision: bool = False) -> None:
        """Run the canonical contact machine on one realtime caller final."""
        from app.services.scripts.call_state_tracker import (
            CallState,
            _classify_core_confirmation,
            update_state_from_user_turn,
        )
        from app.domain.services.voice_pipeline.turn_runner import (
            _agent_read_back_phone,
            _is_email_correction,
            _is_phone_correction,
            email_on_the_table,
        )
        from app.domain.services.voice_pipeline.contact_capture import (
            ContactSource, ContactCaptureState, CaptureStatus, bind_contact_evidence,
        )

        slots = getattr(self._contact_session, "captured_slots", None)
        if slots is None or not is_dataclass(slots):
            slots = CallState()
        before_signature = self._contact_state_signature(slots)
        current_slots = slots
        history = self._contact_history
        readback_receipt = self._last_contact_readback
        source = None
        if self._caller_item_id(raw) == self._current_caller_item_key and self._current_caller_item_key is not None:
            source = ContactSource(self._current_caller_item_key, self._current_caller_order,
                hashlib.sha256(text.strip().encode("utf-8")).hexdigest())
        if revision:
            slots = self._current_contact_baseline or CallState()
            history = self._current_contact_history
            readback_receipt = self._current_contact_readback
        baseline = slots

        # Same rule as the cascaded path (turn_runner.email_on_the_table): the
        # agent's LATEST read-back is the value the caller is answering.
        slots, email_readback = email_on_the_table(slots, text, history)
        pending_email = getattr(slots, "email", None)

        pending_phone = getattr(slots, "phone", None)
        phone_readback = _agent_read_back_phone(
            history, pending_phone
        )
        email_gate = bool(
            pending_email
            and not getattr(slots, "email_confirmed", False)
            and email_readback
            and not _is_email_correction(text, pending_email)
        )
        phone_gate = bool(
            pending_phone
            and not getattr(slots, "phone_confirmed", False)
            and phone_readback
            and not _is_phone_correction(text, pending_phone)
        )
        confidence, alternatives, explicit_reask = self._contact_evidence(raw)
        updated = update_state_from_user_turn(
            slots,
            text,
            readback_issued=email_readback,
            confirmation_verdict=(
                _classify_core_confirmation(text) if email_gate else None
            ),
            phone_readback_issued=phone_readback,
            phone_confirmation_verdict=(
                _classify_core_confirmation(text) if phone_gate else None
            ),
            phone_region=self._contact_phone_region,
            independent_confirmation_value=pending_email,
            phone_independent_confirmation_value=pending_phone,
            transcript_confidence=confidence,
            transcript_alternatives=alternatives,
            explicit_contact_reask=explicit_reask,
        )
        updated = bind_contact_evidence(baseline, updated, source, readback=readback_receipt)
        if revision:
            # Re-evaluate only the current ASR item's contribution against its
            # original context. Never restore the whole old CallState: another
            # contact/tool/manual producer may have changed unrelated fields.
            changes = {}
            owned_kinds = set()
            prior_result = self._current_contact_result or baseline
            for kind in ("email", "phone"):
                previous = getattr(prior_result, f"{kind}_capture", None)
                owned_fields = (f"{kind}_capture", kind, f"{kind}_confirmed",
                                f"{kind}_readback_attempts", f"earlier_{kind}_captures")
                if any(getattr(current_slots, name, None) != getattr(prior_result, name, None)
                       for name in owned_fields):
                    continue
                # Equality with the result we actually observed proves this
                # field has no intervening independent writer, including a
                # result with no contact where corrected ASR introduces one.
                candidate = getattr(updated, f"{kind}_capture", None)
                if candidate is None and previous is not None:
                    candidate = ContactCaptureState(kind=kind, status=CaptureStatus.NEEDS_CLARIFICATION,
                        value_source=source, status_source=source,
                        clarification_prompt="Please state that contact detail again.")
                changes[f"{kind}_capture"] = candidate
                changes[kind] = candidate.normalized_value if candidate else None
                changes[f"{kind}_confirmed"] = bool(candidate and candidate.status is CaptureStatus.CONFIRMED)
                changes[f"{kind}_readback_attempts"] = candidate.attempts if candidate else 0
                # Additional contacts are part of this field only; an unrelated
                # current capture does not authorize reverting their sequence.
                previous_earlier = getattr(prior_result, f"earlier_{kind}_captures", ())
                if getattr(current_slots, f"earlier_{kind}_captures", ()) == previous_earlier:
                    changes[f"earlier_{kind}_captures"] = getattr(updated, f"earlier_{kind}_captures", ())
                owned_kinds.add(kind)
            if getattr(current_slots, "active_contact_kind", None) in owned_kinds or current_slots.active_contact_kind is None:
                changes["active_contact_kind"] = updated.active_contact_kind
            if current_slots.contact_capture_paused == prior_result.contact_capture_paused:
                changes["contact_capture_paused"] = updated.contact_capture_paused
            updated = replace(current_slots, **changes)
            for kind in {"email", "phone"} - owned_kinds:
                # CallState's compatibility constructor lifts bare scalars.
                # A revision must preserve an independent producer's exact
                # evidence shape, not manufacture structured confirmation.
                object.__setattr__(updated, f"{kind}_capture", getattr(current_slots, f"{kind}_capture", None))
            self._current_contact_result = replace(prior_result, **changes)
            self._contact_session._lead_capture_revision_source = source
            # Replace, rather than append, this caller entry in the short
            # readback history; it is the same caller turn.
            for index in range(len(self._contact_history) - 1, -1, -1):
                item = self._contact_history[index]
                if str(getattr(item.role, "value", item.role)) == "user":
                    self._contact_history[index] = item.model_copy(update={"content": text})
                    break
        self._contact_session.captured_slots = updated
        previous_live_state = self._live_state
        email_changed = not revision or (current_slots.email, current_slots.email_confirmed) != (updated.email, updated.email_confirmed)
        phone_changed = not revision or (current_slots.phone, current_slots.phone_confirmed) != (updated.phone, updated.phone_confirmed)
        self._live_state = reduce_live_state(
            self._live_state,
            ConfirmedContactsEvidence(
                email=updated.email if email_changed else self._live_state.confirmed_email,
                email_confirmed=bool(updated.email_confirmed) if email_changed else bool(self._live_state.confirmed_email),
                phone=updated.phone if phone_changed else self._live_state.confirmed_phone,
                phone_confirmed=bool(updated.phone_confirmed) if phone_changed else bool(self._live_state.confirmed_phone),
            ),
        )
        if self._live_state != previous_live_state:
            # Publish the canonical confirmation snapshot before any backend
            # contact directive can trigger the provider's next response.
            await self._publish_live_state()
        contact_changed = self._contact_state_signature(updated) != before_signature
        if contact_changed:
            from app.domain.services.voice_pipeline.contact_capture import (
                CaptureStatus,
                capture_mode_directive,
            )

            directive_captures = []
            for resolved_kind in ("email", "phone"):
                candidate = getattr(updated, f"{resolved_kind}_capture", None)
                prior = getattr(current_slots, f"{resolved_kind}_capture", None)
                if (
                    candidate is not None
                    and candidate != prior
                    and candidate.status
                    in {CaptureStatus.CONFIRMED, CaptureStatus.CANCELLED}
                ):
                    directive_captures.append(candidate)

            active_kind = getattr(updated, "active_contact_kind", None)
            active_capture = getattr(updated, f"{active_kind}_capture", None)
            paused = bool(getattr(updated, "contact_capture_paused", False))
            if not paused and active_capture is not None and active_capture not in directive_captures:
                directive_captures.append(active_capture)

            directives = [
                directive
                for capture in directive_captures
                if (directive := capture_mode_directive(capture))
            ]
            if getattr(self._gw, "playback_evidence", None) == "transmitted":
                directives = [d.replace("ask for a clear yes or no", "ask the caller to say 'yes' and repeat the complete value, including the country code for a phone number") for d in directives]
            if paused:
                directives.append(
                    "The caller paused or declined contact confirmation. Do not ask for or read back "
                    "contact details unless the caller explicitly offers or resumes them. Retained "
                    "candidates remain unconfirmed. Respect the caller's goodbye."
                )
            if directives:
                # One provider interruption, ordered resolution first. This
                # retires stale persistent system items before advancing to a
                # second contact field without issuing two cancel requests.
                await self._enforce_contact_directive("\n".join(directives))
        self._schedule_contact_persist(force=contact_changed)

    @staticmethod
    def _contact_state_signature(slots: Any) -> tuple:
        def one(name: str) -> tuple:
            capture = getattr(slots, f"{name}_capture", None)
            if capture is None:
                return ()
            return (
                capture.status,
                capture.normalized_value,
                capture.attempts,
                capture.clarification_prompt,
                capture.value_source,
                capture.confirmation_source,
                capture.status_source,
                capture.readback,
            )

        return (getattr(slots, "active_contact_kind", None),
                bool(getattr(slots, "contact_capture_paused", False)), one("email"), one("phone"))

    async def _enforce_contact_directive(self, directive: str) -> None:
        """Replace the provider's speculative reply with backend-owned mode."""
        sender = getattr(self._rt, "interrupt_with_text", None)
        if not callable(sender):
            logger.warning(
                "realtime_contact_directive_unsupported call=%s",
                self._call_id[:12],
            )
            return
        await self._cancel_playback()
        await sender(directive)

    def _schedule_contact_persist(self, *, force: bool = False) -> None:
        if self._knowledge_pool is None:
            return
        from app.domain.services.voice_pipeline.lead_slot_capture import (
            capture_turn_slots,
            pending_contact_revocations,
            snapshot_slots,
        )

        # Do not enqueue a task for an unconfirmed email/phone. Besides saving a
        # DB scheduling hop, this keeps pending contact data in memory only.
        if (
            not force
            and not snapshot_slots(
                getattr(self._contact_session, "captured_slots", None)
            )
            and not pending_contact_revocations(self._contact_session)
        ):
            return

        previous = self._contact_persist_tail

        async def persist_in_order() -> None:
            # Same-source contact upserts are intentionally serialized. Without
            # this tail, an older confirmed value can finish after a corrected
            # value and overwrite it in the audit row.
            if previous is not None:
                try:
                    await asyncio.shield(previous)
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 - next snapshot still retries
                    pass
            await capture_turn_slots(
                self._contact_session,
                pool=self._knowledge_pool,
                reason="realtime_turn",
            )

        task = asyncio.create_task(
            persist_in_order(),
            name=f"rt-contact-{self._call_id}",
        )
        self._contact_persist_tail = task
        self._contact_tasks.add(task)

        def retire(done: asyncio.Task) -> None:
            self._contact_tasks.discard(done)
            if self._contact_persist_tail is done:
                self._contact_persist_tail = None

        task.add_done_callback(retire)

    def _record_caller_revision(self, caller_order: int, text: str) -> None:
        annotate = getattr(self._transcript_service, "annotate_turn_revision", None)
        if not callable(annotate) or self._current_caller_transcript_index is None:
            return
        try:
            annotate(self._call_id, turn_index=self._current_caller_transcript_index,
                     provider_item_id=self._current_caller_item_key,
                     caller_turn_order=caller_order, content=text)
            self._schedule_transcript_flush()
        except Exception as exc:  # evidence must not break the active call
            logger.debug("realtime caller revision record failed error_type=%s", type(exc).__name__)

    def _record_turn(self, role: str, text: str, *, metadata=None) -> Optional[int]:
        """Accumulate one finalised transcript turn (role-tagged, in order) into
        the shared TranscriptService buffer. Fail-soft: a transcript error must
        never break the call, so everything here is swallowed."""
        if self._transcript_service is None:
            return
        try:
            recorded = self._transcript_service.accumulate_turn(
                self._call_id,
                role,
                text,
                talklee_call_id=self._talklee_call_id,
                turn_index=self._turn_index,
                is_final=True if role == "user" else None,
                metadata=metadata,
            )
            if recorded is None:
                return None
            self._turn_index += 1
            self._schedule_transcript_flush()
            return self._turn_index - 1
        except Exception as exc:  # noqa: BLE001 — transcript must never break a call
            logger.debug("realtime_bridge record_turn err call=%s: %s",
                         self._call_id, exc)

    def _schedule_transcript_flush(self) -> None:
        """Coalesce accepted native evidence onto the existing durable writer.

        A single task reads the latest canonical snapshot; no stale captured
        payload can win after a revision. Failed/no-bound writes remain memory
        only and must never be advertised as crash-recoverable evidence.
        """
        flush = getattr(self._transcript_service, "flush_to_database", None)
        if self._knowledge_pool is None or not callable(flush):
            return
        from app.domain.services.voice_pipeline.lead_slot_capture import resolve_call_binding, _as_uuid
        binding = resolve_call_binding(self._contact_session)
        target = _as_uuid(binding.get("call_id") or self._talklee_call_id)
        tenant = _as_uuid(binding.get("tenant_id") or self._tenant_id)
        if not target or not tenant:
            return
        self._transcript_flush_pending = True
        if self._transcript_flush_task is not None and not self._transcript_flush_task.done():
            return

        async def persist_latest():
            while self._transcript_flush_pending:
                self._transcript_flush_pending = False
                try:
                    await flush(self._call_id, db_pool=self._knowledge_pool,
                                tenant_id=tenant, target_call_id=target)
                except Exception as exc:
                    logger.warning("realtime transcript flush failed error_type=%s", type(exc).__name__)

        self._transcript_flush_task = asyncio.create_task(persist_latest(), name=f"rt-transcript-{self._call_id}")

    async def _publish_live_state(self) -> None:
        """Replace the persistent realtime state block; never break audio."""
        publish = getattr(self._rt, "update_live_state", None)
        if not callable(publish):
            logger.warning(
                "realtime_bridge live state unavailable call=%s", self._call_id
            )
            return
        try:
            block = render_live_state_block(
                self._live_state, opening_interrupted=self._opening_interrupted)
            await publish(block)
        except Exception as exc:  # noqa: BLE001 - state steering is fail-soft
            logger.warning(
                "realtime_bridge live-state publish err call=%s: %s",
                self._call_id,
                exc,
            )

    async def _send_tool_result_after_playback(self, call_id, result, *, knowledge_lookup_sequence=None):
        # A lookup may finish while its spoken hold is still playing. Wait in
        # the detached tool task, never in the event pump, to prevent overlap.
        playback = self._playback_task
        if playback is not None and not playback.done():
            await asyncio.gather(playback, return_exceptions=True)
        if not self._stop.is_set():
            if (knowledge_lookup_sequence is not None
                    and knowledge_lookup_sequence != self._knowledge_lookup_seq):
                result = {**result, "status": "superseded", "text": _SUPERSEDED_KB_INFO, "sources": []}
            await self._rt.send_function_result(call_id, result)

    async def _handle_function_call(self, fc: Any) -> None:
        """Fulfil knowledge and deterministic action tools. Never raises."""
        try:
            if fc.name == "knowledge_lookup":
                query = fc.parsed_arguments().get("query", "")
                result = await self._lookup_knowledge(query)
                if result["status"] != "superseded":
                    self._live_state = reduce_live_state(
                        self._live_state,
                        ToolResultEvidence(
                            tool_name="knowledge_lookup",
                            success=result["status"] == "matched",
                            code=result["status"],
                        ),
                    )
                # Publish before send_function_result triggers the continuation,
                # so that response sees the deterministic tool outcome too.
                await self._publish_live_state()
                await self._send_tool_result_after_playback(
                    fc.call_id, result, knowledge_lookup_sequence=result.get("lookup_sequence"))
            else:
                from app.domain.services.voice_pipeline.action_tools import (
                    ACTION_END_CALL,
                    VOICE_ACTION_NAMES,
                    run_voice_action,
                )

                if fc.name not in VOICE_ACTION_NAMES:
                    self._live_state = reduce_live_state(
                        self._live_state,
                        ToolResultEvidence(
                            tool_name=str(fc.name or "unknown_tool"),
                            success=False,
                            code="unknown_tool",
                        ),
                    )
                    await self._publish_live_state()
                    await self._send_tool_result_after_playback(
                        fc.call_id, {"error": f"unknown tool {fc.name}"}
                    )
                    return

                caller_revision = self._caller_activity_revision
                result = await run_voice_action(
                    self._action_session,
                    fc.name,
                    fc.parsed_arguments(),
                    user_text=("" if fc.name == ACTION_END_CALL and self._caller_transcript_pending
                               else self._latest_caller_text),
                    previous_assistant_text=self._previous_assistant_text,
                )
                if fc.name == ACTION_END_CALL and result["success"]:
                    if caller_revision != self._caller_activity_revision or not self._arm_caller_end_call():
                        # Keep the shared action evidence consistent with a
                        # caller who resumed while the tool was in flight.
                        result = await run_voice_action(self._action_session, ACTION_END_CALL, user_text="")
                self._live_state = reduce_live_state(
                    self._live_state,
                    ToolResultEvidence(
                        tool_name=str(result.get("action") or fc.name),
                        success=bool(result.get("success")),
                        code=str(result.get("status") or "execution_error"),
                    ),
                )
                # The provider continues as soon as the function result lands;
                # publish its evidence first so the same response sees it.
                await self._publish_live_state()
                # Close the tool round-trip before any completion claim or
                # end-call side effect. send_function_result is awaited, so the
                # result is on the provider wire before execution continues.
                await self._send_tool_result_after_playback(fc.call_id, result)
        except Exception as exc:  # noqa: BLE001
            logger.debug("realtime_bridge function-call err call=%s: %s",
                         self._call_id, exc)
            self._live_state = reduce_live_state(
                self._live_state,
                ToolResultEvidence(
                    tool_name=str(getattr(fc, "name", None) or "unknown_tool"),
                    success=False,
                    code="execution_error",
                ),
            )
            await self._publish_live_state()
            try:
                from app.domain.services.voice_pipeline.action_tools import (
                    VOICE_ACTION_NAMES,
                    execution_failure_result,
                )

                fallback = (
                    execution_failure_result(self._action_session, fc.name)
                    if getattr(fc, "name", None) in VOICE_ACTION_NAMES
                    else {"error": "lookup failed"}
                )
                await self._send_tool_result_after_playback(
                    fc.call_id, fallback
                )
            except Exception:  # noqa: BLE001
                pass

    def _snapshot_caller_question(self):
        # Take the boundary at speech start when available: provider output
        # can arrive before delayed final transcription of that same reply.
        self._previous_assistant_text = next((
            message.content for message in reversed(self._contact_history)
            if getattr(message.role, "value", message.role) == "assistant"
        ), "")

    def _revoke_pending_end_call(self, *, awaiting_transcript=False):
        # Once transport termination has started it is an external effect; do
        # not cancel its coroutine halfway through sending the request.
        if self._hangup_started:
            return
        self._caller_activity_revision += 1
        self._caller_transcript_pending = awaiting_transcript
        self._pending_end_call_revision = None
        self._closing_after_generation = None
        self._goodbye_completed.clear()
        self._action_session._end_call_requested = False
        task, self._termination_task = self._termination_task, None
        if task is not None and not task.done():
            task.cancel()

    def _record_caller_opt_out(self, text: str) -> bool:
        from app.domain.services.voice_pipeline.identity_disposition import contains_dnc
        opted_out = contains_dnc(text)
        if opted_out:
            self._action_session._caller_opted_out = True
            # DNC removes future calling permission independently of whether
            # the caller wants this conversation to continue. Coalesce writes;
            # never block the sole model/audio event pump on database I/O.
            if self._opt_out_task is None or self._opt_out_task.done():
                self._opt_out_task = asyncio.create_task(self._persist_caller_opt_out(),
                    name=f"rt-opt-out-{self._call_id}")
        return opted_out

    async def _persist_caller_opt_out(self):
        from app.domain.services.dialer.opt_out import purge_opt_out_before_farewell
        # The bridge's call identity is also valid when no CallSession was
        # supplied (older construction sites); it grants no tenant authority.
        from types import SimpleNamespace
        session = (self._action_session if getattr(self._action_session, "call_id", None)
                   else SimpleNamespace(call_id=self._call_id))
        return await purge_opt_out_before_farewell(session)

    def _arm_caller_end_call(self, *, require_explicit=False):
        from app.domain.services.end_session_action import caller_signaled_end
        from app.domain.services.voice_pipeline.identity_disposition import contains_explicit_goodbye
        if self._caller_transcript_pending or self._hangup_started:
            return False
        # DNC survives a canceled immediate hangup. Persistence starts in-call;
        # shared telephony teardown retries any unacknowledged cleanup.
        opted_out = self._record_caller_opt_out(self._latest_caller_text)
        if not caller_signaled_end(self._latest_caller_text, previous_assistant_text=self._previous_assistant_text):
            return False
        # A topic-level "no thanks" is not an automatic instruction to hang
        # up. The transcript-only fallback requires an explicit close or DNC.
        if require_explicit and not (opted_out or contains_explicit_goodbye(self._latest_caller_text)):
            return False
        if self._pending_end_call_revision == self._caller_activity_revision:
            return True
        self._pending_end_call_revision = self._caller_activity_revision
        self._closing_after_generation = self._playback_generation
        self._goodbye_completed.clear()
        self._action_session._end_call_requested = True
        self._termination_task = asyncio.create_task(
            self._finish_end_call(self._caller_activity_revision))
        return True

    async def _finish_end_call(self, caller_revision):
        try:
            await asyncio.wait_for(self._goodbye_completed.wait(), timeout=15.0)
        except asyncio.TimeoutError:
            logger.warning("realtime_goodbye_timeout call=%s", self._call_id)
        if self._opt_out_task is not None:
            # Its helper has a bounded DB timeout. Shield it from a caller's
            # interruption cancelling this close; DNC remains monotonic.
            await asyncio.shield(self._opt_out_task)
        from app.domain.services.end_session_action import caller_signaled_end
        if (self._caller_transcript_pending
                or caller_revision != self._pending_end_call_revision
                or caller_revision != self._caller_activity_revision
                or not caller_signaled_end(self._latest_caller_text, previous_assistant_text=self._previous_assistant_text)):
            return
        self._hangup_started = True
        if self._on_end_call is not None:
            await self._on_end_call()
        else:
            hangup = getattr(self._gw, "hangup_call", None)
            if not callable(hangup):
                self._failure_reason = "Call transport has no termination capability"
            else:
                await hangup(self._call_id, "agent_end_call")
        self._stop.set()
        await self._rt.close()

    async def _lookup_knowledge(self, query: str) -> dict:
        """Top-k campaign-knowledge nodes rendered for the voice model. Returns
        explicit evidence status, fenced data and source references. Reuses
        retrieve_knowledge — the cascaded per-turn retrieval.

        SECURITY: what this returns lands in a ``function_call_output`` item,
        which can appear authoritative to the model. Its contents are still
        untrusted tenant/third-party data, so they receive
        the SAME defenses the cascaded inject path applies
        (``turn_streamer._knowledge_block_for_turn``): per-node
        ``scan_for_injection`` drops a poisoned node, and ``fence_untrusted`` +
        ``DATA_ONLY_NOTE`` delimit what survives. The note is carried INLINE here
        (unlike the cascaded tool path, which puts it in its system addendum)
        because this bridge does not author the realtime session instructions —
        see app/realtime/prompts.py — so the result must be
        self-framing.

        BOUNDED: retrieval is capped by the shared per-turn budget. Without it a
        saturated pool left the caller on an open-ended "let me check" hold with
        no reply ever arriving.
        """
        self._knowledge_lookup_seq = getattr(self, "_knowledge_lookup_seq", 0) + 1
        lookup_seq = self._knowledge_lookup_seq
        self._verified_knowledge = []
        self._knowledge_evidence = {"status": "unavailable", "passages": [], "text": ""}
        pinned_nodes = self._knowledge_snapshot_nodes
        source_policy = "admission_snapshot" if pinned_nodes is not None else "current_lookup"

        def finish(status, text, evidence=None):
            if lookup_seq != self._knowledge_lookup_seq:
                return {"status": "superseded", "text": _SUPERSEDED_KB_INFO,
                        "sources": [], "source_policy": source_policy, "lookup_sequence": lookup_seq}
            evidence = evidence or {"status": status, "passages": [], "text": ""}
            self._knowledge_evidence = evidence
            self._verified_knowledge = [p["text"] for p in evidence["passages"]] if status == "matched" else []
            source_keys = ("node_id", "version", "coverage", "source_id", "source_version", "updated_at")
            sources = [{key: passage[key] for key in source_keys if key in passage}
                       for passage in evidence["passages"]]
            # Query, headings and body text can contain private information.
            # Trace status and source identity without logging their content.
            digest = hashlib.sha256(json.dumps(sources, sort_keys=True, default=str).encode()).hexdigest()
            logger.info("realtime_kb_evidence call=%s %s", self._call_id, json.dumps({
                "status": status, "policy": source_policy, "passages": len(sources),
                "sources_sha256": digest,
            }, sort_keys=True))
            return {"status": status, "text": text, "sources": sources,
                    "source_policy": source_policy, "lookup_sequence": lookup_seq}

        if not isinstance(query, str) or not query.strip() or not self._campaign_id:
            return finish("no_match", _NO_KB_INFO)
        query = query.strip()
        # SECURITY — fail closed (issue #5): a missing/empty tenant must NEVER
        # reach retrieve_knowledge. acquire_with_tenant treats tenant_id=None as
        # an RLS BYPASS (app.bypass_rls='on'), so a tenantless realtime session
        # would read ACROSS tenants. Without a validated tenant we decline the
        # lookup entirely rather than risk cross-tenant KB exposure — we do NOT
        # pass None through to get a bypass.
        tenant_id = (self._tenant_id or "").strip()
        if pinned_nodes is None and not self._knowledge_pool:
            return finish("unavailable", "Company knowledge is unavailable. I cannot confirm that detail.")
        if pinned_nodes is None and not tenant_id:
            logger.warning(
                "realtime_bridge KB lookup BLOCKED — no tenant on session call=%s "
                "(refusing RLS-bypass cross-tenant read)", self._call_id,
            )
            return finish("unavailable", "Company knowledge is unavailable. I cannot confirm that detail.")
        from app.domain.services.voice_pipeline.kb_budget import (
            _KNOWLEDGE_RETRIEVE_TIMEOUT_S,
        )
        from app.domain.services.voice_pipeline.knowledge_tool import (
            fence_kb_result,
        )

        try:
            from app.services.scripts.knowledge.retrieval import (
                retrieve_pinned_knowledge,
                retrieve_knowledge,
            )
            if pinned_nodes is not None:
                nodes = retrieve_pinned_knowledge(pinned_nodes, query, k=_REALTIME_KB_K)
            else:
                nodes = await asyncio.wait_for(
                    retrieve_knowledge(
                        self._knowledge_pool,
                        tenant_id=tenant_id,
                        campaign_id=self._campaign_id,
                        query=query,
                        k=_REALTIME_KB_K,
                        bump_hits=False,
                        raise_on_error=True,
                    ),
                    # The SHARED per-turn budget (kb_budget), the same one the inject
                    # path and the cascaded tool path use — not a new number. On
                    # expiry wait_for cancels the retrieval, which unwinds the pool
                    # acquire too, so a saturated pool can't hold the turn open.
                    timeout=_KNOWLEDGE_RETRIEVE_TIMEOUT_S,
                )
        except asyncio.TimeoutError:
            # Match the other two paths: on timeout the turn proceeds WITHOUT
            # facts. Returning the no-info sentinel (rather than nothing) keeps
            # the tool round-trip closed so the model speaks instead of leaving
            # the caller on an open hold, and gives it nothing to invent from.
            logger.warning(
                "realtime_bridge KB lookup TIMEOUT >%.0fms call=%s — answering "
                "without facts", _KNOWLEDGE_RETRIEVE_TIMEOUT_S * 1000, self._call_id,
            )
            return finish("unavailable", "Company knowledge is temporarily unavailable. I cannot confirm that detail.")
        except Exception as exc:  # noqa: BLE001
            logger.debug("realtime_bridge knowledge lookup failed error_type=%s", type(exc).__name__)
            return finish("unavailable", "Company knowledge is temporarily unavailable. I cannot confirm that detail.")
        logger.info("realtime_kb_lookup call=%s query_chars=%d hits=%d",
                    self._call_id, len(query), len(nodes or []))
        if not nodes:
            return finish("no_match", _NO_KB_INFO)
        from app.domain.services.voice_pipeline.kb_budget import prepare_knowledge_evidence
        evidence = prepare_knowledge_evidence(nodes, query,
            chunk_chars=_REALTIME_NODE_CHARS, total_chars=_REALTIME_NODE_CHARS * 2)
        if evidence["status"] != "matched":
            return finish(evidence["status"], _NO_KB_INFO, evidence)
        return finish("matched", fence_kb_result(evidence["text"], with_note=True), evidence)
