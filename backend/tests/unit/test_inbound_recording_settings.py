"""Inbound recording settings: unspeakable notice, account setting, platform flag."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import ValidationError

from app.api.v1 import dependencies as _dependencies  # noqa: F401
import app.domain.services.inbound_campaign_service as campaign_module
from app.api.v1.schemas.inbound_campaigns import (
    InboundCampaignCreateRequest,
    InboundCampaignUpdateRequest,
    InboundRuntimeCapabilitiesResponse,
)
from app.domain.services.inbound_campaign_service import (
    InboundCampaignError,
    InboundCampaignService,
)
from tests.unit.test_inbound_campaign_service import _CasConn, _ready_bundle

TENANT = "11111111-1111-1111-1111-111111111111"
ACTOR = "22222222-2222-2222-2222-222222222222"
CONFIG = "33333333-3333-3333-3333-333333333333"
MESSAGE = (
    "The recording notice cannot offer callers a way to opt out, such as pressing "
    "a key: this service cannot do that during a call. Remove that sentence."
)
BAD_NOTICE = "This call is recorded. Press 9 to opt out."
GOOD_NOTICE = "This call is recorded for quality and training."


def _create_body(**extra):
    return {
        "name": "Line",
        "did_number": "+15551234567",
        "campaign_id": CONFIG,
        "sip_trunk_id": ACTOR,
        **extra,
    }


# ---- schema -----------------------------------------------------------------


def test_schema_create_refuses_opt_out_notice_when_recording_on():
    with pytest.raises(ValidationError) as exc:
        InboundCampaignCreateRequest(
            **_create_body(recording_enabled=True, consent_message=BAD_NOTICE)
        )
    assert MESSAGE in str(exc.value)


def test_schema_create_accepts_opt_out_text_when_recording_off_and_plain_notice_on():
    InboundCampaignCreateRequest(
        **_create_body(recording_enabled=False, consent_message=BAD_NOTICE)
    )
    InboundCampaignCreateRequest(
        **_create_body(recording_enabled=True, consent_message=GOOD_NOTICE)
    )


def test_schema_update_refuses_opt_out_notice_when_recording_on():
    with pytest.raises(ValidationError) as exc:
        InboundCampaignUpdateRequest(
            expected_version=1, recording_enabled=True, consent_message=BAD_NOTICE
        )
    assert MESSAGE in str(exc.value)


def test_schema_update_accepts_opt_out_text_when_recording_off():
    InboundCampaignUpdateRequest(
        expected_version=1, recording_enabled=False, consent_message=BAD_NOTICE
    )


# ---- service ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_service_create_refuses_opt_out_notice_only_when_recording_on():
    service = InboundCampaignService(object())
    with pytest.raises(InboundCampaignError) as exc:
        await service.create_campaign(
            tenant_id=TENANT,
            actor_id=ACTOR,
            actor_role="tenant_admin",
            payload=_create_body(recording_enabled=True, consent_message=BAD_NOTICE),
            idempotency_key="create-key-123",
        )
    assert str(exc.value) == MESSAGE
    assert exc.value.code == "unsupported_recording_notice"
    assert exc.value.status_code == 422


def _update_service(monkeypatch, *, recording_enabled, consent_message):
    conn = _CasConn(version=3, status="paused")
    conn.execute = AsyncMock(return_value="UPDATE 1")

    @asynccontextmanager
    async def acquire(_pool, _tenant):
        yield conn

    monkeypatch.setattr(campaign_module, "acquire_with_tenant", acquire)
    service = InboundCampaignService(object())
    service._claim_operation = AsyncMock(return_value=None)
    service._load_bundle = AsyncMock(side_effect=[object(), object()])
    before = {
        "name": "Paused line",
        "campaign_id": CONFIG,
        "opening_mode": "caller_first",
        "greeting": None,
        "timezone": "UTC",
        "business_hours": {},
        "after_hours_action": "hangup",
        "transfer_number": None,
        "recording_enabled": recording_enabled,
        "consent_message": consent_message,
        "recording_policy": {},
        "transfer_policy": {},
        "qualification_config": {},
        "did_number": "+15551234567",
        "sip_trunk_id": ACTOR,
        "assignment_id": CONFIG,
    }
    service._serialize_bundle = Mock(side_effect=[before, before])
    service._verified_phone = AsyncMock(return_value={"id": ACTOR})
    service._active_trunk = AsyncMock(return_value={"id": ACTOR})
    service._assert_did_free = AsyncMock(return_value=None)
    service._audit = AsyncMock(return_value=None)
    service._store_operation = AsyncMock(return_value=None)
    return service


async def _update(service, **fields):
    return await service.update_campaign(
        tenant_id=TENANT,
        config_id=CONFIG,
        actor_id=ACTOR,
        actor_role="tenant_admin",
        payload={
            "expected_version": 3,
            "did_number": "+15551234567",
            "sip_trunk_id": ACTOR,
            **fields,
        },
        idempotency_key="update-key-123",
    )


@pytest.mark.asyncio
async def test_service_update_refuses_opt_out_notice_when_recording_on(monkeypatch):
    service = _update_service(monkeypatch, recording_enabled=False, consent_message=None)
    with pytest.raises(InboundCampaignError) as exc:
        await _update(service, recording_enabled=True, consent_message=BAD_NOTICE)
    assert str(exc.value) == MESSAGE
    assert exc.value.code == "unsupported_recording_notice"


@pytest.mark.asyncio
async def test_service_update_checks_merged_state_when_only_notice_is_sent(monkeypatch):
    # Recording is already on; the schema cannot see that, the service must.
    service = _update_service(monkeypatch, recording_enabled=True, consent_message=GOOD_NOTICE)
    with pytest.raises(InboundCampaignError) as exc:
        await _update(service, consent_message=BAD_NOTICE)
    assert str(exc.value) == MESSAGE


@pytest.mark.asyncio
async def test_service_update_accepts_opt_out_text_when_recording_off(monkeypatch):
    service = _update_service(monkeypatch, recording_enabled=False, consent_message=None)
    await _update(service, consent_message=BAD_NOTICE)


# ---- readiness --------------------------------------------------------------


def _check(result, key):
    return next(item for item in result["checks"] if item["key"] == key)


def test_readiness_recording_notice_supported():
    bad = InboundCampaignService._readiness(
        _ready_bundle(recording_enabled=True, consent_message=BAD_NOTICE)
    )
    check = _check(bad, "recording_notice_supported")
    assert check["label"] == "Recording notice can be spoken as written"
    assert check["passed"] is False
    assert check["detail"] == MESSAGE
    assert "recording_notice_supported" in {b["code"] for b in bad["blockers"]}

    good = InboundCampaignService._readiness(
        _ready_bundle(recording_enabled=True, consent_message=GOOD_NOTICE)
    )
    assert _check(good, "recording_notice_supported")["passed"] is True
    off = InboundCampaignService._readiness(
        _ready_bundle(recording_enabled=False, consent_message=BAD_NOTICE)
    )
    assert _check(off, "recording_notice_supported")["passed"] is True


DISABLED_MSG = (
    'Your account\'s recording setting is "disabled", so calls on this campaign '
    "would not be recorded. Change it in Settings, or switch recording off for "
    "this campaign."
)
UNSAVED_MSG = (
    "Save a recording setting for your account in Settings before recording "
    "incoming calls."
)


def _account(monkeypatch, *, strict, enabled, mode, saved):
    if strict:
        monkeypatch.setenv("RECORDING_REQUIRE_NOTICE_PROOF", "true")
    else:
        monkeypatch.delenv("RECORDING_REQUIRE_NOTICE_PROOF", raising=False)
    result = InboundCampaignService._readiness(
        _ready_bundle(
            recording_enabled=enabled,
            consent_message=GOOD_NOTICE if enabled else None,
            tenant_recording_mode=mode,
            tenant_recording_policy_saved=saved,
        )
    )
    return _check(result, "recording_account_setting")


def test_readiness_account_setting_passes_when_campaign_recording_off(monkeypatch):
    check = _account(monkeypatch, strict=True, enabled=False, mode="disabled", saved=True)
    assert check["label"] == "Account recording setting allows recording"
    assert check["passed"] is True


def test_readiness_account_setting_fails_when_policy_disabled(monkeypatch):
    for strict in (False, True):
        check = _account(monkeypatch, strict=strict, enabled=True, mode="disabled", saved=True)
        assert check["passed"] is False
        assert check["detail"] == DISABLED_MSG


def test_readiness_account_setting_unsaved_policy_depends_on_strict_setting(monkeypatch):
    strict = _account(monkeypatch, strict=True, enabled=True, mode=None, saved=False)
    assert strict["passed"] is False
    assert strict["detail"] == UNSAVED_MSG
    default = _account(monkeypatch, strict=False, enabled=True, mode=None, saved=False)
    assert default["passed"] is True


def test_readiness_account_setting_passes_with_recording_policy_saved(monkeypatch):
    for strict in (False, True):
        check = _account(
            monkeypatch, strict=strict, enabled=True, mode="two_party", saved=True
        )
        assert check["passed"] is True


def test_bundle_sql_reads_saved_policy_with_tenant_predicate():
    sql = campaign_module._BUNDLE_SQL
    assert "FROM tenant_recording_policy trp" in sql
    assert "trp.tenant_id = cfg.tenant_id" in sql
    assert "trp2.tenant_id = cfg.tenant_id" in sql


# ---- capabilities -----------------------------------------------------------


def _capabilities_conn(monkeypatch, *, recording, owned):
    from app.domain.services.telephony import inbound_transfer

    reads = {"acquired": 0, "controls": 0}

    class Conn:
        async def fetchrow(self, query, *_args):
            assert "platform_runtime_controls" in query
            reads["controls"] += 1
            return {
                "inbound_transfer_enabled": True,
                "inbound_recording_enabled": recording,
            }

        async def fetchval(self, query, *_args):
            assert "FROM inbound_campaign_configs" in query
            return owned

    @asynccontextmanager
    async def acquire(_pool, _tenant):
        reads["acquired"] += 1
        yield Conn()

    monkeypatch.setattr(campaign_module, "acquire_with_tenant", acquire)
    monkeypatch.setattr(
        inbound_transfer, "inbound_transfer_scope_available", lambda **_k: True
    )
    return reads


@pytest.mark.asyncio
@pytest.mark.parametrize("recording", [True, False])
@pytest.mark.parametrize("config_id", [CONFIG, None])
async def test_capabilities_report_platform_recording_flag(monkeypatch, recording, config_id):
    reads = _capabilities_conn(monkeypatch, recording=recording, owned=True)
    result = await InboundCampaignService(object()).get_runtime_capabilities(
        tenant_id=TENANT, config_id=config_id
    )
    assert result["recording_platform_enabled"] is recording
    assert result["transfer_platform_enabled"] is True
    assert result["transfer_runtime_available"] is (config_id is not None)
    assert result["transfer_configuration_available"] is (config_id is not None)
    assert set(result) == {
        "transfer_runtime_available",
        "transfer_platform_enabled",
        "transfer_configuration_available",
        "recording_platform_enabled",
    }
    assert reads == {"acquired": 1, "controls": 1}
    InboundRuntimeCapabilitiesResponse(**result)
