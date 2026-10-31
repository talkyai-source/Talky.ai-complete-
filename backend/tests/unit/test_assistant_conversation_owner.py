"""Assistant chat history belongs to the user who wrote it (30 Oct 2026).

The history panel listed, opened and deleted every conversation in the
tenant, so any teammate could read another user's assistant chats. These
tests call the real endpoint functions against an in-memory table.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints import assistant_ws

TENANT = "tenant-A"


class _Query:
    def __init__(self, rows, deleted):
        self.rows, self.deleted = rows, deleted
        self.filters, self.mode, self.one = [], "select", False

    def select(self, *_a, **_k):
        return self

    def delete(self):
        self.mode = "delete"
        return self

    def eq(self, column, value):
        self.filters.append((column, value))
        return self

    def order(self, *_a, **_k):
        return self

    def range(self, *_a):
        return self

    def single(self):
        self.one = True
        return self

    def execute(self):
        hits = [r for r in self.rows if all(str(r.get(c)) == str(v) for c, v in self.filters)]
        if self.mode == "delete":
            self.deleted.extend(r["id"] for r in hits)
            return SimpleNamespace(data=hits, count=len(hits))
        if self.one:
            return SimpleNamespace(data=hits[0] if hits else None, count=len(hits))
        return SimpleNamespace(data=hits, count=len(hits))


class _Db:
    def __init__(self):
        self.deleted = []
        self.rows = [
            {"id": "mine", "tenant_id": TENANT, "user_id": "u1", "title": "my chat"},
            {"id": "teammates", "tenant_id": TENANT, "user_id": "u2", "title": "their chat"},
            {"id": "foreign", "tenant_id": "tenant-B", "user_id": "u9", "title": "other tenant"},
        ]

    def table(self, name):
        assert name == "assistant_conversations"
        return _Query(self.rows, self.deleted)


USER = SimpleNamespace(id="u1", tenant_id=TENANT)


@pytest.mark.asyncio
async def test_history_lists_only_the_users_own_conversations():
    result = await assistant_ws.list_conversations(page=1, page_size=20, current_user=USER, db_client=_Db())

    assert [c["id"] for c in result["conversations"]] == ["mine"]
    assert result["total"] == 1


@pytest.mark.asyncio
async def test_a_teammates_conversation_cannot_be_opened():
    with pytest.raises(HTTPException) as exc:
        await assistant_ws.get_conversation("teammates", current_user=USER, db_client=_Db())

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_the_users_own_conversation_opens():
    result = await assistant_ws.get_conversation("mine", current_user=USER, db_client=_Db())

    assert result["id"] == "mine"


@pytest.mark.asyncio
async def test_a_teammates_conversation_cannot_be_deleted():
    db = _Db()

    await assistant_ws.delete_conversation("teammates", current_user=USER, db_client=db)

    assert db.deleted == []
