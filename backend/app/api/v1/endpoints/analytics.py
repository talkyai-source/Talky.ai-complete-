"""
Analytics Endpoints — call time-series for the dashboard charts.

Calls finish as status='ended'/'completed' with the real result in `outcome`,
so series are classified by `outcome` (keying on `status` under-counts badly).
Supports hour/day/week/month buckets and a per-campaign breakdown.
"""
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel
from typing import Dict, List, Optional
from datetime import date, datetime, time, timedelta, timezone
from app.core.postgres_adapter import Client

from app.api.v1.dependencies import get_db_client, get_current_user, CurrentUser
from app.utils.tenant_filter import apply_tenant_filter

router = APIRouter(prefix="/analytics", tags=["analytics"])

# A call "connected" (answered) vs "failed" is decided by outcome, not status.
_ANSWERED_OUTCOMES = {
    "answered", "customer_hung_up", "agent_hung_up",
    "goal_achieved", "goal_not_achieved",
}
_FAILED_OUTCOMES = {
    "no_answer", "busy", "rejected", "unreachable",
    "network_failure", "failed", "cancelled", "voicemail",
}
_GOAL_OUTCOMES = {"goal_achieved"}

_VALID_GROUP_BY = {"hour", "day", "week", "month"}


class CallSeriesItem(BaseModel):
    """Single bucket in a call series."""
    date: str
    total_calls: int
    answered: int
    failed: int
    goal_achieved: int = 0


class CampaignSeries(BaseModel):
    campaign_id: str
    name: str
    series: List[CallSeriesItem]


class CallAnalyticsResponse(BaseModel):
    series: List[CallSeriesItem]


class CampaignAnalyticsResponse(BaseModel):
    campaigns: List[CampaignSeries]


def _parse_dt(value) -> Optional[datetime]:
    try:
        if isinstance(value, datetime):
            dt = value
        elif isinstance(value, str):
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        else:
            return None
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _bucket_key(dt: datetime, group_by: str) -> str:
    if group_by == "hour":
        return dt.strftime("%Y-%m-%dT%H:00")
    if group_by == "week":  # week starts Monday
        return (dt - timedelta(days=dt.weekday())).strftime("%Y-%m-%d")
    if group_by == "month":
        return dt.strftime("%Y-%m-01")
    return dt.strftime("%Y-%m-%d")


def _classify(outcome: Optional[str], bucket: dict) -> None:
    bucket["total"] += 1
    o = (outcome or "").lower()
    if o in _ANSWERED_OUTCOMES:
        bucket["answered"] += 1
    elif o in _FAILED_OUTCOMES:
        bucket["failed"] += 1
    if o in _GOAL_OUTCOMES:
        bucket["goal"] += 1


def _resolve_range(from_date: Optional[str], to_date: Optional[str], default_days: int = 30):
    end_date = date.fromisoformat(to_date) if to_date else datetime.now(timezone.utc).date()
    start_date = date.fromisoformat(from_date) if from_date else end_date - timedelta(days=default_days)
    if start_date > end_date:
        raise HTTPException(status_code=400, detail="'from' date cannot be later than 'to' date")
    start_dt = datetime.combine(start_date, time.min, tzinfo=timezone.utc)
    end_dt_excl = datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=timezone.utc)
    return start_dt, end_dt_excl


def _to_series(buckets: Dict[str, dict]) -> List[CallSeriesItem]:
    return [
        CallSeriesItem(
            date=k,
            total_calls=g["total"],
            answered=g["answered"],
            failed=g["failed"],
            goal_achieved=g["goal"],
        )
        for k, g in sorted(buckets.items())
    ]


class HourStat(BaseModel):
    """Answer/goal performance for one hour-of-day (0–23)."""
    hour: int
    total: int
    answered: int
    answer_rate: float
    goal_achieved: int
    goal_rate: float


class BestTimeResponse(BaseModel):
    timezone: str
    hours: List[HourStat]
    best_hour: Optional[int] = None  # highest answer_rate among hours with enough volume


class AttemptStat(BaseModel):
    """Performance of the Nth dial attempt to a lead (1 = first call)."""
    attempt: int
    total: int
    answered: int
    answer_rate: float
    goal_achieved: int
    goal_rate: float


class RetryEffectivenessResponse(BaseModel):
    attempts: List[AttemptStat]


# Minimum calls in an hour before it's eligible to be the "best hour" — keeps
# a single lucky answer from a dead hour winning the recommendation.
_BEST_HOUR_MIN_VOLUME = 5


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


