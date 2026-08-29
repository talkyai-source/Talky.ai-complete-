# Report 13 — Money: Minute Top-Ups, and the Four Ways "Credit Once" Quietly Breaks

**Date:** 2026-08-25
**Previous report:** `report12.md`
**Production HEAD:** `41326b94` — **everything in this report is live**
**Migration applied:** `0021_billing_topup` (prod `alembic_version` now reads `0021_billing_topup`)
**Scope:** goals.md §9 (Billing Minute Top-Up), end to end — schema, service,
webhook routing, API, frontend, receipt, admin reconciliation, and the proof.

**The one-line version:** a tenant can now buy call minutes mid-cycle, and the
minutes land in the exact quota the dialler blocks on. The whole design exists
to make one sentence true — *a verified successful payment credits its minutes
exactly once* — and this report is mostly about the four ordinary, unexotic ways
that sentence breaks if you implement it the obvious way.

**The finding that matters most:** Stripe delivers subscription checkouts and
top-up checkouts **down the same event type, to the same endpoint**. The
subscription handler this codebase already had writes `plan_id` and
`stripe_subscription_id` straight from the session — and a one-time payment
session carries neither. An unrouted top-up would have **nulled out the plan of
the customer who had just paid us.** §3.

**And the caveat that matters:** production has no Stripe keys. The flow is
built, proven, and deployed, and it **cannot take a penny** until two
environment variables are set. §13.

---

## Table of Contents

