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
    then the CAPTURED facts header is prepended on top.
"""
from __future__ import annotations

from typing import Optional

from app.services.scripts.prompt_builder import compose_system_prompt


def build_turn_prompt(
    base_prompt: str,
    *,
    live_state_block: Optional[str] = None,
    ask_ai_block: Optional[str] = None,
    knowledge_block: Optional[str] = None,
    end_session_block: Optional[str] = None,
    audio_tags_block: Optional[str] = None,
    accent_block: Optional[str] = None,
    captured_slots=None,
) -> str:
    """Assemble the final per-turn system prompt from the base + resolved
    optional blocks. Pure: same inputs → same string, no I/O.

    Each ``*_block`` is either the already-resolved text to append, or ``None``
    / ``""`` to skip it (a falsy block is never appended). ``captured_slots``
    (a ``CallState`` or ``None``) drives the CAPTURED header.

    Top-of-prompt order (highest-attention first): LIVE STATE → CAPTURED → base.
    Both are per-turn facts that must dominate, so they are prepended last.
    """
    parts = [base_prompt]
    for block in (
        ask_ai_block,
        knowledge_block,
        end_session_block,
        audio_tags_block,
        accent_block,
    ):
        if block:
            parts.append(block)
    prompt = "\n\n".join(parts)
    if captured_slots is not None:
        prompt = compose_system_prompt(prompt, captured_slots)
    if live_state_block:
        prompt = live_state_block + "\n\n" + prompt
    return prompt
