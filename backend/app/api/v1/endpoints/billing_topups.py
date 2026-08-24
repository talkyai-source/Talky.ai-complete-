"""Minute top-ups — buy call minutes outside the plan cycle (goals.md §9).

    GET  /billing/topups/packages   the approved catalogue
    POST /billing/topups/checkout   start a purchase, get a payment URL
    GET  /billing/topups/orders     what this tenant has bought, and its state
    GET  /billing/topups/balance    minutes: allocated, used, remaining, bought
    GET  /billing/topups/ledger     every signed movement, for reconciliation

THERE IS NO WEBHOOK ENDPOINT HERE — DELIBERATELY
-------------------------------------------------
Stripe delivers subscription events and top-up events down the SAME endpoint,
``POST /billing/webhooks``. Adding a second URL would mean a second signing
secret to configure and a second thing to get wrong at the exact moment money
is involved. ``BillingService.handle_webhook`` routes on the ``purpose`` stamped
into the session metadata instead, so signature verification and event-id
idempotency stay in one place.

WHAT THE CLIENT IS ALLOWED TO SEND
-----------------------------------
A package code. Not minutes, not a price. An endpoint that accepts an amount
from the browser sells 10,000 minutes for a penny the first time somebody edits
a request, and the fact that the UI only ever sends good values is not a control
— it is a hope.
"""
from __future__ import annotations

import csv
import io
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.api.v1.dependencies import (
    CurrentUser,
    get_audit_logger,
    get_current_user,
    get_db_client,
    require_platform_admin,
)
from app.core.container import get_container
from app.core.security.rbac import Permission, require_permission
from app.domain.services.audit_logger import AuditEvent, AuditLogger
from app.domain.services.billing_service import BillingService
from app.domain.services.minutes_quota import compute_minutes_status
from app.domain.services.topup_service import TopupError, TopupService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/billing/topups", tags=["billing"])


def _tenant(user) -> str:
    tid = getattr(user, "tenant_id", None)
    if not tid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User is not associated with a tenant",
        )
    return str(tid)


def _service() -> TopupService:
    c = get_container()
    if not c.is_initialized:
        raise HTTPException(status_code=503, detail="Backend not ready")
    return TopupService(c.db_pool)


# ── models ──────────────────────────────────────────────────────────────────

class PackageOut(BaseModel):
    code: str
    name: str
    minutes: int
    price_cents: int
    currency: str
    expires_days: Optional[int] = None
    # Shown so the larger bundle can justify itself. Computed here rather than
    # in the browser so every surface quotes the same number.
    price_per_minute_cents: float


class CheckoutIn(BaseModel):
    package_code: str = Field(..., max_length=64)
    success_url: Optional[str] = None
    cancel_url: Optional[str] = None


class CheckoutOut(BaseModel):
    order_id: str
    session_id: str
    checkout_url: str
    minutes: int
    price_cents: int
    currency: str
    mock_mode: bool = False
    message: Optional[str] = None


class BalanceOut(BaseModel):
    allocated: int
    used_minutes: int
    remaining_minutes: int
    unlimited: bool
    exhausted: bool
    purchased_minutes: int


# ── catalogue ───────────────────────────────────────────────────────────────

@router.get(
    "/packages",
    response_model=list[PackageOut],
    dependencies=[Depends(require_permission(Permission.BILLING_READ))],
)
async def list_packages(current_user: CurrentUser = Depends(get_current_user)):
    rows = await _service().packages()
    return [
        PackageOut(
            **r,
            price_per_minute_cents=round(r["price_cents"] / r["minutes"], 2),
        )
        for r in rows
    ]


# ── balance ─────────────────────────────────────────────────────────────────

