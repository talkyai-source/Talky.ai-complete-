#!/usr/bin/env python3
"""Day 5 ARI External Media controller for Asterisk <-> C++ echo integration.

Implements the official ARI flow:
- consume Stasis events from /ari/events
- create bridge
- create externalMedia channel (ulaw/udp/rtp/client)
- add channels to bridge
- fetch UNICASTRTP_LOCAL_* vars
- start/stop C++ gateway RTP sessions deterministically
"""

from __future__ import annotations

import argparse
import asyncio
import json
import signal
import time
from dataclasses import dataclass
from typing import Dict, Optional
from urllib.parse import quote

import requests

try:
    import websockets  # type: ignore
except ImportError:  # pragma: no cover - handled at runtime
    websockets = None


class AriClientError(RuntimeError):
    """Raised when ARI returns an error response."""


class GatewayClientError(RuntimeError):
    """Raised when voice-gateway-cpp control API returns an error."""


@dataclass
class SessionBinding:
    user_channel_id: str
    external_channel_id: str
    bridge_id: str
    session_id: str
    listen_port: int
    started_at: float


@dataclass(frozen=True)
class TransferConfig:
    enabled: bool
    target_endpoint: str
    delay_seconds: float
    use_continue_fallback: bool
    continue_context: str
    continue_extension: str
    continue_priority: int


class TenantRuntimeGuard:
    """Simple in-process tenant guard for active sessions and transfer inflight limits."""

    def __init__(self, *, tenant_id: str, max_active_calls: int, max_transfer_inflight: int) -> None:
        self._tenant_id = str(tenant_id or "default-tenant").strip() or "default-tenant"
        self._max_active_calls = max(1, int(max_active_calls))
        self._max_transfer_inflight = max(1, int(max_transfer_inflight))
        self._active_calls = 0
        self._transfer_inflight = 0

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    @property
    def active_calls(self) -> int:
        return self._active_calls

    @property
    def transfer_inflight(self) -> int:
        return self._transfer_inflight

    @property
    def max_active_calls(self) -> int:
        return self._max_active_calls

    @property
    def max_transfer_inflight(self) -> int:
        return self._max_transfer_inflight

    def try_acquire_call(self) -> bool:
        if self._active_calls >= self._max_active_calls:
            return False
        self._active_calls += 1
        return True

    def release_call(self) -> None:
        if self._active_calls > 0:
            self._active_calls -= 1

    def try_acquire_transfer(self) -> bool:
        if self._transfer_inflight >= self._max_transfer_inflight:
            return False
        self._transfer_inflight += 1
        return True

    def release_transfer(self) -> None:
        if self._transfer_inflight > 0:
            self._transfer_inflight -= 1


class PortAllocator:
    def __init__(self, start_port: int, end_port: int) -> None:
        if start_port <= 0 or end_port <= 0 or start_port > end_port:
            raise ValueError("invalid port range")
        self._start = start_port
        self._end = end_port
        self._next = start_port
        self._in_use: set[int] = set()

    def allocate(self) -> int:
        span = self._end - self._start + 1
        for _ in range(span):
            candidate = self._next
            self._next += 1
            if self._next > self._end:
                self._next = self._start
            if candidate not in self._in_use:
                self._in_use.add(candidate)
                return candidate
        raise RuntimeError("no free RTP listen port available")

    def release(self, port: int) -> None:
        self._in_use.discard(port)


