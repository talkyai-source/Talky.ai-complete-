"""Packet W1 -- block reasons, 503 labelling, retry vocabulary, event spam, copy.

* every bridge 503 was labelled "voice provider not ready";
* a refused line published the raw ``tenant_pbx_required`` (generic message);
* self-clearing pacing codes wrote a "Calls are paused" stream_events row on
  every dial cycle;
* the caller-ID copy sent tenants to a Settings page that does not exist.
"""
from __future__ import annotations

import json

import pytest

from app.domain.services.dialer import block_state
from app.domain.services.dialer.block_reasons import (
    SELF_CLEARING_CODES,
    BlockCode,
    BlockReason,
    classify,
)
from app.workers import retry_policy as rp
from app.workers.dialer_worker import DialerWorker
from tests.unit.test_w1_dialer_job_lifecycle import _job, _published_codes, _worker


# ═══ 4. 503 labelling ════════════════════════════════════════════════════

_TELEPHONY_503_CODES = [
    "telephony_not_active_on_node",
    "telephony_disabled_on_node",
    "pod_draining",
    "pod_at_capacity",
    "telephony_adapter_unavailable",
]


def _pipeline_503(w: DialerWorker, body) -> None:
    async def fake_make_call(job, rules, *, call_intent=None):
        w._last_bridge_http_status = 503
        w._last_bridge_body = body
        return DialerWorker._PIPELINE_UNAVAILABLE

    w._make_call = fake_make_call


@pytest.mark.asyncio
@pytest.mark.parametrize("code", _TELEPHONY_503_CODES)
@pytest.mark.parametrize("shape", ["envelope", "detail", "flat"])
async def test_telephony_503_is_labelled_as_the_phone_system_not_the_voice_provider(
    code, shape
):
    body = {
        "envelope": json.dumps({"error": {"code": code, "message": "m"}}),
        "detail": json.dumps({"detail": {"error": code}}),
        "flat": json.dumps({"error": code}),
    }[shape]
    w = _worker()
    _pipeline_503(w, body)
    job = _job()
    await w.process_job(job)

    codes = _published_codes(w)
    assert BlockCode.TELEPHONY_UNAVAILABLE in codes
    assert BlockCode.VOICE_PIPELINE_UNAVAILABLE not in codes
    assert w._update_job_status.call_args.kwargs["reason"] == "telephony_unavailable"
    # Same-attempt hold is unchanged: 60 s, no attempt consumed.
    assert job.attempt_number == 1
    w.queue_service.schedule_retry.assert_not_awaited()
    assert w.queue_service._redefer_inflight.call_args.kwargs["delay_seconds"] == 60


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        None,
        "",
        "<html>bad gateway</html>",
        json.dumps({"error": {"code": "pipeline_unavailable", "message": "m"}}),
        json.dumps({"error": {"code": "tts_warmup_failed", "message": "m"}}),
    ],
)
async def test_a_real_voice_pipeline_503_keeps_the_voice_provider_label(body):
    w = _worker()
    _pipeline_503(w, body)
    await w.process_job(_job())

    codes = _published_codes(w)
    assert BlockCode.VOICE_PIPELINE_UNAVAILABLE in codes
    assert BlockCode.TELEPHONY_UNAVAILABLE not in codes
    assert w._update_job_status.call_args.kwargs["reason"] == "voice_pipeline_unavailable"


def test_telephony_unavailable_message_does_not_blame_the_voice_provider():
    reason = classify("telephony_unavailable", retry_after_seconds=60)
    assert reason.code is BlockCode.TELEPHONY_UNAVAILABLE
    text = reason.message.lower()
    assert "phone system" in text
    assert "resume automatically" in text
    assert "api key" not in text and "tts" not in text and "stt" not in text
    assert reason.next_eligible_at is not None
    assert reason.severity == "warning"


@pytest.mark.parametrize("status", [503, 400])
def test_the_adapter_unavailable_code_is_a_known_transient_failure(status):
    category, reason = rp.classify_telephony_response(
        http_status=status, error_code="telephony_adapter_unavailable"
    )
    assert category is rp.FailureCategory.TRANSIENT_NETWORK
    assert reason == "telephony_adapter_unavailable"  # not 'unknown_code_...'


