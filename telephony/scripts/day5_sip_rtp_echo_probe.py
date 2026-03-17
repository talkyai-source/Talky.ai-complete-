#!/usr/bin/env python3
"""Day 5 SIP + RTP echo probe.

Places deterministic SIP calls, sends PCMU RTP toward Asterisk, and asserts that
RTP is received back through the Day 5 external-media echo path.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import socket
import struct
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

TO_TAG_RE = re.compile(r";\s*tag=([^;>\s]+)", re.IGNORECASE)


@dataclass
class CallResult:
    call_index: int
    success: bool
    invite_codes: List[int]
    bye_code: Optional[int]
    sent_rtp_packets: int
    received_rtp_packets: int
    reason: str
    call_id: str
    bind_ip: str
    target: str


def _build_sdp(bind_ip: str, media_port: int) -> str:
    return (
        "v=0\r\n"
        f"o=- 0 0 IN IP4 {bind_ip}\r\n"
        "s=Talky Day5 Probe\r\n"
        f"c=IN IP4 {bind_ip}\r\n"
        "t=0 0\r\n"
        f"m=audio {media_port} RTP/AVP 0\r\n"
        "a=rtpmap:0 PCMU/8000\r\n"
        "a=ptime:20\r\n"
    )


def _build_request(
    *,
    method: str,
    target_host: str,
    target_port: int,
    from_user: str,
    extension: str,
    local_ip: str,
    local_port: int,
    call_id: str,
    cseq: int,
    from_tag: str,
    to_header: str,
    contact_user: str,
    body: str = "",
) -> bytes:
    branch = f"z9hG4bK-{uuid.uuid4().hex[:12]}"
    request_uri = f"sip:{extension}@{target_host}:{target_port}"
    lines = [
        f"{method} {request_uri} SIP/2.0",
        f"Via: SIP/2.0/UDP {local_ip}:{local_port};branch={branch};rport",
        f"From: <sip:{from_user}@{local_ip}>;tag={from_tag}",
        f"To: {to_header}",
        f"Call-ID: {call_id}",
        f"CSeq: {cseq} {method}",
        f"Contact: <sip:{contact_user}@{local_ip}:{local_port}>",
        "Max-Forwards: 70",
        "User-Agent: talky-day5-echo-probe/1.0",
    ]
    if body:
        lines.append("Content-Type: application/sdp")
    lines.append(f"Content-Length: {len(body.encode('utf-8'))}")
    message = "\r\n".join(lines) + "\r\n\r\n" + body
    return message.encode("utf-8")


def _parse_response(raw: bytes) -> Tuple[int, Dict[str, str], str]:
    text = raw.decode("utf-8", errors="ignore")
    head, _, body = text.partition("\r\n\r\n")
    lines = head.split("\r\n")
    first = lines[0] if lines else ""
    parts = first.split()
    if len(parts) < 2 or parts[0] != "SIP/2.0":
        raise ValueError(f"not a SIP response: {first!r}")
    code = int(parts[1])
    headers: Dict[str, str] = {}
    for line in lines[1:]:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        headers[key.strip().lower()] = value.strip()
    return code, headers, body


def _extract_to_tag(to_header: str) -> Optional[str]:
    match = TO_TAG_RE.search(to_header)
    if not match:
        return None
    return match.group(1).strip()


def _parse_sdp_target(default_host: str, sdp_body: str) -> Tuple[str, int]:
    target_ip = default_host
    target_port: Optional[int] = None

    for raw_line in sdp_body.splitlines():
        line = raw_line.strip()
        if line.startswith("c=IN IP4 "):
            target_ip = line.split()[-1]
        elif line.startswith("m=audio "):
            parts = line.split()
            if len(parts) >= 2:
                target_port = int(parts[1])

    if target_port is None or target_port <= 0:
        raise ValueError("missing media port in SDP answer")

    return target_ip, target_port


def _build_rtp_packet(*, seq: int, ts: int, ssrc: int, payload: bytes) -> bytes:
    header = struct.pack("!BBHII", 0x80, 0x00, seq & 0xFFFF, ts & 0xFFFFFFFF, ssrc & 0xFFFFFFFF)
    return header + payload


def _is_valid_rtp_p0(packet: bytes) -> bool:
    if len(packet) < 12:
        return False
    version = (packet[0] >> 6) & 0x03
    payload_type = packet[1] & 0x7F
    return version == 2 and payload_type == 0


def _recv_until_final(
    sock: socket.socket,
    *,
    timeout_s: float,
    expected_method: str,
    call_id: str,
) -> Tuple[List[int], Optional[Dict[str, str]], str]:
    deadline = time.monotonic() + timeout_s
    seen_codes: List[int] = []
    final_headers: Optional[Dict[str, str]] = None
    final_body = ""

    while time.monotonic() < deadline:
        remaining = max(0.05, deadline - time.monotonic())
        sock.settimeout(remaining)
        data, _ = sock.recvfrom(65535)
        code, headers, body = _parse_response(data)

        cseq_value = headers.get("cseq", "")
        cseq_parts = cseq_value.split()
        if len(cseq_parts) < 2:
            continue
        method = cseq_parts[1].upper()
        if method != expected_method.upper():
            continue
        if headers.get("call-id", "").strip() != call_id:
            continue

        seen_codes.append(code)
        if code >= 200:
            final_headers = headers
            final_body = body
            break

    return seen_codes, final_headers, final_body


def _run_call(
    *,
    call_index: int,
    host: str,
    port: int,
    extension: str,
    bind_ip: str,
    timeout_s: float,
    hold_ms: int,
) -> CallResult:
    media_port = 42000 + ((call_index * 2) % 2000)
    from_user = f"day5probe{call_index}"
    from_tag = uuid.uuid4().hex[:8]
    call_id = f"{uuid.uuid4().hex}-{call_index}@talky.day5"

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sip_sock:
        sip_sock.bind((bind_ip, 0))
        local_ip, local_port = sip_sock.getsockname()

        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as rtp_sock:
            rtp_sock.bind((local_ip, media_port))
            rtp_sock.settimeout(0.01)

            sdp = _build_sdp(local_ip, media_port)
            to_header = f"<sip:{extension}@{host}:{port}>"

            invite = _build_request(
                method="INVITE",
                target_host=host,
                target_port=port,
                from_user=from_user,
                extension=extension,
                local_ip=local_ip,
                local_port=local_port,
                call_id=call_id,
                cseq=1,
                from_tag=from_tag,
                to_header=to_header,
                contact_user=from_user,
                body=sdp,
            )
            sip_sock.sendto(invite, (host, port))

            invite_codes, final_headers, final_body = _recv_until_final(
                sip_sock,
                timeout_s=timeout_s,
                expected_method="INVITE",
                call_id=call_id,
            )
            if not final_headers:
                return CallResult(
                    call_index=call_index,
                    success=False,
                    invite_codes=invite_codes,
                    bye_code=None,
                    sent_rtp_packets=0,
                    received_rtp_packets=0,
                    reason="timeout_waiting_invite_final",
                    call_id=call_id,
                    bind_ip=bind_ip,
                    target=f"{host}:{port}",
                )

            final_invite_code = invite_codes[-1]
            if final_invite_code != 200:
                return CallResult(
                    call_index=call_index,
                    success=False,
                    invite_codes=invite_codes,
                    bye_code=None,
                    sent_rtp_packets=0,
                    received_rtp_packets=0,
                    reason=f"invite_failed_{final_invite_code}",
                    call_id=call_id,
                    bind_ip=bind_ip,
                    target=f"{host}:{port}",
                )

            to_value = final_headers.get("to", to_header)
            to_tag = _extract_to_tag(to_value)
            if not to_tag:
                return CallResult(
                    call_index=call_index,
                    success=False,
                    invite_codes=invite_codes,
                    bye_code=None,
                    sent_rtp_packets=0,
                    received_rtp_packets=0,
                    reason="missing_to_tag_in_200",
                    call_id=call_id,
                    bind_ip=bind_ip,
                    target=f"{host}:{port}",
                )

            try:
                remote_media_ip, remote_media_port = _parse_sdp_target(host, final_body)
            except Exception as exc:  # noqa: BLE001
                return CallResult(
                    call_index=call_index,
                    success=False,
                    invite_codes=invite_codes,
                    bye_code=None,
                    sent_rtp_packets=0,
                    received_rtp_packets=0,
                    reason=f"invalid_sdp_answer:{exc}",
                    call_id=call_id,
                    bind_ip=bind_ip,
                    target=f"{host}:{port}",
                )

            ack_to = f"<sip:{extension}@{host}:{port}>;tag={to_tag}"
            ack = _build_request(
                method="ACK",
                target_host=host,
                target_port=port,
                from_user=from_user,
                extension=extension,
                local_ip=local_ip,
                local_port=local_port,
                call_id=call_id,
                cseq=1,
                from_tag=from_tag,
                to_header=ack_to,
                contact_user=from_user,
                body="",
            )
            sip_sock.sendto(ack, (host, port))

            sent_rtp_packets = 0
            received_rtp_packets = 0

            next_send = time.monotonic()
            end_send = next_send + max(0.2, hold_ms / 1000.0)
            seq = random.randint(0, 65535)
            ts = random.randint(0, 2**32 - 1)
            ssrc = random.randint(1, 2**32 - 1)

            while time.monotonic() < end_send:
                now = time.monotonic()
                if now >= next_send:
                    marker = (call_index + sent_rtp_packets) % 251
                    payload = bytes([marker]) * 160
                    packet = _build_rtp_packet(seq=seq, ts=ts, ssrc=ssrc, payload=payload)
                    rtp_sock.sendto(packet, (remote_media_ip, remote_media_port))
                    sent_rtp_packets += 1
                    seq = (seq + 1) & 0xFFFF
                    ts = (ts + 160) & 0xFFFFFFFF
                    next_send += 0.02

                try:
                    data, _ = rtp_sock.recvfrom(2048)
                    if _is_valid_rtp_p0(data):
                        received_rtp_packets += 1
                except TimeoutError:
                    pass
                except OSError:
                    break

            tail_deadline = time.monotonic() + 0.25
            while time.monotonic() < tail_deadline:
                try:
                    data, _ = rtp_sock.recvfrom(2048)
                    if _is_valid_rtp_p0(data):
                        received_rtp_packets += 1
                except TimeoutError:
                    break
                except OSError:
                    break

            bye = _build_request(
                method="BYE",
                target_host=host,
                target_port=port,
                from_user=from_user,
                extension=extension,
                local_ip=local_ip,
                local_port=local_port,
                call_id=call_id,
                cseq=2,
                from_tag=from_tag,
                to_header=ack_to,
                contact_user=from_user,
                body="",
            )
            sip_sock.sendto(bye, (host, port))

            bye_codes, bye_headers, _ = _recv_until_final(
                sip_sock,
                timeout_s=timeout_s,
                expected_method="BYE",
                call_id=call_id,
            )
            bye_code = bye_codes[-1] if bye_codes else None
            if not bye_headers or bye_code != 200:
                reason = "timeout_waiting_bye_final" if not bye_headers else f"bye_failed_{bye_code}"
                return CallResult(
                    call_index=call_index,
                    success=False,
                    invite_codes=invite_codes,
                    bye_code=bye_code,
                    sent_rtp_packets=sent_rtp_packets,
                    received_rtp_packets=received_rtp_packets,
                    reason=reason,
                    call_id=call_id,
                    bind_ip=bind_ip,
                    target=f"{host}:{port}",
                )

            if received_rtp_packets <= 0:
                return CallResult(
                    call_index=call_index,
                    success=False,
                    invite_codes=invite_codes,
                    bye_code=bye_code,
                    sent_rtp_packets=sent_rtp_packets,
                    received_rtp_packets=received_rtp_packets,
                    reason="no_echo_rtp_received",
                    call_id=call_id,
                    bind_ip=bind_ip,
                    target=f"{host}:{port}",
                )

            return CallResult(
                call_index=call_index,
                success=True,
                invite_codes=invite_codes,
                bye_code=bye_code,
                sent_rtp_packets=sent_rtp_packets,
                received_rtp_packets=received_rtp_packets,
                reason="ok",
                call_id=call_id,
                bind_ip=bind_ip,
                target=f"{host}:{port}",
            )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Day 5 SIP + RTP echo probe")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=15060)
    parser.add_argument("--extension", default="750")
    parser.add_argument("--calls", type=int, default=20)
    parser.add_argument("--bind-ip", default="127.0.0.1")
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--hold-ms", type=int, default=800)
    parser.add_argument("--evidence-file", type=Path, required=True)
    return parser


def main() -> int:
    args = _build_parser().parse_args()

    results: List[CallResult] = []
    for i in range(1, args.calls + 1):
        result = _run_call(
            call_index=i,
            host=args.host,
            port=args.port,
            extension=args.extension,
            bind_ip=args.bind_ip,
            timeout_s=args.timeout,
            hold_ms=args.hold_ms,
        )
        results.append(result)
        print(
            f"[day5] call={i} success={result.success} invite={result.invite_codes} "
            f"bye={result.bye_code} rtp_sent={result.sent_rtp_packets} "
            f"rtp_recv={result.received_rtp_packets} reason={result.reason}",
            flush=True,
        )

    passed = sum(1 for r in results if r.success)
    failed = len(results) - passed
    total_sent = sum(r.sent_rtp_packets for r in results)
    total_recv = sum(r.received_rtp_packets for r in results)

    payload = {
        "target": f"{args.host}:{args.port}",
        "extension": args.extension,
        "calls": len(results),
        "passed": passed,
        "failed": failed,
        "total_rtp_packets_sent": total_sent,
        "total_rtp_packets_received": total_recv,
        "results": [asdict(r) for r in results],
    }

    args.evidence_file.parent.mkdir(parents=True, exist_ok=True)
    args.evidence_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(
        f"[day5] summary calls={len(results)} passed={passed} failed={failed} "
        f"rtp_sent={total_sent} rtp_recv={total_recv}",
        flush=True,
    )

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
