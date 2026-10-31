"""bughunt billing-money-credentials-9 (residual) - the Stripe customer race and
the blocking SDK calls in BillingService.

Root cause 1 (race). ``create_or_get_customer`` read the tenant's
``stripe_customer_id``, created a Stripe customer when it was missing and wrote
it back, with nothing serializing the three steps. Two first checkouts that
overlap (two workers, two replicas, or two tabs once the SDK call no longer
blocks the loop) each create a customer; the tenant ends up bound to the second
while the first checkout session belongs to the first, and the top-up webhook
refuses to credit a session whose customer differs from
``tenants.stripe_customer_id`` (billing_topup_events), so a paid top-up sits in
needs_review without credit.

Root cause 2 (event loop). ``stripe.Customer.create`` and
``stripe.checkout.Session.create`` were called directly from the coroutine, so
one slow Stripe response froze every request on that worker.

These tests use a fake ``stripe`` module (no network) and an in-memory stand-in
for the tenants row and ``pg_advisory_xact_lock``. The last two tests run the
same checks on a real PostgreSQL when BH2_BILLING_PG_DSN names a local bh2_*
database; they are skipped otherwise.
"""
from __future__ import annotations

import asyncio
import os
import threading
import time
from types import ModuleType, SimpleNamespace
from urllib.parse import urlparse
from uuid import uuid4

import pytest

from app.domain.services import billing_service as bs
from app.domain.services.billing_service import BillingService

TENANT = "11111111-1111-4111-8111-111111111111"
EMAIL = "owner@example.test"
BUSINESS = "Acme Dental"


# --------------------------------------------------------------------------
# fake stripe SDK
# --------------------------------------------------------------------------

class FakeStripe(ModuleType):
    """Just enough of the SDK for BillingService.

    ``_stripe_call`` goes through ``StripeClient(...).v1.<resource>.create``;
    those calls are recorded with via="client". The module-level resources the
    pre-fix code called straight from the coroutine are recorded with
    via="direct", so a regression to them is visible in the assertions.
    Every create sleeps like a network round trip to widen the race window.
    """

    def __init__(self, create_delay: float = 0.3, on_create=None):
        super().__init__("stripe")
        self.api_key = None
        self.create_delay = create_delay
        self.on_create = on_create
        self.heartbeat = None
        self.calls: list[dict] = []
        self._mutex = threading.Lock()
        self._seq = 0
        fake = self

        class _Resource:
            def __init__(self, kind):
                self.kind = kind

            def create(self, params=None, options=None):
                return fake._create(self.kind, "client", dict(params or {}), dict(options or {}))

        class RequestsClient:
            def __init__(self, timeout=None):
                self.timeout = timeout

            def close(self):
                pass

        class StripeClient:
            def __init__(self, api_key, http_client=None, max_network_retries=None):
                self.v1 = SimpleNamespace(
                    customers=_Resource("customer"),
                    checkout=SimpleNamespace(sessions=_Resource("session")),
                )

        self.RequestsClient = RequestsClient
        self.StripeClient = StripeClient
        self.Customer = SimpleNamespace(create=lambda **kw: fake._create("customer", "direct", kw, {}))
        self.checkout = SimpleNamespace(
            Session=SimpleNamespace(create=lambda **kw: fake._create("session", "direct", kw, {}))
        )

    def _create(self, kind, via, params, options):
        ticks_before = self.heartbeat() if self.heartbeat else None
        with self._mutex:
            self._seq += 1
            seq = self._seq
            record = {
                "kind": kind, "via": via, "thread": threading.get_ident(),
                "params": params, "options": options, "id": None,
                "ticks_before": ticks_before, "ticks_after": None,
            }
            self.calls.append(record)
        time.sleep(self.create_delay)
        if self.on_create:
            self.on_create(kind)
        record["id"] = f"{'cus' if kind == 'customer' else 'cs'}_fake_{seq}"
        record["ticks_after"] = self.heartbeat() if self.heartbeat else None
        return SimpleNamespace(id=record["id"], url=f"https://checkout.stripe.test/{seq}")

    def created(self, kind):
        return [c for c in self.calls if c["kind"] == kind]


