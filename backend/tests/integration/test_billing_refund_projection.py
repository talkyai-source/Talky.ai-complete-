"""Real RLS, immutable observations and financial separation; synthetic provider."""

import json
import os
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import asyncpg
import pytest

from app.core.db_utils import acquire_with_tenant
from app.domain.services.billing_webhooks import BillingWebhookReviewRequired
from app.domain.services.topup_service import TopupService
from tests.integration.test_billing_topup_events import (
    event,
    process,
    state,
    topup_db,  # noqa: F401
)
from tests.integration.test_billing_webhook_migration import webhook_db  # noqa: F401

pytestmark = pytest.mark.integration


async def test_real_ledger_rows_publish_one_contract_through_both_read_endpoints(
    topup_db,  # noqa: F811
    monkeypatch,
):
    from app.api.v1.endpoints import billing, billing_topups

    fixture = topup_db
    await setup_refund(fixture)
    await process(fixture, event(fixture, "ledger_reverse", "charge.refunded"))
    monkeypatch.setattr(
        billing_topups,
        "get_container",
        lambda: SimpleNamespace(
            is_initialized=True,
            db_pool=fixture.db.pool,
        ),
    )
    user = SimpleNamespace(tenant_id=str(fixture.db.tenant))
    adjustments = await billing.get_adjustments(user, fixture.db.pool)
    ledger = await billing_topups.list_ledger(100, user)
    assert adjustments == ledger["entries"] and len(adjustments) == 2
    assert {row["kind"] for row in adjustments} == {"topup", "refund"}
    assert sum(row["amount_cents"] for row in adjustments) == 0
    assert sum(row["minutes_delta"] for row in adjustments) == 0
    for row in adjustments:
        assert isinstance(row["id"], str) and row["id"].isdigit()
        assert row["order_id"] == str(fixture.order["id"])
        assert row["provider_payment_id"] == fixture.data.payment["id"]
        assert row["currency_exponent"] == 2 and row["currency"].lower() == "gbp"
        assert isinstance(row["created_at"], str) and row["created_at"].endswith("+00:00")
    foreign = SimpleNamespace(tenant_id=str(uuid4()))
    assert await billing.get_adjustments(foreign, fixture.db.pool) == []
    assert await billing_topups.list_ledger(100, foreign) == {"entries": []}
    output = os.environ.get("CP04_LEDGER_RECONCILIATION_OUTPUT")
    if output:
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "scope": "Synthetic provider payment/refund; actual PostgreSQL ledger and API reads; no live payment",
                    "api_adjustments": adjustments,
                    "api_ledger": ledger,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )


async def setup_refund(fixture, *, refund_status="pending", amount=2500, list_failure=False):
    await process(fixture, event(fixture, "purchase"))
    charge = fixture.data.charge
    charge.update(amount_refunded=amount, refunded=amount == 2500)
    refund = {
        "id": "re_" + uuid4().hex,
        "charge": charge["id"],
        "payment_intent": charge["payment_intent"],
        "currency": "gbp",
        "amount": amount,
        "status": refund_status,
        "created": 1700000000,
    }
    original = fixture.billing._stripe_call

    async def provider(resource, method, *args, **kwargs):
        if resource == "Refund":
            if method == "retrieve":
                assert args == (refund["id"],)
                return deepcopy(refund)
            assert method == "list" and kwargs["charge"] == charge["id"]
            if list_failure:
                raise TimeoutError("Synthetic interrupted provider read")
            return {"data": [deepcopy(refund)], "has_more": False}
        return await original(resource, method, *args, **kwargs)

    fixture.billing._stripe_call = provider
    return refund


def refund_event(fixture, refund, suffix):
    envelope = event(fixture, suffix)
    envelope["type"] = "refund.updated"
    envelope["data"]["object"] = deepcopy(refund)
    return envelope


