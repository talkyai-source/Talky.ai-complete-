"""The agent is told when the knowledge base has NOT answered the question.

Call d644f0ea (2026-09-28, Dojo-PC): asked "does Dojo work with my EPOS?" the
agent said "Yes, we've helped set up your EPOS". Two causes, both general:

* the previous caller line was appended to every search, so the question was
  searched as "work epos total twenty one dot ninety nine" and lost the
  section that says to ask which EPOS;
* retrieval always returns up to 3 sections, and a section sharing one common
  word ("pay") counted as an answer, so the model could never tell "found it"
  from "found nothing".

Measured on prod over 2,354 real example questions in 24 campaigns: the
coverage label puts 0.30% of real questions under 0.5 and every off-topic
probe (Klarna, weather, flights, ...) under 0.45.
"""
from __future__ import annotations

import asyncio
import inspect

import pytest

import app.domain.services.voice_pipeline.turn_streamer as ts
from app.domain.models.conversation import Message, MessageRole
from app.domain.services.voice_pipeline.kb_budget import (
    content_words,
    knowledge_match_is_weak,
    needs_previous_turn_context,
)
from app.services.scripts.knowledge import retrieval

NODES = [
    {"heading": "2.2 Dojo Plus", "content": "Dojo Plus is £11.99 per location per month. Advanced plan for growing businesses.",
     "search_text": "2.2 Dojo Plus Dojo Plus is £11.99 per location per month. Advanced plan for growing businesses.", "priority": 0},
    {"heading": "5. Integrations & EPOS Partner Intelligence", "content": "Ask which EPOS the merchant uses; never claim integration from a generic count.",
     "search_text": "5. Integrations & EPOS Partner Intelligence Ask which EPOS the merchant uses; never claim integration from a generic count.", "priority": 0},
    {"heading": "2.3 Seven-Day Settlement Add-on", "content": "Seven-Day Settlement is a separate £10 per month add-on, you pay it on top of the plan.",
     "search_text": "2.3 Seven-Day Settlement Add-on Seven-Day Settlement is a separate £10 per month add-on, you pay it on top of the plan.", "priority": 0},
]


class _Session:
    call_id = "call-kb-relevance"
    tenant_id = "t1"
    campaign_id = "c1"
    knowledge_mode = "retrieve"

    def __init__(self):
        self._knowledge_snapshot_nodes = [dict(n) for n in NODES]


def _block(*user_lines: str) -> str:
    msgs = [Message(role=MessageRole.USER, content=line) for line in user_lines]
    return asyncio.run(ts._knowledge_block_for_turn(_Session(), msgs))


# ── the search query ─────────────────────────────────────────────────────

def test_a_self_contained_question_is_searched_on_its_own(monkeypatch):
    seen: list[str] = []
    real = retrieval.retrieve_pinned_knowledge

    def spy(nodes, query, **kw):
        seen.append(query)
        return real(nodes, query, **kw)

    monkeypatch.setattr(retrieval, "retrieve_pinned_knowledge", spy)
    _block("So what's the total? Twenty one dot ninety nine?", "does Did you work with my EPOs?")
    assert seen == ["does Did you work with my EPOs?"]


def test_a_thin_follow_up_still_borrows_the_previous_line(monkeypatch):
    seen: list[str] = []
    real = retrieval.retrieve_pinned_knowledge
    monkeypatch.setattr(
        retrieval, "retrieve_pinned_knowledge",
        lambda nodes, query, **kw: seen.append(query) or real(nodes, query, **kw),
    )
    _block("Tell me about Dojo Plus.", "And the price?")
    assert seen == ["And the price? Tell me about Dojo Plus."]


def test_content_words_ignore_filler():
    assert content_words("does Did you work with my EPOs?") == ["work", "epos"]
    assert needs_previous_turn_context("And the price?")
    assert not needs_previous_turn_context("does Did you work with my EPOs?")


# ── what the model is told ───────────────────────────────────────────────

def test_an_off_topic_question_is_labelled_unanswered():
    block = _block("Do you do Klarna or buy now pay later?")
    assert "NO CONFIRMED ANSWER" in block


def test_a_question_the_knowledge_answers_is_not_labelled_unanswered():
    block = _block("How much is Dojo Plus?")
    assert "NO CONFIRMED ANSWER" not in block
    assert "£11.99" in block


def test_every_block_forbids_filling_gaps_and_adding_prices_up():
    block = _block("How much is Dojo Plus?")
    assert "never add figures together" in block
    assert "general knowledge" in block


def test_loose_matches_on_an_off_topic_question_are_labelled_unanswered():
    assert "NO CONFIRMED ANSWER" in _block("What's the weather in Dubai?")


def test_no_hit_at_all_still_tells_the_model_not_to_guess():
    block = _block("Zxqv wrplk?")
    assert block == ts.KNOWLEDGE_NO_MATCH_NOTE


def test_weak_is_decided_by_coverage_and_never_removes_knowledge():
    assert knowledge_match_is_weak([{"coverage": 0.2}, {"coverage": 0.3}])
    assert not knowledge_match_is_weak([{"coverage": 0.2}, {"coverage": 0.9}])
    # Nodes from a path that does not label coverage are trusted as before.
    assert not knowledge_match_is_weak([{"heading": "x"}])
    assert not knowledge_match_is_weak([])


# ── the database path labels coverage without changing the ranking ────────

def test_the_sql_labels_coverage_and_ignores_spoken_numbers():
    src = inspect.getsource(retrieval.retrieve_knowledge)
    assert "AS coverage" in src
    assert "NOT (lexeme = ANY($6::text[]))" in src
    assert "list(_COVERAGE_IGNORED_LEXEMES)" in src
    # Ranking is untouched: the same tiered ORDER BY feeds top_k.
    assert "THEN 2" in src and "ORDER BY ord" in src
    assert "nineti" in retrieval._COVERAGE_IGNORED_LEXEMES


def test_pinned_snapshot_hits_carry_coverage():
    hits = retrieval.retrieve_pinned_knowledge(NODES, "How much is Dojo Plus?", k=3)
    assert hits and hits[0]["coverage"] == pytest.approx(1.0)
    spoken = retrieval.retrieve_pinned_knowledge(
        NODES, "I pay eleven ninety nine already so I get seven day settlement right", k=3,
    )
    # Spoken numbers are not held against a real question.
    assert max(h["coverage"] for h in spoken) >= 0.5
