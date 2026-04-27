"""Do-Not-Call list service (T2.1).

Wraps the `dnc_entries` table (created by migration
20260402_add_callguard_tables.sql) with a small domain service so
admin endpoints and the in-call opt-out path share one source of
truth.

The CallGuard check already reads this table — so adding a row here
immediately blocks all future origination to that number.

Design
------
- **Numbers are stored in E.164 after normalisation.** Whatever the
  UI sends, we normalise once before insert — guards against the
  obvious case of "+1 415-555-1234" vs "+14155551234".
- **Source taxonomy:** `caller_opt_out`, `manual_admin`,
  `ftc_national`, `regulator_complaint`, `bulk_import`. Free-text
  in the DB but constants listed here so call sites stay
  consistent.
- **Global entries (tenant_id=NULL) apply cross-tenant** (e.g. the
  FTC list). The CallGuard query already matches both tenant-scoped
  AND global rows.
- **Expiry supported** via `expires_at` so time-bounded complaints
  can be honoured without staying on the list forever. `NULL` means
  "permanent".
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

logger = logging.getLogger(__name__)


# Source taxonomy — use these constants at every call site so admin
# filtering / analytics can trust the values.
SOURCE_CALLER_OPT_OUT = "caller_opt_out"
SOURCE_MANUAL_ADMIN = "manual_admin"
SOURCE_FTC_NATIONAL = "ftc_national"
SOURCE_REGULATOR_COMPLAINT = "regulator_complaint"
SOURCE_BULK_IMPORT = "bulk_import"

KNOWN_SOURCES = {
    SOURCE_CALLER_OPT_OUT,
    SOURCE_MANUAL_ADMIN,
    SOURCE_FTC_NATIONAL,
    SOURCE_REGULATOR_COMPLAINT,
    SOURCE_BULK_IMPORT,
}


@dataclass
class DNCEntry:
    id: str
    tenant_id: Optional[str]
    normalized_number: str
    source: str
    reason: Optional[str]
    expires_at: Optional[datetime]
    created_at: Optional[datetime]


def normalize_e164(raw: str) -> str:
    """Strip cosmetic characters and return in E.164 form. Uses
    libphonenumber when available (installed for T1.5); falls back
    to digit-only stripping otherwise.
    """
    if not raw:
        return ""
    text = raw.strip()
    try:
        import phonenumbers
        parsed = phonenumbers.parse(text, None)
        if phonenumbers.is_valid_number(parsed):
            return phonenumbers.format_number(
                parsed, phonenumbers.PhoneNumberFormat.E164,
            )
    except Exception:
        pass
    # Fallback: strip everything except digits and a leading +.
    cleaned = "".join(c for c in text if c.isdigit() or c == "+")
    if cleaned and not cleaned.startswith("+"):
        cleaned = "+" + cleaned
    return cleaned


class DNCService:
    """Thin DB wrapper. One instance per request is fine — asyncpg
    handles the pooling."""

    def __init__(self, db_pool: Any):
        self._db = db_pool

    # ──────────────────────────────────────────────────────────────────
    # Write path
    # ──────────────────────────────────────────────────────────────────

    async def add(
        self,
        *,
        tenant_id: Optional[str],
        e164: str,
        source: str,
        reason: Optional[str] = None,
        expires_at: Optional[datetime] = None,
        added_by: Optional[str] = None,
    ) -> DNCEntry:
        """Add or replace a DNC entry. Idempotent — same number + same
        tenant just refreshes the row (updated_at changes)."""
        normalized = normalize_e164(e164)
        if not normalized:
            raise ValueError("DNC entry requires a valid phone number")
        if source not in KNOWN_SOURCES:
            logger.info("dnc_unknown_source source=%s — accepted but not taxonomised", source)

        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO dnc_entries
                    (tenant_id, normalized_number, source, reason, added_by, expires_at)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT DO NOTHING
                RETURNING id, tenant_id, normalized_number, source, reason,
                          expires_at, created_at
                """,
                tenant_id,
                normalized,
                source,
                reason,
                added_by,
                expires_at,
            )
            if row is None:
                # Already existed — refresh and return the existing row.
                row = await conn.fetchrow(
                    """
                    UPDATE dnc_entries
                    SET updated_at = NOW(),
                        reason = COALESCE($4, reason),
                        expires_at = COALESCE($5, expires_at)
                    WHERE (tenant_id = $1 OR (tenant_id IS NULL AND $1 IS NULL))
                      AND normalized_number = $2
                      AND source = $3
                    RETURNING id, tenant_id, normalized_number, source, reason,
                              expires_at, created_at
                    """,
                    tenant_id,
                    normalized,
                    source,
                    reason,
                    expires_at,
                )
        if row is None:
            raise RuntimeError("DNC insert+refresh returned nothing — DB state inconsistent")
        return _row_to_entry(row)

    async def add_caller_opt_out(
        self,
        *,
        tenant_id: str,
        e164: str,
        call_id: Optional[str] = None,
    ) -> DNCEntry:
        """Shortcut for the in-call opt-out path. Used by the voice
        pipeline when it detects a stop request (DTMF opt-out, STOP
        keyword, etc). Permanent by default — the caller asked us not
        to call again."""
        reason = f"Caller opted out during call_id={call_id}" if call_id else "Caller opt-out"
        return await self.add(
            tenant_id=tenant_id,
            e164=e164,
            source=SOURCE_CALLER_OPT_OUT,
            reason=reason,
        )

    async def bulk_import(
        self,
        *,
        tenant_id: Optional[str],
        numbers: list[str],
        source: str,
        reason: Optional[str] = None,
    ) -> dict:
        """Insert many at once. Returns a per-row result dict so the
        caller can show "accepted / skipped / invalid" counts."""
        if source not in KNOWN_SOURCES:
            logger.info("dnc_bulk_unknown_source source=%s", source)
        accepted: list[str] = []
        skipped: list[str] = []
        invalid: list[str] = []

        async with self._db.acquire() as conn:
            for raw in numbers:
                normalized = normalize_e164(raw)
                if not normalized:
                    invalid.append(raw)
                    continue
                try:
                    await conn.execute(
                        """
                        INSERT INTO dnc_entries
                            (tenant_id, normalized_number, source, reason)
                        VALUES ($1, $2, $3, $4)
                        ON CONFLICT DO NOTHING
                        """,
                        tenant_id,
                        normalized,
                        source,
                        reason,
                    )
                    accepted.append(normalized)
                except Exception as exc:
                    logger.warning(
                        "dnc_bulk_import_row_failed number=%s err=%s",
                        normalized, exc,
                    )
                    skipped.append(normalized)
        return {
            "accepted_count": len(accepted),
            "skipped_count": len(skipped),
            "invalid_count": len(invalid),
            "accepted": accepted,
            "invalid": invalid,
        }

    async def remove(self, *, tenant_id: str, entry_id: str) -> bool:
        """Delete an entry owned by this tenant. Returns True if a row
        was actually deleted (absent rows return False — idempotent)."""
        async with self._db.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM dnc_entries WHERE tenant_id = $1 AND id = $2",
                tenant_id,
                entry_id,
            )
        return result.endswith(" 1") if isinstance(result, str) else False

    # ──────────────────────────────────────────────────────────────────
    # Read path
    # ──────────────────────────────────────────────────────────────────

    async def list_for_tenant(
        self,
        tenant_id: str,
        *,
        include_global: bool = False,
        limit: int = 200,
    ) -> list[DNCEntry]:
        sql = """
            SELECT id, tenant_id, normalized_number, source, reason,
                   expires_at, created_at
            FROM dnc_entries
            WHERE (tenant_id = $1 {global_clause})
              AND (expires_at IS NULL OR expires_at > NOW())
            ORDER BY created_at DESC
            LIMIT $2
        """.format(
            global_clause="OR tenant_id IS NULL" if include_global else "",
        )
        async with self._db.acquire() as conn:
            rows = await conn.fetch(sql, tenant_id, limit)
        return [_row_to_entry(r) for r in rows]

    async def is_on_dnc(
        self,
        *,
        tenant_id: Optional[str],
        e164: str,
    ) -> bool:
        """Single-number check. Used by CallGuard; shape-compatible
        with the existing _check_dnc query but exposed as a service
        method for callers outside the guard (analytics, UI."""
        normalized = normalize_e164(e164)
        if not normalized:
            return False
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT 1 FROM dnc_entries
                WHERE (tenant_id = $1 OR tenant_id IS NULL)
                  AND normalized_number = $2
                  AND (expires_at IS NULL OR expires_at > NOW())
                LIMIT 1
                """,
                tenant_id,
                normalized,
            )
        return row is not None


def _row_to_entry(row: Any) -> DNCEntry:
    return DNCEntry(
        id=str(row["id"]),
        tenant_id=str(row["tenant_id"]) if row.get("tenant_id") else None,
        normalized_number=row["normalized_number"],
        source=row["source"],
        reason=row.get("reason"),
        expires_at=row.get("expires_at"),
        created_at=row.get("created_at"),
    )
