"""
Admin Usage Endpoints
Usage analytics: summary and breakdown by provider/tenant/type
"""

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel
from typing import List, Literal, Optional
from datetime import date, datetime, time, timedelta, timezone
from math import isfinite
from app.core.postgres_adapter import Client

from app.api.v1.dependencies import get_db_client, require_platform_admin, CurrentUser

router = APIRouter()

_COST_CURRENCY = "USD"
_INBOUND_MONETARY_SOURCE = "inbound_usage_transactions"
_INBOUND_MONETARY_NOTE = (
    "Supplier cost and provider attribution are unavailable. Legacy outbound USD "
    "estimates cover only recorded calls.cost values, not complete supplier spend. "
    "Authoritative inbound monetary totals are ledger-only and excluded. "
    "Counts and recorded duration include both directions; action records are not API requests."
)


class LegacyOutboundEstimate(BaseModel):
    """Known legacy values only; never complete supplier-cost coverage."""

    recorded_total: Optional[float]
    covered_call_count: int
    missing_call_count: int
    currency: Literal["USD"] = "USD"
    coverage: Literal["unavailable", "partial", "recorded_rows_only"]


def _legacy_outbound_estimate(calls: list[dict]) -> LegacyOutboundEstimate:
    costs = []
    missing = 0
    for call in calls:
        if str(call.get("direction") or "outbound").strip().lower() == "inbound":
            continue
        value = call.get("cost")
        try:
            amount = float(value) if value is not None and not isinstance(value, bool) else None
        except (TypeError, ValueError, OverflowError):
            amount = None
        if amount is None or not isfinite(amount):
            missing += 1
        else:
            costs.append(amount)
    total = sum(costs) if costs else None
    return LegacyOutboundEstimate(
        recorded_total=total if total is not None and isfinite(total) else None,
        covered_call_count=len(costs),
        missing_call_count=missing,
        coverage="unavailable" if not costs else "partial" if missing else "recorded_rows_only",
    )


def _legacy_outbound_call_cost(calls: list[dict]) -> Optional[float]:
    """Compatibility projection of known legacy outbound values, not supplier spend."""
    return _legacy_outbound_estimate(calls).recorded_total


def _rows(response) -> list[dict]:
    # The adapter may return an error envelope instead of raising. Never turn
    # a dependency failure into an apparently empty usage period.
    if getattr(response, "error", None):
        raise RuntimeError("usage query unavailable")
    if not isinstance(response.data, list):
        raise RuntimeError("usage rows unavailable")
    return response.data


def _period(from_date: Optional[str], to_date: Optional[str]):
    today = datetime.now(timezone.utc).date()
    start_text = from_date if from_date is not None else today.replace(day=1).isoformat()
    end_text = to_date if to_date is not None else today.isoformat()
    try:
        start, end = date.fromisoformat(start_text), date.fromisoformat(end_text)
        if start.isoformat() != start_text or end.isoformat() != end_text or start > end:
            raise ValueError("invalid report period")
        return (start_text, end_text,
                datetime.combine(start, time.min, timezone.utc),
                datetime.combine(end + timedelta(days=1), time.min, timezone.utc))
    except (ValueError, TypeError, OverflowError) as exc:
        raise HTTPException(status_code=400,
                            detail="Use YYYY-MM-DD dates with from_date on or before to_date.") from exc


def _monetary_scope() -> dict:
    return {
        "cost_currency": _COST_CURRENCY,
        "legacy_calls_cost_scope": "outbound_only",
        "authoritative_inbound_monetary_totals_included": False,
        "authoritative_inbound_monetary_source": _INBOUND_MONETARY_SOURCE,
        "monetary_note": _INBOUND_MONETARY_NOTE,
        "supplier_cost_status": "unavailable",
    }


# =============================================================================
# Response Models
# =============================================================================


class UsageBreakdownItem(BaseModel):
    """Usage breakdown by provider"""

    provider: str  # deepgram, groq, openai, twilio
    usage_type: str  # stt, tts, llm, sms, calls
    total_units: int  # seconds, tokens, count
    estimated_cost: Optional[float]
    tenant_count: int


class UsageSummaryResponse(BaseModel):
    """Aggregated usage summary"""

    total_cost: Optional[float]
    total_call_seconds: int
    total_call_minutes: int
    total_action_records: int
    total_api_calls: int  # Deprecated compatibility alias: action records, not API requests.
    legacy_outbound_estimate: LegacyOutboundEstimate
    supplier_cost_status: Literal["unavailable"] = "unavailable"
    providers: List[UsageBreakdownItem]
    period_start: str
    period_end: str
    cost_currency: str = _COST_CURRENCY
    legacy_calls_cost_scope: Literal["outbound_only"] = "outbound_only"
    authoritative_inbound_monetary_totals_included: bool = False
    authoritative_inbound_monetary_source: str = _INBOUND_MONETARY_SOURCE
    monetary_note: str = _INBOUND_MONETARY_NOTE


# =============================================================================
# Endpoints
# =============================================================================


