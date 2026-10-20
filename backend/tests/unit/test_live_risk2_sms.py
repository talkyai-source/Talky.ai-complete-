"""compliance-5: SMSService.send_sms is the single DNC/opt-out gate for every caller."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.sms_service import SMSService

TENANT = "00000000-0000-0000-0000-000000000001"
NUMBER = "+14155550123"


class _Conn:
    def __init__(self, flagged=False, boom=False):
        self.flagged, self.boom, self.args = flagged, boom, None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def fetchval(self, query, *args):
        if self.boom:
            raise RuntimeError("db down")
        self.args = args
        return self.flagged


def _service(monkeypatch, *, dnc=False, dnc_error=False, lead_flag=False, lead_error=False):
    import app.services.sms_service as mod
    import app.domain.services.dnc_service as dnc_mod

    conn = _Conn(flagged=lead_flag, boom=lead_error)
    monkeypatch.setattr(mod, "acquire_with_tenant", lambda *_a, **_k: conn)
    seen = {}

    class _DNC:
        def __init__(self, _pool):
            pass

        async def is_on_dnc(self, *, tenant_id, e164):
            seen["call"] = (tenant_id, e164)
            if dnc_error:
                raise RuntimeError("dnc down")
            return dnc

    monkeypatch.setattr(dnc_mod, "DNCService", _DNC)
    svc = SMSService(MagicMock(), template_manager=MagicMock())
    provider = MagicMock()
    provider.is_configured.return_value = True
    provider.send_sms = AsyncMock()
    svc._provider = provider
    svc._create_action_record = AsyncMock(return_value="act-1")
    svc._update_action_status = AsyncMock()
    return svc, provider, conn, seen


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs", [{"dnc": True}, {"lead_flag": True}])
async def test_suppressed_number_is_not_texted(monkeypatch, kwargs):
    svc, provider, _c, seen = _service(monkeypatch, **kwargs)
    out = await svc.send_sms(tenant_id=TENANT, to_number=NUMBER, message="hi", triggered_by="reminder")
    assert out["success"] is False and out["opted_out"] is True
    assert out["confirmation_allowed"] is False
    provider.send_sms.assert_not_awaited()
    svc._create_action_record.assert_not_awaited()
    assert seen["call"] == (TENANT, NUMBER)


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs", [{"dnc_error": True}, {"lead_error": True}])
async def test_lookup_error_fails_closed(monkeypatch, kwargs):
    svc, provider, _c, _s = _service(monkeypatch, **kwargs)
    out = await svc.send_sms(tenant_id=TENANT, to_number=NUMBER, message="hi")
    assert out["success"] is False and out["opted_out"] is False
    assert "could not be checked" in out["error"]
    provider.send_sms.assert_not_awaited()


@pytest.mark.asyncio
async def test_suppression_gate_runs_before_idempotent_path(monkeypatch):
    svc, provider, _c, _s = _service(monkeypatch, dnc=True)
    svc._check_idempotency = AsyncMock(side_effect=AssertionError("must not be reached"))
    out = await svc.send_sms(tenant_id=TENANT, to_number=NUMBER, message="hi", idempotency_key="k")
    assert out["opted_out"] is True
    provider.send_sms.assert_not_awaited()


@pytest.mark.asyncio
async def test_meeting_reminder_path_is_gated(monkeypatch):
    svc, provider, _c, _s = _service(monkeypatch, dnc=True)
    svc.template_manager.render_meeting_reminder.return_value = "reminder"
    out = await svc.send_meeting_reminder(tenant_id=TENANT, to_number=NUMBER, reminder_type="1h",
                                          name="A", title="T", time="9am")
    assert out["opted_out"] is True
    provider.send_sms.assert_not_awaited()


@pytest.mark.asyncio
async def test_allowed_number_is_sent(monkeypatch):
    svc, provider, conn, _s = _service(monkeypatch)
    provider.send_sms.return_value = MagicMock(success=True, message_id="m1", provider="vonage",
                                               cost=0.01, metadata={}, error=None)
    out = await svc.send_sms(tenant_id=TENANT, to_number=NUMBER, message="hi")
    assert out["success"] is True and out["message_id"] == "m1"
    provider.send_sms.assert_awaited_once()
    assert conn.args[0] == TENANT and NUMBER in conn.args[1]
