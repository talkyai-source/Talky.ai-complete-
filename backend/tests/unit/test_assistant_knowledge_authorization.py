from __future__ import annotations

import ast
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.domain.services import campaign_knowledge_access as access
from app.infrastructure.assistant import agent, streaming
from app.infrastructure.assistant.tools import ALL_TOOLS
from app.infrastructure.assistant.tools import campaign_admin
from app.infrastructure.assistant.tools.dispatch import dispatch_tool
from app.services.scripts.knowledge import retrieval


TENANT_ID = "10000000-0000-0000-0000-000000000001"
CAMPAIGN_ID = "2aaaaaaa-0000-0000-0000-000000000002"
NODE_ID = "30000000-0000-0000-0000-000000000003"
ACTOR_ID = "40000000-0000-0000-0000-000000000004"
CAMPAIGNS_READ = "campaigns:read"
CAMPAIGNS_UPDATE = "campaigns:update"
INBOUND_READ = "inbound:read"
INBOUND_MANAGE = "inbound:manage"


class _Conn:
    def __init__(
        self,
        *,
        direction: str = "outbound",
        permissions: set[str] | None = None,
        lock_results: list[bool] | None = None,
    ) -> None:
        self.direction = direction
        self.permissions = set(permissions or set())
        self.lock_results = deque(lock_results or [True])
        self.lock_sql: list[str] = []
        self.lock_args: list[tuple[object, ...]] = []
        self.updated: list[tuple[str, tuple[object, ...]]] = []
        self.node = {
            "id": NODE_ID,
            "depth": 0,
            "heading": "Pricing",
            "content": "Old content",
            "keywords": [],
            "example_questions": [],
            "enabled": True,
            "priority": 0,
            "summary": "Old summary",
            "voice_answer": "Old answer",
            "hit_count": 0,
        }

    async def fetchval(self, sql: str, *args):
        if "pg_try_advisory_xact_lock" in sql:
            self.lock_sql.append(sql)
            self.lock_args.append(args)
            return self.lock_results.popleft() if self.lock_results else True
        if "SELECT direction" in sql:
            assert args == (CAMPAIGN_ID, TENANT_ID)
            return self.direction
        if "UPDATE campaign_knowledge_nodes" in sql:
            self.updated.append((sql, args))
            return NODE_ID
        raise AssertionError(f"unexpected fetchval: {sql}")

    async def fetch(self, sql: str, *args):
        if "FROM tenant_users" in sql:
            assert args == (ACTOR_ID, TENANT_ID)
            return [{"name": permission} for permission in self.permissions]
        if "FROM user_permissions" in sql:
            assert args == (ACTOR_ID, TENANT_ID)
            return []
        if "FROM campaign_knowledge_nodes" in sql:
            assert args == (CAMPAIGN_ID, TENANT_ID)
            return [self.node]
        raise AssertionError(f"unexpected fetch: {sql}")

    async def fetchrow(self, sql: str, *args):
        if "FROM campaign_knowledge_nodes" in sql:
            assert args == (NODE_ID, CAMPAIGN_ID, TENANT_ID)
            return self.node
        raise AssertionError(f"unexpected fetchrow: {sql}")


class _Pool:
    def __init__(self, conn: _Conn) -> None:
        self.conn = conn
        self.acquisitions = 0


@pytest.fixture(autouse=True)
def _single_connection_pool(monkeypatch):
    @asynccontextmanager
    async def fake_acquire(pool, tenant_id, *, user_id=None, request_id=None, timeout=None):
        assert tenant_id == TENANT_ID
        assert user_id == ACTOR_ID
        assert timeout is not None
        pool.acquisitions += 1
        yield pool.conn

    monkeypatch.setattr(access, "acquire_with_tenant", fake_acquire)


