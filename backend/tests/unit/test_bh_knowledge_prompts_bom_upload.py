"""bughunt knowledge-prompts-9: a UTF-8 BOM must not flatten the heading tree."""
from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api.v1.endpoints import campaign_knowledge as knowledge_api

CAMPAIGN_ID = "33333333-3333-3333-3333-333333333333"
BOM_MD = b"\xef\xbb\xbf# Title\n\n## A\ntext"


class _User:
    id = "11111111-1111-1111-1111-111111111111"
    tenant_id = "22222222-2222-2222-2222-222222222222"


class _Stop(Exception):
    pass


@pytest.mark.asyncio
async def test_bom_upload_keeps_title_heading_and_child_path(monkeypatch):
    seen: dict = {}

    @asynccontextmanager
    async def access(**_kwargs):
        yield SimpleNamespace(conn=SimpleNamespace(fetchval=AsyncMock()))

    real_prepare = knowledge_api.prepare_markdown

    def prepare(raw_md):
        seen["parsed_from"] = raw_md
        seen["prepared"] = real_prepare(raw_md)
        return seen["prepared"]

    async def recover(*_a, **_k):
        return 0

    async def create(_pool, **kwargs):
        seen["stored_raw"] = kwargs["raw_md"]
        raise _Stop()

    async def mark_failed(*_a, **_k):
        return False

    monkeypatch.setattr(knowledge_api, "knowledge_enabled", lambda: True)
    monkeypatch.setattr(knowledge_api, "_http_knowledge_access", access)
    monkeypatch.setattr(knowledge_api, "prepare_markdown", prepare)
    monkeypatch.setattr(knowledge_api, "recover_stale_processing_sources", recover)
    monkeypatch.setattr(knowledge_api, "create_processing_source", create)
    monkeypatch.setattr(knowledge_api, "mark_source_failed_durably", mark_failed)

    class Upload:
        filename = "guide.md"

        async def read(self, size=-1):
            return BOM_MD

    with pytest.raises(Exception):
        await knowledge_api.upload_knowledge(
            CAMPAIGN_ID, Upload(), current_user=_User(),
            db_client=SimpleNamespace(pool=object()),
        )

    assert not seen["parsed_from"].startswith("\ufeff")
    assert seen["stored_raw"] == seen["parsed_from"]
    nodes = seen["prepared"].nodes
    assert nodes[0].heading == "Title"
    assert [n.heading for n in nodes] == ["Title", "A"]
    assert nodes[1].depth == 2
