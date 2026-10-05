"""Shared source-only passage preparation and per-turn knowledge budgets.

Traditional injection, tool lookup and native lookup use the same evidence
boundary. Complete relevant passages must fit; generated enrichment cannot
replace source facts or a qualifier removed by truncation.
"""
from __future__ import annotations

import math
import os
import re
from app.services.scripts.knowledge.passages import content_words, select_passage

_KB_MAX_CHUNKS = int(os.getenv("VOICE_KB_MAX_CHUNKS", "3"))
# Preserve existing configured budgets; the preparer selects whole passages.
_KB_CHUNK_CHARS = int(os.getenv("VOICE_KB_CHUNK_CHARS", "600"))
_KB_TOTAL_CHARS = int(os.getenv("VOICE_KB_TOTAL_CHARS", "2000"))

# Hard cap on the per-turn knowledge lookup so a slow/contended DB can never add
# more than this to time-to-first-token. On timeout we skip knowledge for the
# turn; missing knowledge remains unavailable factual evidence. Retrieval is awaited
# SERIALLY before the LLM iterator starts, so this budget sits directly on the
# first-token critical path: keep it tight, but large enough to avoid silent
# "timed-out → no knowledge" turns.
_KNOWLEDGE_RETRIEVE_TIMEOUT_S = float(os.getenv("KNOWLEDGE_RETRIEVE_TIMEOUT_MS", "500")) / 1000.0


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


def _coverage(value) -> float | None:
    """Unknown/malformed relevance is not proof that a source answers a query."""
    if value is None or isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return value if math.isfinite(value) and 0 <= value <= 1 else None


def knowledge_match_is_weak(hits: list[dict]) -> bool:
    """True unless at least one hit has measured sufficient query coverage.

    Both supported retrievers return coverage. Missing legacy metadata remains
    usable for cautious diagnostics, never an implicit factual authorization.
    """
    return not any(
        (coverage := _coverage(hit.get("coverage"))) is not None
        and coverage >= KNOWLEDGE_MIN_COVERAGE
        for hit in hits if isinstance(hit, dict)
    )


def prepare_knowledge_evidence(hits: list[dict], query: str, *,
                               chunk_chars: int = _KB_CHUNK_CHARS,
                               total_chars: int = _KB_TOTAL_CHARS) -> dict:
    """One source/qualification/security boundary for all three voice consumers."""
    from app.services.scripts.prompts.prompt_safety import scan_for_injection

    passages = []
    used = 0
    for node in hits[:_KB_MAX_CHUNKS]:
        if not isinstance(node, dict):
            continue
        # Summary/voice_answer are generated phrasing, not source evidence.
        raw = str(node.get("content") or "").strip()
        if not raw:
            continue
        heading = str(node.get("heading") or "")
        if scan_for_injection(f"{heading} {raw}"):
            continue
        available = min(chunk_chars, total_chars - used - len(heading) - 4)
        if available <= 0:
            break
        body = select_passage(raw, query, available)
        if not body:
            continue
        text = f"- {heading}: {body}"
        passages.append({"node_id": str(node.get("id") or ""),
                         "version": node.get("version"),
                         "source_id": str(node["source_id"]) if node.get("source_id") is not None else None,
                         "source_version": node.get("source_version"),
                         "text": text,
                         "coverage": _coverage(node.get("coverage"))})
        used += len(text) + 1
    strong = [p for p in passages if p["coverage"] is not None
              and p["coverage"] >= KNOWLEDGE_MIN_COVERAGE]
    if strong:
        # One relevant passage cannot authorize every loosely related hit's
        # prices. Consumers use precisely these passages as financial evidence.
        passages = strong
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