async def test_partial_refund_status_does_not_apply_undefined_allocation_policy(
    topup_db,  # noqa: F811
):  # noqa: F811
    fixture = topup_db
    refund = await setup_refund(fixture, amount=500)
    before = await state(fixture)
    envelope = refund_event(fixture, refund, "status")
    assert (await process(fixture, envelope))["status"] == "handled"
    assert (await process(fixture, envelope))["status"] == "duplicate"
    after = await state(fixture)
    assert after["ledger"] == before["ledger"] and after["allocated"] == 1250
    assert after["order"] == "paid"
    orders = await TopupService(fixture.db.pool).history(str(fixture.db.tenant))
    details = orders[0]["refund_details"]
    assert details["detail_status"] == "complete"
    assert details["refunds"][0]["status"] == "pending" and details["refunds"][0]["amount"] == 500
    assert details["captured_at"] and details["source_event_id"] == envelope["id"]
    # The separate financial event is still fenced for policy review.
    with pytest.raises(BillingWebhookReviewRequired):
        await process(fixture, event(fixture, "partial", "charge.refunded"))


async def test_refund_status_changes_retained_without_second_ledger_reversal(
    topup_db,  # noqa: F811
):  # noqa: F811
    fixture = topup_db
    refund = await setup_refund(fixture)
    await process(fixture, refund_event(fixture, refund, "pending"))
    await process(fixture, event(fixture, "reverse", "charge.refunded"))
    await process(fixture, event(fixture, "reverse_duplicate_business", "charge.refunded"))
    refund["status"] = "succeeded"
    await process(fixture, refund_event(fixture, refund, "succeeded"))
    saved = await state(fixture)
    assert [row["kind"] for row in saved["ledger"]] == ["topup", "refund"]
    assert saved["allocated"] == 1000
    history = await TopupService(fixture.db.pool).history(str(fixture.db.tenant))
    assert history[0]["refund_details"]["refunds"][0]["status"] == "succeeded"
    ledger = await TopupService(fixture.db.pool).ledger(str(fixture.db.tenant))
    assert all(row["order_id"] == fixture.order["id"] for row in ledger)
    assert all(row["provider_payment_id"] == fixture.data.payment["id"] for row in ledger)
    async with acquire_with_tenant(fixture.db.pool, str(fixture.db.tenant)) as conn:
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM billing_refund_snapshots WHERE order_id=$1",
                fixture.order["id"],
            )
            == 2
        )
    async with acquire_with_tenant(fixture.db.pool, str(uuid4())) as conn:
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM billing_refund_snapshots WHERE order_id=$1",
                fixture.order["id"],
            )
            == 0
        )
    async with acquire_with_tenant(fixture.db.admin, None) as conn:
        with pytest.raises(asyncpg.RaiseError, match="append-only"):
            async with conn.transaction():
                await conn.execute(
                    "UPDATE billing_refund_snapshots SET content_hash='forged' WHERE order_id=$1",
                    fixture.order["id"],
                )
    async with acquire_with_tenant(fixture.db.pool, str(fixture.db.tenant)) as conn:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with conn.transaction():
                await conn.execute(
                    """INSERT INTO billing_refund_snapshots(order_id,tenant_id,source_event_id,content_hash,projection)
                VALUES($1,$2,'forged','forged','{}')""",
                    fixture.order["id"],
                    fixture.db.tenant,
                )


async def test_interrupted_refund_detail_is_explicitly_partial(topup_db):  # noqa: F811
    fixture = topup_db
    refund = await setup_refund(fixture, list_failure=True)
    await process(fixture, refund_event(fixture, refund, "interrupted"))
    history = await TopupService(fixture.db.pool).history(str(fixture.db.tenant))
    assert history[0]["refund_details"]["detail_status"] == "partial"
    assert history[0]["refund_details"]["refunds"] == []
    assert len((await state(fixture))["ledger"]) == 1


async def test_foreign_current_customer_rejects_refund_capture(topup_db):  # noqa: F811
    fixture = topup_db
    refund = await setup_refund(fixture)
    fixture.data.charge["customer"] = "cus_other"
    with pytest.raises(BillingWebhookReviewRequired):
        await process(fixture, refund_event(fixture, refund, "foreign"))
    history = await TopupService(fixture.db.pool).history(str(fixture.db.tenant))
    assert history[0]["refund_details"]["detail_status"] == "unavailable"


