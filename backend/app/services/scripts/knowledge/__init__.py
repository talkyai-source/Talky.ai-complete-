"""Campaign knowledge layer (vectorless RAG).

Turns an uploaded .md/.txt into a hierarchical, LLM-enriched knowledge tree
that is retrieved vectorlessly (Postgres FTS + pg_trgm) into the agent's
system prompt. See
docs/superpowers/plans/2026-06-03-vectorless-rag-campaign-knowledge.md.
"""
from app.services.scripts.knowledge.budget import (
    choose_mode,
    context_window_for,
    estimate_tokens,
    inline_budget_for,
)
from app.services.scripts.knowledge.ingest_service import ingest_markdown
from app.services.scripts.knowledge.md_tree import ParsedNode, parse_markdown_tree
from app.services.scripts.knowledge.retrieval import compact_tree, retrieve_knowledge

__all__ = [
    "ParsedNode",
    "parse_markdown_tree",
    "estimate_tokens",
    "choose_mode",
    "inline_budget_for",
    "context_window_for",
    "ingest_markdown",
    "retrieve_knowledge",
    "compact_tree",
]
