"""Payments that need review against real PostgreSQL (CI only: needs a disposable *_test database).

Proves the SQL of services/payment_reviews.py and migration 0104: the CHECK accepts
`dismissed`, both list queries run, close + log + foreign key, the alert's count drops,
and a recurring local review reopens a closed row. It runs as the same non-superuser,
non-BYPASSRLS role the PayPal plan tests use, with only the rights this feature needs.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio

from app.core.db_utils import acquire_with_tenant
from app.domain.services import paypal_subscriptions as ps
from app.domain.services.paypal_topups import PayPalTopupService
from app.domain.services import payment_reviews as pr
from tests.integration.test_paypal_subscriptions_pg import pp_db  # noqa: F401
from tests.integration.test_billing_price_options import billing_db  # noqa: F401

pytestmark = pytest.mark.integration

REASON = "Checked at the provider and settled by hand."
# scripts/healthwatch.py counts exactly these rows.
_ALERT_COUNT = (
    "SELECT (SELECT count(*) FROM processed_webhook_events WHERE state='needs_review') + "
    "(SELECT count(*) FROM paypal_webhook_events WHERE state = 'needs_review' "
    "OR (state IN ('failed', 'received') AND received_at < NOW() - INTERVAL '1 hour'))"
)


@pytest_asyncio.fixture
async def rv(pp_db):  # noqa: F811 - imported pytest fixture
    fixture = pp_db
    suffix = uuid4().hex[:10]
    fixture.pp_event = f"pgreview:{suffix}:paypal"
    fixture.pp_stuck = f"pgreview:{suffix}:stuck"
    fixture.st_event = f"evt_pgreview_{suffix}"
    fixture.order_id = uuid4()
    fixture.extra_events = []
    async with acquire_with_tenant(fixture.pool, None) as conn:
        for grant in (
            f'GRANT SELECT,INSERT ON paypal_webhook_review_log TO "{fixture.role}"',
            f'GRANT SELECT ON topup_orders TO "{fixture.role}"',
            f'GRANT SELECT,UPDATE ON processed_webhook_events TO "{fixture.role}"',
            f'GRANT INSERT ON billing_webhook_review_log TO "{fixture.role}"',
        ):
            await conn.execute(grant)
        await conn.execute(
            """INSERT INTO topup_orders(id,tenant_id,package_code,minutes,price_cents,currency,status,
            provider,provider_session_id) VALUES($1,$2,'mins_250',250,2500,'GBP','pending','paypal',$3)""",
            fixture.order_id, fixture.tenants[0], f"PPO-{suffix}",
        )
        await conn.execute(
            """INSERT INTO paypal_webhook_events
            (event_id,event_type,resource_type,resource_id,order_id,provider_mode,state,detail,payload_hash)
            VALUES($1,'LOCAL.CAPTURE.REVIEW','order',$2,$3,'test','needs_review','amount_mismatch',$4)""",
            fixture.pp_event, f"PPO-{suffix}", fixture.order_id, "a" * 64,
        )
        await conn.execute(
            """INSERT INTO paypal_webhook_events
            (event_id,event_type,provider_mode,state,detail,payload_hash,received_at)
            VALUES($1,'PAYMENT.CAPTURE.COMPLETED','test','failed','topup_x',$2, NOW() - INTERVAL '2 hours')""",
            fixture.pp_stuck, "b" * 64,
        )
        payload = {"data": {"object": {
            "id": f"cs_{suffix}", "mode": "payment", "customer": "cus_PgReview1", "amount_total": 1900,
            "currency": "usd", "metadata": {"tenant_id": str(fixture.tenants[1]), "purpose": "minute_topup"}}}}
        await conn.execute(
            """INSERT INTO processed_webhook_events
            (event_id,event_type,state,event_payload,payload_hash,provider_mode,legacy_claim,attempt_count,
             last_error_code)
            VALUES($1,'checkout.session.completed','needs_review',$2::jsonb,$3,'test',FALSE,1,
                   'credit_not_confirmed')""",
            fixture.st_event, json.dumps(payload), "c" * 64,
        )
    try:
        yield fixture
    finally:
        ids = [fixture.pp_event, fixture.pp_stuck, *fixture.extra_events]
        async with acquire_with_tenant(fixture.pool, None) as conn:
            await conn.execute("DELETE FROM paypal_webhook_review_log WHERE event_id=ANY($1::text[])", ids)
            await conn.execute("DELETE FROM paypal_webhook_events WHERE event_id=ANY($1::text[])", ids)
            await conn.execute("DELETE FROM billing_webhook_review_log WHERE event_id=$1", fixture.st_event)
            await conn.execute("DELETE FROM processed_webhook_events WHERE event_id=$1", fixture.st_event)
            await conn.execute("DELETE FROM topup_orders WHERE id=$1", fixture.order_id)


async def _close(fixture, provider, event_id, seen, reason=REASON):
    async with acquire_with_tenant(fixture.service_pool, None) as conn:
        return await pr.close_payment_review(
            conn, provider=provider, event_id=event_id, seen_attempt_count=seen,
            operator="staff@example.com (id)", operator_id=str(uuid4()), reason=reason)


async def _state(fixture, event_id):
    async with acquire_with_tenant(fixture.pool, None) as conn:
        return await conn.fetchrow(
            "SELECT state, attempt_count FROM paypal_webhook_events WHERE event_id=$1", event_id)


async def test_the_state_check_accepts_dismissed_and_still_refuses_unknown_states(rv):
    fixture = rv
    # Two transactions: a refused statement aborts its transaction, which would
    # also undo an accepted statement made in the same one.
    async with acquire_with_tenant(fixture.pool, None) as conn:
        await conn.execute("UPDATE paypal_webhook_events SET state='dismissed' WHERE event_id=$1", fixture.pp_event)
    assert (await _state(fixture, fixture.pp_event))["state"] == "dismissed"
    with pytest.raises(asyncpg.CheckViolationError):
        async with acquire_with_tenant(fixture.pool, None) as conn:
            await conn.execute("UPDATE paypal_webhook_events SET state='bogus' WHERE event_id=$1", fixture.pp_event)
    assert (await _state(fixture, fixture.pp_event))["state"] == "dismissed"


async def test_both_list_queries_run_and_join_account_price_and_name(rv):
    fixture = rv
    async with acquire_with_tenant(fixture.service_pool, None) as conn:
        out = await pr.list_payment_reviews(conn)
    items = {i["event_id"]: i for i in out["items"]}
    paypal, stuck, stripe = items[fixture.pp_event], items[fixture.pp_stuck], items[fixture.st_event]
    assert paypal["tenant_id"] == str(fixture.tenants[0]) and paypal["business_name"] == "Synthetic CP02"
    assert (paypal["kind"], paypal["amount_minor"], paypal["currency"]) == ("minutes", 2500, "gbp")
    assert stuck["state"] == "stuck" and stuck["can_close"] is False
    assert (stripe["tenant_id"], stripe["kind"], stripe["amount_minor"]) == (
        str(fixture.tenants[1]), "minutes", 1900)
    assert "cus_PgReview1" not in json.dumps(out, default=str)


async def test_close_logs_the_decision_drops_the_alert_count_and_is_idempotent(rv):
    fixture = rv
    async with acquire_with_tenant(fixture.pool, None) as conn:
        before = await conn.fetchval(_ALERT_COUNT)
    first = await _close(fixture, "paypal", fixture.pp_event, 1)
    again = await _close(fixture, "paypal", fixture.pp_event, 1)
    assert first["changed"] is True and again["changed"] is False
    async with acquire_with_tenant(fixture.pool, None) as conn:
        after = await conn.fetchval(_ALERT_COUNT)
        logs = await conn.fetch(
            "SELECT operator, decision, reason, attempt_count_seen FROM paypal_webhook_review_log "
            "WHERE event_id=$1", fixture.pp_event)
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await conn.execute(
                "INSERT INTO paypal_webhook_review_log(event_id,operator,decision,reason,attempt_count_seen) "
                "VALUES('no-such-event','op','dismiss',$1,0)", REASON)
    assert before - after == 1
    assert len(logs) == 1 and logs[0]["decision"] == "dismiss" and logs[0]["attempt_count_seen"] == 1
    assert logs[0]["reason"] == REASON and logs[0]["operator"].startswith("staff@example.com")


async def test_close_is_refused_when_the_count_moved_or_the_row_is_young_and_stuck(rv):
    fixture = rv
    with pytest.raises(ValueError, match="changed after the list was read"):
        await _close(fixture, "paypal", fixture.pp_event, 7)
    with pytest.raises(ValueError, match="4 days"):
        await _close(fixture, "paypal", fixture.pp_stuck, 1)
    assert (await _state(fixture, fixture.pp_event))["state"] == "needs_review"
    assert (await _state(fixture, fixture.pp_stuck))["state"] == "failed"


class _GenuineClient:
    async def verify_webhook_signature(self, **kw):
        return True


async def test_close_refused_after_changed_body_redelivery(rv, monkeypatch):
    from app.domain.services import paypal_webhooks as pw

    fixture = rv
    monkeypatch.setenv("PAYPAL_ENV", "sandbox")
    monkeypatch.setenv("PAYPAL_WEBHOOK_ID", "wh-synthetic")
    seen = (await _state(fixture, fixture.pp_event))["attempt_count"]
    raw = json.dumps({"id": fixture.pp_event, "event_type": "PAYMENT.CAPTURE.COMPLETED",
                      "resource_type": "capture", "resource": {"id": "CHANGED-BODY"}}).encode()
    answer = await pw.handle_paypal_webhook(
        fixture.pool, headers={}, raw_body=raw, client=_GenuineClient(), service=object(), plans=object())
    assert answer == {"status": "needs_review"}
    after = await _state(fixture, fixture.pp_event)
    assert after["state"] == "needs_review" and after["attempt_count"] == seen + 1
    with pytest.raises(ValueError, match="changed after the list was read"):
        await _close(fixture, "paypal", fixture.pp_event, seen)
    assert (await _state(fixture, fixture.pp_event))["state"] == "needs_review"


async def test_a_stripe_review_closes_with_the_existing_log_and_rules(rv):
    fixture = rv
    out = await _close(fixture, "stripe", fixture.st_event, 1)
    assert out["changed"] is True and out["financial_effects_applied"] is False
    async with acquire_with_tenant(fixture.pool, None) as conn:
        state = await conn.fetchval("SELECT state FROM processed_webhook_events WHERE event_id=$1", fixture.st_event)
        n = await conn.fetchval("SELECT count(*) FROM billing_webhook_review_log WHERE event_id=$1", fixture.st_event)
    assert state == "dismissed" and n == 1


async def test_a_recurring_local_review_reopens_a_closed_row(rv):
    fixture = rv
    await _close(fixture, "paypal", fixture.pp_event, 1)
    async with acquire_with_tenant(fixture.service_pool, None) as conn:
        await conn.execute(ps._Q_REVIEW, fixture.pp_event, "I-X", "test", "charged_after_cancel", "d" * 64)
    row = await _state(fixture, fixture.pp_event)
    assert row["state"] == "needs_review" and row["attempt_count"] == 2


async def _plan_review_recorded_again(fixture, rule):
    """The real subscription service writes the review; it is closed; then it is written again."""
    row = {"id": uuid4(), "paypal_subscription_id": "I-pgreview", "provider_mode": "test"}
    event_id = f"local:review:plan:{row['id']}:{rule}"
    fixture.extra_events.append(event_id)
    await fixture.svc._record_review(row, rule)
    first = await _state(fixture, event_id)
    assert first["state"] == "needs_review"
    # the list was read at attempt_count 1; the sweep may already have written it again
    await fixture.svc._record_review(row, rule)
    closed = await _close(fixture, "paypal", event_id, seen_attempt_count_for(rule, first))
    assert closed["changed"] is True
    async with acquire_with_tenant(fixture.pool, None) as conn:
        counted_after_close = await conn.fetchval(_ALERT_COUNT)
    await fixture.svc._record_review(row, rule)  # the next sweep tick
    async with acquire_with_tenant(fixture.pool, None) as conn:
        counted_after_tick = await conn.fetchval(_ALERT_COUNT)
    return event_id, counted_after_close, counted_after_tick


def seen_attempt_count_for(rule, first):
    # a recurring rule is closed with a stale count; any other rule needs the current one
    return first["attempt_count"] if rule in pr._RECURRING_RULES else first["attempt_count"] + 1


@pytest.mark.parametrize("rule", ["binding", "not_confirmed_with_paypal"])
async def test_a_closed_recurring_plan_review_stays_closed_and_the_alert_stays_down(rv, rule):
    fixture = rv
    event_id, after_close, after_tick = await _plan_review_recorded_again(fixture, rule)
    row = await _state(fixture, event_id)
    assert row["state"] == "dismissed" and row["attempt_count"] == 3
    assert after_tick == after_close


async def test_a_closed_recurring_review_stays_closed_in_its_period_and_a_new_period_opens_a_new_one(rv):
    fixture = rv
    plan = uuid4()
    rule = "not_confirmed_with_paypal"
    base = f"local:review:plan:{plan}:{rule}"
    may, june = f"{base}:2026-12-05", f"{base}:2027-01-05"
    fixture.extra_events.extend([base, may, june])
    row = {"id": plan, "paypal_subscription_id": "I-pgperiod", "provider_mode": "test",
           "current_period_end": datetime(2026, 12, 5, 12, 0, tzinfo=UTC)}
    await fixture.svc._record_review(row, rule)
    first = await _state(fixture, may)
    assert first["state"] == "needs_review" and await _state(fixture, base) is None
    await _close(fixture, "paypal", may, first["attempt_count"])
    await fixture.svc._record_review(row, rule)  # the next sweep, same period
    again = await _state(fixture, may)
    assert again["state"] == "dismissed" and await _state(fixture, june) is None
    row["current_period_end"] = datetime(2027, 1, 5, 12, 0, tzinfo=UTC)  # the plan's period moved on
    await fixture.svc._record_review(row, rule)
    assert (await _state(fixture, june))["state"] == "needs_review"
    assert (await _state(fixture, may))["state"] == "dismissed"
    assert await _state(fixture, base) is None


async def test_a_closed_review_of_another_plan_rule_reopens_when_recorded_again(rv):
    fixture = rv
    event_id, after_close, after_tick = await _plan_review_recorded_again(fixture, "superseded")
    assert (await _state(fixture, event_id))["state"] == "needs_review"
    assert after_tick == after_close + 1


async def test_a_closed_minutes_review_reopens_when_recorded_again(rv):
    fixture = rv
    order = {"id": fixture.order_id, "provider_session_id": "PPO-again"}
    service = PayPalTopupService(fixture.service_pool)
    event_id = f"local:review:{fixture.order_id}:amount_mismatch"
    fixture.extra_events.append(event_id)
    await service._record_review(order, "amount_mismatch")
    first = await _state(fixture, event_id)
    await _close(fixture, "paypal", event_id, first["attempt_count"])
    assert (await _state(fixture, event_id))["state"] == "dismissed"
    await service._record_review(order, "amount_mismatch")
    assert (await _state(fixture, event_id))["state"] == "needs_review"
