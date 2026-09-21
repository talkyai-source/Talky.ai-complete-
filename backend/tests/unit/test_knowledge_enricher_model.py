"""The knowledge enricher must point at a model that exists.

It was hardcoded to llama-3.1-8b-instant. That id began returning 404 on this
account around 2026-08-17 and the voice path moved off it the same day, but the
enricher was missed. Enrichment is fail-soft, so every upload from then on
logged a warning per batch and published its nodes bare: no summary, no spoken
answer, no keywords, no example questions. Seen again live on 2026-09-22:

    knowledge enrich batch [0:20] failed (Error code: 404 - The model
    `llama-3.1-8b-instant` does not exist or you do not have access to it)
    — leaving those nodes unenriched

Reading the canonical menu instead means a retired model cannot leave this
pointing at something that no longer exists.
"""
from __future__ import annotations

import importlib

import pytest

from app.domain.models.ai_config import GROQ_MODELS
from app.services.scripts.knowledge import enricher


def test_the_default_comes_from_the_canonical_groq_menu():
    assert enricher._default_enrich_model() == GROQ_MODELS[0].id


def test_the_retired_model_is_gone():
    assert enricher._ENRICH_MODEL != "llama-3.1-8b-instant"
    assert "llama" not in enricher._ENRICH_MODEL.lower()


def test_the_model_in_use_is_one_the_product_actually_offers():
    offered = {m.id for m in GROQ_MODELS}
    assert enricher._ENRICH_MODEL in offered


def test_an_env_override_still_wins(monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_ENRICH_MODEL", "some/other-model")
    reloaded = importlib.reload(enricher)
    try:
        assert reloaded._ENRICH_MODEL == "some/other-model"
    finally:
        monkeypatch.delenv("KNOWLEDGE_ENRICH_MODEL", raising=False)
        importlib.reload(enricher)


@pytest.mark.parametrize("blank", ["", "   "])
def test_a_blank_override_falls_back_rather_than_sending_an_empty_model(
    monkeypatch, blank
):
    monkeypatch.setenv("KNOWLEDGE_ENRICH_MODEL", blank)
    reloaded = importlib.reload(enricher)
    try:
        assert reloaded._ENRICH_MODEL == reloaded._default_enrich_model()
        assert reloaded._ENRICH_MODEL
    finally:
        monkeypatch.delenv("KNOWLEDGE_ENRICH_MODEL", raising=False)
        importlib.reload(enricher)


# --------------------------------------------------------------------------
# Batch size and output budget
# --------------------------------------------------------------------------


def test_the_output_budget_scales_with_the_batch():
    # A flat 2048 for the whole request meant a full batch was truncated
    # mid-array and, because a parse failure drops the batch, every node in it
    # was lost.
    assert enricher._max_tokens_for(1) < enricher._max_tokens_for(8)
    assert enricher._max_tokens_for(8) < enricher._max_tokens_for(25)


def test_the_budget_is_bounded_at_both_ends():
    assert enricher._max_tokens_for(0) == enricher._TOKENS_FLOOR
    assert enricher._max_tokens_for(10_000) == enricher._TOKENS_CEILING
    assert enricher._max_tokens_for(-5) == enricher._TOKENS_FLOOR


def test_a_full_batch_gets_more_than_the_old_flat_budget():
    # The old value, for comparison: 2048 for up to 25 nodes.
    assert enricher._max_tokens_for(25) > 2048


def test_the_batch_is_small_enough_for_the_model_to_stay_valid():
    # gpt-oss-20b returned structurally invalid JSON at 25 nodes per request.
    assert enricher._BATCH_SIZE <= 10


def test_a_failed_batch_is_retried_one_node_at_a_time():
    # Guard: a batch failure is usually one bad section. Without the per-node
    # retry, it discards every node in the batch.
    from pathlib import Path

    source = (
        Path(enricher.__file__).resolve()
    ).read_text(encoding="utf-8")
    assert "retrying one node" in source
    assert "for node_index, node in chunk:" in source


# --------------------------------------------------------------------------
# Body-less sections
# --------------------------------------------------------------------------


class _Node:
    def __init__(self, heading, content):
        self.heading = heading
        self.content = content


def test_a_heading_with_no_body_is_not_worth_enriching():
    assert not enricher._worth_enriching(_Node("SECTION 1: COMPANY PROFILE", ""))
    assert not enricher._worth_enriching(_Node("Source: example.co.uk", "n/a"))
    assert not enricher._worth_enriching(_Node("Parent", "   \n  "))


def test_a_section_with_real_content_is_worth_enriching():
    body = "We cover residential, commercial and industrial estimating across the UK."
    assert enricher._worth_enriching(_Node("Project Types", body))


def test_the_threshold_is_tunable(monkeypatch):
    import importlib

    monkeypatch.setenv("KNOWLEDGE_ENRICH_MIN_CONTENT_CHARS", "1")
    reloaded = importlib.reload(enricher)
    try:
        assert reloaded._worth_enriching(_Node("x", "ab"))
    finally:
        monkeypatch.delenv("KNOWLEDGE_ENRICH_MIN_CONTENT_CHARS", raising=False)
        importlib.reload(enricher)


def test_nothing_worth_enriching_makes_no_api_call():
    import asyncio

    nodes = [_Node("A", ""), _Node("B", "  ")]
    out = asyncio.run(enricher.enrich_nodes(nodes))
    # One empty enrichment per node, and it returned before touching the SDK.
    assert len(out) == len(nodes)
    assert all(e.summary == "" and not e.keywords for e in out)


def test_enrichments_land_on_the_right_node_when_some_are_skipped():
    # The trap in skipping: indexes shift. Enrichment i must still map to
    # nodes[i], not to the i-th SENT node.
    from pathlib import Path

    source = Path(enricher.__file__).read_text(encoding="utf-8")
    assert "keeping their ORIGINAL positions" in source
    assert "for node_index, node in chunk:" in source
