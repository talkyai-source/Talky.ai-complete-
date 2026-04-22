# spoken_email_normalizer

**Module:** `app/services/scripts/spoken_email_normalizer.py`
**Test file:** `tests/unit/test_spoken_email_normalizer.py`
**Line budget:** ≤ 600 lines (actual: ~120)

## Purpose

Convert a caller's spoken (or typed) email utterance into the canonical
`local@domain.tld` form so the slot tracker can store a stable value
and the agent can read it back confidently.

Handles both styles the 2026-04-22 live call produced:

- **Typed-style:** `"my email is john@gmail.com"` → `"john@gmail.com"`
- **Spoken-style:** `"all state estimation at gmail dot com"`
  → `"allstateestimation@gmail.com"`
- **"at the rate" variants:** `"... at the rate gmail dot com"` → `"@gmail.com"`
- **Digit words:** `"seven"` → `"7"` (zero through nine)

Returns `None` when nothing resembling an email is present.

## Public API

```python
def extract_email_from_speech(utterance: str) -> Optional[str]
```

Pure function, no I/O, no state. Safe to call once per user turn from
the pipeline.

## Why this exists (design decisions)

- **No LLM round-trip.** A secondary Groq call per turn would add
  ~300ms to our ~500ms TTFT budget. Regex + token rewrite is ~0.1ms.
- **Idempotent.** Calling it on an already-clean email returns the same
  email, so the tracker can re-apply it without damage.
- **Written-vs-spoken branching.** Detecting whether the original
  utterance contained a literal `@` is cheap and tells us whether
  whitespace inside the local-part is meaningful. When the `@` was
  spoken ("at", "at the rate"), we collapse all whitespace in the
  local-part. When it was written, we keep the last whitespace-
  separated token so `"my email is john@gmail.com"` doesn't become
  `"myemailisjohn@gmail.com"`.

## Related docs

- [2026-04-22 Agent Intelligence Plan](./2026-04-22-agent-intelligence-plan.md) — Task 1
- [2026-04-22 Execution Log](./2026-04-22-agent-intelligence-execution.md)