@router.get("/usage/summary", response_model=UsageSummaryResponse)
async def get_admin_usage_summary(
    admin_user: CurrentUser = Depends(require_platform_admin),
    db_client: Client = Depends(get_db_client),
    tenant_id: Optional[str] = Query(None, description="Filter by tenant"),
    from_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    to_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
):
    """
    Get aggregated usage summary across providers.

    Returns recorded duration/action counts and explicitly incomplete legacy estimates.
    Supplier cost and provider attribution are unavailable from these source rows.
    """
    try:
        from_date, to_date, start_at, end_at = _period(from_date, to_date)

        # Get call statistics
        calls_query = db_client.table("calls").select(
            "id, duration_seconds, cost, tenant_id, direction"
        )
        if tenant_id:
            calls_query = calls_query.eq("tenant_id", tenant_id)
        calls_query = calls_query.gte("created_at", start_at).lt("created_at", end_at)
        calls_response = calls_query.execute()

        calls = _rows(calls_response)
        total_call_seconds = sum((c.get("duration_seconds") or 0) for c in calls)
        total_call_minutes = total_call_seconds // 60

        # Get actions for API usage
        actions_query = db_client.table("assistant_actions").select("id, type, tenant_id")
        if tenant_id:
            actions_query = actions_query.eq("tenant_id", tenant_id)
        actions_query = actions_query.gte("created_at", start_at).lt("created_at", end_at)
        actions_response = actions_query.execute()

        actions = _rows(actions_response)
        total_action_records = len(actions)

        # Calls and action rows cannot establish supplier/model usage or spend.
        # The optional cost-event buffer is lossy and has no wired provider
        # producers in this source snapshot; absence cannot establish zero.
        return UsageSummaryResponse(
            total_cost=None,
            total_call_seconds=total_call_seconds,
            total_call_minutes=total_call_minutes,
            total_action_records=total_action_records,
            total_api_calls=total_action_records,
            legacy_outbound_estimate=_legacy_outbound_estimate(calls),
            providers=[],
            period_start=from_date,
            period_end=to_date,
            **_monetary_scope(),
        )

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail="Usage data is unavailable. Please retry."
        ) from exc


@router.get("/usage/breakdown")
async def get_admin_usage_breakdown(
    admin_user: CurrentUser = Depends(require_platform_admin),
    db_client: Client = Depends(get_db_client),
    group_by: str = Query("provider", description="Group by: provider, tenant, type"),
    tenant_id: Optional[str] = Query(None, description="Filter by tenant"),
    from_date: Optional[str] = Query(None, description="Start date"),
    to_date: Optional[str] = Query(None, description="End date"),
):
    """
    Get detailed usage breakdown.

    Can group by provider, tenant, or usage type.
    """
    try:
        from_date, to_date, start_at, end_at = _period(from_date, to_date)

        # Get call data with tenant info
        calls_query = db_client.table("calls").select(
            "id, tenant_id, duration_seconds, cost, direction, created_at, "
            "tenants(business_name)"
        )
        if tenant_id:
            calls_query = calls_query.eq("tenant_id", tenant_id)
        calls_query = calls_query.gte("created_at", start_at).lt("created_at", end_at)
        calls_response = calls_query.execute()

        calls = _rows(calls_response)

        breakdown = []

        if group_by == "tenant":
            tenant_calls = {}
            for call in calls:
                tenant_calls.setdefault(call.get("tenant_id"), []).append(call)
            for tid, rows in tenant_calls.items():
                seconds = sum((row.get("duration_seconds") or 0) for row in rows)
                breakdown.append(
                    {
                        "tenant_id": tid,
                        "tenant_name": (rows[0].get("tenants") or {}).get(
                            "business_name", "Unknown"
                        ),
                        "call_count": len(rows),
                        "total_seconds": seconds,
                        "total_minutes": seconds // 60,
                        "total_cost": None,
                        "legacy_outbound_estimate": _legacy_outbound_estimate(rows).model_dump(),
                    }
                )

        elif group_by == "type":
            seconds = sum((c.get("duration_seconds") or 0) for c in calls)
            breakdown = [
                {
                    "type": "voice_calls",
                    "total_units": seconds // 60,
                    "total_seconds": seconds,
                    "total_cost": None,
                    "count": len(calls),
                    "legacy_outbound_estimate": _legacy_outbound_estimate(calls).model_dump(),
                }
            ]
            actions_query = db_client.table("assistant_actions").select("type")
            if tenant_id:
                actions_query = actions_query.eq("tenant_id", tenant_id)
            actions_query = actions_query.gte("created_at", start_at).lt("created_at", end_at)
            action_types = {}
            for action in _rows(actions_query.execute()):
                atype = action.get("type", "unknown")
                action_types[atype] = action_types.get(atype, 0) + 1
            for atype, count in action_types.items():
                breakdown.append(
                    {
                        "type": atype,
                        "total_units": count,
                        "total_cost": None,
                        "count": count,
                        "unit": "action_records",
                    }
                )
        # Provider attribution remains empty, rather than naming providers that
        # these rows do not establish. This does not mean no usage occurred.

        return {
            "breakdown": breakdown,
            "group_by": group_by,
            "period_start": from_date,
            "period_end": to_date,
            **_monetary_scope(),
        }

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail="Usage data is unavailable. Please retry."
        ) from exc
