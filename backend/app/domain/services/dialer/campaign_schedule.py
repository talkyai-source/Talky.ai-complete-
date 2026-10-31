"""Per-campaign calling schedule overlay (Phase 3c-v2).

The client now owns a campaign's calling hours + timezone, stored in
``campaigns.calling_config``. At dial time the worker overlays whatever
the campaign set on top of the tenant's default ``CallingRules`` and asks
two questions:

  * :func:`effective_rules` — the rules to actually evaluate the window
    with (campaign timezone/window/days win; anything the campaign left
    blank falls back to the tenant default).
  * :func:`schedule_ignored` — did the client flip the override ("call
    anytime")? When true the worker skips the window gate entirely so the
    client is never blocked — the UI still shows the out-of-hours warning.

Pure logic, no DB — the worker passes in the already-loaded config dict. The
one exception is the platform kill switch below, read from the environment.

Platform kill switch ``DIALER_ALLOW_IGNORE_SCHEDULE`` (default ``true``): the
"call anytime" override is tenant-settable, so a tenant can dial around the
clock. Set it to ``false`` (also ``0`` / ``no`` / ``off``) in the dialer's
environment and :func:`schedule_ignored` returns False for every campaign, so
the calling window is enforced again whatever the campaign stored. It is read
on every call, so a systemd ``Environment=`` change needs only a restart.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from app.domain.models.calling_rules import CallingRules

# Keys the campaign may override on the tenant CallingRules.
_OVERLAY_KEYS = ("timezone", "time_window_start", "time_window_end", "allowed_days")
_IGNORE_KEY = "ignore_schedule"


def effective_rules(tenant_rules: CallingRules, calling_config: Optional[dict]) -> CallingRules:
    """Tenant rules with the campaign's schedule fields overlaid.

    Only keys the campaign actually set are applied; everything else keeps
    the tenant value. Invalid overlay values fall back by being ignored at
    validation time (CallingRules construction). Returns the tenant rules
    unchanged when there's no campaign config.
    """
    if not calling_config:
        return tenant_rules
    data = tenant_rules.model_dump()
    for key in _OVERLAY_KEYS:
        val = calling_config.get(key)
        if val not in (None, ""):
            data[key] = val
    try:
        return CallingRules(**data)
    except Exception:
        # A malformed override must never break dialing — fall back to the
        # tenant rules rather than raising in the hot path.
        return tenant_rules


_SWITCH_OFF = {"0", "false", "no", "off"}
_SWITCH_ON = {"1", "true", "yes", "on"}
_warned_switch_values: set = set()


def _ignore_schedule_allowed() -> bool:
    """Platform kill switch: False when ``DIALER_ALLOW_IGNORE_SCHEDULE`` is off.

    An unrecognised value keeps today's behaviour (allowed) but is logged once,
    so a typo such as ``flase`` is visible in the dialer log.
    """
    value = os.getenv("DIALER_ALLOW_IGNORE_SCHEDULE", "true").strip().lower()
    if value not in _SWITCH_OFF | _SWITCH_ON and value not in _warned_switch_values:
        _warned_switch_values.add(value)
        logging.getLogger(__name__).warning(
            "DIALER_ALLOW_IGNORE_SCHEDULE=%r is not a recognised value; "
            "'call anytime' stays allowed (use false/0/no/off to disable it)",
            value,
        )
    return value not in _SWITCH_OFF


def schedule_ignored(calling_config: Optional[dict]) -> bool:
    """True when the client chose to dial regardless of the window — and the
    platform still allows that (``DIALER_ALLOW_IGNORE_SCHEDULE``)."""
    if not calling_config:
        return False
    if not _ignore_schedule_allowed():
        return False
    return bool(calling_config.get(_IGNORE_KEY, False))
