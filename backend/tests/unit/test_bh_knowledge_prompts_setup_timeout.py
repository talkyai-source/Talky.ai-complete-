"""bughunt knowledge-prompts-4: call-setup catalog load must not reuse the
500 ms per-turn timeout."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.services.scripts.knowledge import session_inject

ROW = {"id": "c1", "tenant_id": "t1", "knowledge_mode": "retrieve"}


def _session():
    return SimpleNamespace(system_prompt="P", campaign_id="c1", tenant_id=None, knowledge_mode=None)


class _Catalog:
    nodes = ["n"]
    checksum = "abc"


@pytest.mark.asyncio
async def test_slow_but_healthy_load_is_not_dropped_at_500ms(monkeypatch):
    monkeypatch.setenv("CAMPAIGN_KNOWLEDGE_ENABLED", "true")

    async def slow_load(pool, tenant_id, campaign_id):
        await asyncio.sleep(0.8)  # > the 500 ms per-turn budget
        return _Catalog()

    monkeypatch.setattr(session_inject, "load_section_catalog", slow_load)
    cs = _session()
    await session_inject.apply_campaign_knowledge(cs, ROW, pool=object())
    assert cs._knowledge_catalog is not None


@pytest.mark.asyncio
async def test_one_transient_failure_is_retried(monkeypatch):
    monkeypatch.setenv("CAMPAIGN_KNOWLEDGE_ENABLED", "true")
    calls = []

    async def flaky(pool, tenant_id, campaign_id):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("pool exhausted")
        return _Catalog()

    monkeypatch.setattr(session_inject, "load_section_catalog", flaky)
    cs = _session()
    await session_inject.apply_campaign_knowledge(cs, ROW, pool=object())
    assert len(calls) == 2 and cs._knowledge_catalog is not None


@pytest.mark.asyncio
async def test_persistent_failure_stays_fail_soft_after_one_retry(monkeypatch):
    monkeypatch.setenv("CAMPAIGN_KNOWLEDGE_ENABLED", "true")
    calls = []

    async def always_fail(pool, tenant_id, campaign_id):
        calls.append(1)
        raise RuntimeError("down")

    monkeypatch.setattr(session_inject, "load_section_catalog", always_fail)
    cs = _session()
    await session_inject.apply_campaign_knowledge(cs, ROW, pool=object())
    assert len(calls) == 2 and cs._knowledge_catalog is None


def test_setup_timeout_is_independent_of_the_per_turn_constant():
    from app.domain.services.voice_pipeline import kb_budget
    assert session_inject._KNOWLEDGE_SETUP_TIMEOUT_S > kb_budget._KNOWLEDGE_RETRIEVE_TIMEOUT_S
