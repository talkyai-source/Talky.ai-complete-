"""Payments that need a person (Stripe and PayPal): list them, mark one as handled.

Closing a review records who closed it and why. It never gives or takes back
minutes, a plan or money.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Literal

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.v1.dependencies import CurrentUser, get_audit_logger, get_db_client, require_platform_admin
from app.core.db_utils import acquire_with_tenant
from app.core.postgres_adapter import Client
from app.core.security.platform_admin_guard import require_platform_admin_mutation
from app.core.security.principal import PrincipalUnavailable, load_session_principal
from app.domain.services.audit_logger import AuditEvent, AuditLogger
from app.domain.services.billing_webhooks import BillingWebhookRetryable
from app.domain.services.payment_reviews import close_payment_review, list_payment_reviews

router = APIRouter()

_UNAVAILABLE = "The list of payments that need review is unavailable. It does not mean there are none."
_UNCONFIRMED = "The result is not confirmed. Read the list again before trying again."


class PaymentReviewItem(BaseModel):
    provider: Literal["stripe", "paypal"]
    event_id: str
    event_type: str | None = None
    state: Literal["needs_review", "stuck"]
    received_at: datetime
    waiting_hours: int
    kind: Literal["minutes", "plan", "unknown"]
    tenant_id: str | None = None
    business_name: str | None = None
    amount_minor: int | None = None
    currency: str | None = None
    currency_exponent: int | None = None
    reason_code: str | None = None
    reason_text: str
    provider_reference: str | None = None
    attempt_count: int
    can_close: bool
    close_blocked_reason: str | None = None


class PaymentReviewList(BaseModel):
    items: list[PaymentReviewItem]
    truncated: bool
    limit: int


class ClosePaymentReview(BaseModel):
    provider: Literal["stripe", "paypal"]
    event_id: str = Field(min_length=1, max_length=255)
    seen_attempt_count: int = Field(ge=0)
    reason: str = Field(max_length=2000)


class PaymentReviewClosed(BaseModel):
    provider: Literal["stripe", "paypal"]
    event_id: str
    state: Literal["dismissed"]
    changed: bool
    prior_state: str
    financial_effects_applied: Literal[False]


async def _session(conn, request, user):
    actor = str(user.id)
    sid = getattr(request.state, "authenticated_session_id", None)
    if getattr(request.state, "authenticated_user_id", None) != actor or not sid:
        raise HTTPException(401, "Current authenticated session is required.")
    try:
        principal = await load_session_principal(conn, {"sub": actor, "sid": sid, "tenant_id": user.tenant_id})
    except PrincipalUnavailable as exc:
        raise HTTPException(401, "Current account or session is unavailable.") from exc
    if principal["role"] != "platform_admin":
        raise HTTPException(403, "Current platform admin authority is required.")


@router.get("/billing/payment-reviews", response_model=PaymentReviewList)
async def get_payment_reviews(
    request: Request,
    user: CurrentUser = Depends(require_platform_admin),
    db: Client = Depends(get_db_client),
):
    try:
        async with asyncio.timeout(10), acquire_with_tenant(db.pool, None, user_id=user.id, timeout=3) as conn:
            await _session(conn, request, user)
            return await list_payment_reviews(conn)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503, _UNAVAILABLE) from exc


@router.post("/billing/payment-reviews/close", response_model=PaymentReviewClosed)
async def close_review(
    body: ClosePaymentReview,
    request: Request,
    user: CurrentUser = Depends(require_platform_admin),
    db: Client = Depends(get_db_client),
    audit: AuditLogger = Depends(get_audit_logger),
):
    try:
        async with asyncio.timeout(10), acquire_with_tenant(db.pool, None, user_id=user.id, timeout=3) as conn:
            await audit.lock_transaction(conn)
            await require_platform_admin_mutation(conn, user.id)
            await _session(conn, request, user)
            result = await close_payment_review(
                conn, provider=body.provider, event_id=body.event_id,
                seen_attempt_count=body.seen_attempt_count,
                operator=f"{user.email} ({user.id})"[:200], operator_id=str(user.id),
                reason=body.reason,
            )
            if result["changed"]:
                await audit.log_in_transaction(
                    conn, event_type=AuditEvent.BILLING_UPDATED, action="payment_review_closed",
                    actor_id=user.id, actor_role="platform_admin", resource_type="payment_review",
                    metadata={
                        "provider": result["provider"], "event_id": result["event_id"],
                        "prior_state": result["prior_state"], "financial_effects_applied": False,
                    },
                )
        return result
    except HTTPException:
        raise
    except (asyncpg.PostgresError, asyncpg.InterfaceError, asyncpg.exceptions.ClientConfigurationError) as exc:
        # asyncpg's own failures (not the service's refusals) never show their text.
        raise HTTPException(503, _UNCONFIRMED) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except BillingWebhookRetryable as exc:
        raise HTTPException(
            409, "Another process is handling this payment message. Try again in a minute."
        ) from exc
    except Exception as exc:
        raise HTTPException(503, _UNCONFIRMED) from exc
