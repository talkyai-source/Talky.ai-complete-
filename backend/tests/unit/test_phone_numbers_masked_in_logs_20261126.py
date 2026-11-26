"""A phone number is never written whole into the server log (last four digits only)."""
from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

@pytest.fixture(autouse=True)
def _redactor_off():
    """The process-wide log redactor (app/core/log_redact.py) may already be installed by an
    earlier import; it would hide a whole number and make these tests pass for the wrong
    reason. They test the call site's own masking, so switch it off for each test."""
    from app.core.log_redact import set_pii_redaction_enabled

    set_pii_redaction_enabled(False)
    yield
    set_pii_redaction_enabled(None)


NUMBER = "+442079460123"
LAST4 = "0123"
ERROR_TEXT = "boom-private-text"


def _assert_masked(caplog):
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert NUMBER not in text and NUMBER[1:] not in text
    assert LAST4 in text
    assert ERROR_TEXT not in text
    return text


@pytest.mark.asyncio
async def test_lookup_failure_log_masks_number_and_hides_error_text(monkeypatch, caplog):
    from app.core import db_utils
    from app.domain.services import tenant_phone_number_service as mod

    def boom(*a, **k):
        raise RuntimeError(f"{ERROR_TEXT} {NUMBER}")

    monkeypatch.setattr(db_utils, "acquire_with_tenant", boom)
    svc = mod.TenantPhoneNumberService(MagicMock())
    caplog.set_level(logging.DEBUG)
    ok = await svc.is_verified_for_tenant("11111111-1111-1111-1111-111111111111", NUMBER)
    assert ok is False
    text = _assert_masked(caplog)
    assert "RuntimeError" in text


def test_timezone_parse_failure_log_masks_number(monkeypatch, caplog):
    import phonenumbers
    from app.domain.services import phone_timezone as mod

    def boom(*a, **k):
        raise ValueError(f"{ERROR_TEXT} {NUMBER}")

    monkeypatch.setattr(phonenumbers, "parse", boom)
    caplog.set_level(logging.DEBUG)
    assert mod.lookup_timezone_sync(NUMBER) is None
    text = _assert_masked(caplog)
    assert "ValueError" in text


def test_timezone_lookup_failure_log_masks_number(monkeypatch, caplog):
    from phonenumbers import timezone as pn_timezone
    from app.domain.services import phone_timezone as mod

    def boom(*a, **k):
        raise KeyError(f"{ERROR_TEXT} {NUMBER}")

    monkeypatch.setattr(pn_timezone, "time_zones_for_number", boom)
    caplog.set_level(logging.DEBUG)
    assert mod.lookup_timezone_sync(NUMBER) is None
    text = _assert_masked(caplog)
    assert "KeyError" in text


# ---- the call paths: every line below printed a whole number before ----------

from types import SimpleNamespace  # noqa: E402
from unittest.mock import AsyncMock  # noqa: E402

OTHER = "+442079460199"


def _no_whole_number(caplog, *numbers):
    text = "\n".join(r.getMessage() for r in caplog.records)
    for n in numbers:
        assert n not in text and n[1:] not in text
        assert n[-4:] in text
    assert ERROR_TEXT not in text
    return text


@pytest.mark.asyncio
async def test_asterisk_originate_log_masks_destination(monkeypatch, caplog):
    from unittest.mock import AsyncMock as _AM
    from app.infrastructure.telephony.asterisk_adapter import AsteriskAdapter

    adapter = AsteriskAdapter()
    adapter._ari = _AM(return_value={"id": "pre-1"})
    caplog.set_level(logging.INFO)
    try:
        await adapter.originate_call(NUMBER, "+15550001", channel_id="pre-1")
        _no_whole_number(caplog, NUMBER)
    finally:
        for task in list(adapter._originated_expiry_tasks.values()):
            task.cancel()


