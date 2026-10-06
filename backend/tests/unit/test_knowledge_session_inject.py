"""Setup pins one scoped catalog for every non-none campaign mode."""
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.services.voice_pipeline.knowledge_tool import knowledge_system_addendum
from app.services.scripts.knowledge import session_inject

ROWS = [{"id": "node-a", "source_id": "source-a", "source_version": 2,
         "version": "2026-10-07T00:00:00Z", "parent_id": None, "path": "1", "depth": 1,
         "heading": "Warranty", "content": "The warranty lasts five years."}]


def session(**changes):
    return SimpleNamespace(**{**dict(system_prompt="PERSONA", campaign_id="c1", tenant_id=None,
                                    knowledge_mode=None), **changes})


def snapshot(mode="retrieve"):
    return {"enabled": True, "mode": mode, "tenant_id": "t1", "campaign_id": "c1",
            "checksum": "saved-admission-checksum", "nodes": ROWS}


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["inline", "map_retrieve", "retrieve"])
async def test_actual_loader_pins_all_modes_without_full_dump(monkeypatch, mode):
    from app.core import db_utils
    monkeypatch.setenv("CAMPAIGN_KNOWLEDGE_ENABLED", "true")
    calls = []
    class Connection:
        async def fetch(self, sql, *args):
            assert "n.parent_id" in sql and "s.status = 'ready'" in sql and "AND n.enabled" in sql
            calls.append(args)
            return [{**ROWS[0], "updated_at": ROWS[0]["version"]}]
    @asynccontextmanager
    async def acquire(pool, tenant_id):
        assert tenant_id == "t1" and pool == "synthetic-pool"
        yield Connection()
    monkeypatch.setattr(db_utils, "acquire_with_tenant", acquire)
    config = session()
    await session_inject.apply_campaign_knowledge(config, {"id": "c1", "tenant_id": "t1", "knowledge_mode": mode}, pool="synthetic-pool")
    assert calls == [("c1", "t1")]
    assert config.knowledge_mode == mode and config.system_prompt == "PERSONA"
    assert config._knowledge_catalog.source_policy == "call_snapshot"
    assert "Warranty" in knowledge_system_addendum(config)
    assert "five years" not in knowledge_system_addendum(config)
    call_session = session()
    session_inject.copy_prepared_knowledge(config, call_session)
    assert call_session._knowledge_catalog is config._knowledge_catalog and call_session.tenant_id == "t1"


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["flag_off", "mode_none", "wrong_tenant", "wrong_campaign"])
async def test_disabled_or_cross_scope_setup_does_not_read(monkeypatch, reason):
    monkeypatch.setenv("CAMPAIGN_KNOWLEDGE_ENABLED", "false" if reason == "flag_off" else "true")
    load = AsyncMock(side_effect=AssertionError("must not load"))
    monkeypatch.setattr(session_inject, "load_section_catalog", load)
    cs = session(tenant_id="other" if reason == "wrong_tenant" else None,
                 campaign_id="other" if reason == "wrong_campaign" else "c1")
    await session_inject.apply_campaign_knowledge(cs, {"id": "c1", "tenant_id": "t1",
        "knowledge_mode": "none" if reason == "mode_none" else "retrieve"}, pool=object())
    load.assert_not_awaited()
    assert cs._knowledge_catalog is None and cs.system_prompt == "PERSONA"


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [RuntimeError("unavailable"), TimeoutError()])
async def test_setup_failure_is_explicit_unavailable_without_lexical_fallback(monkeypatch, error):
    monkeypatch.setenv("CAMPAIGN_KNOWLEDGE_ENABLED", "true")
    monkeypatch.setattr(session_inject, "load_section_catalog", AsyncMock(side_effect=error))
    cs = session()
    await session_inject.apply_campaign_knowledge(cs, {"id": "c1", "tenant_id": "t1", "knowledge_mode": "inline"}, pool=object())
    assert cs._knowledge_catalog is None and "unavailable" in knowledge_system_addendum(cs)
    assert cs.system_prompt == "PERSONA"


@pytest.mark.parametrize("mode", ["inline", "map_retrieve", "retrieve"])
def test_pinned_snapshot_remains_original_without_environment_or_database(monkeypatch, mode):
    monkeypatch.delenv("CAMPAIGN_KNOWLEDGE_ENABLED", raising=False)
    cs = session()
    session_inject.apply_pinned_campaign_knowledge(cs, snapshot(mode))
    assert cs._knowledge_catalog.source_policy == "admission_snapshot"
    assert cs._knowledge_snapshot_checksum == "saved-admission-checksum"
    assert cs._knowledge_catalog.nodes[0]["content"] == ROWS[0]["content"]
    assert cs.knowledge_mode == mode and cs.system_prompt == "PERSONA"


@pytest.mark.parametrize("mutation", ["disabled", "tenant", "campaign", "revision"])
def test_pinned_bad_scope_or_proof_is_unavailable(mutation):
    from copy import deepcopy
    saved = deepcopy(snapshot())
    if mutation == "disabled":
        saved["enabled"] = False
    elif mutation in {"tenant", "campaign"}:
        saved[f"{mutation}_id"] = "other"
    else:
        saved["nodes"][0]["source_version"] = None
    cs = session(tenant_id="t1")
    session_inject.apply_pinned_campaign_knowledge(cs, saved)
    assert cs._knowledge_catalog is None
