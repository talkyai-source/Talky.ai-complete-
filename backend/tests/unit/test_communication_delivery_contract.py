import asyncio
import threading
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock

import pytest
from cryptography.fernet import Fernet

from app.infrastructure.assistant.tools import comms
from app.infrastructure.connectors.base import ConnectorProviderError
from app.infrastructure.connectors.sms.base import SMSResult
from app.infrastructure.connectors.sms.vonage_sms import VonageSMSProvider
from app.services.email_service import EmailService, EmailNotConnectedError
from app.services.sms_service import SMSService

TENANT = '00000000-0000-0000-0000-000000000001'


class Connection:
    def __init__(self, fail=None):
        self.queries, self.fail = [], fail
    async def __aenter__(self):
        return self
    async def __aexit__(self, *_):
        pass
    def transaction(self):
        return self
    async def execute(self, query, *args):
        self.queries.append((query, args))
        if self.fail and query.lstrip().startswith(self.fail):
            raise RuntimeError('database unavailable')
        return 'UPDATE 1' if query.lstrip().startswith('UPDATE') else 'INSERT 0 1'
    async def fetchrow(self, query, *args):
        self.queries.append((query, args))
        return None


class Pool:
    def __init__(self, fail=None):
        self.conn = Connection(fail)
    def acquire(self):
        return self.conn


@pytest.fixture(autouse=True)
def encryption(monkeypatch):
    monkeypatch.setenv('CONNECTOR_ENCRYPTION_KEY', Fernet.generate_key().decode())
    # These dispatch/receipt controls stub connector resolution. Supply its
    # corresponding current authorization rows; canonical resolution is tested
    # through the real OAuth callback in test_reviewed_account_identity.
    from tests.unit.test_inbox_original_account import _Rows
    import app.services.email_service as module
    def client(pool):
        rows = _Rows()
        rows.pool = pool
        rows.rows['connectors'] = [dict(id=cid, tenant_id=TENANT, provider='gmail', status='active')
                                  for cid in ('connector', 'c')]
        rows.rows['connector_accounts'] = [dict(id='authorization-row', connector_id=cid,
            tenant_id=TENANT, status='active', external_account_id='account',
            created_at='2026-01-01T00:00:00+00:00') for cid in ('connector', 'c')]
        return rows
    monkeypatch.setattr(module, 'PostgresClient', client)


def email_service(pool):
    service = EmailService(pool, NS(validate_content=MagicMock()))
    connector = NS(tenant_id=TENANT, account_row_id='authorization-row', external_account_id='account', send_email=AsyncMock(return_value=NS(id='mail-receipt', thread_id='thread')))
    service._get_active_email_connector = AsyncMock(return_value=(connector, 'connector', 'gmail'))
    return service, connector


async def send(service, kind):
    if kind == 'email':
        return await service.send_email(TENANT, ['test@example.invalid'], 'Subject', 'Exact body')
    return await service.send_sms(TENANT, '+15555550101', 'Exact message')


def service_for(kind, pool):
    if kind == 'email':
        service, provider = email_service(pool)
        return service, provider.send_email
    service = SMSService(pool)
    service._provider = NS(is_configured=lambda: True, send_sms=AsyncMock(return_value=SMSResult(
        success=True, message_id='sms-receipt', provider='vonage')))
    return service, service._provider.send_sms


@pytest.mark.parametrize('kind', ['email', 'sms'])
async def test_no_external_send_when_initial_audit_write_fails(kind):
    service, operation = service_for(kind, Pool(fail='INSERT'))
    with pytest.raises(RuntimeError, match='database unavailable'):
        await send(service, kind)
    operation.assert_not_awaited()


@pytest.mark.parametrize('kind', ['email', 'sms'])
async def test_external_ack_is_not_confirmed_when_receipt_persistence_fails(kind):
    service, operation = service_for(kind, Pool(fail='UPDATE assistant_actions SET status'))
    result = await send(service, kind)
    assert result['success'] is False and result['status'] == 'unknown'
    assert result['confirmation_allowed'] is False and result['message_id']
    operation.assert_awaited_once()


@pytest.mark.parametrize('kind', ['email', 'sms'])
async def test_transport_error_after_dispatch_is_unknown_never_retried(kind):
    pool = Pool()
    service, operation = service_for(kind, pool)
    operation.side_effect = TimeoutError('response lost')
    result = await send(service, kind)
    assert not result['success'] and result['status'] == 'unknown'
    operation.assert_awaited_once()
    assert any(args[0] == 'unknown' for sql, args in pool.conn.queries if sql.startswith('UPDATE'))


