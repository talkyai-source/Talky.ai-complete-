#!/usr/bin/env python3
"""Day 4 RTP loopback probe for voice-gateway-cpp.

Runs a local RTP burst against an active voice-gateway-cpp instance, validates
sequence/timestamp behavior, and computes pacing metrics.
"""

from __future__ import annotations

import argparse
import json
import socket
import statistics
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


@dataclass
class EchoPacket:
    seq: int
    ts: int
    payload_len: int
    received_at: float


def http_json(method: str, url: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    body = None
    headers = {"Content-Type": "application/json"}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url=url, method=method, data=body, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            text = resp.read().decode("utf-8")
            return json.loads(text)
    except urllib.error.HTTPError as exc:
        payload_text = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} {url}: {payload_text}") from exc


def build_rtp_packet(seq: int, timestamp: int, ssrc: int, payload: bytes) -> bytes:
    header = bytearray(12)
    header[0] = 0x80  # RTP v2
    header[1] = 0x00  # PT=0 (PCMU)
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


def parse_rtp_packet(data: bytes, received_at: float) -> EchoPacket | None:
    if len(data) < 12:
        return None
    if (data[0] >> 6) != 2:
        return None

    seq = (data[2] << 8) | data[3]
    ts = (data[4] << 24) | (data[5] << 16) | (data[6] << 8) | data[7]
    return EchoPacket(seq=seq, ts=ts, payload_len=len(data) - 12, received_at=received_at)


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    sorted_values = sorted(values)
    idx = int(round((len(sorted_values) - 1) * p))
    return sorted_values[idx]


def reserve_udp_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return int(port)


def main() -> int:
    parser = argparse.ArgumentParser(description="Day 4 RTP probe")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18080)
    parser.add_argument("--session-id", default="day4-probe-session")
    parser.add_argument("--packet-count", type=int, default=64)
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--output-pacing", required=True)
    parser.add_argument("--output-stats-sample", required=True)
    args = parser.parse_args()

    listen_port = reserve_udp_port()
    sink_port = reserve_udp_port()

    base_url = f"http://{args.host}:{args.port}"

    sink_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sink_sock.bind(("127.0.0.1", sink_port))
    sink_sock.settimeout(4.0)

    sender_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    start_payload = {
        "session_id": args.session_id,
        "listen_ip": "127.0.0.1",
        "listen_port": listen_port,
        "remote_ip": "127.0.0.1",
        "remote_port": sink_port,
        "codec": "pcmu",
        "ptime_ms": 20,
    }

    started = False
    start_time = time.monotonic()
    response_start: dict[str, Any] = {}
    response_stop: dict[str, Any] = {}
    response_health: dict[str, Any] = {}

    echo_packets: list[EchoPacket] = []

    try:
        response_start = http_json("POST", f"{base_url}/v1/sessions/start", start_payload)
        started = True

        # Health and process stats before media burst.
        response_health = http_json("GET", f"{base_url}/health")

        ssrc = 0x12345678
        payload = bytes([0x7F] * 160)

        # Burst send to force queueing in the paced transmitter.
        for idx in range(args.packet_count):
            pkt = build_rtp_packet(seq=1000 + idx, timestamp=50000 + (idx * 160), ssrc=ssrc, payload=payload)
            sender_sock.sendto(pkt, ("127.0.0.1", listen_port))

        # Collect echoed packets.
        end_deadline = time.monotonic() + 6.0
        while time.monotonic() < end_deadline and len(echo_packets) < args.packet_count:
            try:
                data, _ = sink_sock.recvfrom(4096)
            except socket.timeout:
                break
            parsed = parse_rtp_packet(data, time.monotonic())
            if parsed is not None:
                echo_packets.append(parsed)

        session_stats = http_json("GET", f"{base_url}/v1/sessions/{args.session_id}/stats")
        with open(args.output_stats_sample, "w", encoding="utf-8") as f:
            json.dump(session_stats, f, indent=2)

    finally:
        if started:
            try:
                response_stop = http_json(
                    "POST",
                    f"{base_url}/v1/sessions/stop",
                    {"session_id": args.session_id, "reason": "day4_probe_complete"},
                )
            except Exception as exc:  # noqa: BLE001
                response_stop = {"error": str(exc)}

        sender_sock.close()
        sink_sock.close()

    seq_ok = True
    ts_ok = True
    inter_packet_ms: list[float] = []

    for prev, curr in zip(echo_packets, echo_packets[1:]):
        if ((prev.seq + 1) & 0xFFFF) != curr.seq:
            seq_ok = False
        if ((prev.ts + 160) & 0xFFFFFFFF) != curr.ts:
            ts_ok = False
        inter_packet_ms.append((curr.received_at - prev.received_at) * 1000.0)

    p95 = percentile(inter_packet_ms, 0.95) if inter_packet_ms else 0.0
    p50 = statistics.median(inter_packet_ms) if inter_packet_ms else 0.0
    max_delta = max(inter_packet_ms) if inter_packet_ms else 0.0

    result = {
        "session_id": args.session_id,
        "listen_port": listen_port,
        "sink_port": sink_port,
        "packets_sent": args.packet_count,
        "packets_received": len(echo_packets),
        "sequence_monotonic": seq_ok,
        "timestamp_monotonic": ts_ok,
        "p50_inter_packet_ms": round(p50, 3),
        "p95_inter_packet_ms": round(p95, 3),
        "max_inter_packet_ms": round(max_delta, 3),
        "health_response": response_health,
        "start_response": response_start,
        "stop_response": response_stop,
        "probe_duration_ms": round((time.monotonic() - start_time) * 1000.0, 1),
        "acceptance": {
            "packets_received_min_48": len(echo_packets) >= 48,
            "sequence_monotonic": seq_ok,
            "timestamp_monotonic": ts_ok,
            "p95_between_19_21": 19.0 <= p95 <= 21.0,
            "max_le_25": max_delta <= 25.0,
        },
    }

    with open(args.output_json, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)

    with open(args.output_pacing, "w", encoding="utf-8") as f:
        f.write("Day4 RTP pacing analysis\n")
        f.write(f"packets_received={len(echo_packets)}\n")
        f.write(f"sequence_monotonic={seq_ok}\n")
        f.write(f"timestamp_monotonic={ts_ok}\n")
        f.write(f"p50_inter_packet_ms={p50:.3f}\n")
        f.write(f"p95_inter_packet_ms={p95:.3f}\n")
        f.write(f"max_inter_packet_ms={max_delta:.3f}\n")

    if not all(result["acceptance"].values()):
        print(json.dumps(result, indent=2))
        return 1

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
