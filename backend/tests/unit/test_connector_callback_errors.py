"""D3-3 / D3-6: every OAuth return names its card; a cancelled sign-in cleans up.

Several failure returns (provider error such as a denied consent,
missing_code, connector_not_found, invalid_state, callback_failed, ...) were
built without the provider, so the return URL carried no ``type`` and the
frontend guessed which card the error belonged to. A denied or cancelled
sign-in also left its pending connector row behind.
"""
from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest

from app.api.v1.endpoints import connectors as ce
from tests.unit.test_connectors_card_keys import TENANT, FakeDB, _resp

STATE = {"tenant_id": TENANT, "provider": "google_calendar", "connector_id": "c-1", "user_id": "u-1",
         "redirect_uri": "http://api/callback", "code_verifier": "V"}


@pytest.fixture
def oauth(monkeypatch):
    calls = {"rejected": [], "states": []}

    class StateManager:
        def __init__(self, data):
            self.data = data

        async def validate_state(self, state):
            calls["states"].append(state)
            if isinstance(self.data, Exception):
                raise self.data
            return dict(self.data) if self.data else None

    class Lifecycle:
        def __init__(self, _db, *, actor):
            self.actor = actor

        async def reject_pending(self, tenant_id, connector_id):
            calls["rejected"].append((tenant_id, connector_id))

    def use_state(data):
        monkeypatch.setattr(ce, "get_oauth_state_manager", lambda: StateManager(data))

    monkeypatch.setattr(ce, "ConnectorLifecycle", Lifecycle)
    monkeypatch.setenv("FRONTEND_URL", "https://app.example")
    calls["use_state"] = use_state
    return calls


def _query(response):
    return {key: values[0] for key, values in parse_qs(urlsplit(response.headers["location"]).query).items()}


@pytest.mark.parametrize("error,code,expected", [("access_denied", None, "access_denied"), (None, None, "missing_code")])
async def test_a_denied_or_codeless_return_names_its_card_and_rejects_its_pending_row(oauth, error, code, expected):
    oauth["use_state"](STATE)
    response = await ce.oauth_callback(request=None, state="S", code=code, error=error, db_client=FakeDB())
    query = _query(response)
    assert query["status"] == "error" and query["error"] == expected
    assert query["type"] == "calendar" and query["provider"] == "google_calendar"
    assert oauth["rejected"] == [(TENANT, "c-1")]


async def test_an_unreadable_state_on_a_denied_return_still_answers_without_guessing(oauth):
    oauth["use_state"](None)
    response = await ce.oauth_callback(request=None, state="S", code=None, error="access_denied", db_client=FakeDB())
    query = _query(response)
    assert query["error"] == "access_denied" and "type" not in query
    assert oauth["rejected"] == []


async def test_errors_after_the_state_is_read_carry_the_card_type(oauth):
    oauth["use_state"](STATE)
    db = FakeDB({("connectors", "select"): [_resp(None)]})  # connector row not found
    response = await ce.oauth_callback(request=None, state="S", code="CODE", error=None, db_client=db)
    query = _query(response)
    assert query["error"] == "connector_not_found" and query["type"] == "calendar"


async def test_an_unexpected_failure_after_the_state_is_read_still_names_the_card(oauth, monkeypatch):
    oauth["use_state"](STATE)

    class Boom:
        def table(self, _name):
            raise RuntimeError("synthetic failure")

    response = await ce.oauth_callback(request=None, state="S", code="CODE", error=None, db_client=Boom())
    query = _query(response)
    assert query["error"] == "callback_failed" and query["type"] == "calendar"
