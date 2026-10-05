"""Predeclared lexical controls, not a semantic or model-quality evaluation."""

import pytest

from app.domain.services.voice_pipeline.kb_budget import prepare_knowledge_evidence
from app.services.scripts.knowledge.retrieval import retrieve_pinned_knowledge


def evidence(query, content):
    node = {"id": "synthetic", "source_id": "authored-source", "source_version": 1,
            "version": 1, "heading": "Reference", "content": content}
    hits = retrieve_pinned_knowledge([node], query, k=3)
    return prepare_knowledge_evidence(hits, query)


PAIRS = [("account", "accounts"), ("fee", "fees"), ("booking", "bookings"),
         ("delivery", "deliveries"), ("batch", "batches"), ("invoice", "invoices")]


@pytest.mark.parametrize("singular,plural", PAIRS)
@pytest.mark.parametrize("reverse", [False, True])
def test_regular_inflection_preserves_authored_evidence(singular, plural, reverse):
    query, source_word = (singular, plural) if reverse else (plural, singular)
    source = f"The {source_word} is described in this document."
    result = evidence(query, source)
    assert result["status"] == "matched", result
    assert result["passages"][0]["source_id"] == "authored-source"
    assert source in result["passages"][0]["text"]


@pytest.mark.parametrize("query,source", [
    ("Is it free?", "A fee applies."),
    ("Any news?", "New orders are open."),
    ("Police", "The policy is available."),
    ("Form", "The dispatch is from the warehouse."),
    ("Quantum booking warranty", "Booking information is available."),
    ("Banana orbital launch", "Booking information is available."),
    ("model500s", "The model500 is the available product."),
    ("Status", "A statue is displayed."),
    ("Series", "The species is protected."),
    ("Business", "The bus is available."),
])
def test_fuzzy_or_partial_match_does_not_authorize_facts(query, source):
    assert evidence(query, source)["status"] != "matched"


def test_inflection_keeps_negative_answer_and_qualification():
    source = ("Subscription payment is not supported. "
              "A minimum notice period applies.")
    result = evidence("Are subscription payments supported?", source)
    assert result["status"] == "matched"
    assert source in result["passages"][0]["text"]


def test_long_source_inflection_keeps_answer_and_adjacent_exclusion():
    qualifier = "Deliveries take five days. This excludes remote islands."
    source = "General administration information. " * 25 + qualifier
    result = evidence("Delivery", source)
    assert result["status"] == "matched", result
    assert qualifier in result["passages"][0]["text"]


@pytest.mark.parametrize("query,source", [
    ("Universes", "Universities are listed here."),
    ("Universe courses", "University course fees are 100 units."),
    ("Organ memberships", "Organization membership costs 50 units."),
])
def test_derivational_collision_does_not_authorize_source(query, source):
    """Added after the blanket-stem probe exposed these unsafe admissions."""
    assert evidence(query, source)["status"] != "matched"


def test_oversized_unknown_word_stays_in_coverage_denominator():
    query = "a" * 100_000 + " accounts warranty"
    assert evidence(query, "The account is described in this document.")["status"] != "matched"
