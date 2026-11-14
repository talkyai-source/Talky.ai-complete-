"""Forms audit B-F2/F20: what POST /email/send does with the payload the Send-email dialog builds.

Drives the real endpoint function and the real EmailService.send_email with a fake
connector. Only the DB-touching helpers are stubbed.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints import email as email_endpoint
from app.api.v1.endpoints.email import SendEmailRequest
from app.services.email_service import EmailService

TENANT = "00000000-0000-0000-0000-000000000001"
TEMPLATE_HTML = "<p>Hello from the template</p>"

# Exactly what the dialog sent before the fix (backend-api.ts email.send).
OLD_UNEDITED = {"to": ["a@x.com"], "template_id": "meeting_confirmation", "subject": "Hi"}
OLD_EDITED = {**OLD_UNEDITED, "html": "<p>My edit</p>"}
# What the dialog sends after the fix.
NEW_UNEDITED = {**OLD_UNEDITED, "body_html": TEMPLATE_HTML, "body": "Hello from the template"}
NEW_EDITED = {**OLD_UNEDITED, "body_html": "<p>My edit</p>", "body": "My edit"}


def _service():
    service = EmailService(MagicMock())
    service._create_action_record = AsyncMock(return_value="action-1")
    service._update_action_status = AsyncMock()
    service._bind_action_authorization = AsyncMock()
    connector = MagicMock()
    connector.send_email = AsyncMock(return_value=SimpleNamespace(id="msg-1", thread_id="t-1"))
    service._get_active_email_connector = AsyncMock(return_value=(connector, "conn-1", "gmail"))
    return service, connector


async def _post(payload):
    service, connector = _service()
    identity = {"connector_id": "conn-1", "provider": "gmail", "account_row_id": "acc-1"}
    with patch.object(email_endpoint, "get_email_service", return_value=service), \
         patch("app.services.email_service.reviewed_authorization_identity", return_value=identity), \
         patch("app.services.email_service.check_reviewed_authorization_current"):
        result = await email_endpoint.send_email(
            SendEmailRequest(**payload), current_user=SimpleNamespace(tenant_id=TENANT), db_client=MagicMock(),
        )
    return result, connector


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [OLD_UNEDITED, OLD_EDITED], ids=["unedited", "edited"])
async def test_old_dialog_payload_is_refused_whether_or_not_the_user_edited(payload):
    """No `body` and no template_context: nothing renders, the body is empty, validation refuses.
    `html` is not a field on SendEmailRequest, so the edit is dropped before that."""
    assert "html" not in SendEmailRequest.model_fields
    service, connector = _service()
    with patch.object(email_endpoint, "get_email_service", return_value=service):
        with pytest.raises(HTTPException) as exc:
            await email_endpoint.send_email(
                SendEmailRequest(**payload), current_user=SimpleNamespace(tenant_id=TENANT), db_client=MagicMock(),
            )
    assert exc.value.status_code == 500
    assert "Body cannot be empty" in exc.value.detail
    connector.send_email.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [NEW_UNEDITED, NEW_EDITED], ids=["unedited", "edited"])
async def test_new_dialog_payload_sends_what_the_preview_showed(payload):
    result, connector = await _post(payload)
    assert result.status == "accepted" and result.message_id == "msg-1"
    sent = connector.send_email.await_args.kwargs
    assert sent["subject"] == "Hi"
    assert sent["body_html"] == payload["body_html"]
    assert sent["body"] == payload["body"]


@pytest.mark.asyncio
async def test_empty_subject_is_refused_so_the_dialog_must_require_one():
    service, connector = _service()
    with patch.object(email_endpoint, "get_email_service", return_value=service):
        with pytest.raises(HTTPException) as exc:
            await email_endpoint.send_email(
                SendEmailRequest(**{**NEW_UNEDITED, "subject": ""}),
                current_user=SimpleNamespace(tenant_id=TENANT), db_client=MagicMock(),
            )
    assert "Subject cannot be empty" in exc.value.detail
    connector.send_email.assert_not_called()

