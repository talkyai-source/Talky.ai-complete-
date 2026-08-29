"""Minute top-ups (goals.md §9). Credit exactly once, only after payment.

THE ONE INVARIANT
-----------------
A verified successful payment credits its minutes exactly once. Everything in
this module exists to hold that under the conditions that actually occur:

  * Stripe REDELIVERS webhooks. A delivery that times out is redelivered by
    contract, not by accident. Two deliveries of one event must credit once.
  * Two deliveries can arrive CONCURRENTLY, so the guard cannot be
    "SELECT then INSERT" — it has to be a uniqueness constraint the database
    enforces. That is ``billing_ledger.provider_event_id``, and it is why the
    credit path treats a conflict as a no-op rather than an error.
  * A checkout that is created is not a checkout that is paid. Minutes are
    credited on the PAID event only.

EVERY WRITE HERE GOES THROUGH acquire_with_tenant
--------------------------------------------------
``topup_orders`` and ``billing_ledger`` carry FORCE row-level security. A bare
``pool.acquire()`` has no tenant GUC set, so the policy evaluates to NULL, the
row is invisible, and an UPDATE touches nothing — and **a zero-row UPDATE is a
success in PostgreSQL**. It returns no error. That exact shape silently dropped
call transcripts on this codebase before, so the webhook paths take the bypass
connection explicitly (a webhook has no user session to derive a tenant from —
the tenant is read *from the order*) and the tenant-facing reads take the
tenant-scoped one.

UNLIMITED TENANTS ARE SKIPPED, NOT TOPPED UP
---------------------------------------------
``minutes_allocated <= 0`` is the existing sentinel for unlimited. Adding 500 to
0 would turn an uncapped account into one capped at 500 — a top-up that leaves
the customer worse off than before they paid. Those tenants get a ledger entry
for the money and no change to the allocation, and the mismatch is logged loudly
because it means something was sold that should not have been sellable.
"""
from __future__ import annotations

import logging
from typing import Optional

from app.core.db_utils import acquire_with_tenant

logger = logging.getLogger(__name__)

TERMINAL_FAILURE_STATES = {"failed", "cancelled"}

# A tenant clicking Buy repeatedly creates a pending row each time. That is
# harmless until a script does it in a loop, so the open-order count is capped.
MAX_OPEN_ORDERS = 20


class TopupError(RuntimeError):
    """Refused before any money or minutes moved."""


def _rows_affected(tag: str) -> int:
    """asyncpg returns 'UPDATE 3'. Zero is the case worth catching."""
    try:
        return int(str(tag).strip().rsplit(" ", 1)[-1])
    except (ValueError, IndexError):
        return -1