async def test_duplicate_saved_payment_binding_does_not_choose_an_order(topup_db):  # noqa: F811
    fixture = topup_db
    refund = await setup_refund(fixture)
    duplicate_order = uuid4()
    async with acquire_with_tenant(fixture.db.admin, None) as conn:
        await conn.execute(
            """INSERT INTO topup_orders
            (id,tenant_id,package_code,minutes,price_cents,currency,status,provider,
             provider_session_id,provider_payment_id)
            VALUES($1,$2,'mins_250',250,2500,'GBP','paid','stripe',$3,$4)""",
            duplicate_order,
            fixture.db.tenant,
            "cs_" + uuid4().hex,
            fixture.data.payment["id"],
        )
    before = await state(fixture)
    with pytest.raises(BillingWebhookReviewRequired, match="refund_order_binding_ambiguous"):
        await process(fixture, refund_event(fixture, refund, "ambiguous_order"))
    after = await state(fixture)
    assert after["ledger"] == before["ledger"] and after["allocated"] == before["allocated"]
    assert after["notifications"] == before["notifications"]
    async with acquire_with_tenant(fixture.db.pool, str(fixture.db.tenant)) as conn:
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM billing_refund_snapshots WHERE order_id=ANY($1::uuid[])",
                [fixture.order["id"], duplicate_order],
            )
            == 0
        )


@pytest.mark.parametrize("metadata_key", ["tenant_id", "order_id", "purpose"])
async def test_current_payment_metadata_must_match_the_saved_topup(
    topup_db, metadata_key  # noqa: F811
):  # noqa: F811
    fixture = topup_db
    refund = await setup_refund(fixture)
    before = await state(fixture)
    fixture.data.payment["metadata"][metadata_key] = (
        "subscription" if metadata_key == "purpose" else str(uuid4())
    )
    with pytest.raises(BillingWebhookReviewRequired, match="refund_order_binding_mismatch"):
        await process(fixture, refund_event(fixture, refund, "wrong_metadata"))
    after = await state(fixture)
    assert after["ledger"] == before["ledger"] and after["allocated"] == before["allocated"]
    assert after["notifications"] == before["notifications"]
    async with acquire_with_tenant(fixture.db.pool, str(fixture.db.tenant)) as conn:
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM billing_refund_snapshots WHERE order_id=$1",
                fixture.order["id"],
            )
            == 0
        )


async def test_explicit_detail_refresh_repairs_partial_capture_without_money_replay(
    topup_db,  # noqa: F811
):  # noqa: F811
    from app.domain.services.billing_webhook_reconciliation import BillingReconciliation

    fixture = topup_db
    refund = await setup_refund(fixture, list_failure=True)
    envelope = refund_event(fixture, refund, "refreshable")
    await process(fixture, envelope)
    before = await state(fixture)
    prior_provider = fixture.billing._stripe_call

    async def recovered_provider(resource, method, *args, **kwargs):
        if resource == "Event":
            assert method == "retrieve" and args == (envelope["id"],)
            return deepcopy(envelope)
        if resource == "Refund" and method == "list":
            return {"data": [deepcopy(refund)], "has_more": False}
        return await prior_provider(resource, method, *args, **kwargs)

    fixture.billing._stripe_call = recovered_provider
    reconciliation = BillingReconciliation(fixture.db.pool, fixture.billing)
    result = await reconciliation.refresh_details(
        envelope["id"],
        operator="Synthetic finance reviewer",
        reason="Recapture after synthetic provider read recovered",
    )
    assert result["financial_effects_applied"] is False and result["notifications_sent"] is False
    after = await state(fixture)
    assert after["ledger"] == before["ledger"] and after["notifications"] == before["notifications"]
    history = await TopupService(fixture.db.pool).history(str(fixture.db.tenant))
    details = history[0]["refund_details"]
    assert (
        details["detail_status"] == "complete"
        and details["source_event_id"] == result["observation"]
    )
    async with acquire_with_tenant(fixture.db.pool, None) as conn:
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM billing_webhook_review_log WHERE event_id=$1 AND decision='refresh_provider_details'",
                envelope["id"],
            )
            == 1
        )
