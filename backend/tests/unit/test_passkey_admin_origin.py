"""R3: passkey sign-in works on the Admin console's host.

The RP ID ``talkleeai.com`` already covers ``admin.talkleeai.com``, but the
server pinned the inbound Origin to ``[PASSKEY_RP_ORIGIN] + dev origins`` and
answered 400 "Origin not permitted" on the Admin host.
"""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints import passkeys as passkey_ep
from app.core.security import passkeys


@pytest.fixture(autouse=True)
def production_rp(monkeypatch):
    monkeypatch.setattr(passkeys, "RP_ID", "talkleeai.com")
    monkeypatch.setattr(passkeys, "RP_ORIGIN", "https://talkleeai.com")
    monkeypatch.delenv("PASSKEY_EXTRA_ORIGINS", raising=False)


def _request(origin):
    return SimpleNamespace(headers={"origin": origin})


def test_the_admin_host_is_allowed_by_default():
    assert "https://admin.talkleeai.com" in passkeys.get_allowed_origins()
    assert passkey_ep._validate_passkey_origin(_request("https://admin.talkleeai.com")) == "https://admin.talkleeai.com"


def test_the_main_site_and_dev_origins_still_work():
    allowed = passkeys.get_allowed_origins()
    assert allowed[0] == "https://talkleeai.com"
    assert "http://localhost:3000" in allowed


def test_extra_origins_must_be_https_hosts_under_the_rp_id(monkeypatch):
    monkeypatch.setenv("PASSKEY_EXTRA_ORIGINS", " https://admin.talkleeai.com/ ,https://evil.example.com,"
                       "http://ops.talkleeai.com,https://staging.talkleeai.com/path,https://talkleeai.com.evil.com")
    extras = [o for o in passkeys.get_allowed_origins() if o not in ("https://talkleeai.com", *passkeys.DEV_ORIGINS)]
    assert extras == ["https://admin.talkleeai.com"]


def test_an_empty_setting_turns_the_default_off(monkeypatch):
    monkeypatch.setenv("PASSKEY_EXTRA_ORIGINS", "")
    assert "https://admin.talkleeai.com" not in passkeys.get_allowed_origins()
    with pytest.raises(HTTPException) as refused:
        passkey_ep._validate_passkey_origin(_request("https://admin.talkleeai.com"))
    assert refused.value.status_code == 400


def test_an_unrelated_origin_is_still_refused():
    with pytest.raises(HTTPException) as refused:
        passkey_ep._validate_passkey_origin(_request("https://evil.example.com"))
    assert refused.value.status_code == 400