@router.get("/best-time", response_model=BestTimeResponse)
async def get_best_time_to_call(
    from_date: Optional[str] = Query(None, alias="from", description="Start date (YYYY-MM-DD)"),
    to_date: Optional[str] = Query(None, alias="to", description="End date (YYYY-MM-DD)"),
    tz: str = Query("UTC", description="IANA timezone the hours are reported in"),
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
):
    """Answer + goal rate by hour-of-day so the user knows WHEN to dial.

    Each call is bucketed by the hour-of-day of its ``created_at`` in the
    requested timezone; we report total/answered/goal and the rates, plus
    the single best hour (highest answer rate among hours with enough
    volume to be trustworthy)."""
    import pytz
    try:
        zone = pytz.timezone(tz)
    except Exception:
        zone = pytz.UTC

    try:
        start_dt, end_dt_excl = _resolve_range(from_date, to_date)
        query = db_client.table("calls").select("created_at, outcome")
        query = query.gte("created_at", start_dt).lt("created_at", end_dt_excl)
        query = apply_tenant_filter(query, current_user.tenant_id)
        response = query.execute()
        if getattr(response, "error", None):
            raise HTTPException(status_code=500, detail=f"Failed to fetch analytics: {response.error}")

        buckets: Dict[int, dict] = {
            h: {"total": 0, "answered": 0, "failed": 0, "goal": 0} for h in range(24)
        }
        for call in response.data or []:
            dt = _parse_dt(call.get("created_at"))
            if dt is None:
                continue
            hour = dt.astimezone(zone).hour
            _classify(call.get("outcome"), buckets[hour])

        hours = [
            HourStat(
                hour=h,
                total=g["total"],
                answered=g["answered"],
                answer_rate=_rate(g["answered"], g["total"]),
                goal_achieved=g["goal"],
                goal_rate=_rate(g["goal"], g["total"]),
            )
            for h, g in sorted(buckets.items())
        ]
        eligible = [h for h in hours if h.total >= _BEST_HOUR_MIN_VOLUME]
        best_hour = max(eligible, key=lambda h: h.answer_rate).hour if eligible else None
        return BestTimeResponse(timezone=str(zone), hours=hours, best_hour=best_hour)
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid date format: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch analytics: {str(e)}")


@router.get("/retry-effectiveness", response_model=RetryEffectivenessResponse)
async def get_retry_effectiveness(
    from_date: Optional[str] = Query(None, alias="from", description="Start date (YYYY-MM-DD)"),
    to_date: Optional[str] = Query(None, alias="to", description="End date (YYYY-MM-DD)"),
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
):
    """Does retrying actually pay off? Answer/goal rate by attempt ordinal.

    We order every lead's calls by time and label them 1st, 2nd, 3rd…
    attempt, then report the answer + goal rate at each ordinal. A sharp
    drop after attempt 1 tells you the retries aren't converting; a flat
    line says they are."""
    try:
        start_dt, end_dt_excl = _resolve_range(from_date, to_date)
        query = db_client.table("calls").select("lead_id, created_at, outcome")
        query = query.gte("created_at", start_dt).lt("created_at", end_dt_excl)
        query = apply_tenant_filter(query, current_user.tenant_id)
        response = query.execute()
        if getattr(response, "error", None):
            raise HTTPException(status_code=500, detail=f"Failed to fetch analytics: {response.error}")

        # Group calls per lead, then order each lead's calls by time so we can
        # assign a 1-based attempt ordinal.
        per_lead: Dict[str, list] = {}
        for call in response.data or []:
            lead_id = call.get("lead_id")
            dt = _parse_dt(call.get("created_at"))
            if not lead_id or dt is None:
                continue
            per_lead.setdefault(str(lead_id), []).append((dt, call.get("outcome")))

        attempts: Dict[int, dict] = {}
        for calls in per_lead.values():
            calls.sort(key=lambda c: c[0])
            for idx, (_dt, outcome) in enumerate(calls, start=1):
                bucket = attempts.setdefault(
                    idx, {"total": 0, "answered": 0, "failed": 0, "goal": 0}
                )
                _classify(outcome, bucket)

        result = [
            AttemptStat(
                attempt=n,
                total=g["total"],
                answered=g["answered"],
                answer_rate=_rate(g["answered"], g["total"]),
                goal_achieved=g["goal"],
                goal_rate=_rate(g["goal"], g["total"]),
            )
            for n, g in sorted(attempts.items())
        ]
        return RetryEffectivenessResponse(attempts=result)
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid date format: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch analytics: {str(e)}")


