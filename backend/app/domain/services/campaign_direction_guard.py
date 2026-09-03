"""Typed boundary error for database-enforced campaign direction races."""

from __future__ import annotations

from typing import Any


CAMPAIGN_DIRECTION_LOCK_KEY_SQL = (
    "hashtextextended('talky:campaign-direction:' || $1::uuid::text, 0)"
)
CAMPAIGN_DIRECTION_LOCK_SQL = (
    f"SELECT pg_advisory_xact_lock({CAMPAIGN_DIRECTION_LOCK_KEY_SQL})"
)


async def acquire_campaign_direction_lock(conn: Any, campaign_id: str) -> None:
    """Serialize direction-sensitive work for one campaign.

    The caller must already be inside a transaction.  Advisory locks are used
    instead of a row lock because knowledge ingestion writes the campaign from
    a second pooled connection; a row lock here would deadlock against our own
    write.  Every direction-changing path must take this same lock.
    """

    # Canonicalise inside PostgreSQL.  UUID comparisons accept alternate text
    # spellings (notably upper-case hex); hashing the raw argument would let an
    # application request and the row trigger lock different advisory keys for
    # the same campaign.
    await conn.execute(CAMPAIGN_DIRECTION_LOCK_SQL, str(campaign_id))


class OutboundCampaignDirectionConflict(RuntimeError):
    """The campaign stopped being outbound before a dependent write landed."""

    def __init__(self, campaign_id: str, constraint: str):
        self.campaign_id = str(campaign_id)
        self.constraint = constraint
        super().__init__(
            f"campaign {self.campaign_id} changed direction before the write completed"
        )


def outbound_direction_guard_constraint(error: Any) -> str | None:
    """Return the named database guard without exposing the raw DB error."""

    message = str(error or "")
    for token in message.replace('"', " ").replace("'", " ").split():
        cleaned = token.strip("()[]{}:;,.")
        if cleaned.endswith("_outbound_campaign_guard"):
            return cleaned
    return None


def raise_for_outbound_direction_guard(error: Any, campaign_id: str) -> None:
    constraint = outbound_direction_guard_constraint(error)
    if constraint:
        raise OutboundCampaignDirectionConflict(campaign_id, constraint)