@pytest.mark.parametrize('kind', ['email', 'sms'])
async def test_cancellation_keeps_unknown_receipt(kind):
    pool = Pool()
    service, operation = service_for(kind, pool)
    operation.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await send(service, kind)
    assert any(args[0] == 'unknown' for sql, args in pool.conn.queries if sql.startswith('UPDATE'))


async def test_email_uses_canonical_resolver_for_pool_and_forced_refresh(monkeypatch):
    import app.services.email_service as module
    pool = Pool()
    resolve = AsyncMock(return_value=('connector', 'connector-id', 'gmail'))
    monkeypatch.setattr(module, 'resolve_active_connector', resolve)
    service = EmailService(pool)
    assert await service._get_active_email_connector(TENANT, force_refresh=True) == ('connector', 'connector-id', 'gmail')
    assert resolve.await_args.args[0].pool is pool
    assert resolve.await_args.args[1:] == (TENANT, 'email')
    assert resolve.await_args.kwargs == {'force_refresh': True, 'reviewed_authorization': True}


async def test_email_authentication_retry_only_after_definite_401():
    service, old = email_service(Pool())
    old.send_email.side_effect = ConnectorProviderError(provider='gmail', operation='send_email',
        category='authentication', status_code=401, message='expired')
    fresh = NS(tenant_id=TENANT, account_row_id='authorization-row', external_account_id='account', send_email=AsyncMock(return_value=NS(id='refreshed-receipt', thread_id=None)))
    service._get_active_email_connector.side_effect = [(old, 'c', 'gmail'), (fresh, 'c', 'gmail')]
    result = await send(service, 'email')
    assert result['success'] and result['status'] == 'accepted'
    assert result['message_id'] == 'refreshed-receipt'
    assert service._get_active_email_connector.await_args.kwargs == {'force_refresh': True, 'connector_id': 'c', 'account_id': 'authorization-row'}
    old.send_email.assert_awaited_once()
    fresh.send_email.assert_awaited_once()


async def test_sms_idempotency_reads_message_from_json_receipt_and_lookup_errors_fail_closed():
    pool = Pool()
    service = SMSService(pool)
    await service._check_idempotency(TENANT, 'key')
    query = next(q for q, _ in pool.conn.queries if 'FROM assistant_actions' in q)
    assert "output_data->>'message_id' AS external_message_id" in query
    pool.conn.fetchrow = AsyncMock(side_effect=RuntimeError('database unavailable'))
    service._provider = NS(send_sms=AsyncMock())
    with pytest.raises(RuntimeError):
        await service.send_sms(TENANT, '+15555550101', 'body', idempotency_key='key')
    service._provider.send_sms.assert_not_awaited()


async def test_email_preview_freezes_resolved_recipient_without_later_lookup(monkeypatch):
    lookup = AsyncMock(return_value=('approved@example.invalid', {'id': 'lead'}))
    monkeypatch.setattr(comms, '_resolve_lead_email', lookup)
    backend = NS(review_connector=AsyncMock(return_value={'connector_id': 'connector', 'provider': 'gmail', 'external_account_id': 'account'}), send_email=AsyncMock(return_value={'success': True, 'message_id': 'real-receipt'}))
    monkeypatch.setattr('app.services.email_service.get_email_service', lambda _: backend)
    preview = await comms.send_email(TENANT, object(), lead_id='lead', subject='Subject', body='Reviewed body')
    assert preview['preview']
    frozen = preview['_apply_args']
    lookup.side_effect = AssertionError('must not re-resolve on apply')
    backend = NS(send_email=AsyncMock(return_value={'success': True, 'message_id': 'real-receipt'}))
    monkeypatch.setattr('app.services.email_service.get_email_service', lambda _: backend)
    result = await comms.send_email(TENANT, object(), confirm=True, **frozen)
    assert result['success']
    assert backend.send_email.await_args.kwargs['to'] == ['approved@example.invalid']
    assert backend.send_email.await_args.kwargs['body'] == 'Reviewed body'


async def test_tenant_email_does_not_fall_back_to_global_smtp(monkeypatch):
    backend = NS(send_email=AsyncMock(side_effect=EmailNotConnectedError()))
    monkeypatch.setattr('app.services.email_service.get_email_service', lambda _: backend)
    smtp = MagicMock()
    monkeypatch.setattr('app.infrastructure.connectors.email.smtp.SMTPConnector', smtp)
    result = await comms.send_email(TENANT, object(), to=['test@example.invalid'], subject='s', body='b', confirm=True)
    assert not result['success'] and result['status'] == 'failed'
    smtp.assert_not_called()