def _bridge_owner_off(monkeypatch):
    from app.api.v1.endpoints import telephony_bridge as bridge

    monkeypatch.setattr(bridge, "require_internal_or_tenant", lambda request: SimpleNamespace(is_internal=True))
    monkeypatch.setattr(bridge, "_require_call_process", lambda: None)
    monkeypatch.setattr(bridge, "resolve_call_tenant", lambda *a, **k: "tenant-1")
    monkeypatch.setattr(bridge, "_dialer_intent_contract", lambda *a, **k: False)
    monkeypatch.setattr(bridge, "_load_agent_lead_context", AsyncMock(return_value=None))
    return bridge


@pytest.mark.asyncio
async def test_make_call_not_owner_log_masks_destination(monkeypatch, caplog):
    from fastapi import HTTPException

    bridge = _bridge_owner_off(monkeypatch)
    backend = SimpleNamespace(is_telephony_owner=lambda: False, telephony_owner_id=AsyncMock(return_value="other"))
    monkeypatch.setattr(bridge, "get_state_backend", lambda: backend)
    caplog.set_level(logging.INFO)
    with pytest.raises(HTTPException) as caught:
        await bridge.make_call(MagicMock(), bridge.MakeCallRequest(destination=NUMBER))
    assert caught.value.status_code == 503
    text = _no_whole_number(caplog, NUMBER)
    assert "make_call_not_owner" in text


@pytest.mark.asyncio
async def test_make_call_provider_at_capacity_log_masks_destination(monkeypatch, caplog):
    from fastapi import HTTPException
    from app.core import container as container_mod
    from app.core import readiness
    from app.domain.services.telephony import provider_availability

    bridge = _bridge_owner_off(monkeypatch)
    backend = SimpleNamespace(is_telephony_owner=lambda: True, telephony_owner_id=AsyncMock(return_value="me"))
    monkeypatch.setattr(bridge, "get_state_backend", lambda: backend)
    monkeypatch.setattr(bridge, "_adapter", SimpleNamespace(connected=True, name="other"))
    monkeypatch.setattr(readiness, "is_draining", lambda: False)
    monkeypatch.setattr(bridge, "_outbound_capacity_refusal", lambda destination: None)
    monkeypatch.setattr(container_mod, "get_container", lambda: SimpleNamespace(db_pool=object(), redis=None))
    monkeypatch.setattr(provider_availability, "require_production_outbound_selection", AsyncMock())
    monkeypatch.setattr(bridge, "check_caller_id_ownership",
                        AsyncMock(return_value=SimpleNamespace(allowed=True, require_attestation=False)))
    guard_result = SimpleNamespace(
        decision=bridge.GuardDecision.ALLOW, check_results=[], failed_checks=[], total_latency_ms=1,
        retry_after_seconds=None, queue_position=None,
    )
    monkeypatch.setattr(bridge, "CallGuard", lambda **k: SimpleNamespace(evaluate=AsyncMock(return_value=guard_result)))
    monkeypatch.setattr(bridge, "prepare_prewarmed_session", AsyncMock(return_value=SimpleNamespace(
        effective_first_speaker="agent", session=None, failure_reason="x")))
    monkeypatch.setattr(bridge, "_provider_capacity_refusal", lambda reason: {
        "provider": "p", "in_flight": 1, "max_concurrent": 1})
    caplog.set_level(logging.INFO)
    with pytest.raises(HTTPException):
        await bridge.make_call(MagicMock(), bridge.MakeCallRequest(destination=NUMBER, tenant_id="tenant-1"))
    text = _no_whole_number(caplog, NUMBER)
    assert "make_call_provider_at_capacity" in text


@pytest.mark.asyncio
async def test_twilio_answer_log_masks_both_numbers(monkeypatch, caplog):
    from app.api.v1.endpoints import twilio_bridge as tb

    monkeypatch.setattr(tb, "_bridge_enabled", lambda: True)
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "dummy")
    monkeypatch.setattr(tb, "verify_twilio_signature", lambda **k: True)
    monkeypatch.setattr(tb, "_is_supported_answer_direction", lambda d: True)
    monkeypatch.setattr(tb, "_mint_ws_token", lambda **k: None)

    async def form():
        return {"CallSid": "CA1234567890abcdef", "From": NUMBER, "To": OTHER}

    request = SimpleNamespace(form=form, headers={})
    caplog.set_level(logging.INFO)
    await tb.twilio_answer(request)
    text = _no_whole_number(caplog, NUMBER, OTHER)
    assert "Twilio answer webhook" in text


