"""LLM enrichment of knowledge-tree nodes (vectorless RAG, P1).

Ingest-time (not latency-critical): for each parsed node, ask the LLM for a
1-line `summary`, a spoken-style `voice_answer` (1-2 sentences the agent can
say verbatim), `keywords` (synonyms + likely STT mishears, to power fuzzy
retrieval), and `example_questions`. One JSON call per batch.

Fail-soft by design: any error → empty enrichment, and the node still works
(retrieval falls back to heading+content). Enrichment quality is an
optimisation, never a correctness requirement.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import List

from app.services.scripts.knowledge.md_tree import ParsedNode

logger = logging.getLogger(__name__)

_ENRICH_MODEL = os.getenv("KNOWLEDGE_ENRICH_MODEL", "llama-3.1-8b-instant")
_BATCH_SIZE = int(os.getenv("KNOWLEDGE_ENRICH_BATCH", "25"))
_CONTENT_CLIP = 600  # chars of node content sent to the enricher


@dataclass
class NodeEnrichment:
    summary: str = ""
    voice_answer: str = ""
    keywords: List[str] = field(default_factory=list)
    example_questions: List[str] = field(default_factory=list)


_SYSTEM = (
    "You enrich sections of a company knowledge base so a phone agent can answer "
    "callers. For EACH input section return concise metadata. Respond ONLY with "
    "JSON: {\"nodes\":[{\"i\":int,\"summary\":str,\"voice_answer\":str,"
    "\"keywords\":[str],\"example_questions\":[str]}]}. "
    "summary: one short line. voice_answer: 1-2 sentences the agent can say out "
    "loud, natural and specific. keywords: 4-8 terms a caller might use INCLUDING "
    "likely speech-to-text mishears/synonyms. example_questions: 2-3 questions this "
    "section answers. Keep it tight; no extra keys."
)


def _empty(n: int) -> List[NodeEnrichment]:
    return [NodeEnrichment() for _ in range(n)]


async def enrich_nodes(nodes: List[ParsedNode]) -> List[NodeEnrichment]:
    """Return one NodeEnrichment per node (same order). Never raises."""
    if not nodes:
        return []
    out = _empty(len(nodes))
    try:
        from groq import AsyncGroq
        from app.infrastructure.providers.key_pool import parse_keys_csv
    except Exception as exc:  # pragma: no cover - import guard
        logger.warning("knowledge enrich: groq SDK unavailable (%s) — skipping", exc)
        return out

    keys = parse_keys_csv(os.getenv("GROQ_API_KEY", ""))
    if not keys:
        logger.warning("knowledge enrich: no GROQ_API_KEY — skipping enrichment")
        return out
    client = AsyncGroq(api_key=keys[0])

    for start in range(0, len(nodes), _BATCH_SIZE):
        batch = nodes[start:start + _BATCH_SIZE]
        payload = [
            {"i": start + j, "heading": n.heading, "content": n.content[:_CONTENT_CLIP]}
            for j, n in enumerate(batch)
        ]
        try:
            resp = await client.chat.completions.create(
                model=_ENRICH_MODEL,
                messages=[
                    {"role": "system", "content": _SYSTEM},
                    {"role": "user", "content": json.dumps({"sections": payload})},
                ],
                response_format={"type": "json_object"},
                temperature=0.2,
                max_tokens=2048,
            )
            data = json.loads(resp.choices[0].message.content or "{}")
            for item in data.get("nodes", []):
                i = item.get("i")
                if not isinstance(i, int) or not (0 <= i < len(out)):
                    continue
                out[i] = NodeEnrichment(
                    summary=str(item.get("summary", "") or "")[:300],
                    voice_answer=str(item.get("voice_answer", "") or "")[:400],
                    keywords=[str(k)[:40] for k in (item.get("keywords") or [])][:12],
                    example_questions=[str(q)[:160] for q in (item.get("example_questions") or [])][:5],
                )
        except Exception as exc:
            logger.warning(
                "knowledge enrich batch [%d:%d] failed (%s) — leaving those nodes unenriched",
                start, start + len(batch), exc,
            )
            # leave this batch as empty enrichments; continue with the rest
    return out
