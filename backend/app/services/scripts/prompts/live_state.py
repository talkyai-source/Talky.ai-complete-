"""Per-turn LIVE STATE block — a fresh, top-of-prompt fact that re-anchors the
agent's identity and progress on EVERY turn.

Why this exists
---------------
"Introduce yourself once / never re-introduce / never invent a title" is a
*static* rule in the base prompt. Over a call — especially on smaller models —
attention dilutes and the model forgets it already introduced itself, so it
re-introduces and sometimes drifts its job title (observed in production
transcripts: an agent that opened as a "representative" later called itself a
"senior business consultant"). A short fact restated *every* turn is far harder
to lose than a rule buried earlier in a long prompt.

This is the per-turn anchor that fixes that — and it's provider-agnostic, so it
helps every model, the weak ones most.

Design
------
- Restates the agent's NAME every turn (the value that drifts most) and
  *references* — never re-declares — the role, so it can't introduce a second,
  competing role title (the persona body is the one place the role is defined).
- ``has_introduced`` is the key signal: once the agent has given its real
  opening (set in turn_runner after the first LLM reply), this flips and tells
  the model not to introduce itself again.

Pure function, no I/O. Prepended at the very TOP of the per-turn prompt by
``build_turn_prompt`` (above the CAPTURED facts) so it sits in the
highest-attention position.
"""
from __future__ import annotations


def build_live_state_block(
    *,
    agent_name: str,
    company_name: str,
    has_introduced: bool = False,
    time_of_day_line: str = "",
    structured_state_block: str = "",
) -> str:
    """Return the LIVE STATE block, or '' when there's no identity to anchor.

    ``time_of_day_line`` — optional pre-built line stating the CALLEE's local
    time-of-day so a "morning/afternoon/evening" greeting matches their clock
    (see voice_pipeline.time_of_day). Empty when the timezone is unknown."""
    name = (agent_name or "").strip()
    company = (company_name or "").strip()
    structured = (structured_state_block or "").strip()
    if not name and not company and not time_of_day_line and not structured:
        return ""

    # Phrased as a status line ("You're on this call as …") rather than a fresh
    # "You are …" declaration, so the literal identity is declared once (in the
    # persona) while LIVE STATE still re-anchors the name every turn.
    who = f"You're on this call as {name}" if name else "You're on this call"
    if company:
        who += f", for {company}"
    lines = [
        f"- {who}. Keep this exact name for the whole call, and keep the same "
        "role you opened with — never switch to a different name or job title.",
    ]
    if has_introduced:
        lines.append(
            "- You have ALREADY introduced yourself and said why you're calling. "
            "Do NOT introduce yourself again, restate your name/role, or "
            "re-explain your reason for calling — just continue naturally from "
            "where the conversation is now."
        )
    else:
        # OPENER BUDGET (2026-08-07): "one or two sentences" was the loosest
        # number anywhere in the prompt for the same turn — and this block is
        # PREPENDED above everything else on every turn, so it had the top
        # attention slot. lead_gen STAGE 1 and the inbound directive both say
        # "one breath, under twenty words"; all three now agree. Divergent
        # numbers for one turn is how a 7.7s greeting survived a prompt that
        # already said "keep it short".
        # OPENER WORDING (2026-08-13): the budget alone was not enough. A
        # production call opened with "hope I've not caught you at a bad
        # time?" — the worst-converting opener family measured, and the exact
        # move the persona was rewritten to replace. The rewrite landed in
        # lead_gen's STAGE 1, mid-prompt, stated as a CONCEPT with one
        # positive example and (deliberately, to avoid priming) no negative
        # one. Nothing in the four trailing blocks mentions it, and THIS
        # block — the last thing read before turn 0 is generated — carried
        # only the length rule. So on the highest-stakes sentence of the
        # call, the freshest instruction was silent on the one distinction
        # that decides it.
        #
        # The fix is the pattern that made "never re-introduce" stick: state
        # it early, and carry it forward HERE every turn. Phrased as the
        # positive move plus what to open ON, so it constrains without
        # quoting the banned sentence.
        lines.append(
            "- You have not introduced yourself yet — give your short opening "
            "this turn: who you are and why you're calling, in one breath, "
            "under twenty words. Then stop and let them answer."
        )
        lines.append(
            "- Open on your REASON for calling, never on their availability. "
            "A forward ask like \"got a minute?\" is fine; anything that asks "
            "whether you have caught them at an awkward or inconvenient "
            "moment is the worst-performing opening there is — it invites a "
            "no before they know what you want."
        )
    # ANSWER BEFORE YOU ASK (2026-08-13). A production call escalated from
    # confusion to profanity in six turns because the caller asked "why are
    # you calling me" THREE times and never got a straight answer: turn 1
    # re-introduced and appended a question, turn 2 restated vaguely and
    # appended a question, turn 4 asked HIM to qualify himself while his own
    # question was still open.
    #
    # Nothing in the prompt caused that on purpose — it is what happens when
    # FOUR separate blocks teach "acknowledge, then ask" (HARD RULE 3,
    # COMMUNICATION_PRINCIPLES, the persona's HOW YOU SOUND, and
    # FINAL_RESPONSE_CONTRACT in the trailing slot) and exactly ONE
    # mid-prompt line says to answer a direct question outright. By recency
    # and by repetition the ask-reflex wins, so the model kept advancing its
    # own agenda over an open question.
    #
    # This is the missing priority rule, in the one block that is re-read on
    # every single turn. It deliberately does NOT list example phrasings: the
    # last attempt at this failed because its three examples ("what makes you
    # different?", "why you?", "what is this?") did not match what the caller
    # actually said, and the model would not generalise.
    lines.append(
        "- Turn priority: honor a clear stop or opt-out request and any urgent "
        "safety issue first. Otherwise resolve an unanswered direct question, "
        "then handle wrong-person, identity, or timing issues; only after that "
        "continue discovery, qualification, or your next step."
    )
    lines.append(
        "- If they asked you something and you have not answered it, ANSWER "
        "IT FIRST — plainly, in one sentence, and add no question of your "
        "own that turn. An unanswered question outranks your next step, your "
        "qualifying, and any stage you are working through. If they ask the "
        "same thing again, you did not answer it: say the plain answer with "
        "nothing attached."
    )
    if time_of_day_line:
        lines.append(time_of_day_line)
    if structured:
        lines.append(structured)
    if not lines:
        return ""
    return (
        "LIVE STATE — current call status, read this before you reply:\n"
        + "\n".join(lines)
    )
