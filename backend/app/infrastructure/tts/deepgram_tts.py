"""
Deepgram TTS Provider — Streaming WebSocket Implementation with Text Chunking

Uses the Deepgram Aura-2 streaming WebSocket API for ultra-low latency
text-to-speech. Implements Deepgram best practices:
- Text chunking for reduced latency and natural speech
- First-chunk optimization for faster time-to-first-byte
- Proper WebSocket lifecycle management (Speak → Flush → Flushed → Close)
- container=none to avoid WAV header clicks

Reference:
    https://developers.deepgram.com/docs/streaming-text-to-speech
    https://developers.deepgram.com/docs/tts-ws-flush
    https://developers.deepgram.com/docs/send-llm-outputs-to-the-tts-web-socket
    https://developers.deepgram.com/docs/tts-media-output-settings
    https://developers.deepgram.com/docs/tts-text-chunking
"""
import os
import asyncio
import json
import logging
import re
from typing import AsyncIterator, List, Dict, Optional

import aiohttp

from app.domain.interfaces.tts_provider import TTSProvider
from app.domain.models.ai_config import DEEPGRAM_AURA2_VOICES
from app.domain.models.conversation import AudioChunk

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Deepgram Aura-2 voices (official list from domain model constant).
# Full list: https://developers.deepgram.com/docs/tts-models
# ---------------------------------------------------------------------------
DEEPGRAM_VOICES = [
    {
        "id": voice.id,
        "name": voice.name,
        "gender": (voice.gender or "Unknown").title(),
        "language": voice.language,
    }
    for voice in DEEPGRAM_AURA2_VOICES
]

# ---------------------------------------------------------------------------
# Text Chunking Configuration (per Deepgram best practices)
# ---------------------------------------------------------------------------
# Voice assistants: 50-100 character chunks for best latency
# Call center bots: Complete sentences (most natural)
# Long-form content: 200-400 characters for better intonation
#
# Voice assistants: 50-100 character chunks for best latency (per Deepgram).
# Smaller chunks = faster time-to-first-byte + faster barge-in response
# because less audio is buffered server-side when an interrupt fires.
CHUNK_MAX_CHARS = 100
CHUNK_FIRST_MAX_CHARS = 50


def _chunk_text_by_sentences(text: str, max_first_chunk: int = CHUNK_FIRST_MAX_CHARS, max_chunk: int = CHUNK_MAX_CHARS) -> List[str]:
    """
    Split text into chunks at sentence boundaries for natural speech.
    
    Per Deepgram best practices:
    - Split at sentence boundaries (. ! ? ;)
    - First chunk should be smaller for faster time-to-first-byte
    - Subsequent chunks can be larger for better intonation
    
    Args:
        text: Input text to chunk
        max_first_chunk: Max chars for first chunk (latency critical)
        max_chunk: Max chars for subsequent chunks
    
    Returns:
        List of text chunks
    """
    if not text or not text.strip():
        return []
    
    # Clean the text first
    text = text.strip()
    
    # Split at sentence boundaries while keeping the punctuation
    # Pattern: split on (. |! |? |; ) but keep the delimiter
    sentence_pattern = r'(?<=[.!?;])\s+'
    sentences = re.split(sentence_pattern, text)
    sentences = [s.strip() for s in sentences if s.strip()]
    
    if not sentences:
        return [text] if text else []
    
    chunks = []
    is_first_chunk = True
    current_chunk = ""
    max_size = max_first_chunk if is_first_chunk else max_chunk
    
    for sentence in sentences:
        # If single sentence exceeds max, split at clause boundaries
        if len(sentence) > max_size:
            # First, flush any accumulated text
            if current_chunk:
                chunks.append(current_chunk.strip())
                current_chunk = ""
                is_first_chunk = False
                max_size = max_chunk
            
            # Split long sentence at clause boundaries (commas with conjunctions).
            # Python's regex engine requires fixed-width look-behind, so use
            # comma-delimiter splitting with look-ahead instead.
            # - ", and ...": keep conjunction at start of next clause
            # - ", ...": split at regular comma boundaries
            clause_pattern = r',\s+(?=(?:and|but|or|nor|for|yet|so)\b)|,\s+'
            clauses = re.split(clause_pattern, sentence)
            clauses = [c.strip() for c in clauses if c.strip()]
            
            current_clause_chunk = ""
            for clause in clauses:
                if len(current_clause_chunk) + len(clause) + 1 <= max_size:
                    current_clause_chunk = f"{current_clause_chunk} {clause}".strip()
                else:
                    if current_clause_chunk:
                        chunks.append(current_clause_chunk)
                        is_first_chunk = False
                        max_size = max_chunk
                    # If single clause is still too long, split at word boundaries
                    if len(clause) > max_size:
                        words = clause.split()
                        current_clause_chunk = ""
                        for word in words:
                            if len(current_clause_chunk) + len(word) + 1 <= max_size:
                                current_clause_chunk = f"{current_clause_chunk} {word}".strip()
                            else:
                                if current_clause_chunk:
                                    chunks.append(current_clause_chunk)
                                    is_first_chunk = False
                                    max_size = max_chunk
                                current_clause_chunk = word
                    else:
                        current_clause_chunk = clause
            
            if current_clause_chunk:
                chunks.append(current_clause_chunk)
                is_first_chunk = False
                max_size = max_chunk
        
        # Normal case: try to add sentence to current chunk
        elif len(current_chunk) + len(sentence) + 1 <= max_size:
            current_chunk = f"{current_chunk} {sentence}".strip()
        else:
            # Flush current chunk and start new one
            if current_chunk:
                chunks.append(current_chunk.strip())
                is_first_chunk = False
                max_size = max_chunk
            current_chunk = sentence
    
    # Don't forget the last chunk
    if current_chunk:
        chunks.append(current_chunk.strip())
    
    return chunks if chunks else [text]


