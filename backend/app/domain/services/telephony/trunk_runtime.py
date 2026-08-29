"""Shared, fail-closed SIP trunk runtime-readiness contract.

``tenant_sip_trunks.is_active`` records operator intent.  It is not proof
that Asterisk loaded the endpoint or that a registration is healthy.  The
15-second trunk-status updater writes that independent runtime evidence and
all inbound activation/admission paths evaluate it through this module.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Optional


DEFAULT_FRESHNESS_SECONDS = 60
_READY_REGISTERED = {"registered"}
_READY_IP_AUTH = {"loaded", "registered"}


@dataclass(frozen=True)
class TrunkRuntimeReadiness:
    ready: bool
    code: str
    detail: str


def trunk_status_freshness_seconds() -> int:
    """Maximum age of Asterisk evidence accepted by call admission.

    The updater normally runs every 15 seconds.  Values below 30 seconds are
    unsafe under ordinary timer jitter; values above five minutes hide a dead
    updater for too long, so configuration is bounded deliberately.
    """
    raw = os.getenv(
        "TELEPHONY_TRUNK_STATUS_FRESHNESS_SECONDS",
        str(DEFAULT_FRESHNESS_SECONDS),
    )
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_FRESHNESS_SECONDS
    return min(max(value, 30), 300)


def _metadata(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _aware_utc(value: Any) -> Optional[datetime]:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def evaluate_trunk_runtime(
    trunk: Mapping[str, Any],
    *,
    now: Optional[datetime] = None,
    freshness_seconds: Optional[int] = None,
    require_inbound: bool = True,
) -> TrunkRuntimeReadiness:
    """Return whether a trunk has fresh, type-appropriate Asterisk proof.

    Registration trunks must be ``registered``.  IP-auth trunks do not have a
    registration object, so the updater proves their namespaced endpoint is
    actually loaded and reports ``loaded``.  ``registered`` is also accepted
    for the hand-managed platform-default endpoint.
    """
    if not bool(trunk.get("trunk_active", trunk.get("is_active"))):
        return TrunkRuntimeReadiness(False, "inactive", "SIP trunk is disabled.")

    direction = str(trunk.get("trunk_direction", trunk.get("direction")) or "").lower()
    if require_inbound and direction not in {"inbound", "both"}:
        return TrunkRuntimeReadiness(
            False,
            "outbound_only",
            "SIP trunk is not configured to accept inbound traffic.",
        )

    checked_at = _aware_utc(
        trunk.get("trunk_live_status_checked_at", trunk.get("live_status_checked_at"))
    )
    if checked_at is None:
        return TrunkRuntimeReadiness(
            False,
            "status_missing",
            "Asterisk has not reported runtime status for this trunk yet.",
        )

    current = _aware_utc(now) or datetime.now(timezone.utc)
    age_seconds = (current - checked_at).total_seconds()
    limit = freshness_seconds or trunk_status_freshness_seconds()
    if age_seconds < -5:
        return TrunkRuntimeReadiness(
            False,
            "status_clock_skew",
            "Asterisk status timestamp is in the future; check host clocks.",
        )
    if age_seconds > limit:
        return TrunkRuntimeReadiness(
            False,
            "status_stale",
            f"Asterisk runtime status is stale ({int(age_seconds)} seconds old).",
        )

    status = str(
        trunk.get("trunk_live_registration_status", trunk.get("live_registration_status"))
        or "unknown"
    ).strip().lower()
    register = bool(_metadata(trunk.get("trunk_metadata", trunk.get("metadata"))).get("register"))
    allowed = _READY_REGISTERED if register else _READY_IP_AUTH
    if status not in allowed:
        reason = str(
            trunk.get("trunk_live_status_detail", trunk.get("live_status_detail")) or ""
        ).strip()
        suffix = f" ({reason})" if reason else ""
        expectation = "registered" if register else "loaded in Asterisk"
        return TrunkRuntimeReadiness(
            False,
            f"runtime_{status}",
            f"SIP trunk is not {expectation}; live status is {status}{suffix}.",
        )

    return TrunkRuntimeReadiness(
        True,
        "ready",
        "Asterisk reports a healthy registration."
        if status == "registered"
        else "Asterisk reports the inbound endpoint is loaded.",
    )
