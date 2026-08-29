#!/usr/bin/env python3
"""Re-normalise every ``dnc_entries.normalized_number`` to the canonical form.

    python scripts/backfill_dnc_normalized_numbers.py                 # dry run
    python scripts/backfill_dnc_normalized_numbers.py --apply         # write
    python scripts/backfill_dnc_normalized_numbers.py --tenant <uuid> # one tenant
    python scripts/backfill_dnc_normalized_numbers.py --apply --delete-duplicates

WHY THIS EXISTS
---------------
``dnc_entries.normalized_number`` is a LOOKUP key: CallGuard normalises the
number it is about to dial and compares it to this column byte-for-byte. The
write path used to normalise with ``normalize_e164_libphonenumber`` (region
``None``) while the guard read with ``normalize_e164_digits``. For a bare
10-digit US number the two disagree --

    "(415) 555-1234"  stored as  +4155551234
                      looked up as  +14155551234

-- so the row matched nothing and a number the customer had put on Do-Not-Call
stayed dialable. The code is fixed (both sides now use ``normalize_e164``);
rows written before the fix are still wrong and this script repairs them.

WHAT IT WILL AND WILL NOT DO
----------------------------
* It **never guesses**. A value that cannot be turned into strict E.164
  (``+``, ``+00``, a UK national ``+07700900123``) is reported as
  ``unrepairable`` and left exactly as it is, for a human to decide.
* Re-normalising a damaged value cannot on its own recover a dropped country
  code -- ``normalize_e164_digits('+4155551234')`` is ``'+4155551234'``. So
  when the table carries the raw input (``phone_number``) that column wins;
  otherwise the ``+1`` repair fires ONLY when the stored value is invalid
  E.164 *and* prefixing ``1`` makes it a valid number. That pair of conditions
  is what stops a real +31/+41 number being mangled into a US one.
* ``(tenant_id, normalized_number)`` is unique. Where the repair makes two
  rows collide the OLDER row keeps/gets the canonical value and the newer one
  is reported as ``duplicate`` and left untouched -- unless you pass
  ``--delete-duplicates``, which removes it.
* It is idempotent. A second run reports every row as ``unchanged``.

Dry run is the default and prints a per-tenant summary plus every row it
would touch. Nothing is written without ``--apply``.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable, Optional

# The one canonical DNC normalisation -- imported, never re-implemented, so
# this script cannot drift away from the code it is repairing after.
sys.path.insert(0, os.getcwd())
from app.domain.services.phone_number_normalizer import (  # noqa: E402
    is_strict_e164,
    normalize_e164_digits,
)

ACTION_UNCHANGED = "unchanged"
ACTION_UPDATE = "update"
ACTION_UNREPAIRABLE = "unrepairable"
ACTION_DUPLICATE = "duplicate"


# ---------------------------------------------------------------------------
# The pure half -- unit-tested in tests/unit/test_backfill_dnc_normalized_numbers.py
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RowPlan:
    """What the backfill intends to do with one ``dnc_entries`` row."""

    row_id: str
    tenant_id: Optional[str]
    old: str
    new: Optional[str]
    action: str
    duplicate_of: Optional[str] = None

    @property
    def changes(self) -> bool:
        return self.action == ACTION_UPDATE


def _is_valid_number(value: str) -> Optional[bool]:
    """libphonenumber's verdict on an E.164 string, or None if unavailable."""
    try:
        import phonenumbers
    except Exception:  # noqa: BLE001 - the fallback below is deliberate
        return None
    try:
        return bool(phonenumbers.is_valid_number(phonenumbers.parse(value, None)))
    except Exception:  # noqa: BLE001 - unparseable is simply "not valid"
        return False


def _looks_like_a_nanp_number(digits: str) -> bool:
    """NANP shape, used only when libphonenumber is not importable.

    Area code and exchange both start 2-9 -- the rule the +1 repair leans on.
    """
    return (
        len(digits) == 10
        and digits[0] in "23456789"
        and digits[3] in "23456789"
    )


def _resolve(value: Optional[str]) -> Optional[str]:
    """Canonical E.164 for one candidate string, or None if we'd be guessing."""
    if not value:
        return None
    candidate = normalize_e164_digits(value)
    well_formed = is_strict_e164(candidate)

    # Already a real, complete number -- nothing to do.
    if well_formed and _is_valid_number(candidate) is True:
        return candidate

    # The ONE repair this script performs. The historical bug dropped the US
    # country code, leaving '+' + 10 digits which is not a valid number on its
    # own but becomes one the moment the '1' is restored. Both halves of that
    # condition matter: a genuine 10-digit-after-'+' number (say a Dutch
    # +31 6...) is *valid* already and never reaches here.
    digits = candidate.lstrip("+")
    if len(digits) == 10:
        repaired = "+1" + digits
        if is_strict_e164(repaired):
            verdict = _is_valid_number(repaired)
            if verdict is True:
                return repaired
            # phonenumbers unavailable -- fall back to the NANP shape rule.
            if verdict is None and _looks_like_a_nanp_number(digits):
                return repaired

    # Well-formed but libphonenumber doesn't recognise it (test ranges, new
    # allocations, an extension glued on). Not ours to "fix" -- keep it.
    if well_formed:
        return candidate
    return None


