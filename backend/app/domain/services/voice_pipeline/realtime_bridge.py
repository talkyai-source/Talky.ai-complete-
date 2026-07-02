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
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

_WIRE_RATE = 8000  # OpenAI Realtime audio/pcmu is μ-law @ 8 kHz


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
    ) -> None:
        self._call_id = call_id
        self._rt = realtime_session
        self._gw = media_gateway
        self._internal_rate = int(internal_sample_rate or 8000)
        self._knowledge_pool = knowledge_pool
        self._tenant_id = tenant_id
        self._campaign_id = campaign_id
        # Optional callable returning whether the call is still active; lets the
        # caller pump stop promptly on hangup. Defaults to "always active" —
        # the pumps also stop when the RealtimeSession closes.
        self._session_active = session_active or (lambda: True)
        # Agent-first: make the model greet immediately on connect. Set False
        # for caller-speaks-first campaigns (let semantic VAD wait for the
        # caller). Defaults True — outbound telephony is agent-first.
        self._greet_on_start = greet_on_start

        self._stop = asyncio.Event()
        self._caller_task: Optional[asyncio.Task] = None
        self._model_task: Optional[asyncio.Task] = None

    # ── Lifecycle ────────────────────────────────────────────────────────
    async def run(self) -> None:
        """Run until the call ends or the realtime session closes. Fail-soft:
        never raises out — a realtime error ends this bridge (and the call)
        cleanly."""
        logger.info("realtime_bridge start call=%s internal_rate=%d",
                    self._call_id, self._internal_rate)
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
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — never crash the call worker
            logger.error("realtime_bridge run error call=%s: %s",
                         self._call_id, exc)
        finally:
            await self.stop()
            logger.info("realtime_bridge end call=%s", self._call_id)

    async def stop(self) -> None:
        """Idempotent teardown: stop pumps, close the realtime session."""
        self._stop.set()
        for task in (self._caller_task, self._model_task):
            if task is not None and not task.done():
                task.cancel()
        for task in (self._caller_task, self._model_task):
            if task is not None:
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
        self._caller_task = None
        self._model_task = None
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
                    # Caller barged in: drop whatever the gateway still has
                    # buffered so the agent stops mid-sentence immediately.
                    try:
                        await self._gw.clear_output_buffer(self._call_id)
                    except Exception as exc:  # noqa: BLE001
                        logger.debug("realtime_bridge clear_output_buffer err: %s", exc)

                elif kind == "function_call" and ev.function_call:
                    await self._handle_function_call(ev.function_call)

                elif kind == "agent_transcript" and ev.text:
                    logger.debug("realtime agent: %s", ev.text)

                elif kind == "caller_transcript" and ev.text:
                    logger.debug("realtime caller: %s", ev.text)

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
        try:
            from app.services.scripts.knowledge.retrieval import retrieve_knowledge
            nodes = await retrieve_knowledge(
                self._knowledge_pool,
                tenant_id=self._tenant_id or "",
                campaign_id=self._campaign_id,
                query=query,
                k=2,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("realtime_bridge knowledge lookup err: %s", exc)
            return "I couldn't look that up right now."
        if not nodes:
            return "I don't have specific information on that."
        parts = []
        for n in nodes:
            body = (n.get("voice_answer") or n.get("summary")
                    or n.get("content") or "").strip()
            head = (n.get("heading") or "").strip()
            if body:
                parts.append(f"{head}: {body}" if head else body)
        return "  ".join(parts) if parts else "I don't have specific information on that."
