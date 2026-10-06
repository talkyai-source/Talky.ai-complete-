"""Authored source wins over generated derivatives in diagnostics and both live adapters."""
from __future__ import annotations

import pytest

from app.services.scripts.knowledge.retrieval import render_node_answer

_NODE = {
    "heading": "Pricing",
    "voice_answer": "Our base plan is 200 pounds a month.",
    "summary": "Pricing overview.",
    "content": (
        "Our base plan is 200 pounds a month. The tender add-on is an extra "
        "75 pounds per month. Onboarding is free."
    ),
}


def test_a_fact_below_the_first_sentence_survives():
    out = render_node_answer(_NODE, max_chars=400)
    assert "75 pounds" in out, "the add-on price must reach the model"
    assert "Onboarding is free" in out


def test_the_old_precedence_would_have_lost_it():
    """Pins the defect itself, so the regression is unmistakable."""
    old = _NODE.get("voice_answer") or _NODE.get("summary") or _NODE.get("content")
    assert "75 pounds" not in old


def test_no_source_text_cannot_be_replaced_by_unapproved_generated_fact():
    node = {"heading": "X", "voice_answer": "Spoken only.", "content": ""}
    # No source revision or human approval binds this generated wording.
    assert render_node_answer(node, max_chars=200) == ""


def test_truncation_respects_the_budget():
    out = render_node_answer(_NODE, max_chars=40)
    assert len(out) <= 40


async def _delivered_source(path, *, with_source=True):
    from types import SimpleNamespace

    from app.domain.services.voice_pipeline.knowledge_tool import run_knowledge_lookup
    from app.realtime.bridge import RealtimeBridge
    from app.services.scripts.knowledge.sections import build_section_catalog

    node = {**_NODE, "id": "synthetic-pricing", "version": 1,
            "source_id": "handbook", "source_version": 1,
            "content": _NODE["content"] if with_source else "",
            "voice_answer": "The tender add-on costs 999 pounds per month.",
            "summary": "The tender add-on costs 999 pounds per month."}
    catalog = build_section_catalog([node], tenant_id="synthetic", campaign_id="synthetic",
                                    source_policy="call_snapshot")
    arguments = {"section_ids": [catalog.nodes[0]["section_id"]]}
    if path == "tool":
        session = SimpleNamespace(tenant_id="synthetic", campaign_id="synthetic", _knowledge_catalog=catalog)
        text = await run_knowledge_lookup(session, arguments)
        return text, session._knowledge_evidence["status"]
    bridge = RealtimeBridge(call_id="synthetic-source-test", realtime_session=SimpleNamespace(),
                            media_gateway=SimpleNamespace(), tenant_id="synthetic", campaign_id="synthetic")
    bridge._knowledge_catalog = catalog
    result = await bridge._lookup_knowledge(arguments)
    return result["text"], result["status"]


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["tool", "realtime"])
@pytest.mark.parametrize("with_source", [True, False])
async def test_every_delivery_path_uses_authored_source(path, with_source):
    text, status = await _delivered_source(path, with_source=with_source)
    assert "999 pounds" not in text
    if with_source:
        assert status == "available"
        assert text.count("75 pounds") == 1
        assert "Onboarding is free" in text
    else:
        assert status == "unavailable"
        assert "75 pounds" not in text
