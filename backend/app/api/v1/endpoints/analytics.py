"""
Analytics Endpoints — call time-series for the dashboard charts.

Calls finish as status='ended'/'completed' with the real result in `outcome`,
so series are classified by `outcome` (keying on `status` under-counts badly).
Supports hour/day/week/month buckets and a per-campaign breakdown.
"""
from app.core.error_handlers import safe_exception_message
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel
from typing import Annotated, Dict, List, Literal, Optional
from datetime import datetime, timedelta, timezone
from app.core.postgres_adapter import Client

from app.api.v1.dependencies import get_db_client, get_current_user, CurrentUser
from app.core.security.rbac import Permission, require_permission
from app.utils.tenant_filter import apply_tenant_filter
from app.domain.services.call_outcomes import (
    ANSWERED_OUTCOMES,
    FAILED_OUTCOMES,
    GOAL_OUTCOMES,
    classify_outcome,
)
from app.domain.services.reporting_time import (
    ReportingTimeUnavailable, calendar_range, resolve_reporting_zone,
)

router = APIRouter(prefix="/analytics", tags=["analytics"])
_require_analytics_read = require_permission(Permission.ANALYTICS_READ)

# A call "connected" (answered) vs "failed" is decided by outcome, not status.
# Canonical definitions live in `call_outcomes`; these aliases are kept so the
# module-local names (and the tests that import them) keep working.
_ANSWERED_OUTCOMES = ANSWERED_OUTCOMES
_FAILED_OUTCOMES = FAILED_OUTCOMES
_GOAL_OUTCOMES = GOAL_OUTCOMES

_VALID_GROUP_BY = {"hour", "day", "week", "month"}


class CallSeriesItem(BaseModel):
    """Single bucket in a call series."""
    date: str
    total_calls: int
    answered: int
    failed: int
    # Calls in neither list (no outcome yet, or an unlisted outcome), so that
    # answered + failed + other == total_calls on every row.
    other: int = 0
    goal_achieved: int = 0


class CallSummary(BaseModel):
    """Totals for the whole answer; answered + failed + other == total_calls."""
    total_calls: int = 0
    answered: int = 0
    failed: int = 0
    other: int = 0
    goal_achieved: int = 0


class WindowBucket(BaseModel):
    """One fixed-length slice of a short window (``window_hours``)."""
    start: str
    end: str
    total_calls: int
    answered: int
    failed: int
    other: int


class CampaignSeries(BaseModel):
    campaign_id: str
    name: str
    series: List[CallSeriesItem]


class CallAnalyticsResponse(BaseModel):
    series: List[CallSeriesItem]
    timezone: str = "UTC"
    direction: Literal["outbound", "inbound", "all"] = "outbound"
    include_tests: bool = False
    summary: Optional[CallSummary] = None
    # Only with ?window_hours=: `series` is then empty and `buckets` is filled.
    window_hours: Optional[int] = None
    bucket_minutes: Optional[int] = None
    buckets: Optional[List[WindowBucket]] = None


class CampaignAnalyticsResponse(BaseModel):
    campaigns: List[CampaignSeries]
    timezone: str = "UTC"
    direction: Literal["outbound", "inbound", "all"] = "outbound"
    include_tests: bool = False


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
    label = classify_outcome(o)
    bucket[label] = bucket.get(label, 0) + 1
    if o in _GOAL_OUTCOMES:
        bucket["goal"] += 1


def _resolve_range(from_date: Optional[str], to_date: Optional[str], default_days: int = 30, *, zone=None):
    return calendar_range(from_date, to_date, zone=zone, default_days=default_days)


def _to_series(buckets: Dict[str, dict]) -> List[CallSeriesItem]:
    return [
        CallSeriesItem(
            date=k,
            total_calls=g["total"],
            answered=g["answered"],
            failed=g["failed"],
            other=g.get("other", 0),
            goal_achieved=g["goal"],
        )
        for k, g in sorted(buckets.items())
    ]


