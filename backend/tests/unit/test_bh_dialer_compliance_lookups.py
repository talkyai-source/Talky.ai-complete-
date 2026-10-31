"""outbound-telephony-dialer-10: pre-dial compliance lookups must fail CLOSED.

Before the fix `_get_tenant_rules` fell back to ``CallingRules.default()``,
`_get_campaign_calling_config` to ``None``, `_get_lead_last_called` to
``None`` and `_get_lead_attempts_today` to ``0`` on ANY exception. One failed
query (pool-acquire timeout, statement timeout) therefore turned the calling
window, the campaign schedule overlay, the lead cooldown and the per-lead
daily cap into "allow" and the job originated.

The loaders now raise ``ComplianceLookupUnavailable`` on a query failure
(row absent / NULL still means "use defaults") and ``process_job`` re-defers
the SAME attempt instead of dialling.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest

from app.domain.models.calling_rules import CallingRules
from app.domain.models.dialer_job import DialerJob
from app.workers.dialer_worker import DialerCallIntent, DialerWorker


def _job() -> DialerJob:
    return DialerJob(
        job_id="job-10",
        campaign_id="campaign-10",
        lead_id="lead-10",
        tenant_id="tenant-10",
        phone_number="+15551234567",
    )


def _worker_with_conn(conn) -> DialerWorker:
    w = DialerWorker()

    @asynccontextmanager
    async def _acquire():
        yield conn

    w._acquire_db = _acquire  # type: ignore[assignment]
    return w


def _failing_conn(exc: Exception):
    conn = MagicMock()
    conn.fetchrow = AsyncMock(side_effect=exc)
    conn.fetchval = AsyncMock(side_effect=exc)
    conn.fetch = AsyncMock(side_effect=exc)
    return conn


def _empty_conn():
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=None)
    conn.fetchval = AsyncMock(return_value=None)
    return conn


# ── loaders ──────────────────────────────────────────────────────────

_LOADERS = [
    ("_get_tenant_rules", "tenant-10"),
    ("_get_campaign_calling_config", "campaign-10"),
    ("_get_lead_last_called", "lead-10"),
    ("_get_lead_attempts_today", "lead-10"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("name,arg", _LOADERS)
async def test_loader_raises_typed_error_when_the_query_fails(name, arg):
    w = _worker_with_conn(_failing_conn(asyncio_timeout()))
    with pytest.raises(RuntimeError) as info:
        await getattr(w, name)(arg)
    assert type(info.value).__name__ == "ComplianceLookupUnavailable"


def asyncio_timeout() -> Exception:
    import asyncio

    return asyncio.TimeoutError("pool acquire timed out")


@pytest.mark.asyncio
async def test_absent_rows_still_mean_defaults_not_unavailable():
    w = _worker_with_conn(_empty_conn())
    rules = await w._get_tenant_rules("tenant-10")
    assert rules.time_window_start == CallingRules.default().time_window_start
    assert await w._get_campaign_calling_config("campaign-10") is None
    assert await w._get_lead_last_called("lead-10") is None
    assert await w._get_lead_attempts_today("lead-10") == 0


@pytest.mark.asyncio
async def test_lead_timezone_db_error_raises_but_missing_column_stays_best_effort():
    w = _worker_with_conn(_failing_conn(asyncpg.PostgresConnectionError("blip")))
    with pytest.raises(RuntimeError) as info:
        await w._get_lead_timezone("lead-10")
    assert type(info.value).__name__ == "ComplianceLookupUnavailable"

    # Pre-0020 database: the column does not exist -> documented best-effort None.
    w = _worker_with_conn(
        _failing_conn(asyncpg.UndefinedColumnError('column "timezone" does not exist'))
    )
    assert await w._get_lead_timezone("lead-10") is None


# ── process_job wiring ───────────────────────────────────────────────


def _base_worker() -> DialerWorker:
    w = DialerWorker()
    w.queue_service = AsyncMock()
    w._redis = None
    w._db_pool = None
    w._get_campaign_status = AsyncMock(return_value="running")
    w._stale_payload_reason = AsyncMock(return_value=None)
    w._load_existing_call_intent = AsyncMock(return_value=None)
    w._tenant_minutes_exhausted = AsyncMock(return_value=False)
    # A cap of 3/day so the attempts-today loader is actually consulted.
    rules = CallingRules.default()
    rules.max_calls_per_lead_per_day = 3
    w._get_tenant_rules = AsyncMock(return_value=rules)
    w._get_campaign_calling_config = AsyncMock(return_value={})
    w._get_lead_last_called = AsyncMock(return_value=None)
    w._get_lead_attempts_today = AsyncMock(return_value=0)
    w._get_lead_timezone = AsyncMock(return_value=None)
    w.rules_engine.can_make_call = AsyncMock(return_value=(True, ""))
    w._resolve_batch_size = MagicMock(return_value=0)
    w._resolve_call_gap = MagicMock(return_value=0)
    w._evaluate_call_guard = AsyncMock(return_value="allow")
    w._update_job_status = AsyncMock()
    w._make_call = AsyncMock(return_value="provider-call-1")
    intent = DialerCallIntent("call-1", "tk-1", "leg-1", "initiated", None, True)
    w._create_call_intent = AsyncMock(return_value=intent)
    w._bind_call_intent = AsyncMock()
    w._update_lead_status = AsyncMock()
    w._mark_campaign_dialed = AsyncMock()
    w._emit_progress_event_throttled = AsyncMock()
    w._publish_reason = AsyncMock()
    return w


def _unavailable():
    import app.workers.dialer_worker as dw

    return getattr(dw, "ComplianceLookupUnavailable", RuntimeError)("db blip")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "loader",
    [
        "_get_tenant_rules",
        "_get_campaign_calling_config",
        "_get_lead_last_called",
        "_get_lead_timezone",
        "_get_lead_attempts_today",
    ],
)
async def test_failed_compliance_lookup_defers_the_job_and_never_dials(loader):
    w = _base_worker()
    setattr(w, loader, AsyncMock(side_effect=_unavailable()))

    await w.process_job(_job())

    w._make_call.assert_not_awaited()
    w._create_call_intent.assert_not_awaited()
    w.queue_service.schedule_retry.assert_not_awaited()
    w.queue_service.mark_failed.assert_not_awaited()
    w.queue_service._redefer_inflight.assert_awaited_once()
    _, kwargs = w.queue_service._redefer_inflight.call_args
    assert kwargs["delay_seconds"] >= 30
    _, status_kwargs = w._update_job_status.call_args
    assert status_kwargs.get("reason") == "rules_unavailable"


@pytest.mark.asyncio
async def test_failed_lookup_on_a_replayed_intent_parks_the_same_attempt():
    """A resumed attempt that already owns a durable call row must stay parked
    on the SAME attempt (never a new one) when its rules cannot be read."""
    w = _base_worker()
    intent = DialerCallIntent("call-1", "tk-1", "leg-1", "initiated", None, False)
    w._load_existing_call_intent = AsyncMock(return_value=intent)
    w._get_tenant_rules = AsyncMock(side_effect=_unavailable())
    w._park_uncertain_origination = AsyncMock()

    await w.process_job(_job())

    w._make_call.assert_not_awaited()
    w._park_uncertain_origination.assert_awaited_once()
    w.queue_service.schedule_retry.assert_not_awaited()
