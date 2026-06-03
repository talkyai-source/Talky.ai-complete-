"""Unit tests for the model-aware knowledge budget (vectorless RAG P1)."""
from __future__ import annotations

from app.services.scripts.knowledge.budget import (
    choose_mode,
    context_window_for,
    estimate_tokens,
    inline_budget_for,
)


def test_estimate_tokens():
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("a" * 400) == 100


def test_context_window_known_and_default():
    assert context_window_for("llama-3.1-8b-instant") == 8192
    assert context_window_for("llama-3.3-70b-versatile") == 131072
    assert context_window_for(None) == 8192
    assert context_window_for("some-unknown-model") == 8192  # safe default


def test_inline_budget_scales_with_model():
    small = inline_budget_for("llama-3.1-8b-instant")
    big = inline_budget_for("llama-3.3-70b-versatile")
    assert 0 < small < big
    # 8B leaves a modest inline budget (a few thousand tokens), not the whole window
    assert 1000 < small < 4000


def test_choose_mode_thresholds_for_8b():
    m = "llama-3.1-8b-instant"
    budget = inline_budget_for(m)
    assert choose_mode(0, m) == "none"
    assert choose_mode(budget // 2, m) == "inline"
    assert choose_mode(budget * 3, m) == "map_retrieve"
    assert choose_mode(budget * 50, m) == "retrieve"


def test_big_model_inlines_more():
    big = "llama-3.3-70b-versatile"
    # a KB that is "retrieve" on the 8B can be "inline" on a 128k model
    tokens = inline_budget_for("llama-3.1-8b-instant") * 6
    assert choose_mode(tokens, "llama-3.1-8b-instant") == "retrieve"
    assert choose_mode(tokens, big) == "inline"
