"""
Concurrent-call load test harness.

Hits the outbound-call endpoint with N parallel originations so we can
measure pod behaviour under sustained concurrency. Audio realism comes
from the SIP loopback path (the C++ telephony gateway streams a 60-second
WAV the dialer points at) — this script is the pressure source.

Usage:

  ./venv/bin/python backend/scripts/loadtest_calls.py \\
      --concurrent 50 --duration 600 --base-url http://localhost:8000

Pass criteria for Phase 1 (per architecture_plan.md §Phase 1.6):
  - p95 first-audio < 1.2s
  - p95 turn latency < 1.8s
  - zero session leaks (watchdog deletions == 0 after drain)
  - no provider-inflight gauge crosses 85% of cap

The 503 response from the make_call endpoint indicates pod-at-capacity —
counted but not treated as a failure (it is the correct behaviour).

This is a stress driver only; latency / leak measurements are read from
Prometheus / `/api/v1/sip/telephony/status` after the run.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Optional

import aiohttp

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("loadtest")


@dataclass
class Stats:
    accepted: int = 0
    rejected_503: int = 0
    rejected_other: int = 0
    errors: int = 0
    latencies_ms: list[float] = field(default_factory=list)


async def _originate_one(
    session: aiohttp.ClientSession,
    base_url: str,
    destination: str,
    tenant_id: str,
    stats: Stats,
) -> None:
    t0 = time.monotonic()
    params = {
        "destination": destination,
        "caller_id": "1001",
        "tenant_id": tenant_id,
    }
    try:
        async with session.post(
            f"{base_url}/api/v1/sip/telephony/call",
            params=params,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            elapsed_ms = (time.monotonic() - t0) * 1000.0
            stats.latencies_ms.append(elapsed_ms)
            if resp.status == 200 or resp.status == 202:
                stats.accepted += 1
            elif resp.status == 503:
                stats.rejected_503 += 1
            else:
                stats.rejected_other += 1
                body = await resp.text()
                log.debug("non-503 reject: %s %s", resp.status, body[:120])
    except Exception as exc:
        stats.errors += 1
        log.debug("originate error: %s", exc)


async def _ramp_and_hold(
    base_url: str,
    concurrent: int,
    duration_s: int,
    tenant_id: str,
    destination: str,
    rps: float,
    stats: Stats,
) -> None:
    """
    Maintain `concurrent` calls in flight for `duration_s` seconds.

    Origination is paced by `rps` (requests/sec) so we don't slam the
    pod with a single instantaneous burst. The pod itself enforces the
    final concurrency ceiling — this script just keeps a steady stream
    of new calls coming in.
    """
    deadline = time.monotonic() + duration_s
    interval = 1.0 / max(rps, 0.1)
    async with aiohttp.ClientSession() as http:
        # Prime: kick off the first wave
        tasks: list[asyncio.Task] = []
        for _ in range(min(concurrent, 16)):
            tasks.append(
                asyncio.create_task(
                    _originate_one(http, base_url, destination, tenant_id, stats)
                )
            )
            await asyncio.sleep(interval * (0.5 + random.random()))

        # Hold: replenish at the configured rate
        while time.monotonic() < deadline:
            tasks.append(
                asyncio.create_task(
                    _originate_one(http, base_url, destination, tenant_id, stats)
                )
            )
            await asyncio.sleep(interval)
            tasks = [t for t in tasks if not t.done()]
            if len(tasks) % 25 == 0:
                _summarise(stats, prefix="progress")

        # Drain: wait for outstanding originations to finish (not the calls)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


def _summarise(stats: Stats, *, prefix: str = "final") -> None:
    lat = stats.latencies_ms
    if lat:
        lat_sorted = sorted(lat)
        p50 = lat_sorted[len(lat_sorted) // 2]
        p95 = lat_sorted[int(len(lat_sorted) * 0.95)]
    else:
        p50 = p95 = 0.0
    log.info(
        "%s accepted=%d rejected_503=%d rejected_other=%d errors=%d "
        "p50_originate_ms=%.0f p95_originate_ms=%.0f",
        prefix,
        stats.accepted, stats.rejected_503, stats.rejected_other, stats.errors,
        p50, p95,
    )


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--base-url", default="http://localhost:8000")
    p.add_argument("--concurrent", type=int, default=50,
                   help="Target concurrent calls (pod cap is the real ceiling).")
    p.add_argument("--duration", type=int, default=600,
                   help="Soak duration seconds.")
    p.add_argument("--tenant-id", default="loadtest")
    p.add_argument("--destination", default="9999",
                   help="Loopback destination served by the test trunk.")
    p.add_argument("--rps", type=float, default=2.0,
                   help="Origination requests per second.")
    args = p.parse_args()

    stats = Stats()
    log.info(
        "loadtest_start base=%s concurrent=%d duration=%ds rps=%.1f",
        args.base_url, args.concurrent, args.duration, args.rps,
    )
    try:
        asyncio.run(
            _ramp_and_hold(
                base_url=args.base_url,
                concurrent=args.concurrent,
                duration_s=args.duration,
                tenant_id=args.tenant_id,
                destination=args.destination,
                rps=args.rps,
                stats=stats,
            )
        )
    except KeyboardInterrupt:
        log.warning("interrupted")
    finally:
        _summarise(stats)


if __name__ == "__main__":
    main()
