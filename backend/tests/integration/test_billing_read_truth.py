"""Tenant-scoped invoice and meter reads on the actual migrated PostgreSQL DB.

Provider objects are synthetic. No payment, refund, email or telephone request
is sent; immutable observations and their synthetic parent rows are retained.
"""

import json
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints import billing as api
from app.api.v1.endpoints import billing_topups
from app.core.db_utils import acquire_with_tenant
from app.domain.services.billing_service import BillingService
from tests.integration.test_billing_invoice_projection import (
    invoice_db,  # noqa: F401
    state_db,  # noqa: F401
    checkout_db,  # noqa: F401
    billing_db,  # noqa: F401
    make_invoice,
    observe,
    snapshots,
)

pytestmark = pytest.mark.integration


def user(fixture, index=0):
    return SimpleNamespace(tenant_id=str(fixture.tenants[index]))


def billing(fixture):
    service = BillingService.__new__(BillingService)
    service.db_client = SimpleNamespace(pool=fixture.service_pool)
    return service


async def test_list_detail_keep_historical_financial_facts_and_latest_capture(
    invoice_db,  # noqa: F811
):  # noqa: F811
    fixture = invoice_db
    _, _, session, invoice = await make_invoice(fixture)
    await observe(fixture, session, "evt_" + uuid4().hex, kind="checkout.session.completed")
    invoice.update({"status": "open", "amount_paid": 0, "amount_remaining": 1900})
    invoice["status_transitions"]["paid_at"] = None
    await observe(fixture, invoice, "evt_" + uuid4().hex, kind="invoice.updated")
    invoice_id = str((await snapshots(fixture, invoice))[0]["invoice_id"])
    first = await api.get_invoice(invoice_id, user(fixture), fixture.service_pool)
    assert first["subtotal"] == 1900 and first["total"] == 2052 and first["amount_due"] == 1900
    assert first["taxes"][0]["amount"] == 342 and first["discounts"][0]["amount"] == 190
    assert first["line_items"][0]["description"] == "Original purchased annual terms"
    async with acquire_with_tenant(fixture.pool, None) as conn:
        await conn.execute(
            "UPDATE plans SET name='Future catalog edit',minutes=99999,price=999 WHERE id=$1",
            fixture.plan,
        )
    assert await api.get_invoice(invoice_id, user(fixture), fixture.service_pool) == first
    reference = "evt_" + uuid4().hex
    fixture.objects[("Invoice", invoice["id"])].update({"amount_paid": 1800, "amount_remaining": 100})
    await observe(fixture, invoice, reference, kind="invoice.updated")
    listed = await api.list_invoices(10, user(fixture), fixture.service_pool)
    latest = await api.get_invoice(invoice_id, user(fixture), fixture.service_pool)
    assert listed["invoices"] == [latest]
    assert latest["status"] == "open" and latest["amount_paid"] == 1800
    assert latest["amount_remaining"] == 100 and latest["source_reference"] == reference
    assert latest["detail_status"] == "complete" and latest["captured_at"] is not None
    output = os.environ.get("CP04_RECONCILIATION_OUTPUT")
    if output:
        # Explicitly opted-in synthetic evidence for the frontend acceptance
        # fixture. The provider is the in-process fake above, never live Stripe.
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        saved = (await snapshots(fixture, invoice))[-1]
        path.write_text(
            json.dumps(
                {
                    "scope": "Synthetic provider objects; actual PostgreSQL capture and API read; no live payment",
                    "provider_invoice": fixture.objects[("Invoice", invoice["id"])],
                    "stored_snapshot": dict(saved),
                    "api_invoice": latest,
                    "api_list": listed,
                },
                indent=2,
                default=str,
            )
            + "\n",
            encoding="utf-8",
        )


async def test_other_tenant_cannot_read_invoice_or_its_provider_documents(invoice_db):  # noqa: F811
    fixture = invoice_db
    _, _, _, invoice = await make_invoice(fixture)
    await observe(fixture, invoice, "evt_" + uuid4().hex)
    invoice_id = str((await snapshots(fixture, invoice))[0]["invoice_id"])
    assert await api.list_invoices(10, user(fixture, 1), fixture.service_pool) == {
        "invoices": [],
        "count": 0,
    }
    with pytest.raises(HTTPException) as exc:
        await api.get_invoice(invoice_id, user(fixture, 1), fixture.service_pool)
    assert exc.value.status_code == 404


