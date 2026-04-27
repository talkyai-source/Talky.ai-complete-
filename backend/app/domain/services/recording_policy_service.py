"""Per-tenant recording-consent policy (T0.4).

Consulted by RecordingService before creating a recording, and by the
call origination path to decide whether a pre-answer announcement must
play. Keeps the compliance decision in one place so the audio pipeline
stays simple.

The DB row lives in `tenant_recording_policy`. If no row exists for a
tenant, we fall back to a SAFE default (two-party consent, announcement
required) — never silently record.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)


# Callers can treat this as an enum in practice.
CONSENT_ONE_PARTY = "one_party"
CONSENT_TWO_PARTY = "two_party"
CONSENT_DISABLED = "disabled"


@dataclass
class RecordingDecision:
    """Outcome of a recording-policy check.

    `should_record` is the gate — if False the RecordingService skips
    the whole upload pipeline. `announcement_required` is advisory:
    the origination path plays the announcement before pipeline start
    when set.
    """
    should_record: bool
    announcement_required: bool
    announcement_text: Optional[str]
    opt_out_dtmf_digit: Optional[str]
    retention_days: int
    reason: str  # machine-readable; logged for audit


# Safe default when the tenant has no row yet — opt-in, two-party,
# announce. Better to over-protect than under-protect on a first boot.
_SAFE_DEFAULT = RecordingDecision(
    should_record=True,
    announcement_required=True,
    announcement_text=(
        "This call may be recorded for quality and training purposes. "
        "Press 9 at any time to opt out of recording."
    ),
    opt_out_dtmf_digit="9",
    retention_days=90,
    reason="tenant_default_two_party",
)


class RecordingPolicyService:
    """Thin wrapper around `tenant_recording_policy`."""

    def __init__(self, db_pool: Any):
        self._db_pool = db_pool

    async def decide(
        self,
        *,
        tenant_id: str,
        destination_country_code: Optional[str] = None,
    ) -> RecordingDecision:
        """Return the recording decision for a specific outbound call.

        `destination_country_code` is an ISO-3166 alpha-2 (e.g. "GB", "DE")
        or the more specific "US-CA" / "US-MA" form used by the default
        two-party list for US states. Pass None if you don't know — we
        default to two-party in that case.
        """
        row = await self._load_row(tenant_id)
        if row is None:
            logger.info(
                "recording_policy_default_applied tenant=%s — no explicit "
                "policy row, using safe two-party default",
                tenant_id,
            )
            return _SAFE_DEFAULT

        mode = row["default_consent_mode"]
        retention_days = int(row["retention_days"])
        announcement_text = row["announcement_text"]
        dtmf = row["opt_out_dtmf_digit"]
        two_party_codes = set(row["two_party_country_codes"] or [])

        if mode == CONSENT_DISABLED:
            return RecordingDecision(
                should_record=False,
                announcement_required=False,
                announcement_text=None,
                opt_out_dtmf_digit=None,
                retention_days=retention_days,
                reason="tenant_policy_disabled",
            )

        if mode == CONSENT_ONE_PARTY:
            # Record without announcement — legal only in one-party-consent
            # jurisdictions. The tenant is asserting responsibility.
            return RecordingDecision(
                should_record=True,
                announcement_required=False,
                announcement_text=None,
                opt_out_dtmf_digit=None,
                retention_days=retention_days,
                reason="tenant_policy_one_party",
            )

        # two_party — announcement required iff destination is in the
        # list, OR the list is empty (safe default = announce everywhere).
        needs_announcement = (
            not two_party_codes
            or _country_matches_any(destination_country_code, two_party_codes)
        )
        return RecordingDecision(
            should_record=True,
            announcement_required=needs_announcement,
            announcement_text=announcement_text if needs_announcement else None,
            opt_out_dtmf_digit=dtmf if needs_announcement else None,
            retention_days=retention_days,
            reason=(
                "tenant_policy_two_party_announce"
                if needs_announcement
                else "tenant_policy_two_party_no_announce"
            ),
        )

    # ──────────────────────────────────────────────────────────────────

    async def _load_row(self, tenant_id: str) -> Optional[dict]:
        try:
            async with self._db_pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM tenant_recording_policy WHERE tenant_id = $1",
                    tenant_id,
                )
        except Exception as exc:
            logger.error(
                "recording_policy_lookup_failed tenant=%s err=%s — applying safe default",
                tenant_id, exc,
            )
            return None
        return dict(row) if row else None


def _country_matches_any(cc: Optional[str], codes: set[str]) -> bool:
    """Match either a raw alpha-2 ("DE") or a subdivision ("US-CA")
    against the configured list. An empty callee country defaults to
    match — we don't know enough to skip the announcement safely."""
    if not cc:
        return True
    cc = cc.upper()
    if cc in codes:
        return True
    # If we got a subdivision like "US-CA", also match the plain "US"
    # entry. If we got a country like "US" and the list has a more
    # specific "US-CA", we conservatively return False (won't announce);
    # the caller will typically pass the subdivision when it matters.
    if "-" in cc and cc.split("-", 1)[0] in codes:
        return True
    return False