def test_every_telephony_hold_code_is_known_to_the_classifier():
    for code in _TELEPHONY_503_CODES:
        category, reason = rp.classify_telephony_response(
            http_status=503, error_code=code
        )
        assert category is rp.FailureCategory.TRANSIENT_NETWORK
        assert reason == code, code


# ═══ 5. refused-line reasons ═════════════════════════════════════════════


@pytest.mark.parametrize(
    "sub,needle",
    [
        ("trunk_caller_id_not_verified", "caller id"),
        ("no_caller_id", "caller id"),
        ("selected_trunk_not_ready", "registered"),
        ("campaign_assigned_trunk_not_ready", "registered"),
        ("pool_assigned_trunk_not_ready", "registered"),
        ("no_ready_trunk", "no ready"),
        ("no_own_trunk", "no ready"),
        ("campaign_assigned_trunk_invalid", "could not be checked"),
        ("pool_assigned_trunk_lookup_failed", "could not be checked"),
    ],
)
def test_line_not_ready_names_the_cause(sub, needle):
    reason = classify(f"line_not_ready:{sub}", retry_after_seconds=120)
    assert reason.code is BlockCode.LINE_NOT_READY
    assert needle in reason.message.lower()
    assert reason.severity == "error" and reason.stage == "carrier"
    assert reason.details["line_reason"] == sub
    assert reason.next_eligible_at is not None


def test_line_not_ready_without_a_known_cause_is_still_specific():
    for raw in ("line_not_ready", "line_not_ready:something_new"):
        reason = classify(raw)
        assert reason.code is BlockCode.LINE_NOT_READY
        assert "not ready" in reason.message.lower()
        assert reason.code is not BlockCode.ORIGINATE_FAILED


def test_line_not_ready_is_not_swallowed_by_the_caller_id_branch():
    # The sub-reason contains 'caller_id_not_verified'; the dedicated branch
    # must win over the older CALLER_ID_NOT_VERIFIED substring match.
    reason = classify("line_not_ready:trunk_caller_id_not_verified")
    assert reason.code is BlockCode.LINE_NOT_READY


def test_the_line_reason_is_parsed_from_every_bridge_body_shape():
    envelope = json.dumps(
        {
            "error": {
                "code": "tenant_pbx_required",
                "message": "m",
                "details": {"reason": "selected_trunk_not_ready"},
            }
        }
    )
    detail = json.dumps(
        {"detail": {"error": "tenant_pbx_required", "reason": "no_own_trunk"}}
    )
    flat = json.dumps({"error": "tenant_pbx_required", "reason": "no_caller_id"})
    assert rp.parse_bridge_reason(envelope) == "selected_trunk_not_ready"
    assert rp.parse_bridge_reason(detail) == "no_own_trunk"
    assert rp.parse_bridge_reason(flat) == "no_caller_id"
    for junk in (None, "", "not json", "[]", json.dumps({"error": "x"}),
                 json.dumps({"detail": {"reason": "Not A Code!"}})):
        assert rp.parse_bridge_reason(junk) is None


# ═══ 7. event-stream spam ════════════════════════════════════════════════


class FakeRedis:
    def __init__(self):
        self.store: dict[str, str] = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None, nx=None):
        self.store[key] = value
        return True

    async def delete(self, key):
        self.store.pop(key, None)


@pytest.fixture
def emitted(monkeypatch):
    rows: list[dict] = []

    async def _fake_emit(pool, **kwargs):
        rows.append(kwargs)

    monkeypatch.setattr(
        "app.domain.services.event_emitter.emit_event_via_pool", _fake_emit,
    )
    return rows


_SELF_CLEARING_RAW = [
    "call_gap",
    "tenant_gap",
    "batch_capacity",
    "call_guard_throttled",
    "call_guard_queued",
    "lead_cooldown_0.5h_of_2h",
]


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", _SELF_CLEARING_RAW)
async def test_self_clearing_codes_keep_redis_state_but_emit_no_stream_event(raw, emitted):
    redis = FakeRedis()
    reason = classify(raw, retry_after_seconds=5)

    # A dial cycle publishes, then clear_block_reason runs after the
    # origination: the next cycle used to look like a CHANGE every time.
    for _ in range(5):
        did_emit = await block_state.publish_block_reason(
            redis, object(), tenant_id="t1", campaign_id="c1", reason=reason,
        )
        assert did_emit is False
        stored = await block_state.read_block_reason(redis, "c1")
        assert stored["code"] == reason.code.value  # live state still published
        assert stored["title"]
        await block_state.clear_block_reason(redis, "c1")

    assert emitted == []


