"""Customer-facing default brand and durable protocol identifiers."""
import json
import os
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


async def test_registration_options_show_brand_and_keep_identity(monkeypatch):
    from app.core.security import passkeys

    # The id and the origin are read from the environment at import. Pin them, so this test is
    # the same on a machine whose settings file sets them; the display name is a constant.
    monkeypatch.setattr(passkeys, "RP_ID", "talkleeai.com")
    monkeypatch.setattr(passkeys, "RP_ORIGIN", "https://talkleeai.com")
    options = await passkeys.generate_registration_options(
        AsyncMock(), "11111111-1111-4111-8111-111111111111", "fixture@example.invalid"
    )
    assert json.loads(options.options_json)["rp"] == {"name": "Talk-Lee", "id": "talkleeai.com"}


def test_authenticator_issuer_and_existing_enrollment():
    from app.core.security import totp

    seed = totp.generate_totp_secret()
    # A code from an authenticator enrolled under the previous label still verifies:
    # the label is not part of the code.
    previous = pyotp.parse_uri(totp.get_provisioning_uri(seed, "fixture@example.invalid", issuer="Talky.ai"))
    assert totp.verify_totp_code(seed, previous.now())
    # A new enrolment carries the configured label ...
    current = pyotp.parse_uri(totp.get_provisioning_uri(seed, "fixture@example.invalid"))
    assert current.issuer == totp.TOTP_ISSUER_NAME
    # ... whose default is the brand. The label is read from the environment at import, so the
    # default can only be checked where the setting is not set.
    if "TOTP_ISSUER_NAME" not in os.environ:
        assert totp.TOTP_ISSUER_NAME == "Talk-Lee"


async def test_two_step_setup_answer_names_the_issuer_of_the_enrolment_address(monkeypatch):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    from app.api.v1.endpoints.mfa import setup

    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=None)
    conn.execute = AsyncMock(return_value="INSERT 0 1")

    @asynccontextmanager
    async def acquire(_pool, _tenant):
        yield conn

    monkeypatch.setattr(setup, "acquire_with_tenant", acquire)
    monkeypatch.setattr(setup, "lock_authenticated_credential_change", AsyncMock())
    monkeypatch.setattr(setup, "invalidate_all_codes", AsyncMock())
    monkeypatch.setattr(setup, "encrypt_totp_secret", lambda raw: "dummy")
    user = SimpleNamespace(id="11111111-1111-4111-8111-111111111111", tenant_id=None,
                           email="fixture@example.invalid")
    answer = await setup.setup_mfa(MagicMock(), current_user=user, db_client=SimpleNamespace(pool=object()))

    # The answer used to carry its own hard-coded copy of the label.
    assert answer.issuer == pyotp.parse_uri(answer.provisioning_uri).issuer == setup.TOTP_ISSUER_NAME
    assert answer.account == "fixture@example.invalid"


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
