"""A full platform is normal load, not a breakdown.

Both capacity refusals (the call service's ``pod_at_capacity`` and a full speech
provider limit, ``provider_at_capacity``) must show as "All lines are busy",
hold the SAME attempt 30 s at a time, never raise an operator alert and never
use up a lead's attempts. Real outages keep their own path.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints import telephony_bridge as bridge
from app.domain.models.calling_rules import CallingRules
from app.domain.services.dialer import block_state
from app.domain.services.dialer.block_reasons import (
    SELF_CLEARING_CODES,
    BlockCode,
    classify,
)
from app.infrastructure.providers import provider_concurrency as pc
from app.workers.dialer_worker import DialerCallIntent
from tests.unit.test_outbound_capacity_before_dial import _call_path, state  # noqa: F401
from tests.unit.test_w1_dialer_job_lifecycle import (
    _conn_pool,
    _job,
    _published_codes,
    _worker,
)


def _intent() -> DialerCallIntent:
    return DialerCallIntent("call-1", "tk-1", "leg-1", "initiated", None, True)


def _body(code: str) -> str:
    return json.dumps({"detail": {"error": code}})


def _held_worker(body: str):
    w = _worker()
    w._last_bridge_body = body
    w._alert_persistent_line_outage = MagicMock()
    _conn_pool(w, fetchval=0)
    return w


# ---------------------------------------------------------------- dialer ----
@pytest.mark.parametrize("code", ["pod_at_capacity", "provider_at_capacity"])
async def test_capacity_503_publishes_platform_at_capacity_and_holds_30s_same_attempt(code):
    w = _held_worker(_body(code))
    w._pipeline_unavailable_defer_count = AsyncMock(return_value=1)
    w._held_intent_has_headroom = AsyncMock(return_value=True)
    job = _job(attempt=2)

    await w._defer_pipeline_unavailable(job, CallingRules.default(), _intent())

    assert _published_codes(w) == [BlockCode.PLATFORM_AT_CAPACITY]
    assert w._publish_reason.call_args.args[1].retry_after_seconds == 30
    w.queue_service._redefer_inflight.assert_awaited_once()
    assert w.queue_service._redefer_inflight.call_args.kwargs["delay_seconds"] == 30
    assert job.attempt_number == 2
    w._release_intent_line_not_ready.assert_not_awaited()
    w._alert_persistent_line_outage.assert_not_called()
    w._make_call.assert_not_awaited()


@pytest.mark.parametrize("code", ["pod_at_capacity", "provider_at_capacity"])
@pytest.mark.parametrize("over_budget,headroom", [(True, True), (False, False)])
async def test_capacity_past_the_hold_budget_releases_and_redefers_without_alert_or_raise(
    code, over_budget, headroom, caplog
):
    w = _held_worker(_body(code))
    w._pipeline_unavailable_defer_count = AsyncMock(return_value=999 if over_budget else 1)
    w._held_intent_has_headroom = AsyncMock(return_value=headroom)
    job = _job(attempt=3)

    with caplog.at_level("WARNING", logger="app.workers.dialer_worker"):
        await w._defer_pipeline_unavailable(job, CallingRules.default(), _intent())  # must not raise

    w._release_intent_line_not_ready.assert_awaited_once()
    assert w._release_intent_line_not_ready.call_args.kwargs["reason"] == "platform_at_capacity"
    assert w.queue_service._redefer_inflight.call_args.kwargs["delay_seconds"] == 30
    assert job.attempt_number == 3
    assert _published_codes(w) == [BlockCode.PLATFORM_AT_CAPACITY]
    assert w._publish_reason.call_args.args[1].retry_after_seconds == 30
    w._alert_persistent_line_outage.assert_not_called()
    assert not [r for r in caplog.records if r.levelname in ("WARNING", "ERROR")
                and "capacity" in r.getMessage()]


async def test_capacity_release_that_cannot_be_confirmed_parks_the_attempt():
    w = _held_worker(_body("pod_at_capacity"))
    w._pipeline_unavailable_defer_count = AsyncMock(return_value=999)
    w._release_intent_line_not_ready = AsyncMock(return_value=False)

    await w._defer_pipeline_unavailable(_job(), CallingRules.default(), _intent())

    w._park_uncertain_origination.assert_awaited_once()
    w.queue_service._redefer_inflight.assert_not_awaited()


async def test_process_job_with_pod_at_capacity_refusal_holds_instead_of_alerting():
    w = _held_worker(_body("pod_at_capacity"))
    w._held_intent_has_headroom = AsyncMock(return_value=True)

    async def refused(*_a, **_k):
        w._last_bridge_body = _body("pod_at_capacity")
        return w._PIPELINE_UNAVAILABLE

    w._make_call = AsyncMock(side_effect=refused)
    job = _job()
    await w.process_job(job)

    w._make_call.assert_awaited_once()
    assert BlockCode.PLATFORM_AT_CAPACITY in _published_codes(w)
    assert w.queue_service._redefer_inflight.call_args.kwargs["delay_seconds"] == 30
    assert job.attempt_number == 1
    w._alert_persistent_line_outage.assert_not_called()


def test_hold_reason_unchanged_outcomes():
    w = _worker()
    w._last_bridge_body = _body("telephony_adapter_unavailable")
    assert w._pipeline_hold_reason() == "telephony_unavailable"
    w._last_bridge_body = json.dumps({"detail": "Voice pipeline is not ready. Check TTS/STT provider health."})
    assert w._pipeline_hold_reason() == "voice_pipeline_unavailable"
    w._last_bridge_body = _body("pod_at_capacity")
    assert w._pipeline_hold_reason() == "platform_at_capacity"
    w._last_bridge_body = _body("provider_at_capacity")
    assert w._pipeline_hold_reason() == "platform_at_capacity"


async def test_a_real_voice_outage_past_budget_still_raises():
    w = _held_worker(json.dumps({"detail": "Voice pipeline is not ready."}))
    w._pipeline_unavailable_defer_count = AsyncMock(return_value=999)
    with pytest.raises(RuntimeError):
        await w._defer_pipeline_unavailable(_job(), CallingRules.default(), _intent())
    w._release_intent_line_not_ready.assert_not_awaited()


def test_released_capacity_intent_does_not_count_against_the_daily_cap():
    import inspect

    from app.workers import dialer_worker

    src = inspect.getsource(dialer_worker.DialerWorker._get_lead_attempts_today)
    assert "'platform_at_capacity'" in src  # the one allowed text check


async def test_release_helper_writes_platform_at_capacity_literal():
    w = _worker()
    w._release_intent_line_not_ready = type(w)._release_intent_line_not_ready.__get__(w)
    conn = _conn_pool(w)
    assert await w._release_intent_line_not_ready(
        _job(), _intent(), reason="platform_at_capacity") is True
    assert "'platform_at_capacity'" in conn.execute.call_args.args[0]


def test_retry_policy_maps_both_capacity_codes_alike():
    from app.workers.retry_policy import _CODE_TO_CATEGORY  # type: ignore[attr-defined]

    assert _CODE_TO_CATEGORY["provider_at_capacity"] is _CODE_TO_CATEGORY["pod_at_capacity"]


# -------------------------------------------------------- reason registry ----
def test_platform_at_capacity_reason():
    reason = classify("platform_at_capacity", retry_after_seconds=30)
    assert reason.code is BlockCode.PLATFORM_AT_CAPACITY
    assert reason.code in SELF_CLEARING_CODES
    assert reason.severity == "info"
    assert reason.stage == "pacing"
    assert reason.retry_after_seconds == 30
    assert reason.message == "All call lines are in use. Calls start as lines free up."
    assert block_state._title_for(reason) == "All lines are busy"


@pytest.mark.parametrize("raw,code", [
    ("batch_capacity", BlockCode.BATCH_CAPACITY),
    ("max_concurrent_calls_reached_10/10", BlockCode.MAX_CONCURRENT_CALLS),
    ("platform_gap", BlockCode.PLATFORM_GAP),
    ("telephony_unavailable", BlockCode.TELEPHONY_UNAVAILABLE),
    ("tenant_gap", BlockCode.TENANT_GAP),
    ("call_gap", BlockCode.CALL_GAP),
])
def test_existing_codes_are_not_swallowed_by_the_new_one(raw, code):
    assert classify(raw).code is code


def test_advice_for_platform_at_capacity():
    from app.domain.services import call_issue_advice

    advice = call_issue_advice.advise("platform_at_capacity")
    assert advice is not call_issue_advice._FALLBACK
    assert advice.title == "All lines are busy"
    assert advice.severity == "info" and advice.stage == "pacing"


def test_issues_sql_excludes_platform_at_capacity():
    import inspect

    from app.api.v1.endpoints import calls

    assert "'platform_at_capacity'" in inspect.getsource(calls)


# ----------------------------------------------------------- call service ----
@pytest.fixture
def guards():
    pc.reset_guards_for_tests()
    yield
    pc.reset_guards_for_tests()


def _fill(provider: str, cap: int = 2) -> None:
    g = pc.ProviderConcurrencyGuard(provider, cap)
    g._in_flight = cap
    pc._GUARDS[provider] = g


def _warmup_fails(monkeypatch, reason: str) -> None:
    from types import SimpleNamespace

    async def failed(**_kwargs):
        return SimpleNamespace(session=None, effective_first_speaker="agent",
                               failure_reason=reason)

    monkeypatch.setattr(bridge, "prepare_prewarmed_session", failed)


WARMUP_TIMEOUT = "pre_originate_warmup_timeout: pipeline not ready within 5.0s — refusing to ring"


async def test_make_call_full_provider_limit_is_capacity_not_a_failure(state, guards, monkeypatch):  # noqa: F811
    path = _call_path(monkeypatch, state)
    _warmup_fails(monkeypatch, WARMUP_TIMEOUT)
    emit = AsyncMock()
    monkeypatch.setattr(bridge, "emit_event_via_pool", emit)
    _fill("assemblyai", 5)

    with pytest.raises(HTTPException) as error:
        await bridge.make_call(path.request, path.body)

    assert error.value.status_code == 503
    assert error.value.detail["error"] == "provider_at_capacity"
    assert error.value.detail["provider"] == "assemblyai"
    assert error.value.detail["in_flight"] == 5
    assert error.value.detail["max_concurrent"] == 5
    assert "Retry-After" in error.value.headers
    emit.assert_not_awaited()
    assert "originate" not in path.events


async def test_make_call_warmup_timeout_without_a_full_guard_is_unchanged(state, guards, monkeypatch):  # noqa: F811
    path = _call_path(monkeypatch, state)
    _warmup_fails(monkeypatch, WARMUP_TIMEOUT)
    emit = AsyncMock()
    monkeypatch.setattr(bridge, "emit_event_via_pool", emit)
    pc.get_provider_guard("assemblyai")  # registered, empty

    with pytest.raises(HTTPException) as error:
        await bridge.make_call(path.request, path.body)

    assert error.value.status_code == 503
    assert isinstance(error.value.detail, str)
    assert "Check TTS/STT provider health" in error.value.detail
    emit.assert_awaited_once()
    assert emit.await_args.kwargs["title"] == "Call could not start"
    assert emit.await_args.kwargs["severity"] == "critical"
    assert "originate" not in path.events


async def test_make_call_guard_exhausted_text_names_the_provider(state, guards, monkeypatch):  # noqa: F811
    path = _call_path(monkeypatch, state)
    g = pc.ProviderConcurrencyGuard("deepgram", 1, wait_timeout=0.01)
    pc._GUARDS["deepgram"] = g
    async with g.acquire():
        try:
            async with g.acquire():
                pass
        except pc.ProviderGuardTimeout as exc:
            text = f"pre_originate_warmup_handshake_failed: {exc!r}"
    _warmup_fails(monkeypatch, text)
    emit = AsyncMock()
    monkeypatch.setattr(bridge, "emit_event_via_pool", emit)

    with pytest.raises(HTTPException) as error:
        await bridge.make_call(path.request, path.body)

    assert error.value.detail["error"] == "provider_at_capacity"
    assert error.value.detail["provider"] == "deepgram"
    emit.assert_not_awaited()


def test_helper_returns_none_for_other_failures(guards):
    assert bridge._provider_capacity_refusal(None) is None
    assert bridge._provider_capacity_refusal("pre_originate_warmup_handshake_failed: boom") is None
    _fill("assemblyai", 5)
    assert bridge._provider_capacity_refusal("tts provider 401") is None


def test_helper_reports_the_fullest_guard(guards):
    _fill("assemblyai", 5)
    g = pc.ProviderConcurrencyGuard("deepgram", 80)
    g._in_flight = 80
    pc._GUARDS["deepgram"] = g
    d = bridge._provider_capacity_refusal(WARMUP_TIMEOUT)
    assert d["error"] == "provider_at_capacity"
    assert d["in_flight"] >= d["max_concurrent"]


# ---------------------------------------------------------- humanize_failure ----
def test_humanize_capacity_branch():
    from app.domain.services.telephony.failure_reasons import humanize_failure

    for text in ("assemblyai concurrency guard exhausted (5 in flight) — waited 5.0s",
                 "provider_at_capacity"):
        cat, msg = humanize_failure(text)
        assert cat == "capacity"
        assert msg == ("All lines for this voice provider are in use. "
                       "The call starts when one is free.")


def test_humanize_existing_branch_unchanged():
    from app.domain.services.telephony.failure_reasons import humanize_failure

    assert humanize_failure("elevenlabs 401 unauthorized")[0] == "tts"
    assert humanize_failure("")[0] == "prewarm"
