# prompt_builder

**Module:** `app/services/scripts/prompt_builder.py`
**Test file:** `tests/unit/test_prompt_builder.py`
**Line budget:** ≤ 600 lines (actual: ~60)

## Purpose

Prepend a `CAPTURED` block to the base system prompt so the LLM sees
the facts the caller has already given at the very start of its
context — the position Groq's instruction-weighting favors most.

If `CallState` is empty, the base prompt is returned unchanged (no
"CAPTURED: (nothing)" noise wasting tokens).

## Public API

```python
def compose_system_prompt(base_prompt: str, state: CallState) -> str
```

Pure function. Single entry point used by the voice pipeline right
before every LLM call.

## Why this exists (design decisions)

- **Early tokens weigh more.** Per Groq's 2026 guidance for LLaMA 3.1
  8B-instant, instructions placed at the top of the system prompt are
  followed more reliably than ones buried in the middle. The CAPTURED
  block sits above even the persona.
- **Explicit anti-loop wording.** The header includes "do not re-ask,
  do not contradict" so the model can't treat captured facts as
  optional suggestions.
- **Separator makes the block visually distinct.** The dashed
  separator is a format the model recognizes as a section boundary,
  reducing the chance it blends the CAPTURED block into the persona.
- **No CAPTURED header when state is empty.** Avoids burning tokens on
  a block that says nothing and avoids priming the model to
  hallucinate captured facts.

## Related docs

- [2026-04-22 Agent Intelligence Plan](./2026-04-22-agent-intelligence-plan.md) — Task 3
- [system_prompt_structure.md](./system_prompt_structure.md)
- [call_state_tracker.md](./call_state_tracker.md)
