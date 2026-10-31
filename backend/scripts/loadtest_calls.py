"""Retired direct-origination load driver.

The bridge now accepts only worker-owned requests backed by a committed
``dialer_jobs`` attempt and ``calls`` intent. This legacy script cannot safely
manufacture those records and therefore fails closed before making a network
request. Build a load fixture through the normal campaign/dialer queue instead.

Usage:

  ./venv/bin/python backend/scripts/loadtest_calls.py \\
      --request-workers 10 --minimum-originated 300 \\
      --required-peak-live 50 --duration 600 \\
      --evidence-json ./loadtest-evidence.json \\
      --base-url http://localhost:8000

The remaining parsing/measurement helpers stay importable for historical
evidence validation, but neither ``main`` nor ``_originate_one`` can bypass the
durable production protocol.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiohttp

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("loadtest")

RETIRED_ORIGINATION_REASON = (
    "Direct load-test origination is retired; create durable dialer jobs and "
    "drive the production worker instead."
)


@dataclass
class Stats:
    originated: int = 0
    queued: int = 0
    rejected_503: int = 0
    rejected_other: int = 0
    errors: int = 0
    status_errors: int = 0
    request_inflight: int = 0
    max_request_inflight: int = 0
    baseline_live_calls: int | None = None
    max_live_calls: int = 0
    final_live_calls: int | None = None
    live_samples: int = 0
    drain_complete: bool = False
    originated_call_ids: list[str] = field(default_factory=list)
    _originated_call_id_set: set[str] = field(default_factory=set, repr=False)
    latencies_ms: list[float] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._originated_call_id_set.update(self.originated_call_ids)


async def _originate_one(
    session: aiohttp.ClientSession,
    base_url: str,
    destination: str,
    tenant_id: str,
    caller_id: str,
    internal_token: str,
    stats: Stats,
) -> None:
    """Fail closed: direct requests cannot satisfy the durable intent contract."""

    raise RuntimeError(RETIRED_ORIGINATION_REASON)

    t0 = time.monotonic()
    payload = {
        "destination": destination,
        "caller_id": caller_id,
        "tenant_id": tenant_id,
    }
    headers = {
        "Content-Type": "application/json",
        "X-Internal-Service-Token": internal_token,
    }
    stats.request_inflight += 1
    stats.max_request_inflight = max(
        stats.max_request_inflight,
        stats.request_inflight,
    )
    try:
        async with session.post(
            f"{base_url.rstrip('/')}/api/v1/sip/telephony/call",
            json=payload,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            elapsed_ms = (time.monotonic() - t0) * 1000.0
            stats.latencies_ms.append(elapsed_ms)
            if resp.status == 200:
                try:
                    response_payload = await resp.json(content_type=None)
                except Exception:
                    response_payload = None
                call_id = (
                    str(response_payload.get("call_id") or "").strip()
                    if isinstance(response_payload, dict)
                    else ""
                )
                if (
                    isinstance(response_payload, dict)
                    and response_payload.get("status") == "calling"
                    and call_id
                    and call_id not in stats._originated_call_id_set
                ):
                    stats._originated_call_id_set.add(call_id)
                    stats.originated_call_ids.append(call_id)
                    stats.originated += 1
                else:
                    stats.rejected_other += 1
                    log.debug("invalid origination success payload")
            elif resp.status == 202:
                try:
                    response_payload = await resp.json(content_type=None)
                except Exception:
                    response_payload = None
                if (
                    isinstance(response_payload, dict)
                    and response_payload.get("status") == "queued"
                ):
                    stats.queued += 1
                else:
                    stats.rejected_other += 1
                    log.debug("invalid queued response payload")
            elif resp.status == 503:
                stats.rejected_503 += 1
            else:
                stats.rejected_other += 1
                body = await resp.text()
                log.debug("non-503 reject: %s %s", resp.status, body[:120])
    except Exception as exc:
        stats.errors += 1
        log.debug("originate error: %s", exc)
    finally:
        stats.request_inflight -= 1


def _live_count_from_status(payload: Any) -> int:
    """Extract a trustworthy cluster count from the bridge status response."""

    if not isinstance(payload, dict):
        raise ValueError("status response is not an object")
    if payload.get("status") != "running" or payload.get("healthy") is not True:
        raise ValueError("telephony adapter is not healthy and running")
    if payload.get("connected") is not True:
        raise ValueError("telephony adapter is not connected")
    capacity = payload.get("capacity")
    if not isinstance(capacity, dict):
        raise ValueError("status response has no capacity object")
    current = capacity.get("global_current")
    maximum = capacity.get("global_max")
    if isinstance(current, bool) or not isinstance(current, int) or current < 0:
        raise ValueError("capacity.global_current is not a non-negative integer")
    if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum <= 0:
        raise ValueError("capacity.global_max is not a positive integer")
    if current > maximum:
        raise ValueError("cluster live-call count exceeds its declared maximum")
    return current


async def _sample_live_count(
    session: aiohttp.ClientSession,
    base_url: str,
    internal_token: str,
    stats: Stats,
) -> int | None:
    """Sample Redis-backed live-call concurrency; failures remain fatal evidence."""

    try:
        async with session.get(
            f"{base_url.rstrip('/')}/api/v1/sip/telephony/status",
            headers={"X-Internal-Service-Token": internal_token},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status != 200:
                raise ValueError(f"status endpoint returned HTTP {resp.status}")
            payload = await resp.json(content_type=None)
        current = _live_count_from_status(payload)
        stats.live_samples += 1
        stats.max_live_calls = max(stats.max_live_calls, current)
        stats.final_live_calls = current
        return current
    except Exception as exc:
        stats.status_errors += 1
        log.warning("live-count sample failed: %s", exc)
        return None


async def _monitor_live_calls(
    session: aiohttp.ClientSession,
    base_url: str,
    internal_token: str,
    stats: Stats,
    stop_event: asyncio.Event,
    poll_interval_s: float,
) -> None:
    while not stop_event.is_set():
        await _sample_live_count(session, base_url, internal_token, stats)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=poll_interval_s)
        except TimeoutError:
            continue


async def _wait_for_zero_drain(
    session: aiohttp.ClientSession,
    base_url: str,
    internal_token: str,
    stats: Stats,
    poll_interval_s: float,
    timeout_s: float,
) -> None:
    """Require two consecutive zero-live samples after origination stops."""

    deadline = time.monotonic() + timeout_s
    consecutive_zero = 0
    while time.monotonic() < deadline:
        current = await _sample_live_count(session, base_url, internal_token, stats)
        if current == 0:
            consecutive_zero += 1
            if consecutive_zero >= 2:
                stats.drain_complete = True
                return
        else:
            consecutive_zero = 0
        remaining = deadline - time.monotonic()
        if remaining > 0:
            await asyncio.sleep(min(poll_interval_s, remaining))


async def _ramp_and_hold(
    base_url: str,
    request_workers: int,
    duration_s: int,
    tenant_id: str,
    destination: str,
    caller_id: str,
    internal_token: str,
    rps: float,
    stats: Stats,
    status_poll_s: float = 0.25,
    drain_timeout_s: float = 300.0,
) -> None:
    """
    Keep at most ``request_workers`` origination HTTP requests in flight.

    Origination is paced by `rps` (requests/sec) so we don't slam the
    pod with a single instantaneous burst. A fixed worker pool, rather than an
    ever-growing task list, is the concurrency boundary. Accepted voice calls
    can outlive their HTTP request; their lifecycle is measured separately by
    the telephony metrics and reconciliation gates.
    """

    if request_workers <= 0:
        raise ValueError("request_workers must be greater than zero")
    if duration_s <= 0:
        raise ValueError("duration must be greater than zero")
    if rps <= 0:
        raise ValueError("rps must be greater than zero")
    if status_poll_s <= 0:
        raise ValueError("status_poll_s must be greater than zero")
    if drain_timeout_s <= 0:
        raise ValueError("drain_timeout_s must be greater than zero")
    if not tenant_id.strip():
        raise ValueError("tenant_id must not be empty")
    if not destination.strip():
        raise ValueError("destination must not be empty")
    if not internal_token.strip():
        raise ValueError("INTERNAL_SERVICE_TOKEN must not be empty")

    interval = 1.0 / rps
    queue: asyncio.Queue[object] = asyncio.Queue(maxsize=request_workers)
    sentinel = object()

    async with aiohttp.ClientSession() as http:
        baseline = await _sample_live_count(http, base_url, internal_token, stats)
        stats.baseline_live_calls = baseline
        if baseline is None:
            raise RuntimeError("cluster live-call baseline is unavailable")
        if baseline != 0:
            raise RuntimeError(f"cluster live-call baseline must be zero, observed {baseline}")
        deadline = time.monotonic() + duration_s
        monitor_stop = asyncio.Event()
        monitor = asyncio.create_task(
            _monitor_live_calls(
                http,
                base_url,
                internal_token,
                stats,
                monitor_stop,
                status_poll_s,
            )
        )

        async def worker() -> None:
            while True:
                item = await queue.get()
                try:
                    if item is sentinel:
                        return
                    await _originate_one(
                        http,
                        base_url,
                        destination,
                        tenant_id,
                        caller_id,
                        internal_token,
                        stats,
                    )
                finally:
                    queue.task_done()

        workers = [asyncio.create_task(worker()) for _ in range(request_workers)]
        next_launch = time.monotonic()
        try:
            while time.monotonic() < deadline:
                await queue.put(object())
                next_launch += interval
                delay = next_launch - time.monotonic()
                if delay > 0:
                    await asyncio.sleep(min(delay, max(0.0, deadline - time.monotonic())))

                observed = (
                    stats.originated + stats.queued + stats.rejected_503 + stats.rejected_other
                )
                if observed and observed % 25 == 0:
                    _summarise(stats, prefix="progress")

            # Drain requests already admitted to the bounded queue.
            await queue.join()
        finally:
            for _ in workers:
                await queue.put(sentinel)
            await asyncio.gather(*workers, return_exceptions=True)
            monitor_stop.set()
            await monitor

        await _wait_for_zero_drain(
            http,
            base_url,
            internal_token,
            stats,
            status_poll_s,
            drain_timeout_s,
        )


def _summarise(stats: Stats, *, prefix: str = "final") -> None:
    lat = stats.latencies_ms
    if lat:
        lat_sorted = sorted(lat)
        p50 = lat_sorted[len(lat_sorted) // 2]
        p95 = lat_sorted[int(len(lat_sorted) * 0.95)]
    else:
        p50 = p95 = 0.0
    log.info(
        "%s originated=%d queued=%d rejected_503=%d rejected_other=%d errors=%d "
        "status_errors=%d max_request_inflight=%d baseline_live=%s max_live=%d "
        "final_live=%s drain_complete=%s p50_originate_ms=%.0f p95_originate_ms=%.0f",
        prefix,
        stats.originated,
        stats.queued,
        stats.rejected_503,
        stats.rejected_other,
        stats.errors,
        stats.status_errors,
        stats.max_request_inflight,
        stats.baseline_live_calls,
        stats.max_live_calls,
        stats.final_live_calls,
        stats.drain_complete,
        p50,
        p95,
    )


def _result_failures(
    stats: Stats,
    *,
    minimum_originated: int,
    required_peak_live: int,
) -> list[str]:
    failures: list[str] = []
    if stats.errors:
        failures.append(f"request_or_driver_errors={stats.errors}")
    if stats.rejected_other:
        failures.append(f"unexpected_responses={stats.rejected_other}")
    if stats.queued:
        failures.append(f"untracked_queued_responses={stats.queued}")
    if stats.status_errors:
        failures.append(f"live_status_errors={stats.status_errors}")
    if stats.baseline_live_calls != 0:
        failures.append(f"baseline_live_calls={stats.baseline_live_calls!r}")
    if stats.live_samples < 3:
        failures.append(f"live_samples={stats.live_samples}<3")
    if stats.originated != len(stats.originated_call_ids):
        failures.append("originated_count_does_not_match_unique_call_ids")
    if len(stats._originated_call_id_set) != len(stats.originated_call_ids):
        failures.append("originated_call_ids_are_not_unique")
    if stats.originated < minimum_originated:
        failures.append(f"originated={stats.originated}<{minimum_originated}")
    if stats.max_live_calls < required_peak_live:
        failures.append(f"max_live_calls={stats.max_live_calls}<{required_peak_live}")
    if not stats.drain_complete or stats.final_live_calls != 0:
        failures.append(f"drain_incomplete(final_live_calls={stats.final_live_calls!r})")
    return failures


def _result_exit_code(
    stats: Stats,
    *,
    minimum_originated: int,
    required_peak_live: int,
    interrupted: bool = False,
) -> int:
    """Fail closed when the pressure driver did not produce trustworthy traffic."""

    if interrupted:
        return 130
    return (
        1
        if _result_failures(
            stats,
            minimum_originated=minimum_originated,
            required_peak_live=required_peak_live,
        )
        else 0
    )


def _write_evidence(
    target: Path,
    *,
    stats: Stats,
    minimum_originated: int,
    required_peak_live: int,
    duration_s: int,
    request_workers: int,
    rps: float,
    started_at_epoch: float,
    finished_at_epoch: float,
    exit_code: int,
    driver_error: str | None,
) -> None:
    """Publish one non-secret evidence artifact without overwriting history."""

    if target.exists():
        raise FileExistsError(f"refusing to overwrite evidence artifact: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(f"{target.name}.partial")
    if partial.exists():
        raise FileExistsError(f"refusing to overwrite partial evidence: {partial}")
    payload = {
        "schema_version": 1,
        "passed": exit_code == 0,
        "exit_code": exit_code,
        "driver_error": driver_error,
        "requirements": {
            "minimum_originated": minimum_originated,
            "required_peak_live": required_peak_live,
            "zero_live_baseline": True,
            "zero_live_drain": True,
        },
        "configuration": {
            "duration_seconds": duration_s,
            "request_workers": request_workers,
            "requests_per_second": rps,
            "started_at_epoch": started_at_epoch,
            "finished_at_epoch": finished_at_epoch,
        },
        "observed": {
            "originated": stats.originated,
            "queued": stats.queued,
            "rejected_503": stats.rejected_503,
            "rejected_other": stats.rejected_other,
            "request_errors": stats.errors,
            "live_status_errors": stats.status_errors,
            "max_request_inflight": stats.max_request_inflight,
            "baseline_live_calls": stats.baseline_live_calls,
            "max_live_calls": stats.max_live_calls,
            "final_live_calls": stats.final_live_calls,
            "live_samples": stats.live_samples,
            "drain_complete": stats.drain_complete,
            "originated_call_ids": stats.originated_call_ids,
        },
        "failures": _result_failures(
            stats,
            minimum_originated=minimum_originated,
            required_peak_live=required_peak_live,
        ),
    }
    with partial.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    # Hard-link publication is atomic and, unlike os.replace(), cannot
    # overwrite a path another evidence writer created after our first check.
    os.link(partial, target)
    partial.unlink()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--base-url", default="http://localhost:8000")
    p.add_argument(
        "--request-workers",
        type=int,
        default=10,
        help="Maximum simultaneous HTTP requests; this is not live-call concurrency.",
    )
    p.add_argument(
        "--minimum-originated",
        type=int,
        required=True,
        help="Minimum unique status=calling responses required (release floor: 300).",
    )
    p.add_argument(
        "--required-peak-live",
        type=int,
        required=True,
        help="Required Redis-backed cluster live-call peak from /status.",
    )
    p.add_argument(
        "--evidence-json",
        required=True,
        help="New path for the immutable, non-secret evidence JSON artifact.",
    )
    p.add_argument("--duration", type=int, default=600, help="Soak duration seconds.")
    p.add_argument("--tenant-id", default=os.getenv("LOADTEST_TENANT_ID", ""))
    p.add_argument(
        "--destination",
        default=os.getenv("LOADTEST_DESTINATION", "9999"),
        help="Loopback destination served by the test trunk.",
    )
    p.add_argument(
        "--caller-id",
        default=os.getenv("LOADTEST_CALLER_ID", "1001"),
        help="Caller ID configured for the test trunk.",
    )
    p.add_argument("--rps", type=float, default=2.0, help="Origination requests per second.")
    p.add_argument(
        "--status-poll-interval",
        type=float,
        default=0.25,
        help="Seconds between Redis-backed cluster live-count samples.",
    )
    p.add_argument(
        "--drain-timeout",
        type=float,
        default=300.0,
        help="Seconds allowed for two consecutive zero-live drain samples.",
    )
    args = p.parse_args()

    p.error(RETIRED_ORIGINATION_REASON)

    internal_token = os.getenv("INTERNAL_SERVICE_TOKEN", "").strip()
    if not internal_token:
        p.error("INTERNAL_SERVICE_TOKEN must be set in the process environment")
    if not args.tenant_id.strip():
        p.error("--tenant-id or LOADTEST_TENANT_ID is required")
    if args.request_workers <= 0:
        p.error("--request-workers must be greater than zero")
    if args.minimum_originated < 300:
        p.error("--minimum-originated must be at least 300 for release evidence")
    if args.required_peak_live <= 0:
        p.error("--required-peak-live must be greater than zero")
    if args.required_peak_live > args.minimum_originated:
        p.error("--required-peak-live cannot exceed --minimum-originated")
    if args.duration <= 0:
        p.error("--duration must be greater than zero")
    if args.rps <= 0:
        p.error("--rps must be greater than zero")
    if args.status_poll_interval <= 0:
        p.error("--status-poll-interval must be greater than zero")
    if args.drain_timeout <= 0:
        p.error("--drain-timeout must be greater than zero")
    if not args.destination.strip():
        p.error("--destination must not be empty")
    if not args.caller_id.strip():
        p.error("--caller-id must not be empty")
    evidence_path = Path(args.evidence_json).expanduser()
    if evidence_path.exists():
        p.error(f"refusing to overwrite evidence artifact: {evidence_path}")
    evidence_partial = evidence_path.with_name(f"{evidence_path.name}.partial")
    if evidence_partial.exists():
        p.error(f"refusing to overwrite partial evidence: {evidence_partial}")

    stats = Stats()
    started_at_epoch = time.time()
    log.info(
        "loadtest_start base=%s request_workers=%d minimum_originated=%d "
        "required_peak_live=%d duration=%ds rps=%.1f",
        args.base_url,
        args.request_workers,
        args.minimum_originated,
        args.required_peak_live,
        args.duration,
        args.rps,
    )
    interrupted = False
    driver_error: str | None = None
    try:
        asyncio.run(
            _ramp_and_hold(
                base_url=args.base_url,
                request_workers=args.request_workers,
                duration_s=args.duration,
                tenant_id=args.tenant_id,
                destination=args.destination,
                caller_id=args.caller_id,
                internal_token=internal_token,
                rps=args.rps,
                stats=stats,
                status_poll_s=args.status_poll_interval,
                drain_timeout_s=args.drain_timeout,
            )
        )
    except KeyboardInterrupt:
        log.warning("interrupted")
        interrupted = True
    except Exception as exc:
        stats.errors += 1
        driver_error = f"{type(exc).__name__}: {exc}"
        log.error("load driver failed: %s", driver_error)
    finally:
        _summarise(stats)

    exit_code = _result_exit_code(
        stats,
        minimum_originated=args.minimum_originated,
        required_peak_live=args.required_peak_live,
        interrupted=interrupted,
    )
    try:
        _write_evidence(
            evidence_path,
            stats=stats,
            minimum_originated=args.minimum_originated,
            required_peak_live=args.required_peak_live,
            duration_s=args.duration,
            request_workers=args.request_workers,
            rps=args.rps,
            started_at_epoch=started_at_epoch,
            finished_at_epoch=time.time(),
            exit_code=exit_code,
            driver_error=driver_error,
        )
    except Exception as exc:
        log.error("evidence publication failed: %s", exc)
        exit_code = 1
    if exit_code:
        log.error(
            "loadtest_invalid failures=%s",
            ",".join(
                _result_failures(
                    stats,
                    minimum_originated=args.minimum_originated,
                    required_peak_live=args.required_peak_live,
                )
            )
            or "interrupted_or_evidence_failure",
        )
        raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
