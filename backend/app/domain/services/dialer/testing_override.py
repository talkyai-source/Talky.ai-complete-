"""Explicit, opt-in TESTING override for the calling-schedule gate.

⚠️  COMPLIANCE — READ BEFORE CHANGING ANYTHING IN THIS FILE  ⚠️
--------------------------------------------------------------
Calling-hour and calling-day restrictions are not a UX preference. They exist
to satisfy telemarketing law — UK Ofcom's persistent-misuse / permitted-hours
guidance, and TCPA-style 8am-9pm local-time rules in the US. Dialling a
consumer outside the permitted window is a regulatory violation, not a bug.

Therefore:

  * The schedule checks in ``CallingRules.is_within_time_window`` and
    ``SchedulingRuleEngine.can_make_call`` are NOT removed, weakened, or
    widened by this module. They still run and still produce their normal
    structured block reason.
  * The default calling window is NOT changed for anybody. This override is
    OFF unless an operator deliberately switches it on.
  * When it IS on, it is LOUD: every permitted call logs at WARNING, the
    override is surfaced to the user through the same reason channel that
    shows blocking reasons ("TESTING MODE: schedule bypassed"), and the
    origination is stamped as overridden on the call's own audit trail
    (``call_legs.metadata`` + a ``schedule_override`` ``call_events`` row) so
    it is provable after the fact which calls went out under it.
  * It is reversible in one step: unset the env var / flip the campaign flag
    and the next dial is gated normally again. No data migration, no restart
    semantics beyond re-reading the environment.

Two independent switches, both default-OFF
------------------------------------------
1. ``DIALER_TESTING_IGNORE_SCHEDULE=true`` (environment) — a deployment-wide
   operator switch. Intended for a staging box or a short, supervised
   production test window. Because it is deployment-wide, prefer #2.
2. ``campaigns.calling_config.testing_mode_ignore_schedule = true`` — scoped
   to a single campaign. Preferred on merit: it limits the blast radius to the
   one campaign being tested.

   ⚠️ Caveat, pre-existing and deliberately NOT changed here: the campaign
   update endpoint rebuilds ``calling_config`` from the submitted schedule
   (``campaigns._calling_config_from_schedule``), so editing a campaign's
   calling hours through the UI REPLACES the whole blob and drops any key it
   doesn't know about — this flag, and equally ``batch_size``,
   ``call_gap_seconds`` and ``trunk``. Until that merge is fixed, set this key
   directly on the row, and re-check it after any schedule edit. The env
   switch (#1) is unaffected by that behaviour.

Not to be confused with ``calling_config.ignore_schedule``
---------------------------------------------------------
That pre-existing key is a *product* feature ("call anytime") the client
chooses in the UI; it silently skips the window gate and is untouched here.
The testing override is deliberately separate so that "someone is testing
outside hours right now" is distinguishable — in logs, in the UI, and in the
per-call audit trail — from "this client configured a 24/7 campaign".
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)

#: Deployment-wide switch. Absent/unset ⇒ OFF.
TESTING_OVERRIDE_ENV = "DIALER_TESTING_IGNORE_SCHEDULE"

#: Per-campaign switch inside ``campaigns.calling_config``. Absent ⇒ OFF.
TESTING_OVERRIDE_CFG_KEY = "testing_mode_ignore_schedule"

#: Values that count as "on". Anything else — including "", "0", "false",
#: "no", None, or a typo — is OFF. Fail-safe by construction: an ambiguous
#: value must never widen a compliance gate.
_TRUTHY = frozenset({"1", "true", "yes", "on"})

#: Source labels returned by :func:`schedule_override_source`.
SOURCE_ENV = "env:" + TESTING_OVERRIDE_ENV
SOURCE_CAMPAIGN = "campaign:" + TESTING_OVERRIDE_CFG_KEY


def _truthy(value: Any) -> bool:
    if value is True:
        return True
    if value is None or value is False:
        return False
    return str(value).strip().lower() in _TRUTHY


def env_override_enabled() -> bool:
    """True only when the deployment-wide testing switch is explicitly on."""
    return _truthy(os.getenv(TESTING_OVERRIDE_ENV))


def campaign_override_enabled(calling_config: Optional[dict]) -> bool:
    """True only when this campaign explicitly opted into the testing override."""
    if not isinstance(calling_config, dict):
        return False
    return _truthy(calling_config.get(TESTING_OVERRIDE_CFG_KEY))


def schedule_override_source(calling_config: Optional[dict]) -> Optional[str]:
    """Which switch (if any) is bypassing the schedule gate right now.

    Returns ``None`` when the override is OFF — which is the default and the
    only state a normal deployment should ever be in. The per-campaign switch
    is reported first because it is the narrower, preferred one.
    """
    if campaign_override_enabled(calling_config):
        return SOURCE_CAMPAIGN
    if env_override_enabled():
        return SOURCE_ENV
    return None


def log_override_used(
    *,
    source: str,
    campaign_id: Optional[str],
    tenant_id: Optional[str],
    phone_number: Optional[str],
    blocked_reason: Optional[str],
    schedule: Optional[str] = None,
) -> None:
    """Emit the mandatory WARNING for one call permitted by the override.

    Logged on EVERY permitted call, deliberately un-throttled: an override of
    a compliance gate must leave one log line per affected call so an audit
    can count them.
    """
    logger.warning(
        "TESTING MODE: schedule bypassed — dialling OUTSIDE the configured "
        "calling window. source=%s campaign=%s tenant=%s dest=%s "
        "would_have_blocked_with=%s schedule=%s. Calling-hour rules exist for "
        "legal/compliance reasons (Ofcom/TCPA); turn this override off before "
        "real calling (%s / calling_config.%s).",
        source, campaign_id, tenant_id, phone_number, blocked_reason,
        schedule or "unknown", TESTING_OVERRIDE_ENV, TESTING_OVERRIDE_CFG_KEY,
    )


def override_audit_payload(
    *, source: str, blocked_reason: Optional[str], schedule: Optional[str] = None,
) -> dict[str, Any]:
    """The blob stamped onto a call that was placed under the override, so it
    is auditable afterwards ("which calls went out outside hours, and why")."""
    return {
        "schedule_override": True,
        "schedule_override_source": source,
        "schedule_override_blocked_reason": blocked_reason,
        "schedule_override_schedule": schedule,
    }
