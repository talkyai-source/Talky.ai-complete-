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

def _default_enrich_model() -> str:
    """The Groq model to enrich with, taken from the canonical model menu.

    This was hardcoded to ``llama-3.1-8b-instant``. That id started returning
    404 on this account around 2026-08-17 and the voice path was moved off it
    the same day, but the enricher was missed. Every upload since then had its
    summaries, spoken answers, keywords and example questions silently dropped:
    enrichment is fail-soft, so each batch logged a warning and the nodes were
    published bare. Observed again on 2026-09-22.

    Reading the menu means a model the product retires cannot leave this
    pointing at something that no longer exists. Falls back to the id the
    product runs today only if the menu cannot be read at all.
    """
    try:
        from app.domain.models.ai_config import GROQ_MODELS

        for entry in GROQ_MODELS:
            model_id = str(getattr(entry, "id", "") or "").strip()
            if model_id:
                return model_id
    except Exception:  # pragma: no cover - defensive
        pass
    return "openai/gpt-oss-20b"


_ENRICH_MODEL = os.getenv("KNOWLEDGE_ENRICH_MODEL", "").strip() or _default_enrich_model()
# Nodes per request. Was 25. A batch that large asks the model for roughly
# 25 x (summary + spoken answer + up to 12 keywords + up to 5 questions) inside
# ONE json_object response, and gpt-oss-20b returns structurally invalid JSON
# at that size -- the whole batch is then lost, because enrichment is fail-soft
# and a parse failure drops every node in it. Smaller requests are far more
# reliable and cost the same in total tokens (observed 2026-09-22).
_BATCH_SIZE = int(os.getenv("KNOWLEDGE_ENRICH_BATCH", "8"))
# Output budget per node: a summary, a spoken answer, keywords and example
# questions. Was a flat 2048 for the whole request regardless of batch size, so
# a full batch was truncated mid-array and, again, lost entirely.
_TOKENS_PER_NODE = int(os.getenv("KNOWLEDGE_ENRICH_TOKENS_PER_NODE", "200"))
_TOKENS_FLOOR = 1024
_TOKENS_CEILING = 8192
# A heading with no body under it -- a parent in the tree, or a one-line source
# line -- has nothing to summarise. Sending it anyway spends a request, returns
# nothing usable, and on a strict json_object endpoint often fails outright,
# which then looks in the log exactly like a real enrichment failure. On a
# 27-section document that was 7 wasted calls and 7 misleading warnings.
_MIN_CONTENT_CHARS = int(os.getenv("KNOWLEDGE_ENRICH_MIN_CONTENT_CHARS", "40"))


def _worth_enriching(node) -> bool:
    """True if the node has enough body for a summary to mean anything."""
    return len((getattr(node, "content", "") or "").strip()) >= _MIN_CONTENT_CHARS


def _max_tokens_for(batch_size: int) -> int:
    """Output budget scaled to how many nodes the request must describe."""
    wanted = 256 + _TOKENS_PER_NODE * max(1, int(batch_size))
    return max(_TOKENS_FLOOR, min(_TOKENS_CEILING, wanted))
# Chars of node content sent to the enricher. This was 600, which truncated
# every node to just the TOP of the section, so keywords/voice_answer NEVER
# saw any fact below ~600 chars — the enrichment silently summarised only the
# head of each node. (Retrieval still FTS/trgm-indexes the FULL content, but
# the fuzzy keywords + spoken answer missed later facts.) Raised to cover whole
# realistic KB sections; env-overridable for cost control on huge corpora.
# NOTE: this only affects FUTURE ingests — nodes already enriched at the old
# 600-char cap must be RE-INGESTED to pick up their later facts.
_CONTENT_CLIP = int(os.getenv("KNOWLEDGE_ENRICH_CONTENT_CHARS", "6000"))


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

    # Index the nodes worth sending, keeping their ORIGINAL positions so each
    # enrichment still lands on the right node. Headings with no body keep the
    # empty enrichment they were initialised with.
    candidates = [(i, n) for i, n in enumerate(nodes) if _worth_enriching(n)]
    skipped = len(nodes) - len(candidates)
    if skipped:
        logger.info(
            "knowledge enrich: %d of %d section(s) have no body to summarise — "
            "skipped, not failed", skipped, len(nodes),
        )
    if not candidates:
        return out

    for start in range(0, len(candidates), _BATCH_SIZE):
        chunk = candidates[start:start + _BATCH_SIZE]
        batch = [n for _, n in chunk]
        payload = [
            {"i": i, "heading": n.heading, "content": n.content[:_CONTENT_CLIP]}
            for i, n in chunk
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
                max_tokens=_max_tokens_for(len(batch)),
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
                "knowledge enrich batch [%d:%d] failed (%s) — retrying one node "
                "at a time so a single bad response does not lose the batch",
                start, start + len(chunk), exc,
            )
            # A batch failure is usually ONE malformed section dragging the
            # whole response down. Retrying singly recovers the rest instead of
            # discarding every node in the batch.
            for node_index, node in chunk:
                single = [{
                    "i": node_index,
                    "heading": node.heading,
                    "content": node.content[:_CONTENT_CLIP],
                }]
                try:
                    resp = await client.chat.completions.create(
                        model=_ENRICH_MODEL,
                        messages=[
                            {"role": "system", "content": _SYSTEM},
                            {"role": "user", "content": json.dumps({"sections": single})},
                        ],
                        response_format={"type": "json_object"},
                        temperature=0.2,
                        max_tokens=_max_tokens_for(1),
                    )
                    data = json.loads(resp.choices[0].message.content or "{}")
                except Exception:
                    continue
                for item in data.get("nodes", []):
                    i = item.get("i")
                    if not isinstance(i, int) or not (0 <= i < len(out)):
                        continue
                    out[i] = NodeEnrichment(
                        summary=str(item.get("summary", "") or "")[:300],
                        voice_answer=str(item.get("voice_answer", "") or "")[:400],
                        keywords=[str(k)[:40] for k in (item.get("keywords") or [])][:12],
                        example_questions=[
                            str(q)[:160] for q in (item.get("example_questions") or [])
                        ][:5],
                    )
    return out