@pytest.mark.asyncio
async def test_read_lease_uses_shared_campaign_lock_and_current_read_permission():
    conn = _Conn(permissions={CAMPAIGNS_READ})
    pool = _Pool(conn)

    async with access.campaign_knowledge_access_lease(
        pool,
        tenant_id=TENANT_ID,
        campaign_id=CAMPAIGN_ID,
        actor_user_id=ACTOR_ID,
        mutate=False,
    ) as lease:
        assert lease.conn is conn
        assert lease.direction == "outbound"
        assert lease.required_permission == CAMPAIGNS_READ

    assert pool.acquisitions == 1
    assert len(conn.lock_sql) == 1
    assert "pg_try_advisory_xact_lock_shared" in conn.lock_sql[0]
    assert access.CAMPAIGN_DIRECTION_LOCK_KEY_SQL in conn.lock_sql[0]


@pytest.mark.asyncio
async def test_lease_canonicalizes_campaign_uuid_before_lock_and_lookup():
    conn = _Conn(permissions={CAMPAIGNS_READ})
    async with access.campaign_knowledge_access_lease(
        _Pool(conn),
        tenant_id=TENANT_ID,
        campaign_id=CAMPAIGN_ID.upper(),
        actor_user_id=ACTOR_ID,
        mutate=False,
    ) as lease:
        assert lease.campaign_id == CAMPAIGN_ID
    assert conn.lock_args == [(CAMPAIGN_ID,)]


@pytest.mark.asyncio
async def test_malformed_campaign_id_is_typed_not_found_without_db_access():
    pool = _Pool(_Conn(permissions={CAMPAIGNS_READ}))
    with pytest.raises(access.CampaignKnowledgeNotFound):
        async with access.campaign_knowledge_access_lease(
            pool,
            tenant_id=TENANT_ID,
            campaign_id="not-a-uuid",
            actor_user_id=ACTOR_ID,
            mutate=False,
        ):
            pass
    assert pool.acquisitions == 0


@pytest.mark.asyncio
async def test_mutation_lease_uses_exclusive_lock_and_outbound_update_permission():
    conn = _Conn(permissions={CAMPAIGNS_UPDATE})
    pool = _Pool(conn)

    async with access.campaign_knowledge_access_lease(
        pool,
        tenant_id=TENANT_ID,
        campaign_id=CAMPAIGN_ID,
        actor_user_id=ACTOR_ID,
        mutate=True,
    ) as lease:
        assert lease.required_permission == CAMPAIGNS_UPDATE

    assert "pg_try_advisory_xact_lock(" in conn.lock_sql[0]
    assert "_shared" not in conn.lock_sql[0]


@pytest.mark.asyncio
async def test_inbound_mutation_requires_inbound_manage_not_campaign_update():
    conn = _Conn(
        direction="inbound",
        permissions={CAMPAIGNS_UPDATE},
    )

    with pytest.raises(access.CampaignKnowledgeAccessDenied) as denied:
        async with access.campaign_knowledge_access_lease(
            _Pool(conn),
            tenant_id=TENANT_ID,
            campaign_id=CAMPAIGN_ID,
            actor_user_id=ACTOR_ID,
            mutate=True,
        ):
            pass

    assert denied.value.required_permission == INBOUND_MANAGE

    conn.permissions = {INBOUND_MANAGE}
    async with access.campaign_knowledge_access_lease(
        _Pool(conn),
        tenant_id=TENANT_ID,
        campaign_id=CAMPAIGN_ID,
        actor_user_id=ACTOR_ID,
        mutate=True,
    ) as lease:
        assert lease.required_permission == INBOUND_MANAGE


@pytest.mark.asyncio
async def test_inbound_read_requires_inbound_read():
    conn = _Conn(direction="inbound", permissions={CAMPAIGNS_READ})
    with pytest.raises(access.CampaignKnowledgeAccessDenied) as denied:
        async with access.campaign_knowledge_access_lease(
            _Pool(conn),
            tenant_id=TENANT_ID,
            campaign_id=CAMPAIGN_ID,
            actor_user_id=ACTOR_ID,
            mutate=False,
        ):
            pass
    assert denied.value.required_permission == INBOUND_READ