class DeepgramTTSProvider(TTSProvider):
    """
    Deepgram TTS provider using the **streaming WebSocket API** (Aura-2).
    
    Implements Deepgram best practices:
    - Text chunking for reduced latency and natural speech patterns
    - First-chunk optimization: smaller initial chunks for faster playback start
    - One WebSocket per synthesis call (clean state for each utterance)
    - Proper message flow: Speak → Flush → Flushed → Close
    - container=none to avoid WAV header click artifacts
    """

    TTS_WS_URL = "wss://api.deepgram.com/v1/speak"

    def __init__(self):
        self._api_key: Optional[str] = None
        self._config: Dict = {}
        self._default_voice: str = "aura-2-andromeda-en"  # Customer service optimized
        self._sample_rate: int = 24000  # Deepgram streaming default
        self._session: Optional[aiohttp.ClientSession] = None
        # Warm (persistent) WebSocket — reused across synthesis calls
        self._warm_ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._warm_ws_voice: Optional[str] = None
        self._warm_ws_rate: Optional[int] = None
        # Prevents concurrent synthesis on same connection
        self._synthesis_lock: Optional[asyncio.Lock] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self, config: dict) -> None:
        """Initialise with API key, voice, and sample rate."""
        self._config = config
        self._api_key = config.get("api_key") or os.getenv("DEEPGRAM_API_KEY")

        if not self._api_key:
            raise ValueError(
                "Deepgram API key not found in config or DEEPGRAM_API_KEY env var"
            )

        self._default_voice = config.get("voice_id", "aura-2-andromeda-en")
        # Deepgram recommends 24000 for streaming, but support 16000 if explicitly set
        requested_rate = config.get("sample_rate", 24000)
        self._sample_rate = 24000 if requested_rate not in (8000, 16000, 24000, 32000, 48000) else requested_rate

        self._session = aiohttp.ClientSession()
        self._synthesis_lock = asyncio.Lock()

        # Pre-warm the WebSocket connection so the first synthesis has zero cold-start
        try:
            await self._get_connection(self._default_voice, self._sample_rate)
            logger.info(
                f"DeepgramTTS warm connection established: voice={self._default_voice}, "
                f"rate={self._sample_rate}"
            )
        except Exception as e:
            logger.warning(f"DeepgramTTS warm connection failed, will retry on first synthesis: {e}")

    async def clear_queue(self) -> None:
        """Send Deepgram Clear message to cancel buffered TTS audio.

        Per Deepgram docs (https://developers.deepgram.com/docs/tts-ws-clear):
        Clear resets the internal text and audio buffer and stops sending new
        audio chunks as soon as possible.  This is the correct way to handle
        barge-in — without it, already-buffered audio continues arriving after
        the caller starts speaking.

        After Clear the warm WebSocket is in a dirty state (a ``ClearResponse``
        event is pending), so we discard it and let the next synthesis open a
        fresh connection.
        """
        ws = self._warm_ws
        if ws is not None and not ws.closed:
            try:
                await ws.send_json({"type": "Clear"})
                logger.debug("Sent Deepgram TTS Clear message")
            except Exception as exc:
                logger.debug("Failed to send TTS Clear: %s", exc)
            # The connection now has a pending ClearResponse + possibly stale
            # audio bytes.  Discard it so the next synthesis starts clean.
            self._warm_ws = None
            try:
                await ws.close()
            except Exception:
                pass

    async def cleanup(self) -> None:
        """Release resources."""
        if self._warm_ws and not self._warm_ws.closed:
            try:
                await self._warm_ws.send_json({"type": "Close"})
                await self._warm_ws.close()
            except Exception:
                pass
            self._warm_ws = None
        if self._session:
            await self._session.close()
            self._session = None

    async def _get_connection(
        self, voice_id: str, rate: int
    ) -> "aiohttp.ClientWebSocketResponse":
        """Return the warm WebSocket if still open and params match, else open a new one."""
        if (
            self._warm_ws is not None
            and not self._warm_ws.closed
            and self._warm_ws_voice == voice_id
            and self._warm_ws_rate == rate
        ):
            return self._warm_ws

        # Close stale connection if params changed
        if self._warm_ws and not self._warm_ws.closed:
            try:
                await self._warm_ws.send_json({"type": "Close"})
                await self._warm_ws.close()
            except Exception:
                pass

        url = (
            f"{self.TTS_WS_URL}"
            f"?model={voice_id}"
            f"&encoding=linear16"
            f"&sample_rate={rate}"
            f"&container=none"
        )
        headers = {"Authorization": f"Token {self._api_key}"}
        self._warm_ws = await self._session.ws_connect(url, headers=headers, heartbeat=30)
        self._warm_ws_voice = voice_id
        self._warm_ws_rate = rate
        return self._warm_ws

    # ------------------------------------------------------------------
    # Streaming TTS via WebSocket with Text Chunking
    # ------------------------------------------------------------------

    async def stream_synthesize(
        self,
        text: str,
        voice_id: str,
        sample_rate: int = 24000,
        **kwargs,
    ) -> AsyncIterator[AudioChunk]:
        """
        Synthesise speech using the Deepgram streaming WebSocket API with text chunking.
        
        Implements Deepgram best practices:
        - Text chunking at sentence boundaries for natural speech
        - First-chunk optimization: smaller first chunk for faster playback
        - Sequential processing: each chunk sent as separate Speak message
        - Wait for Flushed confirmation before closing
        
        Per Deepgram docs:
        - encoding=linear16 → raw 16-bit PCM (no container)
        - container=none → avoids WAV header clicks
        - sample_rate: 8000 | 16000 | 24000 | 32000 | 48000
        
        Args:
            text: Text to synthesize
            voice_id: Voice model ID (e.g., "aura-2-andromeda-en")
            sample_rate: Output sample rate
            
        Yields:
            AudioChunk with raw Int16-PCM data
        """
        if not self._session:
            raise RuntimeError(
                "DeepgramTTSProvider not initialised. Call initialize() first."
            )

        selected_voice = voice_id or self._default_voice
        rate = sample_rate or self._sample_rate

        # Do NOT re-chunk here.  The streaming pipeline (_stream_llm_and_tts)
        # already performs sentence-level splitting via _find_sentence_end before
        # calling this method — so `text` is always one complete sentence.
        # Splitting a sentence further into 50-100 char sub-chunks causes Deepgram
        # to synthesise each piece independently, resetting its prosodic model at
        # every boundary.  The audible result is unnatural pitch/rhythm breaks
        # mid-sentence ("I understand your concern" ← pause → "and I'd be happy…").
        # Sending the full sentence as one Speak message lets Deepgram plan intonation
        # across the whole sentence, producing natural-sounding speech.
        # _chunk_text_by_sentences is still used by synthesize_raw (REST path) where
        # arbitrary-length text may arrive.
        if not text or not text.strip():
            logger.warning("No text to synthesize")
            return
        chunks = [text.strip()]

        logger.debug("TTS: sending sentence as single Speak message (%d chars)", len(text))

        # Acquire synthesis lock — prevents concurrent callers from interleaving
        # messages on the shared warm connection. Released in the finally block.
        await self._synthesis_lock.acquire()
        sender_task: Optional[asyncio.Task] = None
        # Track whether synthesis completed cleanly with a Flushed event.
        # If False in the finally block the warm WS is in a dirty state
        # (stale audio/Flushed messages buffered) and must be discarded so the
        # next synthesis opens a fresh connection rather than reading stale data.
        flushed_cleanly = False
        try:
            try:
                ws = await self._get_connection(selected_voice, rate)
            except aiohttp.WSServerHandshakeError as e:
                if e.status == 400:
                    logger.error(
                        "Deepgram TTS rejected model/voice '%s' (HTTP 400)",
                        selected_voice,
                    )
                    raise RuntimeError(
                        f"Deepgram rejected TTS voice/model '{selected_voice}'. "
                        "Please choose a valid Aura-2 voice id."
                    )
                logger.error(f"Deepgram TTS WS handshake error: {e}")
                raise RuntimeError(f"Deepgram TTS handshake error: {e}")
            except aiohttp.ClientError as e:
                logger.error(f"Deepgram TTS WS network error: {e}")
                raise RuntimeError(f"Deepgram TTS network error: {e}")

            # Send all text chunks + Flush concurrently while the receive loop
            # is already running — Deepgram starts synthesising on the first
            # Speak message, so first audio arrives ~75ms after the first send,
            # without waiting for all chunks to be transmitted.
            async def _send_all() -> None:
                for chunk in chunks:
                    if chunk.strip():
                        await ws.send_json({"type": "Speak", "text": chunk})
                await ws.send_json({"type": "Flush"})

            sender_task = asyncio.create_task(_send_all())

            try:
                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.BINARY:
                        yield AudioChunk(data=msg.data, sample_rate=rate, channels=1)

                    elif msg.type == aiohttp.WSMsgType.TEXT:
                        data = json.loads(msg.data)
                        event_type = data.get("type", "")

                        if event_type == "Flushed":
                            logger.debug("Deepgram TTS Flushed — synthesis complete")
                            flushed_cleanly = True
                            break
                        elif event_type == "Warning":
                            logger.warning(f"Deepgram TTS warning: {data.get('warn_msg', data)}")
                        elif event_type == "Error":
                            err_msg = data.get("err_msg", data)
                            logger.error(f"Deepgram TTS error: {err_msg}")
                            raise RuntimeError(f"Deepgram TTS error: {err_msg}")
                        elif event_type == "Metadata":
                            logger.debug(f"Deepgram TTS metadata: {data}")

                    elif msg.type in (
                        aiohttp.WSMsgType.CLOSED,
                        aiohttp.WSMsgType.CLOSING,
                        aiohttp.WSMsgType.ERROR,
                    ):
                        logger.warning(f"Deepgram TTS WS closed unexpectedly: {msg}")
                        self._warm_ws = None  # mark stale; next call reconnects
                        break
            except Exception:
                self._warm_ws = None
                raise

        except Exception as e:
            if not isinstance(e, RuntimeError):
                logger.error(f"Deepgram TTS WS synthesis failed: {e}", exc_info=True)
                raise RuntimeError(f"Deepgram TTS synthesis failed: {e}")
            raise
        finally:
            if sender_task is not None and not sender_task.done():
                sender_task.cancel()
                try:
                    # asyncio.shield prevents outer-task cancellation from
                    # propagating into sender_task while we wait for it to
                    # acknowledge its own cancellation.  The 0.5s timeout
                    # bounds the wait in case the task is stuck writing to a
                    # dead socket (otherwise the synthesis_lock stays held for
                    # the full sock_read=15s OS timeout).
                    await asyncio.wait_for(asyncio.shield(sender_task), timeout=0.5)
                except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                    pass
            # If synthesis was interrupted (barge-in, error, or GeneratorExit from
            # the outer async-for break), the warm WebSocket has unread messages
            # (stale audio chunks and/or the Flushed event) that would be consumed
            # by the *next* synthesis call — causing it to read stale data and yield
            # nothing (TTS-total: 0ms, silence on all subsequent turns).
            # Discard the connection so _get_connection opens a fresh one next time.
            if not flushed_cleanly:
                # Snapshot voice/rate before nulling so the pre-warm task can
                # use the same parameters as the interrupted synthesis.
                _prewarm_voice = self._warm_ws_voice or self._default_voice
                _prewarm_rate  = self._warm_ws_rate  or self._sample_rate
                _stale_ws = self._warm_ws
                self._warm_ws = None
                # Explicitly close the stale connection so aiohttp releases the
                # underlying TCP socket immediately rather than waiting for GC or
                # the 30s heartbeat to time it out.  Without this, frequent barge-ins
                # accumulate leaked file descriptors.
                if _stale_ws is not None and not _stale_ws.closed:
                    try:
                        await _stale_ws.close()
                    except Exception:
                        pass
                # Kick off a background reconnect immediately so the warm WS is
                # ready before the next turn starts (~200-500ms later).  Without
                # this, the next synthesis call incurs a cold WebSocket handshake
                # (~75ms) that could otherwise be hidden during barge-in processing.
                asyncio.create_task(
                    self._prewarm_after_interrupt(_prewarm_voice, _prewarm_rate),
                    name="deepgram_tts_prewarm",
                )
            self._synthesis_lock.release()

    # ------------------------------------------------------------------
    # Background pre-warm after barge-in
    # ------------------------------------------------------------------

    async def _prewarm_after_interrupt(self, voice_id: str, rate: int) -> None:
        """
        Re-establish the warm WebSocket connection in the background after a
        barge-in discards the previous one.

        The next synthesis call arrives ~200-500ms after barge-in (after the
        caller finishes speaking and LLM generates a response).  Running the
        ~75ms WebSocket handshake here hides it behind that dead time so the
        next turn starts with zero cold-start overhead.

        Guards:
        - No-op if a warm WS already exists (another path re-warmed it first).
        - Synthesis lock is NOT held — this runs freely in the background.
        - Any exception is swallowed; a failure just means the next synthesis
          opens its own connection via _get_connection as normal.
        """
        if self._warm_ws is not None and not self._warm_ws.closed:
            return  # already warm — nothing to do
        try:
            await self._get_connection(voice_id, rate)
            logger.debug(
                "DeepgramTTS: background pre-warm complete (voice=%s, rate=%d)",
                voice_id, rate,
            )
        except Exception as exc:
            logger.debug("DeepgramTTS: background pre-warm failed: %s", exc)

    # ------------------------------------------------------------------
    # Raw synthesis (for telephony / RTP — keeps REST fallback)
    # ------------------------------------------------------------------

    async def synthesize_raw(
        self,
        text: str,
        voice_id: str,
        sample_rate: int = 24000,
    ) -> bytes:
        """
        Synthesise speech and return raw Int16-PCM bytes via REST.
        
        Uses text chunking for better quality and lower latency.
        
        Useful for telephony (RTP) where we need the full buffer at once.
        """
        if not self._session:
            raise RuntimeError("DeepgramTTSProvider not initialised.")

        selected_voice = voice_id or self._default_voice
        
        # Chunk the text for better results
        chunks = _chunk_text_by_sentences(text)
        if not chunks:
            return b""
        
        # For REST API, we concatenate all chunks with slight delays
        # to simulate natural pauses between sentences
        audio_parts = []
        
        for i, chunk in enumerate(chunks):
            url = (
                f"https://api.deepgram.com/v1/speak"
                f"?model={selected_voice}"
                f"&encoding=linear16"
                f"&sample_rate={sample_rate}"
                f"&container=none"
            )

            headers = {
                "Authorization": f"Token {self._api_key}",
                "Content-Type": "application/json",
            }

            async with self._session.post(
                url, json={"text": chunk}, headers=headers
            ) as response:
                if response.status != 200:
                    error_text = await response.text()
                    raise RuntimeError(
                        f"Deepgram TTS error ({response.status}): {error_text}"
                    )
                audio_data = await response.read()
                audio_parts.append(audio_data)
                
                # Add small silence between sentences (100ms of 16-bit silence)
                if i < len(chunks) - 1:
                    silence_samples = int(sample_rate * 0.1)  # 100ms
                    silence_bytes = b'\x00\x00' * silence_samples
                    audio_parts.append(silence_bytes)
        
        return b"".join(audio_parts)

    # ------------------------------------------------------------------
    # Voice catalogue
    # ------------------------------------------------------------------

    async def get_available_voices(self) -> List[Dict]:
        """Return available Deepgram Aura-2 voices."""
        return [
            {
                "id": v["id"],
                "name": v["name"],
                "language": v["language"],
                "gender": v["gender"],
                "description": f"Deepgram Aura-2 {v['gender']} voice",
            }
            for v in DEEPGRAM_VOICES
        ]

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "deepgram"

    def __repr__(self) -> str:
        return f"DeepgramTTSProvider(voice={self._default_voice})"
