"""bughunt knowledge-prompts-3: contractions must not become STT keyterms."""
from __future__ import annotations

from app.domain.services.telephony_session_config import (
    _build_call_keyterms,
    _extract_product_terms,
)

SCRIPT = (
    "You're calling about Dojo Go. Don't pitch hard; it's a card reader. "
    "If they say they don't want it, don't argue. We can't quote prices, "
    "that's for sales. Mention the Pocket Card Reader and iZettle, "
    "and note most SMB owners already have one."
)


def test_contractions_do_not_become_keyterms():
    terms = _extract_product_terms({"additional_instructions": SCRIPT}, "Dojo")
    assert "Pocket Card Reader" in terms
    assert "iZettle" in terms
    assert "SMB" in terms
    # no lowercase-led fragments cut out of the apostrophes
    assert [t for t in terms if t[:1].islower() and t != "iZettle"] == []
    assert not any("'" in t or "’" in t for t in terms)
    assert not any(t.startswith(("re ", "t ", "s ")) for t in terms)


def test_garbage_does_not_reach_stt_keyterms():
    terms = _extract_product_terms({"additional_instructions": SCRIPT}, "Acme")
    built = _build_call_keyterms("Acme", "Sam", terms)
    assert "Dojo Go" in built
    assert not any(t.lower().startswith(("re calling", "t argue", "s a card")) for t in built)


def test_double_and_curly_quoted_phrases_still_work():
    text = 'Say the "Pocket" reader, or the “Flex Stand” and the ‘Hub Mini’ unit.'
    terms = _extract_product_terms({"additional_instructions": text}, "Acme")
    assert "Pocket" in terms
    assert "Flex Stand" in terms
    assert "Hub Mini" in terms


def test_single_quoted_phrase_with_space_boundaries_still_works():
    terms = _extract_product_terms(
        {"additional_instructions": "mention the 'Pocket' reader"}, "Acme"
    )
    assert "Pocket" in terms
