"""
Asterisk implementation of the generic CallControlAdapter interface.

Architecture
------------
Asterisk (B2BUA)
  └── ARI (Asterisk REST Interface) ─── controls channels / bridges
       └── ExternalMedia channel ─────── RTP to/from C++ Voice Gateway
            └── C++ Voice Gateway ─────── sends audio chunks to backend via HTTP callback
                                          receives TTS audio via POST /v1/sessions/{id}/tts

Audio path (inbound call → AI pipeline):
  Caller → Asterisk → ExternalMedia (UnicastRTP) → C++ Gateway (UDP)
         → POST /api/v1/sip/telephony/audio/{session_id} → VoicePipelineService (STT→LLM→TTS)
         → POST /v1/sessions/{session_id}/tts on C++ Gateway → Caller hears AI response

Call control path:
  AsteriskAdapter.originate_call()  ─→ ARI POST /channels
  AsteriskAdapter.hangup()          ─→ ARI DELETE /channels/{id}
  AsteriskAdapter.transfer()        ─→ ARI POST /channels/{id}/redirect
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Coroutine, Dict, List, Optional

import aiohttp

from app.domain.interfaces.call_control_adapter import CallControlAdapter

logger = logging.getLogger(__name__)

# Max lifetime of a C++ gateway media session. This was hardcoded to 300000 ms
# (5 min), which silently killed the agent's audio at exactly 5 minutes on every
# real call while the SIP channel + caller RTP stayed perfectly healthy — the
# caller heard dead air. There is no reason to cap a live answered call at 5 min;
# default to 2 hours (the gateway's own default) and make it env-tunable.
_SESSION_FINAL_TIMEOUT_MS = int(os.getenv("TELEPHONY_SESSION_FINAL_TIMEOUT_MS", "7200000"))


@dataclass
class _UnicastRtpCacheEntry:
    created_key: str
    remote_ip: Optional[str] = None
    remote_port: Optional[int] = None
    cached_at: float = 0.0


# Q.850 cause code → a snake-case string the outcome resolver recognises
# (see outcome_resolver._BUSY_CAUSES / _NO_ANSWER_CAUSES / _REJECT_CAUSES).
# Only the codes that change a dial outcome are mapped; everything else falls
# through to the resolver's session-state heuristic.
_Q850_CAUSE_TEXT: Dict[int, str] = {
    1:  "unallocated_number",       # not active / invalid → INVALID/UNREACHABLE
    17: "user_busy",                # busy
    18: "no_user_response",         # no answer
    19: "no_answer",                # no answer (user alerted, no pickup)
    20: "no_answer",                # subscriber absent / phone off
    21: "call_rejected",            # declined
    22: "unallocated_number",       # number changed
    27: "destination_out_of_order", # destination unreachable
    28: "unallocated_number",       # invalid number format
    34: "switch_congestion",        # no circuit available
    38: "switch_congestion",        # network out of order
    42: "switch_congestion",        # switching equipment congestion
    44: "switch_congestion",        # requested channel unavailable
}


class AsteriskAdapter(CallControlAdapter):
    """
    CallControlAdapter backed by Asterisk ARI + C++ Voice Gateway.

    Depends on:
      - Asterisk with ARI enabled (ari.conf, http.conf)
      - services/voice-gateway-cpp running on ASTERISK_GATEWAY_BASE_URL
    """

    def __init__(
        self,
        ari_host: str | None = None,
        ari_port: int | None = None,
        ari_username: str | None = None,
        ari_password: str | None = None,
        gateway_base_url: str | None = None,
        app_name: str | None = None,
        gateway_rtp_ip: str | None = None,
    ) -> None:
        self._ari_host = ari_host or os.getenv("ASTERISK_ARI_HOST", "127.0.0.1")
        self._ari_port = int(ari_port or os.getenv("ASTERISK_ARI_PORT", "8088"))
        self._ari_username = ari_username or os.getenv("ASTERISK_ARI_USER", "talky")
        self._ari_password = ari_password or os.getenv("ASTERISK_ARI_PASSWORD", "talky_local_only_change_me")
        if self._ari_password in ("talky_local_only_change_me", "talky", "admin", "password", ""):
            logger.warning(
                "AsteriskAdapter: ARI password is a known default — "
                "set ASTERISK_ARI_PASSWORD env var in production"
            )
        self._gateway_base_url = (gateway_base_url or os.getenv("ASTERISK_GATEWAY_BASE_URL", "http://127.0.0.1:18080")).rstrip("/")
        self._app_name = app_name or os.getenv("ASTERISK_ARI_APP", "talky_ai")
        self._gateway_rtp_ip = gateway_rtp_ip or os.getenv("ASTERISK_GATEWAY_RTP_IP", "127.0.0.1")

        self._session: Optional[aiohttp.ClientSession] = None
        self._connected_flag: bool = False
        self._ws_task: Optional[asyncio.Task] = None
        self._stop_event: asyncio.Event = asyncio.Event()

        # Active sessions: channel_id → session metadata dict
        self._active_sessions: Dict[str, Dict[str, Any]] = {}
        # channel_id → external_channel_id (UnicastRTP)
        self._ext_channels: Dict[str, str] = {}
        # channel_id → bridge_id
        self._bridges: Dict[str, str] = {}
        # channel_id → gateway session_id
        self._gateway_sessions: Dict[str, str] = {}
        # Outbound channels waiting for callee to answer:
        # channel_id → {"bridge_id": str, "listen_port": int, "session_id": str}
        self._pending_outbound: Dict[str, Dict[str, Any]] = {}
        # ChannelStateChange(Up) events that arrived before _on_outbound_stasis_start
        # ran (race condition when StasisStart is delayed in the ARI WebSocket queue).
        self._preemptive_up_channels: set = set()
        # Channel IDs originated by originate_call() — used as the primary
        # routing decision in StasisStart so we don't depend on Asterisk
        # reliably passing appArgs through PJSIP trunks.
        self._originated_channels: set[str] = set()
        self._originated_channel_order: list[str] = []

        # Q.850 / SIP hangup cause (as Asterisk's cause_txt string) captured
        # off the terminal ARI event, keyed by channel id. The outcome resolver
        # reads this (via get_hangup_cause) to tell no-answer / busy / rejected
        # apart from an agent-side hangup — without it every unanswered call was
        # mislabelled and never got its no-answer +24h reschedule. Bounded: an
        # entry is popped when consumed, and stale ones are cleaned on hangup.
        self._hangup_causes: Dict[str, str] = {}

        # Global event callbacks
        self._call_arrived_callbacks: Dict[str, Callable] = {}
        self._call_ended_callbacks: Dict[str, Callable] = {}
        # Generic new-call callback (used when call_id is not yet known at registration time)
        self._on_new_call: Optional[Callable] = None
        self._on_any_call_end: Optional[Callable] = None
        # Optional ringing-phase callback.  Fired once an outbound channel is
        # parked in the mixing bridge and is waiting for the callee to answer.
        # Used by the telephony bridge to pre-warm STT/TTS/LLM providers
        # during the 2–10 s of otherwise idle ring time, so that first-turn
        # latency after answer matches subsequent turns.
        self._on_ringing: Optional[Callable] = None
        self._on_outbound_channel_alias: Optional[Callable] = None

        # RTP port allocator (32000–32999, matching Day 5 defaults)
        self._rtp_port_start = int(os.getenv("ASTERISK_RTP_PORT_START", "32000"))
        self._rtp_port_end = int(os.getenv("ASTERISK_RTP_PORT_END", "32999"))
        self._rtp_next = self._rtp_port_start
        self._rtp_in_use: set[int] = set()
        self._rtp_lock = asyncio.Lock()
        self._channel_varset_cache: Dict[tuple[str, str], _UnicastRtpCacheEntry] = {}
        self._channel_varset_cache_ttl_s = max(
            1.0,
            float(os.getenv("ASTERISK_CHANNELVARSET_CACHE_TTL_S", "120")),
        )

        # Per-call TTS error counters (suppresses log spam when Gateway session
        # is not running — logs first error and every 50th thereafter).
        self._tts_error_counts: Dict[str, int] = {}

        # Phase C — inbound routing. channel_id → {"called_did", "context",
        # "caller_number"} extracted from the inbound StasisStart event so the
        # bridge can resolve the call to a tenant/campaign. One-time debug dump
        # of the raw StasisStart channel fields lets ops confirm WHICH field
        # actually carries the DID on the live carrier leg.
        self._inbound_call_meta: Dict[str, Dict[str, Any]] = {}
        self._inbound_debug_dumped: bool = False

    def _track_originated_channel(self, channel_id: str) -> None:
        if not channel_id:
            return
        if channel_id not in self._originated_channels:
            self._originated_channel_order.append(channel_id)
        self._originated_channels.add(channel_id)

    def _discard_originated_channel(self, channel_id: str) -> None:
        self._originated_channels.discard(channel_id)
        try:
            self._originated_channel_order.remove(channel_id)
        except ValueError:
            pass

    def _consume_oldest_originated_channel(self) -> Optional[str]:
        while self._originated_channel_order:
            channel_id = self._originated_channel_order.pop(0)
            if channel_id in self._originated_channels:
                self._originated_channels.discard(channel_id)
                return channel_id
        if not self._originated_channels:
            return None
        channel_id = next(iter(self._originated_channels))
        self._originated_channels.discard(channel_id)
        return channel_id

    def get_hangup_cause(self, channel_id: str) -> Optional[str]:
        """Return (and consume) the captured Q.850 hangup cause for a channel.

        The lifecycle's call-ended hook calls this so the outcome resolver can
        classify no-answer / busy / rejected from the real PBX cause instead of
        defaulting to an agent-side hangup. Popped so the map stays bounded.
        """
        return self._hangup_causes.pop(channel_id, None)

    def _emit_outbound_channel_alias(self, original_call_id: str, actual_call_id: str) -> None:
        if (
            not self._on_outbound_channel_alias
            or not original_call_id
            or not actual_call_id
            or original_call_id == actual_call_id
        ):
            return
        try:
            result = self._on_outbound_channel_alias(original_call_id, actual_call_id)
            if asyncio.iscoroutine(result):
                asyncio.create_task(result)
        except Exception as exc:
            logger.warning(
                "AsteriskAdapter: outbound channel alias callback failed "
                "original=%s actual=%s error=%s",
                original_call_id[:12],
                actual_call_id[:12],
                exc,
            )

    # ------------------------------------------------------------------
    # CallControlAdapter interface — identity
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "asterisk"

    @property
    def connected(self) -> bool:
        return self._connected_flag

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self, config: Optional[Dict[str, Any]] = None) -> None:
        if config:
            self._ari_host = config.get("ari_host", self._ari_host)
            self._ari_port = int(config.get("ari_port", self._ari_port))
            self._ari_username = config.get("ari_username", self._ari_username)
            self._ari_password = config.get("ari_password", self._ari_password)
            self._gateway_base_url = config.get("gateway_base_url", self._gateway_base_url)

        connector = aiohttp.TCPConnector()
        self._session = aiohttp.ClientSession(
            connector=connector,
            auth=aiohttp.BasicAuth(self._ari_username, self._ari_password),
        )

        # Verify ARI is reachable
        try:
            async with self._session.get(
                f"http://{self._ari_host}:{self._ari_port}/ari/asterisk/info",
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status not in (200, 201):
                    raise RuntimeError(f"ARI info returned {resp.status}")
        except Exception as exc:
            await self._session.close()
            raise RuntimeError(f"AsteriskAdapter: cannot reach ARI: {exc}") from exc

        self._connected_flag = True
        self._stop_event.clear()

        # Start ARI WebSocket event listener
        self._ws_task = asyncio.create_task(self._ari_event_listener())
        logger.info(
            f"AsteriskAdapter connected to ARI at {self._ari_host}:{self._ari_port} "
            f"app={self._app_name}"
        )

    async def disconnect(self) -> None:
        self._connected_flag = False
        self._stop_event.set()
        if self._ws_task:
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass
        if self._session:
            await self._session.close()
            self._session = None
        logger.info("AsteriskAdapter disconnected")

    async def health_check(self) -> bool:
        """Probe ARI /asterisk/info endpoint."""
        try:
            connector = aiohttp.TCPConnector()
            async with aiohttp.ClientSession(
                connector=connector,
                auth=aiohttp.BasicAuth(self._ari_username, self._ari_password),
            ) as sess:
                async with sess.get(
                    f"http://{self._ari_host}:{self._ari_port}/ari/asterisk/info",
                    timeout=aiohttp.ClientTimeout(total=3),
                ) as resp:
                    return resp.status == 200
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ari_url(self, path: str) -> str:
        return f"http://{self._ari_host}:{self._ari_port}/ari{path}"

    async def _ari(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
        ok: tuple = (200, 201, 204),
    ) -> Any:
        if not self._session:
            raise RuntimeError("AsteriskAdapter not connected")
        async with self._session.request(
            method,
            self._ari_url(path),
            params=params,
            json=json_body,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status not in ok:
                body = await resp.text()
                raise RuntimeError(f"ARI {method} {path} → {resp.status}: {body[:300]}")
            try:
                return await resp.json(content_type=None)
            except Exception:
                return {}

    async def list_active_channel_ids(self) -> Optional[set]:
        """Return the set of channel IDs Asterisk currently has up.

        Ground truth for the session watchdog's zombie reconcile: a local
        voice session whose call_id is NOT in this set corresponds to a
        channel Asterisk has already torn down — i.e. a ChannelDestroyed
        event we missed. Such a session must be force-ended so it releases
        its global concurrency slot (otherwise the slot leaks until a long
        inactivity timeout, and ~10 leaks block ALL outbound calls).

        Returns ``None`` (not an empty set) when ARI can't be queried, so
        the caller can distinguish "no channels" from "couldn't check" and
        skip the reconcile rather than wrongly tearing down live calls.
        """
        if not self._session:
            return None
        try:
            channels = await self._ari("GET", "/channels")
        except Exception as exc:  # noqa: BLE001
            logger.debug("ari_list_channels_failed err=%s", exc)
            return None
        if not isinstance(channels, list):
            return None
        ids: set = set()
        for ch in channels:
            cid = ch.get("id") if isinstance(ch, dict) else None
            if cid:
                ids.add(cid)
        return ids

    async def _gateway(
        self,
        method: str,
        path: str,
        payload: Optional[Dict[str, Any]] = None,
        ok: tuple = (200,),
    ) -> Any:
        if not self._session:
            raise RuntimeError("AsteriskAdapter not connected")
        async with self._session.request(
            method,
            f"{self._gateway_base_url}{path}",
            json=payload,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status not in ok:
                body = await resp.text()
                raise RuntimeError(
                    f"Gateway {method} {path} → {resp.status}: {body[:300]}"
                )
            try:
                return await resp.json(content_type=None)
            except Exception:
                return {}

    async def _alloc_rtp_port(self) -> int:
        async with self._rtp_lock:
            span = self._rtp_port_end - self._rtp_port_start + 1
            for _ in range(span):
                candidate = self._rtp_next
                self._rtp_next += 1
                if self._rtp_next > self._rtp_port_end:
                    self._rtp_next = self._rtp_port_start
                if candidate not in self._rtp_in_use:
                    self._rtp_in_use.add(candidate)
                    return candidate
            raise RuntimeError("AsteriskAdapter: no free RTP port available")

    async def _release_rtp_port(self, port: int) -> None:
        async with self._rtp_lock:
            self._rtp_in_use.discard(port)

    def _channel_created_key(self, channel: Optional[Dict[str, Any]]) -> str:
        if not channel:
            return ""
        for key in ("creationtime", "creationTime", "created_at"):
            value = channel.get(key)
            if value:
                return str(value)
        return ""

    def _channel_cache_key(self, channel_id: str, created_key: str) -> tuple[str, str]:
        return (channel_id, created_key)

    def _purge_expired_channel_varset_cache(self, now: float) -> None:
        expired = [
            key
            for key, entry in self._channel_varset_cache.items()
            if now - entry.cached_at > self._channel_varset_cache_ttl_s
        ]
        for key in expired:
            self._channel_varset_cache.pop(key, None)

    def _update_channel_varset_cache(
        self,
        *,
        channel_id: str,
        channel: Optional[Dict[str, Any]],
        variable: str,
        value: Any,
        now: float,
    ) -> None:
        if variable not in {"UNICASTRTP_LOCAL_ADDRESS", "UNICASTRTP_LOCAL_PORT"}:
            return

        self._purge_expired_channel_varset_cache(now)
        created_key = self._channel_created_key(channel)
        key = self._channel_cache_key(channel_id, created_key)
        entry = self._channel_varset_cache.get(key)
        if not entry:
            entry = _UnicastRtpCacheEntry(created_key=created_key, cached_at=now)
            self._channel_varset_cache[key] = entry

        entry.cached_at = now
        if variable == "UNICASTRTP_LOCAL_ADDRESS":
            entry.remote_ip = str(value or "127.0.0.1")
        elif variable == "UNICASTRTP_LOCAL_PORT":
            try:
                entry.remote_port = int(value)
            except (TypeError, ValueError):
                entry.remote_port = None

    def _cache_unicastrtp_local(
        self,
        *,
        channel_id: str,
        channel: Optional[Dict[str, Any]],
        remote_ip: str,
        remote_port: int,
        now: float,
    ) -> None:
        self._update_channel_varset_cache(
            channel_id=channel_id,
            channel=channel,
            variable="UNICASTRTP_LOCAL_ADDRESS",
            value=remote_ip,
            now=now,
        )
        self._update_channel_varset_cache(
            channel_id=channel_id,
            channel=channel,
            variable="UNICASTRTP_LOCAL_PORT",
            value=remote_port,
            now=now,
        )

    def _get_cached_unicastrtp_local(
        self,
        *,
        channel_id: str,
        channel: Optional[Dict[str, Any]],
        now: float,
    ) -> Optional[tuple[str, int]]:
        self._purge_expired_channel_varset_cache(now)
        created_key = self._channel_created_key(channel)

        candidates: list[_UnicastRtpCacheEntry] = []
        if created_key:
            entry = self._channel_varset_cache.get(self._channel_cache_key(channel_id, created_key))
            if entry:
                candidates.append(entry)
        else:
            for (cached_channel_id, _), entry in self._channel_varset_cache.items():
                if cached_channel_id == channel_id:
                    candidates.append(entry)

        if not candidates:
            return None

        freshest = max(candidates, key=lambda item: item.cached_at)
        if freshest.remote_ip and freshest.remote_port:
            return freshest.remote_ip, freshest.remote_port
        return None

    async def _resolve_unicastrtp_local(
        self,
        *,
        channel_id: str,
        channel: Optional[Dict[str, Any]],
    ) -> tuple[str, int]:
        loop = asyncio.get_running_loop()
        now = loop.time()
        cached = self._get_cached_unicastrtp_local(channel_id=channel_id, channel=channel, now=now)
        if cached:
            return cached

        addr_var = await self._ari(
            "GET", f"/channels/{channel_id}/variable",
            params={"variable": "UNICASTRTP_LOCAL_ADDRESS"},
        )
        remote_ip = str(addr_var.get("value", "") or "127.0.0.1")

        remote_port = 0
        for attempt in range(6):
            port_var = await self._ari(
                "GET", f"/channels/{channel_id}/variable",
                params={"variable": "UNICASTRTP_LOCAL_PORT"},
            )
            raw_port = port_var.get("value", 0)
            try:
                remote_port = int(raw_port) if raw_port else 0
            except (TypeError, ValueError):
                remote_port = 0
            if remote_port:
                break
            if attempt < 5:
                await asyncio.sleep(0.1)

        if not remote_port:
            raise RuntimeError(
                f"UNICASTRTP_LOCAL_PORT returned 0 after retries for "
                f"channel={channel_id[:12]}"
            )

        self._cache_unicastrtp_local(
            channel_id=channel_id,
            channel=channel,
            remote_ip=remote_ip,
            remote_port=remote_port,
            now=loop.time(),
        )
        return remote_ip, remote_port

    def _drop_channel_varset_cache(self, channel_id: str) -> None:
        stale_keys = [
            key for key in self._channel_varset_cache
            if key[0] == channel_id
        ]
        for key in stale_keys:
            self._channel_varset_cache.pop(key, None)

    # ------------------------------------------------------------------
    # ARI WebSocket event listener
    # ------------------------------------------------------------------

    async def _ari_event_listener(self) -> None:
        """Connect to the ARI WebSocket and dispatch events."""
        import aiohttp
        api_key = f"{self._ari_username}:{self._ari_password}"
        ws_url = (
            f"ws://{self._ari_host}:{self._ari_port}/ari/events"
            f"?app={self._app_name}&api_key={api_key}"
        )
        safe_url = ws_url.replace(api_key, f"{self._ari_username}:***")
        logger.info("AsteriskAdapter: connecting ARI WS %s", safe_url)

        connector = aiohttp.TCPConnector()
        _reconnect_attempts = 0
        async with aiohttp.ClientSession(connector=connector) as sess:
            while not self._stop_event.is_set():
                try:
                    async with sess.ws_connect(
                        ws_url,
                        heartbeat=20,
                        timeout=aiohttp.ClientWSTimeout(ws_close=5),
                    ) as ws:
                        _reconnect_attempts = 0
                        logger.info("AsteriskAdapter: ARI WS connected")
                        async for msg in ws:
                            if self._stop_event.is_set():
                                break
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                import json
                                try:
                                    event = json.loads(msg.data)
                                    await self._handle_ari_event(event)
                                except Exception as exc:
                                    logger.debug(f"ARI event parse error: {exc}")
                            elif msg.type in (
                                aiohttp.WSMsgType.ERROR,
                                aiohttp.WSMsgType.CLOSED,
                            ):
                                break
                except asyncio.CancelledError:
                    return
                except Exception as exc:
                    if self._stop_event.is_set():
                        return
                    _reconnect_attempts += 1
                    delay = min(0.25 * (2 ** (_reconnect_attempts - 1)), 30.0) * (0.5 + random.random())
                    logger.warning(
                        "AsteriskAdapter: ARI WS disconnected (attempt=%d, retry_in=%.1fs) — %s",
                        _reconnect_attempts, delay, exc,
                        extra={"ari_reconnect_attempt": _reconnect_attempts, "retry_delay_s": round(delay, 2)},
                    )
                    await asyncio.sleep(delay)

    async def _handle_ari_event(self, event: Dict[str, Any]) -> None:
        """Process ARI events and drive the session lifecycle."""
        event_type = str(event.get("type", ""))
        channel = event.get("channel") or {}
        channel_id = str(channel.get("id") or "")
        channel_name = str(channel.get("name") or "")
        loop = asyncio.get_running_loop()

        if event_type in {"ChannelVarset", "ChannelVarSet"} and channel_id:
            self._update_channel_varset_cache(
                channel_id=channel_id,
                channel=channel,
                variable=str(event.get("variable") or ""),
                value=event.get("value"),
                now=loop.time(),
            )
            return

        if event_type == "StasisStart":
            args: List[str] = event.get("args", [])
            # Skip UnicastRTP (external media) channels
            if channel_name.startswith("UnicastRTP/"):
                return

            # --- Outbound routing decision ---
            # Three ways to identify an outbound channel:
            # 1. channel_id matches a pre-generated ID in _originated_channels
            # 2. appArgs[0] == "outbound" (unreliable through PJSIP trunks)
            # 3. _originated_channels is non-empty — when originating through
            #    a PJSIP trunk (e.g. PJSIP/1002@lan-pbx), Asterisk creates
            #    a NEW channel for the trunk leg with a different ID than the
            #    one we requested.  The pre-generated ID never enters Stasis;
            #    the trunk-created channel does.  If we have any pending
            #    originated IDs, this StasisStart is almost certainly the
            #    trunk-created leg of that origination.
            is_our_originated = channel_id in self._originated_channels
            arg0 = args[0] if args else ""
            is_trunk_leg = (
                not is_our_originated
                and len(self._originated_channels) > 0
                and channel_name.startswith("PJSIP/")
            )

            if is_our_originated or arg0 == "outbound" or is_trunk_leg:
                if is_trunk_leg:
                    # Consume the oldest pending originated ID since this is
                    # its trunk leg. The queue keeps concurrent outbound calls
                    # deterministic instead of relying on set iteration order.
                    stale_id = self._consume_oldest_originated_channel()
                    if stale_id is None:
                        return
                    self._emit_outbound_channel_alias(stale_id, channel_id)
                    logger.info(
                        f"AsteriskAdapter: matched trunk-created channel "
                        f"{channel_id[:12]} to originated {stale_id[:12]}"
                    )
                elif not is_our_originated and arg0 == "outbound" and len(self._originated_channels) == 1:
                    stale_id = self._consume_oldest_originated_channel()
                    if stale_id is not None:
                        self._emit_outbound_channel_alias(stale_id, channel_id)
                else:
                    self._discard_originated_channel(channel_id)
                asyncio.create_task(self._on_outbound_stasis_start(channel_id))
            else:
                # Any other StasisStart (including inbound or unknown args)
                # is treated as an inbound call.
                asyncio.create_task(self._on_stasis_start(channel_id, event))

        elif event_type == "ChannelStateChange":
            # Fired when a channel transitions state, e.g. Ring → Up (callee answered).
            channel_state = str(channel.get("state") or "").lower()
            if channel_state == "up":
                if channel_id in self._pending_outbound:
                    asyncio.create_task(self._on_outbound_answered(channel_id))
                else:
                    # StasisStart processing may be pending as a create_task that
                    # hasn't run yet (ARI WS delivers events faster than tasks are
                    # scheduled).  Record the Up event so _on_outbound_stasis_start
                    # can fire _on_outbound_answered immediately after parking.
                    logger.debug(
                        f"AsteriskAdapter: ChannelStateChange(Up) arrived before "
                        f"StasisStart processed for channel={channel_id[:12]} — saved for later"
                    )
                    self._preemptive_up_channels.add(channel_id)

        elif event_type in ("StasisEnd", "ChannelDestroyed", "ChannelHangupRequest"):
            # Capture the hangup cause (Q.850) BEFORE we tear anything down so
            # the outcome resolver can classify no-answer / busy / rejected
            # instead of defaulting to an agent-side hangup. ChannelDestroyed
            # carries `cause` (int) + `cause_txt` (e.g. "No Answer", "User
            # busy"); the channel's `hangupsource`/`cause` may also be present.
            _cause_txt = event.get("cause_txt") or channel.get("cause_txt")
            if not _cause_txt:
                _cause_int = event.get("cause")
                if _cause_int is None:
                    _cause_int = channel.get("cause")
                if _cause_int is not None:
                    _cause_txt = _Q850_CAUSE_TEXT.get(int(_cause_int)) if str(_cause_int).lstrip("-").isdigit() else None
            if _cause_txt and channel_id not in self._hangup_causes:
                # Keep the first (most authoritative) terminal cause for a
                # channel — StasisEnd + ChannelDestroyed can both fire.
                self._hangup_causes[channel_id] = str(_cause_txt)
            # Drop any preemptive Up record for channels that are now gone.
            self._preemptive_up_channels.discard(channel_id)
            self._discard_originated_channel(channel_id)
            # Clean up pending outbound channels that were never answered.
            if channel_id in self._pending_outbound:
                asyncio.create_task(self._cleanup_pending_outbound(channel_id))
            elif channel_id in self._active_sessions:
                asyncio.create_task(self._on_stasis_end(channel_id, event_type))
            elif channel_id in self._ext_channels.values():
                # External channel ended — find and clean up parent
                parent = next(
                    (k for k, v in self._ext_channels.items() if v == channel_id),
                    None,
                )
                if parent:
                    asyncio.create_task(self._on_stasis_end(parent, event_type))

    async def _on_outbound_stasis_start(self, channel_id: str) -> None:
        """
        Handle an outbound call entering Stasis (callee is still ringing).

        Creates the mixing bridge and adds the outbound channel to it, then
        stores the pending state.  The ExternalMedia channel and C++ gateway
        session are NOT started here — they are deferred to _on_outbound_answered
        so that no RTP timeout fires while we are waiting for the callee to pick up.
        """
        logger.info(f"AsteriskAdapter: outbound call ringing channel={channel_id[:12]}")
        listen_port = await self._alloc_rtp_port()
        session_id = f"asterisk-{channel_id[:12]}-{listen_port}"
        bridge_id = ""

        try:
            # 1. Create mixing bridge
            bridge = await self._ari("POST", "/bridges", params={"type": "mixing"})
            bridge_id = bridge.get("id", "")
            if not bridge_id:
                raise RuntimeError("ARI bridge create returned no id")

            # 2. Add outbound channel to bridge (starts ringing the remote party)
            await self._ari(
                "POST", f"/bridges/{bridge_id}/addChannel",
                params={"channel": channel_id},
                ok=(200, 204, 209),
            )

            # Park the metadata — _on_outbound_answered will complete the setup
            self._pending_outbound[channel_id] = {
                "bridge_id": bridge_id,
                "listen_port": listen_port,
                "session_id": session_id,
            }

            # Fire the ringing-phase callback FIRST — before checking for
            # preemptive Up — so the bridge can start pre-warming STT/TTS/LLM
            # connections regardless of whether the callee already answered.
            # This fixes a critical race: when ChannelStateChange(Up) arrives
            # before StasisStart is processed, the old code returned early and
            # _on_ringing was never called, forcing a 2+ second answer-path
            # warmup and causing the user's first "hello" to be lost.
            if self._on_ringing is not None:
                asyncio.create_task(self._on_ringing(channel_id))

            # Race condition: the callee may have already answered while
            # StasisStart was sitting in the ARI WebSocket queue.  If so,
            # ChannelStateChange(Up) was stored in _preemptive_up_channels;
            # we must fire _on_outbound_answered right now instead of waiting.
            if channel_id in self._preemptive_up_channels:
                self._preemptive_up_channels.discard(channel_id)
                logger.info(
                    f"AsteriskAdapter: outbound call already answered (preemptive Up) "
                    f"channel={channel_id[:12]} — completing media setup immediately"
                )
                asyncio.create_task(self._on_outbound_answered(channel_id))
                return

            logger.info(
                f"AsteriskAdapter: outbound channel parked, waiting for answer "
                f"channel={channel_id[:12]} bridge={bridge_id[:12]} rtp_port={listen_port}"
            )

        except Exception as exc:
            logger.error(f"AsteriskAdapter: outbound stasis start failed: {exc}")
            if bridge_id:
                try:
                    await self._ari("DELETE", f"/bridges/{bridge_id}", ok=(200, 204, 404, 422))
                except Exception:
                    pass
            await self._release_rtp_port(listen_port)

    async def _on_outbound_answered(self, channel_id: str) -> None:
        """
        Complete ExternalMedia + C++ gateway setup once the callee answers.

        Called when ChannelStateChange fires with state=Up for a pending
        outbound channel.  At this point RTP will flow immediately, so the
        gateway startup timeout won't expire before audio arrives.
        """
        pending = self._pending_outbound.pop(channel_id, None)
        if not pending:
            return

        bridge_id = pending["bridge_id"]
        listen_port = pending["listen_port"]
        session_id = pending["session_id"]
        ext_channel_id = ""

        logger.info(
            "t_answer channel=%s rtp_port=%s",
            channel_id[:12], listen_port,
            extra={"call_id": channel_id, "t_answer_ms": 0},
        )
        logger.info(
            f"AsteriskAdapter: outbound call answered — completing media setup "
            f"channel={channel_id[:12]} rtp_port={listen_port}"
        )

        try:
            loop = asyncio.get_running_loop()
            _t_setup_start = loop.time()

            # 3. Create ExternalMedia channel pointing at C++ Gateway RTP listener.
            # This one must run first — steps 4/5/6 all need ext_channel_id.
            ext_data = await self._ari(
                "POST", "/channels/externalMedia",
                params={
                    "app": self._app_name,
                    "external_host": f"{self._gateway_rtp_ip}:{listen_port}",
                    "format": "ulaw",
                    "encapsulation": "rtp",
                    "transport": "udp",
                    "connection_type": "client",
                    "direction": "both",
                },
            )
            ext_channel_id = ext_data.get("id", "")
            if not ext_channel_id:
                raise RuntimeError("ARI externalMedia returned no channel id")

            # 4/5/6. addChannel + two UNICASTRTP_LOCAL_* GETs are independent of
            # each other (they only share the ext_channel_id dependency), so run
            # them concurrently.  Saves ~200 ms on a typical outbound answer
            # (ASTERISK-26771: each ARI request has ~50-200 ms baseline latency).
            add_coro = self._ari(
                "POST", f"/bridges/{bridge_id}/addChannel",
                params={"channel": ext_channel_id},
                ok=(200, 204, 209),
            )
            _, (remote_ip, remote_port) = await asyncio.gather(
                add_coro,
                self._resolve_unicastrtp_local(channel_id=ext_channel_id, channel=ext_data),
            )

            # 6. Start C++ Gateway session — call is already answered so RTP is
            #    flowing immediately; no startup-timeout risk.
            await self._gateway(
                "POST", "/v1/sessions/start",
                payload={
                    "session_id": session_id,
                    "listen_ip": self._gateway_rtp_ip,
                    "listen_port": listen_port,
                    "remote_ip": remote_ip,
                    "remote_port": remote_port,
                    "codec": "pcmu",
                    "ptime_ms": 20,
                    "echo_enabled": False,
                    "startup_no_rtp_timeout_ms": 10000,   # 10s — call is live
                    "active_no_rtp_timeout_ms": 15000,    # 15s silence timeout
                    "session_final_timeout_ms": _SESSION_FINAL_TIMEOUT_MS,  # max call lifetime (2h default)
                    "jitter_buffer_prefetch_frames": 1,   # was default 3 (60ms) — loopback has no jitter; 1 frame = 20ms
                    # 2 frames = 40ms = Deepgram Flux's optimal chunk size. Was 4 (80ms) when
                    # we re-batched downstream; now we hand off frames at Flux's native rate so
                    # there's no re-chunking jitter and per-call resamples drop by 50%.
                    "audio_callback_batch_frames": 2,
                    "audio_callback_url": (
                        f"{os.getenv('BACKEND_INTERNAL_URL', 'http://127.0.0.1:8000')}"
                        f"/api/v1/sip/telephony/audio/{session_id}"
                    ),
                },
                ok=(200, 409),
            )

            # Track the session
            self._active_sessions[channel_id] = {
                "session_id": session_id,
                "listen_port": listen_port,
                "bridge_id": bridge_id,
            }
            self._ext_channels[channel_id] = ext_channel_id
            self._bridges[channel_id] = bridge_id
            self._gateway_sessions[channel_id] = session_id

            _setup_ms = (loop.time() - _t_setup_start) * 1000.0
            logger.info(
                "ari_setup_done channel=%s session=%s rtp_port=%s remote=%s:%s setup_ms=%.0f",
                channel_id[:12],
                session_id,
                listen_port,
                remote_ip,
                remote_port,
                _setup_ms,
                extra={
                    "call_id": channel_id,
                    "ari_setup_ms": round(_setup_ms),
                    "session_id": session_id,
                },
            )

            # 7. Notify callbacks so the AI pipeline can start
            cb = self._call_arrived_callbacks.get(channel_id)
            if cb:
                asyncio.create_task(cb(channel_id))
            elif self._on_new_call:
                asyncio.create_task(self._on_new_call(channel_id))

        except Exception as exc:
            logger.error(f"AsteriskAdapter: outbound answered setup failed: {exc}")
            if session_id:
                try:
                    await self._gateway(
                        "POST", "/v1/sessions/stop",
                        payload={"session_id": session_id, "reason": "setup_failed"},
                    )
                except Exception:
                    pass
            if ext_channel_id:
                self._drop_channel_varset_cache(ext_channel_id)
                try:
                    await self._ari("DELETE", f"/channels/{ext_channel_id}", ok=(200, 204, 404))
                except Exception:
                    pass
            if bridge_id:
                try:
                    await self._ari("DELETE", f"/bridges/{bridge_id}", ok=(200, 204, 404, 422))
                except Exception:
                    pass
            try:
                await self._ari("DELETE", f"/channels/{channel_id}", ok=(200, 204, 404))
            except Exception:
                pass
            await self._release_rtp_port(listen_port)

    def _extract_inbound_meta(self, event: Dict[str, Any]) -> Dict[str, Any]:
        """Pull the called DID + dialplan context (+ caller number) out of an
        inbound StasisStart event, DEFENSIVELY — the exact field that carries
        the DID varies by trunk config, so we try several.

        DID candidates, in order: ``channel.dialplan.exten`` (the dialed
        extension/DID in most PJSIP inbound setups), then the ``connected``
        line number, then the StasisStart ``args`` (a dialplan can pass the
        DID as an app arg). Context: ``channel.dialplan.context``.

        Emits a ONE-TIME DEBUG dump of the raw channel dialplan/caller/
        connected fields so an operator can eyeball which field actually
        carries the DID on the live carrier (Blaze) inbound leg.
        """
        channel = event.get("channel") or {}
        dialplan = channel.get("dialplan") or {}
        caller = channel.get("caller") or {}
        connected = channel.get("connected") or {}
        args = event.get("args") or []

        if not self._inbound_debug_dumped:
            self._inbound_debug_dumped = True
            logger.info(
                "INBOUND_STASIS_DEBUG (one-time) dialplan=%s caller=%s "
                "connected=%s args=%s channel_name=%s — confirm which field "
                "carries the DID on the live carrier leg",
                dialplan, caller, connected, args, channel.get("name"),
            )

        called_did = (
            (dialplan.get("exten") if isinstance(dialplan, dict) else None)
            or (connected.get("number") if isinstance(connected, dict) else None)
            or (args[0] if args else None)
        )
        context = dialplan.get("context") if isinstance(dialplan, dict) else None
        caller_number = caller.get("number") if isinstance(caller, dict) else None

        return {
            "called_did": called_did,
            "context": context,
            "caller_number": caller_number,
        }

    def get_inbound_call_meta(self, channel_id: str) -> Optional[Dict[str, Any]]:
        """Return the {called_did, context, caller_number} captured for an
        inbound channel (or None). Consumed by the bridge's _on_new_call to
        resolve the tenant/campaign. Non-destructive read."""
        return self._inbound_call_meta.get(channel_id)

    async def _on_stasis_start(self, channel_id: str, event: Dict[str, Any]) -> None:
        """Set up ExternalMedia → C++ Gateway → AI pipeline for a new inbound call."""
        logger.info(f"AsteriskAdapter: new call channel={channel_id[:12]}")
        # Phase C — capture the DID/context BEFORE any await so the bridge can
        # read it in _on_new_call to resolve the tenant/campaign.
        try:
            self._inbound_call_meta[channel_id] = self._extract_inbound_meta(event)
        except Exception as _meta_exc:  # noqa: BLE001 — never block call setup
            logger.debug("inbound_meta_extract_failed channel=%s err=%s", channel_id[:12], _meta_exc)
        listen_port = await self._alloc_rtp_port()
        session_id = f"asterisk-{channel_id[:12]}-{listen_port}"
        bridge_id = ""
        ext_channel_id = ""

        try:
            # 1. Create mixing bridge
            bridge = await self._ari("POST", "/bridges", params={"type": "mixing"})
            bridge_id = bridge.get("id", "")
            if not bridge_id:
                raise RuntimeError("ARI bridge create returned no id")

            # 2. Add caller channel to bridge
            await self._ari(
                "POST", f"/bridges/{bridge_id}/addChannel",
                params={"channel": channel_id},
                ok=(200, 204, 209),
            )

            # 3. Create ExternalMedia channel pointing at C++ Gateway RTP listener
            ext_data = await self._ari(
                "POST", "/channels/externalMedia",
                params={
                    "app": self._app_name,
                    "external_host": f"{self._gateway_rtp_ip}:{listen_port}",
                    "format": "ulaw",
                    "encapsulation": "rtp",
                    "transport": "udp",
                    "connection_type": "client",
                    "direction": "both",
                },
            )
            ext_channel_id = ext_data.get("id", "")
            if not ext_channel_id:
                raise RuntimeError("ARI externalMedia returned no channel id")

            # 4/5. addChannel + two UNICASTRTP_LOCAL_* GETs are independent of
            # each other (share only ext_channel_id), so run them concurrently.
            # Mirrors the same optimisation in _on_outbound_answered.
            add_coro = self._ari(
                "POST", f"/bridges/{bridge_id}/addChannel",
                params={"channel": ext_channel_id},
                ok=(200, 204, 209),
            )
            _, (remote_ip, remote_port) = await asyncio.gather(
                add_coro,
                self._resolve_unicastrtp_local(channel_id=ext_channel_id, channel=ext_data),
            )

            # 6. Start C++ Gateway session (AI mode: echo_enabled=False once TTS hooked in)
            await self._gateway(
                "POST", "/v1/sessions/start",
                payload={
                    "session_id": session_id,
                    "listen_ip": self._gateway_rtp_ip,
                    "listen_port": listen_port,
                    "remote_ip": remote_ip,
                    "remote_port": remote_port,
                    "codec": "pcmu",
                    "ptime_ms": 20,
                    "echo_enabled": False,
                    # Increase timeouts for AI pipeline initialization
                    "startup_no_rtp_timeout_ms": 30000,  # 30 seconds (was 5s default)
                    "active_no_rtp_timeout_ms": 15000,   # 15 seconds (was 8s default)
                    "session_final_timeout_ms": _SESSION_FINAL_TIMEOUT_MS,  # max call lifetime (2h default)
                    "jitter_buffer_prefetch_frames": 1,   # was default 3 (60ms) — loopback has no jitter; 1 frame = 20ms
                    # 2 frames = 40ms = Deepgram Flux's optimal chunk size. Was 4 (80ms) when
                    # we re-batched downstream; now we hand off frames at Flux's native rate so
                    # there's no re-chunking jitter and per-call resamples drop by 50%.
                    "audio_callback_batch_frames": 2,
                    # Tell the gateway to POST audio chunks to our backend callback
                    "audio_callback_url": (
                        f"{os.getenv('BACKEND_INTERNAL_URL', 'http://127.0.0.1:8000')}"
                        f"/api/v1/sip/telephony/audio/{session_id}"
                    ),
                },
                ok=(200, 409),
            )

            # Track the session
            self._active_sessions[channel_id] = {
                "session_id": session_id,
                "listen_port": listen_port,
                "bridge_id": bridge_id,
            }
            self._ext_channels[channel_id] = ext_channel_id
            self._bridges[channel_id] = bridge_id
            self._gateway_sessions[channel_id] = session_id

            logger.info(
                f"AsteriskAdapter: session started channel={channel_id[:12]} "
                f"session={session_id} rtp_port={listen_port} remote={remote_ip}:{remote_port}"
            )

            # 7. Notify any registered callback for this call_id
            cb = self._call_arrived_callbacks.get(channel_id)
            if cb:
                asyncio.create_task(cb(channel_id))
            elif self._on_new_call:
                asyncio.create_task(self._on_new_call(channel_id))

        except Exception as exc:
            logger.error(f"AsteriskAdapter: session start failed: {exc}")
            # Best-effort cleanup
            if session_id:
                try:
                    await self._gateway(
                        "POST", "/v1/sessions/stop",
                        payload={"session_id": session_id, "reason": "start_failed"},
                    )
                except Exception:
                    pass
            if ext_channel_id:
                self._drop_channel_varset_cache(ext_channel_id)
                try:
                    await self._ari("DELETE", f"/channels/{ext_channel_id}", ok=(200, 204, 404))
                except Exception:
                    pass
            if bridge_id:
                try:
                    await self._ari("DELETE", f"/bridges/{bridge_id}", ok=(200, 204, 404, 422))
                except Exception:
                    pass
            try:
                await self._ari("DELETE", f"/channels/{channel_id}", ok=(200, 204, 404))
            except Exception:
                pass
            await self._release_rtp_port(listen_port)

    async def _cleanup_pending_outbound(self, channel_id: str) -> None:
        """Release resources for an outbound call that was never answered."""
        pending = self._pending_outbound.pop(channel_id, None)
        if not pending:
            return
        await self._release_rtp_port(pending["listen_port"])
        bridge_id = pending.get("bridge_id", "")
        if bridge_id:
            try:
                await self._ari("DELETE", f"/bridges/{bridge_id}", ok=(200, 204, 404, 422))
            except Exception:
                pass
        logger.info(f"AsteriskAdapter: unanswered outbound call cleaned up channel={channel_id[:12]}")

        # Signal the bridge so it can release any ringing-phase VoiceSession
        # that was pre-created for this channel.  Without this hook, an
        # abandoned ring would leak the STT/TTS WebSocket connections that
        # _on_ringing opened during the ring window.
        if self._on_any_call_end is not None:
            try:
                asyncio.create_task(self._on_any_call_end(channel_id))
            except Exception as exc:
                logger.debug(f"on_any_call_end dispatch failed for {channel_id[:12]}: {exc}")

    async def _on_stasis_end(self, channel_id: str, reason: str) -> None:
        """Tear down C++ Gateway session and ARI bridge when a call ends."""
        session_info = self._active_sessions.pop(channel_id, None)
        ext_channel_id = self._ext_channels.pop(channel_id, None)
        bridge_id = self._bridges.pop(channel_id, None)
        session_id = self._gateway_sessions.pop(channel_id, None)
        self._tts_error_counts.pop(channel_id, None)
        self._inbound_call_meta.pop(channel_id, None)

        if session_id:
            try:
                await self._gateway(
                    "POST", "/v1/sessions/stop",
                    payload={"session_id": session_id, "reason": reason},
                )
            except Exception as exc:
                logger.debug(f"AsteriskAdapter: gateway stop error: {exc}")

        if ext_channel_id:
            self._drop_channel_varset_cache(ext_channel_id)
            try:
                await self._ari("DELETE", f"/channels/{ext_channel_id}", ok=(200, 204, 404))
            except Exception:
                pass

        if bridge_id:
            try:
                await self._ari("DELETE", f"/bridges/{bridge_id}", ok=(200, 204, 404, 422))
            except Exception:
                pass

        if session_info:
            await self._release_rtp_port(session_info["listen_port"])

        logger.info(f"AsteriskAdapter: session ended channel={channel_id[:12]} reason={reason}")

        cb = self._call_ended_callbacks.get(channel_id)
        if cb:
            asyncio.create_task(cb(channel_id))
        elif self._on_any_call_end:
            asyncio.create_task(self._on_any_call_end(channel_id))

    # ------------------------------------------------------------------
    # CallControlAdapter — call event callbacks
    # ------------------------------------------------------------------

    async def on_call_arrived(self, call_id: str, callback: Callable[..., Coroutine]) -> None:
        self._call_arrived_callbacks[call_id] = callback

    async def on_call_ended(self, call_id: str, callback: Callable[..., Coroutine]) -> None:
        self._call_ended_callbacks[call_id] = callback

    def set_new_call_callback(self, callback: Callable) -> None:
        """Global callback invoked for every new inbound call (call_id is passed as arg)."""
        self._on_new_call = callback

    def set_call_end_callback(self, callback: Callable) -> None:
        """Global callback invoked when any call ends."""
        self._on_any_call_end = callback

    def set_ringing_callback(self, callback: Callable) -> None:
        """
        Optional callback invoked once an outbound channel has been parked
        in its mixing bridge and is waiting for the callee to answer.
        Used by the telephony bridge for ringing-phase provider warmup.
        Signature: `async def callback(channel_id: str) -> None`.
        """
        self._on_ringing = callback

    def set_outbound_channel_alias_callback(self, callback: Callable) -> None:
        """
        Optional callback invoked when ARI reports a different outbound channel
        ID than the one passed to originate_call(channel_id=...).
        Signature: `def callback(original_call_id: str, actual_call_id: str)`.
        """
        self._on_outbound_channel_alias = callback

    def register_call_event_handlers(
        self,
        on_new_call: Callable,
        on_call_ended: Callable,
        on_audio_received: Optional[Callable] = None,
    ) -> None:
        """Wire bridge-level callbacks into ARI event handlers."""
        self._on_new_call = on_new_call
        self._on_any_call_end = on_call_ended

    # ------------------------------------------------------------------
    # CallControlAdapter — audio I/O
    # ------------------------------------------------------------------

    async def start_audio_stream(self, call_id: str) -> None:
        """
        Audio streaming from caller starts automatically via the C++ Gateway
        audio_callback_url set during session creation.
        This method is a no-op for Asterisk (streaming begins at session start).
        """
        logger.debug(
            f"AsteriskAdapter.start_audio_stream: streaming already active for {call_id[:12]}"
        )

    async def send_tts_audio(self, call_id: str, pcmu_audio: bytes) -> None:
        """
        Send TTS audio to the caller via the C++ Gateway.

        Endpoint: POST /v1/sessions/tts/play
        Body: {"session_id": "...", "pcmu_base64": "...", "clear_existing": false}
        """
        session_id = self._gateway_sessions.get(call_id)
        if not session_id:
            logger.warning(
                f"[AsteriskAdapter] send_tts_audio: no gateway session for call_id={call_id[:12]}"
            )
            return

        import base64
        try:
            pcmu_b64 = base64.b64encode(pcmu_audio).decode()

            await self._gateway(
                "POST",
                "/v1/sessions/tts/play",
                payload={
                    "session_id": session_id,
                    "pcmu_base64": pcmu_b64,
                    "clear_existing": False,
                },
            )
            # Reset error counter on first successful delivery.
            self._tts_error_counts.pop(call_id, None)
        except Exception as exc:
            count = self._tts_error_counts.get(call_id, 0) + 1
            self._tts_error_counts[call_id] = count
            if count == 1:
                logger.error(f"[AsteriskAdapter] ❌ send_tts_audio failed: {exc}")
            elif count % 50 == 0:
                logger.warning(
                    f"[AsteriskAdapter] send_tts_audio still failing for {call_id[:12]} "
                    f"({count} errors total) — last error: {exc}"
                )

    async def interrupt_tts(self, call_id: str) -> None:
        """
        Stop playing TTS audio via the C++ Gateway interrupt endpoint.

        Endpoint: POST /v1/sessions/tts/interrupt
        Body: {"session_id": "...", "reason": "barge_in"}
        """
        session_id = self._gateway_sessions.get(call_id)
        if not session_id:
            return
        try:
            await self._gateway(
                "POST",
                "/v1/sessions/tts/interrupt",
                payload={"session_id": session_id, "reason": "barge_in"},
                ok=(200, 204, 404),
            )
        except Exception as exc:
            logger.debug(f"AsteriskAdapter.interrupt_tts: {exc}")

    # ------------------------------------------------------------------
    # CallControlAdapter — call control
    # ------------------------------------------------------------------

    async def originate_call(
        self,
        destination: str,
        caller_id: str,
        channel_id: Optional[str] = None,
        trunk_endpoint: Optional[str] = None,
    ) -> str:
        """
        Originate an outbound call via ARI that rings the destination phone.

        For outbound calls, ARI creates a channel to the target endpoint with
        app=talky_ai.  When the called party answers the channel enters Stasis,
        _on_stasis_start fires, and the ExternalMedia bridge + AI pipeline are
        attached — exactly the same flow as inbound calls.

        Two strategies depending on the destination:
          1. Internal/test extensions (e.g. 750) → Local channel through dialplan
          2. Real PBX extensions → Direct PJSIP dial (so audio goes through our
             mixing bridge, not a separate Dial()-created media path)
        """
        if destination == "750":
            # Test extension: route through dialplan
            endpoint = f"Local/{destination}@from-opensips"
            # Pre-generate channel ID and register it BEFORE the ARI POST.
            # This prevents a race condition where the StasisStart WS event
            # arrives before the HTTP response — at that point the channel
            # would NOT be in _originated_channels and would be mis-routed
            # to the inbound handler.
            pre_id = channel_id or f"talky-out-{uuid.uuid4()}"
            self._track_originated_channel(pre_id)
            try:
                data = await self._ari(
                    "POST",
                    "/channels",
                    params={
                        "endpoint": endpoint,
                        "callerId": caller_id,
                        "app": self._app_name,
                        "appArgs": "outbound",
                        "channelId": pre_id,
                    },
                )
            except Exception:
                self._discard_originated_channel(pre_id)
                raise
            channel_id = data.get("id", pre_id)
            # ARI should use our pre_id, but if it returns something else,
            # update the tracking set.
            if channel_id != pre_id:
                self._discard_originated_channel(pre_id)
                self._track_originated_channel(channel_id)
            logger.info(f"AsteriskAdapter: originated test call to {destination} channel={channel_id[:12]}")
            return channel_id

        # -------------------------------------------------------------------
        # Real extensions: originate through a PJSIP trunk.
        #
        # Endpoint is configurable via TELEPHONY_PJSIP_OUTBOUND_ENDPOINT so
        # production can route through the upstream carrier (default:
        # blazedigitel-endpoint, registered in /etc/asterisk/pjsip.conf)
        # while local dev can still target lan-pbx by setting the env var.
        #
        # Per-tenant isolation: the caller may pass an explicit
        # ``trunk_endpoint`` (e.g. ``trunk-<trunkid>`` for a BYO/own trunk).
        # When None — the historical behaviour — we fall back to the global
        # env endpoint, so default-trunk tenants are byte-for-byte unchanged.
        # -------------------------------------------------------------------
        import os as _os
        trunk = trunk_endpoint or _os.getenv(
            "TELEPHONY_PJSIP_OUTBOUND_ENDPOINT", "blazedigitel-endpoint"
        )
        endpoint = f"PJSIP/{destination}@{trunk}"

        # Ring timeout (seconds): how long Asterisk lets the destination ring
        # before giving up. Without it ARI defaults to 30s, but making it
        # explicit + env-tunable lets us enforce a "natural" ring window — a
        # call that isn't answered within it is torn down by Asterisk with a
        # no-answer cause, which the outcome resolver maps to NO_ANSWER and the
        # disposition policy reschedules for +24h (never the same day).
        ring_timeout = int(_os.getenv("DIALER_RING_TIMEOUT_S", "30"))

        # Pre-generate channel ID and register BEFORE ARI POST to prevent
        # the StasisStart WS event from arriving before the HTTP response.
        pre_id = channel_id or f"talky-out-{uuid.uuid4()}"
        self._track_originated_channel(pre_id)

        try:
            data = await self._ari(
                "POST",
                "/channels",
                params={
                    "endpoint": endpoint,
                    "callerId": caller_id,
                    "app": self._app_name,
                    "appArgs": "outbound",
                    "channelId": pre_id,
                    "timeout": ring_timeout,
                },
            )
        except Exception:
            self._discard_originated_channel(pre_id)
            raise

        channel_id = data.get("id", pre_id)
        # ARI should use our pre_id, but if it returns something else,
        # update the tracking set.
        if channel_id != pre_id:
            self._discard_originated_channel(pre_id)
            self._track_originated_channel(channel_id)

        logger.info(f"AsteriskAdapter: originated call to {destination} via {endpoint} channel={channel_id[:12]}")

        # Safety: remove the pre-generated ID from _originated_channels after
        # 30 seconds.  For PJSIP trunk calls, the actual StasisStart channel
        # has a different ID; the trunk-leg matcher in _handle_ari_event will
        # consume it.  This timer prevents stale entries from leaking if the
        # origination fails silently (no StasisStart at all).
        async def _expire_originated(cid: str) -> None:
            await asyncio.sleep(30)
            if cid in self._originated_channels:
                self._discard_originated_channel(cid)
                logger.debug(f"AsteriskAdapter: expired stale originated channel {cid[:12]}")

        asyncio.create_task(_expire_originated(channel_id))

        return channel_id

    async def hangup(self, call_id: str) -> None:
        """Delete (hang up) a channel via ARI."""
        try:
            await self._ari("DELETE", f"/channels/{call_id}", ok=(200, 204, 404))
        except Exception as exc:
            logger.warning(f"AsteriskAdapter.hangup: {exc}")

    async def transfer(
        self,
        call_id: str,
        destination: str,
        mode: str = "blind",
    ) -> Dict[str, Any]:
        """Transfer a call via ARI redirect."""
        try:
            await self._ari(
                "POST",
                f"/channels/{call_id}/redirect",
                params={"endpoint": f"PJSIP/{destination}"},
                ok=(200, 204),
            )
            return {"status": "success", "call_id": call_id, "destination": destination, "mode": mode}
        except Exception as exc:
            logger.error(f"AsteriskAdapter.transfer: {exc}")
            return {"status": "failed", "call_id": call_id, "error": str(exc)}
