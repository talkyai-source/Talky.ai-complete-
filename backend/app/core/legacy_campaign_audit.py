"""Legacy-campaign audit (T2.6).

The persona-prompt sprint (2026-04-24) layered guardrails + persona
templates on top of campaign script_config. Campaigns that don't
have a `persona_type` set fall through to the legacy hardcoded
estimation prompt at `telephony_session_config.TELEPHONY_ESTIMATION_SYSTEM_PROMPT`.

Goal: remove the hardcoded fallback once every live campaign has
been migrated. Pre-T2.6 there was no visibility into how many
campaigns still relied on the fallback — operators would have had
to ad-hoc query the DB to know.

What we ship here:
- A pure-data audit function that returns the list of running /
  scheduled campaigns missing `persona_type`.
- A startup hook that runs the audit and logs a structured WARN
  when any are found in production.
- An auditor used by the `/health` endpoint to surface the count
  without exposing tenant/campaign IDs publicly.

What we deliberately DO NOT ship:
- Removal of the fallback. That's the next step once the audit
  reads zero on prod for a sustained window.
- Any forced migration. Operators decide when to flip.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class LegacyCampaignAuditResult:
    """Snapshot of campaigns still relying on the legacy hardcoded
    prompt. Lightweight — designed to fit in a /health response and
    in operator log lines.

    `unmigrated_ids` is intentionally a small sample, not the full
    list. Use the DB directly when you need the full set.
    """
    probed: bool
    total_active: int = 0
    missing_persona: int = 0
    sample_ids: list[str] = field(default_factory=list)
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "probed": self.probed,
            "total_active": self.total_active,
            "missing_persona": self.missing_persona,
            "sample_ids": list(self.sample_ids),
            "error": self.error,
        }

    @property
    def fully_migrated(self) -> bool:
        return self.probed and self.missing_persona == 0


_ACTIVE_STATUSES = ("running", "scheduled", "paused", "draft")


async def audit_legacy_campaigns(
    db_pool: Any,
    *,
    sample_size: int = 5,
) -> LegacyCampaignAuditResult:
    """Return the count + a sample of active campaigns that still
    fall through to the hardcoded estimation prompt.

    Tolerates DB failures by returning `probed=False` rather than
    raising — this is observability, not a safety check.
    """
    if db_pool is None:
        return LegacyCampaignAuditResult(probed=False, error="no_db_pool")

    try:
        async with db_pool.acquire() as conn:
            total = await conn.fetchval(
                "SELECT COUNT(*) FROM campaigns WHERE status = ANY($1::text[])",
                list(_ACTIVE_STATUSES),
            )
            # `script_config->>'persona_type' IS NULL` covers both rows
            # where script_config itself is NULL and rows where the key
            # is missing inside the JSONB.
            missing_rows = await conn.fetch(
                """
                SELECT id::text AS id
                FROM campaigns
                WHERE status = ANY($1::text[])
                  AND COALESCE(script_config->>'persona_type', '') = ''
                ORDER BY created_at DESC
                LIMIT $2
                """,
                list(_ACTIVE_STATUSES),
                sample_size,
            )
            missing_count = await conn.fetchval(
                """
                SELECT COUNT(*) FROM campaigns
                WHERE status = ANY($1::text[])
                  AND COALESCE(script_config->>'persona_type', '') = ''
                """,
                list(_ACTIVE_STATUSES),
            )
    except Exception as exc:
        logger.debug("legacy_campaign_audit_failed err=%s", exc)
        return LegacyCampaignAuditResult(probed=False, error=str(exc))

    sample = [str(r["id"]) for r in missing_rows or []]
    return LegacyCampaignAuditResult(
        probed=True,
        total_active=int(total or 0),
        missing_persona=int(missing_count or 0),
        sample_ids=sample,
    )


def log_audit_summary(result: LegacyCampaignAuditResult) -> None:
    """Emit a structured log line summarising the audit. Operators
    pick this up in their dashboards / Sentry breadcrumbs."""
    if not result.probed:
        logger.info("legacy_campaign_audit_skipped reason=%s", result.error or "unknown")
        return
    if result.fully_migrated:
        logger.info(
            "legacy_campaign_audit ok total_active=%d missing_persona=0",
            result.total_active,
        )
        return
    logger.warning(
        "legacy_campaign_audit_unmigrated total_active=%d missing_persona=%d "
        "sample=%s — these campaigns still use the legacy hardcoded prompt; "
        "set script_config.persona_type to migrate. See "
        "backend/docs/scrutiny/2026-04-25-tier-0-blockers.md.",
        result.total_active,
        result.missing_persona,
        ",".join(result.sample_ids[:5]) or "-",
    )
