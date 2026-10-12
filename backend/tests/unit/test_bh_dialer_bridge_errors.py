"""outbound-telephony-dialer-3: bridge error codes the retry classifier did not
know (call_throttled, call_blocked, tenant_pbx_required) fell to
``INTERNAL/unknown_code_*`` -> one 60 s retry -> lead permanently ``failed``.
Separately, an ENVELOPED 409 raised ``TypeError: unhashable type: dict`` in
`_make_call` (membership test of a dict in a set), and the blanket
``except Exception`` turned every enveloped 409 into "origination uncertain".

Fix under test:
  * ``call_throttled`` / ``tenant_pbx_required`` are tenant-fixable and
    transient -> `_make_call` signals a SAME-ATTEMPT re-defer (no attempt
    burned), like metering_unavailable.
  * ``call_blocked`` is a compliance block (DNC etc.) -> never retried.
  * the 409 code is read from both body shapes (flat string / envelope).
  * unknown codes on a transient HTTP status keep the status-family category.
"""
from __future__ import annotations

import json
import logging
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from app.domain.models.calling_rules import CallingRules
from app.domain.models.dialer_job import DialerJob
from app.workers import retry_policy as rp
from app.workers.dialer_worker import DialerCallIntent, DialerWorker


def _envelope(code: str, status_message: str = "x", details=None) -> str:
    return json.dumps(
        {"error": {"code": code, "message": status_message, "details": details}}
    )


def _job() -> DialerJob:
    return DialerJob(
        job_id="job-3",
        campaign_id="campaign-3",
        lead_id="lead-3",
        tenant_id="tenant-3",
        phone_number="+15551234567",
    )


# ── retry_policy: classification ─────────────────────────────────────


def test_call_throttled_and_tenant_pbx_required_are_transient():
    for code in ("call_throttled", "tenant_pbx_required", "telephony_not_active_on_node"):
        category, reason = rp.classify_telephony_response(
            http_status=429, error_code=code
        )
        assert category == rp.FailureCategory.TRANSIENT_NETWORK, code
        assert reason == code


def test_call_blocked_is_never_retried():
    category, reason = rp.classify_telephony_response(
        http_status=429, error_code="call_blocked"
    )
    assert reason == "call_blocked"
    decision = rp.smart_decision(category=category, reason=reason, attempt_number=1)
    assert decision.should_retry is False


def test_unknown_code_on_a_transient_status_keeps_the_status_family():
    category, reason = rp.classify_telephony_response(
        http_status=503, error_code="some_new_infra_code"
    )
    assert category == rp.FailureCategory.TRANSIENT_NETWORK
    assert reason == "unknown_code_some_new_infra_code"
    category, _ = rp.classify_telephony_response(
        http_status=429, error_code="some_new_throttle"
    )
    assert category == rp.FailureCategory.TRANSIENT_NETWORK


def test_unknown_code_on_other_statuses_is_still_visible_internal():
    category, reason = rp.classify_telephony_response(
        http_status=418, error_code="brand_new_code"
    )
    assert category == rp.FailureCategory.INTERNAL
    assert reason == "unknown_code_brand_new_code"


def test_parse_bridge_error_reads_every_body_shape():
    # canonical envelope
    assert rp.parse_bridge_error(_envelope("origination_in_progress", "m")) == (
        "origination_in_progress",
        "m",
    )
    # flat JSONResponse body
    assert rp.parse_bridge_error(
        json.dumps({"error": "origination_cleanup_pending", "message": "m"})
    ) == ("origination_cleanup_pending", "m")
    # FastAPI-native {"detail": {...}} body
    assert rp.parse_bridge_error(
        json.dumps({"detail": {"error": "call_throttled"}})
    )[0] == "call_throttled"
    assert rp.parse_bridge_error("not json") == (None, None)
    assert rp.parse_bridge_error(json.dumps({"detail": "Telephony adapter not connected"})) == (
        None,
        None,
    )


# ── _make_call: reading the bridge response ──────────────────────────


class _Resp:
    def __init__(self, status: int, body: str):
        self.status = status
        self._body = body

    async def text(self):
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Session:
    def __init__(self, status: int, body: str):
        self._resp = _Resp(status, body)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def post(self, *a, **k):
        return self._resp


