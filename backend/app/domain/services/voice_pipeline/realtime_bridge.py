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

Discipline
----------
* Exactly TWO tasks (caller pump + model pump). Both are cancelled on stop().
* No unbounded buffers — the gateway queue and the RealtimeSession event queue
  are both already bounded; we add none.
* Fail-soft: any error logs and ends the bridge cleanly. A realtime failure
  must end the CALL, never crash the worker.

Mid-call connection loss (Fix 14)
---------------------------------
The realtime session runs ONE websocket per call with no reconnect. If that
socket drops WHILE the call is still up, both pumps end and ``run()`` returns —
which today leaves the call in dead air until the ~300 s inactivity watchdog
notices. To avoid that, ``run()`` distinguishes an *unexpected mid-call socket
death* (``realtime_session.closed()`` became true while nobody asked us to
stop and the call is still active) from a *normal* call end (a caller hangup
cancels the pipeline task → ``CancelledError``; a clean ``stop()`` sets the
stop event). Only the former arms the ``on_connection_lost`` callback, which
the lifecycle layer wires to rebuild the cascaded pipeline on the SAME media
gateway. The callback fires EXACTLY ONCE, is wrapped so its own error can never
crash the bridge, and the bridge then ends WITHOUT triggering the normal
'call over' teardown — the callback owner decides what happens next.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import os
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

_WIRE_RATE = 8000  # OpenAI Realtime audio/pcmu is μ-law @ 8 kHz

# Per-node char budget for the realtime knowledge function-result. The
# source-first render (full node content) can be large; cap each node on a
# safe boundary so a giant KB section can't balloon the model's tool result
# (and its latency). Env-overridable.
_REALTIME_NODE_CHARS = int(os.getenv("KNOWLEDGE_REALTIME_NODE_CHARS", "2000"))


