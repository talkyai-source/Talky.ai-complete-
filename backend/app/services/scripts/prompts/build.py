"""The single per-turn prompt assembler.

A telephony prompt is built in two lifecycles:

* **Base** — composed once at call setup by :func:`compose_prompt` (guardrails
  + persona + knowledge-precedence + final-contract). Stored on
  ``session.system_prompt`` and stable for the whole call.
* **Per-turn** — a few optional blocks are layered on that base every turn,
  because they depend on live state: the caller's latest message, the campaign
  knowledge it matches, the selected voice's accent/capabilities, and the slots
  captured so far.

This module owns the per-turn ORDER. The pipeline (turn_streamer) does the
runtime work — fetching campaign knowledge, resolving the accent, checking voice
capabilities — and hands the resolved block strings here; this function decides
only how they stack. Centralising the order means the SHAPE of the final prompt
lives in the prompts folder, next to the text it assembles, instead of being
smeared across the turn loop.

Order (a behaviour-preserving extraction of the old inline assembly — asserted
by tests):

    [base]
    + ask-AI product info      (ask-ai sessions, keyword-gated)
    + campaign knowledge       (tool addendum OR injected KB block)
    + end-session tool block   (when the model drives end-of-call)
    + audio-tags block         (expressive voices only)
    + accent block             (accent-matched fillers)
    + trailing block           (per-model addendum + compliance floor, re-asserted
                                LAST so the safety invariants keep the recency slot
                                even after the optional blocks are appended)
    then the CAPTURED facts header is prepended on top.

  (That was the order until 2026-08-13 — it is now the ``cache_friendly_order
  =False`` path, kept executable so the revert switch stays tested.)

CACHE-FRIENDLY ORDER (2026-08-13) — AND WHY IT DID NOT WORK (2026-08-17)
------------------------------------------------------------------------
⚠️ **READ THIS BEFORE TRUSTING ANYTHING BELOW ABOUT CACHING.** The reordering
described here is real, is live, and is harmless. It also delivers **nothing**
on the model this system actually runs, and the reasoning that motivated it was
wrong. Both facts are kept here rather than quietly edited away, because the
mistake is more instructive than the fix.

The original argument: the order above put the two most volatile blocks — LIVE
STATE and CAPTURED — at character 0, above an 8.5k-token prompt otherwise
identical from turn to turn. Prompt caches key on the longest common prefix
from the first token, so a block that changes every turn at the very front
means no prefix can ever match. Production seemed to bear that out: 426 of 426
voice LLM calls over 7 days returned ``cache_hit_ratio=0.00``, while the same
Groq account showed hits of up to 6656 tokens on ``llama-3.1-8b-instant``. That
comparison was read as proof the account caches fine and the prefix was at
fault — "never a model limitation".

**That conclusion was wrong, and measurement settled it.** Four days after the
reorder shipped, the voice model had taken **0 cache hits across 561,106 prompt
tokens over 81 turns**. A direct two-request probe (2026-08-17) sent two
byte-identical 2,418-token prompts back to back:

    qwen/qwen3.6-27b       first  200  prompt_tokens=2418  cached=None
                           second 200  prompt_tokens=2418  cached=None
    llama-3.1-8b-instant   404 — model_not_found

``cached`` is **None, not 0** — Groq reports no caching for this model at all.
And the model used as the control no longer exists on the account, so the
comparison that anchored the whole diagnosis was never valid in the first place.

What remains true: ``prompt_time`` p50 ~634ms is the dominant term in
time-to-first-token. What is now known: **no ordering change can recover it.**
The prompt is ~6,498 tokens at turn 0 and grows ~115 tokens per turn; the only
real levers are making the prompt smaller or moving to a model that caches.

So the rule below stays — stable-for-the-call blocks first, per-turn blocks
last — because it is correct by construction and costs nothing. Just do not
expect it to buy latency here, and do not cite the 6656-token llama hits as
evidence of anything.

    [base]  [end-session]  [audio-tags]  [accent]   <- identical all call: CACHED
    [ask-AI]  [knowledge]  [CAPTURED]  [LIVE STATE] <- per-turn
    [trailing]                                      <- safety floor keeps LAST

Two things worth stating plainly, because they look like risks and are not:

* **LIVE STATE does not get weaker, it gets stronger.** It moves from position
  0 to a few hundred tokens from the end. This codebase's own hard-won finding
  is that the trailing slot wins — that is precisely why ``trailing_block``
  exists and why the per-turn re-anchor was put there. Moving a block from the
  front of 8.5k tokens to the back is a promotion.
* **The compliance floor keeps the final word.** ``trailing_block`` is still
  the last text in the prompt, unchanged. It sits after the volatile blocks
  and so is not itself cached; at ~1k characters that is a rounding error
  against the 6.6k tokens that now are.

``VOICE_PROMPT_CACHE_ORDER=false`` restores the pre-2026-08-13 order exactly,
without a redeploy — the same instant-revert pattern used for the STT reorder.
"""
from __future__ import annotations

