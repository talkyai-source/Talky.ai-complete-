"""Model-aware knowledge-injection budget (vectorless RAG, P1).

Decides how a campaign's knowledge is fed to the agent, derived from the
*model's* context window — never a hardcoded token cap (plan §13A):

  inline        whole tree fits the budget          → inlined once at session build
  map_retrieve  tree skeleton fits, detail doesn't   → skeleton inlined + per-turn FTS
  retrieve      too big for either                   → per-turn FTS only (size-independent)

Pure + stateless → unit-testable. Token counts are estimated (chars/4); exact
tokenisation isn't needed for a budgeting decision.
"""
from __future__ import annotations

import os

# LEGACY table, kept only so an old stored model id still resolves.
#
# Every id below had been removed from the product by 2026-09-07 (see
# app/domain/models/ai_config.py: the menu is Cerebras gpt-oss-120b primary and
# Groq openai/gpt-oss-20b fallback). Duplicating the numbers here is what let
# this rot silently: the live models were absent, so every campaign fell
# through to the 8k default and was budgeted as if it ran an 8k model.
# context_window_for() now asks the canonical menu FIRST and only falls back
# here, so this table can never again be the reason a current model is unknown.
MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    "llama-3.1-8b-instant": 8192,
    "llama-3.3-70b-versatile": 131072,
    "llama-3.1-70b-versatile": 131072,
    "gemini-2.5-flash": 1_000_000,
    "gemini-3.1-flash-lite": 1_000_000,
    "gemini-3.5-flash": 1_000_000,
}
_DEFAULT_CONTEXT_WINDOW = 8192

# Tokens we must leave free in the window for the rest of the turn.
_RESERVED_PERSONA = 600
_RESERVED_HISTORY = 2600     # ~20 user/assistant pairs (matches _MAX_HISTORY_PAIRS)
_RESERVED_RESPONSE = 400
_RESERVED_SAFETY = 200
# Fraction of the remaining window we'll spend on inlined knowledge.
_INLINE_FRACTION = 0.6
# map_retrieve covers KBs up to this multiple of the inline budget.
_MAP_RETRIEVE_MULTIPLE = 4
# The renderer that bakes knowledge into the system prompt truncates at this
# many characters (retrieval.compact_tree / compact_tree_from_nodes both take
# it as their max_chars default). It is the real ceiling on inlining, so the
# budget below is derived from it rather than invented.
INLINE_BAKE_MAX_CHARS = 12000


def tokens_for_chars(chars: int) -> int:
    """Characters to tokens at the one ratio this module budgets with."""
    return (max(0, int(chars)) + 3) // 4


def estimate_tokens(text: str) -> int:
    """Rough token estimate (≈ 4 chars/token). Good enough for budgeting."""
    return tokens_for_chars(len(text))


def _declared_context_window(model: str) -> int | None:
    """Context window from the canonical model menu, or None if undeclared.

    ``app/domain/models/ai_config.py`` is where the product declares which
    models it runs; asking it keeps ONE source of truth. A menu entry may leave
    ``context_window`` unset on purpose when the provider does not publish the
    number -- that returns None here, and the caller keeps the conservative
    default rather than inventing a window.

    Imported lazily and defensively so a budgeting decision can never be the
    thing that breaks on an import cycle or a menu refactor.
    """
    try:
        from app.domain.models.ai_config import (
            CEREBRAS_MODELS,
            GEMINI_MODELS,
            GROQ_MODELS,
        )
    except Exception:  # pragma: no cover - defensive
        return None

    wanted = model.strip().lower()
    bare = wanted.rsplit("/", 1)[-1]
    for entry in (*GROQ_MODELS, *GEMINI_MODELS, *CEREBRAS_MODELS):
        entry_id = str(getattr(entry, "id", "") or "").strip().lower()
        if not entry_id:
            continue
        if wanted == entry_id or bare == entry_id.rsplit("/", 1)[-1]:
            window = getattr(entry, "context_window", None)
            return int(window) if window else None
    return None


def context_window_for(model: str | None) -> int:
    if not model:
        return _DEFAULT_CONTEXT_WINDOW
    declared = _declared_context_window(model)
    if declared:
        return declared
    bare = model.strip().rsplit("/", 1)[-1]
    return MODEL_CONTEXT_WINDOWS.get(
        model, MODEL_CONTEXT_WINDOWS.get(bare, _DEFAULT_CONTEXT_WINDOW)
    )


def max_inline_tokens() -> int:
    """Ceiling on inlined knowledge: what the bake can actually emit.

    This is NOT a latency knob, and it does not shrink any prompt -- the bake
    was always truncated at INLINE_BAKE_MAX_CHARS. It exists because choosing
    "inline" for a knowledge base larger than the bake can render is a silent
    lie: inline is the ONE mode that runs no per-turn retrieval, so everything
    past the truncation point is dropped and nothing goes looking for it again.
    A 131k-token window would otherwise mark a 300 KB knowledge base "inline"
    and then quietly serve the first 12 KB of it.

    Above this, map_retrieve and retrieve both keep per-turn search, so the
    overflow stays reachable. Read per call so it is tunable without a redeploy
    and monkeypatchable in tests.
    """
    default = tokens_for_chars(INLINE_BAKE_MAX_CHARS)
    raw = os.getenv("KNOWLEDGE_MAX_INLINE_TOKENS", "").strip()
    if raw:
        try:
            parsed = int(raw)
        except ValueError:
            return default
        if parsed > 0:
            return parsed
    return default


def inline_budget_for(model: str | None) -> int:
    """Max tokens of knowledge we'll inline in the system prompt for this model."""
    ctx = context_window_for(model)
    available = ctx - (_RESERVED_PERSONA + _RESERVED_HISTORY + _RESERVED_RESPONSE + _RESERVED_SAFETY)
    return max(0, min(int(available * _INLINE_FRACTION), max_inline_tokens()))


def choose_mode(total_tokens: int, model: str | None) -> str:
    """Pick none|inline|map_retrieve|retrieve for a KB of `total_tokens`."""
    if total_tokens <= 0:
        return "none"
    budget = inline_budget_for(model)
    if total_tokens <= budget:
        return "inline"
    if total_tokens <= budget * _MAP_RETRIEVE_MULTIPLE:
        return "map_retrieve"
    return "retrieve"
