"""Search metadata may route candidates but cannot establish source coverage."""
import copy
import json
import socket

import pytest

from app.domain.services.voice_pipeline.kb_budget import prepare_knowledge_evidence
from app.services.scripts.knowledge.enricher import _decode_enrichments
from app.services.scripts.knowledge.ingest_service import _search_text
from app.services.scripts.knowledge.retrieval import retrieve_pinned_knowledge


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*_args, **_kwargs):
        raise AssertionError("No provider or database request is permitted")
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)
    monkeypatch.setattr(socket, "getaddrinfo", denied)


def indexed(*, field="keywords", heading="Support", content=None):
    """A structurally valid response for the exact requested node, not misrouting."""
    derivative = ["orbital", "guidance"] if field == "keywords" else ["What is orbital guidance?"]
    enrichment = _decode_enrichments(json.dumps({"nodes": [{"i": 0, field: derivative}]}), {0})[0]
    content = content if content is not None else "Support is available during ordinary business hours each weekday."
    return {"id": "original", "source_id": "guide", "source_version": 2,
            "version": "revision-2", "heading": heading, "content": content,
            "search_text": _search_text(heading, content, enrichment.keywords, enrichment.example_questions)}


@pytest.mark.parametrize("field", ["keywords", "example_questions"])
@pytest.mark.parametrize("query,expected", [("orbital guidance", 0.0), ("orbital guidance support", 1 / 3)])
def test_valid_same_node_aliases_route_without_authorizing_unrelated_source(field, query, expected):
    node = indexed(field=field)
    original = copy.deepcopy(node)
    hits = retrieve_pinned_knowledge([node], query)
    assert [hit["id"] for hit in hits] == ["original"]  # Keep the routing benefit.
    evidence = prepare_knowledge_evidence(hits, query)
    assert evidence["status"] == "weak_match", evidence
    assert hits[0]["coverage"] == pytest.approx(expected)
    assert evidence["passages"][0]["source_version"] == 2
    assert evidence["passages"][0]["version"] == "revision-2"
    assert node["content"] in evidence["text"] and node == original


@pytest.mark.parametrize("field", ["summary", "voice_answer"])
def test_legacy_fallback_derivatives_do_not_grant_factual_coverage(field):
    node = indexed()
    node.pop("search_text")
    node[field] = "Orbital guidance"
    hits = retrieve_pinned_knowledge([node], "orbital guidance")
    assert hits and hits[0]["id"] == "original"
    assert prepare_knowledge_evidence(hits, "orbital guidance")["status"] == "weak_match"
    assert hits[0]["coverage"] == 0


@pytest.mark.parametrize("heading,content", [
    ("Orbital guidance", "The supported system requires a calibrated antenna."),
    ("Support", "Orbital guidance requires a calibrated antenna."),
])
def test_authored_heading_or_content_still_establishes_coverage(heading, content):
    node = indexed(heading=heading, content=content)
    hits = retrieve_pinned_knowledge([node], "orbital guidance")
    evidence = prepare_knowledge_evidence(hits, "orbital guidance")
    assert hits[0]["coverage"] == 1 and evidence["status"] == "matched"
    assert content in evidence["text"]


def test_unchanged_ranking_keeps_alias_candidate_but_only_supported_source_is_admitted():
    alias = indexed()
    alias["priority"] = 10
    supported = indexed(heading="Orbital guidance", content="Orbital guidance requires calibration.")
    supported["id"] = "supported"
    hits = retrieve_pinned_knowledge([alias, supported], "orbital guidance", k=2)
    assert [hit["id"] for hit in hits] == ["original", "supported"]
    evidence = prepare_knowledge_evidence(hits, "orbital guidance")
    assert evidence["status"] == "matched"
    assert [passage["node_id"] for passage in evidence["passages"]] == ["supported"]


def test_raw_unenriched_source_retains_coverage_and_original_text():
    node = {"id": "support", "heading": "Support", "content": "Support business hours are weekdays."}
    hits = retrieve_pinned_knowledge([node], "support business hours")
    assert hits[0]["coverage"] == 1
    assert prepare_knowledge_evidence(hits, "support business hours")["status"] == "matched"


def test_incoming_coverage_cannot_override_measured_source_coverage():
    node = indexed()
    node["coverage"] = 1
    hits = retrieve_pinned_knowledge([node], "orbital guidance")
    assert prepare_knowledge_evidence(hits, "orbital guidance")["status"] == "weak_match"
    assert hits[0]["coverage"] == 0 and node["coverage"] == 1


def test_alias_without_any_authored_body_never_becomes_a_passage():
    hits = retrieve_pinned_knowledge([indexed(content="")], "orbital guidance")
    assert hits and prepare_knowledge_evidence(hits, "orbital guidance")["status"] == "no_match"


def test_numeric_only_query_cannot_inherit_prior_query_confidence():
    node = {"id": "legacy", "content": "Support is available on weekdays.",
            "search_text": "20", "coverage": 1}
    hits = retrieve_pinned_knowledge([node], "20")
    assert hits and hits[0]["id"] == "legacy"  # Numeric routing remains available.
    assert prepare_knowledge_evidence(hits, "20")["status"] == "weak_match"
    assert hits[0]["coverage"] is None and node["coverage"] == 1
