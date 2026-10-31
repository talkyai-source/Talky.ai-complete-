"""HTTP integration for the shared direction-aware knowledge lease."""

from __future__ import annotations

from contextlib import asynccontextmanager
import inspect
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.api.v1.dependencies import CurrentUser
from app.api.v1.endpoints import campaign_knowledge
from app.domain.services.campaign_knowledge_access import (
    CampaignKnowledgeAccessBusy,
    CampaignKnowledgeAccessDenied,
    CampaignKnowledgeAccessUnavailable,
    CampaignKnowledgeNotFound,
)


CAMPAIGN_ID = "33333333-3333-3333-3333-333333333333"
TENANT_ID = "22222222-2222-2222-2222-222222222222"
USER_ID = "11111111-1111-1111-1111-111111111111"


def _route_dependencies(method: str, path: str) -> set[object]:
    route = next(
        item
        for item in campaign_knowledge.router.routes
        if item.path == path and method in item.methods
    )
    return {dependency.call for dependency in route.dependant.dependencies}


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("PATCH", "/campaigns/{campaign_id}/knowledge/nodes/{node_id}"),
        ("DELETE", "/campaigns/{campaign_id}/knowledge/sources/{source_id}"),
    ],
)
def test_campaign_knowledge_mutations_hold_directional_lease(method, path):
    assert campaign_knowledge._require_knowledge_mutation in _route_dependencies(
        method,
        path,
    )


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/campaigns/{campaign_id}/knowledge"),
        ("POST", "/campaigns/{campaign_id}/knowledge/test"),
    ],
)
def test_campaign_knowledge_reads_hold_directional_lease(method, path):
    assert campaign_knowledge._require_knowledge_read in _route_dependencies(
        method,
        path,
    )


def test_slow_upload_does_not_hold_one_dependency_lease_for_entire_request():
    dependencies = _route_dependencies(
        "POST",
        "/campaigns/{campaign_id}/knowledge",
    )
    assert campaign_knowledge._require_knowledge_mutation not in dependencies


def _user() -> CurrentUser:
    return CurrentUser(
        id=USER_ID,
        email="user@example.test",
        tenant_id=TENANT_ID,
        role="user",
    )


def _domain_lease(conn=object(), *, mutate=True):
    return SimpleNamespace(
        conn=conn,
        tenant_id=TENANT_ID,
        campaign_id=CAMPAIGN_ID,
        actor_user_id=USER_ID,
        direction="outbound",
        required_permission="campaigns:update" if mutate else "campaigns:read",
        mutate=mutate,
    )


@pytest.mark.asyncio
async def test_mutation_dependency_holds_lease_and_invalidates_only_after_commit(
    monkeypatch,
):
    events: list[str] = []

    @asynccontextmanager
    async def access(*_args, **kwargs):
        assert kwargs["tenant_id"] == TENANT_ID
        assert kwargs["campaign_id"] == CAMPAIGN_ID
        assert kwargs["actor_user_id"] == USER_ID
        assert kwargs["mutate"] is True
        events.append("lease_open")
        yield _domain_lease()
        events.append("committed")

    monkeypatch.setattr(campaign_knowledge, "campaign_knowledge_access_lease", access)
    monkeypatch.setattr(
        campaign_knowledge,
        "_invalidate_campaign_cache_fail_soft",
        lambda *_args: events.append("invalidated"),
    )

    dependency = campaign_knowledge._require_knowledge_mutation
    assert inspect.isasyncgenfunction(dependency)
    generator = dependency(CAMPAIGN_ID, _user(), SimpleNamespace(pool=object()))
    request_lease = await anext(generator)
    request_lease.mark_cache_dirty()
    assert events == ["lease_open"]

    with pytest.raises(StopAsyncIteration):
        await anext(generator)
    assert events == ["lease_open", "committed", "invalidated"]


@pytest.mark.asyncio
async def test_http_dependency_preserves_endpoint_http_exception(monkeypatch):
    @asynccontextmanager
    async def access(*_args, **_kwargs):
        yield _domain_lease()

    monkeypatch.setattr(campaign_knowledge, "campaign_knowledge_access_lease", access)
    generator = campaign_knowledge._require_knowledge_mutation(
        CAMPAIGN_ID,
        _user(),
        SimpleNamespace(pool=object()),
    )
    await anext(generator)

    original = HTTPException(status_code=409, detail={"code": "endpoint_conflict"})
    with pytest.raises(HTTPException) as exc:
        await generator.athrow(original)
    assert exc.value is original


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (CampaignKnowledgeNotFound(CAMPAIGN_ID), 404, "campaign_not_found"),
        (
            CampaignKnowledgeAccessDenied("campaigns:update"),
            403,
            "permission_denied",
        ),
        (CampaignKnowledgeAccessBusy(CAMPAIGN_ID), 503, "knowledge_access_busy"),
        (
            CampaignKnowledgeAccessUnavailable(),
            503,
            "authorization_unavailable",
        ),
    ],
)
@pytest.mark.asyncio
async def test_domain_access_failures_map_to_stable_http_contract(
    monkeypatch,
    error,
    status,
    code,
):
    @asynccontextmanager
    async def access(*_args, **_kwargs):
        raise error
        yield  # pragma: no cover

    monkeypatch.setattr(campaign_knowledge, "campaign_knowledge_access_lease", access)

    with pytest.raises(HTTPException) as exc:
        async with campaign_knowledge._http_knowledge_access(
            db_client=SimpleNamespace(pool=object()),
            current_user=_user(),
            campaign_id=CAMPAIGN_ID,
            mutate=True,
        ):
            pass
    assert exc.value.status_code == status
    assert exc.value.detail["code"] == code


def test_mutation_commit_failure_cannot_return_false_success(monkeypatch):
    class _Conn:
        async def fetchval(self, query, *_args):
            if "UPDATE campaign_knowledge_nodes" in query:
                return "node-1"
            raise AssertionError(query)

    @asynccontextmanager
    async def access_then_fail_commit(*_args, **_kwargs):
        yield _domain_lease(_Conn())
        raise CampaignKnowledgeAccessUnavailable()

    monkeypatch.setattr(
        campaign_knowledge,
        "campaign_knowledge_access_lease",
        access_then_fail_commit,
    )
    monkeypatch.setattr(campaign_knowledge, "knowledge_enabled", lambda: True)

    app = FastAPI()
    app.include_router(campaign_knowledge.router)
    app.dependency_overrides[campaign_knowledge.get_current_user] = _user
    app.dependency_overrides[campaign_knowledge.get_db_client] = lambda: SimpleNamespace(
        pool=object()
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.patch(
            f"/campaigns/{CAMPAIGN_ID}/knowledge/nodes/node-1",
            json={"voice_answer": "New answer"},
        )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "authorization_unavailable"
