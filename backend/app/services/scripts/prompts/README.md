# Prompts folder — the single home for the telephony agent's prompt text

This folder owns the wording **and** the assembly of the campaign telephony
agent's system prompt. If you want to change how the agent thinks, talks, or
introduces itself, it's here. Nothing else builds a telephony prompt.

## The model (3 layers + precedence)

```
SYSTEM  (guardrails.py)        universal rationality: honesty, turn-taking,
                               anti-repetition, don't re-introduce, number/
                               email readback. Identity is NOT declared here —
                               it defers to the persona.
PERSONA (personas/*.py)        exactly ONE selected: lead_gen | receptionist |
                               customer_support. Owns the identity line
                               (name + role + company), goal, stages, tone.
KNOWLEDGE (composer.py)        KNOWLEDGE_PRECEDENCE: the client's campaign
                               knowledge is the SINGLE SOURCE OF TRUTH and wins
                               on any conflict with system or persona.
```

Precedence: **client knowledge > persona > system** for *facts*; the system
layer still governs *behaviour/safety* (how to talk, honesty), which the
persona and knowledge can't override.

## Where each thing lives

| File | What it holds |
|---|---|
| `guardrails.py` | `GENERIC_GUARDRAILS` (the system/rationality layer) + `ELEVEN_V3_AUDIO_TAGS_INSTRUCTIONS` |
| `personas/lead_gen.py` · `receptionist.py` · `customer_support.py` | the three persona bodies (slot-based + knowledge-driven) + openings |
| `composer.py` | base-prompt assembly (`compose_prompt`), `KNOWLEDGE_PRECEDENCE`, `FINAL_RESPONSE_CONTRACT`, pronunciations, `brand_correction_line` |
| `build.py` | `build_turn_prompt` — the single **per-turn** assembler (block order + CAPTURED prepend) |
| `direction.py` | `inbound_directive_block` + the caller-first sentinel |
| `accent_fillers.py` | accent-matched fillers/dialect blocks |
| `agent_name_rotator.py` | per-call agent-name selection |

A telephony prompt is built in two places, both here:
**base** = `compose_prompt()` (once at call setup) → **per-turn** =
`build_turn_prompt()` (each turn, layers KB / audio-tags / accent / CAPTURED on
top).

## Intentionally NOT in this folder (and why)

Two pieces of prompt text live elsewhere on purpose — moving them here would be
worse, not cleaner:

- **End-session tool instructions** → `app/domain/services/end_session_action.py`
  (`build_end_session_tool_instructions`). It's the *protocol spec* for the
  end-of-call JSON envelope and must stay byte-in-sync with
  `parse_end_session_action()` in the same file. Keep the spec next to its
  parser.
- **Ask-AI product pitch** → `app/domain/services/ask_ai_constants.py`
  (`TALKY_PRODUCT_INFO`). That's Talky's own pricing for the public web demo —
  a separate product, not a campaign agent — and the module is deliberately
  import-free to break a circular dependency (see its docstring).

The greeting/opener lines (the literal first spoken sentence) live in
`telephony_session_config.py` with the greeting-dispatch logic — they're TTS
openers, not system-prompt instructions.

## Composed from OUTSIDE this folder — check these before changing turn shape

`compose_prompt()` appends two more prompt blocks that are **not** in this
folder, in the trailing (high-recency) slot right before the compliance floor:

| Block | Lives in |
|---|---|
| `call_control_rules()` — END_CALL sentinel + a "HOW YOU SELL" section | `app/domain/services/voice_pipeline/end_call.py` |
| `gatekeeper_rules()` — wrong-person pivot, soft objections, graceful exit | `app/domain/services/voice_pipeline/gatekeeper.py` |

They are listed here because the folder-boundary has already cost us twice:
being outside this folder means a turn-shape or opener pass done *here* misses
them, and they land LAST, so on recency they beat everything above.

**Three contradictions have been found here so far. All three are FIXED — and
all three were the same bug**: a sentence in one of those two trailing files
described a specific turn or a specific length WITHOUT saying which, so recency
made it describe every turn and it silently beat the blocks above it.

1. ~~`end_call.CALL_CONTROL_RULES` → "Under ~30 words a turn"~~ — **fixed
   2026-08-07.** At 2.8 words/sec that licensed a ~10.7s turn, almost exactly
   the 11s monologue measured in production, and it was the LAST number the
   model read about turn length. Replaced with no number at all; turn length
   now lives only in guardrails HARD RULE 2.
2. ~~`gatekeeper.GATEKEEPER_RULES` → "feel free to tell me to get lost"~~ —
   **fixed 2026-08-07.** The worst-converting opener family (Gong, 300M+
   calls: 0.9–2.15%, vs 11.18% for own-the-cold-call / permission-based). It
   reached the model on every call through this block, and
   `test_prompt_composer_direction.py` *asserted it must be present* — the
   gate was enforcing the bug. That assertion is now inverted.
3. ~~`end_call.CALL_CONTROL_RULES` → "Introduce yourself and the company
   first"~~ — **fixed 2026-08-11.** Written when the agent spoke first. Turn 1
   is now a bare pickup greeting that waits, so this unscoped sentence sat
   downstream of both guardrails' "never re-introduce" rule and `live_state`'s
   `has_introduced=True` branch — telling the model, last, to introduce itself
   again on every mid-call turn. Now scoped to the first real reply.

**The rule this yields:** any sentence in `end_call.py` or `gatekeeper.py` that
describes a SPECIFIC turn or a SPECIFIC length must name which turn — or
recency makes it apply to all of them.