def _patch_session(monkeypatch, status: int, body: str) -> None:
    monkeypatch.setattr(
        aiohttp, "ClientSession", lambda *a, **k: _Session(status, body)
    )


async def _call(monkeypatch, status: int, body: str):
    _patch_session(monkeypatch, status, body)
    w = DialerWorker()
    intent = DialerCallIntent("call-1", "tk-1", "leg-1", "initiated", None, True)
    result = await w._make_call(_job(), CallingRules.default(), call_intent=intent)
    return w, result


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        _envelope("origination_cleanup_pending"),
        json.dumps({"error": "origination_cleanup_pending"}),
        _envelope("origination_in_progress"),
        json.dumps({"detail": {"error": "origination_in_progress"}}),
    ],
)
async def test_409_cleanup_pending_is_uncertain_in_both_shapes(monkeypatch, caplog, body):
    with caplog.at_level(logging.ERROR):
        w, result = await _call(monkeypatch, 409, body)
    assert result == DialerWorker._ORIGINATION_UNCERTAIN
    # It must be recognised, not the accident of a TypeError in the blanket except.
    assert "unhashable" not in caplog.text
    assert "Originate error" not in caplog.text


@pytest.mark.asyncio
async def test_deterministic_409_is_not_parked_as_uncertain(monkeypatch):
    w, result = await _call(
        monkeypatch, 409, _envelope("inbound_campaign_managed_separately")
    )
    assert result is None
    assert w._last_bridge_http_status == 409
    code, _ = rp.parse_bridge_error(w._last_bridge_body)
    assert code == "inbound_campaign_managed_separately"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,code,min_delay",
    [
        (429, "call_throttled", 60),
        (422, "tenant_pbx_required", 60),
    ],
)
async def test_tenant_fixable_rejections_signal_a_same_attempt_redefer(
    monkeypatch, status, code, min_delay
):
    w, result = await _call(monkeypatch, status, _envelope(code))
    assert result == DialerWorker._BRIDGE_TRANSIENT_REJECT
    reason, delay = w._last_bridge_defer
    assert reason == code
    assert delay >= min_delay


@pytest.mark.asyncio
async def test_throttle_honours_the_bridge_retry_after(monkeypatch):
    w, result = await _call(
        monkeypatch,
        429,
        _envelope("call_throttled", details={"retry_after_seconds": 240}),
    )
    assert result == DialerWorker._BRIDGE_TRANSIENT_REJECT
    assert w._last_bridge_defer[1] == 240


@pytest.mark.asyncio
async def test_call_blocked_is_not_deferred(monkeypatch):
    w, result = await _call(
        monkeypatch,
        429,
        _envelope("call_blocked", details={"reasons": ["number_on_dnc_list: internal"]}),
    )
    assert result is None  # -> generic failure boundary -> classifier -> terminal


# ── process_job / replay wiring ──────────────────────────────────────


def _base_worker() -> DialerWorker:
    w = DialerWorker()
    w.queue_service = AsyncMock()
    w._redis = None
    w._db_pool = None
    w._get_campaign_status = AsyncMock(return_value="running")
    w._load_existing_call_intent = AsyncMock(return_value=None)
    w._tenant_minutes_exhausted = AsyncMock(return_value=False)
    w._get_tenant_rules = AsyncMock(return_value=CallingRules.default())
    w._get_campaign_calling_config = AsyncMock(return_value={})
    w._get_lead_last_called = AsyncMock(return_value=None)
    w._get_lead_attempts_today = AsyncMock(return_value=0)
    w._get_lead_timezone = AsyncMock(return_value=None)
    w.rules_engine.can_make_call = AsyncMock(return_value=(True, ""))
    w._resolve_batch_size = MagicMock(return_value=0)
    w._resolve_call_gap = MagicMock(return_value=0)
    w._evaluate_call_guard = AsyncMock(return_value="allow")
    w._update_job_status = AsyncMock()
    intent = DialerCallIntent("call-1", "tk-1", "leg-1", "initiated", None, True)
    w._create_call_intent = AsyncMock(return_value=intent)
    w._mark_call_intent_not_originated = AsyncMock(return_value=True)
    w._update_lead_status = AsyncMock()
    w._publish_reason = AsyncMock()
    w._record_job_failure_classification = AsyncMock()
    w._emit_unknown_bridge_code_alert = AsyncMock()
    return w


