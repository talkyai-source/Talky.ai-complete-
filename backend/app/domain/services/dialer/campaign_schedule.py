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

Pure logic, no DB — the worker passes in the already-loaded config dict.
"""
from __future__ import annotations

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


def schedule_ignored(calling_config: Optional[dict]) -> bool:
    """True when the client chose to dial regardless of the window."""
    if not calling_config:
        return False
    return bool(calling_config.get(_IGNORE_KEY, False))
