# call_state_tracker

**Module:** `app/services/scripts/call_state_tracker.py`
**Test file:** `tests/unit/test_call_state_tracker.py`
**Line budget:** ≤ 600 lines (actual: ~180)

## Purpose

Sticky, per-call slot store that records facts the caller has already
given — so the agent never re-asks for something it already knows.

Lives in memory on the `CallSession` as `session.captured_slots` and is
updated every user turn. Feeds the prompt composer, which injects the
facts into the LLM context on the next generation.

## Public API

```python
@dataclass(frozen=True)
class CallState:
    email: Optional[str] = None
    follow_up: Optional[str] = None
    project_type: Optional[str] = None
    bidding_active: Optional[bool] = None
    declined_count: int = 0

def update_state_from_user_turn(state: CallState, utterance: str) -> CallState
```

`update_state_from_user_turn` is pure — it returns a new `CallState`
via `dataclasses.replace` and never mutates. Fields that match in the
utterance are filled; fields that don't match are carried over from
the incoming state.

## Why this exists (design decisions)

- **Sticky by default.** Missing signals do NOT clear prior values.
  This is the core fix for the 2026-04-22 bug, where the agent asked
  for the email a second time because nothing was remembering it.
- **Deterministic, not LLM-based.** Regex + keyword heuristics run in
  microseconds. An LLM slot-extractor per turn would cost us a whole
  TTFT budget.
- **Frozen dataclass.** Immutable state means we can cheaply keep a
  reference to the "previous" slots if a future feature wants diff-
  based prompts, and we avoid a class of aliasing bugs.
- **Bidding heuristics include variants.** e.g. "multiple type of
  projects" matches the bidding-active pattern because the real caller
  phrased it that way.

## Related docs

- [2026-04-22 Agent Intelligence Plan](./2026-04-22-agent-intelligence-plan.md) — Task 2
- [spoken_email_normalizer.md](./spoken_email_normalizer.md) — used for the email slot
- [prompt_builder.md](./prompt_builder.md) — consumer of `CallState`
