"""Per-conversation review endpoints (goals.md §3)."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.v1.dependencies import (
    CurrentUser,
    get_current_user,
    get_db_client,
    require_admin_tenant,
)
from app.core.postgres_adapter import Client
from app.core.security.rbac import Permission, require_permission
from app.domain.services.conversation_review_service import (
    REVIEW_TAGS,
    ConversationReviewService,
    InvalidReviewError,
    ReviewCallNotFoundError,
    ReviewNotFoundError,
    is_reward_eligible,
    reward_daily_cap,
    reward_points,
    rewards_enabled,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/calls", tags=["conversation-reviews"])


class ReviewSubmission(BaseModel):
    rating: int = Field(..., ge=1, le=5, description="1 (poor) to 5 (excellent)")
    tags: List[str] = Field(default_factory=list, description=f"Any of: {', '.join(REVIEW_TAGS)}")
    comment: Optional[str] = Field(None, max_length=4000)


class ReviewResponse(BaseModel):
    id: str
    call_id: str
    campaign_id: Optional[str] = None
    user_id: str
    rating: int
    tags: List[str]
    comment: Optional[str] = None
    # Snapshotted at review time — what the agent actually ran on.
    prompt_template: Optional[str] = None
    prompt_version: Optional[str] = None
    prompt_hash: Optional[str] = None
    awarded_points: int = 0
    created_at: datetime
    updated_at: datetime


class ReviewOptions(BaseModel):
    """What the UI needs to render the form and set expectations honestly."""

    tags: List[str]
    rewards_enabled: bool
    points_per_review: int
    daily_cap: int
    #: False when the current draft would earn nothing, so the UI can say so
    #: BEFORE submission rather than after (§3: "Show reward eligibility before
    #: submission").
    bare_rating_earns_reward: bool


def get_review_service(db_client: Client = Depends(get_db_client)) -> ConversationReviewService:
    return ConversationReviewService(db_client.pool)


def _tenant_id(current_user: CurrentUser) -> str:
    if not current_user.tenant_id:
        raise HTTPException(status_code=403, detail="No tenant context")
    return str(current_user.tenant_id)


def _response(row: dict) -> ReviewResponse:
    return ReviewResponse(
        id=str(row["id"]),
        call_id=str(row["call_id"]),
        campaign_id=str(row["campaign_id"]) if row.get("campaign_id") else None,
        user_id=str(row["user_id"]),
        rating=int(row["rating"]),
        tags=list(row.get("review_tags") or []),
        comment=row.get("comment"),
        prompt_template=row.get("prompt_template"),
        prompt_version=row.get("prompt_version"),
        prompt_hash=row.get("prompt_hash"),
        awarded_points=int(row.get("awarded_points") or 0),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _as_http(exc: Exception) -> HTTPException:
    if isinstance(exc, HTTPException):
        return exc
    if isinstance(exc, ReviewCallNotFoundError):
        # 404 and not 403: confirming a call exists but belongs to someone else
        # is itself a cross-tenant disclosure.
        return HTTPException(status_code=404, detail="Call not found")
    if isinstance(exc, ReviewNotFoundError):
        return HTTPException(status_code=404, detail="Review not found")
    if isinstance(exc, InvalidReviewError):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=500, detail="Review operation failed")


@router.get(
    "/reviews/options",
    response_model=ReviewOptions,
    dependencies=[Depends(require_permission(Permission.CALLS_READ))],
)
async def get_review_options() -> ReviewOptions:
    """Tag vocabulary and reward rules, so the client never hardcodes them."""
    return ReviewOptions(
        tags=list(REVIEW_TAGS),
        rewards_enabled=rewards_enabled(),
        points_per_review=reward_points(),
        daily_cap=reward_daily_cap(),
        bare_rating_earns_reward=is_reward_eligible(5, [], None),
    )


@router.put(
    "/{call_id}/review",
    response_model=ReviewResponse,
    dependencies=[Depends(require_permission(Permission.CALLS_CREATE))],
)
async def submit_review(
    call_id: str,
    body: ReviewSubmission,
    current_user: CurrentUser = Depends(get_current_user),
    service: ConversationReviewService = Depends(get_review_service),
) -> ReviewResponse:
    """Leave or update this user's review of the conversation.

    PUT, not POST: a user has at most one review per call, so submitting twice
    is an update of the same row rather than a second review. ``awarded_points``
    is 0 on an edit — the reward is tied to the review, once, by a database
    constraint.
    """
    try:
        row = await service.submit(
            tenant_id=_tenant_id(current_user),
            call_id=call_id,
            user_id=str(current_user.id),
            rating=body.rating,
            tags=body.tags,
            comment=body.comment,
        )
    except Exception as exc:
        if not isinstance(exc, (ReviewCallNotFoundError, InvalidReviewError, HTTPException)):
            logger.exception("submit_review failed call=%s", call_id[:12])
        raise _as_http(exc) from exc
    return _response(row)


@router.get(
    "/{call_id}/review",
    response_model=ReviewResponse,
    dependencies=[Depends(require_permission(Permission.CALLS_READ))],
)
async def get_my_review(
    call_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    service: ConversationReviewService = Depends(get_review_service),
) -> ReviewResponse:
    try:
        row = await service.get_my_review(
            tenant_id=_tenant_id(current_user),
            call_id=call_id,
            user_id=str(current_user.id),
        )
    except Exception as exc:
        if not isinstance(exc, (ReviewCallNotFoundError, HTTPException)):
            logger.exception("get_my_review failed call=%s", call_id[:12])
        raise _as_http(exc) from exc
    if not row:
        raise HTTPException(status_code=404, detail="You have not reviewed this call")
    return _response(row)


@router.get(
    "/{call_id}/reviews",
    response_model=List[ReviewResponse],
    dependencies=[Depends(require_permission(Permission.CALLS_READ))],
)
async def list_call_reviews(
    call_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    service: ConversationReviewService = Depends(get_review_service),
) -> List[ReviewResponse]:
    """Every review on this call — several teammates may have rated it."""
    try:
        rows = await service.list_for_call(
            tenant_id=_tenant_id(current_user), call_id=call_id
        )
    except Exception as exc:
        if not isinstance(exc, (ReviewCallNotFoundError, HTTPException)):
            logger.exception("list_call_reviews failed call=%s", call_id[:12])
        raise _as_http(exc) from exc
    return [_response(r) for r in rows]


@router.get(
    "/reviews/rewards/balance",
    dependencies=[Depends(require_permission(Permission.CALLS_READ))],
)
async def get_reward_balance(
    current_user: CurrentUser = Depends(get_current_user),
    service: ConversationReviewService = Depends(get_review_service),
):
    """Summed from the ledger on every read, never stored."""
    try:
        return await service.reward_balance(
            tenant_id=_tenant_id(current_user), user_id=str(current_user.id)
        )
    except Exception as exc:
        logger.exception("get_reward_balance failed user=%s", str(current_user.id)[:12])
        raise _as_http(exc) from exc


# ── the management view (goals.md §3) ───────────────────────────────────────
#
# A SEPARATE router with its own prefix, not more paths under /calls. A listing
# at /calls/reviews is two segments, exactly like /calls/{call_id}, so it would
# depend forever on this router being registered first. /reviews cannot collide
# with anything.
#
# require_admin_tenant, not CALLS_READ: this reads every reviewer's assessment
# across the tenant, which is a management view rather than "my own review". It
# also guarantees a tenant_id is present, so the query cannot degrade into an
# unfiltered read.
admin_router = APIRouter(prefix="/reviews", tags=["conversation-reviews"])


class ReviewListResponse(BaseModel):
    items: List[ReviewResponse]
    total: int
    page: int
    page_size: int


@admin_router.get("", response_model=ReviewListResponse)
async def list_reviews(
    campaign_id: Optional[str] = Query(None, description="Filter to one campaign"),
    prompt_version: Optional[str] = Query(None, description="e.g. lead_gen@2"),
    rating_min: Optional[int] = Query(None, ge=1, le=5),
    rating_max: Optional[int] = Query(None, ge=1, le=5),
    tag: Optional[str] = Query(None, description=f"One of: {', '.join(REVIEW_TAGS)}"),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
    current_user: CurrentUser = Depends(require_admin_tenant),
    service: ConversationReviewService = Depends(get_review_service),
) -> ReviewListResponse:
    """Every review in the tenant, filtered by campaign, prompt version, rating
    and tag — the four axes goals.md §3 names."""
    try:
        result = await service.list_reviews(
            tenant_id=str(current_user.tenant_id),
            campaign_id=campaign_id,
            prompt_version=prompt_version,
            rating_min=rating_min,
            rating_max=rating_max,
            tag=tag,
            page=page,
            page_size=page_size,
        )
    except Exception as exc:
        if not isinstance(exc, (InvalidReviewError, HTTPException)):
            logger.exception("list_reviews failed tenant=%s", str(current_user.tenant_id)[:12])
        raise _as_http(exc) from exc
    return ReviewListResponse(
        items=[_response(r) for r in result["items"]],
        total=result["total"],
        page=result["page"],
        page_size=result["page_size"],
    )


@admin_router.get("/summary")
async def summarise_reviews(
    campaign_id: Optional[str] = Query(None),
    prompt_version: Optional[str] = Query(None),
    current_user: CurrentUser = Depends(require_admin_tenant),
    service: ConversationReviewService = Depends(get_review_service),
):
    """Aggregate by prompt version and by failure category.

    This is the query the Safe Improvement Loop runs on: which prompt version is
    doing worse, and at what. `low_rated` counts 1s and 2s — the calls a human is
    supposed to go and listen to before changing anything.
    """
    try:
        return await service.summarise(
            tenant_id=str(current_user.tenant_id),
            campaign_id=campaign_id,
            prompt_version=prompt_version,
        )
    except Exception as exc:
        logger.exception("summarise_reviews failed tenant=%s", str(current_user.tenant_id)[:12])
        raise _as_http(exc) from exc
