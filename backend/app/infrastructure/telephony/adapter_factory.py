"""
CallControlAdapterFactory — auto-detecting PBX adapter factory.

Usage
-----
    adapter = await CallControlAdapterFactory.create("auto")
    # or force a specific backend:
    adapter = await CallControlAdapterFactory.create("asterisk")
    adapter = await CallControlAdapterFactory.create("freeswitch")

Preferred usage (caching + health monitoring)
---------------------------------------------
    adapter = await AdapterRegistry.get_or_create("freeswitch")

Auto-detection order
--------------------
1. Try Asterisk (ARI health check) — primary B2BUA.
2. Try FreeSWITCH (ESL health check) — backup B2BUA.
3. Raise RuntimeError if neither is available.

The factory does NOT call adapter.connect() — caller is responsible for
connecting after factory returns an adapter instance. This keeps health_check()
lightweight (plain TCP probe) and avoids holding a persistent connection from
inside the factory.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

from app.domain.interfaces.call_control_adapter import CallControlAdapter

logger = logging.getLogger(__name__)


class CallControlAdapterFactory:
    """
    Creates a CallControlAdapter instance for the active B2BUA.

    All adapter classes are imported lazily to avoid circular imports and to
    keep the factory usable in test contexts that don't have all dependencies.
    """

    @classmethod
    async def create(
        cls,
        adapter_type: str | None = None,
        connect: bool = False,
    ) -> CallControlAdapter:
        """
        Return a CallControlAdapter for the requested (or auto-detected) backend.

        Parameters
        ----------
        adapter_type:
            "auto"        — try Asterisk first, fall back to FreeSWITCH (default)
            "asterisk"    — always use Asterisk
            "freeswitch"  — always use FreeSWITCH
        connect:
            If True, call adapter.connect() before returning.
            Default False so callers can configure additional callbacks first.
        """
        effective_type = (
            adapter_type
            or os.getenv("TELEPHONY_ADAPTER")
            or cls._read_pbx_backend_config()
            or "auto"
        ).lower()

        if effective_type == "asterisk":
            adapter = cls._make_asterisk()
        elif effective_type == "freeswitch":
            adapter = cls._make_freeswitch()
        else:
            adapter = await cls._auto_detect()

        if connect:
            await adapter.connect()

        return adapter

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @classmethod
    def _read_pbx_backend_config(cls) -> str | None:
        """Read ``providers.telephony.sip.pbx_backend`` from providers.yaml."""
        try:
            from app.core.config import ConfigManager
            config = ConfigManager()
            return config.get("providers.telephony.sip.pbx_backend")
        except Exception:
            return None

    @classmethod
    def _make_asterisk(cls) -> CallControlAdapter:
        from app.infrastructure.telephony.asterisk_adapter import AsteriskAdapter
        return AsteriskAdapter()

    @classmethod
    def _make_freeswitch(cls) -> CallControlAdapter:
        from app.infrastructure.telephony.freeswitch_adapter import FreeSwitchAdapter
        return FreeSwitchAdapter()

    @classmethod
    async def _auto_detect(cls) -> CallControlAdapter:
        """
        Probe Asterisk, then FreeSWITCH.
        Return the first adapter that responds to its health check.
        """
        asterisk = cls._make_asterisk()
        logger.debug("CallControlAdapterFactory: probing Asterisk …")
        try:
            if await asterisk.health_check():
                logger.info("CallControlAdapterFactory: Asterisk healthy → using AsteriskAdapter")
                return asterisk
        except Exception as exc:
            logger.debug(f"Asterisk probe failed: {exc}")

        freeswitch = cls._make_freeswitch()
        logger.debug("CallControlAdapterFactory: probing FreeSWITCH …")
        try:
            if await freeswitch.health_check():
                logger.info(
                    "CallControlAdapterFactory: FreeSWITCH healthy → using FreeSwitchAdapter"
                )
                return freeswitch
        except Exception as exc:
            logger.debug(f"FreeSWITCH probe failed: {exc}")

        raise RuntimeError(
            "CallControlAdapterFactory: no B2BUA available "
            "(both Asterisk and FreeSWITCH health checks failed)"
        )

    @classmethod
    def list_adapters(cls) -> list[str]:
        """Return the list of supported adapter type strings."""
        return ["auto", "asterisk", "freeswitch"]


# ---------------------------------------------------------------------------
# AdapterRegistry — process-level cache + background health monitor
# ---------------------------------------------------------------------------

class AdapterRegistry:
    """
    Process-level cache and background health monitor for CallControlAdapter.

    Prevents redundant ESL connections by returning the same adapter instance
    for the same effective adapter type.  A background asyncio.Task periodically
    calls ``health_check()`` on every cached adapter and logs warnings so that
    on-call alerting can detect PBX degradation before a call fails.

    The ESL-level auto-reconnect (Task 5) handles low-level socket recovery;
    this registry handles *application*-level visibility and lifecycle.

    Typical usage
    -------------
        # app startup
        AdapterRegistry.start_monitor()

        # instead of CallControlAdapterFactory.create():
        adapter = await AdapterRegistry.get_or_create("freeswitch")

        # app shutdown
        await AdapterRegistry.stop()
    """

    _instances: dict[str, CallControlAdapter] = {}
    _lock: Optional[asyncio.Lock] = None   # lazy-init; Lock requires a running loop
    _monitor_task: Optional[asyncio.Task] = None
    _monitor_interval: float = 30.0
    _stopping: bool = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @classmethod
    async def get_or_create(
        cls,
        adapter_type: str | None = None,
        connect: bool = False,
    ) -> CallControlAdapter:
        """
        Return a cached adapter for *adapter_type*, creating one if needed.

        The lock prevents concurrent creation of the same adapter key so that
        two simultaneous callers don't both spin up fresh connections.

        Parameters
        ----------
        adapter_type:
            Same values accepted by ``CallControlAdapterFactory.create()``.
        connect:
            Passed through to the factory on the first (cache-miss) call only.
        """
        effective_type = cls._resolve_key(adapter_type)

        async with cls._get_lock():
            cached = cls._instances.get(effective_type)
            if cached is not None and cached.connected:
                logger.debug("AdapterRegistry: cache HIT — %s (%s)", effective_type, cached.name)
                return cached

            logger.info("AdapterRegistry: cache MISS — creating %s adapter …", effective_type)
            adapter = await CallControlAdapterFactory.create(
                adapter_type=adapter_type,
                connect=connect,
            )
            cls._instances[effective_type] = adapter
            logger.info("AdapterRegistry: cached %s → %s", effective_type, adapter.name)
            return adapter

    @classmethod
    def start_monitor(cls, interval: float = 30.0) -> None:
        """
        Start the background health-check loop.

        Safe to call multiple times; only one task runs at a time.
        Must be called from within a running asyncio event loop.
        """
        if cls._monitor_task and not cls._monitor_task.done():
            logger.debug("AdapterRegistry: monitor already running")
            return

        cls._monitor_interval = interval
        cls._stopping = False
        cls._monitor_task = asyncio.create_task(
            cls._health_loop(), name="adapter-health-monitor"
        )
        logger.info(
            "AdapterRegistry: health monitor started (interval=%.0fs)", interval
        )

    @classmethod
    async def stop(cls) -> None:
        """
        Cancel the health monitor and disconnect all cached adapters.

        Called during application shutdown via ServiceContainer.shutdown().
        """
        cls._stopping = True

        if cls._monitor_task and not cls._monitor_task.done():
            cls._monitor_task.cancel()
            try:
                await cls._monitor_task
            except asyncio.CancelledError:
                pass
        cls._monitor_task = None

        for key, adapter in list(cls._instances.items()):
            try:
                if adapter.connected:
                    await adapter.disconnect()
                    logger.info("AdapterRegistry: disconnected cached %s adapter", key)
            except Exception as exc:
                logger.warning("AdapterRegistry: error disconnecting %s: %s", key, exc)

        cls._instances.clear()
        logger.info("AdapterRegistry: shutdown complete")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @classmethod
    def _get_lock(cls) -> asyncio.Lock:
        """Return the shared lock, lazily creating it in the current event loop."""
        if cls._lock is None:
            cls._lock = asyncio.Lock()
        return cls._lock

    @classmethod
    def _resolve_key(cls, adapter_type: str | None) -> str:
        """Compute the cache key using the same resolution order as the factory."""
        return (
            adapter_type
            or os.getenv("TELEPHONY_ADAPTER")
            or CallControlAdapterFactory._read_pbx_backend_config()
            or "auto"
        ).lower()

    @classmethod
    async def _health_loop(cls) -> None:
        """Periodically probe every cached adapter and log health status."""
        while not cls._stopping:
            try:
                await asyncio.sleep(cls._monitor_interval)
            except asyncio.CancelledError:
                break

            if cls._stopping:
                break

            for key, adapter in list(cls._instances.items()):
                try:
                    healthy = await asyncio.wait_for(
                        adapter.health_check(), timeout=10.0
                    )
                    if healthy:
                        logger.debug(
                            "AdapterRegistry health OK — %s (%s)", key, adapter.name
                        )
                    else:
                        logger.warning(
                            "AdapterRegistry health DEGRADED — %s (%s) connected=%s "
                            "(ESL reconnect handles low-level recovery)",
                            key,
                            adapter.name,
                            adapter.connected,
                        )
                except asyncio.TimeoutError:
                    logger.warning(
                        "AdapterRegistry health TIMEOUT — %s (%s)", key, adapter.name
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.error(
                        "AdapterRegistry health ERROR — %s (%s): %s",
                        key,
                        getattr(adapter, "name", "?"),
                        exc,
                    )
