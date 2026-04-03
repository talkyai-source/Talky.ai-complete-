#!/usr/bin/env python3
"""Simple SIP OPTIONS probe for WS-A verification."""

from __future__ import annotations

import argparse
import socket
import sys
import uuid


def build_options(host: str, port: int, from_user: str = "probe") -> bytes:
    branch = f"z9hG4bK-{uuid.uuid4().hex[:12]}"
    call_id = f"{uuid.uuid4().hex}@{host}"
    tag = uuid.uuid4().hex[:8]
    msg = (
        f"OPTIONS sip:{host}:{port} SIP/2.0\r\n"
        f"Via: SIP/2.0/UDP 127.0.0.1;branch={branch};rport\r\n"
        f"From: <sip:{from_user}@localhost>;tag={tag}\r\n"
        f"To: <sip:{host}:{port}>\r\n"
        f"Call-ID: {call_id}\r\n"
        f"CSeq: 1 OPTIONS\r\n"
        f"Contact: <sip:{from_user}@127.0.0.1>\r\n"
        "Max-Forwards: 70\r\n"
        "Content-Length: 0\r\n"
        "\r\n"
    )
    return msg.encode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Send SIP OPTIONS and verify response.")
    parser.add_argument("--host", default="127.0.0.1", help="SIP host")
    parser.add_argument("--port", type=int, default=15060, help="SIP port")
    parser.add_argument("--timeout", type=float, default=2.5, help="Socket timeout (seconds)")
    args = parser.parse_args()

    payload = build_options(args.host, args.port)

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(args.timeout)
        sock.sendto(payload, (args.host, args.port))
        try:
            data, addr = sock.recvfrom(4096)
        except TimeoutError:
            print("FAIL: SIP OPTIONS probe timed out", file=sys.stderr)
            return 2

    response = data.decode("utf-8", errors="ignore")
    first_line = response.splitlines()[0] if response.splitlines() else ""

    if not first_line.startswith("SIP/2.0"):
        print(f"FAIL: Non-SIP response from {addr}: {first_line!r}", file=sys.stderr)
        return 3

    print(f"PASS: SIP response from {addr}: {first_line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
