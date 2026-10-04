"""Explicit currency codes use the existing amount/currency/condition boundary."""

import pytest

from app.domain.services.voice_pipeline.grounded_figures import (
    UNGROUNDED_FIGURE_REPLACEMENT,
    ground_spoken_figures,
)


@pytest.mark.parametrize("code", ["GBP", "USD", "EUR", "CAD", "AUD", "NZD"])
@pytest.mark.parametrize("form", ["{code} 20", "{code}20", "{code} twenty", "20 {code}"])
def test_iso_codes_require_source_and_accept_matching_facts(code, form):
    text = "It costs " + form.format(code=code) + " per month excluding tax."
    source = f"Monthly price: {code.lower()} 20 excluding tax."
    assert ground_spoken_figures(text, [source]) == (text, [])
    guarded, unsupported = ground_spoken_figures(text, [])
    assert guarded == UNGROUNDED_FIGURE_REPLACEMENT
    assert unsupported


@pytest.mark.parametrize(
    "claim",
    [
        "It costs USD 20 per month excluding tax.",
        "It costs EUR 20 per month excluding tax.",
        "It costs CAD 20 per month excluding tax.",
        "It costs GBP 20 per year excluding tax.",
        "It costs GBP 20 per month.",
        "It costs GBP 20 per month including tax.",
        "It costs GBP 999 per month excluding tax.",
    ],
)
def test_explicit_currency_and_conditions_cannot_be_rewritten(claim):
    safe, bad = ground_spoken_figures(claim, ["GBP 20 per month excluding tax."])
    assert safe == UNGROUNDED_FIGURE_REPLACEMENT
    assert bad


def test_symbol_source_and_iso_source_share_the_same_currency():
    text = "It costs GBP 20 per month."
    assert ground_spoken_figures(text, ["Monthly price: £20."]) == (text, [])
    spoken = "It costs £20 per month."
    assert ground_spoken_figures(spoken, ["Monthly price: GBP 20."]) == (spoken, [])


def test_rejected_iso_price_is_not_positive_evidence():
    claim = "It costs GBP 20 per month."
    assert ground_spoken_figures(claim, ["It is not GBP 20 per month."])[1]
    approved = "It costs GBP 30 per month."
    assert ground_spoken_figures(
        approved, ["It is not GBP 20 per month but GBP 30 per month."]
    ) == (approved, [])


@pytest.mark.parametrize(
    "text",
    [
        "The model is ABC 999.",
        "Product XGBP20 is available.",
        "Product GBP20X is available.",
        "Use the code EUR_20.",
        "We have 20 CADENCE devices.",
        "Reference USD20A needs checking.",
        "We serve 150,000 businesses.",
    ],
)
def test_non_currency_product_identifiers_and_counts_are_not_prices(text):
    assert ground_spoken_figures(text, []) == (text, [])
