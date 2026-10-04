"""The event list stamps its authenticated recipient, not event actors or input."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.dependencies import CurrentUser, get_current_user, get_db_client
from app.api.v1.endpoints import stream_events


@pytest.mark.parametrize(
    "tenant_id,user_id",
    [("tenant-a", "user-a"), ("tenant-b", "user-a"), ("tenant-a", "user-b")],
)
def test_response_owner_is_authenticated_recipient(monkeypatch, tenant_id, user_id):
    conn = SimpleNamespace(
        fetch=AsyncMock(
            return_value=[
                {
                    "id": "event-1",
                    "category": "alert",
                    "title": "Synthetic lead",
                    "description": None,
                    "severity": "info",
                    "related_campaign_id": None,
                    "related_call_id": None,
                    "actor_user_id": "different-actor",
                    "metadata": None,
                    "created_at": datetime.now(UTC),
                }
            ]
        )
    )
    scopes = []

    @asynccontextmanager
    async def acquire(pool, scope):
        scopes.append(scope)
        yield conn

    monkeypatch.setattr(stream_events, "acquire_with_tenant", acquire)
    app = FastAPI()
    app.include_router(stream_events.router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        id=user_id, tenant_id=tenant_id, email="synthetic@example.invalid"
    )
    app.dependency_overrides[get_db_client] = lambda: SimpleNamespace(pool=object())
    with TestClient(app) as client:
        response = client.get("/api/v1/events?tenant_id=untrusted&user_id=untrusted")
    assert response.status_code == 200
    body = response.json()
    assert body["tenant_id"] == tenant_id
    assert body["user_id"] == user_id
    assert body["items"][0]["actor_user_id"] == "different-actor"
    assert scopes == [tenant_id]
    assert conn.fetch.await_args.args[1] == tenant_id


def test_missing_authenticated_tenant_does_not_return_an_owner_stamp():
    app = FastAPI()
    app.include_router(stream_events.router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        id="user-a", email="synthetic@example.invalid"
    )
    app.dependency_overrides[get_db_client] = lambda: SimpleNamespace(pool=None)
    with TestClient(app) as client:
        response = client.get("/api/v1/events?tenant_id=untrusted")
    assert response.status_code == 400
    assert "items" not in response.json()
