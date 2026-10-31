"""Pure lexical source-search diagnostic contracts; not the live conversation path."""
import inspect

import pytest

from app.domain.services.voice_pipeline.kb_budget import content_words, knowledge_match_is_weak, needs_previous_turn_context
from app.services.scripts.knowledge import retrieval

NODES = [
    {"heading": "2.2 Dojo Plus", "content": "Dojo Plus is £11.99 per location per month. Advanced plan for growing businesses.",
     "search_text": "2.2 Dojo Plus Dojo Plus is £11.99 per location per month. Advanced plan for growing businesses.", "priority": 0},
    {"heading": "5. Integrations & EPOS Partner Intelligence", "content": "Ask which EPOS the merchant uses; never claim integration from a generic count.",
     "search_text": "5. Integrations & EPOS Partner Intelligence Ask which EPOS the merchant uses; never claim integration from a generic count.", "priority": 0},
    {"heading": "2.3 Seven-Day Settlement Add-on", "content": "Seven-Day Settlement is a separate £10 per month add-on, you pay it on top of the plan.",
     "search_text": "2.3 Seven-Day Settlement Add-on Seven-Day Settlement is a separate £10 per month add-on, you pay it on top of the plan.", "priority": 0},
]


def test_content_words_ignore_filler():
    assert content_words("does Did you work with my EPOs?") == ["work", "epos"]
    assert needs_previous_turn_context("And the price?")
    assert not needs_previous_turn_context("does Did you work with my EPOs?")


def test_weak_is_decided_by_coverage_and_never_removes_knowledge():
    assert knowledge_match_is_weak([{"coverage": 0.2}, {"coverage": 0.3}])
    assert not knowledge_match_is_weak([{"coverage": 0.2}, {"coverage": 0.9}])
    # Unknown coverage cannot grant factual authority; supported retrievers
    # measure relevance, while malformed/legacy hits remain cautious.
    assert knowledge_match_is_weak([{"heading": "x"}])
    assert knowledge_match_is_weak([])


def test_the_sql_labels_coverage_and_ignores_spoken_numbers():
    src = inspect.getsource(retrieval.retrieve_knowledge)
    assert "AS coverage" in src
    assert "NOT (lexeme = ANY($6::text[]))" in src
    assert "list(_COVERAGE_IGNORED_LEXEMES)" in src
    # Ranking is untouched: the same tiered ORDER BY feeds top_k.
    # Ranking (2026-10-01): rare query words in the HEADING count most, then
    # rare words anywhere; the all-words tier is only a small bonus.
    assert "to_tsvector('english', coalesce(c.heading, ''))" in src
    assert "ORDER BY ord" in src
    assert "nineti" in retrieval._COVERAGE_IGNORED_LEXEMES


def test_pinned_snapshot_hits_carry_coverage():
    hits = retrieval.retrieve_pinned_knowledge(NODES, "How much is Dojo Plus?", k=3)
    assert hits and hits[0]["coverage"] == pytest.approx(1.0)
    spoken = retrieval.retrieve_pinned_knowledge(
        NODES, "I pay eleven ninety nine already so I get seven day settlement right", k=3,
    )
    # Spoken numbers are not held against a real question.
    assert max(h["coverage"] for h in spoken) >= 0.5
