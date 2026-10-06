"""All THREE knowledge delivery paths must render nodes source-first.

WHY THIS EXISTS (2026-07-31)
----------------------------
`voice_answer` is an enricher summary of only the TOP of a knowledge node, but
retrieval (FTS + pg_trgm) can match a fact ANYWHERE in the node. Leading with
`voice_answer` therefore silently drops any fact below the first sentence —
the "KB was bad even on the realtime model" bug.

`render_node_answer()` fixed that by leading with the node's own `content`.
But the fix was only wired into TWO of the three delivery paths:

    compact_tree            (inline bake)      -> fixed
    realtime_bridge         (realtime model)   -> fixed
    turn_streamer inject    (DEFAULT per-turn) -> STILL BROKEN
    knowledge_tool          (tool-call mode)   -> STILL BROKEN

The two that were missed are the ones most campaigns actually use. Concretely,
for a node whose content is:

    "Our base plan is 200 pounds a month. The tender add-on is an extra 75
     pounds per month. Onboarding is free."

...with voice_answer "Our base plan is 200 pounds a month.", a caller asking
about the add-on got a context window that did not contain the 75-pound fact
at all — so the agent either failed to answer or invented a number.

These tests exercise each delivery path with authored text and conflicting
generated text. An unused renderer import is not evidence of source-first use.
"""
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

    from app.domain.models.conversation import Message, MessageRole
    from app.domain.services.voice_pipeline.knowledge_tool import run_knowledge_lookup
    from app.domain.services.voice_pipeline.turn_streamer import _knowledge_block_for_turn
    from app.realtime.bridge import RealtimeBridge

    node = {**_NODE, "id": "synthetic-pricing", "version": 1,
            "content": _NODE["content"] if with_source else "",
            "voice_answer": "The tender add-on costs 999 pounds per month.",
            "summary": "The tender add-on costs 999 pounds per month."}
    query = "tender add-on"
    session = SimpleNamespace(call_id="synthetic-source-test", knowledge_mode="retrieve",
                              _knowledge_snapshot_nodes=[node])
    if path == "inject":
        text = await _knowledge_block_for_turn(session, [Message(role=MessageRole.USER, content=query)])
    elif path == "tool":
        text = await run_knowledge_lookup(session, query)
    else:
        bridge = RealtimeBridge(call_id="synthetic-source-test", realtime_session=SimpleNamespace(),
                                media_gateway=SimpleNamespace(), tenant_id="synthetic",
                                campaign_id="synthetic", knowledge_snapshot_nodes=[node])
        result = await bridge._lookup_knowledge(query)
        return result["text"], result["status"]
    return text, session._knowledge_evidence["status"]


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["inject", "tool", "realtime"])
@pytest.mark.parametrize("with_source", [True, False])
async def test_every_delivery_path_uses_authored_source(path, with_source):
    text, status = await _delivered_source(path, with_source=with_source)
    assert "999 pounds" not in text
    if with_source:
        assert status == "matched"
        assert "75 pounds" in text
        assert "Onboarding is free" in text
    else:
        assert status != "matched"
        assert "75 pounds" not in text


@pytest.mark.asyncio
async def test_price_guard_reaches_the_tool_path():
    from app.services.scripts.prompts.guardrails import KNOWLEDGE_PRICE_GUARD

    text, status = await _delivered_source("tool")
    assert status == "matched"
    assert KNOWLEDGE_PRICE_GUARD in text