# --------------------------------------------------------------------------
# in-memory stand-in for the tenants row + pg_advisory_xact_lock
# --------------------------------------------------------------------------

class FakeTenantsDb:
    """One tenants row per id. Writes become visible at COMMIT (READ COMMITTED);
    ``pg_advisory_xact_lock`` is held until the transaction ends. It offers the
    asyncpg pool surface the fixed code uses AND the PostgREST-style
    ``table()`` chain the pre-fix code used, both over the same rows, so the
    same scenario can be replayed against the old implementation."""

    def __init__(self, tenants=(TENANT,), advisory=True):
        self.rows = {t: {"stripe_customer_id": None} for t in tenants}
        self.advisory = advisory
        self.mutex = threading.Lock()
        self.locks: dict[str, threading.Lock] = {}
        self.pool = _FakePool(self)

    # pre-fix surface -------------------------------------------------------
    def table(self, name):
        assert name == "tenants"
        return _LegacyQuery(self)

    # helpers -----------------------------------------------------------------
    def bound(self, tenant=TENANT):
        with self.mutex:
            return self.rows[tenant]["stripe_customer_id"]

    def bind(self, customer_id, tenant=TENANT):
        with self.mutex:
            self.rows[tenant]["stripe_customer_id"] = customer_id


class _LegacyQuery:
    def __init__(self, db):
        self.db, self.op, self.values, self.key = db, "select", None, None

    def select(self, *_a, **_k):
        self.op = "select"
        return self

    def update(self, values):
        self.op, self.values = "update", values
        return self

    def eq(self, column, value):
        assert column == "id"
        self.key = str(value)
        return self

    def single(self):
        return self

    def execute(self):
        with self.db.mutex:
            row = self.db.rows.get(self.key)
            if self.op == "select":
                return SimpleNamespace(data=dict(row) if row else None)
            if row is not None:
                row.update(self.values)
            return SimpleNamespace(data=[dict(row)] if row else [])


class _FakePool:
    def __init__(self, db):
        self.db = db

    def acquire(self, timeout=None):
        return _Acquire(self.db)


class _Acquire:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        self.conn = _FakeConn(self.db)
        return self.conn

    async def __aexit__(self, *_exc):
        self.conn.release_locks()  # a pooled connection never keeps a lock
        return False


class _Txn:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, _exc, _tb):
        conn = self.conn
        if exc_type is None:
            with conn.db.mutex:
                for tenant, customer in conn.pending.items():
                    conn.db.rows[tenant]["stripe_customer_id"] = customer
        conn.pending.clear()
        conn.release_locks()  # advisory xact locks end with the transaction
        return False


class _FakeConn:
    def __init__(self, db):
        self.db, self.pending, self.held = db, {}, []

    def transaction(self):
        return _Txn(self)

    def release_locks(self):
        while self.held:
            self.held.pop().release()

    async def execute(self, sql, *args):
        text = " ".join(sql.split())
        if text.startswith("SET LOCAL"):
            return "SET"
        if "pg_advisory_xact_lock" in text:
            assert "stripe_customer:" in text, text
            if self.db.advisory:
                with self.db.mutex:
                    lock = self.db.locks.setdefault(str(args[0]), threading.Lock())
                await asyncio.get_running_loop().run_in_executor(None, lock.acquire)
                self.held.append(lock)
            return "SELECT 1"
        if text.startswith("UPDATE tenants SET stripe_customer_id"):
            customer, tenant = args[0], str(args[1])
            # Honour the predicate only if the SQL actually carries it, so a
            # fix that drops the guard is caught instead of emulated.
            guarded = "AND COALESCE(stripe_customer_id, '') = ''" in text
            with self.db.mutex:
                row = self.db.rows.get(tenant)
                if row is None or (guarded and (row["stripe_customer_id"] or "") != ""):
                    return "UPDATE 0"
            self.pending[tenant] = customer
            return "UPDATE 1"
        raise AssertionError(f"unexpected SQL: {text}")

    async def fetchrow(self, sql, *args):
        text = " ".join(sql.split())
        assert text.startswith("SELECT stripe_customer_id FROM tenants WHERE id = $1"), text
        tenant = str(args[0])
        with self.db.mutex:
            row = self.db.rows.get(tenant)
            if row is None:
                return None
            current = dict(row)
        if tenant in self.pending:
            current["stripe_customer_id"] = self.pending[tenant]
        return current


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------

