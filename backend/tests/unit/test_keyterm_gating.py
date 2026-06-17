"""Tests for STT keyterm gating + product-name extraction.

Two changes under test:
  1. Email-spelling terms (dot / at / dash / domains) are CAPTURE-ONLY now —
     off the always-on path so they can't bias ordinary speech; folded in only
     while the caller is spelling an email (DeepgramFlux capture mode).
  2. Product / brand names are pulled from the campaign's free-text config and
     added to the per-call base keyterms.
"""
from app.domain.services.telephony_session_config import (
    _build_call_keyterms,
    _extract_product_terms,
)
from app.infrastructure.stt.deepgram_flux import DeepgramFluxSTTProvider


# ---------------------------------------------------------------------------
# Base keyterms — brand/agent/product, NO email terms
# ---------------------------------------------------------------------------
def test_base_keyterms_have_brand_and_agent_no_email_terms():
    terms = _build_call_keyterms("Dojo Payments Ltd", "Sarah")
    low = [t.lower() for t in terms]
    assert "dojo payments ltd" in low
    assert "dojo" in low          # significant single word of the brand
    assert "sarah" in low
    # Email-spelling terms must NOT be in the always-on set anymore.
    for email_term in ("dot", "at sign", "dash", "gmail.com", "hyphen"):
        assert email_term not in low


def test_product_terms_added_to_base():
    terms = _build_call_keyterms("Dojo", "Sarah", ["Dojo Go", "Pocket Reader"])
    low = [t.lower() for t in terms]
    assert "dojo go" in low
    assert "pocket reader" in low


def test_keyterms_capped_at_60():
    terms = _build_call_keyterms("Acme", "Bob", [f"Prod{i}X" for i in range(100)])
    assert len(terms) <= 60


# ---------------------------------------------------------------------------
# Product extraction heuristic
# ---------------------------------------------------------------------------
def test_extract_multiword_titlecase():
    sc = {"additional_instructions": "Pitch the Dojo Go card reader to small shops."}
    out = _extract_product_terms(sc, "Dojo")
    assert "Dojo Go" in out


def test_extract_quoted_and_internalcap_and_digit():
    sc = {"additional_instructions": 'We sell "Pocket" alongside iZettle and the G2 terminal.'}
    out = [t.lower() for t in _extract_product_terms(sc, "Acme")]
    assert "pocket" in out      # quoted
    assert "izettle" in out     # internal cap
    assert "g2" in out          # has digit


def test_extract_ignores_plain_capitalised_words():
    sc = {"additional_instructions": "Please be polite. When the caller answers, greet them."}
    out = _extract_product_terms(sc, "Acme")
    # "Please" / "When" are sentence-initial common words, not products.
    assert out == []


def test_extract_skips_brand_and_stopwords():
    sc = {"additional_instructions": "Represent Acme Corp well."}
    out = [t.lower() for t in _extract_product_terms(sc, "Acme Corp")]
    assert "acme corp" not in out   # it's the brand, already covered


def test_extract_explicit_products_list():
    sc = {"products": ["Widget Pro", "Gizmo 5000"]}
    out = _extract_product_terms(sc, "Acme")
    assert "Widget Pro" in out
    assert "Gizmo 5000" in out


def test_extract_handles_non_dict():
    assert _extract_product_terms(None, "Acme") == []
    assert _extract_product_terms({}, "Acme") == []


# ---------------------------------------------------------------------------
# DeepgramFlux capture-mode keyterm payloads
# ---------------------------------------------------------------------------
def _provider_with_keyterms(base, capture):
    p = DeepgramFluxSTTProvider()
    p._keyterms = list(base)
    p._capture_keyterms = list(capture)
    p._eot_timeout_ms = 500
    p._eot_threshold = 0.85
    p._eager_eot_threshold = 0.7
    return p


def test_enter_capture_mode_adds_email_terms():
    p = _provider_with_keyterms(["Dojo", "Sarah"], ["dot", "at sign", "gmail.com"])
    p.enter_capture_mode("call-1")
    payload = p._pending_config["call-1"]
    kt = [t.lower() for t in payload["keyterms"]]
    assert "dojo" in kt and "sarah" in kt          # base kept
    assert "dot" in kt and "gmail.com" in kt        # email terms folded in
    assert payload["type"] == "Configure"


def test_reset_capture_mode_drops_email_terms():
    p = _provider_with_keyterms(["Dojo", "Sarah"], ["dot", "at sign", "gmail.com"])
    p.reset_capture_mode("call-1")
    kt = [t.lower() for t in p._pending_config["call-1"]["keyterms"]]
    assert "dojo" in kt and "sarah" in kt
    assert "dot" not in kt and "gmail.com" not in kt


def test_capture_active_keyterms_dedup():
    # An overlapping term must not appear twice.
    p = _provider_with_keyterms(["dot", "Dojo"], ["dot", "at sign"])
    merged = [t.lower() for t in p._capture_active_keyterms()]
    assert merged.count("dot") == 1
    assert "at sign" in merged