@pytest.mark.asyncio
async def test_vonage_answer_log_masks_both_numbers(monkeypatch, caplog):
    from app.api.v1.endpoints import vonage_bridge as vb

    monkeypatch.setattr(vb, "_bridge_enabled", lambda: True)
    monkeypatch.setenv("VONAGE_SIGNATURE_SECRET", "dummy")
    monkeypatch.setattr(vb, "verify_vonage_signature", lambda **k: True)
    monkeypatch.setattr(vb, "_is_supported_answer_direction", lambda d: True)

    async def json_body():
        return {"uuid": "abcdef0123456789", "from": NUMBER, "to": OTHER, "direction": "outbound"}

    request = SimpleNamespace(json=json_body, headers={})
    caplog.set_level(logging.INFO)
    try:
        await vb.vonage_answer(request)
    except Exception:  # the rest of the handler is not under test
        pass
    text = _no_whole_number(caplog, NUMBER, OTHER)
    assert "Vonage answer webhook" in text


@pytest.mark.asyncio
async def test_caller_id_guard_lookup_failure_masks_number_and_hides_error_text(monkeypatch, caplog):
    from app.domain.services.telephony import caller_id_guard as guard

    async def boom(self, **kwargs):
        raise RuntimeError(f"{ERROR_TEXT} {NUMBER}")

    monkeypatch.setattr(guard.TenantPhoneNumberService, "is_verified_for_tenant", boom)
    monkeypatch.setenv("CALLER_ID_ENFORCEMENT_MODE", "log")
    caplog.set_level(logging.INFO)
    await guard.check_caller_id_ownership(
        object(), tenant_id="tenant-1", caller_id=NUMBER, environment="test")
    text = _no_whole_number(caplog, NUMBER)
    assert "caller_id_verification_lookup_failed" in text and "RuntimeError" in text


@pytest.mark.asyncio
async def test_resolve_ai_config_for_did_failure_masks_did_and_hides_error_text(monkeypatch, caplog):
    from app.core import container as container_mod
    from app.domain.services import tenant_ai_config_resolver as mod
    from app.domain.services.telephony import inbound_router

    async def boom(*a, **k):
        raise RuntimeError(f"{ERROR_TEXT} {NUMBER}")

    monkeypatch.setattr(container_mod, "get_container", lambda: SimpleNamespace(db_pool=object()))
    monkeypatch.setattr(inbound_router, "resolve_inbound_route", boom)
    fake = SimpleNamespace(for_tenant_async=AsyncMock(return_value=object()))
    monkeypatch.setattr(mod, "get_tenant_ai_config_resolver", lambda: fake)
    caplog.set_level(logging.INFO)
    tenant, _cfg = await mod.resolve_ai_config_for_did(NUMBER)
    assert tenant is None
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert NUMBER not in text and NUMBER[1:] not in text
    assert ERROR_TEXT not in text
    assert "resolve_ai_config_for_did_failed" in text and "RuntimeError" in text


# ---- second layer: the call sites that still printed a whole number ----------


def _pod_full(monkeypatch):
    from app.api.v1.endpoints import telephony_bridge as bridge
    from app.core import readiness
    from app.domain.services.telephony import lifecycle

    monkeypatch.setattr(readiness, "is_pod_at_capacity", lambda: True)
    monkeypatch.setattr(lifecycle, "committed_call_load", lambda: 0)
    monkeypatch.setattr(bridge, "get_state_backend", lambda: SimpleNamespace(voice_session_count=lambda: 0))
    return bridge


def test_pod_at_capacity_log_masks_destination(monkeypatch, caplog):
    bridge = _pod_full(monkeypatch)
    caplog.set_level(logging.INFO)
    assert bridge._outbound_capacity_refusal(NUMBER)["error"] == "pod_at_capacity"
    text = _no_whole_number(caplog, NUMBER)
    assert "make_call_pod_at_capacity" in text