@pytest.mark.asyncio
async def test_process_job_redefers_the_same_attempt_for_a_throttle():
    w = _base_worker()

    async def fake_make_call(job, rules, *, call_intent=None):
        w._last_bridge_defer = ("call_throttled", 90)
        return DialerWorker._BRIDGE_TRANSIENT_REJECT

    w._make_call = fake_make_call
    job = _job()
    await w.process_job(job)

    assert job.attempt_number == 1  # not consumed
    w.queue_service.schedule_retry.assert_not_awaited()
    w.queue_service.mark_failed.assert_not_awaited()
    w.queue_service._redefer_inflight.assert_awaited_once()
    _, kwargs = w.queue_service._redefer_inflight.call_args
    assert kwargs["delay_seconds"] == 90
    _, status_kwargs = w._update_job_status.call_args
    assert status_kwargs.get("reason") == "call_throttled"


@pytest.mark.asyncio
async def test_replayed_attempt_also_redefers_the_same_attempt_for_tenant_pbx():
    w = _base_worker()
    intent = DialerCallIntent("call-1", "tk-1", "leg-1", "initiated", None, False)
    w._load_existing_call_intent = AsyncMock(return_value=intent)

    async def fake_make_call(job, rules, *, call_intent=None):
        w._last_bridge_defer = ("tenant_pbx_required", 120)
        return DialerWorker._BRIDGE_TRANSIENT_REJECT

    w._make_call = fake_make_call
    job = _job()
    await w.process_job(job)

    assert job.attempt_number == 1
    w.queue_service.schedule_retry.assert_not_awaited()
    w.queue_service._redefer_inflight.assert_awaited_once()
    _, kwargs = w.queue_service._redefer_inflight.call_args
    assert kwargs["delay_seconds"] == 120


@pytest.mark.asyncio
async def test_call_blocked_is_terminal_not_retried():
    w = _base_worker()

    async def fake_make_call(job, rules, *, call_intent=None):
        w._last_bridge_http_status = 429
        w._last_bridge_body = _envelope("call_blocked")
        return None

    w._make_call = fake_make_call
    await w.process_job(_job())

    w.queue_service.schedule_retry.assert_not_awaited()
    w.queue_service.mark_failed.assert_awaited_once()


@pytest.mark.asyncio
async def test_unknown_code_raises_a_visible_alert():
    w = _base_worker()

    async def fake_make_call(job, rules, *, call_intent=None):
        w._last_bridge_http_status = 418
        w._last_bridge_body = _envelope("brand_new_code")
        return None

    w._make_call = fake_make_call
    await w.process_job(_job())

    w._emit_unknown_bridge_code_alert.assert_awaited_once()
    assert w._emit_unknown_bridge_code_alert.call_args.args[1] == "brand_new_code"


# ── reviewer additions: the bridge's OWN generic codes are not "unknown" ──────
#
# error_handlers.http_exception_handler stamps every plain 500 with
# ``internal_error`` (and a slowapi 429 with ``rate_limited``). A bridge-side
# originate failure (ARI error after the claim) is exactly such a 500, so every
# one of them fell into the unknown-code branch and raised the "Unrecognised
# telephony error ... add the code to the retry classifier" alert on the tenant.


def test_the_bridges_own_generic_codes_are_known_and_retryable():
    for status, code in ((500, "internal_error"), (429, "rate_limited")):
        category, reason = rp.classify_telephony_response(http_status=status, error_code=code)
        assert category == rp.FailureCategory.TRANSIENT_NETWORK
        assert reason == code  # not 'unknown_code_<code>'


@pytest.mark.asyncio
async def test_a_plain_500_from_the_bridge_does_not_raise_the_unknown_code_alert():
    w = _base_worker()

    async def fake_make_call(job, rules, *, call_intent=None):
        w._last_bridge_http_status = 500
        w._last_bridge_body = _envelope("internal_error")
        return None

    w._make_call = fake_make_call
    await w.process_job(_job())

    w._emit_unknown_bridge_code_alert.assert_not_awaited()
    _, kw = w._record_job_failure_classification.call_args
    assert kw["reason"] == "internal_error" and kw["category"] == "transient_network"
