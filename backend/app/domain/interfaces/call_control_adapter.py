"""
Generic PBX Call Control Adapter Interface.

Defines the abstract contract that every B2BUA backend must implement so the AI
pipeline never has to know whether Asterisk, FreeSWITCH, or any future PBX is
handling the call.

Implementing classes:
  - FreeSwitchAdapter  (backend/app/infrastructure/telephony/freeswitch_adapter.py)
  - AsteriskAdapter    (backend/app/infrastructure/telephony/asterisk_adapter.py)
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Callable, Coroutine, Dict, Optional


class CallControlAdapter(ABC):
    """
    Abstract interface for PBX call control.

    All methods are async so that implementations can use non-blocking I/O
    (ESL, ARI, HTTP, WebSocket) without the caller caring about the transport.
    """

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @abstractmethod
    async def connect(self, config: Optional[Dict[str, Any]] = None) -> None:
        """
        Connect to the PBX control interface (ESL socket, ARI WebSocket, …).
        Raises RuntimeError if the connection cannot be established.
        """

    @abstractmethod
    async def disconnect(self) -> None:
        """Cleanly close the PBX control connection."""

    @abstractmethod
    async def health_check(self) -> bool:
        """
        Return True if the PBX is reachable and functional.
        Used by the AdapterFactory for auto-detection / failover.
        """

    # ------------------------------------------------------------------
    # Call event callbacks
    # ------------------------------------------------------------------

    @abstractmethod
    async def on_call_arrived(self, call_id: str, callback: Callable[..., Coroutine]) -> None:
        """Register a coroutine to be called when an inbound call is answered."""

    @abstractmethod
    async def on_call_ended(self, call_id: str, callback: Callable[..., Coroutine]) -> None:
        """Register a coroutine to be called when a call is torn down."""

    # ------------------------------------------------------------------
    # Audio I/O
    # ------------------------------------------------------------------

    @abstractmethod
    async def start_audio_stream(self, call_id: str) -> None:
        """
        Tell the PBX to start streaming audio for *call_id* toward the backend.
        After this the adapter's internal mechanism begins delivering audio
        chunks to any registered audio callback.
        """

    @abstractmethod
    async def send_tts_audio(self, call_id: str, pcmu_audio: bytes) -> None:
        """
        Play *pcmu_audio* (G.711 µ-law, 8 kHz, mono) to the caller.
        Implementations must handle chunking / timing as needed.
        """

    @abstractmethod
    async def interrupt_tts(self, call_id: str) -> None:
        """
        Stop any currently playing TTS audio immediately (barge-in support).
        Silently succeeds if no audio is playing.
        """

    # ------------------------------------------------------------------
    # Call control
    # ------------------------------------------------------------------

    @abstractmethod
    async def originate_call(self, destination: str, caller_id: str) -> str:
        """
        Originate an outbound call to *destination* with the given *caller_id*.
        Returns the PBX-assigned call UUID string.
        """

    @abstractmethod
    async def hangup(self, call_id: str) -> None:
        """Hang up the call identified by *call_id*."""

    @abstractmethod
    async def transfer(
        self,
        call_id: str,
        destination: str,
        mode: str = "blind",
    ) -> Dict[str, Any]:
        """
        Transfer *call_id* to *destination*.
        *mode* is one of: "blind", "attended", "deflect".
        Returns a dict with at least {"status": str, "attempt_id": str}.
        """

    # ------------------------------------------------------------------
    # Global event callbacks (bridge-level)
    # ------------------------------------------------------------------

    def register_call_event_handlers(
        self,
        on_new_call: Callable[..., Coroutine],
        on_call_ended: Callable[..., Coroutine],
        on_audio_received: Optional[Callable[..., Coroutine]] = None,
    ) -> None:
        """
        Register global event handlers for all calls on this adapter.

        Called once by the telephony bridge after adapter creation.
        Implementations wire these into their internal event systems
        (ESL events, ARI WebSocket events, etc.).

        Default implementation is a no-op; concrete adapters override.
        """

    def get_transfer_metrics(self) -> Dict[str, Any]:
        """
        Return transfer metrics from this adapter.

        Returns a dict with at least:
            {"attempts": int, "successes": int, "inflight": int}

        Default returns zeros.  Override in adapters that track transfers.
        """
        return {"attempts": 0, "successes": 0, "inflight": 0}

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable adapter name, e.g. 'freeswitch' or 'asterisk'."""

    @property
    @abstractmethod
    def connected(self) -> bool:
        """True while the adapter holds a live connection to the PBX."""