@pytest.fixture
def fake_stripe(monkeypatch):
    fake = FakeStripe()
    monkeypatch.setattr(bs, "stripe", fake)
    return fake


@pytest.fixture
def stripe_calls(monkeypatch):
    """Spy on the seam the brief names: every SDK call must pass through it."""
    seen = []
    original = BillingService._stripe_call

    async def spy(self, resource, method, *args, **kwargs):
        seen.append({"resource": resource, "method": method, "kwargs": dict(kwargs),
                     "thread": threading.get_ident()})
        return await original(self, resource, method, *args, **kwargs)

    monkeypatch.setattr(BillingService, "_stripe_call", spy)
    return seen


def service(monkeypatch, db, mode="test"):
    monkeypatch.delenv("STRIPE_BILLING_DISABLED", raising=False)
    if mode == "mock":
        monkeypatch.setenv("STRIPE_MOCK_MODE", "true")
        monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    else:
        monkeypatch.delenv("STRIPE_MOCK_MODE", raising=False)
        monkeypatch.setenv("STRIPE_SECRET_KEY", f"sk_{mode}_synthetic")
    svc = BillingService(db)
    assert svc.billing_mode == mode
    return svc


# --------------------------------------------------------------------------
# customer creation: behaviour that must not change
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_existing_customer_is_returned_without_touching_stripe(monkeypatch, fake_stripe):
    db = FakeTenantsDb()
    db.bind("cus_existing")
    svc = service(monkeypatch, db)
    result = await svc.create_or_get_customer(TENANT, EMAIL, BUSINESS)
    assert result == {"customer_id": "cus_existing", "created": False}
    assert fake_stripe.calls == []


@pytest.mark.asyncio
async def test_mock_mode_customer_is_deterministic_persisted_and_never_calls_stripe(
    monkeypatch, fake_stripe
):
    db = FakeTenantsDb()
    svc = service(monkeypatch, db, mode="mock")
    first = await svc.create_or_get_customer(TENANT, EMAIL, BUSINESS)
    assert first == {"customer_id": f"cus_mock_{TENANT[:8]}", "created": True}
    assert db.bound() == f"cus_mock_{TENANT[:8]}"
    second = await svc.create_or_get_customer(TENANT, EMAIL, BUSINESS)
    assert second == {"customer_id": f"cus_mock_{TENANT[:8]}", "created": False}
    assert fake_stripe.calls == []


@pytest.mark.asyncio
async def test_unknown_tenant_creates_no_stripe_customer(monkeypatch, fake_stripe):
    svc = service(monkeypatch, FakeTenantsDb(tenants=()))
    with pytest.raises(ValueError):
        await svc.create_or_get_customer(TENANT, EMAIL, BUSINESS)
    assert fake_stripe.calls == []


