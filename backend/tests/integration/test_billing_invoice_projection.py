"""CP04 provider capture, immutability and access on migrated PostgreSQL.

Only synthetic SDK-shaped reads are used; this suite never contacts Stripe.
"""

from copy import deepcopy
from uuid import UUID, uuid4

import asyncpg
import pytest
import pytest_asyncio

from app.core.db_utils import acquire_with_tenant
from app.domain.services.billing_invoice_projection import store_snapshot
from app.domain.services.billing_state_events import (
    BillingStateBusy,
    BillingStateReviewRequired,
    apply_billing_event,
    refresh_invoice_details,
)
from app.domain.services.billing_webhooks import BillingWebhookProcessor
from tests.integration.test_billing_checkout import _tenant, checkout_db  # noqa: F401
from tests.integration.test_billing_price_options import _scoped, billing_db  # noqa: F401
from tests.integration.test_billing_state_events import _paid, state_db  # noqa: F401

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def invoice_db(state_db):  # noqa: F811
    fixture = state_db
    fixture.pages = {}
    read_object = fixture.billing._stripe_call

    async def read(resource, method, *args, **kwargs):
        if method == "list":
            key = (resource, kwargs.get("starting_after"))
            response = fixture.pages.get(key, {"has_more": False, "data": []})
            if isinstance(response, Exception):
                raise response
            return deepcopy(response)
        return await read_object(resource, method, *args, **kwargs)

    fixture.billing._stripe_call = read
    yield fixture


async def make_invoice(fixture):
    receipt, subscription, session, invoice = await _paid(fixture)
    invoice.update(
        {
            "number": "SYNTHETIC-CP04",
            "subtotal": 1900,
            "total": 2052,
            "starting_balance": -152,
            "amount_remaining": 0,
            "total_taxes": [{"amount": 342, "tax_behavior": "exclusive"}],
            "total_discount_amounts": [{"amount": 190, "discount": "di_synthetic"}],
            "period_start": 1700000000,
            "period_end": 1702592000,
            "invoice_pdf": "https://pay.stripe.com/invoice/synthetic/pdf",
            "hosted_invoice_url": "https://invoice.stripe.com/i/synthetic",
        }
    )
    invoice["status_transitions"]["finalized_at"] = 1700000000
    invoice["lines"]["data"][0].update(
        {
            "description": "Original purchased annual terms",
            "amount": 1900,
            "period": {"start": 1700000000, "end": 1731622400},
        }
    )
    fixture.objects[("Invoice", invoice["id"])] = invoice
    return receipt, subscription, session, invoice


async def observe(fixture, invoice, reference, *, kind="invoice.paid"):
    async with acquire_with_tenant(fixture.service_pool, None) as conn:
        return await apply_billing_event(
            conn, fixture.billing, kind, invoice, source_reference=reference
        )


async def snapshots(fixture, invoice):
    async with acquire_with_tenant(fixture.service_pool, None) as conn:
        return await conn.fetch(
            "SELECT s.* FROM invoice_snapshots s JOIN invoices i ON i.id=s.invoice_id WHERE i.stripe_invoice_id=$1 ORDER BY s.id",
            invoice["id"],
        )


async def test_snapshot_records_actual_financial_facts_and_survives_catalog_change(invoice_db):
    fixture = invoice_db
    _, _, _, invoice = await make_invoice(fixture)
    await observe(fixture, invoice, "evt_" + uuid4().hex)
    first = (await snapshots(fixture, invoice))[0]
    assert first["projection"]["detail_status"] == "complete"
    assert first["projection"]["total"] == 2052 and first["projection"]["amount_due"] == 1900
    async with acquire_with_tenant(fixture.pool, None) as conn:
        await conn.execute(
            "UPDATE plans SET name='Changed future plan',price=999,minutes=9999 WHERE id=$1",
            fixture.plan,
        )
        await conn.execute("UPDATE tenants SET minutes_used=71 WHERE id=$1", fixture.tenants[0])
    await observe(fixture, invoice, "evt_" + uuid4().hex)
    current = (await snapshots(fixture, invoice))[-1]
    assert (
        current["projection"]["line_items"][0]["description"] == "Original purchased annual terms"
    )
    assert current["projection"]["subtotal"] == first["projection"]["subtotal"] == 1900
    assert (await _tenant(fixture))["minutes_used"] == 71


