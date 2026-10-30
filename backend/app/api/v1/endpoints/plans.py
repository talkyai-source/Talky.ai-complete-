"""Public plan capabilities and verified server-owned purchase options."""

import json
import logging

from fastapi import APIRouter, Depends, HTTPException

from app.api.v1.dependencies import get_db_pool
from app.core.db_utils import acquire_with_tenant
from app.domain.services.billing_catalog import _EXPONENTS, list_plan_catalog
from app.domain.services.payg import PAYG, PAYG_PLAN_ID

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


@router.get("/payg")
async def pay_as_you_go(pool=Depends(get_db_pool)):
    """The prepaid plan and its current minute bundles, for the public pricing page.

    Prices live in ``topup_packages`` (audience ``payg``) and change through the
    Admin API without a release, so the page reads them from here.
    """
    try:
        async with acquire_with_tenant(pool, None) as conn:
            plan = await conn.fetchrow(
                "SELECT id, name, description, agents, concurrent_calls, features FROM plans "
                "WHERE id = $1 AND billing_model = 'prepaid'",
                PAYG_PLAN_ID,
            )
            packages = await conn.fetch(
                """SELECT code, name, minutes, price_cents, currency FROM topup_packages
                   WHERE is_active AND audience = $1 ORDER BY sort_order, minutes""",
                PAYG,
            )
    except Exception:
        logger.exception("Unable to load pay-as-you-go prices")
        raise HTTPException(
            status_code=503, detail="Pricing is temporarily unavailable. Please try again."
        )
    if plan is None or not packages:
        raise HTTPException(status_code=404, detail="Pay as you go is not offered.")
    features = plan["features"]
    if isinstance(features, str):
        features = json.loads(features)
    return {
        "plan": {**dict(plan), "features": list(features or [])},
        "packages": [
            {
                **dict(row),
                "currency_exponent": _EXPONENTS.get(str(row["currency"]).lower()),
                "price_per_minute_cents": round(row["price_cents"] / row["minutes"], 2),
            }
            for row in packages
        ],
    }
