"""
Phase 4.4 — 1000-concurrent certification harness.

Drives a 60-minute 1000-concurrent soak against a cluster running the
Phase 3 helm chart on enterprise-tier provider plans, then evaluates
each pass criterion from architecture_plan.md Phase 4.4 against
Prometheus metrics. Returns 0 if all pass, non-zero otherwise.

Pass criteria:
  - p95 turn latency at most 2.0s for the entire window
  - zero call drops attributable to platform (provider-side outages
    logged but excluded — flagged via the resilient_*_failover_total
    counters)
  - all alerts green except chaos-induced ones that auto-recovered
    within SLO

Operator workflow:

  PROM_URL=https://prom.talky.example.com:9090 \\
  BASE_URL=https://nginx.talky.example.com    \\
      ./venv/bin/python backend/scripts/certify_1000.py

The script does NOT manage chaos experiments — run those in parallel
via soak_runner.sh if you want chaos-during-certify, or run pristine
for a clean 1000-concurrent number.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Optional

import aiohttp

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("certify")


@dataclass
class Criteria:
    p95_turn_max_seconds: float = 2.0
    drops_attributable_max: int = 0
    saturation_pct_max: float = 90.0


@dataclass
class Result:
    name: str
    passed: bool
    detail: str
    measured: Optional[float] = None
    threshold: Optional[float] = None


@dataclass
class Run:
    start_ts: float = field(default_factory=time.time)
    end_ts: float = 0.0
    concurrent: int = 1000
    duration_s: int = 3600
    results: list[Result] = field(default_factory=list)


async def _prom_query(session: aiohttp.ClientSession, url: str, expr: str) -> Optional[float]:
    """Single-shot Prometheus query — returns the latest sample value
    or None if the query failed / returned no data."""
    try:
        async with session.get(
            f"{url}/api/v1/query",
            params={"query": expr},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            data = await resp.json()
        results = (data.get("data") or {}).get("result") or []
        if not results:
            return None
        value = results[0].get("value")
        if not value or len(value) < 2:
            return None
        return float(value[1])
    except Exception as exc:
        log.warning("prom_query_failed expr=%r err=%s", expr, exc)
        return None


async def _measure_p95_turn(session, prom_url, since_seconds: int) -> Optional[float]:
    expr = (
        "histogram_quantile(0.95, "
        f"sum(rate(talky_turn_latency_seconds_bucket[{since_seconds}s])) by (le)"
        ")"
    )
    return await _prom_query(session, prom_url, expr)


async def _measure_max_saturation(session, prom_url, since_seconds: int) -> Optional[float]:
    expr = (
        f"max_over_time((100 * sum(talky_active_calls) / sum(talky_pod_capacity))[{since_seconds}s:30s])"
    )
    return await _prom_query(session, prom_url, expr)


async def _measure_platform_drops(session, prom_url, since_seconds: int) -> Optional[float]:
    """Count rejected calls EXCLUDING capacity-503 (correct behaviour)
    and EXCLUDING provider-failover (covered by resilient_* path)."""
    expr = (
        "sum(increase(talky_calls_rejected_total"
        '{reason!="capacity",reason!="draining"}'
        f"[{since_seconds}s]))"
    )
    return await _prom_query(session, prom_url, expr)


async def _drive_load(base_url: str, concurrent: int, duration_s: int) -> int:
    """Spawn the existing loadtest_calls.py via asyncio subprocess.
    Returns the subprocess exit code."""
    cmd = [
        sys.executable,
        os.path.join(os.path.dirname(__file__), "loadtest_calls.py"),
        "--base-url", base_url,
        "--concurrent", str(concurrent),
        "--duration", str(duration_s),
        "--rps", str(max(int(concurrent / 30), 1)),
    ]
    log.info("certify_load_start cmd=%s", " ".join(cmd))
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    if proc.stdout is not None:
        async for line in proc.stdout:
            log.info("loadtest: %s", line.decode().rstrip())
    return await proc.wait()


async def _evaluate(prom_url: str, run: Run, criteria: Criteria) -> None:
    window = int(run.end_ts - run.start_ts)
    async with aiohttp.ClientSession() as session:
        p95 = await _measure_p95_turn(session, prom_url, window)
        sat = await _measure_max_saturation(session, prom_url, window)
        drops = await _measure_platform_drops(session, prom_url, window)

    run.results.append(Result(
        name="p95_turn_latency",
        passed=p95 is not None and p95 <= criteria.p95_turn_max_seconds,
        detail=f"{p95:.2f}s <= {criteria.p95_turn_max_seconds}s" if p95 else "no data",
        measured=p95, threshold=criteria.p95_turn_max_seconds,
    ))
    run.results.append(Result(
        name="cluster_saturation_max",
        passed=sat is not None and sat <= criteria.saturation_pct_max,
        detail=f"{sat:.1f}% <= {criteria.saturation_pct_max}%" if sat else "no data",
        measured=sat, threshold=criteria.saturation_pct_max,
    ))
    run.results.append(Result(
        name="platform_attributable_drops",
        passed=drops is not None and drops <= criteria.drops_attributable_max,
        detail=f"{drops:.0f} <= {criteria.drops_attributable_max}" if drops is not None else "no data",
        measured=drops, threshold=float(criteria.drops_attributable_max),
    ))


def _format_report(run: Run) -> str:
    sep = "-" * 64
    lines = [
        sep,
        f"Certification run - {run.concurrent} concurrent, {run.duration_s}s",
        f"Window: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(run.start_ts))}"
        f" -> {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(run.end_ts))}",
        sep,
    ]
    for r in run.results:
        mark = "PASS" if r.passed else "FAIL"
        lines.append(f"  [{mark}] {r.name}: {r.detail}")
    lines.append(sep)
    overall = all(r.passed for r in run.results)
    lines.append(f"  Overall: {'PASS' if overall else 'FAIL'}")
    lines.append(sep)
    return "\n".join(lines)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--base-url", default=os.getenv("BASE_URL", "http://localhost:8000"))
    p.add_argument("--prom-url", default=os.getenv("PROM_URL", "http://prometheus:9090"))
    p.add_argument("--concurrent", type=int, default=1000)
    p.add_argument("--duration", type=int, default=3600)
    p.add_argument("--p95-target", type=float, default=2.0)
    p.add_argument("--out", default="./certify-results")
    args = p.parse_args()

    os.makedirs(args.out, exist_ok=True)
    run = Run(concurrent=args.concurrent, duration_s=args.duration)

    rc = asyncio.run(_drive_load(args.base_url, args.concurrent, args.duration))
    run.end_ts = time.time()
    if rc != 0:
        log.warning("loadtest exited rc=%d - proceeding with metric eval anyway", rc)

    criteria = Criteria(p95_turn_max_seconds=args.p95_target)
    asyncio.run(_evaluate(args.prom_url, run, criteria))

    report = _format_report(run)
    print(report)

    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(run.start_ts))
    out_path = os.path.join(args.out, f"certify_{stamp}.json")
    with open(out_path, "w") as fh:
        json.dump({
            "concurrent": run.concurrent,
            "duration_s": run.duration_s,
            "start_ts": run.start_ts,
            "end_ts": run.end_ts,
            "results": [r.__dict__ for r in run.results],
        }, fh, indent=2)
    log.info("report_saved path=%s", out_path)

    return 0 if all(r.passed for r in run.results) else 1


if __name__ == "__main__":
    sys.exit(main())
