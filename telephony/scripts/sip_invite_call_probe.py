#!/usr/bin/env python3
"""Deterministic SIP INVITE/ACK/BYE probe for Day 2/Day 3 verification."""

from __future__ import annotations

import argparse
import json
import random
import re
import socket
import time
import uuid
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple


TO_TAG_RE = re.compile(r";\s*tag=([^;>\s]+)", re.IGNORECASE)


@dataclass
class CallResult:
    call_index: int
    success: bool
    invite_codes: List[int]
    bye_code: Optional[int]
    reason: str
    call_id: str
    bind_ip: str
    target: str


def _build_sdp(bind_ip: str, media_port: int) -> str:
    return (
        "v=0\r\n"
        f"o=- 0 0 IN IP4 {bind_ip}\r\n"
        "s=Talky Day Probe\r\n"
        f"c=IN IP4 {bind_ip}\r\n"
        "t=0 0\r\n"
        f"m=audio {media_port} RTP/AVP 0\r\n"
        "a=rtpmap:0 PCMU/8000\r\n"
        "a=ptime:20\r\n"
    )


def _build_request(
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
        "User-Agent: talky-day-probe/1.0",
    ]
    if body:
        lines.append("Content-Type: application/sdp")
    lines.append(f"Content-Length: {len(body.encode('utf-8'))}")
    message = "\r\n".join(lines) + "\r\n\r\n" + body
    return message.encode("utf-8")


def _parse_response(raw: bytes) -> Tuple[int, Dict[str, str], str]:
    text = raw.decode("utf-8", errors="ignore")
    lines = text.split("\r\n")
    first = lines[0] if lines else ""
    parts = first.split()
    if len(parts) < 2 or parts[0] != "SIP/2.0":
        raise ValueError(f"Not a SIP response: {first!r}")
    code = int(parts[1])
    headers: Dict[str, str] = {}
    for line in lines[1:]:
        if not line:
            break
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        headers[key.strip().lower()] = value.strip()
    return code, headers, text


def _extract_to_tag(to_header: str) -> Optional[str]:
    match = TO_TAG_RE.search(to_header)
    if not match:
        return None
    return match.group(1).strip()


def _recv_until(
    sock: socket.socket,
    timeout_s: float,
    expected_method: str,
    call_id: str,
) -> Tuple[List[int], Optional[Dict[str, str]]]:
    deadline = time.monotonic() + timeout_s
    invite_codes: List[int] = []
    final_headers: Optional[Dict[str, str]] = None
    while time.monotonic() < deadline:
        remaining = max(0.1, deadline - time.monotonic())
        sock.settimeout(remaining)
        data, _ = sock.recvfrom(65535)
        code, headers, _ = _parse_response(data)
        cseq_value = headers.get("cseq", "")
        cseq_parts = cseq_value.split()
        if len(cseq_parts) < 2:
            continue
        method = cseq_parts[1].upper()
        if method != expected_method.upper():
            continue
        if headers.get("call-id", "").strip() != call_id:
            continue
        invite_codes.append(code)
        if code >= 200:
            final_headers = headers
            break
    return invite_codes, final_headers


def _run_success_call(
    call_index: int,
    host: str,
    port: int,
    extension: str,
    bind_ip: str,
    timeout_s: float,
    hold_ms: int,
) -> CallResult:
    media_port = 40000 + ((call_index * 2) % 2000)
    from_user = f"probe{call_index}"
    contact_user = from_user
    from_tag = uuid.uuid4().hex[:8]
    call_id = f"{uuid.uuid4().hex}-{call_index}@talky.local"

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind((bind_ip, 0))
        local_ip, local_port = sock.getsockname()

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
            contact_user=contact_user,
            body=sdp,
        )
        sock.sendto(invite, (host, port))

        invite_codes, final_headers = _recv_until(
            sock=sock,
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
                reason="missing_to_tag_in_200",
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
            contact_user=contact_user,
            body="",
        )
        sock.sendto(ack, (host, port))
        time.sleep(max(0.05, hold_ms / 1000.0))

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
            contact_user=contact_user,
            body="",
        )
        sock.sendto(bye, (host, port))

        bye_codes, bye_headers = _recv_until(
            sock=sock,
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
                reason=reason,
                call_id=call_id,
                bind_ip=bind_ip,
                target=f"{host}:{port}",
            )

        return CallResult(
            call_index=call_index,
            success=True,
            invite_codes=invite_codes,
            bye_code=bye_code,
            reason="ok",
            call_id=call_id,
            bind_ip=bind_ip,
            target=f"{host}:{port}",
        )