class AriHttpClient:
    def __init__(self, host: str, port: int, username: str, password: str, timeout_s: float = 5.0) -> None:
        self._base = f"http://{host}:{port}/ari"
        self._api_key = f"{username}:{password}"
        self._timeout_s = timeout_s
        self._session = requests.Session()

    def close(self) -> None:
        self._session.close()

    def _request(self, method: str, path: str, *, params: Optional[dict] = None, json_body: Optional[dict] = None, ok: tuple[int, ...] = (200, 201, 204)) -> dict:
        query = dict(params or {})
        query["api_key"] = self._api_key
        url = f"{self._base}{path}"
        response = self._session.request(method, url, params=query, json=json_body, timeout=self._timeout_s)
        if response.status_code not in ok:
            raise AriClientError(f"{method} {path} failed: {response.status_code} {response.text[:400]}")

        if not response.text:
            return {}

        try:
            return response.json()
        except ValueError:
            return {"text": response.text}

    def ping(self) -> dict:
        return self._request("GET", "/asterisk/info")

    def create_bridge(self) -> str:
        data = self._request("POST", "/bridges", params={"type": "mixing"})
        bridge_id = data.get("id")
        if not bridge_id:
            raise AriClientError("bridge create returned no id")
        return str(bridge_id)

    def add_channel_to_bridge(self, bridge_id: str, channel_id: str) -> None:
        self._request("POST", f"/bridges/{bridge_id}/addChannel", params={"channel": channel_id})

    def create_external_media(self, app: str, external_host: str, *, fmt: str = "ulaw") -> str:
        data = self._request(
            "POST",
            "/channels/externalMedia",
            params={
                "app": app,
                "external_host": external_host,
                "format": fmt,
                "encapsulation": "rtp",
                "transport": "udp",
                "connection_type": "client",
                "direction": "both",
            },
        )
        channel_id = data.get("id")
        if not channel_id:
            raise AriClientError("externalMedia create returned no channel id")
        return str(channel_id)

    def get_channel_var(self, channel_id: str, variable: str) -> str:
        data = self._request("GET", f"/channels/{channel_id}/variable", params={"variable": variable})
        value = data.get("value")
        if value is None or value == "":
            raise AriClientError(f"channel var {variable} missing on channel {channel_id}")
        return str(value)

    def transfer_progress(self, channel_id: str) -> None:
        self._request("POST", f"/channels/{channel_id}/transfer_progress", ok=(200, 204, 404, 409, 422))

    def redirect_channel(self, channel_id: str, endpoint: str) -> None:
        self._request(
            "POST",
            f"/channels/{channel_id}/redirect",
            params={"endpoint": endpoint},
            ok=(200, 204),
        )

    def continue_channel(self, channel_id: str, context: str, extension: str, priority: int = 1) -> None:
        self._request(
            "POST",
            f"/channels/{channel_id}/continue",
            params={"context": context, "extension": extension, "priority": int(priority)},
            ok=(200, 204),
        )

    def delete_channel(self, channel_id: str) -> None:
        try:
            self._request("DELETE", f"/channels/{channel_id}", ok=(200, 204, 404))
        except AriClientError:
            # Day 5 cleanup is best effort; callers still enforce post-checks.
            pass

    def delete_bridge(self, bridge_id: str) -> None:
        try:
            self._request("DELETE", f"/bridges/{bridge_id}", ok=(200, 204, 404, 422))
        except AriClientError:
            pass

    def list_channels(self) -> list[dict]:
        data = self._request("GET", "/channels")
        return data if isinstance(data, list) else []

    def list_bridges(self) -> list[dict]:
        data = self._request("GET", "/bridges")
        return data if isinstance(data, list) else []


class GatewayHttpClient:
    def __init__(self, base_url: str, timeout_s: float = 5.0) -> None:
        self._base = base_url.rstrip("/")
        self._timeout_s = timeout_s
        self._session = requests.Session()

    def close(self) -> None:
        self._session.close()

    def _request(self, method: str, path: str, *, payload: Optional[dict] = None, ok: tuple[int, ...] = (200,)) -> dict:
        url = f"{self._base}{path}"
        response = self._session.request(method, url, json=payload, timeout=self._timeout_s)
        if response.status_code not in ok:
            raise GatewayClientError(f"{method} {path} failed: {response.status_code} {response.text[:400]}")
        return response.json() if response.text else {}

    def start_session(
        self,
        *,
        session_id: str,
        listen_ip: str,
        listen_port: int,
        remote_ip: str,
        remote_port: int,
        echo_enabled: bool = True,
    ) -> dict:
        payload = {
            "session_id": session_id,
            "listen_ip": listen_ip,
            "listen_port": listen_port,
            "remote_ip": remote_ip,
            "remote_port": remote_port,
            "codec": "pcmu",
            "ptime_ms": 20,
            "echo_enabled": bool(echo_enabled),
        }
        return self._request("POST", "/v1/sessions/start", payload=payload, ok=(200, 409))

    def stop_session(self, session_id: str, reason: str) -> dict:
        return self._request(
            "POST",
            "/v1/sessions/stop",
            payload={"session_id": session_id, "reason": reason},
            ok=(200,),
        )

    def session_stats(self, session_id: str) -> dict:
        return self._request("GET", f"/v1/sessions/{session_id}/stats", ok=(200, 404))