# --------------------------------------------------------------------------
# (3) the SDK calls go through _stripe_call, off the loop, with idempotency keys
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_customer_create_goes_through_stripe_call_with_the_tenant_idempotency_key(
    monkeypatch, fake_stripe, stripe_calls
):
    db = FakeTenantsDb()
    svc = service(monkeypatch, db)
    result = await svc.create_or_get_customer(TENANT, EMAIL, BUSINESS)

    assert [(c["resource"], c["method"]) for c in stripe_calls] == [("Customer", "create")]
    kwargs = stripe_calls[0]["kwargs"]
    assert kwargs["idempotency_key"] == f"cust-{TENANT}"
    assert kwargs["metadata"] == {"tenant_id": TENANT}
    assert (kwargs["email"], kwargs["name"]) == (EMAIL, BUSINESS)

    (call,) = fake_stripe.calls
    assert call["via"] == "client"
    assert call["options"] == {"idempotency_key": f"cust-{TENANT}"}
    assert "idempotency_key" not in call["params"]
    assert call["thread"] != threading.get_ident(), "the SDK ran on the event loop thread"
    assert result == {"customer_id": call["id"], "created": True}
    assert db.bound() == call["id"]


@pytest.mark.asyncio
async def test_topup_session_goes_through_stripe_call_with_the_order_idempotency_key(
    monkeypatch, fake_stripe, stripe_calls
):
    db = FakeTenantsDb()
    db.bind("cus_existing")
    svc = service(monkeypatch, db)
    result = await svc.create_topup_checkout_session(
        tenant_id=TENANT, email=EMAIL, order_id="order-123", minutes=250, price_cents=2500,
        currency="GBP", product_name="250 minutes", success_url="https://app.example/billing?topup=success",
        cancel_url="https://app.example/billing?topup=cancelled", business_name=BUSINESS,
    )

    assert [(c["resource"], c["method"]) for c in stripe_calls] == [("checkout.Session", "create")]
    assert stripe_calls[0]["kwargs"]["idempotency_key"] == "topup-order-123"

    (call,) = fake_stripe.calls
    assert call["via"] == "client" and call["kind"] == "session"
    assert call["options"] == {"idempotency_key": "topup-order-123"}
    assert call["thread"] != threading.get_ident(), "the SDK ran on the event loop thread"
    params = call["params"]
    assert params["mode"] == "payment"
    assert params["customer"] == "cus_existing"
    assert params["payment_method_types"] == ["card"]
    assert params["client_reference_id"] == "order-123"
    assert params["metadata"]["purpose"] == "minute_topup"
    assert params["payment_intent_data"] == {"metadata": params["metadata"]}
    assert result == {"session_id": call["id"], "checkout_url": "https://checkout.stripe.test/1", "mock_mode": False}


@pytest.mark.asyncio
async def test_stripe_calls_do_not_freeze_the_event_loop(monkeypatch, fake_stripe):
    """First-ever top-up: customer create then session create, 0.3 s each. A
    heartbeat task must keep ticking while each SDK call is in flight; a call
    made directly on the loop freezes it, so the tick count does not move."""
    db = FakeTenantsDb()
    svc = service(monkeypatch, db)
    ticks = {"n": 0}
    stop = asyncio.Event()

    async def beat():
        while not stop.is_set():
            ticks["n"] += 1
            await asyncio.sleep(0.005)

    fake_stripe.heartbeat = lambda: ticks["n"]
    beater = asyncio.create_task(beat())
    try:
        await svc.create_topup_checkout_session(
            tenant_id=TENANT, email=EMAIL, order_id="order-9", minutes=100, price_cents=1000,
            currency="usd", product_name="100 minutes", success_url="https://app.example/billing",
            cancel_url="https://app.example/billing", business_name=BUSINESS,
        )
    finally:
        stop.set()
        await beater

    assert len(fake_stripe.calls) == 2
    for call in fake_stripe.calls:
        moved = call["ticks_after"] - call["ticks_before"]
        assert moved >= 3, f"event loop was frozen during stripe {call['kind']} create ({call['via']} call)"


