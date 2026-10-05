"""Password reset admission acknowledgements do not assert mail delivery."""
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api.v1.endpoints.auth import password_reset as reset


@pytest.mark.parametrize('exists,sent', [(True, False), (True, True), (False, False)])
async def test_forgot_password_does_not_claim_email_was_sent(monkeypatch, exists, sent):
    @asynccontextmanager
    async def connection(*_):
        yield SimpleNamespace(fetchrow=AsyncMock(return_value={
            'id': '00000000-0000-4000-8000-000000000001',
            'password_hash': 'synthetic-stored-generation',
        } if exists else None))
    redis = SimpleNamespace(incr=AsyncMock(return_value=1), expire=AsyncMock(), setex=AsyncMock())
    sender = SimpleNamespace(send_password_reset_email=AsyncMock(return_value=sent))
    audit = SimpleNamespace(log=AsyncMock())
    monkeypatch.setattr(reset, 'acquire_with_tenant', connection)
    monkeypatch.setattr(reset, '_get_redis_or_503', lambda: redis)
    monkeypatch.setattr(reset, 'get_email_service', lambda: sender)
    monkeypatch.setattr(reset, 'get_client_ip', lambda _: '127.0.0.1')
    monkeypatch.setattr(reset, 'get_user_agent', lambda _: 'synthetic')
    result = await reset.forgot_password(None, reset.ForgotPasswordRequest(email='synthetic@example.com'), SimpleNamespace(pool=None), audit)
    assert 'has been sent' not in result['message']
    assert 'delivered' not in result['message']
    if exists:
        assert 'emailed' not in audit.log.call_args.kwargs['description']
    else:
        sender.send_password_reset_email.assert_not_awaited()
