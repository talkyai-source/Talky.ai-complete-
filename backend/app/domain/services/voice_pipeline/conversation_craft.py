"""Per-turn conversation-craft re-anchor — the anti-monologue enforcement.

The 2026-07-08 transcript audit showed the agent lecturing prospects with
35-word market-context monologues even though the lead-gen persona already
teaches discover-before-pitch. Base-prompt rules FADE as the conversation
grows; the platform's own compliance-floor work proved the fix — a compact
block re-stated at the very END of the live per-turn prompt wins via recency
where a page-200 rule loses.

This block rides the same trailing slot as ``compliance_reanchor`` (see
turn_streamer), so every single turn is generated with the craft rules as the
freshest instruction in context. Deliberately tiny: recency power decays with
length, and this is spent on every turn of every call.
"""
from __future__ import annotations

# Keep this SHORT. Every line must pay per-turn rent.
CRAFT_REANCHOR = """\
## THIS TURN (how to speak, every time)
- First, react to THEIR last words: mirror their key phrase back, or name the
  mood in a few words ("sounds like you're flat out") — THEN say your piece.
- One thought, then ONE question. Under 30 words total. If you notice
  yourself explaining, stop and ask instead.
- Ask questions they'll WANT to answer — about their day-to-day ("who prices
  the tenders when you're on site?"), never a survey.
- After you ask, wait — a beat of silence is them thinking, not you failing.
- Know your one next step (their email for a sample, or a callback time THEY
  pick) and steer gently toward it.
- Fresh words every time: if they say hello again or ask you to repeat,
  compress to ONE new shorter line — repeating an earlier sentence verbatim
  is the one thing that gives you away.
- Promise only what exists: the next steps above are your ONLY offers. No
  invented specialists, callbacks, transfers, or timelines — and you already
  have their number, so never ask for it.
"""


def craft_reanchor() -> str:
    """The compact per-turn craft block (constant; function kept for parity
    with compliance_reanchor and easy future personalisation per persona)."""
    return CRAFT_REANCHOR
