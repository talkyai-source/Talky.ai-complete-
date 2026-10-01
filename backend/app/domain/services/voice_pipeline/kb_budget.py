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
from app.services.scripts.knowledge.passages import content_words, select_passage

_KB_MAX_CHUNKS = int(os.getenv("VOICE_KB_MAX_CHUNKS", "3"))
# 2026-09-07: 350 -> 600 per node, 1500 -> 2000 total. Measured on the live
# Dojo knowledge base: the "What are your rates?" node is 1,501 chars and its
# first figure sits at char 654, so a 350-char source-first cut delivered the
# node's preamble ("CRITICAL RULE: never quote a rate...") and dropped both the
# script and the answer — retrieval "worked" while the agent could not answer.
# ~+500 chars per turn (~125 tokens) is the cost; see fit_kb_body for the
# guarantee that the short spoken answer always survives a trim.
_KB_CHUNK_CHARS = int(os.getenv("VOICE_KB_CHUNK_CHARS", "600"))
_KB_TOTAL_CHARS = int(os.getenv("VOICE_KB_TOTAL_CHARS", "2000"))

# Hard cap on the per-turn knowledge lookup so a slow/contended DB can never add
# more than this to time-to-first-token. On timeout we skip knowledge for the
# turn — the agent still answers from persona + history. Retrieval is awaited
# SERIALLY before the LLM iterator starts, so this budget sits directly on the
# first-token critical path: keep it tight, but large enough to avoid silent
# "timed-out → no knowledge" turns.
_KNOWLEDGE_RETRIEVE_TIMEOUT_S = float(os.getenv("KNOWLEDGE_RETRIEVE_TIMEOUT_MS", "500")) / 1000.0


def _trim_kb_body(text: str, limit: int) -> str:
    """Trim a knowledge body to ~limit chars on a word boundary (keeps it a
    clean spoken fact, not a cut-off word)."""
    text = (text or "").strip().replace("\n", " ")
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(" ,;:-")
    return (cut or text[:limit]).rstrip() + "…"


def fit_kb_body(rendered: str, node: dict, limit: int, *, query: str = "") -> str:
    """Fit a source-first rendered node into ``limit`` chars WITHOUT losing the
    answer.

    ``render_node_answer`` leads with the node's source text (so a fact
    anywhere in the node can be matched) and only appends the enricher's short
    ``voice_answer`` when there is room. When the source is longer than the
    budget the old ``_trim_kb_body(render_node_answer(h))`` cut it at
    ``limit`` — which on real knowledge bases kept the node's preamble and
    dropped the sentence that actually answers the caller. The ellipsis told
    the model the fact was incomplete, and it duly said it did not know.

    Rule: if the rendered text fits, return it unchanged. Otherwise trim the
    source and ALWAYS append the spoken ``voice_answer`` (the distilled answer
    for exactly this node) after the ellipsis, reserving room for it inside
    the same budget. A node without a voice_answer degrades to the plain trim.
    """
    if query:
        return select_passage(str(node.get("content") or rendered or ""), query, limit)
    text = (rendered or "").strip()
    if len(text.replace("\n", " ")) <= limit:
        return _trim_kb_body(text, limit)
    phrasing = " ".join(str((node or {}).get("voice_answer") or "").split())
    if not phrasing:
        return _trim_kb_body(text, limit)
    # Never let a very long voice_answer starve the source of all context.
    phrasing = _trim_kb_body(phrasing, max(80, limit // 2))
    head_budget = max(40, limit - len(phrasing) - 1)
    head = _trim_kb_body(text, head_budget)
    if phrasing.lower() in head.lower():
        return head
    return f"{head} {phrasing}"


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


_STOPWORDS = frozenset(
    "a an the and or but if then so of to in on at by for with from about as into "
    "is are was were be been being am do does did done have has had having can could "
    "will would shall should may might must i me my mine we us our you your yours he "
    "him his she her it its they them their this that these those there here what "
    "which who whom whose when where why how not no yes yeah ok okay please just "
    "also very really any some all more most much many than too up out".split()
)

# Share of the question the best knowledge hit actually covers (idf-weighted,
# see retrieval.py) below which the hit is treated as NOT answering it.
# Measured on prod 2026-09-29 over 2,354 real example questions across 24
# campaigns: 0.30% of real questions fall below 0.5, while off-topic probes
# (Klarna, weather, flights, car insurance, football, bitcoin) sit at a median
# of 0.23 and a max of 0.53.
KNOWLEDGE_MIN_COVERAGE = float(os.getenv("KNOWLEDGE_MIN_COVERAGE", "0.5"))


def needs_previous_turn_context(text: str) -> bool:
    """True for a follow-up too thin to search on its own ("and the price?").

    The previous caller turn used to be appended to EVERY query. On call
    d644f0ea (2026-09-28) "does Did you work with my EPOs?" was searched as
    "work epos total twenty one dot ninety nine" and lost the section that
    tells the agent to ask which EPOS; alone it retrieves it.
    """
    return len(content_words(text)) < 2


def knowledge_match_is_weak(hits: list[dict]) -> bool:
    """True when no retrieved node covers enough of the question to answer it.

    Nodes without a coverage figure (older retrieval paths) are trusted, so
    this can only ever ADD caution, never remove knowledge.
    """
    coverages = [h.get("coverage") for h in hits if isinstance(h, dict)]
    known = [float(c) for c in coverages if c is not None]
    if not known or len(known) < len(coverages):
        return False
    return max(known) < KNOWLEDGE_MIN_COVERAGE


def prepare_knowledge_evidence(hits: list[dict], query: str, *,
                               chunk_chars: int = _KB_CHUNK_CHARS,
                               total_chars: int = _KB_TOTAL_CHARS) -> dict:
    """One source/qualification/security boundary for all three voice consumers."""
    from app.services.scripts.knowledge.retrieval import render_node_answer
    from app.services.scripts.prompts.prompt_safety import scan_for_injection

    passages = []
    used = 0
    for node in hits[:_KB_MAX_CHUNKS]:
        raw = render_node_answer(node)
        heading = str(node.get("heading") or "")
        if scan_for_injection(f"{heading} {raw}"):
            continue
        available = min(chunk_chars, total_chars - used - len(heading) - 4)
        if available <= 0:
            break
        body = fit_kb_body(raw, node, available, query=query)
        if not body:
            continue
        text = f"- {heading}: {body}"
        passages.append({"node_id": str(node.get("id") or ""),
                         "version": node.get("version"), "text": text,
                         "coverage": node.get("coverage")})
        used += len(text) + 1
    status = "no_match" if not passages else (
        "weak_match" if knowledge_match_is_weak(passages) else "matched"
    )
    return {"status": status, "passages": passages,
            "text": "\n".join(p["text"] for p in passages)}


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
