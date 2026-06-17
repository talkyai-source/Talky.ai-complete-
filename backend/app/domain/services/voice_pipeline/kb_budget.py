"""Shared per-turn knowledge-injection budget + trimming (one source of truth).

Both KB paths size facts identically:
  * inject mode  — ``turn_streamer._knowledge_block_for_turn`` (always-on block)
  * tool mode    — ``knowledge_tool.run_knowledge_lookup`` (on-demand lookup)

Injecting the full body of k=5 nodes (whole product feature-lists) ballooned the
prompt to ~11-12k tokens, pushing Groq llama-3.3-70b to ~7s/turn and stalling
mid-reply. Best practice (Vapi/LiveKit + general RAG) is a few SHORT, relevant
chunks. These caps bound the block to ~1.5k chars (~400 tokens): top-3 nodes,
each trimmed to ~350 chars, total ≤ ~1500 chars.
"""
from __future__ import annotations

import os

_KB_MAX_CHUNKS = int(os.getenv("VOICE_KB_MAX_CHUNKS", "3"))
_KB_CHUNK_CHARS = int(os.getenv("VOICE_KB_CHUNK_CHARS", "350"))
_KB_TOTAL_CHARS = int(os.getenv("VOICE_KB_TOTAL_CHARS", "1500"))

# Hard cap on the per-turn knowledge lookup so a slow/contended DB can never add
# more than this to time-to-first-token. On timeout we skip knowledge for the
# turn — the agent still answers from persona + history. Retrieval runs
# CONCURRENTLY with the LLM's first-token latency, so a larger budget here adds
# ~0 perceived delay but sharply cuts silent "timed-out → no knowledge" turns.
_KNOWLEDGE_RETRIEVE_TIMEOUT_S = float(os.getenv("KNOWLEDGE_RETRIEVE_TIMEOUT_MS", "500")) / 1000.0


def _trim_kb_body(text: str, limit: int) -> str:
    """Trim a knowledge body to ~limit chars on a word boundary (keeps it a
    clean spoken fact, not a cut-off word)."""
    text = (text or "").strip().replace("\n", " ")
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(" ,;:-")
    return (cut or text[:limit]).rstrip() + "…"
