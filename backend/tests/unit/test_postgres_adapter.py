"""
Postgres adapter compatibility tests.

These tests validate that our Postgres adapter API shim behaves correctly on
PostgreSQL for the call paths used across the backend.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Callable, List, Tuple

import jwt
import pytest

from app.core import postgres_adapter
from app.core.postgres_adapter import Client, QueryBuilder
from app.api.v1.dependencies import get_db_client


Resolver = Callable[[str, Tuple[Any, ...]], Any]


class FakeConn:
    """Simple asyncpg connection test double."""

    def __init__(self) -> None:
        self.fetch_calls: List[Tuple[str, Tuple[Any, ...]]] = []
        self.fetchval_calls: List[Tuple[str, Tuple[Any, ...]]] = []
        self.fetchrow_calls: List[Tuple[str, Tuple[Any, ...]]] = []
        self.execute_calls: List[Tuple[str, Tuple[Any, ...]]] = []
        self.closed = False
        self._fetch_handlers: List[Tuple[str, Any]] = []
        self._fetchval_handlers: List[Tuple[str, Any]] = []
        self._fetchrow_handlers: List[Tuple[str, Any]] = []
        self._execute_handlers: List[Tuple[str, Any]] = []

    def on_fetch(self, contains: str, value: Any) -> None:
        self._fetch_handlers.append((contains, value))

    def on_fetchval(self, contains: str, value: Any) -> None:
        self._fetchval_handlers.append((contains, value))

    def on_fetchrow(self, contains: str, value: Any) -> None:
        self._fetchrow_handlers.append((contains, value))

    def on_execute(self, contains: str, value: Any) -> None:
        self._execute_handlers.append((contains, value))

    @staticmethod
    def _resolve(
        handlers: List[Tuple[str, Any]],
        sql: str,
        args: Tuple[Any, ...],
        default: Any,
    ) -> Any:
        for needle, value in handlers:
            if needle in sql:
                if callable(value):
                    return value(sql, args)
                return value
        return default

    async def fetch(self, sql: str, *args: Any) -> Any:
        self.fetch_calls.append((sql, args))
        return self._resolve(self._fetch_handlers, sql, args, [])

    async def fetchval(self, sql: str, *args: Any) -> Any:
        self.fetchval_calls.append((sql, args))
        return self._resolve(self._fetchval_handlers, sql, args, None)

    async def fetchrow(self, sql: str, *args: Any) -> Any:
        self.fetchrow_calls.append((sql, args))
        return self._resolve(self._fetchrow_handlers, sql, args, None)

    async def execute(self, sql: str, *args: Any) -> Any:
        self.execute_calls.append((sql, args))
        return self._resolve(self._execute_handlers, sql, args, "UPDATE 1")

    async def close(self) -> None:
        self.closed = True


@pytest.fixture
def connect_queue(monkeypatch):
    """
    Patch asyncpg.connect so each query uses the next queued fake connection.
    """
    queue: List[FakeConn] = []

    async def fake_connect(_dsn: str):
        assert queue, "No fake connection queued for asyncpg.connect"
        return queue.pop(0)

    monkeypatch.setattr(postgres_adapter.asyncpg, "connect", fake_connect)
    return queue


def test_select_supports_exact_count_and_multiple_order_clauses(connect_queue):
    conn = FakeConn()
    conn.on_fetchval("SELECT COUNT(*) FROM campaigns", 2)
    conn.on_fetch(
        "SELECT id, created_at, priority FROM campaigns",
        [
            {"id": "c1", "created_at": "2026-02-19T12:00:00", "priority": 10},
            {"id": "c2", "created_at": "2026-02-19T12:01:00", "priority": 5},
        ],
    )
    connect_queue.append(conn)

    response = (
        QueryBuilder(None, "campaigns")
        .select("id, created_at, priority", count="exact")
        .eq("status", "running")
        .order("priority", desc=True)
        .order("created_at")
        .execute()
    )

    assert response.error is None
    assert response.count == 2
    assert [row["id"] for row in response.data] == ["c1", "c2"]
    assert conn.closed

    sql, args = next(
        (query_sql, query_args)
        for query_sql, query_args in conn.fetch_calls
        if "SELECT id, created_at, priority FROM campaigns" in query_sql
    )
    assert "WHERE status = $1" in sql
    assert "ORDER BY priority DESC, created_at ASC" in sql
    assert args == ("running",)


def test_relational_select_and_relation_filter_work_for_admin_queries(connect_queue):
    conn = FakeConn()
    conn.on_fetch(
        "SELECT * FROM assistant_actions",
        [
            {
                "id": "a1",
                "tenant_id": "t1",
                "lead_id": "l1",
                "status": "failed",
                "created_at": "2026-02-19T12:00:00",
            },
            {
                "id": "a2",
                "tenant_id": "t2",
                "lead_id": "l2",
                "status": "failed",
                "created_at": "2026-02-19T12:01:00",
            },
        ],
    )
    conn.on_fetch(
        "SELECT id, business_name FROM tenants",
        [
            {"id": "t1", "business_name": "Tenant One"},
            {"id": "t2", "business_name": "Tenant Two"},
        ],
    )

    def leads_handler(_sql: str, args: Tuple[Any, ...]):
        # args[0] is id list via ANY($1), args[1] is relation filter pattern
        assert set(args[0]) == {"l1", "l2"}
        assert args[1] == "%123%"
        return [{"id": "l1", "first_name": "Ali", "phone_number": "123456"}]

    conn.on_fetch("SELECT id, first_name, phone_number FROM leads", leads_handler)
    connect_queue.append(conn)

    response = (
        QueryBuilder(None, "assistant_actions")
        .select(
            "*, tenants!inner(business_name), leads(first_name, phone_number)",
            count="exact",
        )
        .ilike("leads.phone_number", "%123%")
        .order("created_at", desc=True)
        .execute()
    )

    assert response.error is None
    assert response.count == 1
    assert len(response.data) == 1

    row = response.data[0]
    assert row["id"] == "a1"
    assert row["tenants"]["business_name"] == "Tenant One"
    assert row["leads"]["phone_number"] == "123456"


def test_upsert_single_payload_returns_list_by_default(connect_queue):
    conn = FakeConn()
    conn.on_fetchrow(
        "INSERT INTO subscriptions",
        {
            "id": "sub_1",
            "stripe_subscription_id": "stripe_sub_1",
            "status": "active",
        },
    )
    connect_queue.append(conn)

    response = (
        QueryBuilder(None, "subscriptions")
        .upsert(
            {
                "stripe_subscription_id": "stripe_sub_1",
                "status": "active",
            },
            on_conflict="stripe_subscription_id",
        )
        .execute()
    )

    assert response.error is None
    assert response.data[0]["id"] == "sub_1"
    assert "ON CONFLICT (stripe_subscription_id)" in conn.fetchrow_calls[0][0]


def test_insert_serializes_dict_payloads_for_jsonb_columns(connect_queue):
    conn = FakeConn()
    conn.on_fetchrow(
        "INSERT INTO call_events",
        {
            "id": "evt_1",
            "call_id": "call_1",
            "event_type": "session_end",
            "source": "voice_orchestrator",
            "event_data": {"session_type": "voice_demo"},
        },
    )
    connect_queue.append(conn)

    response = (
        QueryBuilder(None, "call_events")
        .insert(
            {
                "id": "evt_1",
                "call_id": "call_1",
                "event_type": "session_end",
                "source": "voice_orchestrator",
                "event_data": {"session_type": "voice_demo"},
            }
        )
        .execute()
    )

    assert response.error is None
    assert response.data[0]["id"] == "evt_1"

    _sql, args = conn.fetchrow_calls[0]
    assert isinstance(args[4], str)
    assert json.loads(args[4]) == {"session_type": "voice_demo"}


def test_select_decodes_jsonb_columns_to_native_python_values(connect_queue):
    conn = FakeConn()
    conn.on_fetch(
        "FROM information_schema.columns",
        [
            {"column_name": "id", "udt_name": "uuid"},
            {"column_name": "messages", "udt_name": "jsonb"},
        ],
    )
    conn.on_fetch(
        "SELECT id, messages FROM assistant_conversations",
        [
            {
                "id": "conv_1",
                "messages": '[{"role":"user","content":"hello"}]',
            }
        ],
    )
    connect_queue.append(conn)

    response = (
        QueryBuilder(None, "assistant_conversations")
        .select("id, messages")
        .single()
        .execute()
    )

    assert response.error is None
    assert response.data["id"] == "conv_1"
    assert response.data["messages"] == [{"role": "user", "content": "hello"}]


def test_insert_preserves_list_for_postgres_array_columns(connect_queue):
    conn = FakeConn()
    conn.on_fetch(
        "FROM information_schema.columns",
        [
            {"column_name": "id", "udt_name": "uuid"},
            {"column_name": "tags", "udt_name": "_text"},
        ],
    )
    conn.on_fetchrow(
        "INSERT INTO contacts",
        {
            "id": "contact_1",
            "tags": ["vip", "beta"],
        },
    )
    connect_queue.append(conn)

    response = (
        QueryBuilder(None, "contacts")
        .insert(
            {
                "id": "contact_1",
                "tags": ["vip", "beta"],
            }
        )
        .execute()
    )

    assert response.error is None
    assert response.data[0]["id"] == "contact_1"

    _sql, args = conn.fetchrow_calls[0]
    assert isinstance(args[1], list)
    assert args[1] == ["vip", "beta"]


def test_insert_single_modifier_returns_object(connect_queue):
    conn = FakeConn()
    conn.on_fetchrow(
        "INSERT INTO assistant_conversations",
        {
            "id": "conv_1",
            "title": "Hello",
        },
    )
    connect_queue.append(conn)

    response = (
        QueryBuilder(None, "assistant_conversations")
        .insert({"title": "Hello"})
        .single()
        .execute()
    )

    assert response.error is None
    assert response.data["id"] == "conv_1"


def test_upsert_single_modifier_returns_object(connect_queue):
    conn = FakeConn()
    conn.on_fetchrow(
        "INSERT INTO subscriptions",
        {
            "id": "sub_1",
            "stripe_subscription_id": "stripe_sub_1",
            "status": "active",
        },
    )
    connect_queue.append(conn)

    response = (
        QueryBuilder(None, "subscriptions")
        .upsert(
            {
                "stripe_subscription_id": "stripe_sub_1",
                "status": "active",
            },
            on_conflict="stripe_subscription_id",
        )
        .single()
        .execute()
    )

    assert response.error is None
    assert response.data["id"] == "sub_1"


def test_select_coerces_iso_datetime_filters_for_timestamptz_columns(connect_queue):
    conn = FakeConn()
    conn.on_fetch(
        "FROM information_schema.columns",
        [
            {"column_name": "id", "udt_name": "uuid"},
            {"column_name": "created_at", "udt_name": "timestamptz"},
        ],
    )
    conn.on_fetch("SELECT id, created_at FROM calls", [])
    connect_queue.append(conn)

    response = (
        QueryBuilder(None, "calls")
        .select("id, created_at")
        .gte("created_at", "2026-01-21T00:00:00Z")
        .lt("created_at", "2026-01-22")
        .execute()
    )

    assert response.error is None
    _sql, args = conn.fetch_calls[1]
    assert isinstance(args[0], datetime)
    assert args[0].tzinfo is not None
    assert isinstance(args[1], datetime)
    assert args[1].tzinfo is not None


def test_rpc_increment_campaign_counter_is_supported(connect_queue):
    conn = FakeConn()
    conn.on_fetchrow(
        "UPDATE campaigns",
        {
            "id": "camp_1",
            "calls_completed": 7,
        },
    )
    connect_queue.append(conn)

    response = Client(None).rpc(
        "increment_campaign_counter",
        {"p_campaign_id": "camp_1", "p_counter": "calls_completed"},
    ).execute()

    assert response.error is None
    assert response.data == {"id": "camp_1", "calls_completed": 7}


def test_auth_get_user_uses_local_jwt_secret(monkeypatch):
    long_secret = "test-secret-with-minimum-32-bytes-1234567890"
    monkeypatch.setenv("JWT_SECRET", long_secret)
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")
    token = jwt.encode(
        {"sub": "user-123", "email": "u@example.com"},
        long_secret,
        algorithm="HS256",
    )

    client = Client(None)
    response = client.auth.get_user(token)

    assert response.user is not None
    assert response.user.id == "user-123"
    assert response.user.email == "u@example.com"


def test_storage_upload_download_and_signed_url(tmp_path, monkeypatch):
    monkeypatch.setenv("RECORDINGS_STORAGE_DIR", str(tmp_path))
    client = Client(None)
    bucket = client.storage.from_("recordings")

    bucket.upload("call-1/audio.wav", b"abc123")
    assert bucket.download("call-1/audio.wav") == b"abc123"

    signed = bucket.create_signed_url("call-1/audio.wav", expires_in=60)
    assert signed["signedURL"].endswith("/api/v1/recordings/storage/recordings/call-1/audio.wav")


@pytest.mark.asyncio
async def test_execute_result_is_awaitable_inside_async_context(connect_queue):
    conn = FakeConn()
    conn.on_fetch("SELECT id FROM plans", [{"id": "plan-1"}])
    connect_queue.append(conn)

    result = QueryBuilder(None, "plans").select("id").execute()
    awaited = await result

    assert awaited.error is None
    assert awaited.data == [{"id": "plan-1"}]


def test_get_db_client_falls_back_to_container_pool_outside_fastapi(monkeypatch):
    fake_pool = object()
    monkeypatch.setattr("app.api.v1.dependencies.get_db_pool", lambda: fake_pool)

    client = get_db_client(pool=object())
    assert isinstance(client, Client)
    assert client.pool is fake_pool