async def test_identical_event_dedups_but_a_b_a_observations_retain_latest_identity(invoice_db):
    fixture = invoice_db
    _, _, _, invoice = await make_invoice(fixture)
    fixture.pages[("CreditNote", None)] = TimeoutError()
    event = "evt_" + uuid4().hex
    await observe(fixture, invoice, event)
    await observe(fixture, invoice, event)
    assert len(await snapshots(fixture, invoice)) == 1
    fixture.pages.clear()
    await observe(fixture, invoice, "evt_" + uuid4().hex)
    fixture.pages[("CreditNote", None)] = TimeoutError()
    await observe(fixture, invoice, "evt_" + uuid4().hex)
    rows = await snapshots(fixture, invoice)
    assert [row["projection"]["detail_status"] for row in rows] == ["partial", "complete", "partial"]
    assert all(row["projection"]["amount_remaining"] == 0 for row in rows)


async def test_incomplete_lines_do_not_activate_first_payment_but_renewal_detail_can_be_partial(
    invoice_db,
):
    fixture = invoice_db
    receipt, _, _, invoice = await make_invoice(fixture)
    current = fixture.objects[("Invoice", invoice["id"])]
    current["lines"]["has_more"] = True
    cursor = current["lines"]["data"][-1]["id"]
    fixture.pages[("InvoiceLineItem", cursor)] = TimeoutError()
    before = await _tenant(fixture)
    with pytest.raises(BillingStateBusy, match="invoice_lines_pending"):
        await observe(fixture, invoice, "evt_" + uuid4().hex)
    assert (await _tenant(fixture))["subscription_status"] == before["subscription_status"]
    assert not await snapshots(fixture, invoice)
    current["lines"]["has_more"] = False
    await observe(fixture, invoice, "evt_" + uuid4().hex)
    current["lines"]["has_more"] = True
    await observe(fixture, invoice, "evt_" + uuid4().hex)
    assert (await snapshots(fixture, invoice))[-1]["projection"]["detail_status"] == "partial"
    async with acquire_with_tenant(fixture.service_pool, None) as conn:
        assert (
            await conn.fetchval(
                "SELECT status FROM billing_checkout_attempts WHERE id=$1",
                UUID(receipt["request_id"]),
            )
            == "completed"
        )


async def test_refresh_is_capture_only_and_can_repair_partial_detail(invoice_db):
    fixture = invoice_db
    _, _, _, invoice = await make_invoice(fixture)
    fixture.pages[("CreditNote", None)] = TimeoutError()
    await observe(fixture, invoice, "evt_" + uuid4().hex)
    assert (await snapshots(fixture, invoice))[-1]["projection"]["detail_status"] == "partial"
    fixture.pages.clear()
    before = dict(await _tenant(fixture))
    async with acquire_with_tenant(fixture.service_pool, None) as conn:
        result = await refresh_invoice_details(
            conn, fixture.billing, "invoice.paid", invoice, "observation:" + uuid4().hex
        )
    assert result["notifications"] == [] and dict(await _tenant(fixture)) == before
    assert (await snapshots(fixture, invoice))[-1]["projection"]["detail_status"] == "complete"
    assert (await snapshots(fixture, invoice))[-1]["projection"]["amount_remaining"] == 0


@pytest.mark.parametrize("wrong_price", [False, True])
async def test_paginated_first_payment_verifies_every_subscription_line(invoice_db, wrong_price):
    fixture = invoice_db
    _, _, _, invoice = await make_invoice(fixture)
    current = fixture.objects[("Invoice", invoice["id"])]
    first = current["lines"]["data"][0]
    second = deepcopy(first)
    second["id"] = "il_second_" + uuid4().hex
    if wrong_price:
        second["pricing"]["price_details"]["price"] = "price_unrelated"
    current["lines"]["has_more"] = True
    fixture.pages[("InvoiceLineItem", first["id"])] = {"has_more": False, "data": [second]}
    before = await _tenant(fixture)
    if wrong_price:
        with pytest.raises(BillingStateReviewRequired, match="invoice_line_price_mismatch"):
            await observe(fixture, invoice, "evt_" + uuid4().hex)
        assert (await _tenant(fixture))["subscription_status"] == before["subscription_status"]
        assert not await snapshots(fixture, invoice)
    else:
        await observe(fixture, invoice, "evt_" + uuid4().hex)
        assert len((await snapshots(fixture, invoice))[-1]["projection"]["line_items"]) == 2
        assert (await _tenant(fixture))["subscription_status"] == "active"


