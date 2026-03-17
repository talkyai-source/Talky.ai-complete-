"""
FreeSWITCH implementation of the generic CallControlAdapter interface.

Wraps the existing FreeSwitchESL (dual-connection ESL client) and
FreeSwitchAudioBridge (mod_audio_fork WebSocket bridge) behind the
platform-agnostic CallControlAdapter contract.

The AI pipeline only talks to CallControlAdapter; it never imports
any FreeSWITCH-specific class directly.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Callable, Coroutine, Dict, Optional

from app.domain.interfaces.call_control_adapter import CallControlAdapter
from app.infrastructure.telephony.freeswitch_esl import FreeSwitchESL, ESLConfig
from app.infrastructure.telephony.freeswitch_audio_bridge import FreeSwitchAudioBridge

logger = logging.getLogger(__name__)


class FreeSwitchAdapter(CallControlAdapter):
    """
    CallControlAdapter backed by FreeSWITCH ESL + mod_audio_fork.

    Audio flow:
      Caller → FreeSWITCH mod_audio_fork → WebSocket → FreeSwitchAudioBridge
               → on_audio callback → STT → LLM → TTS
      TTS audio → send_tts_audio() → FreeSwitchAudioBridge.send_audio() → FreeSWITCH → Caller
    """

    def __init__(
        self,
        esl_host: str | None = None,
        esl_port: int | None = None,
        esl_password: str | None = None,
        audio_fork_ws_url: str | None = None,
    ) -> None:
        self._esl_config = ESLConfig(
            host=esl_host or os.getenv("FREESWITCH_ESL_HOST", "127.0.0.1"),
            port=int(esl_port or os.getenv("FREESWITCH_ESL_PORT", "8021")),
            password=esl_password or os.getenv("FREESWITCH_ESL_PASSWORD", "ClueCon"),
        )
        self._audio_fork_ws_url = audio_fork_ws_url or os.getenv(
            "FREESWITCH_AUDIO_FORK_WS_URL",
            "ws://127.0.0.1:8000/api/v1/sip/telephony/ws-audio",
        )
        self._esl: FreeSwitchESL = FreeSwitchESL(self._esl_config)
        self._audio_bridge: FreeSwitchAudioBridge = FreeSwitchAudioBridge()
        self._connected_flag: bool = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "freeswitch"

    @property
    def connected(self) -> bool:
        return self._connected_flag and self._esl.connected

    async def connect(self, config: Optional[Dict[str, Any]] = None) -> None:
        if config:
            if "esl_host" in config:
                self._esl_config.host = config["esl_host"]
            if "esl_port" in config:
                self._esl_config.port = int(config["esl_port"])
            if "esl_password" in config:
                self._esl_config.password = config["esl_password"]
            if "audio_fork_ws_url" in config:
                self._audio_fork_ws_url = config["audio_fork_ws_url"]
            self._esl = FreeSwitchESL(self._esl_config)

        ok = await self._esl.connect()
        if not ok:
            raise RuntimeError(
                f"FreeSwitchAdapter: could not connect ESL to "
                f"{self._esl_config.host}:{self._esl_config.port}"
            )
        self._connected_flag = True
        logger.info("FreeSwitchAdapter connected to FreeSWITCH ESL")

    async def disconnect(self) -> None:
        self._connected_flag = False
        await self._esl.disconnect()
        logger.info("FreeSwitchAdapter disconnected")

    async def health_check(self) -> bool:
        """Probe FreeSWITCH ESL with a status command."""
        try:
            probe = FreeSwitchESL(self._esl_config)
            ok = await asyncio.wait_for(probe.connect(), timeout=3.0)
            if ok:
                await probe.disconnect()
            return ok
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Call event callbacks — delegate to ESL event handlers
    # ------------------------------------------------------------------

    async def on_call_arrived(self, call_id: str, callback: Callable[..., Coroutine]) -> None:
        """Register callback invoked when the call with *call_id* is answered."""
        original = self._esl._on_call_start

        async def _dispatch(uuid: str) -> None:
            if uuid == call_id:
                await callback(uuid)
            elif original:
                await original(uuid)

        self._esl._on_call_start = _dispatch

    async def on_call_ended(self, call_id: str, callback: Callable[..., Coroutine]) -> None:
        """Register callback invoked when the call with *call_id* hangs up."""
        original = self._esl._on_call_end

        async def _dispatch(uuid: str) -> None:
            if uuid == call_id:
                await callback(uuid)
            elif original:
                await original(uuid)

        self._esl._on_call_end = _dispatch

    # ------------------------------------------------------------------
    # Audio I/O
    # ------------------------------------------------------------------

    async def start_audio_stream(self, call_id: str) -> None:
        """
        Tell FreeSWITCH to fork audio for *call_id* to the backend WebSocket.
        The mod_audio_fork sends raw L16 PCM; FreeSwitchAudioBridge receives it.
        """
        ws_url = f"{self._audio_fork_ws_url}/{call_id}"
        ok = await self._esl.start_audio_fork(call_id, ws_url)
        if not ok:
            logger.warning(
                f"FreeSwitchAdapter: start_audio_fork returned False for {call_id[:8]}"
            )

    async def send_tts_audio(self, call_id: str, pcmu_audio: bytes) -> None:
        """
        Send TTS audio (G.711 µ-law, 8 kHz, mono) back to the caller via
        the active mod_audio_fork WebSocket connection.

        The audio bridge converts it to L16 on send if needed.
        """
        sent = await self._audio_bridge.send_audio(call_id, pcmu_audio)
        if not sent:
            logger.debug(
                f"FreeSwitchAdapter.send_tts_audio: no active session for {call_id[:8]}"
            )

    async def interrupt_tts(self, call_id: str) -> None:
        """Stop playing TTS to the caller (barge-in)."""
        try:
            await self._esl.api(f"uuid_audio_fork {call_id} stop")
        except Exception as exc:
            logger.debug(f"FreeSwitchAdapter.interrupt_tts: {exc}")

    # ------------------------------------------------------------------
    # Call control
    # ------------------------------------------------------------------

    async def originate_call(self, destination: str, caller_id: str) -> str:
        uuid = await self._esl.originate_call(
            destination=destination,
            caller_id=caller_id,
        )
        if not uuid:
            raise RuntimeError(
                f"FreeSwitchAdapter: originate_call to {destination} failed"
            )
        return uuid

    async def hangup(self, call_id: str) -> None:
        await self._esl.hangup_call(call_id)

    async def transfer(
        self,
        call_id: str,
        destination: str,
        mode: str = "blind",
    ) -> Dict[str, Any]:
        from app.infrastructure.telephony.freeswitch_esl import (
            TransferRequest,
            TransferMode,
        )
        mode_map = {
            "blind": TransferMode.BLIND,
            "attended": TransferMode.ATTENDED,
            "deflect": TransferMode.DEFLECT,
        }
        transfer_mode = mode_map.get(mode, TransferMode.BLIND)
        request = TransferRequest(
            uuid=call_id,
            destination=destination,
            mode=transfer_mode,
        )
        result = await self._esl.request_transfer(request)
        return result.to_dict()

    # ------------------------------------------------------------------
    # Generic event handler registration (CallControlAdapter interface)
    # ------------------------------------------------------------------

    def register_call_event_handlers(
        self,
        on_new_call: Callable[..., Any],
        on_call_ended: Callable[..., Any],
        on_audio_received: Optional[Callable[..., Any]] = None,
    ) -> None:
        """
        Wire bridge-level callbacks into FreeSWITCH ESL events and
        the mod_audio_fork audio bridge.
        """
        import asyncio

        self._esl._on_call_start = on_new_call
        self._esl._on_call_end = on_call_ended

        if on_audio_received:
            self._audio_bridge.set_audio_callback(on_audio_received)
        self._audio_bridge.set_session_end_callback(
            lambda call_id: asyncio.create_task(on_call_ended(call_id))
        )

    def get_transfer_metrics(self) -> Dict[str, Any]:
        """Read transfer metrics from the ESL transfer result cache."""
        if not self._esl or not getattr(self._esl, "connected", False):
            return {"attempts": 0, "successes": 0, "inflight": 0}
        try:
            results = self._esl.list_transfer_results()
        except Exception:
            return {"attempts": 0, "successes": 0, "inflight": 0}

        _TERMINAL = {"completed", "succeeded", "failed", "timeout", "rejected"}
        _SUCCESS = {"completed", "succeeded"}
        _INFLIGHT = {"initiated", "ringing", "pending"}

        attempts = successes = inflight = 0
        for result in results.values():
            status_value = getattr(result, "status", "")
            if hasattr(status_value, "value"):
                status_value = status_value.value
            status = str(status_value).strip().lower()
            if status in _TERMINAL:
                attempts += 1
                if status in _SUCCESS:
                    successes += 1
            elif status in _INFLIGHT:
                inflight += 1

        return {"attempts": attempts, "successes": successes, "inflight": inflight}

    # ------------------------------------------------------------------
    # Expose the underlying audio bridge for endpoint integration
    # ------------------------------------------------------------------

    @property
    def audio_bridge(self) -> FreeSwitchAudioBridge:
        """The FreeSwitchAudioBridge instance managing active audio WebSockets."""
        return self._audio_bridge

    def set_global_audio_callback(self, callback: Callable) -> None:
        """Register a callback for *all* audio received from any FreeSWITCH call."""
        self._audio_bridge.set_audio_callback(callback)

    def set_global_session_start_callback(self, callback: Callable) -> None:
        self._audio_bridge.set_session_start_callback(callback)

    def set_global_session_end_callback(self, callback: Callable) -> None:
        self._audio_bridge.set_session_end_callback(callback)