class Day5Controller:
    def __init__(
        self,
        *,
        ari_client: AriHttpClient,
        gateway_client: GatewayHttpClient,
        ari_host: str,
        ari_port: int,
        ari_username: str,
        ari_password: str,
        app_name: str,
        gateway_rtp_ip: str,
        gateway_rtp_port_start: int,
        gateway_rtp_port_end: int,
        max_completed_calls: int,
        idle_timeout_seconds: int,
        gateway_poll_interval_seconds: float,
        gateway_echo_enabled: bool,
        transfer_config: TransferConfig,
        tenant_id: str,
        tenant_max_active_calls: int,
        tenant_max_transfer_inflight: int,
    ) -> None:
        self.ari = ari_client
        self.gateway = gateway_client
        self.ari_host = ari_host
        self.ari_port = ari_port
        self.ari_username = ari_username
        self.ari_password = ari_password
        self.app_name = app_name
        self.gateway_rtp_ip = gateway_rtp_ip
        self.max_completed_calls = max_completed_calls
        self.idle_timeout_seconds = idle_timeout_seconds
        self.gateway_poll_interval_seconds = max(0.2, gateway_poll_interval_seconds)
        self.gateway_echo_enabled = gateway_echo_enabled
        self.transfer_config = transfer_config

        self.allocator = PortAllocator(gateway_rtp_port_start, gateway_rtp_port_end)
        self.sessions: Dict[str, SessionBinding] = {}
        self.external_to_user: Dict[str, str] = {}
        self.transfer_tasks: Dict[str, asyncio.Task[None]] = {}
        self.tenant_guard = TenantRuntimeGuard(
            tenant_id=tenant_id,
            max_active_calls=tenant_max_active_calls,
            max_transfer_inflight=tenant_max_transfer_inflight,
        )

        self.started_calls = 0
        self.completed_calls = 0
        self.failed_calls = 0
        self.transfer_attempts = 0
        self.transfer_successes = 0
        self.transfer_failures = 0
        self.transfer_rejected = 0
        self._stop = False
        self._last_activity = time.monotonic()

    def _emit(self, event: str, **fields: object) -> None:
        payload = {
            "ts": int(time.time() * 1000),
            "event": event,
            "started_calls": self.started_calls,
            "completed_calls": self.completed_calls,
            "active_sessions": len(self.sessions),
        }
        payload.update(fields)
        print(json.dumps(payload, sort_keys=True), flush=True)

    def stop(self) -> None:
        self._stop = True

    async def _run_in_thread(self, fn, *args, **kwargs):
        return await asyncio.to_thread(fn, *args, **kwargs)

    async def _delayed_blind_transfer(self, user_channel_id: str) -> None:
        if self.transfer_config.delay_seconds > 0:
            await asyncio.sleep(self.transfer_config.delay_seconds)
        await self._execute_blind_transfer(user_channel_id)

    async def _execute_blind_transfer(self, user_channel_id: str) -> None:
        binding = self.sessions.get(user_channel_id)
        if binding is None:
            return

        self.transfer_attempts += 1

        if not self.tenant_guard.try_acquire_transfer():
            self.transfer_rejected += 1
            self._emit(
                "transfer_rejected_concurrency",
                user_channel_id=user_channel_id,
                session_id=binding.session_id,
                tenant_id=self.tenant_guard.tenant_id,
                transfer_inflight=self.tenant_guard.transfer_inflight,
                max_transfer_inflight=self.tenant_guard.max_transfer_inflight,
            )
            return

        try:
            try:
                await self._run_in_thread(self.ari.transfer_progress, user_channel_id)
            except Exception:
                # transfer_progress is optional best-effort signaling.
                pass

            await self._cleanup_user_session(user_channel_id, "blind_transfer_prepare")

            if self.transfer_config.use_continue_fallback:
                try:
                    await self._run_in_thread(
                        self.ari.redirect_channel,
                        user_channel_id,
                        self.transfer_config.target_endpoint,
                    )
                    self.transfer_successes += 1
                    self._emit(
                        "transfer_redirect_dispatched",
                        user_channel_id=user_channel_id,
                        tenant_id=self.tenant_guard.tenant_id,
                        endpoint=self.transfer_config.target_endpoint,
                    )
                    return
                except Exception as redirect_exc:  # noqa: BLE001
                    self._emit(
                        "transfer_redirect_fallback",
                        user_channel_id=user_channel_id,
                        tenant_id=self.tenant_guard.tenant_id,
                        error=str(redirect_exc),
                        fallback="continue",
                    )
                await self._run_in_thread(
                    self.ari.continue_channel,
                    user_channel_id,
                    self.transfer_config.continue_context,
                    self.transfer_config.continue_extension,
                    self.transfer_config.continue_priority,
                )
                self.transfer_successes += 1
                self._emit(
                    "transfer_continue_dispatched",
                    user_channel_id=user_channel_id,
                    tenant_id=self.tenant_guard.tenant_id,
                    context=self.transfer_config.continue_context,
                    extension=self.transfer_config.continue_extension,
                    priority=self.transfer_config.continue_priority,
                )
            else:
                await self._run_in_thread(
                    self.ari.redirect_channel,
                    user_channel_id,
                    self.transfer_config.target_endpoint,
                )
                self.transfer_successes += 1
                self._emit(
                    "transfer_redirect_dispatched",
                    user_channel_id=user_channel_id,
                    tenant_id=self.tenant_guard.tenant_id,
                    endpoint=self.transfer_config.target_endpoint,
                )
        except Exception as exc:  # noqa: BLE001
            self.transfer_failures += 1
            self._emit(
                "transfer_failed",
                user_channel_id=user_channel_id,
                tenant_id=self.tenant_guard.tenant_id,
                error=str(exc),
            )
            await self._run_in_thread(self.ari.delete_channel, user_channel_id)
        finally:
            self.tenant_guard.release_transfer()

    async def _handle_stasis_start(self, event: dict) -> None:
        channel = event.get("channel") or {}
        user_channel_id = str(channel.get("id") or "")
        channel_name = str(channel.get("name") or "")
        args = event.get("args") or []

        if not user_channel_id:
            return
        if channel_name.startswith("UnicastRTP/"):
            return
        if not args or args[0] != "inbound":
            return
        if user_channel_id in self.sessions:
            return

        if not self.tenant_guard.try_acquire_call():
            self.transfer_rejected += 1
            self._emit(
                "call_rejected_concurrency",
                user_channel_id=user_channel_id,
                tenant_id=self.tenant_guard.tenant_id,
                active_calls=self.tenant_guard.active_calls,
                max_active_calls=self.tenant_guard.max_active_calls,
            )
            await self._run_in_thread(self.ari.delete_channel, user_channel_id)
            return

        listen_port = self.allocator.allocate()
        bridge_id = ""
        external_channel_id = ""
        session_id = f"day5-{user_channel_id[:12]}-{listen_port}"

        try:
            bridge_id = await self._run_in_thread(self.ari.create_bridge)
            await self._run_in_thread(self.ari.add_channel_to_bridge, bridge_id, user_channel_id)

            external_channel_id = await self._run_in_thread(
                self.ari.create_external_media,
                self.app_name,
                f"{self.gateway_rtp_ip}:{listen_port}",
            )
            await self._run_in_thread(self.ari.add_channel_to_bridge, bridge_id, external_channel_id)

            remote_ip = await self._run_in_thread(self.ari.get_channel_var, external_channel_id, "UNICASTRTP_LOCAL_ADDRESS")
            remote_port = int(await self._run_in_thread(self.ari.get_channel_var, external_channel_id, "UNICASTRTP_LOCAL_PORT"))

            await self._run_in_thread(
                self.gateway.start_session,
                session_id=session_id,
                listen_ip=self.gateway_rtp_ip,
                listen_port=listen_port,
                remote_ip=remote_ip,
                remote_port=remote_port,
                echo_enabled=self.gateway_echo_enabled,
            )

            binding = SessionBinding(
                user_channel_id=user_channel_id,
                external_channel_id=external_channel_id,
                bridge_id=bridge_id,
                session_id=session_id,
                listen_port=listen_port,
                started_at=time.time(),
            )
            self.sessions[user_channel_id] = binding
            self.external_to_user[external_channel_id] = user_channel_id
            self.started_calls += 1
            self._last_activity = time.monotonic()
            self._emit(
                "session_started",
                user_channel_id=user_channel_id,
                external_channel_id=external_channel_id,
                bridge_id=bridge_id,
                session_id=session_id,
                listen_port=listen_port,
                remote_ip=remote_ip,
                remote_port=remote_port,
            )
            if self.transfer_config.enabled:
                self.transfer_tasks[user_channel_id] = asyncio.create_task(
                    self._delayed_blind_transfer(user_channel_id)
                )
        except Exception as exc:  # noqa: BLE001
            self.failed_calls += 1
            self._emit(
                "session_start_failed",
                user_channel_id=user_channel_id,
                listen_port=listen_port,
                bridge_id=bridge_id,
                external_channel_id=external_channel_id,
                error=str(exc),
            )
            if session_id:
                try:
                    await self._run_in_thread(self.gateway.stop_session, session_id, "start_failed")
                except Exception:  # noqa: BLE001
                    pass
            if external_channel_id:
                await self._run_in_thread(self.ari.delete_channel, external_channel_id)
            if bridge_id:
                await self._run_in_thread(self.ari.delete_bridge, bridge_id)
            await self._run_in_thread(self.ari.delete_channel, user_channel_id)
            self.allocator.release(listen_port)
            self.tenant_guard.release_call()

    async def _cleanup_user_session(self, user_channel_id: str, reason: str) -> None:
        binding = self.sessions.pop(user_channel_id, None)
        if binding is None:
            return

        transfer_task = self.transfer_tasks.pop(user_channel_id, None)
        current_task = asyncio.current_task()
        if transfer_task and transfer_task is not current_task:
            transfer_task.cancel()
            try:
                await transfer_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

        self.external_to_user.pop(binding.external_channel_id, None)
        self.allocator.release(binding.listen_port)

        try:
            await self._run_in_thread(self.gateway.stop_session, binding.session_id, reason)
        finally:
            await self._run_in_thread(self.ari.delete_channel, binding.external_channel_id)
            await self._run_in_thread(self.ari.delete_bridge, binding.bridge_id)

        self.completed_calls += 1
        self.tenant_guard.release_call()
        self._last_activity = time.monotonic()
        self._emit(
            "session_stopped",
            user_channel_id=user_channel_id,
            external_channel_id=binding.external_channel_id,
            bridge_id=binding.bridge_id,
            session_id=binding.session_id,
            reason=reason,
        )

    async def _handle_channel_end(self, event: dict) -> None:
        channel = event.get("channel") or {}
        channel_id = str(channel.get("id") or "")
        if not channel_id:
            return

        if channel_id in self.sessions:
            await self._cleanup_user_session(channel_id, "user_channel_end")
            return

        user_channel_id = self.external_to_user.get(channel_id)
        if user_channel_id:
            await self._cleanup_user_session(user_channel_id, "external_channel_end")

    async def _handle_event(self, event: dict) -> None:
        event_type = str(event.get("type") or "")
        if event_type == "StasisStart":
            await self._handle_stasis_start(event)
        elif event_type in {"StasisEnd", "ChannelDestroyed", "ChannelHangupRequest"}:
            await self._handle_channel_end(event)

    async def _poll_gateway_sessions(self) -> None:
        for user_channel_id, binding in list(self.sessions.items()):
            try:
                stats = await self._run_in_thread(self.gateway.session_stats, binding.session_id)
            except Exception as exc:  # noqa: BLE001
                self._emit(
                    "gateway_stats_error",
                    user_channel_id=user_channel_id,
                    session_id=binding.session_id,
                    error=str(exc),
                )
                continue

            state = str(stats.get("state") or "")
            if state not in {"stopped", "failed"}:
                continue

            stop_reason = str(stats.get("stop_reason") or "gateway_terminal")
            await self._cleanup_user_session(user_channel_id, f"gateway_{stop_reason}")

    def _events_ws_url(self) -> str:
        app = quote(self.app_name, safe="")
        api_key = quote(f"{self.ari_username}:{self.ari_password}", safe="")
        return f"ws://{self.ari_host}:{self.ari_port}/ari/events?app={app}&api_key={api_key}"

    async def run(self) -> int:
        if websockets is None:
            raise RuntimeError(
                "Missing dependency 'websockets'. Use backend/venv/bin/python or install websockets>=13."
            )

        await self._run_in_thread(self.ari.ping)

        ws_url = self._events_ws_url()
        self._emit("controller_started", ws_url=ws_url)
        last_gateway_poll = time.monotonic()

        async with websockets.connect(ws_url, max_size=2 * 1024 * 1024, ping_interval=20, ping_timeout=20) as ws:
            while not self._stop:
                if self.max_completed_calls > 0 and self.completed_calls >= self.max_completed_calls and not self.sessions:
                    break

                if self.idle_timeout_seconds > 0:
                    idle_for = time.monotonic() - self._last_activity
                    if idle_for >= self.idle_timeout_seconds and not self.sessions and self.started_calls > 0:
                        break

                if self.sessions and (time.monotonic() - last_gateway_poll) >= self.gateway_poll_interval_seconds:
                    await self._poll_gateway_sessions()
                    last_gateway_poll = time.monotonic()

                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                except TimeoutError:
                    continue

                event = json.loads(raw)
                await self._handle_event(event)

        for user_channel_id in list(self.sessions.keys()):
            await self._cleanup_user_session(user_channel_id, "controller_shutdown")

        for user_channel_id, task in list(self.transfer_tasks.items()):
            if task.done():
                continue
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
            self.transfer_tasks.pop(user_channel_id, None)

        bridges = await self._run_in_thread(self.ari.list_bridges)
        channels = await self._run_in_thread(self.ari.list_channels)
        external_channels = [c.get("id") for c in channels if str(c.get("name") or "").startswith("UnicastRTP/")]

        self._emit(
            "controller_finished",
            started_calls=self.started_calls,
            completed_calls=self.completed_calls,
            failed_calls=self.failed_calls,
            transfer_attempts=self.transfer_attempts,
            transfer_successes=self.transfer_successes,
            transfer_failures=self.transfer_failures,
            transfer_rejected=self.transfer_rejected,
            tenant_id=self.tenant_guard.tenant_id,
            tenant_active_calls=self.tenant_guard.active_calls,
            tenant_transfer_inflight=self.tenant_guard.transfer_inflight,
            remaining_bridges=len(bridges),
            remaining_external_channels=len(external_channels),
        )

        if self.max_completed_calls > 0 and self.completed_calls < self.max_completed_calls:
            return 2
        if external_channels:
            return 3
        return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Day 5 ARI external media controller")
    parser.add_argument("--ari-host", default="127.0.0.1")
    parser.add_argument("--ari-port", type=int, default=8088)
    parser.add_argument("--ari-username", default="day5")
    parser.add_argument("--ari-password", default="day5_local_only_change_me")
    parser.add_argument("--app-name", default="talky_day5")
    parser.add_argument("--gateway-base-url", default="http://127.0.0.1:18080")
    parser.add_argument("--gateway-rtp-ip", default="127.0.0.1")
    parser.add_argument("--gateway-rtp-port-start", type=int, default=32000)
    parser.add_argument("--gateway-rtp-port-end", type=int, default=32999)
    parser.add_argument("--max-completed-calls", type=int, default=20)
    parser.add_argument("--idle-timeout-seconds", type=int, default=30)
    parser.add_argument("--gateway-poll-interval-seconds", type=float, default=1.0)
    parser.add_argument("--gateway-echo-enabled", type=int, choices=(0, 1), default=1)
    parser.add_argument("--blind-transfer-enabled", type=int, choices=(0, 1), default=0)
    parser.add_argument("--blind-transfer-endpoint", default="Local/blind_target@wsm-synthetic")
    parser.add_argument("--blind-transfer-delay-seconds", type=float, default=1.5)
    parser.add_argument("--blind-transfer-use-continue", type=int, choices=(0, 1), default=0)
    parser.add_argument("--blind-transfer-continue-context", default="wsm-synthetic")
    parser.add_argument("--blind-transfer-continue-extension", default="blind_target")
    parser.add_argument("--blind-transfer-continue-priority", type=int, default=1)
    parser.add_argument("--tenant-id", default="default-tenant")
    parser.add_argument("--tenant-max-active-calls", type=int, default=1000)
    parser.add_argument("--tenant-max-transfer-inflight", type=int, default=1000)
    return parser


