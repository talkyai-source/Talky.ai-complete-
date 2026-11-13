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

import json
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
        # A stored empty day list (saved before D2-4 refused it) is unset, not
        # "no day": the account's days apply.
        if val not in (None, "", []):
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


def _json_dict(value) -> Optional[dict]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    return value if isinstance(value, dict) else None


def describe_schedule(
    tenant_calling_rules,
    calling_config,
    own_timezone_counts: Optional[dict] = None,
) -> dict:
    """The schedule the dialer will enforce for one campaign, for display.

    D2-1/2/3. Built from the dialer's own steps (account rules parsed as
    ``_get_tenant_rules`` does, then :func:`effective_rules`,
    :func:`schedule_ignored` and the lead-timezone precedence), so it cannot
    drift from the gate. ``own_timezone_counts`` maps the campaign's stored
    ``leads.timezone`` values to contact counts; None means not known.

    The switches are read from this process's environment; the API and the
    dialer load the same ``/opt/talky/backend/.env``.
    """
    from app.domain.services.dialer.lead_timezone import (
        normalize_lead_timezone,
        per_lead_timezone_enabled,
    )

    account = _json_dict(tenant_calling_rules) or {}
    tenant_rules, configured = CallingRules.default(), False
    if account:
        try:
            tenant_rules, configured = CallingRules.from_dict(account), True
        except Exception:
            pass  # the dialer logs and uses the defaults too
    config = _json_dict(calling_config)
    rules = effective_rules(tenant_rules, config)
    overlaid = rules is not tenant_rules

    sources = {}
    for key in _OVERLAY_KEYS:
        if overlaid and (config or {}).get(key) not in (None, "", []):
            sources[key] = "campaign"
        elif configured and account.get(key) not in (None, "", []):
            sources[key] = "account"
        else:
            sources[key] = "platform_default"

    own: dict = {}
    for zone, contacts in (own_timezone_counts or {}).items():
        name = normalize_lead_timezone(zone)
        if name:
            own[name] = own.get(name, 0) + int(contacts or 0)
    ranked = sorted(own.items(), key=lambda item: (-item[1], item[0]))

    return {
        **{key: getattr(rules, key) for key in _OVERLAY_KEYS},
        "sources": sources,
        "account_rules": {
            "configured": configured,
            **{key: getattr(tenant_rules, key) for key in _OVERLAY_KEYS},
        },
        "ignore_schedule_requested": bool((config or {}).get(_IGNORE_KEY, False)),
        "ignore_schedule_allowed": _ignore_schedule_allowed(),
        "ignore_schedule": schedule_ignored(config),
        "contact_timezones": {
            # leads.timezone first, then (behind DIALER_PER_LEAD_TIMEZONE) a
            # zone derived from the number, then the timezone above.
            "contact_zone_first": True,
            "phone_derived_zone": per_lead_timezone_enabled(),
            "contacts_with_own_timezone": (
                sum(own.values()) if own_timezone_counts is not None else None),
            "own_timezones": [{"timezone": z, "contacts": n} for z, n in ranked[:10]],
        },
    }
