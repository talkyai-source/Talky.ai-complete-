"""Application lifecycle for the existing exact-event expiry restoration."""
from __future__ import annotations

import asyncio
import logging

from app.domain.services.suspension_service import SuspensionService

logger = logging.getLogger(__name__)


class SuspensionExpiryWatchdog:
    """Each app may scan; existing database transactions serialize effects."""

    def __init__(self, service: SuspensionService, *, interval_seconds: float = 60.0):
        self.service = service
        self.interval_seconds = max(0.01, min(3600.0, interval_seconds))
        self.task = asyncio.create_task(self._run(), name="formal-suspension-expiry")

    async def _run(self) -> None:
        while True:
            started = asyncio.get_running_loop().time()
            try:
                results = await self.service.process_auto_restores()
                if results:
                    logger.info(
                        "suspension_expiry_pass observed=%d restored=%d unknown=%d",
                        len(results), sum(r["restored"] is True for r in results),
                        sum(r["restored"] is None for r in results),
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("suspension_expiry_pass_unavailable err_type=%s", type(exc).__name__)
            elapsed = asyncio.get_running_loop().time() - started
            await asyncio.sleep(max(0.0, self.interval_seconds - elapsed))

    async def stop(self) -> None:
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)


def start_suspension_expiry_watchdog(db_pool, *, redis_client=None, interval_seconds=60.0):
    if db_pool is None:
        return None
    return SuspensionExpiryWatchdog(
        SuspensionService(db_pool, redis_client=redis_client), interval_seconds=interval_seconds,
    )
