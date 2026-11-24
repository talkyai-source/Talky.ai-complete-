"""The database's own error text never reaches the customer (2026-11-24).

A failed old-style database call returns a result whose ``.error`` is the
driver's text: table, column and constraint names, and for a constraint
violation the values of the offending row. Six places put it into what the
customer sees. Each now answers with a fixed sentence and keeps the detail in
one error-level log record.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, HTTPException

DB_ERROR = (
    'duplicate key value violates unique constraint "leads_phone_key" '
    "DETAIL: Key (phone_number)=(+447700900123) already exists."
)
FORBIDDEN = ("duplicate key", "leads_phone_key", "phone_number", "+447700900123", "7700900123")
TENANT = "aabbccdd-1234-4abc-8def-0123456789ab"


def _assert_clean(text: str) -> None:
    for bad in FORBIDDEN:
        assert bad not in text, f"{bad!r} leaked into: {text!r}"


def _assert_one_error_log_with_detail(caplog) -> None:
    records = [r for r in caplog.records if r.levelno >= logging.ERROR and "leads_phone_key" in r.getMessage()]
    assert len(records) == 1, [r.getMessage() for r in caplog.records]


class _FailingQuery:
    """Any chained filter returns itself; execute() fails with the driver text."""

    def __getattr__(self, name):
        return lambda *a, **k: self

    def execute(self):
        return SimpleNamespace(data=None, error=DB_ERROR, count=None)


class _FailingDB:
    pool = object()

    def table(self, name):
        return _FailingQuery()


# ------------------------------------------------------------------ analytics

@pytest.mark.parametrize("path", ["/best-time", "/retry-effectiveness", "/calls", "/calls/by-campaign"])
async def test_analytics_failure_is_a_fixed_sentence(monkeypatch, caplog, path):
    from app.api.v1.endpoints import analytics
    from datetime import timezone

    monkeypatch.setattr(analytics, "resolve_reporting_zone", lambda *a, **k: timezone.utc)
    app = FastAPI()
    app.include_router(analytics.router, prefix="/api/v1")
    user = SimpleNamespace(id="u", tenant_id=TENANT)
    app.dependency_overrides[analytics.get_current_user] = lambda: user
    app.dependency_overrides[analytics.get_db_client] = lambda: _FailingDB()
    app.dependency_overrides[analytics._require_analytics_read] = lambda: None
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://synthetic.invalid")

    with caplog.at_level(logging.ERROR):
        resp = await client.get(f"/api/v1/analytics{path}")

    assert resp.status_code == 500
    body = resp.text
    _assert_clean(body)
    assert resp.json()["detail"] == "Failed to fetch analytics" or resp.json().get("message") == "Failed to fetch analytics"
    _assert_one_error_log_with_detail(caplog)


# ------------------------------------------------------------------ campaigns

async def test_create_campaign_failure_is_a_fixed_sentence(monkeypatch, caplog):
    from app.api.v1.endpoints import campaigns as ep
    from app.api.v1.schemas.campaigns import CampaignCreateRequest

    class _Table:
        def insert(self, payload):
            return _FailingQuery()

    class _Acquire:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *a):
            return False

    async def _cfg(conn, tenant_id):
        return SimpleNamespace(tts_provider="cartesia", pipeline_mode="cascaded")

    async def _voices(provider, *, pool=None, tenant_id=None):
        return {"voice-1"}

    monkeypatch.setattr("app.api.v1.endpoints.ai_options._fetch_tenant_config", _cfg)
    monkeypatch.setattr(ep, "_valid_voice_ids_for_provider", _voices)
    monkeypatch.setattr(ep, "acquire_with_tenant", lambda pool, tenant: _Acquire())
    monkeypatch.setattr(ep, "_build_validated_script_config", lambda **kw: {"persona_type": kw["persona_type"]})

    db_client = SimpleNamespace(pool=object(), table=lambda name: _Table())
    user = SimpleNamespace(tenant_id=TENANT, id="u1")
    body = CampaignCreateRequest(
        name="Spring offer", system_prompt="", voice_id="voice-1", persona_type="receptionist",
        agent_names=["Sarah"], company_name="Dojo",
    )

    with caplog.at_level(logging.ERROR):
        with pytest.raises(HTTPException) as exc:
            await ep.create_campaign(body, request=SimpleNamespace(), current_user=user,
                                     idempotency_key=None, db_client=db_client)

    assert exc.value.status_code == 500
    assert exc.value.detail == "Failed to create campaign"
    _assert_clean(str(exc.value.detail))
    _assert_one_error_log_with_detail(caplog)


# --------------------------------------------------------- assistant tool

async def test_assistant_create_campaign_failure_is_a_fixed_sentence(monkeypatch, caplog):
    from unittest.mock import AsyncMock
    from app.infrastructure.assistant.tools import campaign_create

    monkeypatch.setattr(
        campaign_create, "_resolve_provider_and_voice",
        AsyncMock(return_value={"provider": "cartesia", "voice_id": "voice-1"}),
    )

    class _DB:
        def table(self, name):
            return _FailingQuery()

    with caplog.at_level(logging.ERROR):
        result = await campaign_create.create_campaign(
            "t1", _DB(),
            name="AI estimation",
            goal="inform prospects about AI estimation and the 20% offer",
            persona_type="customer support",
            company_name="AI flux",
            agent_names="Alex",
            confirm=True,
        )

    assert set(result) == {"error"}, result
    assert result["error"] == "Could not create the campaign."
    _assert_clean(result["error"])
    _assert_one_error_log_with_detail(caplog)
