"""minute top-ups: packages, orders, and an immutable ledger (goals.md §9)

WHAT MONEY REQUIRES THAT NOTHING ELSE HERE DOES
------------------------------------------------
Everything in this migration exists to make one sentence true: **a payment
credits minutes exactly once, and only after the provider says it succeeded.**

The three failure modes that sentence rules out are all real and all common:

  double credit   Stripe retries webhooks. It is not a bug, it is the documented
                  contract — a delivery that times out is redelivered. Without a
                  uniqueness constraint on the event id, a slow response earns
                  the customer free minutes.
  early credit    Crediting when the checkout session is CREATED rather than
                  paid gives minutes to anyone who opens the payment page and
                  walks away.
  silent credit   Minutes appearing with no record of which payment bought them
                  makes a refund or a dispute unanswerable.

WHY minutes_allocated AND NOT minutes_used
-------------------------------------------
``minutes_quota`` computes usage as SUM(calls.duration_seconds) against
``tenants.minutes_allocated``. The ``minutes_used`` column is deliberately NOT
the source of truth (see that module). So "credit 500 minutes" means INCREASING
minutes_allocated — and the ledger is what explains why the number moved.

``minutes_allocated <= 0`` is the existing sentinel for UNLIMITED. A top-up must
never turn an unlimited tenant into a metered one, so the credit path skips them
rather than adding 500 to a 0 and silently capping an account that had no cap.

THE LEDGER IS IMMUTABLE
------------------------
Rows are inserted, never updated. A refund is a NEW negative row, not an edit to
the original. That is the only shape in which "what did this tenant actually
buy, and when" survives a dispute six months later.

Revision ID: 0021_billing_topup
Revises: 0020_contact_and_lead_capture
Create Date: 2026-08-25 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import text

from alembic import op

revision: str = "0021_billing_topup"
down_revision: str | None = "0020_contact_and_lead_capture"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ORDER_STATES = (
    "pending",     # order created, checkout not completed
    "paid",        # provider confirmed payment, minutes credited
    "failed",      # provider reported failure
    "cancelled",   # customer abandoned or we expired it
    "refunded",    # money returned, minutes clawed back
    "disputed",    # chargeback opened
)

LEDGER_KINDS = ("topup", "refund", "adjustment", "dispute")


def upgrade() -> None:
    # ── approved packages ───────────────────────────────────────────────────
    # A closed list, not a free-form amount. An endpoint that accepts "minutes"
    # and "price" from the client is an endpoint that sells 10,000 minutes for
    # one pound the first time somebody edits a request.
    op.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS topup_packages (
                id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                code          VARCHAR(64) NOT NULL UNIQUE,
                name          VARCHAR(128) NOT NULL,
                minutes       INTEGER NOT NULL CHECK (minutes > 0),
                price_cents   INTEGER NOT NULL CHECK (price_cents >= 0),
                currency      VARCHAR(3) NOT NULL DEFAULT 'GBP',
                -- NULL = the minutes never expire. An expiry of 0 would read as
                -- "expires immediately", which is a footgun nobody wants.
                expires_days  INTEGER CHECK (expires_days IS NULL OR expires_days > 0),
                is_active     BOOLEAN NOT NULL DEFAULT TRUE,
                sort_order    INTEGER NOT NULL DEFAULT 0,
                created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
    )

    op.execute(
        text(
            f"""
            CREATE TABLE IF NOT EXISTS topup_orders (
                id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id        UUID NOT NULL,
                user_id          UUID,
                package_code     VARCHAR(64) NOT NULL,
                -- Snapshotted from the package at ORDER time. A package whose
                -- price changes next month must not retroactively change what
                -- this customer was charged.
                minutes          INTEGER NOT NULL CHECK (minutes > 0),
                price_cents      INTEGER NOT NULL CHECK (price_cents >= 0),
                currency         VARCHAR(3) NOT NULL,
                status           VARCHAR(16) NOT NULL DEFAULT 'pending',
                provider         VARCHAR(32) NOT NULL DEFAULT 'stripe',
                provider_session_id VARCHAR(255),
                provider_payment_id VARCHAR(255),
                created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                paid_at          TIMESTAMPTZ,
                CONSTRAINT topup_orders_status_valid CHECK (status IN {ORDER_STATES})
            )
            """
        )
    )
    op.execute(
        text(
            "CREATE INDEX IF NOT EXISTS idx_topup_orders_tenant "
            "ON topup_orders (tenant_id, created_at DESC)"
        )
    )
    # One order per checkout session. Stripe can deliver the same
    # checkout.session.completed more than once; this makes the lookup exact.
    op.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_topup_orders_session "
            "ON topup_orders (provider_session_id) "
            "WHERE provider_session_id IS NOT NULL"
        )
    )

    # ── the immutable ledger ────────────────────────────────────────────────
    op.execute(
        text(
            f"""
            CREATE TABLE IF NOT EXISTS billing_ledger (
                id             BIGSERIAL PRIMARY KEY,
                tenant_id      UUID NOT NULL,
                order_id       UUID REFERENCES topup_orders(id),
                kind           VARCHAR(16) NOT NULL,
                -- Signed. A refund is a NEW negative row, never an edit to the
                -- original — that is what makes this answerable in a dispute.
                minutes_delta  INTEGER NOT NULL,
                amount_cents   INTEGER NOT NULL DEFAULT 0,
                currency       VARCHAR(3),
                -- THE IDEMPOTENCY KEY. The provider's event id. A redelivered
                -- webhook collides here and is refused by the database rather
                -- than by application logic that might be racing itself.
                provider_event_id VARCHAR(255),
                note           TEXT,
                created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT billing_ledger_kind_valid CHECK (kind IN {LEDGER_KINDS})
            )
            """
        )
    )
    # NOT a partial index, deliberately. `ON CONFLICT (provider_event_id)`
    # cannot infer a partial index unless the statement repeats its predicate
    # verbatim — Postgres raises "no unique or exclusion constraint matching
    # the ON CONFLICT specification" and the credit fails outright. A plain
    # unique index costs nothing here because Postgres already treats NULLs as
    # distinct, so the manual-adjustment rows that carry no provider event id
    # can still be inserted freely.
    op.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_billing_ledger_event "
            "ON billing_ledger (provider_event_id)"
        )
    )
    op.execute(
        text(
            "CREATE INDEX IF NOT EXISTS idx_billing_ledger_tenant "
            "ON billing_ledger (tenant_id, created_at DESC)"
        )
    )
    op.execute(
        text(
            """
            COMMENT ON TABLE billing_ledger IS
            'Append-only. Never UPDATE or DELETE a row here — a correction is a '
            'new signed entry. provider_event_id is UNIQUE, which is what makes '
            'a redelivered webhook a no-op at the database level rather than '
            'relying on application logic.'
        """
        )
    )

    # ── RLS, canonical shape from 0013 ──────────────────────────────────────
    for tbl in ("topup_orders", "billing_ledger"):
        op.execute(text(f"ALTER TABLE {tbl} ENABLE ROW LEVEL SECURITY"))
        op.execute(text(f"ALTER TABLE {tbl} FORCE ROW LEVEL SECURITY"))
        op.execute(text(f"DROP POLICY IF EXISTS {tbl}_tenant_isolation ON {tbl}"))
        op.execute(
            text(
                f"""
                CREATE POLICY {tbl}_tenant_isolation ON {tbl}
                    USING (
                        COALESCE(NULLIF(current_setting('app.bypass_rls', TRUE), '')::boolean, FALSE)
                        OR tenant_id = NULLIF(current_setting('app.current_tenant_id', TRUE), '')::uuid
                    )
                    WITH CHECK (
                        COALESCE(NULLIF(current_setting('app.bypass_rls', TRUE), '')::boolean, FALSE)
                        OR tenant_id = NULLIF(current_setting('app.current_tenant_id', TRUE), '')::uuid
                    )
                """
            )
        )
    # topup_packages is deliberately NOT tenant-scoped: the catalogue is the
    # same for everyone, and per-tenant pricing is a different feature.

    # ── seed a starter catalogue ────────────────────────────────────────────
    # Priced so the per-minute rate falls as the bundle grows, which is the
    # shape customers expect and the reason to buy the larger one.
    op.execute(
        text(
            """
            INSERT INTO topup_packages
                (code, name, minutes, price_cents, currency, sort_order)
            VALUES
                ('mins_250',  '250 minutes',   250,  2500, 'GBP', 1),
                ('mins_600',  '600 minutes',   600,  5400, 'GBP', 2),
                ('mins_1500', '1,500 minutes', 1500, 12000, 'GBP', 3)
            ON CONFLICT (code) DO NOTHING
            """
        )
    )


def downgrade() -> None:
    # The ledger is deliberately NOT dropped: it is the record of money that
    # changed hands, and a schema rollback is not a reason to lose it. Drop the
    # order/package tables only if the ledger is empty.
    conn = op.get_bind()
    entries = conn.execute(text("SELECT count(*) FROM billing_ledger")).scalar()
    if entries:
        raise RuntimeError(
            f"billing_ledger has {entries} entries — refusing to drop billing "
            "tables. Export the ledger first if this rollback is genuinely "
            "intended."
        )
    op.execute(text("DROP TABLE IF EXISTS billing_ledger"))
    op.execute(text("DROP TABLE IF EXISTS topup_orders"))
    op.execute(text("DROP TABLE IF EXISTS topup_packages"))