def canonical_dnc_number(stored: str, raw: Optional[str] = None) -> Optional[str]:
    """The value this row's ``normalized_number`` SHOULD hold.

    Returns ``None`` when the row cannot be repaired without guessing -- the
    caller reports it rather than writing something invented.

    ``raw`` is the row's untouched source column (``phone_number``) if the
    table has one; it is tried FIRST because it still carries the country
    information the damaged value lost. Re-normalising the damaged value alone
    cannot recover it: ``normalize_e164_digits('+4155551234')`` is just
    ``'+4155551234'`` again.
    """
    return _resolve(raw) or _resolve(stored)


def plan_rows(rows: Iterable[dict]) -> list[RowPlan]:
    """Decide an action for every row, resolving unique-constraint collisions.

    Rows are dicts with ``id``, ``tenant_id``, ``normalized_number``,
    ``created_at`` and optionally ``phone_number``.
    """
    rows = list(rows)

    # Oldest first, so the first row to claim a (tenant, number) key is the
    # one we keep. `created_at` may be NULL on very old rows; those sort last
    # but keep a stable order by id.
    def _sort_key(row: dict):
        created = row.get("created_at")
        return (created is None, created, str(row.get("id")))

    claimed: dict[tuple[Optional[str], str], str] = {}
    plans: dict[str, RowPlan] = {}

    for row in sorted(rows, key=_sort_key):
        row_id = str(row.get("id"))
        tenant_id = row.get("tenant_id")
        tenant_key = str(tenant_id) if tenant_id is not None else None
        old = row.get("normalized_number") or ""
        raw = row.get("phone_number")

        new = canonical_dnc_number(old, raw=raw)
        if new is None:
            plans[row_id] = RowPlan(
                row_id, tenant_id, old, None, ACTION_UNREPAIRABLE,
            )
            continue

        key = (tenant_key, new)
        owner = claimed.get(key)
        if owner is not None:
            plans[row_id] = RowPlan(
                row_id, tenant_id, old, new, ACTION_DUPLICATE, duplicate_of=owner,
            )
            continue

        claimed[key] = row_id
        plans[row_id] = RowPlan(
            row_id,
            tenant_id,
            old,
            new,
            ACTION_UNCHANGED if new == old else ACTION_UPDATE,
        )

    # Preserve the caller's row order in the returned plan.
    return [plans[str(row.get("id"))] for row in rows]


def summarise(plans: Iterable[RowPlan]) -> dict:
    """Counts overall and per tenant, so the dry run is readable."""
    plans = list(plans)
    by_tenant: dict[Optional[str], dict] = defaultdict(
        lambda: {
            ACTION_UNCHANGED: 0,
            ACTION_UPDATE: 0,
            ACTION_UNREPAIRABLE: 0,
            ACTION_DUPLICATE: 0,
        }
    )
    out = {
        "total": len(plans),
        ACTION_UNCHANGED: 0,
        ACTION_UPDATE: 0,
        ACTION_UNREPAIRABLE: 0,
        ACTION_DUPLICATE: 0,
        "by_tenant": by_tenant,
    }
    for plan in plans:
        out[plan.action] += 1
        tenant_key = str(plan.tenant_id) if plan.tenant_id is not None else "GLOBAL"
        by_tenant[tenant_key][plan.action] += 1
    return out


# ---------------------------------------------------------------------------
# The database half
# ---------------------------------------------------------------------------


def dsn() -> str:
    """DATABASE_URL first so this can be pointed at a scratch database; the
    app settings are the fallback for running on the server."""
    raw = os.environ.get("DATABASE_URL")
    if not raw:
        try:
            from app.core.config import get_settings

            raw = get_settings().database_url
        except Exception:  # noqa: BLE001
            raw = None
    if not raw:
        sys.exit(
            "DATABASE_URL is required. Try: set -a && . ./.env && set +a"
        )
    return raw.replace("postgresql+asyncpg", "postgresql")


async def _has_column(conn: Any, table: str, column: str) -> bool:
    return bool(
        await conn.fetchval(
            """
            SELECT 1 FROM information_schema.columns
            WHERE table_name = $1 AND column_name = $2
            """,
            table,
            column,
        )
    )