@router.get("/calls", response_model=CallAnalyticsResponse)
async def get_call_analytics(
    from_date: Optional[str] = Query(None, alias="from", description="Start date (YYYY-MM-DD)"),
    to_date: Optional[str] = Query(None, alias="to", description="End date (YYYY-MM-DD)"),
    group_by: str = Query("day", description="Grouping: hour, day, week, month"),
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
):
    """Aggregate call series classified by outcome. Powers the dashboard's live
    line chart, stacked-area trend, and (hour-grain) the heatmap."""
    if group_by not in _VALID_GROUP_BY:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid group_by. Must be one of: {', '.join(sorted(_VALID_GROUP_BY))}",
        )
    try:
        start_dt, end_dt_excl = _resolve_range(from_date, to_date)
        query = db_client.table("calls").select("created_at, outcome")
        query = query.gte("created_at", start_dt).lt("created_at", end_dt_excl)
        query = apply_tenant_filter(query, current_user.tenant_id)
        response = query.order("created_at").execute()
        if getattr(response, "error", None):
            raise HTTPException(status_code=500, detail=f"Failed to fetch analytics: {response.error}")

        groups: Dict[str, dict] = {}
        for call in response.data or []:
            dt = _parse_dt(call.get("created_at"))
            if dt is None:
                continue
            bucket = groups.setdefault(
                _bucket_key(dt, group_by),
                {"total": 0, "answered": 0, "failed": 0, "goal": 0},
            )
            _classify(call.get("outcome"), bucket)

        return CallAnalyticsResponse(series=_to_series(groups))
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid date format: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch analytics: {str(e)}")


@router.get("/calls/by-campaign", response_model=CampaignAnalyticsResponse)
async def get_call_analytics_by_campaign(
    from_date: Optional[str] = Query(None, alias="from", description="Start date (YYYY-MM-DD)"),
    to_date: Optional[str] = Query(None, alias="to", description="End date (YYYY-MM-DD)"),
    group_by: str = Query("day", description="Grouping: hour, day, week, month"),
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
):
    """Per-campaign call series for the dashboard's campaign-lines chart — REAL
    counts per campaign (replaces the old max_concurrent weight estimate)."""
    if group_by not in _VALID_GROUP_BY:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid group_by. Must be one of: {', '.join(sorted(_VALID_GROUP_BY))}",
        )
    try:
        start_dt, end_dt_excl = _resolve_range(from_date, to_date)
        query = db_client.table("calls").select("created_at, outcome, campaign_id")
        query = query.gte("created_at", start_dt).lt("created_at", end_dt_excl)
        query = apply_tenant_filter(query, current_user.tenant_id)
        response = query.order("created_at").execute()
        if getattr(response, "error", None):
            raise HTTPException(status_code=500, detail=f"Failed to fetch analytics: {response.error}")

        # campaign_id -> {bucket_key -> counts}
        per_campaign: Dict[str, Dict[str, dict]] = {}
        for call in response.data or []:
            cid = call.get("campaign_id")
            if not cid:
                continue
            dt = _parse_dt(call.get("created_at"))
            if dt is None:
                continue
            buckets = per_campaign.setdefault(str(cid), {})
            bucket = buckets.setdefault(
                _bucket_key(dt, group_by),
                {"total": 0, "answered": 0, "failed": 0, "goal": 0},
            )
            _classify(call.get("outcome"), bucket)

        # Resolve campaign names (tenant-scoped) for the ids we actually saw.
        names: Dict[str, str] = {}
        cids = list(per_campaign.keys())
        if cids:
            name_q = db_client.table("campaigns").select("id, name")
            name_q = apply_tenant_filter(name_q, current_user.tenant_id)
            name_q = name_q.in_("id", cids)
            for row in (name_q.execute().data or []):
                names[str(row.get("id"))] = row.get("name") or "Campaign"

        campaigns = [
            CampaignSeries(
                campaign_id=cid,
                name=names.get(cid, "Campaign"),
                series=_to_series(buckets),
            )
            for cid, buckets in per_campaign.items()
        ]
        # Busiest campaigns first (stable, useful order for the chart legend).
        campaigns.sort(key=lambda c: sum(s.total_calls for s in c.series), reverse=True)
        return CampaignAnalyticsResponse(campaigns=campaigns)
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid date format: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch analytics: {str(e)}")
