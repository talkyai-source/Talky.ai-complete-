"""Unit tests for pre-warm campaign-knowledge injection (vectorless RAG P2).

apply_campaign_knowledge() runs at pre-originate warmup. It must:
  - be a strict no-op when the feature flag is off,
  - bake the full tree for inline, the skeleton for map_retrieve,
  - set knowledge_mode (+tenant) for retrieve WITHOUT inlining,
  - never raise (a knowledge hiccup can't break call setup).

compact_tree is monkeypatched so these stay DB-free. A plain SimpleNamespace
stands in for CallSession since the function only touches four attributes.
"""
from __future__ import annotations

import asyncio
import types

from app.services.scripts.knowledge import session_inject


async def _fake_compact_tree(pool, tenant_id, campaign_id, *, skeleton_only=False, max_chars=12000):
    return "SKELETON_TOC" if skeleton_only else "FULL_TREE_BODY"


def _session(**kw):
    base = dict(system_prompt="PERSONA", campaign_id="c1", tenant_id=None, knowledge_mode=None)
    base.update(kw)
    return types.SimpleNamespace(**base)


def _row(mode):
    return {"knowledge_mode": mode, "tenant_id": "t1", "id": "c1"}


def _run(coro):
    return asyncio.run(coro)


def test_flag_off_is_noop(monkeypatch):
    monkeypatch.delenv("CAMPAIGN_KNOWLEDGE_ENABLED", raising=False)
    monkeypatch.setattr(session_inject, "compact_tree", _fake_compact_tree)
    cs = _session()
    _run(session_inject.apply_campaign_knowledge(cs, _row("inline"), pool=object()))
    assert cs.knowledge_mode is None
    assert cs.system_prompt == "PERSONA"


def test_mode_none_is_noop(monkeypatch):
    monkeypatch.setenv("CAMPAIGN_KNOWLEDGE_ENABLED", "true")
    monkeypatch.setattr(session_inject, "compact_tree", _fake_compact_tree)
    cs = _session()
    _run(session_inject.apply_campaign_knowledge(cs, _row("none"), pool=object()))
    assert cs.knowledge_mode is None
    assert cs.system_prompt == "PERSONA"


def test_inline_bakes_full_tree(monkeypatch):
    monkeypatch.setenv("CAMPAIGN_KNOWLEDGE_ENABLED", "true")
    monkeypatch.setattr(session_inject, "compact_tree", _fake_compact_tree)
    cs = _session()
    _run(session_inject.apply_campaign_knowledge(cs, _row("inline"), pool=object()))
    assert cs.knowledge_mode == "inline"
    assert cs.tenant_id == "t1"
    assert cs.system_prompt.startswith("PERSONA")
    assert "FULL_TREE_BODY" in cs.system_prompt


def test_map_retrieve_bakes_skeleton_only(monkeypatch):
    monkeypatch.setenv("CAMPAIGN_KNOWLEDGE_ENABLED", "true")
    monkeypatch.setattr(session_inject, "compact_tree", _fake_compact_tree)
    cs = _session()
    _run(session_inject.apply_campaign_knowledge(cs, _row("map_retrieve"), pool=object()))
    assert cs.knowledge_mode == "map_retrieve"
    assert "SKELETON_TOC" in cs.system_prompt
    assert "FULL_TREE_BODY" not in cs.system_prompt


def test_retrieve_sets_mode_without_inlining(monkeypatch):
    monkeypatch.setenv("CAMPAIGN_KNOWLEDGE_ENABLED", "true")
    monkeypatch.setattr(session_inject, "compact_tree", _fake_compact_tree)
    cs = _session()
    _run(session_inject.apply_campaign_knowledge(cs, _row("retrieve"), pool=object()))
    assert cs.knowledge_mode == "retrieve"
    assert cs.tenant_id == "t1"
    assert cs.system_prompt == "PERSONA"  # large KB: served per-turn, not inlined


def test_no_pool_is_noop(monkeypatch):
    monkeypatch.setenv("CAMPAIGN_KNOWLEDGE_ENABLED", "true")
    monkeypatch.setattr(session_inject, "compact_tree", _fake_compact_tree)
    cs = _session()
    _run(session_inject.apply_campaign_knowledge(cs, _row("inline"), pool=None))
    assert cs.knowledge_mode is None
    assert cs.system_prompt == "PERSONA"


def test_never_raises_when_compact_tree_blows_up(monkeypatch):
    monkeypatch.setenv("CAMPAIGN_KNOWLEDGE_ENABLED", "true")

    async def _boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(session_inject, "compact_tree", _boom)
    cs = _session()
    # must not propagate — call setup continues on the persona prompt
    _run(session_inject.apply_campaign_knowledge(cs, _row("inline"), pool=object()))
    assert cs.system_prompt == "PERSONA"


def test_does_not_clobber_existing_tenant(monkeypatch):
    monkeypatch.setenv("CAMPAIGN_KNOWLEDGE_ENABLED", "true")
    monkeypatch.setattr(session_inject, "compact_tree", _fake_compact_tree)
    cs = _session(tenant_id="already-set")
    _run(session_inject.apply_campaign_knowledge(cs, _row("inline"), pool=object()))
    assert cs.tenant_id == "already-set"
