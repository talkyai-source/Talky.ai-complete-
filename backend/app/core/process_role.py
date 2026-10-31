"""Which role this API process plays (core-infra review 2026-10-07).

Production runs two copies of the same FastAPI app:

* the CALL process (talky-api, 127.0.0.1:8000) owns Asterisk (ARI) and every
  live call's in-process state; the voice gateway's audio callbacks, the
  dialer's originations and every call-control route are sent to it;
* the DASHBOARD process (talky-api-web, 127.0.0.1:8001, TELEPHONY_ENABLED=false)
  serves everything else, so dashboard load or a dashboard restart can never
  stall a live call.

The Redis owner lock alone is not enough to keep the dashboard process off
the phones: the lock goes to whichever process starts first, so after a
reboot the dashboard process could win it and the gateway's callbacks (sent to
:8000) would reach a process with no calls. This switch makes the dashboard
process never claim ownership, never connect ARI and never run orphan recovery.
"""
from __future__ import annotations

import os

_FALSE = {"false", "0", "no", "off"}


def telephony_enabled() -> bool:
    """True unless TELEPHONY_ENABLED is explicitly false (the default keeps
    every existing deployment exactly as it was)."""
    return os.getenv("TELEPHONY_ENABLED", "true").strip().lower() not in _FALSE
