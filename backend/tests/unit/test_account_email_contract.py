"""QA F1-35/F1-17/F4-05/F15-02: capture mail, never send it."""
from html import escape
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.services import email_service as mail
from app.domain.services import notification_service as notifications


ATTACK = '<img src=x onerror=alert(1)>'
BASE = 'https://customer.example.invalid'


@pytest.fixture
def configured(monkeypatch):
    from app.core import config
    settings = SimpleNamespace(email_user='login@example.invalid', email_pass='fake-only',
        from_email='accounts@talkleeai.com', brand_name='Example & Company', frontend_url=BASE)
    monkeypatch.setattr(mail, 'get_settings', lambda: settings)
    monkeypatch.setattr(config, 'get_settings', lambda: settings)
    monkeypatch.setenv('FRONTEND_URL', BASE)
    monkeypatch.setenv('BRAND_NAME', settings.brand_name)
    monkeypatch.setenv('FROM_EMAIL', settings.from_email)
    monkeypatch.setenv('EMAIL_USER', settings.email_user)
    monkeypatch.setenv('EMAIL_PASS', settings.email_pass)
    return settings


@pytest.mark.asyncio
@pytest.mark.parametrize('template', ['verification', 'signup', 'reset'])
async def test_account_templates_escape_names_and_use_the_configured_brand(configured, template):
    service = mail.EmailService()
    service.send_email = AsyncMock(return_value=True)
    if template == 'verification':
        await service.send_verification_email('person@example.invalid', ATTACK,
            'https://talky.ai/auth/verify-email?token=synthetic&next=account')
    elif template == 'signup':
        await service.send_signup_code_email('person@example.invalid', ATTACK, '123456')
    else:
        sender = getattr(service, 'send_password_reset_email', None)
        assert callable(sender), 'Password resets have no dedicated reset template'
        await sender('person@example.invalid', ATTACK, '123456')
    payload = service.send_email.await_args.kwargs
    assert ATTACK not in payload['html_body'], 'Account email renders the supplied name as HTML'
    assert escape(ATTACK) in payload['html_body']
    assert escape(configured.brand_name) in payload['html_body']
    assert configured.brand_name in payload['text_body']
    assert 'talky.ai' not in str(payload).lower()
    if template == 'reset':
        assert 'signup' not in str(payload).lower()
        assert 'reset' in payload['subject'].lower()
        assert '123456' in payload['html_body']
        assert 'reset form you already opened' in payload['html_body']
        assert 'To start a new reset request' in payload['html_body']
        assert f'href="{BASE}/auth/forgot-password"' in payload['html_body']
        assert 'reset form you already opened' in payload['text_body']
    if template == 'verification':
        assert BASE + '/auth/verify-email?token=synthetic&amp;next=account' in payload['html_body']


@pytest.mark.asyncio
@pytest.mark.parametrize('template,path', [('suspension', '/support'),
    ('billing', '/billing'), ('security', '/security')])
async def test_notification_templates_escape_every_text_value_and_link_to_frontend(configured, template, path):
    service = notifications.NotificationService()
    service.send_email = AsyncMock(return_value={'status': 'success'})
    only_email = notifications.NotificationChannel.EMAIL
    if template == 'suspension':
        await service.notify_suspension('synthetic-id', 'person@example.invalid', ATTACK, ATTACK, only_email)
    elif template == 'billing':
        await service.notify_billing_failure('person@example.invalid', 12.5, ATTACK, only_email)
    else:
        await service.notify_security_alert('person@example.invalid', ATTACK, ATTACK, only_email)
    body = service.send_email.await_args.kwargs['html_body']
    assert ATTACK not in body, 'Notification email renders supplied text as HTML'
    assert escape(ATTACK) in body
    assert f'href="{BASE}{path}"' in body
    assert 'talky.ai' not in body.lower()


@pytest.mark.asyncio
@pytest.mark.parametrize('accepted', [True, False])
async def test_notifications_use_the_same_account_sender_and_preserve_acknowledgement(monkeypatch, configured, accepted):
    sender = SimpleNamespace(send_email=AsyncMock(return_value=accepted))
    monkeypatch.setattr(mail, 'get_email_service', lambda: sender)
    service = notifications.NotificationService()
    # If the legacy provider is still used, this fake makes the mistake observable.
    service._send_via_sendgrid = AsyncMock(return_value={'status': 'legacy'})
    service._send_via_smtp = AsyncMock(return_value={'status': 'legacy'})
    service._send_via_ses = AsyncMock(return_value={'status': 'legacy'})
    outcome = await service.send_email('person@example.invalid', 'Synthetic', '<p>hello</p>',
        'hello', reply_to='reply@example.invalid')
    assert sender.send_email.await_count == 1, 'Notifications still bypass the account email stack'
    assert outcome['status'] == ('success' if accepted else 'error')
    assert not outcome.get('message_id'), 'Do not invent provider acceptance identities'
    assert sender.send_email.await_args.kwargs['reply_to'] == 'reply@example.invalid'


@pytest.mark.asyncio
async def test_smtp_uses_configured_from_brand_but_authenticates_with_login(monkeypatch, configured):
    smtp = SimpleNamespace(login=AsyncMock(), send_message=AsyncMock())
    class Connection:
        def __init__(self, **kwargs):
            assert kwargs['start_tls'] is True
            assert 0 < kwargs['timeout'] <= 30
        async def __aenter__(self):
            return smtp
        async def __aexit__(self, *_args):
            return False
    monkeypatch.setattr(mail.aiosmtplib, 'SMTP', Connection)
    service = mail.EmailService()
    assert await service.send_email('person@example.invalid', 'Synthetic', '<p>safe</p>')
    smtp.login.assert_awaited_once_with(configured.email_user, configured.email_pass)
    message = smtp.send_message.await_args.args[0]
    from email.utils import parseaddr
    assert parseaddr(message['From']) == (configured.brand_name, configured.from_email)


@pytest.mark.parametrize('value', ['', 'noreply@talky.ai', 'noreply@talkleeai.com.evil.invalid',
    'Talk-Lee <noreply@talkleeai.com>', 'noreply@talkleeai.com\r\nBcc: other@example.invalid'])
def test_production_gate_requires_an_unambiguous_owned_from_address(monkeypatch, value):
    from app.core import prod_gate
    monkeypatch.setenv('ENVIRONMENT', 'production')
    monkeypatch.setenv('FROM_EMAIL', value)
    with pytest.raises(prod_gate.ProductionGateError) as error:
        prod_gate.enforce_production_gate()
    assert 'FROM_EMAIL' in str(error.value), 'Production boot does not validate the account sender domain'


def test_settings_declare_single_brand_and_from_address(monkeypatch):
    from app.core.config import Settings
    monkeypatch.delenv('BRAND_NAME', raising=False)
    monkeypatch.delenv('FROM_EMAIL', raising=False)
    settings = Settings(_env_file=None)
    assert getattr(settings, 'brand_name', None) == 'Talk-Lee'
    assert 'from_email' in Settings.model_fields
