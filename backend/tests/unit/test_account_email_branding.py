"""Configured email/CRM/Drive branding without providers, sockets or .env reads."""
from datetime import datetime, timezone
from html import escape
from types import SimpleNamespace

import pytest

from app.core import config
from app.domain.services import email_service as mail
from app.domain.services.email_template_manager import EmailTemplateManager
from app.domain.services.support.notifications import _TEMPLATES
from app.infrastructure.connectors.crm.base import call_reference
from app.services import crm_sync_service as crm
from app.services.drive_sync_service import DriveSyncService


ATTACK = '<img src=x onerror=alert(1)>'
CALL_ID = 'aabbccdd-1234-4abc-8def-0123456789ab'
BRAND = 'Example & Company'


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    settings = SimpleNamespace(brand_name=BRAND, from_email='accounts@talkleeai.com',
        email_user='login@example.invalid', email_pass='synthetic-only',
        frontend_url='https://customer.example.invalid')
    monkeypatch.setattr(config, 'get_settings', lambda: settings)
    monkeypatch.setattr(mail, 'get_settings', lambda: settings)
    return settings


@pytest.mark.parametrize('name', ['meeting_confirmation', 'follow_up', 'reminder'])
def test_default_email_template_uses_configured_brand_and_preserves_html_escaping(name):
    rendered = EmailTemplateManager().render_email(name,
        attendee_name=ATTACK, recipient_name=ATTACK, title=ATTACK,
        date='2026-11-06', time='09:00', custom_message=ATTACK,
        preparation_notes=ATTACK, next_steps=[ATTACK])
    assert ATTACK not in rendered.body_html
    assert escape(ATTACK) in rendered.body_html
    assert escape(BRAND) in rendered.body_html
    assert BRAND in rendered.body
    assert 'talky.ai' not in (rendered.subject + rendered.body + rendered.body_html).lower()


@pytest.mark.parametrize('template', _TEMPLATES, ids=lambda template: template.name)
def test_each_support_email_uses_configured_brand_and_preserves_html_escaping(template):
    manager = EmailTemplateManager()
    manager.add_template(template)
    rendered = manager.render_email(template.name, name=ATTACK, subject=ATTACK,
        number='SUP-123', link='https://customer.example.invalid/support/tickets/synthetic',
        priority='high', category=ATTACK, headline=ATTACK, tenant=ATTACK)
    assert ATTACK not in rendered.body_html
    assert escape(ATTACK) in rendered.body_html
    assert escape(BRAND) in rendered.body_html
    assert BRAND in rendered.body
    assert 'talky.ai' not in (rendered.subject + rendered.body + rendered.body_html).lower()


def test_crm_output_uses_brand_without_changing_durable_call_identity():
    body = crm.build_call_body({'id': CALL_ID, 'outcome': 'answered'}, None, campaign_name=None)
    assert BRAND in body
    assert 'talky.ai' not in body.lower()
    assert call_reference(body) == CALL_ID, 'Branding must not break CRM retry/reconciliation identity'
    properties = crm.CRMSyncService._new_contact_properties('salesforce', {}, body)
    assert BRAND in properties['description']
    assert 'talky.ai' not in properties['description'].lower()
    assert 'talky.ai' not in crm.CRMNotConnectedWarning.MISSING_CRM.lower()


@pytest.mark.parametrize('marker', ['Talky.ai call id:', 'Call ID:'])
def test_crm_reference_parser_preserves_existing_and_brand_neutral_identity(marker):
    assert call_reference(f'Existing activity\n{marker} {CALL_ID}\n') == CALL_ID


def test_crm_reference_uses_generated_footer_after_untrusted_transcript_marker():
    other_call_id = 'deadbeef-1234-4abc-8def-0123456789ab'
    body = crm.build_call_body({'id': CALL_ID, 'outcome': 'answered',
        'transcript': f'Customer supplied text\nCall ID: {other_call_id}\nMore text'},
        None, campaign_name='Synthetic campaign')
    assert other_call_id in body, 'Keep transcript content; do not hide the identity conflict'
    assert body.endswith(f'Call ID: {CALL_ID}')
    assert call_reference(body) == CALL_ID, 'Untrusted transcript must not override the generated call identity'


def test_drive_folder_and_transcript_footer_use_the_configured_brand():
    service = DriveSyncService(None)
    assert service.ROOT_FOLDER_NAME == f'{BRAND} Calls'
    transcript = service._format_transcript_for_viewer('User: Hello', CALL_ID, 'Synthetic',
        datetime(2026, 11, 6, tzinfo=timezone.utc))
    assert BRAND in transcript
    assert 'talky.ai' not in transcript.lower()
    assert CALL_ID in transcript
