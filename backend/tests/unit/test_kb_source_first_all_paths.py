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

These tests pin the shared renderer AND assert structurally that no delivery
path has reverted to the old precedence.
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


def test_falls_back_when_the_node_has_no_source_text():
    node = {"heading": "X", "voice_answer": "Spoken only.", "content": ""}
    assert render_node_answer(node, max_chars=200) == "Spoken only."


def test_truncation_respects_the_budget():
    out = render_node_answer(_NODE, max_chars=40)
    assert len(out) <= 40


@pytest.mark.parametrize(
    "module_path",
    [
        "app/domain/services/voice_pipeline/turn_streamer.py",
        "app/domain/services/voice_pipeline/knowledge_tool.py",
        "app/domain/services/voice_pipeline/realtime_bridge.py",
    ],
)
def test_every_delivery_path_uses_the_shared_renderer(module_path):
    """Structural pin.

    The old precedence is a one-liner that is very easy to reintroduce by
    copy-paste, and it fails SILENTLY — the call still works, the agent just
    quietly stops knowing things. So assert no path carries it.
    """
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2] / module_path).read_text(encoding="utf-8")
    assert "render_node_answer" in src, f"{module_path} must use the shared renderer"
    assert 'voice_answer") or h.get("summary")' not in src, (
        f"{module_path} has reverted to the old voice_answer-first precedence, "
        "which silently drops any fact below the first sentence of a node"
    )


def test_price_guard_reaches_the_tool_path():
    """The guard is empirically load-bearing (11/12 invented prices without it,
    0/12 with it) and tool mode targets the very model family it was proven
    against — it must not be inject-path-only."""
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[2]
        / "app/domain/services/voice_pipeline/knowledge_tool.py"
    ).read_text(encoding="utf-8")
    assert "KNOWLEDGE_PRICE_GUARD" in src, (
        "the tool-call KB path returns facts with no price guard; a caller "
        "asking an uncovered price can be quoted an invented number"
    )