async def test_legacy_invoice_without_snapshot_keeps_unknown_detail_null(invoice_db):  # noqa: F811
    fixture = invoice_db
    invoice_id = uuid4()
    async with acquire_with_tenant(fixture.pool, None) as conn:
        await conn.execute(
            """INSERT INTO invoices(id,tenant_id,stripe_invoice_id,status,amount_due,amount_paid,currency)
            VALUES($1,$2,$3,'open',1000,0,'jpy')""",
            invoice_id,
            fixture.tenants[0],
            "in_" + uuid4().hex,
        )
    result = await api.get_invoice(str(invoice_id), user(fixture), fixture.service_pool)
    assert result["amount_due"] == 1000 and result["currency_exponent"] == 0
    assert result["line_items"] is result["taxes"] is result["credits"] is result["refunds"] is None
    assert (
        result["subtotal"] is result["total"] is None and result["detail_status"] == "unavailable"
    )


async def seed_usage(fixture, *, allocated=350):
    parent, inbound, test, foreign = [uuid4() for _ in range(4)]
    outbound_campaign, inbound_campaign, foreign_campaign = [uuid4() for _ in range(3)]
    async with acquire_with_tenant(fixture.pool, None) as conn:
        await conn.execute(f'GRANT SELECT ON calls,call_legs TO "{fixture.role}"')
        await conn.execute(
            "UPDATE tenants SET minutes_allocated=$2 WHERE id=$1", fixture.tenants[0], allocated
        )
        if allocated == 0:
            # Unlimited is a recorded product entitlement, not a missing/default
            # tenant allocation. Keep this positive fixture explicit.
            await conn.execute("UPDATE plans SET minutes=0 WHERE id=$1", fixture.plan)
        await conn.executemany(
            "INSERT INTO campaigns(id,tenant_id,name,direction) VALUES($1,$2,'Synthetic metering',$3)",
            [
                (outbound_campaign, fixture.tenants[0], "outbound"),
                (inbound_campaign, fixture.tenants[0], "inbound"),
                (foreign_campaign, fixture.tenants[1], "outbound"),
            ],
        )
        await conn.executemany(
            """INSERT INTO calls(id,tenant_id,phone_number,status,outcome,duration_seconds,
            direction,billing_status,is_test,campaign_id) VALUES($1,$2,'+15550001111','ended','customer_hung_up',$3,$4,$5,$6,$7)""",
            [
                (parent, fixture.tenants[0], 61, "outbound", "none", False, outbound_campaign),
                (inbound, fixture.tenants[0], 59, "inbound", "finalized", False, inbound_campaign),
                (test, fixture.tenants[0], 600, "outbound", "none", True, outbound_campaign),
                (foreign, fixture.tenants[1], 3600, "outbound", "none", False, foreign_campaign),
            ],
        )
        await conn.executemany(
            """INSERT INTO call_legs(call_id,leg_type,duration_seconds,billing_status)
            VALUES($1,'transfer',$2,$3)""",
            [(inbound, 119, "finalized"), (inbound, 900, "held"), (test, 1000, "finalized")],
        )


@pytest.mark.parametrize("allocated", [350, 0])
async def test_daily_and_monthly_reads_include_only_settled_owned_seconds(
    invoice_db, allocated, monkeypatch  # noqa: F811
):  # noqa: F811
    fixture = invoice_db
    await seed_usage(fixture, allocated=allocated)
    usage = await api.get_usage_summary("minutes", user(fixture), billing(fixture))
    assert usage.total_used == 3 and usage.allocated == allocated
    assert usage.unlimited is (allocated == 0) and usage.overage == 0
    assert usage.remaining == (347 if allocated else 0)
    daily = await api.get_daily_usage(1, user(fixture), fixture.service_pool)
    assert daily[0]["secondsUsed"] == 239 and daily[0]["minutesUsed"] == 3
    assert daily[0]["totalCalls"] == 2
    assert await api.get_overage_alerts(user(fixture), billing(fixture), fixture.service_pool) == []
    monkeypatch.setattr(
        billing_topups,
        "get_container",
        lambda: SimpleNamespace(is_initialized=True, db_pool=fixture.service_pool),
    )
    balance = await billing_topups.get_balance(user(fixture))
    assert balance.used_minutes == usage.total_used and balance.allocated == usage.allocated
    assert balance.remaining_minutes == usage.remaining and balance.unlimited == usage.unlimited


async def test_meter_read_privilege_failure_is_unavailable_not_a_zero_balance(
    invoice_db,  # noqa: F811
):  # noqa: F811
    fixture = invoice_db
    await seed_usage(fixture)
    async with acquire_with_tenant(fixture.pool, None) as conn:
        await conn.execute(f'REVOKE SELECT ON calls FROM "{fixture.role}"')
    with pytest.raises(HTTPException) as exc:
        await api.get_usage_summary("minutes", user(fixture), billing(fixture))
    assert exc.value.status_code == 503 and exc.value.detail["code"] == "usage_unavailable"


async def test_missing_tenant_does_not_appear_as_unlimited(invoice_db):  # noqa: F811
    with pytest.raises(HTTPException) as exc:
        await api.get_usage_summary(
            "minutes", SimpleNamespace(tenant_id=str(uuid4())), billing(invoice_db)
        )
    assert exc.value.status_code == 503
