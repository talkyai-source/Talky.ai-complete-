"""Unit tests for the per-turn knowledge-injection budget (latency fix).

Injecting full bodies of k=5 nodes ballooned the prompt to ~12k tokens and
stalled the LLM. These guard the trimming + total budget that keep it small.
"""
import asyncio
from unittest.mock import patch

import app.domain.services.voice_pipeline.turn_streamer as ts


def test_trim_kb_body_caps_on_word_boundary():
    out = ts._trim_kb_body("one two three four five six seven", 18)
    assert len(out) <= 19          # cap + ellipsis
    assert out.endswith("…")
    assert " fo" not in out[-3:]    # no cut-off mid-word at the tail


def test_trim_kb_body_short_text_unchanged():
    assert ts._trim_kb_body("short answer", 100) == "short answer"
    assert ts._trim_kb_body("  has\nnewlines ", 100) == "has newlines"


def test_trim_kb_body_empty():
    assert ts._trim_kb_body("", 100) == ""
    assert ts._trim_kb_body(None, 100) == ""  # type: ignore[arg-type]


class _FakeSession:
    call_id = "call-xyz-1234"
    tenant_id = "t1"
    campaign_id = "c1"
    knowledge_mode = "retrieve"


def test_knowledge_block_respects_total_budget(monkeypatch):
    """Five huge nodes must be trimmed + budgeted to a small block, not dumped."""
    from app.domain.models.conversation import Message, MessageRole

    big = "word " * 4000  # ~20k chars each — simulates a full feature-list node
    hits = [
        {"heading": f"Node {i}", "voice_answer": None, "summary": None, "content": big}
        for i in range(5)
    ]

    async def fake_retrieve(*a, **k):
        return hits

    # Container/pool plumbing the function checks before retrieving.
    class _Pool: ...
    class _DB: pool = _Pool()
    class _Container:
        is_initialized = True
        db_client = _DB()

    # The function imports these fresh inside, so patch them at their source.
    monkeypatch.setattr("app.core.container.get_container", lambda: _Container())
    monkeypatch.setattr(
        "app.services.scripts.knowledge.retrieval.retrieve_knowledge", fake_retrieve
    )

    msgs = [Message(role=MessageRole.USER, content="tell me about your product")]
    block = asyncio.run(ts._knowledge_block_for_turn(_FakeSession(), msgs))

    # The whole block must stay near the budget, NOT ~100k chars of raw dump.
    assert len(block) <= ts._KB_TOTAL_CHARS + 600   # header + a little slack
    assert "Company knowledge" in block
    # Each injected fact is trimmed (ellipsis present on the huge bodies).
    assert "…" in block
