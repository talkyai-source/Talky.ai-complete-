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

# Context windows for models the agent may run. Extend as needed; unknown
# models fall back to the smallest safe window.
MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    "llama-3.1-8b-instant": 8192,
    "llama-3.3-70b-versatile": 131072,
    "llama-3.1-70b-versatile": 131072,
    "gemini-2.5-flash": 1_000_000,
    "gemini-3.1-flash-lite": 1_000_000,
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


def estimate_tokens(text: str) -> int:
    """Rough token estimate (≈ 4 chars/token). Good enough for budgeting."""
    return (len(text) + 3) // 4


def context_window_for(model: str | None) -> int:
    if not model:
        return _DEFAULT_CONTEXT_WINDOW
    return MODEL_CONTEXT_WINDOWS.get(model, _DEFAULT_CONTEXT_WINDOW)


def inline_budget_for(model: str | None) -> int:
    """Max tokens of knowledge we'll inline in the system prompt for this model."""
    ctx = context_window_for(model)
    available = ctx - (_RESERVED_PERSONA + _RESERVED_HISTORY + _RESERVED_RESPONSE + _RESERVED_SAFETY)
    return max(0, int(available * _INLINE_FRACTION))


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
