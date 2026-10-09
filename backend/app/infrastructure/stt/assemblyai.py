"""AssemblyAI Universal-3.6 Pro on the streaming v3 WebSocket API.

One provider session belongs to one call. AssemblyAI owns recognition and turn
detection; this adapter only translates its protocol into our existing STT
contract. See docs/streaming/{api-spec/streaming-websocket,turn-detection} at
assemblyai.com. Model, English steering and audio format are pinned explicitly.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import time
from collections.abc import AsyncIterator, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from urllib.parse import urlencode

import websockets

from app.domain.interfaces.stt_provider import STTProvider
from app.domain.models.assemblyai_config import ASSEMBLYAI_MODEL, AssemblyAISettings
from app.domain.models.conversation import AudioChunk, BargeInSignal, TranscriptChunk
from app.infrastructure.providers.provider_concurrency import get_provider_guard

logger = logging.getLogger(__name__)
# WebSocket debug logs include URL query context and authentication headers.
# Keep this wire logger private and disabled even if application DEBUG is enabled.
_wire_logger = logging.Logger("assemblyai.private_wire", level=logging.CRITICAL + 1)
_wire_logger.addHandler(logging.NullHandler())
_wire_logger.propagate = False

_ENDPOINTS = {
    "global": "wss://streaming.assemblyai.com/v3/ws",
    "us": "wss://streaming.us.assemblyai.com/v3/ws",
    "eu": "wss://streaming.eu.assemblyai.com/v3/ws",
}
_CONNECT_TIMEOUT = 10.0
_BEGIN_TIMEOUT = 10.0
_SEND_TIMEOUT = 10.0
_DRAIN_TIMEOUT = 5.0
_CLOSE_TIMEOUT = 3.0
_CHUNK_MS = 50  # Provider accepts 50..1000 ms PCM frames, not telephony's 20 ms.


def _safe_error(exc: BaseException, stage: str) -> RuntimeError:
    """Never expose upstream text: errors can include URL prompts or credentials."""
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    if status in (401, 403):
        return RuntimeError(f"AssemblyAI authentication failed (HTTP {status})")
    if status == 429:
        return RuntimeError("AssemblyAI concurrency or rate limit exceeded (HTTP 429)")
    if isinstance(exc, TimeoutError):
        return RuntimeError(f"AssemblyAI {stage} timed out")
    code = getattr(getattr(exc, "rcvd", None), "code", None)
    if isinstance(code, int):
        return RuntimeError(f"AssemblyAI {stage} failed (close code {code})")
    return RuntimeError(f"AssemblyAI {stage} failed")


@dataclass(eq=False)
class _Connection:
    ws: object
    slot: object
    call_id: str | None
    agent_context: str = ""
    closed: bool = False
    adopted: bool = False
    terminate_sent: bool = False
    error: RuntimeError | None = None
    keepalive_task: asyncio.Task | None = None
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    close_task: asyncio.Task | None = None

    async def send(self, payload: bytes | dict) -> None:
        async with self.send_lock:
            if self.closed:
                raise RuntimeError("AssemblyAI session is closed")
            if isinstance(payload, dict) and payload.get("type") == "Terminate":
                if self.terminate_sent:
                    return
                self.terminate_sent = True
            await asyncio.wait_for(
                self.ws.send(json.dumps(payload) if isinstance(payload, dict) else payload),
                timeout=_SEND_TIMEOUT,
            )

    async def close(self) -> None:
        if self.close_task is None:
            self.close_task = asyncio.create_task(self._close_once())
        # A cancelled stream must not leak a billed session or a concurrency slot.
        await asyncio.shield(self.close_task)

    async def _close_once(self) -> None:
        try:
            if self.keepalive_task:
                self.keepalive_task.cancel()
                await asyncio.gather(self.keepalive_task, return_exceptions=True)
            if not self.terminate_sent:
                with suppress(Exception):
                    await asyncio.wait_for(self.send({"type": "Terminate"}), timeout=1.0)
            self.closed = True
            with suppress(Exception):
                await asyncio.wait_for(self.ws.close(), timeout=_CLOSE_TIMEOUT)
        finally:
            self.closed = True
            await self.slot.__aexit__(None, None, None)


class AssemblyAISTTProvider(STTProvider):
    def __init__(self) -> None:
        self._api_key: str | None = None
        self._sample_rate = 16000
        self._encoding = "pcm_s16le"
        self._settings = AssemblyAISettings()
        self._keyterms: list[str] = []
        self._guard = get_provider_guard("assemblyai")
        self._connections: set[_Connection] = set()
        self._pre_connections: dict[str, _Connection] = {}
        self._preconnect_tasks: dict[str, asyncio.Task] = {}
        self._active_connections: dict[str, _Connection] = {}
        self._stream_claims: dict[str, object] = {}
        self._pending_context: dict[str, str] = {}
        self._muted_calls: set[str] = set()
        self._mute_epoch: dict[str, int] = {}
        self._last_message_at: dict[str, float] = {}
        self._generation = 0

    async def initialize(self, config: dict) -> None:
        if self._connections or self._preconnect_tasks or self._stream_claims:
            raise RuntimeError("AssemblyAI cannot be reconfigured with active sessions")
        api_key = config.get("api_key") or os.getenv("ASSEMBLYAI_API_KEY")
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("AssemblyAI API key not found in config or environment")
        self._settings = AssemblyAISettings.model_validate(config.get("assemblyai_settings") or {})
        self._sample_rate = int(config.get("sample_rate", 16000))
        if not 8000 <= self._sample_rate <= 96000:
            raise ValueError("AssemblyAI sample rate must be between 8000 and 96000 Hz")
        raw_encoding = config.get("encoding", "linear16")
        encodings = {
            "linear16": "pcm_s16le",
            "pcm_s16le": "pcm_s16le",
            "mulaw": "pcm_mulaw",
            "pcm_mulaw": "pcm_mulaw",
        }
        if raw_encoding not in encodings:
            raise ValueError("AssemblyAI call audio must be raw mono PCM16 or mu-law")
        self._encoding = encodings[raw_encoding]
        # Use only the terms explicitly saved in the AssemblyAI control. An
        # empty control must mean no boosting, with no hidden campaign hints.
        self._keyterms = list(self._settings.keyterms_prompt)
        self._api_key = api_key.strip()
        logger.info(
            "AssemblyAI initialized model=%s mode=%s region=%s sample_rate=%s",
            ASSEMBLYAI_MODEL,
            self._settings.mode,
            self._settings.region,
            self._sample_rate,
        )

    def _connection_parameters(self, call_id: str | None) -> dict:
        params = self._settings.connection_parameters()
        params.update(
            speech_model=ASSEMBLYAI_MODEL,
            language_codes=["en"],
            sample_rate=self._sample_rate,
            encoding=self._encoding,
        )
        if self._keyterms:
            params["keyterms_prompt"] = self._keyterms
        if self._settings.auto_agent_context and call_id and self._pending_context.get(call_id):
            params["agent_context"] = self._pending_context[call_id]
        return params

    async def _open_connection(self, call_id: str | None) -> _Connection:
        if not self._api_key:
            raise RuntimeError("AssemblyAI is not initialized")
        generation = self._generation
        params = self._connection_parameters(call_id)
        query = urlencode(
            {
                key: (
                    json.dumps(value, separators=(",", ":"))
                    if isinstance(value, list | bool)
                    else str(value)
                )
                for key, value in params.items()
            }
        )
        slot = self._guard.acquire()
        await slot.__aenter__()
        connection = None
        try:
            ws = await websockets.connect(
                f"{_ENDPOINTS[self._settings.region]}?{query}",
                additional_headers={"Authorization": self._api_key},
                open_timeout=_CONNECT_TIMEOUT,
                close_timeout=2.0,
                ping_interval=20,
                ping_timeout=20,
                max_size=1024 * 1024,
                logger=_wire_logger,
            )
            connection = _Connection(
                ws, slot, call_id, agent_context=params.get("agent_context", "")
            )
            self._connections.add(connection)
            # Opening TCP/WebSocket is insufficient: model selection is confirmed
            # by Begin before prewarm or readiness is allowed to succeed.
            message = json.loads(await asyncio.wait_for(ws.recv(), timeout=_BEGIN_TIMEOUT))
            if message.get("type") == "Error":
                code = message.get("error_code")
                raise RuntimeError(
                    f"AssemblyAI session rejected (code {code if isinstance(code, int) else 'unknown'})"
                )
            configuration = message.get("configuration") or {}
            if (
                message.get("type") != "Begin"
                or configuration.get("model") != ASSEMBLYAI_MODEL
                or configuration.get("mode") != self._settings.mode
            ):
                raise RuntimeError(
                    "AssemblyAI did not acknowledge the requested 3.6 Pro model and mode"
                )
            if generation != self._generation or not self._api_key:
                raise RuntimeError("AssemblyAI provider closed during handshake")
            if call_id:
                self._last_message_at[call_id] = time.monotonic()
            if self._settings.inactivity_timeout is not None:
                connection.keepalive_task = asyncio.create_task(self._keep_alive(connection))
            return connection
        except BaseException as exc:
            if connection:
                await self._close_connection(connection)
            else:
                await slot.__aexit__(None, None, None)
            if isinstance(exc, asyncio.CancelledError):
                raise
            if isinstance(exc, RuntimeError) and str(exc).startswith("AssemblyAI "):
                raise exc from None
            raise _safe_error(exc, "connection") from None

    async def _keep_alive(self, connection: _Connection) -> None:
        interval = min(10.0, self._settings.inactivity_timeout / 2)
        try:
            while not connection.closed and not connection.terminate_sent:
                await asyncio.sleep(interval)
                if not connection.closed and not connection.terminate_sent:
                    await connection.send({"type": "KeepAlive"})
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            connection.error = _safe_error(exc, "keepalive")
            # recv/send will also discover closure. Store the sanitized cause so
            # adopting a failed prewarm cannot be reported as a healthy session.

    async def pre_connect(self, call_id: str) -> None:
        if call_id in self._active_connections:
            return
        existing = self._pre_connections.get(call_id)
        if existing and not existing.closed and existing.error is None:
            return
        if existing:
            await self._close_connection(existing)
            self._pre_connections.pop(call_id, None)
        generation = self._generation
        task = self._preconnect_tasks.get(call_id)
        if task is None:
            task = asyncio.create_task(self._open_connection(call_id))
            self._preconnect_tasks[call_id] = task
        try:
            connection = await task
            if generation != self._generation or connection.closed:
                await self._close_connection(connection)
                raise RuntimeError("AssemblyAI provider closed during prewarm")
            if not connection.adopted:
                self._pre_connections[call_id] = connection
                try:
                    await self._flush_agent_context(connection)
                except BaseException:
                    if self._pre_connections.get(call_id) is connection:
                        self._pre_connections.pop(call_id, None)
                    await self._close_connection(connection)
                    raise
        finally:
            if self._preconnect_tasks.get(call_id) is task:
                self._preconnect_tasks.pop(call_id, None)

    async def _close_connection(self, connection: _Connection) -> None:
        try:
            await connection.close()
        finally:
            if connection.close_task and not connection.close_task.done():
                connection.close_task.add_done_callback(
                    lambda _task: self._connections.discard(connection)
                )
            else:
                self._connections.discard(connection)

    async def update_agent_context(self, call_id: str, text: str) -> None:
        if not self._settings.auto_agent_context or not call_id:
            return
        # Keep the end of a long spoken answer, where its substantive question
        # usually lives. Empty text explicitly clears the previous context.
        self._pending_context[call_id] = str(text or "").strip()[-1750:]
        connection = self._active_connections.get(call_id) or self._pre_connections.get(call_id)
        if connection and not connection.closed:
            await self._flush_agent_context(connection)

    async def _flush_agent_context(self, connection: _Connection) -> None:
        if not self._settings.auto_agent_context or not connection.call_id:
            return
        text = self._pending_context.get(connection.call_id, "")
        if text != connection.agent_context:
            try:
                await connection.send({"type": "UpdateConfiguration", "agent_context": text})
                connection.agent_context = text
            except Exception as exc:
                raise _safe_error(exc, "context update") from None

    async def mute(self, call_id: str) -> None:
        self._muted_calls.add(call_id)
        self._mute_epoch[call_id] = self._mute_epoch.get(call_id, 0) + 1

    async def unmute(self, call_id: str) -> None:
        self._muted_calls.discard(call_id)

    def is_muted(self, call_id: str) -> bool:
        return call_id in self._muted_calls

    def seconds_since_last_message(self, call_id: str) -> float | None:
        last = self._last_message_at.get(call_id)
        return None if last is None else max(0.0, time.monotonic() - last)

    def get_last_message_at(self, call_id: str) -> float | None:
        return self._last_message_at.get(call_id)

    async def stream_transcribe(
        self,
        audio_stream: AsyncIterator[AudioChunk],
        language: str = "en",
        context: str | None = None,
        call_id: str | None = None,
        on_eager_end_of_turn: Callable | None = None,
        on_barge_in: Callable | None = None,
    ) -> AsyncIterator[TranscriptChunk | BargeInSignal]:
        if not self._api_key:
            raise RuntimeError("AssemblyAI is not initialized")
        if (language or "en").lower() not in ("en", "en-us", "en-gb", "en-ca", "en-au"):
            raise ValueError("AssemblyAI 3.6 Pro is configured for English only")
        if call_id and call_id in self._stream_claims:
            raise RuntimeError("AssemblyAI already has an active stream for this call")
        # Claim before the handshake awaits: two concurrent consumers must not
        # adopt the same prewarm or clear each other's session state on failure.
        stream_claim = object()
        if call_id:
            self._stream_claims[call_id] = stream_claim
        generation = self._generation
        connection = None
        sender = None
        input_exhausted_at = None
        completed_order = -1
        pending_barge = False
        suppressed_start = False
        suppressed_orders: set[int] = set()
        from app.domain.services.voice_pipeline.backchannel import (
            is_backchannel,
            is_disfluency,
            is_hard_interrupt,
        )

        async def send_audio() -> None:
            nonlocal input_exhausted_at
            buffer = bytearray()
            bytes_per_sample = 2 if self._encoding == "pcm_s16le" else 1
            frame_bytes = self._sample_rate * bytes_per_sample * _CHUNK_MS // 1000
            silence_byte = b"\x00" if bytes_per_sample == 2 else b"\xff"
            epoch = self._mute_epoch.get(call_id, 0)
            next_send_at = 0.0

            async def send_frame(data: bytes, frame_epoch: int) -> None:
                nonlocal next_send_at
                delay = next_send_at - time.monotonic()
                if delay > 0:
                    await asyncio.sleep(delay)
                if call_id and (
                    self.is_muted(call_id) or self._mute_epoch.get(call_id, 0) != frame_epoch
                ):
                    # Mute may start and finish while a queued frame is waiting
                    # for its pacing deadline. Never replay that stale audio.
                    data = silence_byte * len(data)
                # Count time spent writing toward this frame's period. Starting
                # the next deadline after send() would add socket backpressure
                # to every 50 ms frame and make queued caller audio fall behind.
                # Reset to the actual start after a stall; never burst to catch up.
                next_send_at = max(next_send_at, time.monotonic()) + len(data) / (
                    self._sample_rate * bytes_per_sample
                )
                await connection.send(data)

            async for chunk in audio_stream:
                if chunk.sample_rate != self._sample_rate or chunk.channels != 1:
                    raise ValueError(
                        "AssemblyAI audio format does not match initialized mono sample rate"
                    )
                if len(chunk.data) % bytes_per_sample:
                    raise ValueError("AssemblyAI received an incomplete PCM sample")
                current_epoch = self._mute_epoch.get(call_id, 0)
                if epoch != current_epoch:
                    buffer.clear()
                    epoch = current_epoch
                buffer.extend(
                    silence_byte * len(chunk.data)
                    if call_id and self.is_muted(call_id)
                    else chunk.data
                )
                while len(buffer) >= frame_bytes:
                    data = bytes(buffer[:frame_bytes])
                    del buffer[:frame_bytes]
                    await send_frame(data, epoch)
            if buffer:
                # Padding preserves a final short suffix (e.g. '.com') while
                # satisfying the provider's 50 ms minimum packet duration.
                await send_frame(bytes(buffer) + silence_byte * (frame_bytes - len(buffer)), epoch)
            input_exhausted_at = time.monotonic()
            await connection.send({"type": "Terminate"})

        try:
            if call_id:
                await self.pre_connect(call_id)
                connection = self._pre_connections.pop(call_id)
                self._active_connections[call_id] = connection
            else:
                connection = await self._open_connection(None)
            connection.adopted = True
            sender = asyncio.create_task(send_audio())
            while True:
                if connection.error:
                    raise connection.error
                if sender.done() and not sender.cancelled() and sender.exception():
                    error = sender.exception()
                    if isinstance(error, ValueError):
                        raise error
                    raise _safe_error(error, "audio send") from None
                if (
                    input_exhausted_at is not None
                    and time.monotonic() - input_exhausted_at > _DRAIN_TIMEOUT
                ):
                    raise RuntimeError("AssemblyAI session termination timed out")
                try:
                    raw = await asyncio.wait_for(connection.ws.recv(), timeout=0.1)
                except TimeoutError:
                    continue
                except Exception as exc:
                    if generation != self._generation:
                        break
                    raise _safe_error(exc, "stream") from None
                try:
                    event = json.loads(raw)
                    if not isinstance(event, dict):
                        raise ValueError
                except (TypeError, ValueError):
                    raise RuntimeError("AssemblyAI returned an invalid event") from None
                if call_id:
                    self._last_message_at[call_id] = time.monotonic()
                kind = event.get("type")
                if kind == "Error":
                    code = event.get("error_code")
                    raise RuntimeError(
                        f"AssemblyAI stream error (code {code if isinstance(code, int) else 'unknown'})"
                    )
                if kind == "Termination":
                    if input_exhausted_at is None and generation == self._generation:
                        raise RuntimeError("AssemblyAI session ended while caller audio is active")
                    break
                if kind == "SpeechStarted":
                    # AssemblyAI emits this immediately before the first Turn.
                    # Use those words for our existing echo/backchannel guards.
                    suppressed_start = bool(call_id and self.is_muted(call_id))
                    pending_barge = not suppressed_start
                    continue
                if kind != "Turn":
                    # Heartbeat is liveness only. SpeakerRevision must never
                    # replay a user's old words as a new conversation turn.
                    continue
                order = event.get("turn_order")
                if not isinstance(order, int) or isinstance(order, bool):
                    raise RuntimeError("AssemblyAI turn is missing a valid order")
                if order <= completed_order:
                    continue
                final = event.get("end_of_turn") is True
                if suppressed_start or (call_id and self.is_muted(call_id)):
                    suppressed_orders.add(order)
                    pending_barge = False
                    suppressed_start = False
                if order in suppressed_orders:
                    if final:
                        completed_order = order
                        suppressed_orders.discard(order)
                        yield TranscriptChunk(
                            text="",
                            is_final=True,
                            metadata={
                                "provider": "assemblyai",
                                "turn_order": order,
                                "empty_turn": True,
                            },
                        )
                    continue
                text = event.get("transcript", "")
                if not isinstance(text, str):
                    raise RuntimeError("AssemblyAI turn is missing valid transcript text")
                text = text.strip()
                if (
                    pending_barge
                    and text
                    and (
                        is_hard_interrupt(text) or not (is_backchannel(text) or is_disfluency(text))
                    )
                ):
                    # A partial can begin with "uh" or "yes" and grow into
                    # a real interruption. Keep evaluating until it does.
                    pending_barge = False
                    if on_barge_in:
                        result = on_barge_in(text)
                        if inspect.isawaitable(result):
                            await result
                    yield BargeInSignal(text=text)
                # Every Turn re-transcribes the whole turn. Never append text,
                # and never promote an incomplete partial on socket failure.
                words = event.get("words") or []
                confidences = [
                    w["confidence"]
                    for w in words
                    if isinstance(w, dict)
                    and isinstance(w.get("confidence"), int | float)
                    and 0 <= w["confidence"] <= 1
                ]
                confidence = sum(confidences) / len(confidences) if confidences else None
                metadata = {"provider": "assemblyai", "turn_order": order}
                for key in ("speaker_label", "language_code", "language_confidence"):
                    if key in event:
                        metadata[key] = event[key]
                if text:
                    yield TranscriptChunk(
                        text=text, is_final=final, confidence=confidence, metadata=metadata
                    )
                if final:
                    completed_order = order
                    pending_barge = False
                    # Even an empty/withdrawn turn releases the caller-speaking
                    # flag. Explicit metadata prevents dispatching stale partial
                    # text as though it were confirmed by the recognizer.
                    if not text:
                        metadata = {**metadata, "empty_turn": True}
                    yield TranscriptChunk(text="", is_final=True, confidence=1.0, metadata=metadata)
        finally:
            if sender:
                sender.cancel()
                await asyncio.gather(sender, return_exceptions=True)
            if connection:
                await self._close_connection(connection)
            if call_id and self._stream_claims.get(call_id) is stream_claim:
                self._stream_claims.pop(call_id, None)
                self._active_connections.pop(call_id, None)
                self._last_message_at.pop(call_id, None)
                self._pending_context.pop(call_id, None)
                self._muted_calls.discard(call_id)
                self._mute_epoch.pop(call_id, None)

    async def cleanup(self) -> None:
        self._generation += 1
        self._api_key = None
        tasks = list(self._preconnect_tasks.values())
        self._preconnect_tasks.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.gather(
            *(self._close_connection(c) for c in list(self._connections)), return_exceptions=True
        )
        self._pre_connections.clear()
        self._active_connections.clear()
        self._stream_claims.clear()
        self._pending_context.clear()
        self._last_message_at.clear()
        self._muted_calls.clear()
        self._mute_epoch.clear()

    @property
    def name(self) -> str:
        return "assemblyai"

    def __repr__(self) -> str:
        return f"AssemblyAISTTProvider(model={ASSEMBLYAI_MODEL}, mode={self._settings.mode}, sample_rate={self._sample_rate})"