def _print_rows(plans: list[RowPlan], action: str, header: str, limit: int) -> None:
    selected = [p for p in plans if p.action == action]
    if not selected:
        return
    print(f"\n{header} ({len(selected)}):")
    for plan in selected[:limit]:
        tenant = plan.tenant_id or "GLOBAL"
        if action == ACTION_DUPLICATE:
            print(
                f"  tenant={tenant} id={plan.row_id} {plan.old!r} -> {plan.new!r} "
                f"COLLIDES with older row {plan.duplicate_of}"
            )
        elif action == ACTION_UNREPAIRABLE:
            print(f"  tenant={tenant} id={plan.row_id} {plan.old!r} — not E.164, left alone")
        else:
            print(f"  tenant={tenant} id={plan.row_id} {plan.old!r} -> {plan.new!r}")
    if len(selected) > limit:
        print(f"  ... and {len(selected) - limit} more")


async def run(args) -> int:
    import asyncpg

    conn = await asyncpg.connect(dsn(), timeout=15)
    try:
        has_raw = await _has_column(conn, "dnc_entries", "phone_number")
        columns = "id, tenant_id, normalized_number, created_at"
        if has_raw:
            columns += ", phone_number"

        sql = f"SELECT {columns} FROM dnc_entries"
        params: list = []
        if args.tenant:
            sql += " WHERE tenant_id = $1::uuid"
            params.append(args.tenant)
        rows = [dict(r) for r in await conn.fetch(sql, *params)]

        print(f"dnc_entries rows read: {len(rows)}")
        print(f"raw source column 'phone_number' present: {has_raw}")
        if not has_raw:
            print(
                "  (no raw column — a damaged value is repaired only when the\n"
                "   dropped-US-country-code rule applies; everything else that\n"
                "   is not E.164 is reported, not rewritten)"
            )

        plans = plan_rows(rows)
        summary = summarise(plans)

        print("\n=== SUMMARY ===")
        print(f"  total        {summary['total']}")
        print(f"  unchanged    {summary[ACTION_UNCHANGED]}")
        print(f"  to update    {summary[ACTION_UPDATE]}")
        print(f"  duplicates   {summary[ACTION_DUPLICATE]}  (older row kept)")
        print(f"  unrepairable {summary[ACTION_UNREPAIRABLE]}")

        print("\n=== PER TENANT ===")
        for tenant_key in sorted(summary["by_tenant"], key=str):
            counts = summary["by_tenant"][tenant_key]
            print(
                f"  {tenant_key}: update={counts[ACTION_UPDATE]} "
                f"unchanged={counts[ACTION_UNCHANGED]} "
                f"duplicate={counts[ACTION_DUPLICATE]} "
                f"unrepairable={counts[ACTION_UNREPAIRABLE]}"
            )

        _print_rows(plans, ACTION_UPDATE, "ROWS TO UPDATE", args.limit)
        _print_rows(plans, ACTION_DUPLICATE, "DUPLICATE ROWS", args.limit)
        _print_rows(plans, ACTION_UNREPAIRABLE, "UNREPAIRABLE ROWS", args.limit)

        if not args.apply:
            print("\nDRY RUN — nothing written. Re-run with --apply to write.")
            return 0

        updates = [p for p in plans if p.action == ACTION_UPDATE]
        duplicates = [p for p in plans if p.action == ACTION_DUPLICATE]

        written = 0
        deleted = 0
        async with conn.transaction():
            for plan in updates:
                await conn.execute(
                    "UPDATE dnc_entries SET normalized_number = $2 WHERE id = $1",
                    plan.row_id if not _is_uuid_text(plan.row_id) else _as_uuid(plan.row_id),
                    plan.new,
                )
                written += 1
            if args.delete_duplicates:
                for plan in duplicates:
                    await conn.execute(
                        "DELETE FROM dnc_entries WHERE id = $1",
                        plan.row_id if not _is_uuid_text(plan.row_id) else _as_uuid(plan.row_id),
                    )
                    deleted += 1

        print(f"\nAPPLIED — {written} rows updated, {deleted} duplicate rows deleted.")
        if duplicates and not args.delete_duplicates:
            print(
                f"  {len(duplicates)} duplicate rows were LEFT AS THEY WERE. They still\n"
                "  hold a value that matches no dial. Re-run with --delete-duplicates\n"
                "  once you have reviewed the list above."
            )
        return 0
    finally:
        await conn.close()


def _is_uuid_text(value: str) -> bool:
    import uuid as _uuid

    try:
        _uuid.UUID(str(value))
        return True
    except Exception:  # noqa: BLE001
        return False


def _as_uuid(value: str):
    import uuid as _uuid

    return _uuid.UUID(str(value))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Re-normalise dnc_entries.normalized_number (dry run by default).",
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Actually write. Without this the script only reports.",
    )
    parser.add_argument(
        "--tenant", default=None,
        help="Restrict to one tenant_id (UUID).",
    )
    parser.add_argument(
        "--delete-duplicates", action="store_true",
        help="With --apply, delete the newer row of a colliding pair "
             "instead of leaving it in place.",
    )
    parser.add_argument(
        "--limit", type=int, default=50,
        help="How many rows of each category to print (default 50).",
    )
    args = parser.parse_args()
    if args.delete_duplicates and not args.apply:
        print("--delete-duplicates has no effect without --apply (dry run).")
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