@pytest.mark.asyncio
@pytest.mark.parametrize("mutate", [False, True])
async def test_undeclared_inbound_admin_name_never_grants_access(mutate):
    conn = _Conn(direction="inbound", permissions={"inbound:admin"})
    with pytest.raises(access.CampaignKnowledgeAccessDenied):
        async with access.campaign_knowledge_access_lease(
            _Pool(conn),
            tenant_id=TENANT_ID,
            campaign_id=CAMPAIGN_ID,
            actor_user_id=ACTOR_ID,
            mutate=mutate,
        ):
            pass


@pytest.mark.asyncio
async def test_busy_campaign_lock_is_bounded_and_typed():
    conn = _Conn(
        permissions={CAMPAIGNS_READ},
        lock_results=[False],
    )
    with pytest.raises(access.CampaignKnowledgeAccessBusy):
        async with access.campaign_knowledge_access_lease(
            _Pool(conn),
            tenant_id=TENANT_ID,
            campaign_id=CAMPAIGN_ID,
            actor_user_id=ACTOR_ID,
            mutate=False,
            wait_timeout_seconds=0,
        ):
            pass
    assert len(conn.lock_sql) == 1


@pytest.mark.asyncio
async def test_lease_does_not_reclassify_caller_body_exception():
    class EndpointNotFound(Exception):
        pass

    conn = _Conn(permissions={CAMPAIGNS_READ})
    with pytest.raises(EndpointNotFound):
        async with access.campaign_knowledge_access_lease(
            _Pool(conn),
            tenant_id=TENANT_ID,
            campaign_id=CAMPAIGN_ID,
            actor_user_id=ACTOR_ID,
            mutate=False,
        ):
            raise EndpointNotFound("knowledge node not found")


@pytest.mark.asyncio
async def test_lease_maps_authorization_setup_failure_to_typed_unavailable(monkeypatch):
    @asynccontextmanager
    async def broken_acquire(*_args, **_kwargs):
        raise RuntimeError("database detail must not escape")
        yield  # pragma: no cover

    monkeypatch.setattr(access, "acquire_with_tenant", broken_acquire)
    with pytest.raises(access.CampaignKnowledgeAccessUnavailable) as unavailable:
        async with access.campaign_knowledge_access_lease(
            object(),
            tenant_id=TENANT_ID,
            campaign_id=CAMPAIGN_ID,
            actor_user_id=ACTOR_ID,
            mutate=False,
        ):
            pass
    assert "database detail" not in str(unavailable.value)


async def _update(conn: _Conn, *, confirm: bool):
    return await campaign_admin.update_knowledge_node(
        TENANT_ID,
        SimpleNamespace(pool=_Pool(conn)),
        campaign_id=CAMPAIGN_ID,
        node_id=NODE_ID,
        changes={"summary": "New summary"},
        confirm=confirm,
        actor_user_id=ACTOR_ID,
    )


@pytest.mark.asyncio
async def test_readonly_actor_cannot_apply_knowledge_update():
    conn = _Conn(permissions={CAMPAIGNS_READ})
    result = await _update(conn, confirm=True)
    assert result["error"] == "permission_denied"
    assert result["required"] == CAMPAIGNS_UPDATE
    assert conn.updated == []


@pytest.mark.asyncio
async def test_outbound_update_and_inbound_manage_can_apply():
    outbound = _Conn(permissions={CAMPAIGNS_UPDATE})
    inbound = _Conn(
        direction="inbound",
        permissions={INBOUND_MANAGE},
    )

    assert (await _update(outbound, confirm=True))["applied"] is True
    assert (await _update(inbound, confirm=True))["applied"] is True
    assert len(outbound.updated) == len(inbound.updated) == 1


