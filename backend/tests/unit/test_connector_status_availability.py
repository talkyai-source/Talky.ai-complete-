"""D3-1: each connector card says whether its OAuth sign-in can run here.

GET /connectors/status gave no "OAuth is configured" signal for Google or
HubSpot (only Salesforce was hidden when unconfigured), so the dashboard
offered Connect for a sign-in that could only fail. Each card now carries
available + unavailable_reason; the reason never names a server setting.
"""
from __future__ import annotations

import pytest

from app.api.v1.endpoints import connectors as ce
from tests.unit.test_connectors_card_keys import FakeDB, _resp, _user

GOOGLE = ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET")
HUBSPOT = ("HUBSPOT_CLIENT_ID", "HUBSPOT_CLIENT_SECRET")


async def _cards():
    db = FakeDB({("connectors", "select"): [_resp([])]})
    out = await ce.list_connector_statuses(current_user=_user(), db_client=db)
    return {item.type: item for item in out.items}


@pytest.fixture
def nothing_configured(monkeypatch):
    for name in GOOGLE + HUBSPOT + ("SALESFORCE_CLIENT_ID", "SALESFORCE_CLIENT_SECRET"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


async def test_cards_without_oauth_credentials_are_unavailable_with_a_safe_reason(nothing_configured):
    cards = await _cards()
    for key in ("calendar", "email", "drive", "crm"):
        assert cards[key].available is False, key
        reason = cards[key].unavailable_reason or ""
        assert reason and "CLIENT" not in reason and "SECRET" not in reason


async def test_google_cards_become_available_when_google_oauth_is_configured(nothing_configured):
    for name in GOOGLE:
        nothing_configured.setenv(name, "x")
    cards = await _cards()
    assert all(cards[key].available and cards[key].unavailable_reason is None
               for key in ("calendar", "email", "drive"))
    assert cards["crm"].available is False, "HubSpot keeps its own setting"


async def test_hubspot_card_follows_hubspot_credentials(nothing_configured):
    for name in HUBSPOT:
        nothing_configured.setenv(name, "x")
    assert (await _cards())["crm"].available is True


async def test_a_blank_setting_does_not_count_as_configured(nothing_configured):
    nothing_configured.setenv("GOOGLE_CLIENT_ID", "x")
    nothing_configured.setenv("GOOGLE_CLIENT_SECRET", "   ")
    assert (await _cards())["calendar"].available is False
