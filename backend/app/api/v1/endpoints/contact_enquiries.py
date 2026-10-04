"""Public durable intake and a distinct platform-admin operator view."""
from __future__ import annotations

import ipaddress
import logging
import os
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, Query, Request, Response

from app.api.v1.dependencies import CurrentUser, get_db_pool, require_platform_admin
from app.api.v1.endpoints.auth import limiter
from app.core.errors import ApiError
from app.domain.services import contact_enquiry_service as service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/public/contact-enquiries", tags=["public-contact"])
admin_router = APIRouter(prefix="/contact-enquiries", tags=["admin"])


def intake_rate_limit() -> str:
    """Bounded configurable per-process limit using the existing SlowAPI setup."""
    try:
        amount = int(os.getenv("CONTACT_ENQUIRY_RATE_LIMIT_PER_MINUTE", "10"))
        if not 1 <= amount <= 600:
            raise ValueError
    except ValueError:
        amount = 10  # Invalid configuration must not silently disable limiting.
    return f"{amount}/minute"


def intake_client_ip(request: Request) -> str:
    """Trust forwarding only across explicitly configured proxy networks.

    Default is the socket peer. With a Next server proxy, this can be a shared
    bucket, not a per-visitor guarantee. Deployment must verify that configured
    trusted proxies overwrite/append actual peers; callers cannot opt into trust.
    """
    peer = request.client.host if request.client else "unknown"
    try:
        address = ipaddress.ip_address(peer)
        networks = [ipaddress.ip_network(value.strip()) for value in
                    os.getenv("CONTACT_ENQUIRY_TRUSTED_PROXY_CIDRS", "").split(",") if value.strip()]
    except ValueError:
        return peer
    if not any(address in network for network in networks):
        return peer
    forwarded = request.headers.get("x-forwarded-for", "")
    parts = forwarded.split(",") if forwarded else []
    if not parts or len(parts) > 16:
        return peer
    try:
        chain = [ipaddress.ip_address(value.strip()) for value in parts] + [address]
    except ValueError:
        return peer
    # Walk from the real peer backwards, stopping at the nearest untrusted hop.
    # A forged leftmost value behind that hop cannot create another bucket.
    while len(chain) > 1 and any(chain[-1] in network for network in networks):
        chain.pop()
    return str(chain[-1])


def _unavailable(exc: Exception) -> ApiError:
    logger.warning("contact_enquiry_storage_unavailable error_type=%s", type(exc).__name__)
    return ApiError(status=503, code="enquiry_storage_unavailable",
                    message="We could not confirm your enquiry. Keep your message and retry the same request.")


@router.post("", response_model=service.EnquiryReceipt, status_code=201)
@limiter.limit(intake_rate_limit, key_func=intake_client_ip)
async def submit_contact_enquiry(request: Request, response: Response,
                                 body: service.ContactEnquiryInput,
                                 pool: asyncpg.Pool = Depends(get_db_pool)):
    try:
        receipt, created = await service.submit_enquiry(pool, body)
    except service.EnquiryConflict as exc:
        raise ApiError(status=409, code="enquiry_request_conflict",
                       message="This request ID belongs to different enquiry content. Start a new request for the changed message.") from exc
    except (asyncpg.PostgresError, OSError, TimeoutError, RuntimeError) as exc:
        raise _unavailable(exc) from exc
    response.status_code = 201 if created else 200
    return receipt


@admin_router.get("", response_model=service.EnquiryPage)
async def list_contact_enquiries(
    current_user: CurrentUser = Depends(require_platform_admin),
    pool: asyncpg.Pool = Depends(get_db_pool),
    status: service.EnquiryStatus | None = None,
    limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0),
):
    try:
        return await service.list_enquiries(pool, status=status, limit=limit, offset=offset)
    except (asyncpg.PostgresError, OSError, TimeoutError, RuntimeError) as exc:
        raise _unavailable(exc) from exc


@admin_router.patch("/{enquiry_id}", response_model=service.EnquiryItem)
async def change_contact_enquiry_status(
    enquiry_id: UUID, body: service.EnquiryStatusInput,
    current_user: CurrentUser = Depends(require_platform_admin),
    pool: asyncpg.Pool = Depends(get_db_pool),
):
    try:
        result = await service.update_enquiry_status(pool, enquiry_id, body.status, UUID(current_user.id))
    except (asyncpg.PostgresError, OSError, TimeoutError, RuntimeError) as exc:
        raise _unavailable(exc) from exc
    if result is None:
        raise ApiError(status=404, code="enquiry_not_found", message="Enquiry not found.")
    return result
