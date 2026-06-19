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
import re

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


# ── Knowledge intent gate ────────────────────────────────────────────────────
#
# Root-cause fix for "KB query fires on every turn": retrieval was coupled to
# the turn loop regardless of whether the caller actually asked something. A
# bare "okay" / "yeah" triggered a DB lookup whose latency lands BEFORE
# time-to-first-token. This gate skips the lookup for utterances that are
# confidently content-free backchannels. It defaults to ON and is conservative:
# anything that might be a question retrieves, so a real question is never
# starved of knowledge. Set VOICE_KB_SKIP_BACKCHANNELS=0 to always retrieve.
_KB_SKIP_BACKCHANNELS = os.getenv("VOICE_KB_SKIP_BACKCHANNELS", "1").strip().lower() in (
    "1", "true", "yes", "on",
)

# Short, content-free acknowledgements — matched only as the WHOLE utterance,
# never as a substring of a real sentence.
_BACKCHANNELS = frozenset({
    "ok", "okay", "k", "kk", "yeah", "yep", "yes", "yup", "ya", "no", "nope",
    "nah", "sure", "right", "alright", "all right", "cool", "great", "nice",
    "good", "fine", "perfect", "exactly", "totally", "gotcha", "got it",
    "i see", "makes sense", "sounds good", "fair enough", "mhm", "mm", "mmhmm",
    "uh huh", "uh-huh", "hmm", "ah", "oh", "okay then", "thanks", "thank you",
    "cheers", "wow", "yeah yeah", "right right", "okay okay", "mm hmm",
})

# Words that signal a real question / knowledge need — if any appears, retrieve.
_QUESTION_SIGNAL = re.compile(
    r"\b(who|what|when|where|why|how|which|whose|whom|"
    r"do|does|did|can|could|would|will|is|are|was|were|have|has|"
    r"price|cost|much|plan|plans|offer|available|hours|open|refund|"
    r"warranty|policy|fee|fees|quote|estimate|book|booking|appointment)\b",
    re.IGNORECASE,
)


def should_retrieve_knowledge(text: str) -> bool:
    """Decide whether a caller turn warrants a knowledge-base lookup.

    Returns True (retrieve) by default — we only return False for utterances
    that are confidently content-free backchannels, so a real question is never
    starved of knowledge. Skipping those turns removes the per-turn DB query
    (up to the retrieve timeout) from the critical path before first token.
    """
    if not _KB_SKIP_BACKCHANNELS:
        return True
    t = (text or "").strip().lower()
    if not t:
        return False  # nothing to answer → nothing to retrieve
    if "?" in t or _QUESTION_SIGNAL.search(t):
        return True
    norm = re.sub(r"[^a-z\s]", " ", t)
    norm = re.sub(r"\s+", " ", norm).strip()
    if not norm:
        return False  # punctuation/noise only
    words = norm.split()
    if len(words) > 4:
        return True  # substantive utterance → retrieve
    if norm in _BACKCHANNELS or all(w in _BACKCHANNELS for w in words):
        return False  # whole utterance is acknowledgement words → skip
    return True