@router.get(
    "/balance",
    response_model=BalanceOut,
    dependencies=[Depends(require_permission(Permission.BILLING_READ))],
)
async def get_balance(current_user: CurrentUser = Depends(get_current_user)):
    """The same computation the dialler gates on.

    Reading from ``minutes_quota`` rather than re-deriving it is the point: a
    balance screen that disagrees with the thing that blocks calls is worse
    than no balance screen, because it is believed.
    """
    tenant_id = _tenant(current_user)
    svc = _service()
    c = get_container()
    async with c.db_pool.acquire() as conn:
        st = await compute_minutes_status(conn, tenant_id)
    return BalanceOut(**st.as_dict(), purchased_minutes=await svc.purchased_total(tenant_id))


# ── purchase ────────────────────────────────────────────────────────────────

@router.post(
    "/checkout",
    response_model=CheckoutOut,
    dependencies=[Depends(require_permission(Permission.BILLING_UPDATE))],
)
async def create_topup_checkout(
    body: CheckoutIn,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    db_client=Depends(get_db_client),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """Create the order, then the payment session, then link them.

    ORDER FIRST, ALWAYS. The order row is what the webhook matches a payment
    against; creating the Stripe session first opens a window where a customer
    can pay for something we have no record of.

    If the link-back fails after the session exists, this returns 500 and the
    customer sees an error — but the order is still there, unlinked and
    pending, and the failure is logged with both ids so it can be matched by
    hand. The alternative (swallowing it) is a payment that credits nothing.
    """
    tenant_id = _tenant(current_user)
    svc = _service()

    origin = request.headers.get("origin", "http://localhost:3000")
    success_url = body.success_url or f"{origin}/billing?topup=success"
    cancel_url = body.cancel_url or f"{origin}/billing?topup=cancelled"

    try:
        order = await svc.create_order(
            tenant_id=tenant_id,
            user_id=getattr(current_user, "id", None),
            package_code=body.package_code,
        )
    except TopupError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    billing = BillingService(db_client, audit_logger=audit_logger)
    try:
        session = await billing.create_topup_checkout_session(
            tenant_id=tenant_id,
            email=getattr(current_user, "email", "") or "",
            order_id=str(order["id"]),
            minutes=order["minutes"],
            price_cents=order["price_cents"],
            currency=order["currency"],
            product_name=f"Talky.ai — {order['name']}",
            success_url=success_url,
            cancel_url=cancel_url,
            business_name=getattr(current_user, "business_name", None),
        )
    except Exception as e:  # noqa: BLE001
        logger.error(
            "topup_checkout_failed order=%s tenant=%s: %s",
            str(order["id"])[:8], tenant_id[:8], e,
        )
        # The order stays pending. Nothing was charged, so nothing is owed.
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not reach the payment provider. Nothing has been charged.",
        )

    try:
        await svc.attach_session(str(order["id"]), session["session_id"])
    except TopupError as e:
        logger.error(
            "topup_session_orphaned order=%s session=%s — a payment on this "
            "session cannot be matched to its order: %s",
            str(order["id"])[:8], session["session_id"], e,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not start the purchase. Nothing has been charged.",
        )

    await audit_logger.log(
        event_type=AuditEvent.BILLING_UPDATED,
        actor_id=getattr(current_user, "id", None),
        actor_type="user",
        tenant_id=tenant_id,
        action="topup_checkout_created",
        description=f"Started a {order['minutes']}-minute top-up",
        metadata={
            "order_id": str(order["id"]),
            "package_code": order["package_code"],
            "minutes": order["minutes"],
            "price_cents": order["price_cents"],
            "mock_mode": session.get("mock_mode", False),
        },
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )

    return CheckoutOut(
        order_id=str(order["id"]),
        session_id=session["session_id"],
        checkout_url=session["checkout_url"],
        minutes=order["minutes"],
        price_cents=order["price_cents"],
        currency=order["currency"],
        mock_mode=session.get("mock_mode", False),
        message=session.get("message"),
    )


# ── history ─────────────────────────────────────────────────────────────────