# --------------------------------------------------------------------------
# (2) the race
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_concurrent_first_checkouts_in_one_process_create_one_customer(monkeypatch, fake_stripe):
    db = FakeTenantsDb()
    svc = service(monkeypatch, db)
    results = await asyncio.gather(*(svc.create_or_get_customer(TENANT, EMAIL, BUSINESS) for _ in range(4)))

    assert len(fake_stripe.created("customer")) == 1
    ids = {r["customer_id"] for r in results}
    assert ids == {fake_stripe.calls[0]["id"]}
    assert sorted(r["created"] for r in results) == [False, False, False, True]
    assert db.bound() == ids.pop()


@pytest.mark.asyncio
async def test_the_harness_sees_the_race_when_the_lock_is_removed(monkeypatch, fake_stripe):
    """Control: the same scenario with the advisory lock disabled DOES make
    Stripe create several customers, so the test above is sensitive to the lock
    rather than passing by timing luck. The guarded UPDATE is the second line of
    defence: the losers get an error instead of silently rebinding the tenant."""
    db = FakeTenantsDb(advisory=False)
    svc = service(monkeypatch, db)
    results = await asyncio.gather(
        *(svc.create_or_get_customer(TENANT, EMAIL, BUSINESS) for _ in range(4)), return_exceptions=True
    )

    assert len(fake_stripe.created("customer")) > 1
    winners = [r for r in results if isinstance(r, dict)]
    assert len(winners) == 1
    assert any(isinstance(r, RuntimeError) for r in results)
    assert db.bound() == winners[0]["customer_id"]


def test_two_workers_racing_the_first_checkout_create_one_customer(monkeypatch, fake_stripe):
    """Two uvicorn workers / replicas: separate event loops, one database. The
    pre-fix code is race-prone here regardless of the SDK call, because the
    read and the write are separate statements with nothing between them."""
    db = FakeTenantsDb()
    services = [service(monkeypatch, db), service(monkeypatch, db)]
    barrier = threading.Barrier(2)
    results, errors = [], []

    def worker(svc):
        async def go():
            return await svc.create_or_get_customer(TENANT, EMAIL, BUSINESS)

        try:
            barrier.wait(timeout=5)
            results.append(asyncio.run(go()))
        except BaseException as exc:  # noqa: BLE001 - reported below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(s,)) for s in services]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)
    assert not any(t.is_alive() for t in threads), "a worker is stuck waiting for the lock"
    assert errors == []

    assert len(fake_stripe.created("customer")) == 1, (
        f"{len(fake_stripe.created('customer'))} Stripe customers were created for one tenant"
    )
    assert len({r["customer_id"] for r in results}) == 1
    assert sorted(r["created"] for r in results) == [False, True]
    assert db.bound() == results[0]["customer_id"]


@pytest.mark.asyncio
async def test_a_failed_customer_create_binds_nothing_and_releases_the_lock(monkeypatch, fake_stripe):
    db = FakeTenantsDb()
    svc = service(monkeypatch, db)
    boom = {"armed": True}

    def fail_first(_kind):
        if boom["armed"]:
            boom["armed"] = False
            raise RuntimeError("stripe went away")

    fake_stripe.on_create = fail_first
    with pytest.raises(RuntimeError, match="stripe went away"):
        await svc.create_or_get_customer(TENANT, EMAIL, BUSINESS)
    assert db.bound() is None
    assert db.locks[TENANT].locked() is False, "the per-tenant lock leaked past the failed transaction"

    again = await asyncio.wait_for(svc.create_or_get_customer(TENANT, EMAIL, BUSINESS), 5)
    assert again["created"] is True and db.bound() == again["customer_id"]


@pytest.mark.asyncio
async def test_a_customer_bound_by_another_writer_during_the_stripe_call_is_not_overwritten(
    monkeypatch, fake_stripe
):
    """The subscription checkout binds a customer under the tenant row lock,
    not under this per-tenant advisory lock. If it commits while our Stripe
    call is in flight, the stored binding must win: overwriting it would leave
    that purchase's session pointing at a customer the tenant no longer has."""
    db = FakeTenantsDb()
    svc = service(monkeypatch, db)
    fake_stripe.on_create = lambda _kind: db.bind("cus_from_subscription_checkout")

    with pytest.raises(RuntimeError, match="binding changed"):
        await svc.create_or_get_customer(TENANT, EMAIL, BUSINESS)
    assert db.bound() == "cus_from_subscription_checkout"