async def test_credit_note_event_appends_details_without_changing_access_or_notifying(invoice_db):
    fixture = invoice_db
    _, _, _, invoice = await make_invoice(fixture)
    await observe(fixture, invoice, "evt_" + uuid4().hex)
    note = {
        "id": "cn_" + uuid4().hex,
        "invoice": invoice["id"],
        "customer": invoice["customer"],
        "livemode": False,
        "currency": "usd",
        "amount": 500,
        "status": "issued",
        "type": "post_payment",
        "post_payment_amount": 500,
        "pre_payment_amount": 0,
        "refunds": [],
    }
    fixture.objects[("CreditNote", note["id"])] = note
    fixture.pages[("CreditNote", None)] = {"data": [note], "has_more": False}
    before = dict(await _tenant(fixture))
    result = await observe(fixture, note, "evt_" + uuid4().hex, kind="credit_note.created")
    assert result["notifications"] == [] and dict(await _tenant(fixture)) == before
    assert (await snapshots(fixture, invoice))[-1]["projection"]["credits"][0]["id"] == note["id"]
    note["customer"] = "cus_other_tenant"
    with pytest.raises(BillingStateReviewRequired, match="invoice_adjustment_customer_mismatch"):
        await observe(fixture, note, "evt_" + uuid4().hex, kind="credit_note.updated")


async def test_new_capture_before_payment_does_not_suppress_first_durable_receipt(invoice_db):
    fixture = invoice_db
    _, _, _, invoice = await make_invoice(fixture)

    async def handler(conn, event):
        result = await apply_billing_event(
            conn,
            fixture.billing,
            event["type"],
            event["data"]["object"],
            source_reference=event["id"],
        )
        # Isolate the durable receipt from CP06's known owner-role lookup gap;
        # this synthetic address is never sent to an email provider.
        for notice in result["notifications"]:
            notice["recipient"] = "synthetic-billing@example.test"
        return result

    processor = BillingWebhookProcessor(fixture.service_pool)
    for kind in ("invoice.updated", "invoice.paid", "invoice.paid"):
        event = {
            "id": "evt_" + uuid4().hex,
            "type": kind,
            "livemode": False,
            "data": {"object": invoice},
        }
        await processor.process(event, handler)
    async with acquire_with_tenant(fixture.service_pool, None) as conn:
        rows = await conn.fetch(
            "SELECT status FROM billing_webhook_notifications WHERE delivery_key=$1",
            f"invoice:{invoice['id']}:paid",
        )
        assert [row["status"] for row in rows] == ["pending"]
        assert (
            await conn.fetchval(
                "SELECT notification_history_known FROM invoices WHERE stripe_invoice_id=$1",
                invoice["id"],
            )
            is True
        )


async def test_snapshot_capture_and_invoice_roll_back_with_receipt_transaction(invoice_db):
    fixture = invoice_db
    _, _, _, invoice = await make_invoice(fixture)
    with pytest.raises(RuntimeError, match="synthetic_failure"):
        async with acquire_with_tenant(fixture.service_pool, None) as conn:
            await apply_billing_event(
                conn,
                fixture.billing,
                "invoice.paid",
                invoice,
                source_reference="evt_" + uuid4().hex,
            )
            raise RuntimeError("synthetic_failure")
    assert not await snapshots(fixture, invoice)


async def test_snapshot_tenant_rls_binding_and_append_only_are_enforced(invoice_db):
    fixture = invoice_db
    _, _, _, invoice = await make_invoice(fixture)
    await observe(fixture, invoice, "evt_" + uuid4().hex)
    row = (await snapshots(fixture, invoice))[0]
    async with _scoped(fixture, fixture.tenants[0]) as conn:
        flags = await conn.fetchrow(
            "SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user"
        )
        assert not flags["rolsuper"] and not flags["rolbypassrls"]
        assert (
            await conn.fetchval("SELECT count(*) FROM invoice_snapshots WHERE id=$1", row["id"])
            == 1
        )
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with conn.transaction():
                await store_snapshot(
                    conn, row["invoice_id"], fixture.tenants[0], {"source_reference": "forged"}
                )
    async with _scoped(fixture, fixture.tenants[1]) as conn:
        assert (
            await conn.fetchval("SELECT count(*) FROM invoice_snapshots WHERE id=$1", row["id"])
            == 0
        )
    async with acquire_with_tenant(fixture.pool, None) as conn:
        for sql in (
            "UPDATE invoice_snapshots SET projection='{}'::jsonb WHERE id=$1",
            "DELETE FROM invoice_snapshots WHERE id=$1",
        ):
            with pytest.raises(asyncpg.RaiseError, match="append-only"):
                async with conn.transaction():
                    await conn.execute(sql, row["id"])
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            async with conn.transaction():
                await store_snapshot(
                    conn,
                    row["invoice_id"],
                    fixture.tenants[1],
                    {"source_reference": "wrong_tenant"},
                )
