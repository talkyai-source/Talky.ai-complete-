#!/usr/bin/env python3
"""Day 9 blind transfer + tenant control SIP probe.

Places SIP calls to the Day 5/8 Stasis extension and validates that calls are
terminated by remote BYE after blind transfer dispatch. Supports batched
parallel calls for tenant concurrency pressure.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import socket
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple


TO_TAG_RE = re.compile(r";\s*tag=([^;>\s]+)", re.IGNORECASE)


@dataclass
class Day9CallResult:
    batch_index: int
    call_index: int
    success: bool
    invite_codes: List[int]
    invite_final_code: Optional[int]
    remote_bye_received: bool
    remote_bye_elapsed_ms: Optional[float]
    caller_bye_code: Optional[int]
    reason: str
    call_id: str
    bind_ip: str
    target: str


def _build_sdp(bind_ip: str, media_port: int) -> str:
    return (
        "v=0\r\n"
        f"o=- 0 0 IN IP4 {bind_ip}\r\n"
        "s=Talky Day9 Probe\r\n"
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
        "User-Agent: talky-day9-transfer-probe/1.0",
    ]
    if body:
        lines.append("Content-Type: application/sdp")
    lines.append(f"Content-Length: {len(body.encode('utf-8'))}")
    return ("\r\n".join(lines) + "\r\n\r\n" + body).encode("utf-8")


def _build_response(status_code: int, reason: str, headers: Dict[str, str]) -> bytes:
    lines = [f"SIP/2.0 {status_code} {reason}"]
    for key in ("via", "from", "to", "call-id", "cseq"):
        value = headers.get(key)
        if value:
            normalized = {
                "via": "Via",
                "from": "From",
                "to": "To",
                "call-id": "Call-ID",
                "cseq": "CSeq",
            }[key]
            lines.append(f"{normalized}: {value}")
    lines.append("Content-Length: 0")
    return ("\r\n".join(lines) + "\r\n\r\n").encode("utf-8")


def _parse_message(raw: bytes) -> Tuple[str, Dict[str, str], str]:
    text = raw.decode("utf-8", errors="ignore")
    head, _, body = text.partition("\r\n\r\n")
    lines = head.split("\r\n")
    if not lines:
        raise ValueError("invalid SIP message")

    start_line = lines[0].strip()
    headers: Dict[str, str] = {}
    for line in lines[1:]:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        headers[key.strip().lower()] = value.strip()
    return start_line, headers, body


def _extract_to_tag(to_header: str) -> Optional[str]:
    match = TO_TAG_RE.search(to_header or "")
    return match.group(1).strip() if match else None


def _recv_invite_final(
    sock: socket.socket,
    *,
    timeout_s: float,
    call_id: str,
) -> Tuple[List[int], Optional[Dict[str, str]], str]:
    deadline = time.monotonic() + timeout_s
    seen_codes: List[int] = []
    final_headers: Optional[Dict[str, str]] = None
    final_body = ""

    while time.monotonic() < deadline:
        remaining = max(0.05, deadline - time.monotonic())
        sock.settimeout(remaining)
        packet, _ = sock.recvfrom(65535)
        start_line, headers, body = _parse_message(packet)
        if not start_line.startswith("SIP/2.0"):
            continue
        parts = start_line.split()
        if len(parts) < 2:
            continue
        code = int(parts[1])
        cseq = headers.get("cseq", "")
        cseq_parts = cseq.split()
        if len(cseq_parts) < 2 or cseq_parts[1].upper() != "INVITE":
            continue
        if headers.get("call-id", "").strip() != call_id:
            continue

        seen_codes.append(code)
        if code >= 200:
            final_headers = headers
            final_body = body
            break

    return seen_codes, final_headers, final_body


def _recv_bye_response(
    sock: socket.socket,
    *,
    timeout_s: float,
    call_id: str,
) -> Optional[int]:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        remaining = max(0.05, deadline - time.monotonic())
        sock.settimeout(remaining)
        packet, _ = sock.recvfrom(65535)
        start_line, headers, _body = _parse_message(packet)
        if not start_line.startswith("SIP/2.0"):
            continue
        parts = start_line.split()
        if len(parts) < 2:
            continue
        cseq = headers.get("cseq", "")
        cseq_parts = cseq.split()
        if len(cseq_parts) < 2 or cseq_parts[1].upper() != "BYE":
            continue
        if headers.get("call-id", "").strip() != call_id:
            continue
        return int(parts[1])
    return None


def _run_single_call(
    *,
    batch_index: int,
    call_index: int,
    host: str,
    port: int,
    extension: str,
    bind_ip: str,
    invite_timeout_s: float,
    remote_bye_timeout_s: float,
    require_remote_bye: bool,
) -> Day9CallResult:
    media_port = 47000 + ((call_index * 2) % 2000)
    from_user = f"day9probe{call_index}"
    from_tag = uuid.uuid4().hex[:8]
    call_id = f"{uuid.uuid4().hex}-{call_index}@talky.day9"

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sip_sock:
        sip_sock.bind((bind_ip, 0))
        local_ip, local_port = sip_sock.getsockname()

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

        invite_codes, final_headers, _final_body = _recv_invite_final(
            sip_sock,
            timeout_s=invite_timeout_s,
            call_id=call_id,
        )
        final_code = invite_codes[-1] if invite_codes else None
        if not final_headers:
            return Day9CallResult(
                batch_index=batch_index,
                call_index=call_index,
                success=False,
                invite_codes=invite_codes,
                invite_final_code=final_code,
                remote_bye_received=False,
                remote_bye_elapsed_ms=None,
                caller_bye_code=None,
                reason="invite_timeout",
                call_id=call_id,
                bind_ip=bind_ip,
                target=f"{host}:{port}",
            )
        if final_code != 200:
            return Day9CallResult(
                batch_index=batch_index,
                call_index=call_index,
                success=False,
                invite_codes=invite_codes,
                invite_final_code=final_code,
                remote_bye_received=False,
                remote_bye_elapsed_ms=None,
                caller_bye_code=None,
                reason=f"invite_failed_{final_code}",
                call_id=call_id,
                bind_ip=bind_ip,
                target=f"{host}:{port}",
            )

        to_value = final_headers.get("to", to_header)
        to_tag = _extract_to_tag(to_value)
        if not to_tag:
            return Day9CallResult(
                batch_index=batch_index,
                call_index=call_index,
                success=False,
                invite_codes=invite_codes,
                invite_final_code=final_code,
                remote_bye_received=False,
                remote_bye_elapsed_ms=None,
                caller_bye_code=None,
                reason="missing_to_tag",
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

        answer_ts = time.monotonic()
        remote_bye_received = False
        remote_bye_elapsed_ms: Optional[float] = None
        deadline = answer_ts + remote_bye_timeout_s
        while time.monotonic() < deadline:
            remaining = max(0.05, deadline - time.monotonic())
            sip_sock.settimeout(remaining)
            try:
                packet, addr = sip_sock.recvfrom(65535)
            except TimeoutError:
                continue
            start_line, headers, _body = _parse_message(packet)
            if start_line.startswith("SIP/2.0"):
                continue

            parts = start_line.split()
            if not parts:
                continue
            method = parts[0].upper()
            if method != "BYE":
                continue
            if headers.get("call-id", "").strip() != call_id:
                continue

            response = _build_response(200, "OK", headers)
            sip_sock.sendto(response, addr)
            remote_bye_received = True
            remote_bye_elapsed_ms = (time.monotonic() - answer_ts) * 1000.0
            break

        if remote_bye_received:
            return Day9CallResult(
                batch_index=batch_index,
                call_index=call_index,
                success=True,
                invite_codes=invite_codes,
                invite_final_code=final_code,
                remote_bye_received=True,
                remote_bye_elapsed_ms=remote_bye_elapsed_ms,
                caller_bye_code=None,
                reason="remote_bye_ok",
                call_id=call_id,
                bind_ip=bind_ip,
                target=f"{host}:{port}",
            )

        # Safety fallback: close from caller side if transfer did not complete in time.
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
        bye_code = _recv_bye_response(
            sip_sock,
            timeout_s=invite_timeout_s,
            call_id=call_id,
        )
        fallback_success = (not require_remote_bye) and (bye_code in {200, 481})
        return Day9CallResult(
            batch_index=batch_index,
            call_index=call_index,
            success=fallback_success,
            invite_codes=invite_codes,
            invite_final_code=final_code,
            remote_bye_received=False,
            remote_bye_elapsed_ms=None,
            caller_bye_code=bye_code,
            reason="caller_bye_fallback" if fallback_success else "remote_bye_timeout",
            call_id=call_id,
            bind_ip=bind_ip,
            target=f"{host}:{port}",
        )


def _percentile(values: List[float], p: int) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    if p <= 0:
        return ordered[0]
    if p >= 100:
        return ordered[-1]
    rank = (p / 100.0) * (len(ordered) - 1)
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    frac = rank - low
    return ordered[low] + (ordered[high] - ordered[low]) * frac


def run_probe(args: argparse.Namespace) -> int:
    results: List[Day9CallResult] = []
    total = args.batches * args.calls_per_batch
    call_index = 0

    for batch in range(1, args.batches + 1):
        futures = []
        with ThreadPoolExecutor(max_workers=args.calls_per_batch) as pool:
            for _ in range(args.calls_per_batch):
                call_index += 1
                futures.append(
                    pool.submit(
                        _run_single_call,
                        batch_index=batch,
                        call_index=call_index,
                        host=args.host,
                        port=args.port,
                        extension=args.extension,
                        bind_ip=args.bind_ip,
                        invite_timeout_s=args.invite_timeout,
                        remote_bye_timeout_s=args.remote_bye_timeout,
                        require_remote_bye=bool(args.require_remote_bye),
                    )
                )

            for future in as_completed(futures):
                results.append(future.result())

        time.sleep(max(0.0, args.batch_pause_seconds))

    results.sort(key=lambda r: (r.batch_index, r.call_index))
    remote_bye_elapsed = [r.remote_bye_elapsed_ms for r in results if r.remote_bye_elapsed_ms is not None]
    success_total = sum(1 for r in results if r.success)
    remote_bye_total = sum(1 for r in results if r.remote_bye_received)
    invite_fail_total = sum(1 for r in results if (r.invite_final_code or 0) != 200)

    summary: Dict[str, object] = {
        "calls": total,
        "success": success_total,
        "failed": total - success_total,
        "remote_bye_total": remote_bye_total,
        "invite_fail_total": invite_fail_total,
        "remote_bye_elapsed_ms": {
            "p50": _percentile([float(v) for v in remote_bye_elapsed], 50),
            "p95": _percentile([float(v) for v in remote_bye_elapsed], 95),
            "max": max(remote_bye_elapsed) if remote_bye_elapsed else None,
        },
        "results": [asdict(r) for r in results],
    }

    summary_path = Path(args.output_results)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    if args.output_summary:
        Path(args.output_summary).write_text(
            json.dumps(
                {
                    "calls": total,
                    "success": success_total,
                    "failed": total - success_total,
                    "remote_bye_total": remote_bye_total,
                    "invite_fail_total": invite_fail_total,
                    "remote_bye_elapsed_ms": summary["remote_bye_elapsed_ms"],
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    print(json.dumps(summary, indent=2))

    if success_total < args.min_success_calls:
        return 2
    if remote_bye_total < args.min_remote_bye_calls:
        return 3
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Day 9 blind transfer + tenant control probe")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=15060)
    parser.add_argument("--extension", default="750")
    parser.add_argument("--bind-ip", default="127.0.0.1")
    parser.add_argument("--invite-timeout", type=float, default=6.0)
    parser.add_argument("--remote-bye-timeout", type=float, default=8.0)
    parser.add_argument("--batches", type=int, default=2)
    parser.add_argument("--calls-per-batch", type=int, default=3)
    parser.add_argument("--batch-pause-seconds", type=float, default=0.5)
    parser.add_argument("--require-remote-bye", type=int, choices=(0, 1), default=0)
    parser.add_argument("--min-success-calls", type=int, default=2)
    parser.add_argument("--min-remote-bye-calls", type=int, default=0)
    parser.add_argument("--output-results", required=True)
    parser.add_argument("--output-summary", default="")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.batches <= 0:
        raise SystemExit("--batches must be >= 1")
    if args.calls_per_batch <= 0:
        raise SystemExit("--calls-per-batch must be >= 1")
    return run_probe(args)


if __name__ == "__main__":
    raise SystemExit(main())