async def _async_main(args: argparse.Namespace) -> int:
    ari = AriHttpClient(args.ari_host, args.ari_port, args.ari_username, args.ari_password)
    gateway = GatewayHttpClient(args.gateway_base_url)
    transfer_config = TransferConfig(
        enabled=bool(args.blind_transfer_enabled),
        target_endpoint=str(args.blind_transfer_endpoint),
        delay_seconds=max(0.0, float(args.blind_transfer_delay_seconds)),
        use_continue_fallback=bool(args.blind_transfer_use_continue),
        continue_context=str(args.blind_transfer_continue_context),
        continue_extension=str(args.blind_transfer_continue_extension),
        continue_priority=max(1, int(args.blind_transfer_continue_priority)),
    )
    controller = Day5Controller(
        ari_client=ari,
        gateway_client=gateway,
        ari_host=args.ari_host,
        ari_port=args.ari_port,
        ari_username=args.ari_username,
        ari_password=args.ari_password,
        app_name=args.app_name,
        gateway_rtp_ip=args.gateway_rtp_ip,
        gateway_rtp_port_start=args.gateway_rtp_port_start,
        gateway_rtp_port_end=args.gateway_rtp_port_end,
        max_completed_calls=args.max_completed_calls,
        idle_timeout_seconds=args.idle_timeout_seconds,
        gateway_poll_interval_seconds=args.gateway_poll_interval_seconds,
        gateway_echo_enabled=bool(args.gateway_echo_enabled),
        transfer_config=transfer_config,
        tenant_id=args.tenant_id,
        tenant_max_active_calls=args.tenant_max_active_calls,
        tenant_max_transfer_inflight=args.tenant_max_transfer_inflight,
    )

    loop = asyncio.get_running_loop()
    for signame in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signame, controller.stop)
        except NotImplementedError:  # pragma: no cover
            pass

    try:
        return await controller.run()
    finally:
        ari.close()
        gateway.close()


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    try:
        return asyncio.run(_async_main(args))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"event": "controller_crash", "error": str(exc)}), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