def _run_blocked_call(
    call_index: int,
    host: str,
    port: int,
    extension: str,
    bind_ip: str,
    timeout_s: float,
) -> CallResult:
    media_port = 45000 + ((call_index * 2) % 2000)
    from_user = f"blocked{call_index}"
    from_tag = uuid.uuid4().hex[:8]
    call_id = f"{uuid.uuid4().hex}-{call_index}@talky.local"

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind((bind_ip, 0))
        local_ip, local_port = sock.getsockname()
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
        sock.sendto(invite, (host, port))
        invite_codes, final_headers = _recv_until(
            sock=sock,
            timeout_s=timeout_s,
            expected_method="INVITE",
            call_id=call_id,
        )
        if not final_headers:
            return CallResult(
                call_index=call_index,
                success=True,
                invite_codes=invite_codes,
                bye_code=None,
                reason="blocked_timeout_no_response",
                call_id=call_id,
                bind_ip=bind_ip,
                target=f"{host}:{port}",
            )

        final_code = invite_codes[-1]
        if final_code >= 300:
            return CallResult(
                call_index=call_index,
                success=True,
                invite_codes=invite_codes,
                bye_code=None,
                reason=f"blocked_with_{final_code}",
                call_id=call_id,
                bind_ip=bind_ip,
                target=f"{host}:{port}",
            )

        return CallResult(
            call_index=call_index,
            success=False,
            invite_codes=invite_codes,
            bye_code=None,
            reason=f"unexpected_final_{final_code}",
            call_id=call_id,
            bind_ip=bind_ip,
            target=f"{host}:{port}",
        )


def _detect_bind_ip() -> str:
    # Pick a non-loopback IPv4 if possible.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            sock.connect(("1.1.1.1", 80))
            return sock.getsockname()[0]
        except OSError:
            return "127.0.0.1"


def main() -> int:
    parser = argparse.ArgumentParser(description="SIP INVITE/ACK/BYE probe")
    parser.add_argument("--host", default="127.0.0.1", help="SIP target host")
    parser.add_argument("--port", type=int, default=5070, help="SIP target port")
    parser.add_argument("--extension", default="700", help="Target extension")
    parser.add_argument("--calls", type=int, default=10, help="Number of calls to execute")
    parser.add_argument("--bind-ip", default="127.0.0.1", help="Local source IP to bind")
    parser.add_argument("--timeout", type=float, default=4.0, help="Response timeout per call")
    parser.add_argument("--hold-ms", type=int, default=150, help="Hold before BYE (milliseconds)")
    parser.add_argument(
        "--expect-blocked",
        action="store_true",
        help="Expect direct INVITE calls to be blocked/rejected",
    )
    parser.add_argument("--evidence-file", default="", help="Path to write JSON evidence")
    parser.add_argument(
        "--auto-bind-ip",
        action="store_true",
        help="Auto-detect a non-loopback source IP and use it as bind IP",
    )
    args = parser.parse_args()

    bind_ip = _detect_bind_ip() if args.auto_bind_ip else args.bind_ip
    results: List[CallResult] = []
    run_start = time.time()

    for i in range(1, args.calls + 1):
        # Jitter small delay to avoid synchronized branch collisions.
        time.sleep(random.uniform(0.02, 0.08))
        if args.expect_blocked:
            result = _run_blocked_call(
                call_index=i,
                host=args.host,
                port=args.port,
                extension=args.extension,
                bind_ip=bind_ip,
                timeout_s=args.timeout,
            )
        else:
            result = _run_success_call(
                call_index=i,
                host=args.host,
                port=args.port,
                extension=args.extension,
                bind_ip=bind_ip,
                timeout_s=args.timeout,
                hold_ms=args.hold_ms,
            )
        results.append(result)
        status = "PASS" if result.success else "FAIL"
        print(
            f"[{status}] call={i} target={result.target} bind={result.bind_ip} "
            f"invite_codes={result.invite_codes} bye={result.bye_code} reason={result.reason}"
        )

    passed = sum(1 for r in results if r.success)
    payload = {
        "run_mode": "expect_blocked" if args.expect_blocked else "expect_success",
        "target": f"{args.host}:{args.port}",
        "bind_ip": bind_ip,
        "extension": args.extension,
        "calls_requested": args.calls,
        "calls_passed": passed,
        "calls_failed": args.calls - passed,
        "duration_seconds": round(time.time() - run_start, 3),
        "results": [asdict(r) for r in results],
    }

    if args.evidence_file:
        path = Path(args.evidence_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"[INFO] Wrote evidence: {path}")

    print(
        f"[SUMMARY] mode={payload['run_mode']} passed={passed}/{args.calls} "
        f"failed={payload['calls_failed']} target={payload['target']}"
    )
    return 0 if passed == args.calls else 2


if __name__ == "__main__":
    raise SystemExit(main())