class TopupService:
    def __init__(self, pool) -> None:
        self._pool = pool

    # ── catalogue ───────────────────────────────────────────────────────────

    async def packages(self) -> list[dict]:
        """The approved list. A client never names a price or a minute count —
        it names a package CODE and everything else is read from here."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT code, name, minutes, price_cents, currency, expires_days
                  FROM topup_packages
                 WHERE is_active
                 ORDER BY sort_order, minutes
                """
            )
        return [dict(r) for r in rows]

    async def _package(self, conn, code: str) -> dict:
        row = await conn.fetchrow(
            "SELECT code, name, minutes, price_cents, currency FROM topup_packages "
            " WHERE code = $1 AND is_active",
            code,
        )
        if not row:
            raise TopupError(f"Unknown or inactive top-up package {code!r}")
        return dict(row)

    # ── order ───────────────────────────────────────────────────────────────

    async def create_order(
        self, *, tenant_id: str, user_id: Optional[str], package_code: str,
    ) -> dict:
        """Record the intent to buy, BEFORE sending anyone to a payment page.

        The order exists first so the webhook has something to match against. A
        payment arriving for an order we never created is a signal worth having,
        not a row to invent on the fly.

        Price and minutes are SNAPSHOTTED here. If the package is repriced next
        month, this customer is still owed exactly what they were quoted.
        """
        async with acquire_with_tenant(self._pool, str(tenant_id)) as conn:
            pkg = await self._package(conn, package_code)

            open_orders = await conn.fetchval(
                "SELECT count(*) FROM topup_orders "
                " WHERE tenant_id = $1::uuid AND status = 'pending' "
                "   AND created_at > NOW() - INTERVAL '1 hour'",
                str(tenant_id),
            )
            if (open_orders or 0) >= MAX_OPEN_ORDERS:
                raise TopupError(
                    "Too many top-up checkouts started without completing one. "
                    "Finish or cancel the open one before starting another."
                )

            row = await conn.fetchrow(
                """
                INSERT INTO topup_orders
                    (tenant_id, user_id, package_code, minutes, price_cents,
                     currency, status)
                VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, 'pending')
                RETURNING id, package_code, minutes, price_cents, currency, status
                """,
                str(tenant_id), str(user_id) if user_id else None,
                pkg["code"], pkg["minutes"], pkg["price_cents"], pkg["currency"],
            )
        out = dict(row)
        out["name"] = pkg["name"]
        logger.info(
            "topup_order_created tenant=%s order=%s package=%s minutes=%d",
            str(tenant_id)[:8], str(row["id"])[:8], package_code, pkg["minutes"],
        )
        return out

    async def attach_session(self, order_id: str, session_id: str) -> None:
        """Link the order to the provider's checkout session.

        If this write is lost the order can never be matched to its payment, so
        a zero-row result raises rather than passing quietly.
        """
        async with acquire_with_tenant(self._pool, None) as conn:
            tag = await conn.execute(
                "UPDATE topup_orders SET provider_session_id = $2, updated_at = NOW() "
                " WHERE id = $1::uuid",
                str(order_id), session_id,
            )
        if _rows_affected(tag) == 0:
            raise TopupError(
                f"Order {order_id} vanished before its checkout session could be "
                "attached — the payment could not have been matched to it."
            )

    # ── credit ──────────────────────────────────────────────────────────────

    async def credit_paid_order(
        self, *, session_id: str, event_id: str,
        payment_id: Optional[str] = None,
    ) -> bool:
        """Credit minutes for a paid checkout session. True if minutes moved.

        IDEMPOTENT BY CONSTRAINT, NOT BY CHECK. The ledger insert carries the
        provider's event id under a UNIQUE index, so a redelivered webhook
        collides in the database. That holds even when two deliveries are
        processed at the same instant, which a SELECT-then-INSERT would not.

        The whole thing is ONE transaction: order status, ledger row and the
        allocation move together or not at all. A crash between them would
        otherwise leave minutes credited with nothing recording why, or a
        record with no minutes.
        """
        async with acquire_with_tenant(self._pool, None) as conn:
            order = await conn.fetchrow(
                "SELECT id, tenant_id, minutes, price_cents, currency, status "
                "  FROM topup_orders WHERE provider_session_id = $1 FOR UPDATE",
                session_id,
            )
            if not order:
                logger.warning(
                    "topup_webhook_unknown_session session=%s event=%s — a payment "
                    "arrived for an order we never created",
                    session_id[:16], event_id[:24],
                )
                return False

            if order["status"] == "paid":
                logger.info(
                    "topup_already_paid order=%s event=%s — redelivery, no-op",
                    str(order["id"])[:8], event_id[:24],
                )
                return False

            ledger = await conn.fetchrow(
                """
                INSERT INTO billing_ledger
                    (tenant_id, order_id, kind, minutes_delta, amount_cents,
                     currency, provider_event_id, note)
                VALUES ($1, $2, 'topup', $3, $4, $5, $6, $7)
                ON CONFLICT (provider_event_id) DO NOTHING
                RETURNING id
                """,
                order["tenant_id"], order["id"], order["minutes"],
                order["price_cents"], order["currency"], event_id,
                "top-up {} minutes".format(order["minutes"]),
            )
            if ledger is None:
                # The constraint refused it: this exact event already credited.
                # Not an error — this is the guard doing its job.
                logger.info(
                    "topup_duplicate_event event=%s order=%s — already credited, "
                    "no minutes added",
                    event_id[:24], str(order["id"])[:8],
                )
                return False

            allocated = await conn.fetchval(
                "SELECT minutes_allocated FROM tenants WHERE id = $1 FOR UPDATE",
                order["tenant_id"],
            )
            if allocated is not None and allocated > 0:
                await conn.execute(
                    "UPDATE tenants SET minutes_allocated = minutes_allocated + $2 "
                    " WHERE id = $1",
                    order["tenant_id"], order["minutes"],
                )
            else:
                # <= 0 means UNLIMITED. Adding to it would CAP an uncapped
                # account — a top-up that leaves the customer worse off.
                logger.warning(
                    "topup_on_unlimited_tenant tenant=%s order=%s minutes=%d — "
                    "money recorded, allocation untouched. This tenant should not "
                    "have been offered a top-up.",
                    str(order["tenant_id"])[:8], str(order["id"])[:8],
                    order["minutes"],
                )

            # THE CEILING THAT ACTUALLY BLOCKS THE CALL.
            #
            # `tenants.minutes_allocated` (above) is what the dialer gate and
            # every screen read. The call guard's minutes check reads a
            # DIFFERENT column in a DIFFERENT table —
            # `tenant_call_limits.monthly_minutes_allocated`, read by
            # ``call_guard._check_minutes_quota`` — and nothing syncs the two. A
            # tenant with an admin-set ceiling could buy 250 minutes, watch the
            # balance rise everywhere, and still be refused at origination
            # against the old ceiling.
            #
            # `> 0` guard: 0 there means "no quota configured" and the guard
            # passes. Writing 250 into it would CREATE a cap for a tenant who
            # had none — the same trap as the unlimited sentinel above. So this
            # only ever RAISES a ceiling that already exists, and it runs in
            # this transaction so a crash cannot leave minutes bought that
            # cannot be dialled.
            await conn.execute(
                "UPDATE tenant_call_limits "
                "   SET monthly_minutes_allocated = monthly_minutes_allocated + $2, "
                "       updated_at = NOW() "
                " WHERE tenant_id = $1 AND monthly_minutes_allocated > 0",
                order["tenant_id"], order["minutes"],
            )

            await conn.execute(
                "UPDATE topup_orders SET status='paid', paid_at=NOW(), updated_at=NOW(), "
                "       provider_payment_id=COALESCE($2, provider_payment_id) "
                " WHERE id = $1",
                order["id"], payment_id,
            )

        logger.info(
            "topup_credited tenant=%s order=%s minutes=%d event=%s",
            str(order["tenant_id"])[:8], str(order["id"])[:8],
            order["minutes"], event_id[:24],
        )
        return True

    async def mark_failed(self, *, session_id: str, status: str) -> None:
        """A failed or expired checkout. NO ledger entry and NO minutes —
        nothing happened financially, so nothing is recorded."""
        if status not in TERMINAL_FAILURE_STATES:
            raise TopupError(f"{status!r} is not a failure state")
        async with acquire_with_tenant(self._pool, None) as conn:
            await conn.execute(
                "UPDATE topup_orders SET status=$2, updated_at=NOW() "
                " WHERE provider_session_id = $1 AND status = 'pending'",
                session_id, status,
            )
        logger.info("topup_order_%s session=%s", status, session_id[:16])

    async def reverse(
        self, *, event_id: str, kind: str = "refund",
        payment_id: Optional[str] = None, session_id: Optional[str] = None,
    ) -> bool:
        """Claw minutes back after a refund or a chargeback.

        Matched on the PAYMENT id, because that is what ``charge.refunded`` and
        ``charge.dispute.created`` carry — a refund event has no checkout
        session on it.

        A NEW NEGATIVE ROW, never an edit to the original. The original entry is
        what the customer was charged; rewriting it to represent a refund
        destroys the only evidence of what actually happened.

        The allocation is floored at zero: a tenant who has already SPENT the
        minutes must not be pushed negative, because ``<= 0`` reads as UNLIMITED
        and a chargeback would hand them free calls.
        """
        if kind not in ("refund", "dispute"):
            raise TopupError(f"{kind!r} is not a reversal")
        if not payment_id and not session_id:
            raise TopupError("reverse() needs a payment id or a session id")

        async with acquire_with_tenant(self._pool, None) as conn:
            order = await conn.fetchrow(
                "SELECT id, tenant_id, minutes, price_cents, currency, status "
                "  FROM topup_orders "
                " WHERE ($1::text IS NOT NULL AND provider_payment_id = $1) "
                "    OR ($2::text IS NOT NULL AND provider_session_id = $2) "
                " FOR UPDATE",
                payment_id, session_id,
            )
            if not order or order["status"] != "paid":
                # Most refunds on this account are subscription refunds, which
                # have no top-up order. Nothing to reverse is the normal case.
                logger.info(
                    "topup_reverse_skipped ref=%s kind=%s — no paid top-up order",
                    (payment_id or session_id or "")[:16], kind,
                )
                return False

            entry = await conn.fetchrow(
                """
                INSERT INTO billing_ledger
                    (tenant_id, order_id, kind, minutes_delta, amount_cents,
                     currency, provider_event_id, note)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                ON CONFLICT (provider_event_id) DO NOTHING
                RETURNING id
                """,
                order["tenant_id"], order["id"], kind, -order["minutes"],
                -order["price_cents"], order["currency"], event_id,
                "{}: reversed {} minutes".format(kind, order["minutes"]),
            )
            if entry is None:
                return False

            await conn.execute(
                "UPDATE tenants "
                "   SET minutes_allocated = GREATEST(0, minutes_allocated - $2) "
                " WHERE id = $1 AND minutes_allocated > 0",
                order["tenant_id"], order["minutes"],
            )
            # The enforced ceiling moves back with it. Leaving it raised after a
            # refund hands the tenant minutes they no longer own and lets the
            # two tables drift apart again. Floored for the same reason as
            # above, and scoped to a ceiling that is actually configured.
            await conn.execute(
                "UPDATE tenant_call_limits "
                "   SET monthly_minutes_allocated = "
                "           GREATEST(0, monthly_minutes_allocated - $2), "
                "       updated_at = NOW() "
                " WHERE tenant_id = $1 AND monthly_minutes_allocated > 0",
                order["tenant_id"], order["minutes"],
            )
            await conn.execute(
                "UPDATE topup_orders SET status=$2, updated_at=NOW() WHERE id=$1",
                order["id"], "refunded" if kind == "refund" else "disputed",
            )
        logger.info(
            "topup_reversed tenant=%s order=%s kind=%s minutes=-%d",
            str(order["tenant_id"])[:8], str(order["id"])[:8], kind, order["minutes"],
        )
        return True

    # ── read ────────────────────────────────────────────────────────────────

    async def history(self, tenant_id: str, limit: int = 25) -> list[dict]:
        async with acquire_with_tenant(self._pool, str(tenant_id)) as conn:
            rows = await conn.fetch(
                """
                SELECT o.id, o.package_code, o.minutes, o.price_cents, o.currency,
                       o.status, o.created_at, o.paid_at
                  FROM topup_orders o
                 WHERE o.tenant_id = $1::uuid
                 ORDER BY o.created_at DESC
                 LIMIT $2
                """,
                str(tenant_id), limit,
            )
        return [dict(r) for r in rows]

    async def ledger(self, tenant_id: str, limit: int = 100) -> list[dict]:
        """The reconciliation view §9 asks for — every movement, signed."""
        async with acquire_with_tenant(self._pool, str(tenant_id)) as conn:
            rows = await conn.fetch(
                """
                SELECT kind, minutes_delta, amount_cents, currency, note, created_at
                  FROM billing_ledger
                 WHERE tenant_id = $1::uuid
                 ORDER BY created_at DESC
                 LIMIT $2
                """,
                str(tenant_id), limit,
            )
        return [dict(r) for r in rows]

    async def reconciliation(
        self, *, since: Optional[str] = None, until: Optional[str] = None,
        limit: int = 5000,
    ) -> list[dict]:
        """Cross-tenant ledger for admin reconciliation (goals.md §9).

        Deliberately reads the LEDGER and not ``topup_orders``: the ledger is
        what actually moved, and an order can sit in a state that never became
        money. Reconciling against intent rather than record is how a set of
        books stops matching the payment provider's.
        """
        async with acquire_with_tenant(self._pool, None) as conn:
            rows = await conn.fetch(
                """
                SELECT l.id, l.tenant_id, t.business_name, l.kind,
                       l.minutes_delta, l.amount_cents, l.currency,
                       l.provider_event_id, l.note, l.created_at,
                       o.package_code, o.provider_payment_id, o.status AS order_status
                  FROM billing_ledger l
             LEFT JOIN topup_orders o ON o.id = l.order_id
             LEFT JOIN tenants t ON t.id = l.tenant_id
                 WHERE ($1::timestamptz IS NULL OR l.created_at >= $1::timestamptz)
                   AND ($2::timestamptz IS NULL OR l.created_at < $2::timestamptz)
                 ORDER BY l.created_at DESC
                 LIMIT $3
                """,
                since, until, limit,
            )
        return [dict(r) for r in rows]

    async def purchased_total(self, tenant_id: str) -> int:
        """Net minutes this tenant has bought — the sum of the ledger.

        Reported alongside the quota so "you have 340 minutes left" can be
        explained as plan allocation plus what was topped up.
        """
        async with acquire_with_tenant(self._pool, str(tenant_id)) as conn:
            total = await conn.fetchval(
                "SELECT COALESCE(SUM(minutes_delta), 0) FROM billing_ledger "
                " WHERE tenant_id = $1::uuid",
                str(tenant_id),
            )
        return int(total or 0)
