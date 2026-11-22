"""A plan that ends leaves a pay-as-you-go account (owner promise: bought minutes carry over until used).

The plan's monthly minutes end with it; minutes bought as top-ups stay in billing_ledger and stay usable.
An existing admin/dispute suspension or admin archive is never changed here. Caller holds the tenant row
lock (FOR UPDATE) inside its own transaction.
"""
from app.domain.services.billing_checkout import apply_plan_allocation
from app.domain.services.payg import PAYG_PLAN_ID
from app.domain.services.subscription_status import CANCELLED, INACTIVE, SUSPENDED, canonical

_Q_TENANT = ("SELECT subscription_status, plan_id, stripe_subscription_id, stripe_customer_id "
             "FROM tenants WHERE id = $1::uuid FOR UPDATE")
_Q_TO_PAYG = ("UPDATE tenants SET plan_id = $2, subscription_status = $3, stripe_subscription_id = NULL "
              "WHERE id = $1::uuid AND stripe_subscription_id IS NOT DISTINCT FROM $4::text")


async def end_plan(conn, tenant_id, *, stripe_subscription_id=None, stripe_customer_id=None,
                   plan_id=None, ended_status=INACTIVE) -> bool:
    tid = str(tenant_id)
    row = await conn.fetchrow(_Q_TENANT, tid)
    if row is None or row["stripe_subscription_id"] != stripe_subscription_id:
        return False                      # replaced or another plan provides access now
    if stripe_customer_id is not None and row["stripe_customer_id"] != stripe_customer_id:
        return False
    if plan_id is not None and row["plan_id"] != plan_id:
        return False                      # PayPal: the tenant is no longer on this row's plan
    current = canonical(row["subscription_status"])
    status = current if current in (SUSPENDED, CANCELLED) else ended_status
    # BEFORE plan_id changes: closes the running window against the OLD plan's minutes and
    # billing model (an unlimited legacy plan must not debit top-ups), then sets 0 and re-anchors.
    await apply_plan_allocation(conn, tid, 0)
    if await conn.execute(_Q_TO_PAYG, tid, PAYG_PLAN_ID, status, stripe_subscription_id) != "UPDATE 1":
        raise RuntimeError("plan_end_not_applied")   # rolls back the zeroed allocation too
    return True
