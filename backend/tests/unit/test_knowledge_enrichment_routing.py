"""Actual enrichment/index/evidence chain with synthetic Groq responses only."""
import copy
import json
import socket
import sys
from types import SimpleNamespace

import pytest

from app.services.scripts.knowledge import enricher
from app.services.scripts.knowledge.ingest_service import _search_text
from app.services.scripts.knowledge.md_tree import ParsedNode
from app.services.scripts.knowledge.retrieval import retrieve_pinned_knowledge
from app.domain.services.voice_pipeline.kb_budget import prepare_knowledge_evidence


def node(heading="Support", content="Support is available during ordinary business hours each weekday."):
    return ParsedNode(heading=heading, content=content, depth=1)


def item(index, **fields):
    return {"i": index, "summary": "Original summary", "voice_answer": "Original phrasing",
            "keywords": ["ordinary"], "example_questions": [], **fields}


@pytest.fixture(autouse=True)
async def network_guard(monkeypatch):
    def denied(*_args, **_kwargs):
        raise AssertionError("No live provider or database network is permitted")
    with monkeypatch.context() as patch:
        patch.setattr(socket.socket, "connect", denied)
        patch.setattr(socket.socket, "connect_ex", denied)
        patch.setattr(socket, "getaddrinfo", denied)
        yield


@pytest.fixture
def transport(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "synthetic-enrichment-key")
    monkeypatch.setattr(enricher, "_BATCH_SIZE", 2)
    calls = []

    def install(responses):
        queued = list(responses)
        async def create(**kwargs):
            calls.append(json.loads(kwargs["messages"][1]["content"])["sections"])
            value = queued.pop(0) if queued else {"nodes": []}
            if isinstance(value, Exception):
                raise value
            body = value if isinstance(value, str) else json.dumps(value)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=body))])
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        monkeypatch.setitem(sys.modules, "groq", SimpleNamespace(AsyncGroq=lambda **_kwargs: client))
    return install, calls


@pytest.mark.parametrize("single_retry", [False, True])
async def test_off_request_response_cannot_overwrite_accepted_node_and_grant_unrelated_evidence(transport, monkeypatch, single_retry):
    monkeypatch.setattr(enricher, "_BATCH_SIZE", 1)
    poison = {"nodes": [item(0, keywords=["orbital", "guidance"])]}
    transport[0]([{"nodes": [item(0)]}] + (["invalid JSON"] if single_retry else []) + [poison, poison])
    nodes = [node(), node("Billing", "Invoices are paid by bank transfer and contain the monthly charges.")]
    before = copy.deepcopy(nodes)
    output = await enricher.enrich_nodes(nodes)
    indexed = [{"id": str(i), "heading": n.heading, "content": n.content,
                "search_text": _search_text(n.heading, n.content, e.keywords, e.example_questions)}
               for i, (n, e) in enumerate(zip(nodes, output, strict=True))]
    evidence = prepare_knowledge_evidence(retrieve_pinned_knowledge(indexed, "orbital guidance"), "orbital guidance")
    assert evidence["status"] != "matched", evidence
    assert output[0].keywords == ["ordinary"] and output[1] == enricher.NodeEnrichment()
    assert nodes == before
    assert [section["i"] for section in transport[1][0]] == [0]
    assert all([section["i"] for section in call] == [1] for call in transport[1][1:])


MALFORMED = [
    [], None, '"not an object"', {"nodes": None}, {"nodes": {}}, {"nodes": [None]},
    {"nodes": [item(False)]}, {"nodes": [item("0")]}, {"nodes": [item(1)]},
    {"nodes": [item(0), item(0)]}, {"nodes": [item(0), item(0, summary="Conflicting")]},
    {"nodes": [item(0, summary=7)]}, {"nodes": [item(0, voice_answer=[])]},
    {"nodes": [item(0, keywords="orbital guidance")]},
    {"nodes": [item(0, keywords={"orbital": "guidance"})]},
    {"nodes": [item(0, keywords=[1])]},
    {"nodes": [item(0, example_questions="What is orbital guidance?")]},
    {"nodes": [item(0, example_questions=[False])]},
]


