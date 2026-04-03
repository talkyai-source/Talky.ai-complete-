#!/usr/bin/env python3
"""Day 6 media resilience probe for voice-gateway-cpp.

Validates no-RTP timeout behavior, bounded jitter-buffer behavior, and state
transitions with deterministic fault-injection scenarios.
"""

from __future__ import annotations

import argparse
import json
import socket
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class ScenarioResult:
    name: str
    passed: bool
    expected_reason: str
    observed_reason: str
    observed_state: str
    notes: str
    stats: dict[str, Any]


def http_json(method: str, url: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    body = None
    headers = {"Content-Type": "application/json"}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url=url, method=method, data=body, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = {"error": text}
        parsed["http_status"] = exc.code
        return parsed


def reserve_udp_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    port = int(sock.getsockname()[1])
    sock.close()
    return port


def build_rtp_packet(seq: int, timestamp: int, ssrc: int, payload: bytes) -> bytes:
    header = bytearray(12)
    header[0] = 0x80
    header[1] = 0x00
    header[2] = (seq >> 8) & 0xFF
    header[3] = seq & 0xFF
    header[4] = (timestamp >> 24) & 0xFF
    header[5] = (timestamp >> 16) & 0xFF
    header[6] = (timestamp >> 8) & 0xFF
    header[7] = timestamp & 0xFF
    header[8] = (ssrc >> 24) & 0xFF
    header[9] = (ssrc >> 16) & 0xFF
    header[10] = (ssrc >> 8) & 0xFF
    header[11] = ssrc & 0xFF
    return bytes(header) + payload


def send_rtp_sequence(target_port: int, sequence_numbers: list[int], pace_ms: float = 0.0) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    payload = bytes([0x7E] * 160)
    ssrc = 0x44445555
    ts = 16000
    for idx, seq in enumerate(sequence_numbers):
        packet = build_rtp_packet(seq=seq & 0xFFFF, timestamp=(ts + idx * 160) & 0xFFFFFFFF, ssrc=ssrc, payload=payload)
        sock.sendto(packet, ("127.0.0.1", target_port))
        if pace_ms > 0.0:
            time.sleep(pace_ms / 1000.0)
    sock.close()


def start_session(base_url: str, session_id: str, listen_port: int, remote_port: int, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "session_id": session_id,
        "listen_ip": "127.0.0.1",
        "listen_port": listen_port,
        "remote_ip": "127.0.0.1",
        "remote_port": remote_port,
        "codec": "pcmu",
        "ptime_ms": 20,
    }
    payload.update(overrides or {})
    return http_json("POST", f"{base_url}/v1/sessions/start", payload)


def stop_session(base_url: str, session_id: str, reason: str) -> dict[str, Any]:
    return http_json("POST", f"{base_url}/v1/sessions/stop", {"session_id": session_id, "reason": reason})


def session_stats(base_url: str, session_id: str) -> dict[str, Any]:
    return http_json("GET", f"{base_url}/v1/sessions/{session_id}/stats")


def wait_for_terminal_reason(base_url: str, session_id: str, expected_reason: str, timeout_s: float = 6.0) -> tuple[bool, dict[str, Any]]:
    deadline = time.monotonic() + timeout_s
    last_stats: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last_stats = session_stats(base_url, session_id)
        if int(last_stats.get("http_status", 200)) == 404:
            break
        state = str(last_stats.get("state") or "")
        reason = str(last_stats.get("stop_reason") or "")
        if state == "stopped" and reason == expected_reason:
            return True, last_stats
        time.sleep(0.05)
    return False, last_stats


def run_startup_timeout(base_url: str) -> ScenarioResult:
    session_id = "day6-start-timeout"
    listen_port = reserve_udp_port()
    remote_port = reserve_udp_port()

    start_resp = start_session(
        base_url,
        session_id,
        listen_port,
        remote_port,
        {
            "startup_no_rtp_timeout_ms": 400,
            "active_no_rtp_timeout_ms": 900,
            "hold_no_rtp_timeout_ms": 900,
            "session_final_timeout_ms": 5000,
            "watchdog_tick_ms": 50,
        },
    )

    passed, stats = wait_for_terminal_reason(base_url, session_id, "start_timeout", timeout_s=4.0)
    stop_session(base_url, session_id, "cleanup_start_timeout")

    return ScenarioResult(
        name="startup_silence",
        passed=passed and start_resp.get("status") == "started",
        expected_reason="start_timeout",
        observed_reason=str(stats.get("stop_reason") or ""),
        observed_state=str(stats.get("state") or ""),
        notes="No RTP packets sent after start.",
        stats=stats,
    )


def run_no_rtp_timeout(base_url: str) -> ScenarioResult:
    session_id = "day6-no-rtp-timeout"
    listen_port = reserve_udp_port()
    remote_port = reserve_udp_port()

    start_resp = start_session(
        base_url,
        session_id,
        listen_port,
        remote_port,
        {
            "startup_no_rtp_timeout_ms": 1000,
            "active_no_rtp_timeout_ms": 700,
            "hold_no_rtp_timeout_ms": 1500,
            "session_final_timeout_ms": 5000,
            "watchdog_tick_ms": 50,
        },
    )

    send_rtp_sequence(listen_port, [1000, 1001, 1002, 1003, 1004], pace_ms=20.0)

    passed, stats = wait_for_terminal_reason(base_url, session_id, "no_rtp_timeout", timeout_s=4.0)
    stop_session(base_url, session_id, "cleanup_no_rtp_timeout")

    return ScenarioResult(
        name="active_no_rtp_timeout",
        passed=passed and start_resp.get("status") == "started",
        expected_reason="no_rtp_timeout",
        observed_reason=str(stats.get("stop_reason") or ""),
        observed_state=str(stats.get("state") or ""),
        notes="RTP starts then stops before active timeout window.",
        stats=stats,
    )


def run_hold_timeout(base_url: str) -> ScenarioResult:
    session_id = "day6-hold-timeout"
    listen_port = reserve_udp_port()
    remote_port = reserve_udp_port()

    start_resp = start_session(
        base_url,
        session_id,
        listen_port,
        remote_port,
        {
            "startup_no_rtp_timeout_ms": 1000,
            "active_no_rtp_timeout_ms": 3000,
            "hold_no_rtp_timeout_ms": 1200,
            "session_final_timeout_ms": 6000,
            "watchdog_tick_ms": 50,
        },
    )

    send_rtp_sequence(listen_port, [2000, 2001, 2002, 2003, 2004], pace_ms=20.0)

    passed, stats = wait_for_terminal_reason(base_url, session_id, "no_rtp_timeout_hold", timeout_s=4.0)
    stop_session(base_url, session_id, "cleanup_hold_timeout")

    return ScenarioResult(
        name="hold_no_rtp_timeout",
        passed=passed and start_resp.get("status") == "started",
        expected_reason="no_rtp_timeout_hold",
        observed_reason=str(stats.get("stop_reason") or ""),
        observed_state=str(stats.get("state") or ""),
        notes="Hold timeout configured shorter than active timeout to exercise hold reason path.",
        stats=stats,
    )


def run_reorder_duplicate(base_url: str) -> ScenarioResult:
    session_id = "day6-reorder-duplicate"
    listen_port = reserve_udp_port()
    remote_port = reserve_udp_port()

    start_resp = start_session(
        base_url,
        session_id,
        listen_port,
        remote_port,
        {
            "startup_no_rtp_timeout_ms": 1000,
            "active_no_rtp_timeout_ms": 4000,
            "hold_no_rtp_timeout_ms": 4000,
            "session_final_timeout_ms": 6000,
            "watchdog_tick_ms": 50,
            "jitter_buffer_capacity_frames": 64,
            "jitter_buffer_prefetch_frames": 3,
        },
    )

    seqs = [3000, 3001, 3003, 3002, 3003, 3004, 3006, 3005, 3005, 3007]
    send_rtp_sequence(listen_port, seqs, pace_ms=5.0)
    time.sleep(0.4)
    stats = session_stats(base_url, session_id)

    out_of_order = int(stats.get("out_of_order_packets", 0))
    duplicates = int(stats.get("duplicate_packets", 0))
    passed = (
        start_resp.get("status") == "started"
        and int(stats.get("http_status", 200)) == 200
        and str(stats.get("state") or "") in {"buffering", "active", "degraded", "starting"}
        and (out_of_order > 0 or duplicates > 0)
    )

    stop_session(base_url, session_id, "reorder_duplicate_complete")
    post_stop = session_stats(base_url, session_id)

    return ScenarioResult(
        name="burst_reorder_loss",
        passed=passed,
        expected_reason="running_or_manual_stop",
        observed_reason=str(post_stop.get("stop_reason") or stats.get("stop_reason") or ""),
        observed_state=str(post_stop.get("state") or stats.get("state") or ""),
        notes=f"out_of_order={out_of_order} duplicate={duplicates}",
        stats=post_stop if int(post_stop.get("http_status", 200)) == 200 else stats,
    )


def run_queue_pressure(base_url: str) -> ScenarioResult:
    session_id = "day6-queue-pressure"
    listen_port = reserve_udp_port()
    remote_port = reserve_udp_port()

    start_resp = start_session(
        base_url,
        session_id,
        listen_port,
        remote_port,
        {
            "startup_no_rtp_timeout_ms": 1000,
            "active_no_rtp_timeout_ms": 4000,
            "hold_no_rtp_timeout_ms": 4000,
            "session_final_timeout_ms": 6000,
            "watchdog_tick_ms": 50,
            "jitter_buffer_capacity_frames": 16,
            "jitter_buffer_prefetch_frames": 3,
        },
    )

    seqs = list(range(4000, 4400))
    send_rtp_sequence(listen_port, seqs, pace_ms=0.0)
    time.sleep(0.8)

    stats = session_stats(base_url, session_id)
    overflow = int(stats.get("jitter_buffer_overflow_drops", 0))
    dropped = int(stats.get("dropped_packets", 0))

    passed = (
        start_resp.get("status") == "started"
        and int(stats.get("http_status", 200)) == 200
        and (overflow > 0 or dropped > 0)
    )

    stop_session(base_url, session_id, "queue_pressure_complete")
    post_stop = session_stats(base_url, session_id)

    return ScenarioResult(
        name="queue_pressure",
        passed=passed,
        expected_reason="running_or_manual_stop",
        observed_reason=str(post_stop.get("stop_reason") or stats.get("stop_reason") or ""),
        observed_state=str(post_stop.get("state") or stats.get("state") or ""),
        notes=f"overflow={overflow} dropped={dropped}",
        stats=post_stop if int(post_stop.get("http_status", 200)) == 200 else stats,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Day 6 media resilience probe")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18080)
    parser.add_argument("--output-results", required=True)
    parser.add_argument("--output-timeout-summary", required=True)
    parser.add_argument("--output-jitter-metrics", required=True)
    args = parser.parse_args()

    base_url = f"http://{args.host}:{args.port}"

    scenarios = [
        run_startup_timeout,
        run_no_rtp_timeout,
        run_hold_timeout,
        run_reorder_duplicate,
        run_queue_pressure,
    ]

    results: list[ScenarioResult] = []
    for fn in scenarios:
        results.append(fn(base_url))

    process_stats = http_json("GET", f"{base_url}/stats")

    timeout_summary = {
        "start_timeout": 0,
        "no_rtp_timeout": 0,
        "no_rtp_timeout_hold": 0,
        "final_timeout": 0,
        "timeout_events_total": int(process_stats.get("timeout_events_total", 0)),
    }

    jitter_metrics = {
        "jitter_buffer_overflow_drops": 0,
        "jitter_buffer_late_drops": 0,
        "duplicate_packets": 0,
        "out_of_order_packets": 0,
        "dropped_packets": 0,
    }

    for row in results:
        timeout_key = row.observed_reason
        if timeout_key in timeout_summary:
            timeout_summary[timeout_key] += 1

        jitter_metrics["jitter_buffer_overflow_drops"] += int(row.stats.get("jitter_buffer_overflow_drops", 0))
        jitter_metrics["jitter_buffer_late_drops"] += int(row.stats.get("jitter_buffer_late_drops", 0))
        jitter_metrics["duplicate_packets"] += int(row.stats.get("duplicate_packets", 0))
        jitter_metrics["out_of_order_packets"] += int(row.stats.get("out_of_order_packets", 0))
        jitter_metrics["dropped_packets"] += int(row.stats.get("dropped_packets", 0))

    result_payload = {
        "host": args.host,
        "port": args.port,
        "scenarios": [asdict(r) for r in results],
        "process_stats": process_stats,
        "passed": sum(1 for r in results if r.passed),
        "failed": sum(1 for r in results if not r.passed),
    }

    with open(args.output_results, "w", encoding="utf-8") as f:
        json.dump(result_payload, f, indent=2)

    with open(args.output_timeout_summary, "w", encoding="utf-8") as f:
        json.dump(timeout_summary, f, indent=2)

    with open(args.output_jitter_metrics, "w", encoding="utf-8") as f:
        json.dump(jitter_metrics, f, indent=2)

    print(json.dumps(result_payload, indent=2))
    return 0 if result_payload["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
