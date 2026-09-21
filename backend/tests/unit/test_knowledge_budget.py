"""Unit tests for the model-aware knowledge budget (vectorless RAG P1)."""
from __future__ import annotations

from app.services.scripts.knowledge.budget import (
    INLINE_BAKE_MAX_CHARS,
    choose_mode,
    context_window_for,
    estimate_tokens,
    inline_budget_for,
    max_inline_tokens,
    tokens_for_chars,
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
    small = "llama-3.1-8b-instant"
    small_budget = inline_budget_for(small)
    assert small_budget < inline_budget_for(big)
    # A KB that just overflows the 8B budget still inlines on the 128k model.
    tokens = small_budget + 1
    assert choose_mode(tokens, small) == "map_retrieve"
    assert choose_mode(tokens, big) == "inline"


def test_inlining_is_capped_at_what_the_bake_can_actually_render():
    """The ceiling added 2026-09-22, and why it is not a loosened assertion.

    This file previously asserted that a 128k model would inline a knowledge
    base six times the 8B budget. But both bake paths truncate at
    INLINE_BAKE_MAX_CHARS, and inline is the ONE mode that runs no per-turn
    retrieval -- so marking anything larger "inline" serves the first 12 KB and
    silently drops the rest with nothing left to go looking for it. The budget
    is now tied to what the bake can emit. "A bigger window inlines more" still
    holds (asserted directly above); it is bounded rather than unbounded.
    """
    assert max_inline_tokens() == tokens_for_chars(INLINE_BAKE_MAX_CHARS)
    huge = inline_budget_for("llama-3.1-8b-instant") * 6
    assert choose_mode(huge, "llama-3.3-70b-versatile") == "retrieve"
    # Every big-window model lands on the same ceiling.
    for model in ("llama-3.3-70b-versatile", "gemini-2.5-flash", "gpt-oss-120b"):
        assert inline_budget_for(model) == max_inline_tokens(), model