@pytest.mark.asyncio
async def test_apply_reauthorizes_after_permission_revocation():
    conn = _Conn(permissions={CAMPAIGNS_UPDATE})
    preview = await _update(conn, confirm=False)
    assert preview["preview"] is True

    conn.permissions.clear()
    applied = await _update(conn, confirm=True)
    assert applied["error"] == "permission_denied"
    assert conn.updated == []


@pytest.mark.asyncio
async def test_apply_reauthorizes_after_campaign_converts_to_inbound():
    conn = _Conn(permissions={CAMPAIGNS_UPDATE})
    preview = await _update(conn, confirm=False)
    assert preview["preview"] is True

    conn.direction = "inbound"
    applied = await _update(conn, confirm=True)
    assert applied["error"] == "permission_denied"
    assert applied["required"] == INBOUND_MANAGE
    assert conn.updated == []


@pytest.mark.asyncio
async def test_knowledge_tree_read_is_authorized_and_uses_lease_connection():
    conn = _Conn(permissions={CAMPAIGNS_READ})
    result = await campaign_admin.get_knowledge_tree(
        TENANT_ID,
        SimpleNamespace(pool=_Pool(conn)),
        campaign_id=CAMPAIGN_ID,
        actor_user_id=ACTOR_ID,
    )
    assert result["nodes"][0]["heading"] == "Pricing"

    conn.permissions = {CAMPAIGNS_UPDATE}
    denied = await campaign_admin.get_knowledge_tree(
        TENANT_ID,
        SimpleNamespace(pool=_Pool(conn)),
        campaign_id=CAMPAIGN_ID,
        actor_user_id=ACTOR_ID,
    )
    assert denied["error"] == "permission_denied"


@pytest.mark.asyncio
async def test_retriever_reuses_authorized_lease_connection(monkeypatch):
    conn = _Conn(permissions={CAMPAIGNS_READ})
    seen = {}

    async def fake_retrieve(pool, tenant_id, campaign_id, query, **kwargs):
        seen.update(pool=pool, tenant_id=tenant_id, campaign_id=campaign_id, conn=kwargs.get("conn"))
        return [{"heading": "Pricing", "voice_answer": "Ten", "summary": "Price"}]

    monkeypatch.setattr(campaign_admin, "retrieve_knowledge_fn", fake_retrieve)
    result = await campaign_admin.retrieve_knowledge(
        TENANT_ID,
        SimpleNamespace(pool=_Pool(conn)),
        campaign_id=CAMPAIGN_ID,
        query="price",
        actor_user_id=ACTOR_ID,
    )
    assert result["hits"][0]["heading"] == "Pricing"
    assert seen["conn"] is conn


@pytest.mark.asyncio
async def test_supplied_retrieval_connection_never_acquires_pool():
    class RetrievalConn:
        async def fetch(self, sql, *args):
            assert "n.tenant_id = $5" in sql
            assert args == (CAMPAIGN_ID, "price", 3, retrieval._WORD_SIM_FLOOR, TENANT_ID)
            return []

    class NoAcquirePool:
        def acquire(self, **_kwargs):
            raise AssertionError("supplied connection must bypass pool acquisition")

    result = await retrieval.retrieve_knowledge(
        NoAcquirePool(),
        TENANT_ID,
        CAMPAIGN_ID,
        "price",
        k=3,
        conn=RetrievalConn(),
        raise_on_error=True,
    )
    assert result == []


@pytest.mark.asyncio
async def test_diagnostic_retrieval_can_surface_db_failure():
    class BrokenConn:
        async def fetch(self, _sql, *_args):
            raise RuntimeError("database unavailable")

    with pytest.raises(RuntimeError, match="database unavailable"):
        await retrieval.retrieve_knowledge(
            object(),
            TENANT_ID,
            CAMPAIGN_ID,
            "price",
            conn=BrokenConn(),
            raise_on_error=True,
        )


