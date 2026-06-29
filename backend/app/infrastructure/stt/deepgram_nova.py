"""
Deepgram Nova STT Provider (real-time streaming on /v1/listen).

This is the NON-Flux path: nova-3 via the classic streaming endpoint
(`client.listen.v1.connect`), confirmed by Deepgram's nova-3→Flux migration
guide and verified empirically (2026-06-29) — nova-3 runs on v1; the v2 endpoint
is Flux-only and rejects nova-3.

It exists for two reasons:
  1. **Failover secondary** — when Flux (v2 beta) rejects/fails (as it did on
     2026-06-29 when Deepgram started 400-ing `numerals=true`), the resilient
     wrapper falls back here so calls keep working instead of going silent.
  2. **Selectable engine** — operators can pick nova-3 over Flux in AI Options.

Turn-taking is ACOUSTIC (not Flux's semantic EoT):
  - `vad_events`        → SpeechStarted  → barge-in (on_barge_in)
  - `endpointing`       → speech_final   → end-of-turn
  - `utterance_end_ms`  → UtteranceEnd   → end-of-turn fallback
  - `smart_format`+`numerals` → emails ("test@gmail.com") and digits ("0 7 7")
    formatted natively (nova supports numerals; Flux does not).

Output contract MATCHES DeepgramFluxSTTProvider so the pipeline is unchanged:
  - interim/segment text → TranscriptChunk(text=<running full turn>, is_final=False)
  - end of turn → TranscriptChunk(text=<full turn>, is_final=True), then a
    TranscriptChunk(text="", is_final=True) marker (detect_turn_end → fires the LLM).
"""
import asyncio
import logging
import os
from typing import AsyncIterator, Callable, Optional

from app.domain.interfaces.stt_provider import STTProvider
from app.domain.models.conversation import AudioChunk, TranscriptChunk
from app.infrastructure.providers.provider_concurrency import get_provider_guard

logger = logging.getLogger(__name__)