@router.get(
    "/orders",
    dependencies=[Depends(require_permission(Permission.BILLING_READ))],
)
async def list_orders(
    limit: int = 25,
    current_user: CurrentUser = Depends(get_current_user),
):
    rows = await _service().history(_tenant(current_user), min(max(limit, 1), 100))
    return {"orders": [
        {
            "id": str(r["id"]),
            "package_code": r["package_code"],
            "minutes": r["minutes"],
            "price_cents": r["price_cents"],
            "currency": r["currency"],
            "status": r["status"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            "paid_at": r["paid_at"].isoformat() if r["paid_at"] else None,
        }
        for r in rows
    ]}


@router.get(
    "/ledger",
    dependencies=[Depends(require_permission(Permission.BILLING_READ))],
)
async def list_ledger(
    limit: int = 100,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Signed movements. A refund shows as its own negative line rather than
    the original purchase disappearing, which is what makes this answerable
    when a customer asks what they were charged for."""
    rows = await _service().ledger(_tenant(current_user), min(max(limit, 1), 500))
    return {"entries": [
        {
            "kind": r["kind"],
            "minutes_delta": r["minutes_delta"],
            "amount_cents": r["amount_cents"],
            "currency": r["currency"],
            "note": r["note"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        }
        for r in rows
    ]}


# ── admin reconciliation (§9) ───────────────────────────────────────────────
#
# require_platform_admin, not a tenant permission: this reads across every
# tenant on purpose, which is the whole point of a reconciliation view. It has
# to be the ledger rather than the orders — an order can sit in a state that
# never became money, and reconciling against intent instead of record is
# exactly how a set of books stops matching the payment provider's.

admin_router = APIRouter(prefix="/admin/billing", tags=["billing", "admin"])


@admin_router.get("/reconciliation")
async def reconciliation(
    since: Optional[str] = None,
    until: Optional[str] = None,
    limit: int = 5000,
    fmt: str = "json",
    _admin=Depends(require_platform_admin),
):
    """Every minute movement, with the payment that caused it.

    ``fmt=csv`` returns the same rows as a download for a spreadsheet
    reconciliation against the provider's own export.
    """
    rows = await _service().reconciliation(
        since=since, until=until, limit=min(max(limit, 1), 50_000),
    )

    records = [
        {
            "created_at": r["created_at"].isoformat() if r["created_at"] else "",
            "tenant_id": str(r["tenant_id"]),
            "business_name": r["business_name"] or "",
            "kind": r["kind"],
            "minutes_delta": r["minutes_delta"],
            "amount_cents": r["amount_cents"],
            "currency": r["currency"] or "",
            "package_code": r["package_code"] or "",
            "provider_event_id": r["provider_event_id"] or "",
            "provider_payment_id": r["provider_payment_id"] or "",
            "order_status": r["order_status"] or "",
            "note": r["note"] or "",
        }
        for r in rows
    ]

    if fmt.lower() == "csv":
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=list(records[0].keys()) if records
                                else ["created_at", "tenant_id", "business_name",
                                      "kind", "minutes_delta", "amount_cents",
                                      "currency", "package_code",
                                      "provider_event_id", "provider_payment_id",
                                      "order_status", "note"])
        writer.writeheader()
        writer.writerows(records)
        return StreamingResponse(
            iter([buf.getvalue()]),
            media_type="text/csv",
            headers={
                "Content-Disposition":
                    'attachment; filename="talky-minute-reconciliation.csv"'
            },
        )

    # Totals, so the top of the page can be compared against the provider's
    # dashboard without adding up a thousand rows by hand.
    gross = sum(r["amount_cents"] for r in records if r["amount_cents"] > 0)
    refunded = sum(-r["amount_cents"] for r in records if r["amount_cents"] < 0)
    return {
        "entries": records,
        "totals": {
            "rows": len(records),
            "minutes_sold": sum(r["minutes_delta"] for r in records
                                if r["minutes_delta"] > 0),
            "minutes_reversed": sum(-r["minutes_delta"] for r in records
                                    if r["minutes_delta"] < 0),
            "gross_cents": gross,
            "refunded_cents": refunded,
            "net_cents": gross - refunded,
        },
    }