async def test_sms_preview_has_no_mutation_and_apply_uses_provider_receipt(monkeypatch):
    backend = NS(send_sms=AsyncMock(return_value={'success': True, 'status': 'accepted', 'message_id': 'real-id'}))
    monkeypatch.setattr('app.services.sms_service.get_sms_service', lambda _: backend)
    preview = await comms.send_sms(TENANT, object(), ['+15555550101'], 'Approved message')
    assert preview['preview'] and preview['_apply_args']['message'] == 'Approved message'
    backend.send_sms.assert_not_awaited()
    result = await comms.send_sms(TENANT, object(), confirm=True, **preview['_apply_args'])
    assert result['success'] and result['status'] == 'accepted'
    assert result['receipts'][0]['message_id'] == 'real-id'
    backend.send_sms.assert_awaited_once()


async def test_report_preview_freezes_content_and_send_failure_is_not_claimed_success(monkeypatch):
    monkeypatch.setenv('SUPPORT_REPORT_EMAIL', 'support@example.invalid')
    backend = NS(review_connector=AsyncMock(return_value={'connector_id':'email', 'provider':'gmail', 'external_account_id':'account'}), send_email=AsyncMock(return_value={'success': False, 'status': 'unknown', 'error': 'timeout'}))
    monkeypatch.setattr('app.services.email_service.get_email_service', lambda _: backend)
    preview = await comms.report_issue(TENANT, object(), 'Calls failed', contact_email='reporter@example.invalid')
    backend.send_email.assert_not_awaited()
    reviewed = preview['_apply_args']['_prepared_report']['body']
    result = await comms.report_issue(TENANT, object(), confirm=True, **preview['_apply_args'])
    assert not result['success'] and result['status'] == 'unknown'
    assert backend.send_email.await_args.kwargs['body'] == reviewed


async def test_vonage_missing_sdk_is_failure_never_simulated_success():
    provider = VonageSMSProvider()
    provider._initialized = True
    provider._sms = None
    provider._default_from = 'TestSender'
    result = await provider.send_sms('+15555550101', 'test')
    assert not result.success and not result.message_id


async def test_vonage_sdk_send_runs_off_event_loop_and_requires_message_id(monkeypatch):
    import app.infrastructure.connectors.sms.vonage_sms as module
    event_thread = threading.get_ident()
    threads = []
    def submit(message):
        threads.append(threading.get_ident())
        return NS(messages=[NS(status='0', message_id='vonage-receipt', message_price='0.01')])
    monkeypatch.setattr(module, 'VONAGE_V4', True)
    monkeypatch.setattr(module, 'SmsMessage', lambda **kw: NS(**kw))
    provider = VonageSMSProvider()
    provider._initialized = True
    provider._sms = NS(send=submit)
    provider._default_from = 'TestSender'
    result = await provider.send_sms('+15555550101', 'test')
    assert result.success and result.message_id == 'vonage-receipt'
    assert len(threads) == 1 and threads[0] != event_thread


async def test_historical_simulated_sms_receipt_never_confirms_a_real_send():
    service = SMSService(Pool())
    service._check_idempotency = AsyncMock(return_value={
        'id': 'audit', 'status': 'completed', 'external_message_id': 'sim-previous-test'})
    service._provider = NS(send_sms=AsyncMock())
    result = await service.send_sms(TENANT, '+15555550101', 'body', idempotency_key='key')
    assert not result['success'] and result['status'] == 'unknown'
    service._provider.send_sms.assert_not_awaited()


async def test_support_report_requires_receipt_even_if_service_claims_success(monkeypatch):
    monkeypatch.setenv('SUPPORT_REPORT_EMAIL', 'support@example.invalid')
    backend = NS(review_connector=AsyncMock(return_value={'connector_id':'email', 'provider':'gmail', 'external_account_id':'account'}), send_email=AsyncMock(return_value={'success': True}))
    monkeypatch.setattr('app.services.email_service.get_email_service', lambda _: backend)
    preview = await comms.report_issue(TENANT, object(), 'Calls failed', contact_email='reporter@example.invalid')
    result = await comms.report_issue(TENANT, object(), confirm=True, **preview['_apply_args'])
    assert not result['success'] and result['status'] == 'unknown'


