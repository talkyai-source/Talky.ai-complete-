"""Campaign knowledge layer (vectorless RAG).

Turns an uploaded .md/.txt into a hierarchical, LLM-enriched knowledge tree
that is retrieved vectorlessly (Postgres FTS + pg_trgm) into the agent's
system prompt. See
docs/superpowers/plans/2026-06-03-vectorless-rag-campaign-knowledge.md.
"""
from app.services.scripts.knowledge.md_tree import ParsedNode, parse_markdown_tree

__all__ = ["ParsedNode", "parse_markdown_tree"]
