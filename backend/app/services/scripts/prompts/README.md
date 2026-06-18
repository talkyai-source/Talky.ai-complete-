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
