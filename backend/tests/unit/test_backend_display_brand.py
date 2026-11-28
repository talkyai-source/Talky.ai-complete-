"""Customer-facing default brand and durable protocol identifiers."""
import json
from email import message_from_string
from email.utils import parseaddr
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pyotp
import pytest


async def test_sender_display_name_and_explicit_override(monkeypatch):
    from app.infrastructure.connectors.email import smtp

    for field, value in {
        "HOST": "mail.example.invalid", "USER": "fixture", "PASSWORD": "dummy",
        "FROM_EMAIL": "fixture@example.invalid", "USE_TLS": "false",
    }.items():
        monkeypatch.setenv(f"SMTP_{field}", value)
    monkeypatch.delenv("SMTP_FROM_NAME", raising=False)
    transport = MagicMock()
    connection = transport.return_value.__enter__.return_value
    connection.sendmail.return_value = {}
    monkeypatch.setattr(smtp.smtplib, "SMTP", transport)

    for sender in ("Talk-Lee", "Configured sender"):
        if sender != "Talk-Lee":
            monkeypatch.setenv("SMTP_FROM_NAME", sender)
        await smtp.SMTPConnector().send_email(["recipient@example.invalid"], "Fixture", "Body")
        wire = message_from_string(connection.sendmail.call_args.args[2])
        assert parseaddr(wire["From"]) == (sender, "fixture@example.invalid")


async def test_registration_options_show_brand_and_keep_identity():
    from app.core.security import passkeys

    options = await passkeys.generate_registration_options(
        AsyncMock(), "11111111-1111-4111-8111-111111111111", "fixture@example.invalid"
    )
    assert json.loads(options.options_json)["rp"] == {"name": "Talk-Lee", "id": "talkleeai.com"}
    assert passkeys.RP_ORIGIN == "https://talkleeai.com"


def test_authenticator_issuer_and_existing_enrollment():
    from app.core.security import totp

    seed = totp.generate_totp_secret()
    previous = pyotp.parse_uri(totp.get_provisioning_uri(seed, "fixture@example.invalid", issuer="Talky.ai"))
    assert totp.verify_totp_code(seed, previous.now())
    current = pyotp.parse_uri(totp.get_provisioning_uri(seed, "fixture@example.invalid"))
    assert current.issuer == "Talk-Lee"


async def test_public_api_titles():
    from app.api import operational
    from app.main import app

    assert (await operational.root())["message"] == "Talk-Lee API - AI Voice Dialer"
    assert app.title == "Talk-Lee — AI Voice Dialer"


def test_assistant_names_and_demo_identity():
    from app.domain.services.ask_ai_session_config import create_ask_ai_agent_config
    from app.infrastructure.assistant.agent import SYSTEM_PROMPT

    assert SYSTEM_PROMPT.startswith("You are Talk-Lee Assistant, an AI assistant for the Talk-Lee voice calling platform.")
    assert create_ask_ai_agent_config().company_name == "Talk-Lee"


@pytest.mark.parametrize(("name", "identifiers"), [
    ("api/v1/endpoints/telephony_concurrency.py", ["https://talky.ai/problems"]),
    ("api/v1/endpoints/telephony_sip/_shared.py", ["https://talky.ai/problems"]),
    ("api/v1/endpoints/telephony_runtime/_shared.py", ["https://talky.ai/problems"]),
    ("core/security/principal.py", ["X-Talky-Expected-User", "X-Talky-Expected-Tenant"]),
    ("api/v1/endpoints/salesforce.py", [
        "X-Talky-Token",
        "Talky_Phone__c",
        "Talky_Notes__c",
        "Talky_Campaign_Id__c",
    ]),
    ("core/security/cookies.py", ["talky_at", "talky_rt"]),
    ("core/security/sessions/_shared.py", ["talky_sid"]),
    ("api/v1/endpoints/connectors.py", ["talky_oauth_nonce_"]),
])
def test_protocol_identifiers_keep_their_exact_spelling(name, identifiers):
    source = (Path(__file__).resolve().parents[2] / "app" / name).read_text(encoding="utf-8")
    for identifier in identifiers:
        assert f'"{identifier}"' in source
