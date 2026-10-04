"""Public plan capabilities and verified server-owned purchase options."""

import logging

from fastapi import APIRouter, Depends, HTTPException

from app.api.v1.dependencies import get_db_pool
from app.domain.services.billing_catalog import list_plan_catalog

router = APIRouter(prefix="/plans", tags=["plans"])
logger = logging.getLogger(__name__)


@router.get("/")
async def list_plans(pool=Depends(get_db_pool)):
    try:
        return await list_plan_catalog(pool)
    except Exception:
        logger.exception("Unable to load billing catalog")
        raise HTTPException(
            status_code=503, detail="Pricing is temporarily unavailable. Please try again."
        )
