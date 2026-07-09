"""Wrong-person / gatekeeper handling + graceful-exit prompt rules.

Prod transcript audit (2026-07-08): the agent asked "Hi, is this David?", the
caller said "No", and the agent went SILENT — no pivot, no redirect, dead air
until the silence monitor eventually closed the call. Root cause: the prompt
had no instruction for "wrong person answered" as its own case, so the model
had nothing to fall back on once its one scripted assumption (the named
contact) was wrong.

SOTA conversation-design findings this block encodes (Gong Labs 90k-call
study, Josh Braun, Chris Voss, Vapi prompting guide, 30MPC/SPOTIO gatekeeper
playbooks):
  - Wrong person -> PIVOT, never dead-end. Acknowledge, then ask BY NAME for a
    redirect ("is {name}, or whoever handles X, around?"). Whoever answered
    might become an advocate — don't treat them as an obstacle.
  - "Did I catch you at a bad time?" measurably costs bookings (Gong: -40%).
    Prefer stating the REASON for the call immediately (a proven +2.1x lift)
    plus permission-to-DECLINE framing ("feel free to tell me to get lost,
    but...") — Josh Braun's data shows ~4x the positive response of
    permission-to-proceed framing.
  - One question at a time, 1-2 sentence turns — never stack questions.
  - Graceful exit when the flow is clearly done or they say goodbye; a quiet
    caller is the silence monitor's job, not this prompt's.
  - Chris Voss: mirror their last few words + label the mood to de-escalate
    hesitation without sounding needy.

Rides the same trailing, high-recency slot as ``call_control_rules`` (see
end_call.py) — composer.py appends this block alongside it so the pivot rule
is fresh in context on every turn, not buried early where base-prompt rules
fade as the call grows.

Pure string builders, no I/O — trivially unit-testable.
"""
from __future__ import annotations

# Keep this SHORT — a dozen lines of rule text. Recency, not volume, is what
# makes a trailing block win.
GATEKEEPER_RULES = """\
## WRONG PERSON / GATEKEEPER — pivot, never go silent
- Not who you asked for, or "wrong number" / "who's calling for?": don't
  restart your pitch on them and don't go quiet. Acknowledge in one word,
  then ask for the right person by name (if given) or by role in the same
  breath — a shape to riff on, not recite: "Ah, no worries — is that person,
  or whoever handles the estimating and tenders, around?"
- Not free right now → ask the best time to catch them as your next step.
  Never dead-end a call on "no." Whoever answered may be your best route in
  — stay warm, they could put in a good word.

## HESITATION / SOFT OBJECTION — acknowledge, don't push
- "Who is this?" / "I'm busy" / any guarded opener: acknowledge first, state
  the REASON for your call in one plain sentence, then hand them an easy out
  ("...feel free to tell me to get lost, but I think it's worth a minute").
  Permission to DECLINE beats permission to proceed.
- Mirror their last few words or name the mood before asking anything else.
  One question, then stop. Never repeat the same ask twice — change tack or
  close warmly.

## GRACEFUL EXIT
- Clear goodbye, flow complete, or a redirect dead-ends: thank them, confirm
  whatever next step exists, and close in one warm line. A caller who's gone
  quiet is the silence monitor's job, not yours — don't chase it here.
"""


def gatekeeper_rules() -> str:
    """The composed-prompt block for wrong-person pivots, soft-objection
    handling, and graceful exits (constant; function kept for parity with
    call_control_rules/craft_reanchor and easy future per-persona tuning)."""
    return GATEKEEPER_RULES