class DeepgramNovaSTTProvider(STTProvider):
    """Deepgram nova-3 streaming via /v1/listen with VAD + endpointing turn detection."""

    def __init__(self) -> None:
        self._api_key: Optional[str] = None
        self._model: str = "nova-3"
        self._sample_rate: int = 16000
        self._encoding: str = "linear16"
        # Acoustic turn-detection tunables (env-overridable).
        self._endpointing_ms: int = int(os.getenv("NOVA_ENDPOINTING_MS", "300"))
        self._utterance_end_ms: int = int(os.getenv("NOVA_UTTERANCE_END_MS", "1000"))
        self._numerals: bool = os.getenv("NOVA_NUMERALS", "true").strip().lower() in ("1", "true", "yes", "on")
        self._client = None  # AsyncDeepgramClient
        self._guard = get_provider_guard("deepgram")

    async def initialize(self, config: dict) -> None:
        self._api_key = config.get("api_key") or os.getenv("DEEPGRAM_API_KEY")
        if not self._api_key:
            raise ValueError("Deepgram API key not found in config or environment")
        self._model = config.get("model") or "nova-3"
        self._sample_rate = int(config.get("sample_rate", 16000))
        self._encoding = config.get("encoding", "linear16")
        if config.get("endpointing_ms") is not None:
            self._endpointing_ms = int(config["endpointing_ms"])
        if config.get("utterance_end_ms") is not None:
            self._utterance_end_ms = int(config["utterance_end_ms"])

        from deepgram import AsyncDeepgramClient
        self._client = AsyncDeepgramClient(api_key=self._api_key)
        logger.info(
            "DeepgramNovaSTT initialized: model=%s sample_rate=%s endpointing_ms=%s "
            "utterance_end_ms=%s numerals=%s",
            self._model, self._sample_rate, self._endpointing_ms,
            self._utterance_end_ms, self._numerals,
        )

    async def stream_transcribe(
        self,
        audio_stream: AsyncIterator[AudioChunk],
        language: str = "en",
        context: Optional[str] = None,
        call_id: Optional[str] = None,
        on_eager_end_of_turn: Optional[Callable[[str], None]] = None,
        on_barge_in: Optional[Callable[[], None]] = None,
    ) -> AsyncIterator[TranscriptChunk]:
        if not self._client:
            raise RuntimeError("Deepgram nova client not initialized. Call initialize() first.")

        from deepgram.core.events import EventType

        # (kind, payload) events from the SDK callback -> this generator.
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        # Finalized segments of the CURRENT turn (nova finalizes per-segment;
        # the full turn = these joined). Reset after each end-of-turn.
        finals: list[str] = []
        last_interim: list[str] = [""]  # newest interim (fallback if stream closes before a final)
        grace: list[int] = [0]          # consecutive idle ticks after the audio stream ends

        def turn_text() -> str:
            """Best full-turn text: finalized segments, else the last interim."""
            base = " ".join(finals).strip()
            return base or last_interim[0].strip()

        async with self._guard.acquire():
            connect_kwargs = dict(
                model=self._model,
                encoding=self._encoding,
                sample_rate=str(self._sample_rate),
                language=language,
                vad_events="true",
                endpointing=str(self._endpointing_ms),
                utterance_end_ms=str(self._utterance_end_ms),
                interim_results="true",
                smart_format="true",
                mip_opt_out="true",
            )
            if self._numerals:
                connect_kwargs["numerals"] = "true"

            try:
                async with self._client.listen.v1.connect(**connect_kwargs) as conn:
                    def on_message(m) -> None:
                        try:
                            mtype = getattr(m, "type", None)
                            if mtype == "SpeechStarted":
                                queue.put_nowait(("speech_started", None))
                                return
                            if mtype == "UtteranceEnd":
                                queue.put_nowait(("utterance_end", None))
                                return
                            if mtype == "Results":
                                ch = getattr(m, "channel", None)
                                alts = getattr(ch, "alternatives", None) if ch is not None else None
                                if not alts:
                                    return
                                text = (getattr(alts[0], "transcript", "") or "").strip()
                                conf = getattr(alts[0], "confidence", None)
                                is_final = bool(getattr(m, "is_final", False))
                                speech_final = bool(getattr(m, "speech_final", False))
                                queue.put_nowait(("results", (text, is_final, speech_final, conf)))
                        except asyncio.QueueFull:
                            pass  # drop on backpressure rather than block the socket
                        except Exception as exc:  # noqa: BLE001
                            logger.debug("nova on_message error: %s", exc)

                    conn.on(EventType.MESSAGE, on_message)
                    conn.on(EventType.ERROR, lambda e: queue.put_nowait(("error", str(e))))
                    conn.on(EventType.OPEN, lambda _: None)
                    conn.on(EventType.CLOSE, lambda _: None)

                    listen_task = asyncio.create_task(conn.start_listening())

                    async def send_audio() -> None:
                        try:
                            async for audio_chunk in audio_stream:
                                await conn.send_media(audio_chunk.data)
                        except Exception as exc:  # noqa: BLE001
                            logger.debug("nova send_audio ended: %s", exc)

                    sender_task = asyncio.create_task(send_audio())

                    try:
                        while True:
                            try:
                                kind, payload = await asyncio.wait_for(queue.get(), timeout=0.1)
                            except asyncio.TimeoutError:
                                # Production calls never end the audio stream mid-call (turn-end
                                # comes from speech_final/UtteranceEnd). This branch only fires at
                                # call teardown: give a short grace for a trailing end-of-turn
                                # event, then flush whatever turn text we have.
                                if sender_task.done() and queue.empty():
                                    grace[0] += 1
                                    if grace[0] >= 15:  # ~1.5s
                                        ft = turn_text()
                                        if ft:
                                            yield TranscriptChunk(text=ft, is_final=True, confidence=None)
                                            yield TranscriptChunk(text="", is_final=True, confidence=1.0)
                                            finals.clear()
                                        break
                                continue

                            grace[0] = 0
                            if kind == "error":
                                raise RuntimeError(f"Deepgram nova stream error: {payload}")

                            if kind == "speech_started":
                                # Acoustic barge-in: user started speaking.
                                if on_barge_in is not None:
                                    try:
                                        on_barge_in()
                                    except Exception as exc:  # noqa: BLE001
                                        logger.debug("nova on_barge_in error: %s", exc)
                                continue

                            if kind == "utterance_end":
                                # Turn-end fallback (gap-based) when endpointing didn't fire.
                                ft = turn_text()
                                if ft:
                                    yield TranscriptChunk(text=ft, is_final=True, confidence=None)
                                    yield TranscriptChunk(text="", is_final=True, confidence=1.0)
                                    finals.clear()
                                    last_interim[0] = ""
                                continue

                            # kind == "results"
                            text, is_final, speech_final, conf = payload
                            if is_final:
                                if text:
                                    finals.append(text)
                                last_interim[0] = ""
                                if speech_final:
                                    # End of turn: full transcript, then the empty marker.
                                    ft = turn_text()
                                    if ft:
                                        yield TranscriptChunk(text=ft, is_final=True, confidence=conf)
                                    yield TranscriptChunk(text="", is_final=True, confidence=1.0)
                                    finals.clear()
                                else:
                                    # Segment finalized, turn continues — emit running update.
                                    ft = turn_text()
                                    if ft:
                                        yield TranscriptChunk(text=ft, is_final=False, confidence=conf)
                            else:
                                # Interim — running full turn (finalized segments + this interim).
                                last_interim[0] = text
                                running = (" ".join(finals) + " " + text).strip()
                                if running:
                                    yield TranscriptChunk(text=running, is_final=False, confidence=conf)
                    finally:
                        sender_task.cancel()
                        try:
                            await sender_task
                        except (asyncio.CancelledError, Exception):  # noqa: BLE001
                            pass
                        listen_task.cancel()
                        try:
                            await listen_task
                        except (asyncio.CancelledError, Exception):  # noqa: BLE001
                            pass
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(f"Deepgram nova transcription failed: {exc}")

    async def cleanup(self) -> None:
        self._client = None

    @property
    def name(self) -> str:
        return f"deepgram-nova:{self._model}"