@pytest.mark.asyncio
async def test_dispatch_overrides_model_supplied_actor_with_authenticated_actor(monkeypatch):
    captured = {}

    async def fake_tool(_tenant_id, _db_client, *, actor_user_id=None, **kwargs):
        captured.update(actor_user_id=actor_user_id, kwargs=kwargs)
        return {"nodes": []}

    monkeypatch.setitem(ALL_TOOLS["get_knowledge_tree"], "function", fake_tool)
    result = await dispatch_tool(
        "get_knowledge_tree",
        TENANT_ID,
        object(),
        None,
        {"campaign_id": CAMPAIGN_ID, "actor_user_id": "model-spoof"},
        actor_user_id=ACTOR_ID,
    )
    assert result == {"nodes": []}
    assert captured == {
        "actor_user_id": ACTOR_ID,
        "kwargs": {"campaign_id": CAMPAIGN_ID},
    }


@pytest.mark.asyncio
async def test_graph_tool_executor_threads_authenticated_actor(monkeypatch):
    captured = {}

    async def fake_dispatch(*args, actor_user_id=None):
        captured["actor_user_id"] = actor_user_id
        return {"nodes": []}

    monkeypatch.setattr(agent, "dispatch_tool", fake_dispatch)
    await agent.tool_executor(
        {
            "messages": [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "function": {
                                "name": "get_knowledge_tree",
                                "arguments": '{"campaign_id":"%s"}' % CAMPAIGN_ID,
                            },
                        }
                    ],
                }
            ],
            "tenant_id": TENANT_ID,
            "user_id": ACTOR_ID,
            "conversation_id": None,
            "db_client": object(),
            "tool_results": [],
            "model": None,
        }
    )
    assert captured["actor_user_id"] == ACTOR_ID


def test_text_and_voice_apply_dispatches_carry_authenticated_actor():
    root = Path(__file__).resolve().parents[2] / "app" / "api" / "v1" / "endpoints"
    for filename in ("assistant_ws.py", "assistant_voice_ws.py"):
        tree = ast.parse((root / filename).read_text(encoding="utf-8"))
        dispatches = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "dispatch_tool"
        ]
        assert dispatches, filename
        assert all(
            any(keyword.arg == "actor_user_id" for keyword in node.keywords)
            for node in dispatches
        ), filename
        assert all(
            any(
                keyword.arg == "trusted_proposal_apply"
                and isinstance(keyword.value, ast.Constant)
                and keyword.value.value is True
                for keyword in node.keywords
            )
            for node in dispatches
        ), filename


@pytest.mark.asyncio
async def test_streaming_dispatch_threads_authenticated_actor(monkeypatch):
    turn1 = [
        SimpleNamespace(
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(
                        content=None,
                        tool_calls=[
                            SimpleNamespace(
                                index=0,
                                id="call-1",
                                function=SimpleNamespace(
                                    name="get_knowledge_tree",
                                    arguments='{"campaign_id":"%s"}' % CAMPAIGN_ID,
                                ),
                            )
                        ],
                    )
                )
            ]
        )
    ]
    turn2 = [
        SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(content="Done", tool_calls=None))]
        )
    ]

    class Completions:
        def __init__(self):
            self.turns = iter((turn1, turn2))

        async def create(self, **_kwargs):
            chunks = next(self.turns)

            async def gen():
                for chunk in chunks:
                    yield chunk

            return gen()

    client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    captured = {}

    async def fake_dispatch(*args, actor_user_id=None):
        captured["actor_user_id"] = actor_user_id
        return {"nodes": []}

    monkeypatch.setattr(streaming, "get_assistant_client", lambda _model: (client, lambda value: value))
    monkeypatch.setattr(streaming, "dispatch_tool", fake_dispatch)
    events = []
    async for event in streaming.stream_assistant_reply(
        chat_messages=[{"role": "user", "content": "show knowledge"}],
        tenant_id=TENANT_ID,
        user_id=ACTOR_ID,
        conversation_id=None,
        db_client=object(),
        model=None,
    ):
        events.append(event)
    assert events[-1]["type"] == "final"
    assert captured["actor_user_id"] == ACTOR_ID