# --------------------------------------------------------------------------
# the same checks on a real PostgreSQL (opt-in, local bh2_* databases only)
# --------------------------------------------------------------------------

PG_DSN = os.getenv("BH2_BILLING_PG_DSN")
needs_pg = pytest.mark.skipif(not PG_DSN, reason="set BH2_BILLING_PG_DSN to a local bh2_* database")


def _assert_disposable(dsn):
    parsed = urlparse(dsn)
    assert parsed.hostname in {"localhost", "127.0.0.1"}, "real-Postgres checks run on localhost only"
    assert parsed.path.lstrip("/").startswith("bh2_"), "real-Postgres checks run on bh2_* databases only"


async def _pg_tenant(pool):
    tenant = str(uuid4())
    async with pool.acquire() as conn:
        await conn.execute("INSERT INTO tenants(id, business_name) VALUES($1::uuid, $2)", tenant, "bh2-race-test")
    return tenant


async def _pg_drop(pool, tenant):
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM tenants WHERE id = $1::uuid", tenant)


@needs_pg
@pytest.mark.asyncio
async def test_real_postgres_concurrent_first_checkouts_create_one_customer(monkeypatch):
    import asyncpg

    _assert_disposable(PG_DSN)
    pool = await asyncpg.create_pool(PG_DSN, min_size=1, max_size=8)
    tenant = await _pg_tenant(pool)
    fake = FakeStripe(create_delay=0.25)
    monkeypatch.setattr(bs, "stripe", fake)
    try:
        svcs = [service(monkeypatch, SimpleNamespace(pool=pool)) for _ in range(5)]
        results = await asyncio.gather(*(s.create_or_get_customer(tenant, EMAIL, BUSINESS) for s in svcs))
        async with pool.acquire() as conn:
            stored = await conn.fetchval("SELECT stripe_customer_id FROM tenants WHERE id = $1::uuid", tenant)
        assert len(fake.created("customer")) == 1
        assert {r["customer_id"] for r in results} == {stored}
        assert sorted(r["created"] for r in results) == [False] * 4 + [True]
        assert fake.calls[0]["options"] == {"idempotency_key": f"cust-{tenant}"}
    finally:
        await _pg_drop(pool, tenant)
        await pool.close()


@needs_pg
@pytest.mark.asyncio
async def test_real_postgres_keeps_a_customer_bound_by_another_writer(monkeypatch):
    import asyncpg

    _assert_disposable(PG_DSN)
    pool = await asyncpg.create_pool(PG_DSN, min_size=1, max_size=4)
    tenant = await _pg_tenant(pool)
    loop = asyncio.get_running_loop()

    async def other_writer_commits():
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE tenants SET stripe_customer_id = 'cus_other_writer' WHERE id = $1::uuid", tenant
            )

    fake = FakeStripe(
        create_delay=0.05,
        on_create=lambda _k: asyncio.run_coroutine_threadsafe(other_writer_commits(), loop).result(10),
    )
    monkeypatch.setattr(bs, "stripe", fake)
    try:
        svc = service(monkeypatch, SimpleNamespace(pool=pool))
        with pytest.raises(RuntimeError, match="binding changed"):
            await svc.create_or_get_customer(tenant, EMAIL, BUSINESS)
        async with pool.acquire() as conn:
            stored = await conn.fetchval("SELECT stripe_customer_id FROM tenants WHERE id = $1::uuid", tenant)
        assert stored == "cus_other_writer"
    finally:
        await _pg_drop(pool, tenant)
        await pool.close()
