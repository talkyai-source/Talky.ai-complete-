"""Drain live calls before the call service is stopped (systemd ExecStop).

Runs while the service still listens, so caller audio keeps flowing. Asks the
process to stop taking new calls, then waits until no call is committed or
DRAIN_TIMEOUT_S passes. Always exits 0: a failed drain must not block a stop.
Standard library only.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request


def _ready_body(base: str):
    """GET the ready route. It answers 503 while draining, body included."""
    req = urllib.request.Request(f"{base}/api/v1/healthz/ready")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return json.loads(exc.read().decode("utf-8"))


def main(argv=None, *, poll_interval_s: float = 2.0) -> int:
    base = os.getenv("TALKY_CALL_API_URL", "http://127.0.0.1:8000").rstrip("/")
    token = os.getenv("INTERNAL_SERVICE_TOKEN", "").strip()
    try:
        timeout_s = float(os.getenv("DRAIN_TIMEOUT_S", "300"))
    except ValueError:
        timeout_s = 300.0

    if not token:
        print("drain skipped: INTERNAL_SERVICE_TOKEN not set")
        return 0
    try:
        req = urllib.request.Request(
            f"{base}/api/v1/sip/telephony/drain",
            data=b"",
            method="POST",
            headers={"X-Internal-Service-Token": token},
        )
        with urllib.request.urlopen(req, timeout=10):
            pass
    except urllib.error.HTTPError as exc:
        print(f"drain skipped: drain request answered HTTP {exc.code}")
        return 0
    except Exception as exc:
        print(f"drain skipped: drain request failed ({type(exc).__name__})")
        return 0

    deadline = time.monotonic() + timeout_s
    while True:
        try:
            body = _ready_body(base)
            live = body.get("committed_calls")
            if live is None:
                live = body.get("active_sessions")
            live = int(live or 0)
        except Exception as exc:
            print(f"drain ended: service stopped answering ({type(exc).__name__})")
            return 0
        print(f"drain: {live} call(s) still live", flush=True)
        if live <= 0:
            return 0
        if time.monotonic() >= deadline:
            print(f"drain ended: timeout with {live} call(s) still live")
            return 0
        time.sleep(poll_interval_s)


if __name__ == "__main__":
    sys.exit(main())