- [§1. What was asked, and what was delivered](#1-what-was-asked-and-what-was-delivered)
- [§2. The one invariant, and why the obvious implementation is wrong](#2-the-one-invariant-and-why-the-obvious-implementation-is-wrong)
- [§3. One webhook stream, two products](#3-one-webhook-stream-two-products)
- [§4. The schema — money is not the same as intent](#4-the-schema--money-is-not-the-same-as-intent)
- [§5. The unlimited sentinel — a purchase that makes you worse off](#5-the-unlimited-sentinel--a-purchase-that-makes-you-worse-off)
- [§6. Refunds and disputes](#6-refunds-and-disputes)
- [§7. What the real database caught that the fakes could not](#7-what-the-real-database-caught-that-the-fakes-could-not)
- [§8. The silent zero-row UPDATE, again](#8-the-silent-zero-row-update-again)
- [§9. The frontend](#9-the-frontend)
- [§10. The receipt and the reconciliation view](#10-the-receipt-and-the-reconciliation-view)
- [§11. Verification](#11-verification)
- [§12. Deployment record](#12-deployment-record)
- [§13. The Stripe blocker](#13-the-stripe-blocker)
- [§14. What I did not do, and the box left unticked](#14-what-i-did-not-do-and-the-box-left-unticked)
- [§15. Where the eleven P0s stand](#15-where-the-eleven-p0s-stand)
- [Appendix A — file by file](#appendix-a--file-by-file)
- [Appendix B — the API surface](#appendix-b--the-api-surface)
- [Appendix C — verbatim verification output](#appendix-c--verbatim-verification-output)
- [Appendix D — runbook](#appendix-d--runbook)

---

## §1. What was asked, and what was delivered

The instruction was short:

> "billing and tenute minutes work on them once they are done we ll be on 9 and
> these 9 must be 100 percent done"

Two things in that sentence, and a standard.

**"billing"** is goals.md §9 — Billing Minute Top-Up. **"tenant minutes"** is the
part of §9 that most implementations skip: the balance a customer sees, and
whether buying more of it actually changes what the dialler will let them do.
Those are not the same thing, and a system where they disagree is worse than one
with no balance display at all, because a wrong number is believed.

**"100 percent done"** is a standard that has been set repeatedly in this
project, and it means the checkbox is not ticked until the thing works. Not
"backend done, frontend later". Not "the happy path works". §9 has 21 checkboxes
across Backend, Frontend and Acceptance Criteria. Twenty of them are now ticked
and the twenty-first is deliberately not — see §14.

### 1.1 The §9 checklist, line by line

**Backend (10/10):**

| Requirement | Where it lives | Note |
| --- | --- | --- |
| Define approved top-up packages and currency | `topup_packages` + migration seed | Closed catalogue, three GBP bundles |
| Create a top-up order before payment | `TopupService.create_order` | Order first, always — §4.1 |
| Use the provider's hosted checkout | `BillingService.create_topup_checkout_session` | Stripe Checkout, `mode="payment"` |
| Verify signed payment webhooks | existing `POST /billing/webhooks` | Reused deliberately — §3.5 |
| Make webhook processing idempotent | `billing_ledger.provider_event_id` UNIQUE | §2 — the constraint *is* the guard |
| Credit minutes only after verified successful payment | `payment_status == "paid"` gate | §2.2 |
| Record money and minutes in an immutable ledger | `billing_ledger` | Append-only — §4.3 |
| Handle failed, cancelled, duplicate, refunded, disputed | 5 event types routed | §6 |
| Send receipt/confirmation | `_send_topup_receipt` | Cannot fail the webhook — §10.1 |
| Add admin reconciliation view or export | `GET /admin/billing/reconciliation` | JSON + CSV — §10.2 |

**Frontend (6/6):**

| Requirement | Where it lives |
| --- | --- |
| Add **Top up minutes** to Billing | `TopupCard`, mounted on `/billing` |
| Show current minute balance | balance header, reads the quota function |
| Show package minutes, price, currency and expiry rules | the three bundle tiles |
| Show payment status and top-up history | "Recent top-ups" list with status chips |
| Prevent double submission while checkout is starting | `disabled={pending}` + an early return |
| Show clear failure and retry guidance | three distinct banners — §9.6 |

**Acceptance criteria (4/5):**

| Criterion | Status |
| --- | --- |
| Successful verified payment credits minutes once | ✅ proven on real PostgreSQL |
| Duplicate webhook does not duplicate minutes | ✅ proven, including 10-way concurrent |
| Failed/cancelled payment adds no minutes | ✅ tested |
| Tenant billing records remain isolated | ⛔ **deliberately not ticked** — §14.2 |
| New balance is reflected in call quota enforcement | ✅ proven *through the gate function itself* |

### 1.2 The shape of the change

```
 18 files changed, 4001 insertions(+), 27 deletions(-)
```

Of which the §9 work is:

| File | Lines | What |
| --- | ---: | --- |
| `backend/app/domain/services/topup_service.py` | 417 | new — the service |
| `backend/app/api/v1/endpoints/billing_topups.py` | 393 | new — 5 tenant routes + 1 admin |
| `backend/Alembic/versions/0021_billing_topup.py` | 246 | new — 3 tables, 17 statements |
| `backend/app/domain/services/billing_service.py` | +309 | Stripe checkout, routing, receipt |
| `backend/app/api/v1/routes.py` | +8 | registration |
| `backend/tests/unit/test_topup_service.py` | 415 | new — 21 tests |
| `backend/tests/unit/test_billing_topup_webhook_routing.py` | 403 | new — 15 tests |
| `backend/scripts/verify_topup_ledger.py` | 336 | new — 20 real-DB invariants |
| `Talk-Leee/src/components/billing/topup-card.tsx` | 360 | new — the card |
| `Talk-Leee/src/lib/topup-api.ts` | 216 | new — hooks + the four rules |
| `Talk-Leee/src/lib/topup-api.test.ts` | 150 | new — 18 tests |
| `Talk-Leee/src/app/billing/page.tsx` | +7 | mount point |

About **1,950 lines of implementation and 1,300 lines of test and proof** — two
lines of verification for every three of code. That ratio is not an accident.
Everything else in this system can be corrected by editing a row. This part
moves money.

---

## §2. The one invariant, and why the obvious implementation is wrong

Everything in `topup_service.py` exists to hold one sentence:

> A verified successful payment credits its minutes exactly once.

The obvious implementation of that sentence is:

```python
order = await get_order(session_id)
if order.status == "paid":
    return                       # already done
await credit(order)
await mark_paid(order)
```

This is wrong in four separate ways, and each of the four is an ordinary
operating condition rather than an exotic race.

### 2.1 Stripe redelivers by contract, not by accident

Stripe's webhook delivery is at-least-once. A delivery that times out, or that
returns a 5xx, or that the network eats, **is redelivered** — this is documented
behaviour, not a bug you can hope to avoid. The practical consequence is that
your handler will be called twice for one payment, routinely, in normal
operation.

If the guard for that is a status read, the guard works only when the two
deliveries are far enough apart that the first has already committed. Which is
usually true. Which is exactly what makes it dangerous: it works in testing, it
works for months, and then it doesn't.

### 2.2 A completed checkout is not a settled payment

The event is called `checkout.session.completed`. It is easy to read that as "the
customer paid". It is not what it means. The session object carries a separate
field:

```python
if data.get("payment_status") != "paid":
    logger.info(
        "topup_checkout_completed_unpaid session=%s payment_status=%s "
        "— waiting for the payment to settle", ...
    )
    return {"status": "deferred", "event_type": event_type}
```

A session can complete with the payment still processing — delayed payment
methods do this as a matter of course, and so does any flow where the bank has
not yet confirmed. Crediting on completion alone hands out minutes for payments
that may still fail. The credit waits for `payment_status == "paid"`, and there
is a test that asserts `credit_paid_order` is not called at all when it is
anything else:

```python
async def test_an_unsettled_payment_defers_instead_of_crediting(monkeypatch):
    result = await svc._handle_topup_event(
        "checkout.session.completed",
        _topup_session(payment_status="unpaid"),
        "evt_1",
    )
    assert result["status"] == "deferred"
    assert called["credit"] == 0, "minutes were credited before the money arrived"
```

### 2.3 Two deliveries can be in flight at the same instant

This is the one that kills the status check outright. Consider two deliveries
processed concurrently by two workers:

```
worker A: SELECT status  → 'pending'
worker B: SELECT status  → 'pending'      ← neither has written yet
worker A: credit 250
worker B: credit 250                       ← 500 minutes for one payment
```

There is no ordering of application-level statements that fixes this. The guard
has to be something the database enforces across both transactions, which means
it has to be a **uniqueness constraint**.

So the credit is conditional on a ledger insert that carries Stripe's own event
id under a UNIQUE index:

```python
ledger = await conn.fetchrow(
    """
    INSERT INTO billing_ledger
        (tenant_id, order_id, kind, minutes_delta, amount_cents,
         currency, provider_event_id, note)
    VALUES ($1, $2, 'topup', $3, $4, $5, $6, $7)
    ON CONFLICT (provider_event_id) DO NOTHING
    RETURNING id
    """,
    ...
)
if ledger is None:
    # The constraint refused it: this exact event already credited.
    # Not an error — this is the guard doing its job.
    logger.info("topup_duplicate_event event=%s order=%s — already credited, "
                "no minutes added", event_id[:24], str(order["id"])[:8])
    return False
```

`RETURNING id` is load-bearing. Without it, `ON CONFLICT DO NOTHING` succeeds
either way and you cannot tell a fresh insert from a refused one. The credit
happens **only** on the branch where a row came back.

The status check is still there, above this. It is a fast path — it avoids the
work in the common redelivery case and produces a clearer log line. It is not
the guard, and the test suite says so explicitly:

```python
async def test_the_ledger_refuses_a_repeat_event_even_if_the_order_looks_unpaid():
    """The order-status check is a fast path, not the guard.

    Two deliveries processed concurrently both read status='pending' before
    either writes. Only the uniqueness constraint separates them, so the credit
    must depend on the ledger insert returning a row — not on the status read.
    """
    store = make_store(allocated=1000, order=paid_order())
    store["seen_events"].add("evt_dup")   # a concurrent delivery got there first
    svc = TopupService(FakePool(store))

    credited = await svc.credit_paid_order(session_id="cs_1", event_id="evt_dup")

    assert credited is False
    assert store["allocated"] == 1000, (
        "minutes were added despite the ledger refusing the entry — the credit "
        "is not actually gated on the uniqueness constraint"
    )
```

This test is constructed so it passes **only** if the ledger result is what
gates the credit. An implementation that gates on status alone fails it, even
though such an implementation would pass every other test in the file.

### 2.4 Proven, not argued

The above is a design argument. Design arguments about concurrency are wrong
often enough that they are worth proving. `scripts/verify_topup_ledger.py`
launches ten concurrent deliveries of one event against a real PostgreSQL:

```python
results = await asyncio.gather(*[
    svc.credit_paid_order(session_id="cs_verify_2", event_id="evt_verify_2")
    for _ in range(10)
], return_exceptions=True)
credited = sum(1 for r in results if r is True)
errors   = [r for r in results if isinstance(r, Exception)]
```

Result:

```
PASS  ten simultaneous deliveries credit exactly once
      — credited=1 errors=0 1250 -> 1850
```

One credit. Nine no-ops. Zero exceptions — which matters too: a design that
"only" credits once because nine of the ten raise is not the same thing, because
nine 500s means nine Stripe retries.

And separately, the constraint itself, exercised directly rather than through
the code path that depends on it:

```python
try:
    await pool.execute(
        "INSERT INTO billing_ledger (tenant_id, order_id, kind, "
        " minutes_delta, provider_event_id) "
        "VALUES ($1::uuid, $2::uuid, 'topup', 250, 'evt_verify_1')", ...)
    check("the database refuses a duplicate event id", False,
          "the second insert was accepted")
except asyncpg.UniqueViolationError:
    check("the database refuses a duplicate event id", True)
```

```
PASS  the database refuses a duplicate event id
```

### 2.5 The hole in the idempotency this codebase already had

`BillingService` already had webhook idempotency, added earlier:

```python
if event_id and not await self._claim_webhook_event(event_id, event_type):
    logger.info("Duplicate Stripe webhook ignored event_id=%s type=%s", ...)
    return {"status": "duplicate", ...}
```

`_claim_webhook_event` inserts into `processed_webhook_events` and returns
whether *this* call claimed it. It commits on its own connection, **before the
handler runs.**

Which means: if the handler then fails — a connection reset, a pool timeout, a
transient anything — the event is already claimed. Stripe redelivers. The
redelivery is discarded as a duplicate. **The customer paid and never receives
the minutes, permanently, with no error anywhere that says so.**

That is a pre-existing hole, not one I introduced, but it becomes a money
problem the moment money flows through it. The fix is to release the claim when
the handler raises:

```python
if await self._is_topup_event(event_type, data):
    try:
        return await self._handle_topup_event(event_type, data, event_id)
    except Exception:
        # The claim was taken before the handler ran, so a redelivery
        # would be discarded as a duplicate and the minutes would never
        # be credited. Release it and let Stripe retry.
        if event_id:
            await self._release_webhook_claim(event_id)
        raise
```

Releasing is safe here precisely *because* of §2.3: the ledger constraint means a
retry that races the original still credits once. The claim table is an
optimisation; the ledger is the truth.

I deliberately scoped the release to the top-up branch and did not change the
subscription handlers' behaviour. Those handlers send emails, and making their
failures retryable would send duplicate mail. That is a real trade-off in the
other direction and not mine to make unilaterally today — it is noted in §14.3
as open.

The release path is driven end-to-end in test, through the real `handle_webhook`
rather than by calling the release function from the test itself:

```python
async def test_a_failed_credit_releases_the_claim_so_stripe_can_retry(monkeypatch):
    svc = _svc()
    event = _fake_event("checkout.session.completed", _topup_session())
    claimed = _arm_for_real_webhook(svc, monkeypatch, event)
    ...
    with pytest.raises(RuntimeError):
        await svc.handle_webhook(b"{}", "sig")

    assert claimed  == ["evt_boom"], "the event should have been claimed first"
    assert released == ["evt_boom"], (
        "the claim was not released, so Stripe's retry will be discarded as a "
        "duplicate and these minutes are lost for good"
    )
```

The first draft of this test called `_release_webhook_claim` inside the test's
own `except` block, which meant it asserted that my test did the right thing
rather than that the code did. I rewrote it. Worth saying because it is an easy
shape to write by accident and it proves nothing.

---

## §3. One webhook stream, two products

This is the most important finding in the report, and it is not a subtle one
once you see it. It is subtle only because nothing points at it.

### 3.1 What Stripe actually delivers

Stripe does not have a "subscription checkout completed" event and a "one-time
checkout completed" event. There is one event type:

```
checkout.session.completed
```

It fires for both. Both arrive at the same configured endpoint. The only thing
distinguishing them is what is *inside* the session object.

### 3.2 What the existing handler does with it

`_handle_checkout_completed` has been in this codebase since before today. It is
correct for subscriptions:

```python
async def _handle_checkout_completed(self, session: Dict):
    tenant_id       = session.get("metadata", {}).get("tenant_id")
    plan_id         = session.get("metadata", {}).get("plan_id")
    subscription_id = session.get("subscription")
    customer_id     = session.get("customer")

    if not tenant_id:
        logger.warning("Checkout completed but no tenant_id in metadata")
        return

    self.db_client.table("tenants").update({
        "stripe_customer_id":     customer_id,
        "stripe_subscription_id": subscription_id,
        "subscription_status":    "active",
        "plan_id":                plan_id,
    }).eq("id", tenant_id).execute()
```

Read the guard. It returns early on a **missing `tenant_id`**. My top-up session
metadata *has* `tenant_id` — it has to, so the credit knows whose minutes to
move. So the guard does not fire.

### 3.3 The damage

A one-time payment session has no subscription and no plan. So
`session.get("subscription")` is `None` and `metadata.plan_id` is absent. The
UPDATE would run with:

```
stripe_subscription_id = NULL
plan_id                = NULL
subscription_status    = 'active'
```

A customer on a paid plan buys 250 top-up minutes, and in the same instant their
subscription linkage and plan id are wiped. Their plan silently becomes nothing.
The next `/billing/subscription` read returns a plan with no name. Depending on
what else keys off `plan_id`, the blast radius grows from there.

The trigger is *paying us money*. There is no error, no exception, no log line
that says anything went wrong — an UPDATE that sets columns to NULL is a
perfectly successful UPDATE.

### 3.4 The fix, and why it is tested in both directions

Every top-up session is stamped at creation:

```python
meta = {
    "purpose":   "minute_topup",
    "tenant_id": tenant_id,
    "order_id":  str(order_id),
    "minutes":   str(minutes),
}
```

and the dispatch branches on it **before** the handler table is consulted:

```python
# ── minute top-ups branch off FIRST ─────────────────────────────────
# Stripe delivers subscription checkouts and top-up checkouts down the
# same `checkout.session.completed` stream. Routing on the purpose we
# stamped at creation time is what keeps a top-up from reaching
# _handle_checkout_completed, which would null out the tenant's
# plan_id and stripe_subscription_id from a one-time session's empty
# fields — breaking the plan of a customer who just gave us money.
if await self._is_topup_event(event_type, data):
    ...
handlers = {
    "checkout.session.completed": self._handle_checkout_completed,
    ...
}
```

A routing guard has two failure modes, not one. It can let a top-up through to
the subscription handler (the bug above), and it can *swallow a subscription*
into the top-up handler — which would mean subscriptions silently stop being
provisioned. Both are tested:

```python
async def test_a_topup_never_reaches_the_subscription_handler(monkeypatch):
    ...
    assert subscription_handler_ran == [], (
        "a minute top-up was processed as a subscription checkout — this blanks "
        "out plan_id and stripe_subscription_id for a paying customer"
    )


async def test_a_subscription_checkout_still_reaches_its_own_handler(monkeypatch):
    """The other half of the guard: routing must not swallow subscriptions."""
    ...
    assert result["status"] == "handled"
    assert len(ran) == 1
```

Both drive the real `handle_webhook`, with `stripe.Webhook.construct_event`
stubbed to return a fixed event — so the routing under test is the routing that
runs in production, not a re-implementation of it in the test.

And a third, for the shape that would break the guard from below:

```python
async def test_a_session_with_no_metadata_at_all_is_not_a_topup():
    svc = _svc()
    assert await svc._is_topup_event("checkout.session.completed", {}) is False
    assert await svc._is_topup_event(
        "checkout.session.completed", {"metadata": None}) is False
```

`metadata: None` rather than a missing key is exactly the sort of thing a
provider sends occasionally, and `(data.get("metadata") or {}).get("purpose")`
handles it where `data.get("metadata", {}).get(...)` would raise.

### 3.5 Why there is no second webhook endpoint

The obvious alternative is to give top-ups their own URL and sidestep the
routing problem entirely. I did not, for three reasons:

1. **A second endpoint is a second signing secret** to configure, rotate and get
   wrong — at the exact point in the system where getting it wrong means either
   accepting forged payment events or rejecting real ones.
2. **Stripe delivers the same event id to every configured endpoint.** So the
   existing `processed_webhook_events` claim and my ledger constraint would be
   racing across two endpoints instead of one, which is more surface for the
   same guarantee, not less.
3. **Signature verification would exist in two places.** One of them would drift.

So `billing_topups.py` has no webhook route at all, and says so at the top of
the file so the next person does not add one:

```
THERE IS NO WEBHOOK ENDPOINT HERE — DELIBERATELY
-------------------------------------------------
Stripe delivers subscription events and top-up events down the SAME endpoint,
``POST /billing/webhooks``. Adding a second URL would mean a second signing
secret to configure and a second thing to get wrong at the exact moment money
is involved.
```

### 3.6 The events that now route to the top-up path

| Event | Handling |
| --- | --- |
| `checkout.session.completed` | credit, **if** `payment_status == "paid"` and `purpose == "minute_topup"` |
| `checkout.session.expired` | mark the order `cancelled`, no ledger entry |
| `checkout.session.async_payment_failed` | mark the order `failed`, no ledger entry |
| `charge.refunded` | reverse, **only** on a full refund — §6.3 |
| `charge.dispute.created` | reverse as `dispute` |

The two `charge.*` events are claimed for the top-up path unconditionally,
because a charge object carries no checkout session and there is nothing to
route on. That is safe, and the reason is written where the decision is made:

```python
# Reversals arrive on the charge, which carries no checkout session. Neither
# of these has a handler in the subscription table, so claiming them for the
# top-up path costs nothing when the charge turns out to be a subscription:
# the order lookup finds nothing and the handler no-ops.
_TOPUP_CHARGE_EVENTS = {"charge.refunded", "charge.dispute.created"}
```

Neither event had a handler before today, so nothing is being taken away from
another code path.

---

## §4. The schema — money is not the same as intent

Migration `0021_billing_topup`, 17 statements, three tables.

### 4.1 Three tables, and what each one is for

**`topup_packages`** — the approved catalogue. A closed list.

```sql
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
```

The reason this table exists rather than the client naming an amount is stated
in the endpoint's module docstring and enforced by the service signature:

> A package code. Not minutes, not a price. An endpoint that accepts an amount
> from the browser sells 10,000 minutes for a penny the first time somebody
> edits a request, and the fact that the UI only ever sends good values is not a
> control — it is a hope.

There is a test whose entire job is to make sure that stays true as the code
changes:

```python
sig = inspect.signature(TopupService.create_order).parameters
assert "minutes" not in sig and "price_cents" not in sig, (
    "create_order must not accept an amount from its caller"
)
```

**`topup_orders`** — the intent to buy, recorded before anyone is sent to a
payment page.

```sql
    package_code     VARCHAR(64) NOT NULL,
    -- Snapshotted from the package at ORDER time. A package whose
    -- price changes next month must not retroactively change what
    -- this customer was charged.
    minutes          INTEGER NOT NULL CHECK (minutes > 0),
    price_cents      INTEGER NOT NULL CHECK (price_cents >= 0),
    currency         VARCHAR(3) NOT NULL,
    status           VARCHAR(16) NOT NULL DEFAULT 'pending',
    ...
    CONSTRAINT topup_orders_status_valid CHECK (status IN
        ('pending','paid','failed','cancelled','refunded','disputed'))
```

Two things here.

*The snapshot.* Price and minutes are copied onto the order at creation. If the
catalogue is repriced next month, this customer is still owed exactly what they
were quoted. A join to `topup_packages` at credit time would silently rewrite
history whenever pricing changed.

*Order first.* The order is created **before** the Stripe session, always. The
order row is what the webhook matches a payment against; creating the session
first opens a window where a customer can pay for something we have no record
of. That window is small. It is not zero, and the failure inside it is
unrecoverable without manually reading Stripe's dashboard.

The consequence is that a payment arriving with no matching order is a *signal*,
and it is logged as one rather than papered over by inventing a row:

```python
logger.warning(
    "topup_webhook_unknown_session session=%s event=%s — a payment "
    "arrived for an order we never created", session_id[:16], event_id[:24],
)
```

**`billing_ledger`** — what actually moved.

```sql
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
    CONSTRAINT billing_ledger_kind_valid CHECK (kind IN
        ('topup','refund','adjustment','dispute'))
)
```

and a comment on the table itself, because the next person to touch it will not
read this report:

```sql
COMMENT ON TABLE billing_ledger IS
'Append-only. Never UPDATE or DELETE a row here — a correction is a '
'new signed entry. provider_event_id is UNIQUE, which is what makes '
'a redelivered webhook a no-op at the database level rather than '
'relying on application logic.';
```

### 4.2 Why `minutes_allocated` and not `minutes_used`

`tenants` has both columns. Only one is real.

`minutes_quota.py` — the single source of truth this system already had —
computes usage as `SUM(calls.duration_seconds)` for the current month against
`tenants.minutes_allocated`. Its own docstring records why:

> ``minutes_allocated <= 0`` means **unlimited** — never blocked. This is a
> deliberate sentinel: the ``tenants.minutes_used`` column is intentionally
> NOT consulted (no call-end hook writes it, so it always reads 0).

So "credit 500 minutes" means **increasing `minutes_allocated`**. Crediting
`minutes_used` downward would have been the intuitive reading of the column
names and would have done nothing at all, silently, because nothing reads it.

This is exactly the class of bug that looks like it works: the number in the
database changes, the API returns it, and the dialler carries on refusing to
place calls. Which is why §11.2 exists.

### 4.3 The ledger is append-only

A refund does not edit the purchase row. It inserts a new row with a negative
`minutes_delta` and a negative `amount_cents`. The original stays exactly as it
was.

The reason is not aesthetic. Six months from now, a customer disputes a charge
and someone has to answer *what did this tenant actually buy, and when.* If the
refund rewrote the original, the only surviving record says the purchase was for
zero minutes, or does not exist. There is nothing left to answer with.

Proven directly:

```
PASS  a refund takes the minutes back  — 1850 -> 1600
PASS  the refund is its own negative row
PASS  the original purchase row is untouched  — a refund must not rewrite history
```

The third check reads the original ledger row *after* the reversal and asserts
`minutes_delta == 250`.

### 4.4 The seeded catalogue

```sql
INSERT INTO topup_packages
    (code, name, minutes, price_cents, currency, sort_order)
VALUES
    ('mins_250',  '250 minutes',   250,  2500, 'GBP', 1),
    ('mins_600',  '600 minutes',   600,  5400, 'GBP', 2),
    ('mins_1500', '1,500 minutes', 1500, 12000, 'GBP', 3)
ON CONFLICT (code) DO NOTHING
```

| Code | Minutes | Price | Per minute |
| --- | ---: | ---: | ---: |
| `mins_250` | 250 | £25.00 | 10.0p |
| `mins_600` | 600 | £54.00 | 9.0p |
| `mins_1500` | 1,500 | £120.00 | 8.0p |

The per-minute rate falls as the bundle grows, which is the shape customers
expect and the only reason to buy the larger one. That property is asserted, not
assumed, so a future repricing cannot quietly invert it:

```python
check("bundles get cheaper per minute as they get bigger",
      all(
          pkgs[i]["price_cents"] / pkgs[i]["minutes"]
          > pkgs[i + 1]["price_cents"] / pkgs[i + 1]["minutes"]
          for i in range(len(pkgs) - 1)
      ))
```

These are placeholder prices seeded so the flow is complete and testable. They
are a row in a table; changing them is an UPDATE, not a deploy.

### 4.5 The downgrade that refuses

```python
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
    ...
```

A migration rollback is an ordinary operational action. Losing the record of
money that changed hands is not. Right now the ledger is empty so the rollback
is clean; the moment it is not, the rollback stops and says why.

### 4.6 RLS

Both tenant-scoped tables get the canonical policy shape established by
migration 0013:

```sql
CREATE POLICY topup_orders_tenant_isolation ON topup_orders
    USING (
        COALESCE(NULLIF(current_setting('app.bypass_rls', TRUE), '')::boolean, FALSE)
        OR tenant_id = NULLIF(current_setting('app.current_tenant_id', TRUE), '')::uuid
    )
    WITH CHECK ( ... same ... );
```

`NULLIF` is load-bearing: without it, an unset GUC returns `''` and the `::uuid`
cast raises rather than evaluating to false. `WITH CHECK` matters as much as
`USING` — without it a tenant could *insert* a row belonging to somebody else
even though they could not read it back.

`topup_packages` is deliberately **not** tenant-scoped. The catalogue is the same
for everyone; per-tenant pricing is a different feature and pretending otherwise
now would build a wrong abstraction cheaply.

The caveat that applies to all of this is §14.2.

---

## §5. The unlimited sentinel — a purchase that makes you worse off

`minutes_allocated <= 0` means **unlimited**, everywhere in this system.

Now consider an unlimited tenant buying a 250-minute top-up. The naive credit
is:

```sql
UPDATE tenants SET minutes_allocated = minutes_allocated + 250 WHERE id = $1
```

`0 + 250 = 250`. The account that had **no cap** now has a **250-minute cap**.
The customer paid money and came away with strictly less than they started with,
and the system that did it to them reports success.

So the credit branches:

```python
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
        ...
    )
```

Three deliberate choices in that `else`.

**The money is still recorded.** A ledger entry is written regardless. If a
payment was taken, it is in the books. Skipping the ledger to "keep things
tidy" would mean money in Stripe with no corresponding record here — the single
worst outcome available.

**The log is a warning, not an info.** This branch firing means something was
*sold* that should not have been sellable. It is not a normal path; it is a
symptom that the offer surface and the credit surface disagree.

**The frontend never offers it.** `canTopUp()` returns false for an unlimited
balance, so the Buy buttons are not rendered at all — the backend branch is the
backstop, not the primary control. Both exist because the API is reachable
without the UI.

Proven on the real database:

```
PASS  an unlimited tenant is not given a cap  — minutes_allocated=0
PASS  the money is still recorded for them
```

with the warning visible in the run output:

```
topup_on_unlimited_tenant tenant=61f07631 order=90e759a5 minutes=250 —
money recorded, allocation untouched. This tenant should not have been
offered a top-up.
```

Also note `FOR UPDATE` on the tenant read. Two concurrent credits for the same
tenant would otherwise both read the old allocation. The whole credit runs in
one transaction, so the row lock is held until the allocation is written.

---

## §6. Refunds and disputes

§9 asks for "failed, cancelled, duplicate, refunded and disputed". The first
three are covered above. These two are their own shape.

### 6.1 A new negative row, never an edit

Covered in §4.3. The mechanism:

```python
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
    -order["price_cents"], order["currency"], event_id, ...
)
if entry is None:
    return False
```

Same `ON CONFLICT` guard as the credit, for the same reason — refund webhooks
are redelivered too, and a doubly-applied reversal takes 500 minutes off for one
250-minute refund.

### 6.2 Floored at zero, in SQL

```sql
UPDATE tenants
   SET minutes_allocated = GREATEST(0, minutes_allocated - $2)
 WHERE id = $1 AND minutes_allocated > 0
```

A tenant buys 250 minutes, spends them, then charges back. Subtracting freely
lands on a negative allocation — and per §5, **anything `<= 0` reads as
unlimited.** A chargeback would buy them uncapped calling forever. That is not a
theoretical exploit; it is what a naive subtraction does on the first customer
who spends before disputing.

The floor is in the statement, not in Python, and there is a test that keeps it
there:

```python
def test_the_reversal_floor_is_in_the_statement():
    """Structural: doing the floor in Python would leave a window where a
    concurrent credit and reversal interleave into a negative allocation."""
    assert "GREATEST(0" in inspect.getsource(TopupService.reverse)
```

and one that exercises the behaviour:

```python
async def test_a_chargeback_cannot_push_an_allocation_below_zero():
    store = make_store(allocated=100, order=paid_order(status="paid"))
    svc = TopupService(FakePool(store))
    await svc.reverse(event_id="evt_d", kind="dispute", payment_id="pi_1")
    assert store["allocated"] == 0
    assert store["allocated"] >= 0
```

### 6.3 A partial refund is not guessed

Refunding £5 of a £25 bundle is not a reason to take 250 minutes away. But how
many minutes *should* come off is a judgement call — proportional? rounded which
way? what if they have already spent more than the remainder? — and a system
that guesses at that will be wrong in a way that is expensive to unpick.

So it does not guess:

```python
if event_type == "charge.refunded":
    # A PARTIAL refund must not claw back the whole bundle. Only a fully
    # refunded charge reverses the minutes; anything else is flagged for
    # a human because splitting a bundle is a judgement call.
    if not data.get("refunded"):
        logger.warning(
            "topup_partial_refund charge=%s refunded=%s of %s — minutes "
            "left in place, reconcile by hand", ...
        )
        return {"status": "ignored", "event_type": event_type}
```

`charge.refunded` fires for partial refunds too; the `refunded` boolean on the
charge is what says the charge is *fully* refunded. Tested both ways.

### 6.4 Matched on payment id, not session id

A refund event carries a charge. A charge has a `payment_intent`. It does **not**
have a checkout session. So `reverse()` matches on the payment id, with a
session fallback:

```sql
SELECT id, tenant_id, minutes, price_cents, currency, status
  FROM topup_orders
 WHERE ($1::text IS NOT NULL AND provider_payment_id = $1)
    OR ($2::text IS NOT NULL AND provider_session_id = $2)
 FOR UPDATE
```

which is why `create_topup_checkout_session` puts the metadata on the
PaymentIntent as well as the session:

```python
session = stripe.checkout.Session.create(
    ...
    metadata=meta,
    payment_intent_data={"metadata": meta},
)
```

and why the credit records the payment id when it fires:

```sql
UPDATE topup_orders SET status='paid', paid_at=NOW(), updated_at=NOW(),
       provider_payment_id=COALESCE($2, provider_payment_id)
 WHERE id = $1
```

`COALESCE` rather than a plain assignment: if a later event arrives without a
payment id, it must not erase the one we already have.

### 6.5 Most refunds are not top-ups, and that is fine

Because `charge.refunded` is claimed unconditionally (§3.6), the handler runs for
subscription refunds too. It finds no paid top-up order and returns:

```python
if not order or order["status"] != "paid":
    # Most refunds on this account are subscription refunds, which
    # have no top-up order. Nothing to reverse is the normal case.
    logger.info(
        "topup_reverse_skipped ref=%s kind=%s — no paid top-up order",
        (payment_id or session_id or "")[:16], kind,
    )
    return False
```

An `info`, not a warning, because this is the expected majority case. Log level
is a signal to whoever reads the journal at 2am; getting it wrong trains people
to ignore the file.

---

## §7. What the real database caught that the fakes could not

The unit tests use an in-memory connection double that runs the service's real
branching. It caught nothing that a careful read would not have. The real
PostgreSQL run caught two things that would have been production incidents, and
one bug in my own test harness.

### 7.1 `ON CONFLICT` cannot infer a PARTIAL unique index

The first draft of the migration had:

```sql
CREATE UNIQUE INDEX IF NOT EXISTS idx_billing_ledger_event
ON billing_ledger (provider_event_id)
WHERE provider_event_id IS NOT NULL
```

The reasoning was ordinary: manual `adjustment` entries have no provider event
id, so exclude the NULLs and keep the index small.

The first real credit failed outright:

```
asyncpg.exceptions.InvalidColumnReferenceError: there is no unique or
exclusion constraint matching the ON CONFLICT specification
```

**Postgres cannot infer a partial index from `ON CONFLICT (col)`** unless the
statement repeats the index predicate verbatim — `ON CONFLICT (provider_event_id)
WHERE provider_event_id IS NOT NULL`. Without that, it does not match, and the
INSERT raises instead of conflicting.

The consequence had this shipped: **every top-up credit would raise.** Not
silently wrong — loudly broken, on the first real payment. Which is a mercy, but
only after a customer has been charged.

The fix was to drop the predicate rather than duplicate it:

```python
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
```

A total unique index on a nullable column permits unlimited NULLs in Postgres
by default. So the partial predicate bought nothing and broke the only thing
that mattered.

This is worth generalising: **a constraint you never exercise against the real
engine is a comment.** Every test that mattered here had passed.

### 7.2 asyncpg's pool runs `RESET ALL` on release

The verification harness needs its tables in a throwaway schema, so it sets
`search_path` on the pool:

```python
pool = await asyncpg.create_pool(url, min_size=2, max_size=4, init=_init)
```

The migration DDL ran fine. The very next call failed:

```
asyncpg.exceptions.UndefinedTableError: relation "topup_packages" does not exist
```

The tables existed. The connection had forgotten where to look. asyncpg's pool
calls `Connection.reset()` when a connection is **released**, which executes
`RESET ALL` — wiping any session-level `SET` the `init` callback made. `init`
runs once per connection at creation; the first release undoes it.

The fix is `setup=`, which runs on every acquire, after the reset:

```python
pool = await asyncpg.create_pool(url, min_size=2, max_size=4, setup=_init)
```

This is not an obscure detail — it is precisely why `acquire_with_tenant` in this
codebase uses `SET LOCAL` inside a transaction rather than a plain `SET`, and
that module's docstring already said so:

> SET LOCAL inside the wrapping transaction guarantees the GUC is dropped when
> the connection is returned to the pool, so a later consumer of the same
> connection never inherits stale tenant context.

I had read that and still wrote `init=`. Worth writing down.

### 7.3 The off-by-one in my own fake

One unit test failed on first run — the one from §2.3 that asserts the credit is
gated on the ledger result. Not because the service was wrong; because my fake
was.

The fake dedupes on `args[6]`, taken from the reversal statement's parameter
list. But the credit statement hardcodes `kind` as a literal:

```sql
VALUES ($1, $2, 'topup', $3, $4, $5, $6, $7)
```

so it binds one fewer parameter, and `args[6]` is the **note text**, not the
event id. The fake was deduping on `"top-up 250 minutes"`, which is identical
across redeliveries by coincidence and different across packages by coincidence.

```python
if "INSERT INTO billing_ledger" in sql:
    # The credit path hardcodes kind='topup' in the statement and so
    # binds one fewer parameter than the reversal path. Getting this
    # offset wrong is how a fake ends up dedup'ing on the note text.
    if "'topup'" in sql:
        kind, delta, event_id = "topup", args[2], args[5]
    else:
        kind, delta, event_id = args[2], args[3], args[6]
```

Worth including because it is the argument for behavioural tests over
source-scanning ones. A test that asserted `"ON CONFLICT" in source` would have
passed with the fake broken, the service correct, and nobody any the wiser about
either.

---

## §8. The silent zero-row UPDATE, again

This codebase has already lost data to this exact shape once. From report 12:
the test-call transcript flush targeted a row id that did not exist, the UPDATE
matched nothing, and **a zero-row UPDATE is a success in PostgreSQL.** No error.
No exception. Transcripts silently absent.

`topup_orders` and `billing_ledger` carry FORCE row-level security. A bare
`pool.acquire()` has no tenant GUC set, so the policy evaluates to NULL, no row
is visible, and any UPDATE touches nothing — and reports success.

The first draft of `topup_service.py` had exactly that in two places:
`attach_session` and `mark_failed` both used `self._pool.acquire()` directly.
`attach_session` is the write that links an order to its checkout session. If it
silently does nothing, **the payment can never be matched to the order** and the
minutes are never credited, for a payment that succeeded.

Every write now goes through the RLS-aware helper, and the module docstring
states why so it survives the next edit:

```
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
```

And `attach_session` no longer trusts the absence of an error:

```python
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
```

with a helper that reads asyncpg's command tag:

```python
def _rows_affected(tag: str) -> int:
    """asyncpg returns 'UPDATE 3'. Zero is the case worth catching."""
    try:
        return int(str(tag).strip().rsplit(" ", 1)[-1])
    except (ValueError, IndexError):
        return -1
```

Two tests hold the line:

```python
async def test_a_lost_session_link_raises_instead_of_passing_quietly():
    """A zero-row UPDATE is a SUCCESS in PostgreSQL. If the link between order
    and checkout session is lost, the payment can never be matched — and this
    codebase has already lost call transcripts to exactly that shape."""
    store = make_store(order=None)          # nothing to update
    svc = TopupService(FakePool(store))
    with pytest.raises(TopupError):
        await svc.attach_session("order-1", "cs_1")


def test_every_write_path_uses_the_rls_aware_connection():
    for name in ("create_order", "attach_session", "credit_paid_order",
                 "mark_failed", "reverse", "history", "ledger", "purchased_total"):
        src = inspect.getsource(getattr(TopupService, name))
        assert "acquire_with_tenant" in src, (
            f"{name} acquires a connection without setting the RLS context"
        )
```

The second is a source-scanning test, which I generally distrust (§7.3). It
earns its place here because the failure mode it guards is *invisible at
runtime*: a bare acquire produces no error, no wrong value, nothing to assert on
behaviourally — only a write that quietly did not happen.

The corresponding endpoint behaviour, when the link does fail:

```python
try:
    await svc.attach_session(str(order["id"]), session["session_id"])
except TopupError as e:
    logger.error(
        "topup_session_orphaned order=%s session=%s — a payment on this "
        "session cannot be matched to its order: %s",
        str(order["id"])[:8], session["session_id"], e,
    )
    raise HTTPException(
        status_code=500,
        detail="Could not start the purchase. Nothing has been charged.",
    )
```

The customer gets an error before reaching a payment page, so nothing is
charged. The order stays in the database, unlinked and pending, and the log line
carries both ids so it can be matched by hand. The alternative — swallowing it —
is a checkout that succeeds and credits nothing.

---

## §9. The frontend

`TopupCard`, 360 lines, mounted on `/billing` between the overage alerts and the
minutes tracker — the point on the page a customer reaches when they are about
to run out.

### 9.1 A double click is a second charge

```python
async function buy(pkg: TopupPackage) {
  if (pending) return; // the double-click guard — a second order is a second charge
  ...
}
```

plus `disabled={pending}` on every button in the grid, not just the clicked one.

This is not the usual double-submit concern where you get a duplicate row to
clean up. A second checkout session is a second real payment page, and if the
customer completes both, they are charged twice for two legitimate orders that
the idempotency guard will happily credit both of — correctly, because they are
different events for different payments.

`pending` never clears before `window.location.assign()` navigates away, so the
button stays locked through the redirect rather than flickering back to enabled
in the moment before the browser leaves.

### 9.2 Coming back from Stripe proves nothing

The redirect fires as soon as the card is accepted. The webhook that credits the
minutes arrives separately, on its own schedule, and may be a second or two
behind — or longer.

So the success path does not announce a number it has not seen. It polls:

```typescript
const balanceQ = useTopupBalance(confirming ? POLL_MS : undefined);
...
useEffect(() => {
  if (!confirming || balance == null) return;
  if (baseline.current === null) {
    baseline.current = balance.purchased_minutes;
    startedAt.current = Date.now();
    return;
  }
  if (creditHasLanded(baseline.current, balance)) {
    setConfirming(false);
    setCredited(true);
    qc.invalidateQueries({ queryKey: topupKeys.orders() });
    qc.invalidateQueries({ queryKey: ["billing"] });
    return;
  }
  if (Date.now() - startedAt.current > POLL_GIVE_UP_MS) {
    // Stop spinning forever. The payment may still be settling; saying so is
    // better than a spinner that never resolves or a success we can't see.
    setConfirming(false);
  }
}, [confirming, balance, qc]);
```

Three states, three different messages:

| State | What it says |
| --- | --- |
| polling (≤45s) | "Payment received — confirming with your bank and adding the minutes." |
| ledger moved | "Your minutes have been added and are ready to use." |
| gave up | "Your payment went through. The minutes are still being confirmed… Nothing further is needed from you." |

The third is the honest version of a case most implementations render as either
a permanent spinner or a false success.

### 9.3 Why `>` and not `!==`

```typescript
export function creditHasLanded(
  baselinePurchased: number | null,
  current: TopupBalance | null,
): boolean {
  if (baselinePurchased === null || !current) return false;
  return current.purchased_minutes > baselinePurchased;
}
```

`purchased_minutes` is the **sum of the ledger**, so it moves in both
directions. A refund processed in the same window moves it *down*. `!==` would
detect that as a change and announce "your minutes are ready" — a lie in the one
direction that matters. There is a test named for it:

```typescript
test("a refund landing in the same window is not a successful top-up", () => {
    assert.equal(creditHasLanded(600, balance({ purchased_minutes: 350 })), false);
});
```

### 9.4 Why not the shared `billingFetch`

The existing billing hooks go through a helper whose documented contract is:

> Public contract preserved: returns null on any error (network, 4xx, 5xx) so
> consuming pages render an honest empty state instead of throwing.

That is the right trade for a usage chart. It is the wrong one for a purchase: a
failed checkout would come back as `null`, indistinguishable from a success that
returned nothing. So purchases use `api.request` directly, errors throw, and the
button can say what went wrong. The reasoning is at the top of `topup-api.ts` so
nobody "fixes" it later for consistency.

### 9.5 The rules are functions, not ternaries

Four decisions were extracted out of the render tree into named, exported,
tested functions: `canTopUp`, `isLowBalance`, `creditHasLanded`, `formatMoney`.

```typescript
/**
 * An unlimited plan must never be offered a top-up.
 *
 * `minutes_allocated <= 0` is the unlimited sentinel across this system. The
 * backend refuses to add minutes to such a tenant, so showing them a Buy
 * button offers a purchase that would take their money and change nothing.
 */
export function canTopUp(balance: TopupBalance | null): boolean {
  if (!balance) return false;
  return !balance.unlimited;
}
```

Note `canTopUp(null) === false`. The catalogue is hidden until the balance is
known, because Buy buttons that appear and then vanish once the balance loads
are worse than a beat of nothing.

`isLowBalance` turns the remaining-minutes figure amber under 15%, guards the
divide-by-zero on a zero allocation, and never fires for an unlimited plan.
Eighteen frontend tests cover all four, including:

```typescript
test("prices render from minor units, not major", () => {
    // The API returns 2500 for £25. Rendering it as £2,500 is the kind of
    // mistake a customer notices before we do.
    assert.equal(formatMoney(2500, "GBP"), "£25.00");
});

test("every order status the backend defines has a label and a tone", () => {
    // The backend CHECK constraint allows exactly these six. A status with no
    // entry renders as a raw enum value in front of a paying customer.
    ...
});
```

That last one is a direct link between the SQL `CHECK` constraint in §4.1 and
the UI: six allowed statuses, six labels, six tones, asserted.

### 9.6 Three distinct failure banners

§9 asks for "clear failure and retry guidance". "Something went wrong" is not
guidance. There are three, and they say different things because the situations
are different:

**Checkout could not start** — `start.isError`:
> "We could not start the purchase, so nothing has been charged. Please try
> again in a moment."

**Checkout cancelled** — returning with `?topup=cancelled`:
> "Checkout cancelled. You have not been charged."

**Payments not configured** — the backend reported `mock_mode`:
> "Card payments are not configured on this environment yet, so this purchase
> cannot be completed. Nothing has been charged."

All three say explicitly that no money moved, because that is the first thing
somebody wants to know.

The third one is a fix I made after checking production, and it is worth its own
paragraph. In mock mode the backend returns a **placeholder checkout URL**.
Following it would land the customer on `/billing?topup=success` for a payment
that never happened, where the card would poll for 45 seconds waiting for a
webhook that is never coming, and then show "still being confirmed" — about a
purchase that did not occur. The card now stops at the mock response and says
so:

```typescript
// Mock mode means no payment provider is configured on this
// environment. Following the fake checkout URL would land the customer
// on a success page for a payment that never happened and then spin
// forever waiting for a webhook that will never arrive. Say so instead.
if (result?.mock_mode) {
  setMockNotice(result.message ?? "Card payments are not configured …");
  setChosen(null);
  return;
}
```

### 9.7 The history list

Below the catalogue, the last six orders with a status chip each — `Awaiting
payment`, `Added`, `Payment failed`, `Cancelled`, `Refunded`, `Disputed`.

The pending label is deliberately "Awaiting payment" rather than "Pending" or
"Processing", and there is a test for it:

```typescript
test("a pending order says it is unpaid, not that it failed", () => {
    // A customer who abandoned checkout should see that nothing was charged.
    assert.equal(ORDER_STATUS_LABEL.pending, "Awaiting payment");
});
```

A customer who abandoned a checkout comes back to a row that plainly means *this
did not complete and you were not charged*, rather than a row that reads like a
payment stuck in limbo.

### 9.8 The balance header

```
Minutes remaining
      1,350
900 of 2,250 used · 250 topped up
```

`2,250` is the plan's 2,000 plus a 250-minute top-up; `1,350` is what is left
after 900 minutes of calls. The remaining figure turns amber under 15% of the
allocation. The "250 topped up" clause only renders when
`purchased_minutes > 0`, so the number that explains where the extra minutes
came from appears exactly when there is something to explain.

The important property is that this figure comes from the **same computation the
dialler gates on** — see §11.2 — rather than being re-derived. The endpoint says
so:

```python
"""The same computation the dialler gates on.

Reading from ``minutes_quota`` rather than re-deriving it is the point: a
balance screen that disagrees with the thing that blocks calls is worse
than no balance screen, because it is believed.
"""
```

### 9.9 `useSearchParams` needs a Suspense boundary

Next's App Router requires it for a component that reads the query string. The
mount is wrapped, with the reason recorded inline:

```tsx
<OverageAlertsCard alerts={overage} usage={usage} />
{/* Suspense: TopupCard reads the ?topup= return param via
    useSearchParams, which Next requires a boundary for. */}
<Suspense fallback={null}>
  <TopupCard />
</Suspense>
```

---

## §10. The receipt and the reconciliation view

Two §9 backend items that are easy to skip and were not.

### 10.1 The receipt cannot fail the webhook

```python
async def _send_topup_receipt(self, session: Dict) -> None:
    """Confirm the purchase to the customer (goals.md §9).

    NEVER RAISES. The minutes are already credited and committed by the
    time this runs. Letting a mail-provider outage propagate would fail the
    webhook, release the claim, and have Stripe retry an event whose credit
    has already happened — a loop of 500s over an email that could simply
    be sent later. A failed receipt is logged and dropped.
    """
```

The interaction with §2.5 is the whole point. Because a raised exception now
*releases the claim* and invites a retry, an exception from the email step would
produce an endless retry loop over an event whose money has already moved. The
receipt is cosmetic; the credit is not. So it swallows, and the log line says
exactly that:

```python
except Exception as e:  # noqa: BLE001
    logger.error(
        "topup receipt failed (minutes ARE credited, this is cosmetic): %s", e,
    )
```

It is also sent **only when `credited is True`**, so a redelivery does not send a
second receipt for one payment:

```python
credited = await topups.credit_paid_order(...)
if credited:
    # Only on a real credit, so a redelivery does not send a second
    # receipt for one payment.
    await self._send_topup_receipt(data)
```

Recipient resolution tries the Stripe `customer_details.email` first (the address
the customer actually typed at checkout), then falls back to the account owner.
If neither exists it logs a warning and returns — a receipt with nowhere to go
is not worth failing over, but it is worth noticing:

```python
logger.warning(
    "topup_receipt_no_recipient tenant=%s — minutes credited, "
    "no address to confirm to", str(tenant_id)[:8],
)
```

An audit-log entry is written alongside, so the credit is visible in the audit
trail as well as the ledger.

### 10.2 Reconciliation reads the ledger, not the orders

```
GET /admin/billing/reconciliation          → JSON, rows + totals
GET /admin/billing/reconciliation?fmt=csv  → CSV download
```

Guarded by `require_platform_admin`, because reading across every tenant is the
entire point of the view and a tenant-scoped permission would defeat it.

The choice that matters is *which table*:

```python
"""Cross-tenant ledger for admin reconciliation (goals.md §9).

Deliberately reads the LEDGER and not ``topup_orders``: the ledger is
what actually moved, and an order can sit in a state that never became
money. Reconciling against intent rather than record is how a set of
books stops matching the payment provider's.
"""
```

An order can be `pending` forever. It can be `failed`. Neither is money.
Reconciling against orders means reconciling against what people *tried* to buy.

Each row carries the tenant, the business name, the signed minutes and amount,
the package, **both** provider ids and the order status — enough to line up
against Stripe's own export row by row. The JSON form adds totals so the top of
the page can be compared against the Stripe dashboard without adding up a
thousand rows:

```python
"totals": {
    "rows":             len(records),
    "minutes_sold":     sum(r["minutes_delta"] for r in records if r["minutes_delta"] > 0),
    "minutes_reversed": sum(-r["minutes_delta"] for r in records if r["minutes_delta"] < 0),
    "gross_cents":      gross,
    "refunded_cents":   refunded,
    "net_cents":        gross - refunded,
}
```

Sold and reversed are reported separately rather than netted, because "we sold
40,000 minutes and reversed 3,000" and "we sold 37,000 minutes" are different
facts and only one of them is a question worth asking.

The CSV writer names its columns explicitly when there are no rows, so an empty
export is a valid file with headers rather than a zero-byte download.

---

## §11. Verification

### 11.1 Twenty invariants against real PostgreSQL

`backend/scripts/verify_topup_ledger.py`, 336 lines. Design constraints:

**It touches nothing real.** Everything runs in a throwaway schema named
`topup_verify_<pid>`, placed first on the connection's `search_path` so every
unqualified table name in the service resolves there — including a shadow
`tenants` and a shadow `calls`. Created at the start, dropped at the end. No
production table is read or written, and no migration is applied to the real
schema.

**The DDL is not copied.** This is the part I would call the most important
design decision in the harness. A verification script that re-declares the
tables proves that *its copy* of the schema works. So the migration's own
`upgrade()` is executed through a shim:

```python
class _Op:
    @staticmethod
    def execute(stmt):
        statements.append(str(getattr(stmt, "text", stmt)))

import alembic
real_op = alembic.op
alembic.op = _Op
try:
    spec.loader.exec_module(mig)
    mig.op = _Op
    mig.upgrade()
finally:
    alembic.op = real_op

for stmt in statements:
    await conn.execute(stmt)
```

Seventeen statements, captured from the real migration and replayed into the
scratch schema. A copy would drift; this cannot.

**It fails loudly.** Non-zero exit on the first failed invariant, and each check
prints the actual numbers rather than just PASS.

The full output is in Appendix C. The headline lines:

```
PASS  a paid checkout credits its minutes                    — 1000 -> 1250
PASS  the top-up reaches the gate that blocks calls          — remaining 50 -> 300
PASS  a redelivered webhook credits nothing                  — 1250 -> 1250
PASS  the database refuses a duplicate event id
PASS  ten simultaneous deliveries credit exactly once        — credited=1 errors=0 1250 -> 1850
PASS  an unlimited tenant is not given a cap                 — minutes_allocated=0
PASS  a refund takes the minutes back                        — 1850 -> 1600
PASS  the original purchase row is untouched
```

### 11.2 The acceptance criterion asserted through the gate itself

§9's last acceptance criterion is:

> New balance is reflected in call quota enforcement.

There are two ways to "verify" that. The weak way is to read
`tenants.minutes_allocated` after the credit and observe that it went up — which
proves an UPDATE ran, and nothing about enforcement. Per §4.2, this is exactly
where a plausible implementation writes the wrong column and passes.

The harness instead creates a shadow `calls` table, burns 950 minutes against a
1000-minute allocation, and calls **the actual function the dialler and the
start-campaign endpoint both gate on**:

```python
from app.domain.services.minutes_quota import compute_minutes_status

async with pool.acquire() as c:
    q_before = await compute_minutes_status(c, TENANT)
check("the quota gate sees the tenant nearly out",
      q_before.remaining_minutes == 50 and not q_before.exhausted, ...)

# ... the credit happens ...

async with pool.acquire() as c:
    q_after = await compute_minutes_status(c, TENANT)
check("the top-up reaches the gate that blocks calls",
      q_after.remaining_minutes == q_before.remaining_minutes + 250, ...)
```

```
PASS  the quota gate sees the tenant nearly out       — remaining=50
PASS  the top-up reaches the gate that blocks calls   — remaining 50 -> 300
```

A tenant with 50 minutes left, one 250-minute purchase, 300 minutes left
**according to the code that decides whether a call may be placed.** That is
what "tenant minutes" means in the request, and it is the one result in this
report I would not have accepted second-hand.

### 11.3 Unit tests

**36 backend**, split by what they protect:

`test_topup_service.py` — 21 tests:

- redelivery credits once; the ledger refuses a repeat even when the order reads
  unpaid; the `ON CONFLICT`/`RETURNING` structure
- unknown session credits nothing; abandoned checkout writes no ledger row;
  `mark_failed` refuses a non-failure status
- unlimited tenant not capped, money still recorded
- refund is a new negative row; chargeback floored at zero; unpaid order not
  reversible; reverse refuses a bad kind and refuses with nothing to match on
- unknown package refused before an order exists; price and minutes come from
  the package and `create_order` cannot accept an amount; the open-order flood
  cap
- lost session link raises; `_rows_affected` parses the command tag; every write
  path uses the RLS-aware connection; the catalogue read is the only plain
  acquire

`test_billing_topup_webhook_routing.py` — 15 tests:

- a top-up is recognised; a subscription is not; `metadata: None` is not;
  charge events are claimed
- unsettled payment defers without crediting; a settled one credits with
  **Stripe's** event id (not a locally generated one, which would differ per
  redelivery and dedupe nothing); a missing event id still yields a stable key
- partial refund does not reverse; full refund does; a dispute reverses as a
  dispute
- expired → `cancelled`; async failure → `failed`
- a failed credit releases the claim; a top-up never reaches the subscription
  handler; a subscription still reaches its own

**18 frontend** in `topup-api.test.ts`, covering the four extracted rules and
the status-map completeness.

### 11.4 The full gate

```
11 failed, 5657 passed, 16 skipped, 1134 warnings, 36 errors in 187.98s
```

Baseline before this work: **11 failed, 5618 passed.**

Same eleven failures, +39 passing. Zero regressions.

The eleven are the long-standing httpx/starlette TestClient incompatibility
(#79), rooted in the production venv not matching `requirements.txt` (#100), plus
one file-permission assertion that fails on the server because
`install-services.sh` is mode `100644` in git. The 36 collection errors are the
known missing `fakeredis` declaration. All pre-existing, all tracked, none
touched by this change.

One process note. My first gate run reported **14** failures, and I nearly
recorded that as a regression. It was not: earlier in the session I had copied
`/opt/talky/backend/.env` into the scratch worktree for an import smoke test, and
three environment-sensitive tests changed behaviour as a result — including
`test_gate_refuses_missing_stripe_key_in_prod`, which is precisely the sort of
name that looks like it must be my fault. Removing the copied `.env` (which
should not have been sitting in `/tmp` anyway) restored the exact baseline. The
lesson is narrow and worth keeping: **a test harness contaminated with
production configuration produces failures that read like real ones.**

Frontend: `tsc --noEmit` clean, 18/18.

---

## §12. Deployment record

Five commits shipped, `d42156dd` → `41326b94`. Three are this work:

| SHA | Subject |
| --- | --- |
| `d35373aa` | `feat(billing): minute top-ups that credit exactly once` |
| `4dde68f5` | `feat(billing): the Top up minutes card on the Billing page` |
| `41326b94` | `fix(billing): say when payments are not configured instead of faking success` |

The other two — `74ab9e69` and `3db2344d` — are the §7/§11 frontend work from
report 12 that was committed but had not yet reached production. They went out in
the same pull.

**Order of operations**, which matters:

1. Push to `origin/main`.
2. `git pull --ff-only origin main` on `/opt/talky` — code on disk, **old code
   still running**.
3. `alembic upgrade head` — schema in place before any new code executes.
4. Import smoke on the prod checkout: 6 new routes enumerated.
5. `systemctl restart talky-api`.
6. Verify.

Migration before restart, not after. The reverse order gives a window where the
new Billing card calls tables that do not exist.

**Migration result:**

```
INFO  [alembic.runtime.migration] Running upgrade 0020_contact_and_lead_capture
      -> 0021_billing_topup, minute top-ups: packages, orders, and an
      immutable ledger (goals.md §9)
```

**Post-migration verification:**

```
   code    | minutes | price_cents | currency
-----------+---------+-------------+----------
 mins_250  |     250 |        2500 | GBP
 mins_600  |     600 |        5400 | GBP
 mins_1500 |    1500 |       12000 | GBP

         indexname
---------------------------
 billing_ledger_pkey
 idx_billing_ledger_event          ← total, not partial (§7.1)
 idx_billing_ledger_tenant
 idx_topup_orders_session
 idx_topup_orders_tenant
 topup_orders_pkey
 topup_packages_code_key
 topup_packages_pkey

   tablename    |           policyname
----------------+---------------------------------
 topup_orders   | topup_orders_tenant_isolation
 billing_ledger | billing_ledger_tenant_isolation

    version_num
--------------------
 0021_billing_topup

 ledger_rows | orders | active_packages
-------------+--------+-----------------
           0 |      0 |               3
```

**Post-restart verification:**

```
health:200
topups-packages (unauth, expect 401/403): 401
admin-recon    (unauth, expect 401/403): 401
--- errors since restart ---
-- No entries --
```

The 401s are the useful signal: **401 and not 404** proves the routes are
registered *and* gated. A 404 would mean the router never mounted; a 200 would
mean an authorisation hole.

All four services active. Frontend goes out via Vercel auto-deploy from `main`.

**Rollback:** `git checkout d42156dd` + restart restores the code. The schema can
stay — the tables are unreferenced by the old code. `alembic downgrade` works
while the ledger is empty and refuses once it is not (§4.5).

Scratch worktree used for testing (`/tmp/wstx9`) removed, along with the `.env`
copy inside it.

---

## §13. The Stripe blocker

**Production has no `STRIPE_SECRET_KEY` and no `STRIPE_WEBHOOK_SECRET`.**

Verified by counting matching lines on the server, never reading values. Both
count zero. `STRIPE_MOCK_MODE` is not set either, but it does not need to be —
`_should_use_mock_mode()` returns True whenever the secret key is absent.

The consequences, precisely:

| Path | Behaviour in mock mode |
| --- | --- |
| `create_topup_checkout_session` | returns a placeholder URL, `mock_mode: true` |
| `handle_webhook` | returns `{"status": "ignored", "reason": "mock_mode"}` **before signature verification** — every webhook is a no-op |
| Credit | never runs |
| Receipt | never runs |

So the flow is built, tested, proven and deployed, and it **cannot take a
penny.** The card says so (§9.6) rather than performing a purchase.

**To switch it on, two environment variables and one dashboard change:**

1. Set `STRIPE_SECRET_KEY` and `STRIPE_WEBHOOK_SECRET` in
   `/opt/talky/backend/.env`, then restart `talky-api`.
2. In the Stripe dashboard, ensure the webhook endpoint pointing at
   `POST /api/v1/billing/webhooks` subscribes **all five**:

   ```
   checkout.session.completed
   checkout.session.expired
   checkout.session.async_payment_failed
   charge.refunded
   charge.dispute.created
   ```

   The first is the only one an existing subscription-only configuration is
   likely to have. Without the other four, expiries, failures, refunds and
   disputes silently never process — orders sit `pending` forever and refunded
   minutes are never clawed back.

3. Replace the seeded placeholder prices if they are not the intended
   commercial ones. That is an UPDATE on `topup_packages`, not a deploy.

The first real purchase should be watched in the journal. The log lines to look
for, in order:

```
topup_order_created tenant=… order=… package=… minutes=…
topup_checkout_created session=… order=… tenant=… minutes=…
topup_credited tenant=… order=… minutes=… event=…
topup_receipt_sent tenant=… minutes=…
```

and the ones that mean something needs attention:

```
topup_webhook_unknown_session …   a payment with no order (§4.1)
topup_session_orphaned …          the link write was lost (§8)
topup_on_unlimited_tenant …       something was sold that should not be (§5)
topup_partial_refund …            needs a human (§6.3)
webhook claim released after handler failure …   Stripe will retry (§2.5)
```

---

## §14. What I did not do, and the box left unticked

### 14.1 Deliberately out of scope

**Minute expiry.** `topup_packages.expires_days` exists in the schema and the UI
renders "Never expires" or "Valid for N days", but nothing enforces expiry — all
three seeded packages are NULL. Enforcing it means tracking which minutes came
from which bundle and consuming them in an order, which is a materially bigger
feature than §9 asks for. The column is there so adding it later is not a
migration.

**Per-tenant pricing.** `topup_packages` is global. A tenant-scoped catalogue is
a different feature; building the abstraction now on speculation would be a
wrong abstraction cheaply acquired.

**Auto top-up.** Charging a stored card automatically at a low balance is a
recurring-payment product with its own consent and failure semantics. Not asked
for, not built.

**A tenant-facing ledger view.** The `/billing/topups/ledger` endpoint is live
and returns signed movements, but the card shows the order history rather than
the raw ledger. Orders are what a customer recognises. The endpoint is there for
when a ledger view is wanted.

### 14.2 The acceptance criterion I did not tick

> - [ ] Tenant billing records remain isolated.

The RLS policies are written, in the canonical shape, with `WITH CHECK`, on both
tenant-scoped tables — confirmed present in production (§12). Every tenant-facing
query is scoped through `acquire_with_tenant`.

And it is still not ticked, because per the finding recorded as #80, **the
production database role is a superuser with `BYPASSRLS`.** No policy on any
table in this system is currently enforced. A bogus tenant GUC returns other
tenants' rows.

So the honest statement is: the policies are correct and will work the moment
the role is fixed; the isolation is currently provided by the application layer
alone. Ticking the box would claim a database-enforced guarantee the database is
not providing. The goals file carries the reason inline so it is not re-litigated:

```markdown
- [ ] Tenant billing records remain isolated. — RLS policies written and every
      query is tenant-scoped, but this stays OPEN until #80: the production DB
      role is a superuser with BYPASSRLS, so no policy on any table is actually
      enforced. Ticking this would be claiming an isolation the database is not
      currently providing.
```

### 14.3 Open, and known

| # | Item | Note |
| --- | --- | --- |
| — | Subscription handlers do not release their claim on failure | Scoped my fix to the top-up branch; making theirs retryable would send duplicate receipt emails. Needs a decision, not a patch. |
| — | No live end-to-end payment | Cannot be done without Stripe keys (§13). Everything up to the provider boundary is proven. |
| #80 | RLS inert in production | Blocks §14.2. |
| #79/#100 | 11 gate failures / prod venv drift | Pre-existing, untouched. |
| — | `fakeredis` undeclared | 36 collection errors. Pre-existing. |

---

## §15. Where the eleven P0s stand

```
[x] Generic lead-generation prompt connected to live campaign runtime
[x] Prompt version and hash visible in call logs
[x] Expanded contact fields available to the agent
[x] Structured interested-lead information capture
[x] Per-conversation review and feedback storage
[x] Security moved from Settings to the main sidebar
[ ] Inbound campaign MVP
[x] AI Options and AI Summary information tooltips
[x] Billing minute top-up MVP          ← this report
[ ] Client-management and multi-tenant validation with 200 test clients
[ ] End-to-end test and controlled release
```

**8 of 11.**

The three remaining are not independent. The inbound campaign MVP is the only
one that is new construction — there is no inbound backend at all today, and it
is the largest single piece of unbuilt work left. The 200-tenant validation
harness is a test of everything already built, and it is also the natural place
to prove #80 once the database role is fixed, since cross-tenant isolation at
200 tenants is exactly what that harness would exercise. The end-to-end
controlled release depends on both.

Which suggests the order: **inbound campaign, then fix the DB role, then the
200-tenant harness (which validates both), then the release.** The harness is
worth more after #80 than before it, because run today it would report isolation
passing for the wrong reason.

---

## Appendix A — file by file

### `backend/Alembic/versions/0021_billing_topup.py` (246)

Three tables, five indexes, two RLS policies, three seeded packages. 17
statements. `down_revision = "0020_contact_and_lead_capture"`. Revision id is 18
characters — under the `alembic_version.version_num` `varchar(32)` limit, which
a previous migration in this sequence exceeded at 36 and had to be renamed.

The module docstring carries the three failure modes the schema rules out —
double credit, early credit, silent credit — so the *why* travels with the DDL.

### `backend/app/domain/services/topup_service.py` (417)

| Method | Purpose |
| --- | --- |
| `packages()` | the active catalogue; the only plain `pool.acquire()` in the class |
| `_package(conn, code)` | resolve a code, raise `TopupError` if unknown/inactive |
| `create_order(...)` | snapshot price + minutes, cap open orders at 20/hour |
| `attach_session(...)` | link order → session; raises on zero rows (§8) |
| `credit_paid_order(...)` | the invariant (§2) |
| `mark_failed(...)` | terminal non-payment; no ledger entry |
| `reverse(...)` | refund/dispute; new negative row; floored at zero |
| `history(tenant, limit)` | tenant-scoped order list |
| `ledger(tenant, limit)` | tenant-scoped signed movements |
| `reconciliation(since, until, limit)` | cross-tenant, admin only |
| `purchased_total(tenant)` | ledger sum; the frontend's proof-of-credit signal |

`MAX_OPEN_ORDERS = 20` per rolling hour. Not a security control — a bound on how
much junk a stuck client loop can create.

### `backend/app/api/v1/endpoints/billing_topups.py` (393)

Five tenant routes + one admin. `require_permission(BILLING_READ)` for reads,
`BILLING_UPDATE` for checkout, `require_platform_admin` for reconciliation.

The checkout endpoint's failure ordering is the interesting part: order created →
Stripe session → link. Each stage has a distinct failure message, and every one
of them tells the customer nothing was charged, because at each of those points
nothing was.

### `backend/app/domain/services/billing_service.py` (+309)

| Added | Purpose |
| --- | --- |
| `create_topup_checkout_session` | `mode="payment"`, inline `price_data`, `purpose` metadata on session **and** PaymentIntent |
| `_is_topup_event` | the routing predicate (§3) |
| `_handle_topup_event` | the five-event dispatch |
| `_release_webhook_claim` | undo a claim whose handler failed (§2.5) |
| `_send_topup_receipt` | never raises (§10.1) |
| `_TOPUP_SESSION_EVENTS` / `_TOPUP_CHARGE_EVENTS` | the event sets |

Inline `price_data` means no Stripe Price object needs to pre-exist, and the
customer is charged exactly what the order row says — the same snapshot, carried
through.

### `backend/scripts/verify_topup_ledger.py` (336)

Covered in §11.1. `DATABASE_URL` first so it can be pointed at a scratch database
without the app's full configuration present.

### `Talk-Leee/src/lib/topup-api.ts` (216)

Four query hooks, one mutation, four exported rules, two status maps. The
mutation deliberately bypasses `billingFetch` (§9.4).

### `Talk-Leee/src/components/billing/topup-card.tsx` (360)

Covered in §9.

---

## Appendix B — the API surface

```
GET  /api/v1/billing/topups/packages
     → [{code, name, minutes, price_cents, currency, expires_days,
         price_per_minute_cents}]
     auth: BILLING_READ

GET  /api/v1/billing/topups/balance
     → {allocated, used_minutes, remaining_minutes, unlimited,
        exhausted, purchased_minutes}
     auth: BILLING_READ
     note: allocated/used/remaining come from compute_minutes_status —
           the same function the dialler gates on.

POST /api/v1/billing/topups/checkout
     ← {package_code, success_url?, cancel_url?}
     → {order_id, session_id, checkout_url, minutes, price_cents,
        currency, mock_mode, message?}
     auth: BILLING_UPDATE
     note: package_code only. No amount is accepted from the client.

GET  /api/v1/billing/topups/orders?limit=25
     → {orders: [{id, package_code, minutes, price_cents, currency,
                  status, created_at, paid_at}]}
     auth: BILLING_READ

GET  /api/v1/billing/topups/ledger?limit=100
     → {entries: [{kind, minutes_delta, amount_cents, currency,
                   note, created_at}]}
     auth: BILLING_READ

GET  /api/v1/admin/billing/reconciliation?since=&until=&limit=&fmt=json|csv
     → {entries: [...], totals: {rows, minutes_sold, minutes_reversed,
                                 gross_cents, refunded_cents, net_cents}}
     auth: require_platform_admin

POST /api/v1/billing/webhooks              (existing, extended)
     Stripe-signed. Now routes minute-top-up events before the
     subscription handler table.
```

---

## Appendix C — verbatim verification output

```
scratch schema: topup_verify_3093278

  applied 17 statements from the migration

  PASS  the migration seeds a closed catalogue  — mins_250, mins_600, mins_1500
  PASS  bundles get cheaper per minute as they get bigger
  PASS  an order snapshots minutes and price from the package
  PASS  an unknown package is refused
  PASS  the quota gate sees the tenant nearly out  — remaining=50
  PASS  a paid checkout credits its minutes  — 1000 -> 1250
  PASS  the top-up reaches the gate that blocks calls  — remaining 50 -> 300
  PASS  a redelivered webhook credits nothing  — 1250 -> 1250
  PASS  the database refuses a duplicate event id
  PASS  ten simultaneous deliveries credit exactly once  — credited=1 errors=0 1250 -> 1850
  PASS  an unlimited tenant is not given a cap  — minutes_allocated=0
  PASS  the money is still recorded for them
  PASS  a refund takes the minutes back  — 1850 -> 1600
  PASS  the refund is its own negative row
  PASS  the original purchase row is untouched  — a refund must not rewrite history
  PASS  a package cannot sell zero minutes
  PASS  an order cannot carry a negative price
  PASS  an order cannot hold an invented status
  PASS  the ledger cannot hold an invented kind
  PASS  the ledger sums to the net minutes bought  — 250 + 600 - 250 = 600

scratch schema topup_verify_3093278 removed

20 passed, 0 failed
```

The warning emitted mid-run, which is the unlimited-tenant branch working:

```
topup_on_unlimited_tenant tenant=61f07631 order=90e759a5 minutes=250 —
money recorded, allocation untouched. This tenant should not have been
offered a top-up.
```

Backend gate:

```
11 failed, 5657 passed, 16 skipped, 1134 warnings, 36 errors in 187.98s
```

Frontend:

```
ℹ tests 18
ℹ pass 18
ℹ fail 0
```

---

## Appendix D — runbook

**Re-run the invariant proof** (safe against any database — creates and drops its
own schema, touches nothing else):

```bash
cd /opt/talky/backend
export DATABASE_URL=$(grep -m1 '^DATABASE_URL=' .env | cut -d= -f2- | sed 's/+asyncpg//')
venv/bin/python scripts/verify_topup_ledger.py
```

**Reconcile against Stripe:**

```
GET /api/v1/admin/billing/reconciliation?since=2026-09-01&fmt=csv
```

Compare `gross_cents` / `refunded_cents` against the Stripe dashboard for the
same window. Every row carries `provider_event_id` and `provider_payment_id`, so
a mismatch is traceable to a single event.

**Reprice a bundle** (no deploy):

```sql
UPDATE topup_packages SET price_cents = 2200 WHERE code = 'mins_250';
```

Existing orders are unaffected — they snapshotted their price (§4.1).

**Retire a bundle** (no deploy, no deletion):

```sql
UPDATE topup_packages SET is_active = FALSE WHERE code = 'mins_250';
```

It disappears from the catalogue and `create_order` refuses it. Historical orders
and ledger rows keep referring to it by code.

**Add a bundle:**

```sql
INSERT INTO topup_packages (code, name, minutes, price_cents, currency, sort_order)
VALUES ('mins_3000', '3,000 minutes', 3000, 216000, 'GBP', 4);
```

Keep the per-minute rate below the tier beneath it, or the assertion in §4.4
fails on the next verification run — which is the point of the assertion.

**Investigate a payment that credited nothing:**

1. `journalctl -u talky-api | grep topup_` around the payment time.
2. `topup_webhook_unknown_session` → the order was never created or never
   linked; check `topup_orders` for a `pending` row with a NULL
   `provider_session_id`.
3. `topup_duplicate_event` → it credited on an earlier delivery. Check
   `billing_ledger` for the event id.
4. `topup_on_unlimited_tenant` → working as designed; the tenant had no cap.
5. Nothing at all → check `mock_mode` (§13). The webhook returns before it logs.

**Roll back the code** (schema can stay):

```bash
cd /opt/talky && git checkout d42156dd
echo "$SUDO_PW" | sudo -S -p "" systemctl restart talky-api
```

**Roll back the schema** (only while the ledger is empty):

```bash
cd /opt/talky/backend
export DATABASE_URL=…
venv/bin/python -m alembic downgrade 0020_contact_and_lead_capture
```

It will refuse, loudly, if any money has moved.

---

*End of report 13.*
