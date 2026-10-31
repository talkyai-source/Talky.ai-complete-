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
## WRONG PERSON / GATEKEEPER
If the business is right but your contact is absent or unknown, acknowledge and
ask once for the relevant person or role. Do not invent a person's name or
restart the pitch. If there is no route, close politely. A person who does not
know your company may still be the correct contact; accept their correction.

## HESITATION / SOFT OBJECTION
Answer identity or purpose questions plainly. For a busy caller, ask for a
preferred callback time only if a callback route exists. Respect a refusal.

## GRACEFUL EXIT
Thank them and state only a confirmed outcome or an accurate pending request,
then follow ENDING THE CALL. Runtime handles silence; do not chase quiet turns.
"""


def gatekeeper_rules() -> str:
    """The composed-prompt block for wrong-person pivots, soft-objection
    handling, and graceful exits (constant; function kept for parity with
    call_control_rules/craft_reanchor and easy future per-persona tuning)."""
    return GATEKEEPER_RULES
