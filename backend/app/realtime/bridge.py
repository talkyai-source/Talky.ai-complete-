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
import inspect
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

# Bounded "no facts" tool result — returned for no hits, a blocked (tenantless)
# lookup, a retrieve timeout, and when every node was dropped by the injection
# scan. Always closing the tool round-trip with a short, truthful string is what
# keeps the caller off an open-ended hold and leaves the model nothing to invent.
_NO_KB_INFO = "I don't have specific information on that."


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
        self._call_direction = str(call_direction or "outbound").strip().lower()
        # Shared CallSession for deterministic voice-action results. Optional so
        # older construction sites/tests remain compatible; the bridge itself
        # is a safe in-memory fallback for fail-closed unavailable results.
        self._action_session = action_session or self
        self._latest_caller_text = ""
        self._playback_task = None
        self._repair_attempted = False
        self._failure_reason = None
        self._verified_knowledge = []
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
        if self._contact_tasks:
            _done, pending = await asyncio.wait(
                set(self._contact_tasks), timeout=2.0
            )
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
        self._contact_tasks.clear()
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

    # ── Model events: OpenAI -> gateway (+ tools, barge-in) ──────────────
    async def _pump_model_events(self) -> None:
        from app.utils.audio_utils import resample_audio, ulaw_to_pcm

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
                    if contradicted_customer_claim(ev.text or "", self._contact_history):
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
                        "raw": ev.raw or {}, "status": "validated", "receipt": None,
                    }
                    self._playback_task = asyncio.create_task(self._play_validated_response(ev))

                elif kind == "interrupted":
                    # Revoke the timer immediately, before ASR supplies the new
                    # words. A delayed tool from the old turn cannot rearm it.
                    self._revoke_pending_end_call(awaiting_transcript=True)
                    await self._cancel_playback(getattr(ev, "raw", None))
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

                elif kind == "caller_transcript" and ev.text:
                    # Contact values are high-risk transcript content. Log only
                    # event shape, never the raw caller text.
                    logger.debug(
                        "realtime caller transcript call=%s chars=%d final=%s",
                        self._call_id[:12], len(ev.text),
                        bool(getattr(ev, "is_final", False)),
                    )
                    if getattr(ev, "is_final", False):
                        self._revoke_pending_end_call()
                        self._repair_attempted = False
                        self._latest_caller_text = ev.text
                        self._live_user_turn_seq += 1
                        self._action_session._voice_action_user_turn = self._live_user_turn_seq
                        # The caller owns close/DNC intent even when the model
                        # produces only a plain goodbye and omits its tool.
                        self._arm_caller_end_call(require_explicit=True)
                        evidence = evidence_from_transcript(
                            role="user",
                            text=ev.text,
                            turn_id=f"realtime:{self._live_user_turn_seq}",
                        )
                        if evidence is not None:
                            self._live_state = reduce_live_state(
                                self._live_state, evidence
                            )
                            await self._publish_live_state()
                        _tidx = self._turn_index
                        await self._observe_contact_turn(ev.text, getattr(ev, "raw", None))
                        self._remember_contact_turn("user", ev.text)
                        self._record_turn("user", ev.text)
                        # Real-time voicemail detection on the opening turn(s):
                        # if the callee is an answering machine, hang up now and
                        # end the pump — no message, no conversation.
                        if self._call_direction != "inbound" and _tidx <= 1:
                            from app.domain.services.voice_pipeline.voicemail_detector import (
                                detect_and_hang_up_voicemail,
                            )
                            if await detect_and_hang_up_voicemail(
                                self._call_id, ev.text, _tidx
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
        if task is not None and not task.done():
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
                "generation": self._playback_generation, "raw": event.raw or {},
                "status": "validated", "receipt": None}
        receipt = {"utterance_id": utterance["id"], "status": "unknown", "evidence": "unknown", "played_ms": 0}
        started = time.monotonic()
        try:
            self._action_session._voice_action_delivered_text = ""
            await self._send_control_event({"type": "llm_response", "text": event.text})
            begin = getattr(self._gw, "begin_playback", None)
            if callable(begin):
                await begin(self._call_id, utterance["id"])
            else:
                start = getattr(self._gw, "start_playback_tracking", None)
                if callable(start):
                    start(self._call_id)
            utterance["status"] = "playing"
            audio = event.audio or b""
            for offset in range(0, len(audio), 320):
                if self._stop.is_set() or not self._session_active():
                    return
                pcm = ulaw_to_pcm(audio[offset:offset + 320])
                if self._internal_rate != _WIRE_RATE:
                    pcm = resample_audio(pcm, from_rate=_WIRE_RATE, to_rate=self._internal_rate,
                                         channels=1, bit_depth=16, res_type="soxr_mq")
                await self._gw.send_audio(self._call_id, pcm)
                if offset == 0:
                    logger.info("realtime_playout call=%s utterance=%s validation_to_first_submission_ms=%d",
                                self._call_id, utterance["id"], (time.monotonic() - started) * 1000)
            flush = getattr(self._gw, "flush_audio_buffer", None) or getattr(self._gw, "flush_tts_buffer", None)
            if callable(flush):
                await flush(self._call_id)
            finish = getattr(self._gw, "finish_playback", None)
            if callable(finish):
                receipt = await finish(self._call_id, utterance["id"])
            else:
                await self._send_control_event({"type": "tts_audio_complete"})
                # Legacy transports cannot prove which utterance they completed.
                wait = getattr(self._gw, "wait_for_playback_complete", None)
                if callable(wait):
                    await wait(self._call_id)
            utterance["receipt"] = receipt
            self._record_turn("assistant", event.text, metadata={"delivery": receipt})
            # Generated text and queue acceptance are not contact confirmation.
            # Only a transport with explicit playback acknowledgement may advance
            # the contact readback state; other transports retain pending details.
            if (receipt.get("utterance_id") == utterance["id"]
                    and receipt.get("status") == "completed"
                    and receipt.get("evidence") == "transport_played"):
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
            if (self._closing_after_generation is not None
                    and utterance["generation"] > self._closing_after_generation):
                self._goodbye_completed.set()
            await self._send_control_event({"type": "turn_complete"})
        except asyncio.CancelledError:
            self._record_turn("assistant", event.text, metadata={"delivery": {
                "utterance_id": utterance["id"], "status": "interrupted", "evidence": "unknown"}})
            raise
        except Exception:
            logger.exception("realtime_playout_failed call=%s", self._call_id)
            await self._rt.close()

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

    async def _observe_contact_turn(self, text: str, raw: Any = None) -> None:
        """Run the canonical contact machine on one realtime caller final."""
        from app.services.scripts.call_state_tracker import (
            CallState,
            _classify_core_confirmation,
            update_state_from_user_turn,
        )
        from app.services.scripts.spoken_email_normalizer import (
            extract_email_from_speech,
        )
        from app.domain.services.voice_pipeline.turn_runner import (
            _agent_read_back_phone,
            _is_email_correction,
            _is_phone_correction,
            email_on_the_table,
        )

        slots = getattr(self._contact_session, "captured_slots", None)
        if slots is None or not is_dataclass(slots):
            slots = CallState()
        before_signature = self._contact_state_signature(slots)

        # Same rule as the cascaded path (turn_runner.email_on_the_table): the
        # agent's LATEST read-back is the value the caller is answering.
        slots, email_readback = email_on_the_table(slots, text, self._contact_history)
        pending_email = getattr(slots, "email", None)

        pending_phone = getattr(slots, "phone", None)
        phone_readback = _agent_read_back_phone(
            self._contact_history, pending_phone
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
        self._contact_session.captured_slots = updated
        previous_live_state = self._live_state
        self._live_state = reduce_live_state(
            self._live_state,
            ConfirmedContactsEvidence(
                email=getattr(updated, "email", None),
                email_confirmed=bool(getattr(updated, "email_confirmed", False)),
                phone=getattr(updated, "phone", None),
                phone_confirmed=bool(getattr(updated, "phone_confirmed", False)),
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
                prior = getattr(slots, f"{resolved_kind}_capture", None)
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

    def _record_turn(self, role: str, text: str, *, metadata=None) -> None:
        """Accumulate one finalised transcript turn (role-tagged, in order) into
        the shared TranscriptService buffer. Fail-soft: a transcript error must
        never break the call, so everything here is swallowed."""
        if self._transcript_service is None:
            return
        try:
            self._transcript_service.accumulate_turn(
                self._call_id,
                role,
                text,
                talklee_call_id=self._talklee_call_id,
                turn_index=self._turn_index,
                metadata=metadata,
            )
            self._turn_index += 1
        except Exception as exc:  # noqa: BLE001 — transcript must never break a call
            logger.debug("realtime_bridge record_turn err call=%s: %s",
                         self._call_id, exc)

    async def _publish_live_state(self) -> None:
        """Replace the persistent realtime state block; never break audio."""
        publish = getattr(self._rt, "update_live_state", None)
        if not callable(publish):
            logger.warning(
                "realtime_bridge live state unavailable call=%s", self._call_id
            )
            return
        try:
            await publish(render_live_state_block(self._live_state))
        except Exception as exc:  # noqa: BLE001 - state steering is fail-soft
            logger.warning(
                "realtime_bridge live-state publish err call=%s: %s",
                self._call_id,
                exc,
            )

    async def _send_tool_result_after_playback(self, call_id, result):
        # A lookup may finish while its spoken hold is still playing. Wait in
        # the detached tool task, never in the event pump, to prevent overlap.
        playback = self._playback_task
        if playback is not None and not playback.done():
            await asyncio.gather(playback, return_exceptions=True)
        if not self._stop.is_set():
            await self._rt.send_function_result(call_id, result)

    async def _handle_function_call(self, fc: Any) -> None:
        """Fulfil knowledge and deterministic action tools. Never raises."""
        try:
            if fc.name == "knowledge_lookup":
                query = fc.parsed_arguments().get("query", "")
                text = await self._lookup_knowledge(query)
                success = text not in {
                    _NO_KB_INFO,
                    "I couldn't look that up right now.",
                }
                self._live_state = reduce_live_state(
                    self._live_state,
                    ToolResultEvidence(
                        tool_name="knowledge_lookup",
                        success=success,
                        code="ok" if success else "no_match",
                    ),
                )
                # Publish before send_function_result triggers the continuation,
                # so that response sees the deterministic tool outcome too.
                await self._publish_live_state()
                await self._send_tool_result_after_playback(fc.call_id, text)
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

    def _arm_caller_end_call(self, *, require_explicit=False):
        from app.domain.services.end_session_action import caller_signaled_end
        from app.domain.services.voice_pipeline.identity_disposition import contains_dnc, contains_explicit_goodbye
        if self._caller_transcript_pending or self._hangup_started:
            return False
        # DNC survives a canceled immediate hangup. Shared telephony teardown
        # reads this CallSession flag and runs the existing durable opt-out.
        opted_out = contains_dnc(self._latest_caller_text)
        if opted_out:
            self._action_session._caller_opted_out = True
        if not caller_signaled_end(self._latest_caller_text):
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
        from app.domain.services.end_session_action import caller_signaled_end
        if (self._caller_transcript_pending
                or caller_revision != self._pending_end_call_revision
                or caller_revision != self._caller_activity_revision
                or not caller_signaled_end(self._latest_caller_text)):
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

    async def _lookup_knowledge(self, query: str) -> str:
        """Top-k campaign-knowledge nodes rendered for the voice model. Returns
        a short plain-text answer, or a graceful 'no info' string. Reuses
        retrieve_knowledge — the cascaded per-turn retrieval.

        SECURITY: what this returns lands in a ``function_call_output`` item,
        which the realtime model reads as authoritative fact — the highest-trust
        channel in the call. The text itself is tenant/3rd-party data, so it gets
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
        if not query or not self._campaign_id:
            return "No company information is available for that."
        # SECURITY — fail closed (issue #5): a missing/empty tenant must NEVER
        # reach retrieve_knowledge. acquire_with_tenant treats tenant_id=None as
        # an RLS BYPASS (app.bypass_rls='on'), so a tenantless realtime session
        # would read ACROSS tenants. Without a validated tenant we decline the
        # lookup entirely rather than risk cross-tenant KB exposure — we do NOT
        # pass None through to get a bypass.
        pinned_nodes = self._knowledge_snapshot_nodes
        tenant_id = (self._tenant_id or "").strip()
        if pinned_nodes is None and not self._knowledge_pool:
            return _NO_KB_INFO
        if pinned_nodes is None and not tenant_id:
            logger.warning(
                "realtime_bridge KB lookup BLOCKED — no tenant on session call=%s "
                "(refusing RLS-bypass cross-tenant read)", self._call_id,
            )
            return _NO_KB_INFO
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
            return _NO_KB_INFO
        except Exception as exc:  # noqa: BLE001
            logger.debug("realtime_bridge knowledge lookup err: %s", exc)
            return "I couldn't look that up right now."
        logger.info("realtime_kb_lookup call=%s query_chars=%d hits=%d coverage=%s",
                    self._call_id, len(query), len(nodes or []),
                    [round(float(n.get("coverage") or 0), 2) for n in (nodes or [])])
        if not nodes:
            return _NO_KB_INFO
        from app.domain.services.voice_pipeline.kb_budget import prepare_knowledge_evidence
        evidence = prepare_knowledge_evidence(nodes, query,
            chunk_chars=_REALTIME_NODE_CHARS, total_chars=_REALTIME_NODE_CHARS * 2)
        if evidence["status"] != "matched":
            return _NO_KB_INFO
        for passage in evidence["passages"]:
            if passage["text"] not in self._verified_knowledge:
                self._verified_knowledge.append(passage["text"])
        self._verified_knowledge = self._verified_knowledge[-8:]
        return fence_kb_result(evidence["text"], with_note=True)