import os
from typing import Optional

from app.services.scripts.prompt_builder import compose_system_prompt


def _cache_friendly_default() -> bool:
    """Read at CALL time, not import time, so flipping the env var takes effect
    on a restart without also needing a redeploy — and so tests can toggle it
    with monkeypatch instead of reloading the module."""
    return os.getenv("VOICE_PROMPT_CACHE_ORDER", "true").strip().lower() not in (
        "0", "false", "no", "off",
    )


def build_turn_prompt(
    base_prompt: str,
    *,
    live_state_block: Optional[str] = None,
    ask_ai_block: Optional[str] = None,
    knowledge_block: Optional[str] = None,
    end_session_block: Optional[str] = None,
    audio_tags_block: Optional[str] = None,
    accent_block: Optional[str] = None,
    trailing_block: Optional[str] = None,
    captured_slots=None,
    cache_friendly_order: Optional[bool] = None,
) -> str:
    """Assemble the final per-turn system prompt from the base + resolved
    optional blocks. Pure: same inputs → same string, no I/O.

    Each ``*_block`` is either the already-resolved text to append, or ``None``
    / ``""`` to skip it (a falsy block is never appended). ``captured_slots``
    (a ``CallState`` or ``None``) drives the CAPTURED header.

    ``cache_friendly_order`` selects the layout (see the module docstring).
    ``None`` reads ``VOICE_PROMPT_CACHE_ORDER``, which defaults to on. Passing
    ``False`` reproduces the pre-2026-08-13 order byte for byte, which is what
    the legacy-order tests assert.
    """
    if cache_friendly_order is None:
        cache_friendly_order = _cache_friendly_default()

    if not cache_friendly_order:
        # LEGACY ORDER — kept executable, not just described, so the revert
        # switch is covered by tests rather than being a claim in a comment.
        parts = [base_prompt]
        for block in (
            ask_ai_block,
            knowledge_block,
            end_session_block,
            audio_tags_block,
            accent_block,
            trailing_block,
        ):
            if block:
                parts.append(block)
        prompt = "\n\n".join(parts)
        if captured_slots is not None:
            prompt = compose_system_prompt(prompt, captured_slots)
        if live_state_block:
            prompt = live_state_block + "\n\n" + prompt
        return prompt

    # ── stable-for-the-call prefix: this is the part the provider caches ──
    stable = [base_prompt]
    for block in (end_session_block, audio_tags_block, accent_block):
        if block:
            stable.append(block)

    # ── per-turn tail: everything that can differ between two turns of the
    # same call. Nothing here is cacheable, so it all goes after the prefix.
    volatile = [block for block in (ask_ai_block, knowledge_block) if block]
    tail = "\n\n".join(volatile)
    if captured_slots is not None:
        # compose_system_prompt PREPENDS the CAPTURED header to whatever it is
        # given. Handing it the tail (rather than the whole prompt, as the
        # legacy path does) is what moves CAPTURED out of the cached prefix
        # while keeping it above the blocks it qualifies. Returns its input
        # unchanged when no slot is filled, so an empty tail stays empty.
        tail = compose_system_prompt(tail, captured_slots)
    if live_state_block:
        tail = f"{tail}\n\n{live_state_block}" if tail else live_state_block

    parts = stable
    if tail:
        parts = parts + [tail]
    if trailing_block:
        parts = parts + [trailing_block]
    return "\n\n".join(parts)