def test_pod_at_capacity_log_survives_a_missing_destination(monkeypatch, caplog):
    bridge = _pod_full(monkeypatch)
    caplog.set_level(logging.INFO)
    assert bridge._outbound_capacity_refusal(None)["error"] == "pod_at_capacity"


async def _drive_make_call_with_guard_result(monkeypatch, guard_result):
    from app.core import container as container_mod
    from app.core import readiness
    from app.domain.services.telephony import provider_availability

    bridge = _bridge_owner_off(monkeypatch)
    backend = SimpleNamespace(is_telephony_owner=lambda: True, telephony_owner_id=AsyncMock(return_value="me"))
    monkeypatch.setattr(bridge, "get_state_backend", lambda: backend)
    monkeypatch.setattr(bridge, "_adapter", SimpleNamespace(connected=True, name="other"))
    monkeypatch.setattr(readiness, "is_draining", lambda: False)
    monkeypatch.setattr(bridge, "_outbound_capacity_refusal", lambda destination: None)
    monkeypatch.setattr(container_mod, "get_container", lambda: SimpleNamespace(db_pool=object(), redis=None))
    monkeypatch.setattr(provider_availability, "require_production_outbound_selection", AsyncMock())
    monkeypatch.setattr(bridge, "check_caller_id_ownership",
                        AsyncMock(return_value=SimpleNamespace(allowed=True, require_attestation=False)))
    monkeypatch.setattr(bridge, "CallGuard", lambda **k: SimpleNamespace(evaluate=AsyncMock(return_value=guard_result)))
    monkeypatch.setattr(bridge, "prepare_prewarmed_session", AsyncMock(return_value=SimpleNamespace(
        effective_first_speaker="agent", session=None, failure_reason="x")))
    monkeypatch.setattr(bridge, "_provider_capacity_refusal", lambda reason: {
        "provider": "p", "in_flight": 1, "max_concurrent": 1})
    try:
        return await bridge.make_call(
            MagicMock(), bridge.MakeCallRequest(destination=NUMBER, tenant_id="tenant-1"))
    except Exception:  # the refusal that follows is not under test
        return None


def _guard_result(decision, **over):
    base = dict(decision=decision, check_results=[], failed_checks=[], total_latency_ms=1,
                retry_after_seconds=None, queue_position=None)
    base.update(over)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
@pytest.mark.parametrize("name,marker,extra", [
    ("BLOCK", "Call blocked by guard", {}),
    ("THROTTLE", "Call throttled by guard", {}),
    ("QUEUE", "Call queued by guard", {"queue_position": 3}),
])
async def test_guard_decision_logs_mask_destination(monkeypatch, caplog, name, marker, extra):
    from app.api.v1.endpoints import telephony_bridge as bridge

    result = _guard_result(getattr(bridge.GuardDecision, name), **extra)
    caplog.set_level(logging.INFO)
    await _drive_make_call_with_guard_result(monkeypatch, result)
    text = _no_whole_number(caplog, NUMBER)
    assert marker in text


@pytest.mark.asyncio
async def test_guard_bypass_log_masks_destination(monkeypatch, caplog):
    from app.api.v1.endpoints import telephony_bridge as bridge

    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("TELEPHONY_LOCAL_DEV", "1")
    monkeypatch.setenv("TELEPHONY_DEV_BYPASS_GUARD_ERRORS", "true")
    check = SimpleNamespace(passed=False, reason="configuration_load_error")
    result = _guard_result(bridge.GuardDecision.BLOCK, check_results=[check],
                           failed_checks=["configuration_load_error"], tenant_id="tenant-1",
                           phone_number=NUMBER, call_id=None)
    caplog.set_level(logging.INFO)
    await _drive_make_call_with_guard_result(monkeypatch, result)
    text = _no_whole_number(caplog, NUMBER)
    assert "Bypassing CallGuard block" in text


