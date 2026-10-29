"""Support background jobs, one-shot, run by talky-support-jobs.timer every 15 minutes.

The existing talky-cleanup.timer runs once a night, which cannot meet a
15-minute deadline check, hence a separate timer. Each run does the deadline
pass; the run in the first quarter of each hour (or ``--hourly``) also does
the hourly housekeeping. Every pass is idempotent, so a repeated or late run
is harmless. With SUPPORT_ENABLED=false the worker does nothing.

Exit code 0 on success (including "disabled"), 1 on failure.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timezone

from app.domain.services.support import jobs
from app.domain.services.support.config import load_settings

logger = logging.getLogger("support_jobs")


def should_run_hourly(now: datetime, forced: bool) -> bool:
    return forced or now.minute < 15


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Support deadline and housekeeping passes")
    parser.add_argument("--hourly", action="store_true", help="also run the hourly housekeeping now")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    settings = load_settings()
    if not settings.enabled:
        logger.info("support_jobs_skipped reason=support_disabled")
        return 0

    from app.core.db import close_db_pool, init_db_pool

    try:
        pool = await init_db_pool()
    except Exception as exc:
        logger.error("support_jobs_abort db_pool_init_failed error_type=%s", type(exc).__name__)
        return 1
    try:
        summary = {"sla": await jobs.run_sla_pass(pool, settings)}
        if should_run_hourly(datetime.now(timezone.utc), args.hourly):
            summary["hourly"] = await jobs.run_hourly_pass(pool, settings)
    except Exception as exc:
        logger.error("support_jobs_failed error_type=%s", type(exc).__name__, exc_info=True)
        return 1
    finally:
        try:
            await close_db_pool()
        except Exception:
            pass
    logger.info("support_jobs_complete summary=%s", summary)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