class RealtimeBridge:
    """Drives one realtime call: gateway <-> OpenAIRealtimeSession.

    Construct with the already-connected session, the media gateway, the
    call_id, the gateway's internal PCM sample rate, and (optionally) a
    knowledge context so the model's knowledge_lookup tool can be fulfilled.
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
        session_active: Optional[Any] = None,
        greet_on_start: bool = True,
        barge_in_event: Optional[asyncio.Event] = None,
        transcript_service: Optional[Any] = None,
        talklee_call_id: Optional[str] = None,
        on_connection_lost: Optional[Callable[[], Awaitable[None]]] = None,
    ) -> None:
        self._call_id = call_id
        self._rt = realtime_session
        self._gw = media_gateway
        self._internal_rate = int(internal_sample_rate or 8000)
        self._knowledge_pool = knowledge_pool
        self._tenant_id = tenant_id
        self._campaign_id = campaign_id
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
            # Fix 14 — decide, BEFORE stop() runs (stop() sets self._stop),
            # whether the pumps ended because the realtime SOCKET died while
            # the call is still up (→ arm the fallback) versus a normal end.
            # A normal caller-hangup cancels this task → CancelledError, which
            # skips this line entirely; a clean stop() sets self._stop; a
            # voicemail/model-error break leaves the socket OPEN. Only a truly
            # closed socket, unrequested, on a still-active call, arms it.
            if (
                self._rt.closed()
                and not self._stop.is_set()
                and self._session_active()
            ):
                logger.warning(
                    "realtime_bridge connection_lost call=%s — realtime socket "
                    "died mid-call; arming cascaded fallback", self._call_id,
                )
                self._connection_lost = True
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — never crash the call worker
            logger.error("realtime_bridge run error call=%s: %s",
                         self._call_id, exc)
        finally:
            # Fully tear down the bridge (cancel pumps, close the socket)
            # BEFORE handing off, so the cascaded pipeline the callback starts
            # is the SOLE consumer of the gateway's caller-audio queue.
            await self.stop()
            if self._connection_lost:
                await self._invoke_on_connection_lost()
            logger.info("realtime_bridge end call=%s", self._call_id)

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

                if kind == "audio" and ev.audio:
                    pcm16 = ulaw_to_pcm(ev.audio)  # μ-law 8k -> PCM16 8k
                    if self._internal_rate != _WIRE_RATE:
                        try:
                            pcm16 = resample_audio(
                                pcm16,
                                from_rate=_WIRE_RATE,
                                to_rate=self._internal_rate,
                                channels=1,
                                bit_depth=16,
                                res_type="soxr_mq",
                            )
                        except Exception as exc:  # noqa: BLE001
                            logger.debug("realtime_bridge model resample failed: %s", exc)
                            continue
                    try:
                        await self._gw.send_audio(self._call_id, pcm16)
                    except Exception as exc:  # noqa: BLE001
                        logger.debug("realtime_bridge send_audio err: %s", exc)

                elif kind == "interrupted":
                    # Caller barged in: signal the gateway's pacing loop
                    # FIRST so any in-flight send_audio() burst exits within
                    # microseconds (it's blocked on
                    # asyncio.wait_for(barge_in_event.wait(), ...)) instead
                    # of finishing its sleep window, THEN drop whatever the
                    # gateway still has buffered so the agent stops
                    # mid-sentence immediately.
                    if self._barge_in_event is not None:
                        self._barge_in_event.set()
                    try:
                        await self._gw.clear_output_buffer(self._call_id)
                    except Exception as exc:  # noqa: BLE001
                        logger.debug("realtime_bridge clear_output_buffer err: %s", exc)
                    finally:
                        # Un-latch so it doesn't stay "set" and short-circuit
                        # the NEXT turn's pacing loop before any barge-in
                        # actually happens.
                        if self._barge_in_event is not None:
                            self._barge_in_event.clear()

                elif kind == "function_call" and ev.function_call:
                    # Do NOT await here — this is the SOLE event pump. The tool
                    # does a knowledge DB round-trip; awaiting it would stall
                    # audio deltas AND barge-in ("interrupted") for the whole
                    # lookup, so the agent goes silent and un-interruptible
                    # mid-turn. Dispatch it detached and keep draining the
                    # socket; send_function_result already sequences the
                    # follow-up response.create safely (openai_realtime.py:407).
                    tool_task = asyncio.create_task(
                        self._handle_function_call(ev.function_call),
                        name=f"rt-tool-{self._call_id}",
                    )
                    self._tool_tasks.add(tool_task)
                    tool_task.add_done_callback(self._tool_tasks.discard)

                elif kind == "agent_transcript" and ev.text:
                    logger.debug("realtime agent: %s", ev.text)
                    if getattr(ev, "is_final", False):
                        self._record_turn("assistant", ev.text)

                elif kind == "caller_transcript" and ev.text:
                    logger.debug("realtime caller: %s", ev.text)
                    if getattr(ev, "is_final", False):
                        _tidx = self._turn_index
                        self._record_turn("user", ev.text)
                        # Real-time voicemail detection on the opening turn(s):
                        # if the callee is an answering machine, hang up now and
                        # end the pump — no message, no conversation.
                        if _tidx <= 1:
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
                    # A hard error ends the call cleanly.
                    break
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error("realtime_bridge model pump err call=%s: %s",
                         self._call_id, exc)
        logger.debug("realtime_bridge model pump ended call=%s", self._call_id)

    def _record_turn(self, role: str, text: str) -> None:
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
            )
            self._turn_index += 1
        except Exception as exc:  # noqa: BLE001 — transcript must never break a call
            logger.debug("realtime_bridge record_turn err call=%s: %s",
                         self._call_id, exc)

    async def _handle_function_call(self, fc: Any) -> None:
        """Fulfil the model's knowledge_lookup tool using the SAME retrieval
        the cascaded path uses. Any other tool name gets a benign stub so the
        model can continue. Never raises."""
        try:
            if fc.name == "knowledge_lookup":
                query = fc.parsed_arguments().get("query", "")
                text = await self._lookup_knowledge(query)
                await self._rt.send_function_result(fc.call_id, text)
            else:
                await self._rt.send_function_result(
                    fc.call_id, {"error": f"unknown tool {fc.name}"}
                )
        except Exception as exc:  # noqa: BLE001
            logger.debug("realtime_bridge function-call err call=%s: %s",
                         self._call_id, exc)
            try:
                await self._rt.send_function_result(
                    fc.call_id, {"error": "lookup failed"}
                )
            except Exception:  # noqa: BLE001
                pass

    async def _lookup_knowledge(self, query: str) -> str:
        """Top-k campaign-knowledge nodes rendered for the voice model. Returns
        a short plain-text answer, or a graceful 'no info' string. Reuses
        retrieve_knowledge — the cascaded per-turn retrieval."""
        if not query or not self._knowledge_pool or not self._campaign_id:
            return "No company information is available for that."
        # SECURITY — fail closed (issue #5): a missing/empty tenant must NEVER
        # reach retrieve_knowledge. acquire_with_tenant treats tenant_id=None as
        # an RLS BYPASS (app.bypass_rls='on'), so a tenantless realtime session
        # would read ACROSS tenants. Without a validated tenant we decline the
        # lookup entirely rather than risk cross-tenant KB exposure — we do NOT
        # pass None through to get a bypass.
        tenant_id = (self._tenant_id or "").strip()
        if not tenant_id:
            logger.warning(
                "realtime_bridge KB lookup BLOCKED — no tenant on session call=%s "
                "(refusing RLS-bypass cross-tenant read)", self._call_id,
            )
            return "I don't have specific information on that."
        try:
            from app.services.scripts.knowledge.retrieval import (
                render_node_answer,
                retrieve_knowledge,
            )
            nodes = await retrieve_knowledge(
                self._knowledge_pool,
                tenant_id=tenant_id,
                campaign_id=self._campaign_id,
                query=query,
                k=2,
                bump_hits=False,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("realtime_bridge knowledge lookup err: %s", exc)
            return "I couldn't look that up right now."
        if not nodes:
            return "I don't have specific information on that."
        parts = []
        for n in nodes:
            # Source-first (issue #1): the FACT comes from the node's own
            # content (retrieval can match a fact anywhere in the node), not the
            # enricher's top-of-node voice_answer summary.
            body = render_node_answer(n, max_chars=_REALTIME_NODE_CHARS)
            head = (n.get("heading") or "").strip()
            if body:
                parts.append(f"{head}: {body}" if head else body)
        return "  ".join(parts) if parts else "I don't have specific information on that."