@pytest.mark.asyncio
async def test_contact_added_and_revived_logs_mask_number(caplog):
    from app.api.v1.endpoints import campaigns as campaigns_ep
    from app.api.v1.schemas.campaigns import ContactCreate
    from tests.unit.test_bh_campaigns_contacts_contact_writes import PERMISSION, _DB, _User

    caplog.set_level(logging.INFO)
    db = _DB()
    await campaigns_ep.add_contact_to_campaign(
        campaign_id="c1",
        contact=ContactCreate(phone_number=NUMBER, first_name="Ann", permission=PERMISSION),
        current_user=_User(), supabase=db)
    text = _no_whole_number(caplog, NUMBER)
    assert "Contact added to campaign" in text

    caplog.clear()
    db = _DB(leads=[{"id": "l1", "tenant_id": "t1", "campaign_id": "c1", "phone_number": NUMBER,
                     "status": "deleted", "is_lead": False, "do_not_call": False}])
    await campaigns_ep.add_contact_to_campaign(
        campaign_id="c1",
        contact=ContactCreate(phone_number=NUMBER, first_name="Ann", permission=PERMISSION),
        current_user=_User(), supabase=db)
    text = _no_whole_number(caplog, NUMBER)
    assert "Contact revived in campaign" in text


@pytest.mark.asyncio
async def test_vonage_sms_send_log_masks_both_numbers(caplog):
    from app.infrastructure.connectors.sms import vonage_sms

    provider = object.__new__(vonage_sms.VonageSMSProvider)
    provider._initialized = True
    provider._sms = MagicMock()
    provider._default_from = OTHER
    caplog.set_level(logging.INFO)
    try:
        await provider.send_sms(NUMBER, "hello")
    except Exception:  # what happens after the log line is not under test
        pass
    text = _no_whole_number(caplog, NUMBER, OTHER)
    assert "Sending SMS via Vonage" in text


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["originate_call", "originate_with_playback"])
async def test_freeswitch_originate_log_masks_destination(caplog, method):
    from app.infrastructure.telephony.freeswitch_esl import FreeSwitchESL

    esl = object.__new__(FreeSwitchESL)
    esl.api = AsyncMock(return_value="+OK 9f8e7d6c5b4a9f8e")
    caplog.set_level(logging.INFO)
    args = (NUMBER, "/tmp/x.wav") if method == "originate_with_playback" else (NUMBER,)
    assert await getattr(esl, method)(*args)
    # The "ESL originate command" line carries the dial string: outside this brief.
    text = "\n".join(r.getMessage() for r in caplog.records if "Originated call" in r.getMessage())
    assert "Originated call" in text
    assert NUMBER not in text and NUMBER[1:] not in text and LAST4 in text


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["originate_call", "originate_with_playback"])
async def test_freeswitch_originate_command_log_masks_destination(caplog, method):
    from app.infrastructure.telephony.freeswitch_esl import FreeSwitchESL

    esl = object.__new__(FreeSwitchESL)
    esl.api = AsyncMock(return_value="+OK 9f8e7d6c5b4a9f8e")
    caplog.set_level(logging.INFO)
    args = (NUMBER, "/tmp/x.wav") if method == "originate_with_playback" else (NUMBER,)
    assert await getattr(esl, method)(*args)
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "ESL originate" in text
    assert NUMBER not in text and NUMBER[1:] not in text
    cmd_lines = [r.getMessage() for r in caplog.records if "ESL originate" in r.getMessage() and "result" not in r.getMessage()]
    assert cmd_lines and all(LAST4 in line for line in cmd_lines)
    # the real command still goes to FreeSWITCH with the whole number
    assert NUMBER in esl.api.await_args.args[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("destination", [None, ""])
@pytest.mark.parametrize("method", ["originate_call", "originate_with_playback"])
async def test_freeswitch_originate_command_log_survives_missing_destination(caplog, method, destination):
    from app.infrastructure.telephony.freeswitch_esl import FreeSwitchESL

    esl = object.__new__(FreeSwitchESL)
    esl.api = AsyncMock(return_value="-ERR NO_ROUTE_DESTINATION")
    caplog.set_level(logging.INFO)
    args = (destination, "/tmp/x.wav") if method == "originate_with_playback" else (destination,)
    assert await getattr(esl, method)(*args) is None
