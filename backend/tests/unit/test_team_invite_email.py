"""QA F1-15 / D4-2: the invite email.

The token rides in the link's fragment (never sent to a server), names are
escaped in HTML, and no workspace or person name can add a mail header.
"""
from app.domain.services.email_service import EmailService


async def test_the_invite_email_links_to_the_accept_page_and_escapes_names(monkeypatch):
    service = EmailService(sender_email="noreply@example.com", sender_password="unused")
    service.frontend_url = "https://app.example.com"
    sent = {}

    async def capture(**message):
        sent.update(message)
        return True

    monkeypatch.setattr(service, "send_email", capture)
    delivered = await service.send_team_invite_email(
        recipient_email="sam@example.com",
        workspace_name="Acme <b>Ltd</b>\r\nBcc: someone@example.org",
        inviter="Ada\nAdmin",
        role_name="agent",
        token="tok_123-abc",
        expires_at="2026-11-18T09:00:00+00:00",
    )
    assert delivered is True
    assert sent["recipient_email"] == "sam@example.com"
    assert "\n" not in sent["subject"] and "\r" not in sent["subject"]
    assert sent["subject"].startswith("Ada Admin invited you to Acme <b>Ltd</b> Bcc:")
    link = "https://app.example.com/auth/accept-invite#token=tok_123-abc"
    assert link in sent["text_body"]
    assert f'href="{link}"' in sent["html_body"]
    assert "<b>Ltd</b>" not in sent["html_body"] and "&lt;b&gt;Ltd&lt;/b&gt;" in sent["html_body"]
    assert "as an agent" in sent["text_body"] and "18 November 2026" in sent["text_body"]