@pytest.mark.asyncio
async def test_self_clearing_code_stays_silent_even_after_the_reemit_interval(emitted):
    from datetime import datetime, timedelta, timezone

    redis = FakeRedis()
    t0 = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)
    reason = classify("tenant_gap", retry_after_seconds=2)
    await block_state.publish_block_reason(
        redis, object(), tenant_id="t1", campaign_id="c1", reason=reason, now=t0,
    )
    later = t0 + timedelta(seconds=block_state.REEMIT_AFTER_S + 1)
    assert await block_state.publish_block_reason(
        redis, object(), tenant_id="t1", campaign_id="c1", reason=reason, now=later,
    ) is False
    assert emitted == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw,title",
    [
        ("out_of_minutes", "Out of plan minutes"),
        ("line_not_ready:selected_trunk_not_ready", "Phone line not ready"),
        ("telephony_unavailable", "Calling service not connected"),
        ("max_concurrent_calls_reached_10/10", "Waiting for a free call slot"),
        ("daily_lead_cap_reached_3/3", "Daily call limit reached"),
        ("metering_unavailable", "Plan minutes could not be checked"),
    ],
)
async def test_actionable_codes_still_emit_one_event_with_a_proper_title(raw, title, emitted):
    redis = FakeRedis()
    reason = classify(raw)
    assert reason.code not in SELF_CLEARING_CODES
    first = await block_state.publish_block_reason(
        redis, object(), tenant_id="t1", campaign_id="c1", reason=reason,
    )
    again = await block_state.publish_block_reason(
        redis, object(), tenant_id="t1", campaign_id="c1", reason=reason,
    )
    assert first is True and again is False
    assert len(emitted) == 1
    assert emitted[0]["title"] == title
    assert emitted[0]["title"] != "Calls are paused"


def test_every_block_code_has_a_proper_title_except_the_unknown_fallback():
    for code in BlockCode:
        title = block_state._title_for(BlockReason(code, "m"))
        assert title, code
        if code is not BlockCode.UNKNOWN:
            assert title != "Calls are paused", code


# ═══ 8. copy ═════════════════════════════════════════════════════════════


def test_caller_id_copy_does_not_send_tenants_to_a_page_that_does_not_exist():
    reason = classify("caller_id_not_verified")
    assert reason.code is BlockCode.CALLER_ID_NOT_VERIFIED
    text = reason.message
    assert "Settings" not in text and "Phone Numbers" not in text
    assert "support" in text.lower()


# ── integration pass: raw line / telephony codes and caller-ID copy ───────


@pytest.mark.parametrize(
    "raw,needle",
    [
        ("tenant_pbx_required", "not ready"),
        ("no_ready_trunk", "no ready phone line"),
        ("selected_trunk_not_ready", "not registered"),
        ("campaign_assigned_trunk_not_ready", "not registered"),
        ("trunk_caller_id_not_verified", "not verified"),
    ],
)
def test_raw_route_refusal_codes_are_named_as_a_phone_line_problem(raw, needle):
    from app.domain.services.dialer.block_reasons import BlockCode, classify

    reason = classify(raw)
    assert reason.code is BlockCode.LINE_NOT_READY, raw
    assert needle in reason.message.lower(), (raw, reason.message)


@pytest.mark.parametrize(
    "raw",
    ["telephony_not_active_on_node", "telephony_disabled_on_node",
     "telephony_adapter_unavailable", "pod_draining", "pod_at_capacity"],
)
def test_raw_telephony_down_codes_blame_the_phone_system(raw):
    from app.domain.services.dialer.block_reasons import BlockCode, classify

    assert classify(raw).code is BlockCode.TELEPHONY_UNAVAILABLE


def test_caller_id_message_never_asks_for_a_campaign_caller_id():
    from app.domain.services.dialer.block_reasons import classify

    message = classify("caller_id_not_verified").message
    assert "campaign's caller ID" not in message and "Settings" not in message
    assert "Ask support to verify" in message