@pytest.mark.parametrize('channel', ['email', 'sms'])
async def test_unknown_reminder_send_is_held_for_review_not_scheduled_again(channel):
    from app.workers.reminder_worker import ReminderWorker
    worker = ReminderWorker()
    worker._db_pool = pool = Pool()
    unknown = {'success': False, 'status': 'unknown'}
    worker._send_email_reminder = AsyncMock(return_value=unknown)
    worker._sms_service = NS(send_meeting_reminder=AsyncMock(return_value=unknown))
    await worker._process_reminder({'id': 'reminder', 'tenant_id': TENANT,
        'lead_id': None, 'meeting_id': None, 'email': 'test@example.invalid',
        'phone_number': '+15555550101' if channel == 'sms' else None})
    updates = [(sql, args) for sql, args in pool.conn.queries if sql.lstrip().startswith('UPDATE')]
    assert "status = 'failed'" in updates[-1][0]
    assert 'Outcome unknown' in updates[-1][1][1]
    operation = worker._sms_service.send_meeting_reminder if channel == 'sms' else worker._send_email_reminder
    assert operation.await_args.kwargs['idempotency_key'] == 'reminder-reminder'


async def test_email_reminder_uses_durable_request_fence(monkeypatch):
    from app.workers.reminder_worker import ReminderWorker
    execute = AsyncMock(return_value={'success': False, 'status': 'unknown'})
    monkeypatch.setattr('app.services.action_execution.DurableActionExecutor', lambda _: NS(execute=execute))
    worker = ReminderWorker()
    worker._db_pool = Pool()
    worker._email_service = NS(send_templated_email=AsyncMock())
    result = await worker._send_email_reminder(TENANT, 'test@example.invalid', '1h',
        'Test', 'Meeting', '10:00', None, None, None, 'reminder-key')
    assert result['status'] == 'unknown'
    assert execute.await_args.kwargs['idempotency_key'] == 'email-reminder:reminder-key'
    worker._email_service.send_templated_email.assert_not_awaited()


async def test_reminder_honours_approved_email_channel_and_exact_message_with_both_contacts():
    from app.workers.reminder_worker import ReminderWorker
    worker = ReminderWorker()
    worker._db_pool = Pool()
    worker._sms_service = NS(send_sms=AsyncMock(), send_meeting_reminder=AsyncMock())
    worker._send_email_reminder = AsyncMock(return_value={'success': True, 'message_id': 'receipt'})
    await worker._process_reminder({'id': 'reminder', 'tenant_id': TENANT, 'type': 'email',
        'lead_id': None, 'meeting_id': None, 'email': 'test@example.invalid', 'phone_number': '+15555550101',
        'content': {'message': 'Reviewed email reminder'}})
    worker._sms_service.send_sms.assert_not_awaited()
    worker._sms_service.send_meeting_reminder.assert_not_awaited()
    assert worker._send_email_reminder.await_args.kwargs['message'] == 'Reviewed email reminder'


async def test_cancelled_reminder_cannot_be_reclaimed_from_stale_scan():
    from app.workers.reminder_worker import ReminderWorker
    worker = ReminderWorker()
    worker._db_pool = pool = Pool()
    original = pool.conn.execute
    async def execute(sql, *args):
        if "status = 'processing'" in sql:
            assert "status = 'pending'" in sql
            return 'UPDATE 0'
        return await original(sql, *args)
    pool.conn.execute = execute
    worker._send_email_reminder = AsyncMock()
    await worker._process_reminder({'id': 'reminder', 'tenant_id': TENANT,
        'lead_id': None, 'meeting_id': None, 'email': 'test@example.invalid'})
    worker._send_email_reminder.assert_not_awaited()


async def test_support_smtp_uses_off_loop_ack_and_the_transmitted_message_id(monkeypatch):
    from app.infrastructure.connectors.email.smtp import SMTPConnector
    import app.infrastructure.connectors.email.smtp as smtp
    for key, value in {'SMTP_HOST': 'smtp.example.invalid', 'SMTP_USER': 'system',
        'SMTP_PASSWORD': 'test-only', 'SMTP_FROM_EMAIL': 'system@example.invalid', 'SMTP_USE_TLS': 'false'}.items():
        monkeypatch.setenv(key, value)
    event_thread = threading.get_ident()
    seen = []
    class Server:
        def __init__(self, host, port, timeout): assert timeout == 10
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def login(self, *_): pass
        def sendmail(self, sender, recipients, message):
            assert threading.get_ident() != event_thread
            seen.append(message)
            return {}
    monkeypatch.setattr(smtp.smtplib, 'SMTP', Server)
    result = await SMTPConnector().send_email(to=['support@example.invalid'], subject='Test', body='Test')
    assert result.id in seen[0] and 'Message-ID:' in seen[0]
