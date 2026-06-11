"""The assistant's report_issue tool files a support ticket by email — verify
it targets the configured address, includes the tenant id, and refuses an
empty description. The email send is mocked (no real SMTP)."""
from __future__ import annotations

import sys
import types

import pytest

from app.infrastructure.assistant.tools import comms


def _install_fake_email_service(monkeypatch, sink: dict):
    """Patch app.services.email_service.get_email_service with a fake whose
    send_email records its kwargs into `sink`."""
    mod = types.ModuleType("app.services.email_service")

    class _EmailNotConnectedError(Exception):
        pass

    class _Svc:
        async def send_email(self, **kwargs):
            sink.update(kwargs)
            return {"success": True}

    mod.get_email_service = lambda db_client: _Svc()
    mod.EmailNotConnectedError = _EmailNotConnectedError
    monkeypatch.setitem(sys.modules, "app.services.email_service", mod)


def test_support_email_default_and_override(monkeypatch):
    monkeypatch.delenv("SUPPORT_REPORT_EMAIL", raising=False)
    assert "@" in comms._support_report_email()
    monkeypatch.setenv("SUPPORT_REPORT_EMAIL", "ops@example.com")
    assert comms._support_report_email() == "ops@example.com"


@pytest.mark.asyncio
async def test_report_issue_requires_description():
    res = await report_issue_noemail("   ")
    assert res["success"] is False
    assert "description" in res["error"].lower()


async def report_issue_noemail(description):
    # db_client unused on the empty-description early return.
    return await comms.report_issue(tenant_id="t1", db_client=None, description=description)


@pytest.mark.asyncio
async def test_report_issue_sends_to_support_with_tenant(monkeypatch):
    monkeypatch.setenv("SUPPORT_REPORT_EMAIL", "support@example.com")
    sink: dict = {}
    _install_fake_email_service(monkeypatch, sink)

    res = await comms.report_issue(
        tenant_id="tenant-123",
        db_client=None,
        description="Calls won't go through from my campaign",
        category="calls",
        severity="high",
        contact_email="me@acme.com",
    )

    assert res["success"] is True
    assert sink["to"] == ["support@example.com"]
    assert "tenant-123" in sink["body"]
    assert "me@acme.com" in sink["body"]
    assert "Calls won't go through" in sink["body"]
    assert "high" in sink["subject"].lower()
