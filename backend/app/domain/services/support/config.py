"""Support settings read from the environment on each call.

Every number is bounded; an invalid value keeps the safe default and is
logged, so a typo can neither disable a limit nor open the feature wider. A
malformed pilot allowlist turns support off for everyone rather than becoming
"empty" (which would mean every tenant).
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import timedelta
from uuid import UUID

logger = logging.getLogger(__name__)

BOT_TIMEOUT_SECONDS = 20.0
BOT_MAX_TOOL_CALLS = 3
BOT_MAX_OUTPUT_TOKENS = 600

_SLA_DEFAULT_MINUTES = {
    "urgent": (60, 8 * 60),
    "high": (4 * 60, 24 * 60),
    "normal": (24 * 60, 72 * 60),
    "low": (72 * 60, 7 * 24 * 60),
}


def _flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        value = None
    if value is None or not minimum <= value <= maximum:
        logger.warning("support_setting_invalid name=%s using_default=%s", name, default)
        return default
    return value


def _allowlist() -> tuple[frozenset[str], bool]:
    """(tenant ids, valid). Any malformed entry invalidates the whole list."""
    raw = os.getenv("SUPPORT_TENANT_ALLOWLIST", "")
    tenants: set[str] = set()
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        try:
            tenants.add(str(UUID(entry)))
        except ValueError:
            logger.error("support_allowlist_invalid entries_rejected=1 support_disabled=true")
            return frozenset(), False
    return frozenset(tenants), True


def _bot_model() -> str:
    from app.infrastructure.assistant.model_config import normalize_model

    return normalize_model((os.getenv("SUPPORT_BOT_MODEL") or "").strip() or None)


@dataclass(frozen=True)
class SupportSettings:
    enabled: bool
    bot_enabled: bool
    allowlist: frozenset[str]
    allowlist_valid: bool
    bot_model: str
    rate_limit_per_minute: int
    daily_message_cap_per_tenant: int
    max_turns: int
    attachment_max_bytes: int
    transcript_retention_days: int
    staff_alert_email: str | None
    app_base_url: str
    sla: dict[str, tuple[timedelta, timedelta]] = field(default_factory=dict)

    def enabled_for_tenant(self, tenant_id: str | None) -> bool:
        if not self.enabled or not self.allowlist_valid or not tenant_id:
            return False
        if not self.allowlist:
            return True
        try:
            return str(UUID(str(tenant_id))) in self.allowlist
        except ValueError:
            return False

    def ticket_url(self, ticket_id: object) -> str:
        return f"{self.app_base_url}/support/tickets/{ticket_id}"


def load_settings() -> SupportSettings:
    allowlist, valid = _allowlist()
    sla: dict[str, tuple[timedelta, timedelta]] = {}
    for priority, (first_default, resolution_default) in _SLA_DEFAULT_MINUTES.items():
        key = priority.upper()
        first = _bounded_int(f"SUPPORT_SLA_{key}_FIRST_RESPONSE_MINUTES", first_default, 5, 60 * 24 * 30)
        resolution = _bounded_int(f"SUPPORT_SLA_{key}_RESOLUTION_MINUTES", resolution_default, 5, 60 * 24 * 90)
        sla[priority] = (timedelta(minutes=first), timedelta(minutes=max(first, resolution)))
    base_url = (os.getenv("FRONTEND_URL") or "http://localhost:3000").strip().rstrip("/")
    return SupportSettings(
        enabled=_flag("SUPPORT_ENABLED", False),
        bot_enabled=_flag("SUPPORT_BOT_ENABLED", True),
        allowlist=allowlist,
        allowlist_valid=valid,
        bot_model=_bot_model(),
        rate_limit_per_minute=_bounded_int("SUPPORT_RATE_LIMIT_PER_MINUTE", 10, 1, 600),
        daily_message_cap_per_tenant=_bounded_int("SUPPORT_DAILY_MESSAGE_CAP_PER_TENANT", 300, 1, 100_000),
        max_turns=_bounded_int("SUPPORT_MAX_TURNS", 20, 1, 200),
        attachment_max_bytes=_bounded_int("SUPPORT_ATTACHMENT_MAX_MB", 10, 1, 50) * 1024 * 1024,
        transcript_retention_days=_bounded_int("SUPPORT_TRANSCRIPT_RETENTION_DAYS", 180, 1, 3650),
        staff_alert_email=(os.getenv("SUPPORT_STAFF_ALERT_EMAIL") or "").strip() or None,
        app_base_url=base_url,
        sla=sla,
    )


def message_rate_limit() -> str:
    """SlowAPI limit string for bot messages, per signed-in user."""
    return f"{load_settings().rate_limit_per_minute}/minute"
