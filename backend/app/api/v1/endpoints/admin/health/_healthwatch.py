"""The server health watch's last result, for the System Health page.

scripts/healthwatch.py runs from a one-minute timer and records two Redis keys
(10 minute expiry): the time of its last completed run and the fingerprints of
the checks it holds as failing. This reads them. The sentences are the ones the
failure alert already sends (operational_alerts._sentences): numbers and fixed
words only, never customer data, free text or secrets.
"""
from __future__ import annotations

import asyncio
import time
from typing import List, Optional

from pydantic import BaseModel

from app.core import operational_alerts as alerts

# talky-healthwatch.timer: OnUnitActiveSec=1min.
HEALTHWATCH_INTERVAL_SECONDS = 60


class HealthWatchCheck(BaseModel):
    name: str
    status: str
    sentence: str


class HealthWatchState(BaseModel):
    # "ok": ran and nothing alerts; "alert": at least one check alerts;
    # "unknown": no run on record (stopped for 10 minutes) or Redis unreadable.
    overall: str
    last_run_at: Optional[float] = None
    age_seconds: Optional[int] = None
    interval_seconds: int = HEALTHWATCH_INTERVAL_SECONDS
    alerting: List[HealthWatchCheck] = []


def _text(value):
    return value.decode("utf-8", "replace") if isinstance(value, (bytes, bytearray)) else value


async def read_healthwatch_state(client, *, now: Optional[float] = None) -> HealthWatchState:
    if client is None:
        return HealthWatchState(overall="unknown")
    try:
        async with asyncio.timeout(1):
            last_run = _text(await client.get(alerts.HEALTHWATCH_LAST_RUN_KEY))
            failing = _text(await client.get(alerts.HEALTHWATCH_FAILING_KEY))
        ran_at = float(last_run) if last_run is not None else None
    except Exception:
        return HealthWatchState(overall="unknown")
    if ran_at is None:
        return HealthWatchState(overall="unknown")
    checks = []
    for item in (failing or "").split(","):
        if not item:
            continue
        name, _, rest = item.partition(":")
        status = rest.partition(":")[0] or "alert"
        try:
            sentence = alerts._sentences(item)[0]
        except Exception:  # a fingerprint in a form this version does not know: name it, never fail the page
            sentence = f"{name} is failing."
        checks.append(HealthWatchCheck(name=name, status=status, sentence=sentence))
    age = max(0, int((time.time() if now is None else now) - ran_at))
    return HealthWatchState(overall="alert" if checks else "ok", last_run_at=ran_at, age_seconds=age, alerting=checks)
