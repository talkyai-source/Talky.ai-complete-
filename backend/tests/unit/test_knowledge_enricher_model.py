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
