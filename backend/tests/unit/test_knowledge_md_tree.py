"""Unit tests for the Markdown -> knowledge-tree parser (vectorless RAG P1)."""
from __future__ import annotations

from app.services.scripts.knowledge.md_tree import parse_markdown_tree


def _by_heading(nodes, h):
    return next(n for n in nodes if n.heading == h)


def test_empty_input_returns_empty():
    assert parse_markdown_tree("") == []
    assert parse_markdown_tree("   \n  \n") == []


def test_no_headings_collapses_to_single_node():
    nodes = parse_markdown_tree("Just some plain text.\nSecond line.")
    assert len(nodes) == 1
    assert nodes[0].heading == "Overview"
    assert nodes[0].depth == 0
    assert "plain text" in nodes[0].content
    assert nodes[0].parent_index is None
    assert nodes[0].path == "1"


def test_basic_hierarchy_and_paths():
    md = (
        "# Pricing\n"
        "We have plans.\n"
        "## Starter\n"
        "$1,500.\n"
        "## Pro\n"
        "$5,000.\n"
        "# Support\n"
        "Email us.\n"
    )
    nodes = parse_markdown_tree(md)
    pricing = _by_heading(nodes, "Pricing")
    starter = _by_heading(nodes, "Starter")
    pro = _by_heading(nodes, "Pro")
    support = _by_heading(nodes, "Support")

    # top-level siblings
    assert pricing.parent_index is None and support.parent_index is None
    assert pricing.path == "1" and support.path == "2"
    # children of Pricing
    assert starter.parent_index == pricing.index
    assert pro.parent_index == pricing.index
    assert starter.path == "1.1" and pro.path == "1.2"
    # content belongs to the right node (not bleeding into children)
    assert pricing.content == "We have plans."
    assert starter.content == "$1,500."


def test_preamble_becomes_top_level_overview_sibling():
    md = "Intro paragraph before any heading.\n\n# First\nbody\n"
    nodes = parse_markdown_tree(md)
    overview = _by_heading(nodes, "Overview")
    first = _by_heading(nodes, "First")
    assert overview.depth == 0 and overview.parent_index is None and overview.path == "1"
    assert "Intro paragraph" in overview.content
    # First is a SIBLING of Overview (not nested under it)
    assert first.parent_index is None and first.path == "2"


def test_code_fence_hashes_are_not_headings():
    md = (
        "# Real\n"
        "```\n"
        "# not a heading (inside code)\n"
        "```\n"
        "after\n"
    )
    nodes = parse_markdown_tree(md)
    assert [n.heading for n in nodes] == ["Real"]
    assert "# not a heading" in nodes[0].content


def test_skipped_heading_levels():
    # H1 then jump to H3 — H3 still nests under H1 (nearest shallower).
    md = "# Top\n## Mid\n#### Deep\n"
    nodes = parse_markdown_tree(md)
    top = _by_heading(nodes, "Top")
    mid = _by_heading(nodes, "Mid")
    deep = _by_heading(nodes, "Deep")
    assert mid.parent_index == top.index
    assert deep.parent_index == mid.index   # nearest preceding shallower heading
    assert deep.depth == 4