def _summarise(groups) -> CallSummary:
    return CallSummary(
        total_calls=sum(g["total"] for g in groups),
        answered=sum(g["answered"] for g in groups),
        failed=sum(g["failed"] for g in groups),
        other=sum(g.get("other", 0) for g in groups),
        goal_achieved=sum(g["goal"] for g in groups),
    )


# window_hours -> bucket length in minutes (1h/4h: 15 min, 8h: 30 min, 24h: 60 min).
_WINDOW_BUCKET_MINUTES = {1: 15, 4: 15, 8: 30, 24: 60}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _window_buckets(rows, now: datetime, hours: int, zone):
    """Fixed-length buckets over [now - hours, now), counted by ``created_at``
    (the same instant the daily view uses). Bounds are absolute instants, so a
    daylight-saving change only shows as a different offset in the labels."""
    minutes = _WINDOW_BUCKET_MINUTES[hours]
    step = timedelta(minutes=minutes)
    start = now - timedelta(hours=hours)
    counts = [{"total": 0, "answered": 0, "failed": 0, "other": 0, "goal": 0}
              for _ in range(hours * 60 // minutes)]
    for call in rows:
        dt = _parse_dt(call.get("created_at"))
        if dt is None or dt < start or dt >= now:
            continue
        _classify(call.get("outcome"), counts[int((dt - start) / step)])
    out = [
        WindowBucket(
            start=(start + i * step).astimezone(zone).isoformat(),
            end=(start + (i + 1) * step).astimezone(zone).isoformat(),
            total_calls=g["total"], answered=g["answered"], failed=g["failed"], other=g["other"],
        )
        for i, g in enumerate(counts)
    ]
    return out, counts, minutes


def _apply_call_population(
    query,
    *,
    direction: Literal["outbound", "inbound", "all"],
    include_tests: bool,
):
    """Make the analytics population explicit and fail closed by default.

    Dialer analytics historically mixed real outbound calls, inbound callers,
    and browser tests.  Every endpoint now passes through this helper so a new
    chart cannot accidentally reintroduce that mixed population.
    """
    if direction != "all":
        query = query.eq("direction", direction)
    if not include_tests:
        query = query.eq("is_test", False)
    return query


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
    timezone: str = "UTC"


# Minimum calls in an hour before it's eligible to be the "best hour" — keeps
# a single lucky answer from a dead hour winning the recommendation.
_BEST_HOUR_MIN_VOLUME = 5


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


@router.get(
    "/best-time",
    response_model=BestTimeResponse,
    dependencies=[Depends(_require_analytics_read)],
)
async def get_best_time_to_call(
    from_date: Optional[str] = Query(None, alias="from", description="Start date (YYYY-MM-DD)"),
    to_date: Optional[str] = Query(None, alias="to", description="End date (YYYY-MM-DD)"),
    tz: Annotated[Optional[str], Query(description="IANA reporting timezone; defaults to the account setting, then UTC")] = None,
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
):
    """Answer + goal rate by hour-of-day so the user knows WHEN to dial.

    Each call is bucketed by the hour-of-day of its ``created_at`` in the
    requested timezone; we report total/answered/goal and the rates, plus
    the single best hour (highest answer rate among hours with enough
    volume to be trustworthy)."""
    try:
        zone = resolve_reporting_zone(db_client, current_user.tenant_id, requested=tz)
        start_dt, end_dt_excl = _resolve_range(from_date, to_date, zone=zone)
        query = db_client.table("calls").select("created_at, outcome")
        query = query.gte("created_at", start_dt).lt("created_at", end_dt_excl)
        query = _apply_call_population(
            query, direction="outbound", include_tests=False
        )
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
    except ReportingTimeUnavailable:
        raise HTTPException(status_code=503, detail="Reporting timezone unavailable.", headers={"Retry-After": "10"})
    except ValueError as e:
        raise HTTPException(status_code=400, detail=safe_exception_message(e, 'Invalid date format.'))
    except Exception as e:
        raise HTTPException(status_code=500, detail=safe_exception_message(e, 'Failed to fetch analytics.'))


@router.get(
    "/retry-effectiveness",
    response_model=RetryEffectivenessResponse,
    dependencies=[Depends(_require_analytics_read)],
)
async def get_retry_effectiveness(
    from_date: Optional[str] = Query(None, alias="from", description="Start date (YYYY-MM-DD)"),
    to_date: Optional[str] = Query(None, alias="to", description="End date (YYYY-MM-DD)"),
    tz: Annotated[Optional[str], Query(description="IANA reporting timezone; defaults to the account setting, then UTC")] = None,
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
):
    """Does retrying actually pay off? Answer/goal rate by attempt ordinal.

    We order every lead's calls by time and label them 1st, 2nd, 3rd…
    attempt, then report the answer + goal rate at each ordinal. A sharp
    drop after attempt 1 tells you the retries aren't converting; a flat
    line says they are."""
    try:
        zone = resolve_reporting_zone(db_client, current_user.tenant_id, requested=tz)
        start_dt, end_dt_excl = _resolve_range(from_date, to_date, zone=zone)
        query = db_client.table("calls").select("lead_id, created_at, outcome")
        query = query.gte("created_at", start_dt).lt("created_at", end_dt_excl)
        query = _apply_call_population(
            query, direction="outbound", include_tests=False
        )
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
        return RetryEffectivenessResponse(attempts=result, timezone=str(zone))
    except HTTPException:
        raise
    except ReportingTimeUnavailable:
        raise HTTPException(status_code=503, detail="Reporting timezone unavailable.", headers={"Retry-After": "10"})
    except ValueError as e:
        raise HTTPException(status_code=400, detail=safe_exception_message(e, 'Invalid date format.'))
    except Exception as e:
        raise HTTPException(status_code=500, detail=safe_exception_message(e, 'Failed to fetch analytics.'))


@router.get(
    "/calls",
    response_model=CallAnalyticsResponse,
    dependencies=[Depends(_require_analytics_read)],
)
async def get_call_analytics(
    from_date: Optional[str] = Query(None, alias="from", description="Start date (YYYY-MM-DD)"),
    to_date: Optional[str] = Query(None, alias="to", description="End date (YYYY-MM-DD)"),
    tz: Annotated[Optional[str], Query(description="IANA reporting timezone; defaults to the account setting, then UTC")] = None,
    group_by: str = Query("day", description="Grouping: hour, day, week, month"),
    direction: Literal["outbound", "inbound", "all"] = Query(
        "outbound", description="Call direction population"
    ),
    include_tests: bool = Query(False, description="Include browser/test calls"),
    window_hours: Optional[str] = Query(
        None,
        description="Last N hours ending now: 1, 4, 8 or 24. Replaces from/to; "
        "returned in `buckets` of 15 (1h, 4h), 30 (8h) or 60 (24h) minutes.",
    ),
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
    window = None
    if window_hours is not None:
        if window_hours.strip() not in {str(h) for h in _WINDOW_BUCKET_MINUTES}:
            raise HTTPException(status_code=422, detail="window_hours must be one of 1, 4, 8 or 24.")
        if from_date is not None or to_date is not None:
            raise HTTPException(status_code=422, detail="window_hours cannot be combined with from or to.")
        window = int(window_hours)
    try:
        zone = resolve_reporting_zone(db_client, current_user.tenant_id, requested=tz)
        if window is not None:
            now = _utc_now()
            start_dt, end_dt_excl = now - timedelta(hours=window), now
        else:
            start_dt, end_dt_excl = _resolve_range(from_date, to_date, zone=zone)
        query = db_client.table("calls").select("created_at, outcome")
        query = query.gte("created_at", start_dt).lt("created_at", end_dt_excl)
        query = _apply_call_population(
            query, direction=direction, include_tests=include_tests
        )
        query = apply_tenant_filter(query, current_user.tenant_id)
        response = query.order("created_at").execute()
        if getattr(response, "error", None):
            raise HTTPException(status_code=500, detail=f"Failed to fetch analytics: {response.error}")

        if window is not None:
            buckets, counts, minutes = _window_buckets(response.data or [], now, window, zone)
            return CallAnalyticsResponse(
                series=[],
                timezone=str(zone),
                direction=direction,
                include_tests=include_tests,
                summary=_summarise(counts),
                window_hours=window,
                bucket_minutes=minutes,
                buckets=buckets,
            )

        groups: Dict[str, dict] = {}
        for call in response.data or []:
            dt = _parse_dt(call.get("created_at"))
            if dt is None:
                continue
            bucket = groups.setdefault(
                _bucket_key(dt.astimezone(zone), group_by),
                {"total": 0, "answered": 0, "failed": 0, "goal": 0},
            )
            _classify(call.get("outcome"), bucket)

        return CallAnalyticsResponse(
            series=_to_series(groups),
            timezone=str(zone),
            direction=direction,
            include_tests=include_tests,
            summary=_summarise(groups.values()),
        )
    except HTTPException:
        raise
    except ReportingTimeUnavailable:
        raise HTTPException(status_code=503, detail="Reporting timezone unavailable.", headers={"Retry-After": "10"})
    except ValueError as e:
        raise HTTPException(status_code=400, detail=safe_exception_message(e, 'Invalid date format.'))
    except Exception as e:
        raise HTTPException(status_code=500, detail=safe_exception_message(e, 'Failed to fetch analytics.'))


@router.get(
    "/calls/by-campaign",
    response_model=CampaignAnalyticsResponse,
    dependencies=[Depends(_require_analytics_read)],
)
async def get_call_analytics_by_campaign(
    from_date: Optional[str] = Query(None, alias="from", description="Start date (YYYY-MM-DD)"),
    to_date: Optional[str] = Query(None, alias="to", description="End date (YYYY-MM-DD)"),
    tz: Annotated[Optional[str], Query(description="IANA reporting timezone; defaults to the account setting, then UTC")] = None,
    group_by: str = Query("day", description="Grouping: hour, day, week, month"),
    direction: Literal["outbound", "inbound", "all"] = Query(
        "outbound", description="Call direction population"
    ),
    include_tests: bool = Query(False, description="Include browser/test calls"),
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
        zone = resolve_reporting_zone(db_client, current_user.tenant_id, requested=tz)
        start_dt, end_dt_excl = _resolve_range(from_date, to_date, zone=zone)
        query = db_client.table("calls").select("created_at, outcome, campaign_id")
        query = query.gte("created_at", start_dt).lt("created_at", end_dt_excl)
        query = _apply_call_population(
            query, direction=direction, include_tests=include_tests
        )
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
                _bucket_key(dt.astimezone(zone), group_by),
                {"total": 0, "answered": 0, "failed": 0, "goal": 0},
            )
            _classify(call.get("outcome"), bucket)

        # Resolve campaign names (tenant-scoped) for the ids we actually saw.
        names: Dict[str, str] = {}
        cids = list(per_campaign.keys())
        if cids:
            name_q = db_client.table("campaigns").select("id, name")
            name_q = apply_tenant_filter(name_q, current_user.tenant_id)
            if direction != "all":
                name_q = name_q.eq("direction", direction)
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
        return CampaignAnalyticsResponse(
            campaigns=campaigns,
            timezone=str(zone),
            direction=direction,
            include_tests=include_tests,
        )
    except HTTPException:
        raise
    except ReportingTimeUnavailable:
        raise HTTPException(status_code=503, detail="Reporting timezone unavailable.", headers={"Retry-After": "10"})
    except ValueError as e:
        raise HTTPException(status_code=400, detail=safe_exception_message(e, 'Invalid date format.'))
    except Exception as e:
        raise HTTPException(status_code=500, detail=safe_exception_message(e, 'Failed to fetch analytics.'))
