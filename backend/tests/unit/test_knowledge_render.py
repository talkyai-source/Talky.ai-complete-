"""Source-first KB render + boundary-safe budgeting (vectorless RAG).

Covers:
  finding #1 — render_node_answer returns the SOURCE fact, not the enricher's
               top-of-node voice_answer summary (the "KB was bad even on the
               realtime model" bug).
  finding #2 — the enricher no longer clips node content to 600 chars.
  finding #3 — compact_tree drops WHOLE trailing nodes on a boundary + logs it;
               it never char-slices a fact mid-line.
"""
from __future__ import annotations

import asyncio
import logging

from app.services.scripts.knowledge import retrieval as retr
from app.services.scripts.knowledge.retrieval import (
    _truncate_on_boundary,
    compact_tree,
    render_node_answer,
)


# ---------------------------------------------------------------------------
# finding #1 — source-first render
# ---------------------------------------------------------------------------

def test_render_prefers_source_content_over_voice_answer():
    # voice_answer summarises only the TOP; the caller's fact lives later in
    # content. The render must surface the SOURCE fact and LEAD with it.
    node = {
        "heading": "Coverage",
        "voice_answer": "We serve many areas.",
        "summary": "coverage",
        "content": "We cover Texas. We also cover Ohio, Florida and Georgia.",
    }
    out = render_node_answer(node)
    assert out.startswith("We cover Texas")   # source LEADS
    assert "Florida" in out                    # the late fact is present


def test_render_appends_voice_answer_as_phrasing_when_novel():
    node = {
        "heading": "Hours",
        "voice_answer": "We're open nine to five, weekdays.",
        "summary": None,
        "content": "Open 09:00-17:00 Mon-Fri.",
    }
    out = render_node_answer(node)
    assert out.startswith("Open 09:00-17:00 Mon-Fri.")   # fact from source first
    assert "nine to five" in out                          # phrasing appended


def test_render_falls_back_to_voice_answer_when_no_source():
    node = {"heading": "H", "voice_answer": "Nine to five.", "summary": None, "content": None}
    assert render_node_answer(node) == "Nine to five."


def test_render_empty_node_is_empty():
    assert render_node_answer({"heading": "H"}) == ""


# ---------------------------------------------------------------------------
# _truncate_on_boundary
# ---------------------------------------------------------------------------

def test_truncate_cuts_on_word_boundary_not_midword():
    text = "The price is five hundred dollars per unit today."
    out = _truncate_on_boundary(text, 25)
    assert len(out) <= 25
    assert text.startswith(out)
    # the character in the source right after the cut is whitespace → the cut
    # fell between words, never mid-word.
    assert text[len(out):len(out) + 1] in ("", " ")


def test_truncate_prefers_line_boundary():
    text = "First line fact.\nSecond line fact.\nThird line fact."
    out = _truncate_on_boundary(text, 30)
    assert out == "First line fact."   # cut on the newline, whole first fact


def test_truncate_noop_when_under_limit():
    assert _truncate_on_boundary("short", 100) == "short"


# ---------------------------------------------------------------------------
# finding #3 — compact_tree drops whole trailing nodes (no char-slice)
# ---------------------------------------------------------------------------

class _FakeConn:
    def __init__(self, rows):
        self._rows = rows

    async def fetch(self, *a, **k):
        return self._rows


class _FakeAcquire:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


def _patch_rows(monkeypatch, rows):
    conn = _FakeConn(rows)
    monkeypatch.setattr(retr, "acquire_with_tenant", lambda pool, tenant: _FakeAcquire(conn))


def _node(depth, heading, content, summary=None, voice_answer=None):
    return {"depth": depth, "heading": heading, "content": content,
            "summary": summary, "voice_answer": voice_answer}


def test_compact_tree_drops_whole_trailing_nodes_with_log(monkeypatch, caplog):
    rows = [
        _node(0, "A", "a" * 300),
        _node(0, "B", "b" * 300),
        _node(0, "C", "c" * 300),
    ]
    _patch_rows(monkeypatch, rows)
    with caplog.at_level(logging.WARNING):
        out = asyncio.run(compact_tree(object(), "t1", "c1", max_chars=350))
    # Only the first WHOLE node fits under 350; B and C are dropped whole —
    # none of their content leaks (no char-slice of node B's 'b's).
    assert "A:" in out
    assert "b" not in out and "c" not in out
    assert "dropped 2 trailing node(s)" in caplog.text


def test_compact_tree_first_oversized_node_cut_on_line_boundary(monkeypatch):
    rows = [_node(0, "H", "Line one fact.\nLine two fact.\nLine three fact.")]
    _patch_rows(monkeypatch, rows)
    out = asyncio.run(compact_tree(object(), "t1", "c1", max_chars=30))
    assert "Line one fact." in out
    # cut on a line boundary — the second line is NOT partially present.
    assert "Line two" not in out


def test_compact_tree_whole_tree_fits_no_drop(monkeypatch, caplog):
    rows = [_node(0, "A", "short a"), _node(1, "B", "short b")]
    _patch_rows(monkeypatch, rows)
    with caplog.at_level(logging.WARNING):
        out = asyncio.run(compact_tree(object(), "t1", "c1", max_chars=12000))
    assert "A: short a" in out
    assert "B: short b" in out
    assert "dropped" not in caplog.text


def test_compact_tree_skeleton_only_uses_summary(monkeypatch):
    rows = [_node(0, "Topic", "big body", summary="one-liner")]
    _patch_rows(monkeypatch, rows)
    out = asyncio.run(compact_tree(object(), "t1", "c1", skeleton_only=True))
    assert out == "- Topic — one-liner"
    assert "big body" not in out


# ---------------------------------------------------------------------------
# finding #2 — enricher no longer clips to 600 chars
# ---------------------------------------------------------------------------

def test_retrieve_knowledge_fails_closed_on_missing_tenant(monkeypatch):
    """SECURITY: retrieve_knowledge is the shared choke point — a None/empty
    tenant must return [] WITHOUT acquiring a connection (acquire_with_tenant
    would treat None as an RLS bypass)."""
    acquired = {"n": 0}

    def _boom_acquire(pool, tenant):   # must NOT be reached
        acquired["n"] += 1
        raise AssertionError("acquire_with_tenant must not run for a missing tenant")

    monkeypatch.setattr(retr, "acquire_with_tenant", _boom_acquire)
    for bad in (None, "", "   "):
        out = asyncio.run(retr.retrieve_knowledge(object(), bad, "c1", "price"))
        assert out == []
    assert acquired["n"] == 0


def test_enricher_content_clip_raised_and_small_nodes_uncorrupted():
    from app.services.scripts.knowledge import enricher
    assert enricher._CONTENT_CLIP > 600          # no longer the lossy 600 cap
    small = "Short section body."
    # a small node's content passes through the clip completely unchanged.
    assert small[:enricher._CONTENT_CLIP] == small
