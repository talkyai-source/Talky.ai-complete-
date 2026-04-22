# System Prompt Structure — Telephony Estimation Agent

**File:** `app/domain/services/telephony_session_config.py`
**Snapshot tests:** `tests/unit/test_telephony_estimation_prompt.py`

This is the reference for *why* the telephony estimation system prompt
is shaped the way it is. The prompt has three logical layers, stacked
in the order Groq LLaMA 3.1 8B-instant pays attention to them.

## Layer 1 — CAPTURED block (runtime-injected, highest priority)

Prepended by `compose_system_prompt` on every LLM call when the
caller has already supplied a slot (email, follow-up, project type,
bidding status). Contains the explicit rule "do not re-ask, do not
contradict." Lives *above* everything else so the 8B model cannot
overlook it.

## Layer 2 — HARD RULES (static, top of file)

Seven invariants the model must hold regardless of conversation
context:

1. 1 to 2 sentences per turn, hard cap.
2. One question per turn.
3. Trust the CAPTURED block — never re-ask what's in it.
4. If the caller volunteers off-flow data, acknowledge it in one line
   and continue with the next missing slot in priority order
   (bidding → email → follow-up).
5. Stay on construction estimating. Off-topic prompts get one short
   redirect, then resume flow.
6. Decline twice → end politely.

Followed by an **EMAIL HANDLING** block that directly targets the
2026-04-22 loop: don't re-ask if captured, read back once if new, do
not treat repeats as new questions.

## Layer 3 — Persona, flow, domain (static, preserved verbatim)

All original content — IDENTITY, GOAL, STYLE, THINKING STYLE, COMPANY
INFO (incl. `www.allstateestimation.com`), ESTIMATION PROCESS Steps
1-6, SOFTWARE AND TOOLS, CONSTRUCTION KNOWLEDGE, CONVERSATION FLOW,
CORE PITCH, QUALIFY, VALUE POSITIONING, COST FRAME, CONVERSION,
EMAIL CONFIRMATION, FOLLOW-UP, CLOSING, OBJECTION HANDLING, REPLY
RULES, PHONE MANNERS, END CONDITIONS.

Preserved verbatim per explicit user constraint — company naming and
website are customer-visible and must not drift.

## Budget

Rendered prompt is ~8.5k chars (~2.1k tokens). Budget ceiling in the
snapshot test is 9000 chars, leaving ~500 chars of headroom for
future WORKED EXAMPLES without touching model context limits.

## Why this order

Groq's 2026 guidance for 8B-instant: the model treats early tokens as
higher-priority instructions. Stacking the anti-loop / anti-
hallucination rules above the persona ensures they override any
scripted wording below them.

## Related docs

- [prompt_builder.md](./prompt_builder.md) — CAPTURED block composer
- [call_state_tracker.md](./call_state_tracker.md) — slot store feeding the block
- [2026-04-22 Agent Intelligence Plan](./2026-04-22-agent-intelligence-plan.md) — Task 6