@pytest.mark.parametrize("malformed", MALFORMED)
@pytest.mark.parametrize("retry", [False, True])
async def test_invalid_metadata_falls_back_to_unchanged_raw_source_without_escaping(transport, malformed, retry):
    transport[0]((["invalid JSON"] if retry else []) + [malformed, malformed])
    nodes = [node()]
    before = copy.deepcopy(nodes)
    output = await enricher.enrich_nodes(nodes)
    assert output == [enricher.NodeEnrichment()]
    assert nodes == before and len(transport[1]) <= 2
    source = _search_text(nodes[0].heading, nodes[0].content, output[0].keywords, output[0].example_questions)
    assert source == nodes[0].heading + " " + nodes[0].content
    evidence = prepare_knowledge_evidence(retrieve_pinned_knowledge([
        {"id": "original", "heading": nodes[0].heading, "content": nodes[0].content, "search_text": source},
    ], "support business hours"), "support business hours")
    assert evidence["status"] == "matched" and nodes[0].content in evidence["text"]


async def test_batch_decode_is_atomic_before_any_metadata_assignment(transport):
    transport[0]([{"nodes": [item(0, keywords=["orbital", "guidance"]), None]}, {"nodes": []}, {"nodes": []}])
    output = await enricher.enrich_nodes([node(), node()])
    assert output == [enricher.NodeEnrichment(), enricher.NodeEnrichment()]
    assert len(transport[1]) == 3


@pytest.mark.parametrize("skipped_index", [0, 1])
async def test_response_cannot_enrich_an_unrequested_short_or_empty_section(transport, skipped_index):
    nodes = [node(), node()]
    nodes[skipped_index].content = "" if skipped_index == 0 else "short"
    transport[0]([{"nodes": [item(skipped_index)]}, {"nodes": [item(skipped_index)]}])
    output = await enricher.enrich_nodes(nodes)
    assert output == [enricher.NodeEnrichment(), enricher.NodeEnrichment()]
    assert all([s["i"] for s in call] == [1 - skipped_index] for call in transport[1])


async def test_true_index_is_not_integer_one(transport):
    transport[0]([{"nodes": [item(True)]}, {"nodes": []}, {"nodes": [item(True)]}])
    assert await enricher.enrich_nodes([node(), node()]) == [enricher.NodeEnrichment(), enricher.NodeEnrichment()]


async def test_valid_out_of_order_batch_retains_limits_and_original_positions(transport):
    transport[0]([{"nodes": [item(2, summary="s" * 500, voice_answer="v" * 600,
        keywords=["k" * 60] * 15, example_questions=["q" * 200] * 7), item(0)]}])
    nodes = [node(), node("Parent", ""), node()]
    output = await enricher.enrich_nodes(nodes)
    assert [s["i"] for s in transport[1][0]] == [0, 2] and len(transport[1]) == 1
    assert output[0].summary == "Original summary" and output[1] == enricher.NodeEnrichment()
    assert output[2].summary == "s" * 300 and output[2].voice_answer == "v" * 400
    assert output[2].keywords == ["k" * 40] * 12 and output[2].example_questions == ["q" * 160] * 5


async def test_valid_single_retries_recover_their_own_nodes(transport):
    transport[0](["invalid JSON", {"nodes": [item(0)]}, {"nodes": [item(1, summary="Second summary")]}])
    output = await enricher.enrich_nodes([node(), node()])
    assert [e.summary for e in output] == ["Original summary", "Second summary"]
    assert [[s["i"] for s in call] for call in transport[1]] == [[0, 1], [0], [1]]


@pytest.mark.parametrize("response", [{}, {"nodes": []}, {"nodes": [{"i": 0}]}, {"nodes": [item(0, summary=None, voice_answer=None, keywords=None, example_questions=None)]}])
async def test_empty_optional_derivatives_remain_supported(transport, response):
    transport[0]([response])
    assert await enricher.enrich_nodes([node()]) == [enricher.NodeEnrichment()]
    assert len(transport[1]) == 1
