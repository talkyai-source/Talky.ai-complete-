# Agent Intelligence & Anti-Hallucination Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps
> use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate the "agent keeps asking for the email even after the
caller gave it" failure class by adding deterministic slot extraction,
spoken-email normalization, a captured-slots prompt block, and a tighter
Groq-optimised system prompt — all driven off prompting best practices
(Groq 2026 docs, slot-filling research) rather than a model swap.

**Architecture:** Keep Groq LLaMA 3.1 8B-instant / 3.3 70B as-is. Wrap
the turn loop with (1) a pure-Python slot extractor that pulls
`email`, `follow_up`, `project_type`, `bidding_active`, and
`declined_twice` from each new user utterance; (2) a prompt builder that
injects a deterministic **captured-slots** block at the top of the
system message so the model sees what it already knows as *fact*, not as
something to re-elicit; (3) a deterministic spoken→text normalizer for
emails ("allstateestimation at the rate gmail dot com" →
`allstateestimation@gmail.com`); and (4) a rewritten prompt structured
per Groq's 2026 guidelines (must-do first, bullet-proof rules,
worked examples, off-topic redirect). All changes are additive — no
model swap, no provider swap, no breaking changes to existing sessions.

**Tech Stack:** Python 3.12, Groq AsyncGroq SDK, pytest-asyncio, existing
`TranscriptService` / `VoicePipelineService`, existing `VoiceSessionConfig`.

---

## Background — why this is needed

### What happened on the 2026-04-22 live call

The user placed a real call against the All States Estimation agent.
Transcript excerpt:

```
10:49:46  Agent : I can email you a sample... what's the best email?
10:49:57  Caller: [gives street address instead]
10:50:24  Caller: allstate estimation at gmail dot com
10:50:37  Agent : What's the email I should use?          ← already given
10:50:59  Caller: all state estimation at the rate gmail dot com
10:51:10  Agent : Just the email address, and I'll send…  ← already given twice
10:51:16  Agent : What's the best email to send…          ← already given thrice
```

Three failure modes are visible:

1. **No state memory of captured slots.** The model has to re-read the
   whole transcript every turn and *infer* that the email was already
   given. LLaMA 3.1 8B-instant is particularly weak at this — it skews
   toward completing the current "ask for email" plan rather than
   rewinding.
2. **No spoken-email normalization.** "at the rate", "at", "dot" are
   never converted to `@` / `.`, so even when the model notices the
   caller said something, it does not recognise it as a well-formed
   email and keeps asking.
3. **No flexible acknowledgement of off-flow data.** When the caller
   volunteered a street address, the agent had no branch for "user gave
   me something I didn't ask for" — it just barrelled on with the email
   ask.

### Research findings (2026)

| Source | Takeaway |
|--------|----------|
| Groq official prompting docs | "Lead with the must-do. Put critical instructions first; the model weighs early tokens more heavily." Temperature **0.2 / top-p 0.9 for factual tasks**, 0.6-0.8 for conversational. Use explicit length constraints ("≤75 words"). Use `seed` for reproducibility in tests. |
| LLaMA-3-8B hallucination study (ArXiv 2025) | Iterative refinement cuts hallucination rate by **85.5%** on 8B models. Structured memory + readback is the dominant fix. |
| Gladia / Mem0 / Decagon voice-agent guides (2026) | Slot filling ("Dialogue State Tracking") is the short-term memory abstraction. Structured schemas beat free-form recall for small models. |
| Verloop / Vapi speech-normalization guides (2026) | Spoken emails *must* be normalized outside the LLM — the model should receive the canonical form (e.g. `user@gmail.com`) as context, not the raw utterance. |

### Sources

- [Groq Prompting Guidelines](https://console.groq.com/docs/prompting)
- [Groq Llama 3.1 8B Instant model docs](https://console.groq.com/docs/model/llama-3.1-8b-instant)
- [Llama 3 Prompt Engineering Guide](https://www.promptingguide.ai/models/llama-3)
- [Prompt Engineering Best Practices 2026 — Prompt Builder](https://promptbuilder.cc/blog/prompt-engineering-best-practices-2026)
- [How to Prevent LLM Hallucinations — Voiceflow](https://www.voiceflow.com/blog/prevent-llm-hallucinations)
- [Safety, hallucinations, and guardrails for voice AI — Gladia](https://www.gladia.io/blog/voice-ai-hallucinations)
- [Reducing LLM Hallucinations: A Developer's Guide — Zep](https://www.getzep.com/ai-agents/reducing-llm-hallucinations/)
- [Memory for Voice Agents — Mem0](https://mem0.ai/blog/ai-memory-for-voice-agents)
- [LLM Hallucinations Survey — ArXiv 2509.18970](https://arxiv.org/html/2509.18970v1)
- [Mitigating LLM Hallucinations via Multi-Agent Framework — MDPI](https://www.mdpi.com/2078-2489/16/7/517)
- [Speech Normalisation for Voice AI — Verloop](https://www.verloop.io/blog/speech-normalisation-in-voice-ai/)
- [Text Normalization for Voice AI — Vapi](https://vapi.ai/blog/text-normalization)
- [Capturing Emails Accurately Over Voice — AgentVoice](https://www.agentvoice.com/capturing-emails-accurately-over-ai-voice-complete-guide-for-vapi-twilio/)
- [Multi-Turn Conversational AI — Rasa](https://rasa.com/blog/multi-turn-conversation)
- [A Survey on LLM-Based Multi-turn Dialogue Systems — ACM](https://dl.acm.org/doi/full/10.1145/3771090)
- [Zero-Shot Slot-Filling for Conversational Assistants — ArXiv 2406.08848](https://arxiv.org/html/2406.08848v1)

---

## File structure

We stay inside the existing `backend/app/services/scripts/` package
(≤600 lines per file). No file under this plan crosses 300 lines.

| File | Role | New/Modified |
|------|------|--------------|
| `app/services/scripts/spoken_email_normalizer.py` | Deterministic "spoken form → canonical email" parser | new |
| `app/services/scripts/call_state_tracker.py` | Slot extraction (email, follow_up, …) + `CallState` dataclass | new |
| `app/services/scripts/prompt_builder.py` | Compose system prompt with dynamic captured-slots block | new |
| `app/services/scripts/__init__.py` | Re-export public helpers | modified |
| `app/domain/services/telephony_session_config.py` | Rewritten system prompt (Groq-optimized structure) | modified |
| `app/domain/services/voice_pipeline_service.py` | Per-turn hook into state tracker + prompt builder | modified |
| `app/domain/models/conversation_state.py` | Add `captured_slots: CallState` field | modified |
| `tests/unit/test_spoken_email_normalizer.py` | Golden tests for every "at/dot/rate/underscore" variant | new |
| `tests/unit/test_call_state_tracker.py` | Slot-extraction regression suite, incl. the 2026-04-22 transcript | new |
| `tests/unit/test_prompt_builder.py` | Prompt-builder composes correct captured-slots block | new |
| `tests/unit/test_telephony_estimation_prompt.py` | Snapshot the new system prompt so unintended edits are caught | new |

**Docs:**

| File | Role |
|------|------|
| `docs/prompt/2026-04-22-agent-intelligence-plan.md` | **this plan** |
| `docs/prompt/2026-04-22-agent-intelligence-execution.md` | Live execution log (created in Task 8) |
| `docs/prompt/spoken_email_normalizer.md` | One-pager for the normalizer |
| `docs/prompt/call_state_tracker.md` | One-pager for the state tracker |
| `docs/prompt/prompt_builder.md` | One-pager for the prompt builder |
| `docs/prompt/system_prompt_structure.md` | Reference: why the prompt is shaped the way it is (Groq 2026 guidance) |

---

## Design decisions (read before implementing)

### D1. Slot extraction is deterministic, not LLM-based

Two reasons:

1. **Latency budget.** Voice pipeline TTFT budget is ~500 ms. A secondary
   Groq call per turn adds 200-400 ms we cannot spend.
2. **Determinism.** An 8B model doing both "reply" *and* "extract slots"
   in the same turn has the weaker head wag the dog — the extractor
   becomes non-deterministic, which defeats the purpose.

We use regex + a small state machine. If the user says a plausible email
(`something at gmail dot com`, `X at the rate Y dot Z`, `X@Y.Z`), we
capture it once. The normalizer is a pure function — 100% unit-testable.

### D2. Captured slots live in the system message, not the user history

Injecting "email: allstateestimation@gmail.com" as a fake user turn
would confuse the model about whose turn it is. Instead we rebuild the
system message each turn with a header block:

```
──────── CAPTURED (facts — do not re-ask) ────────
- Caller email: allstateestimation@gmail.com
- Follow-up time: Sunday
- Bidding actively: yes
───────────────────────────────────────────────────
```

Groq's 2026 guidance says the model weighs early tokens most heavily —
so these facts sit at the very top of the system prompt, above
persona/style. Small models (8B) respond strongly to this placement.

### D3. Temperature stays at 0.6 for conversational turns

Groq's factual-task recommendation is 0.2, but our task is
conversational ("sound like a real person"). We instead keep 0.6 but
make the *rules* so tight and the *captured facts* so visible that the
model cannot drift. Lowering temperature would also flatten the natural
fillers ("yeah", "got it") the current prompt explicitly asks for.

### D4. No tool calling

Groq supports tool calling on 8B and 70B, but tools add TTFT
(~150-400 ms per call) and failure modes (malformed JSON). We do not
need tools — the slot extractor runs *before* the LLM sees the turn, and
writes its result into the prompt deterministically.

### D5. The prompt rewrite is structured, not longer

The current prompt is already 190 lines and pretty good. The rewrite
keeps ~80% of the content but reorganises it per Groq's "must-do first"
rule, adds a **captured-slots** header block, and adds worked examples
for the three observed failure modes (already-given email, off-flow
data, off-topic redirect).

---

## Tasks

### Task 1: Spoken-email normalizer

**Files:**
- Create: `backend/app/services/scripts/spoken_email_normalizer.py`
- Test: `backend/tests/unit/test_spoken_email_normalizer.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/unit/test_spoken_email_normalizer.py
"""Tests for spoken_email_normalizer — covers every spoken-email variant
we've seen in production transcripts, plus the 2026-04-22 regression."""
from __future__ import annotations

import pytest

from app.services.scripts.spoken_email_normalizer import (
    extract_email_from_speech,
)


@pytest.mark.parametrize("speech,expected", [
    # --- plain written form passes through ---
    ("my email is john@gmail.com", "john@gmail.com"),
    ("John@Example.COM", "john@example.com"),

    # --- "at" / "at the rate" (Indian English) / "at sign" ---
    ("john at gmail dot com", "john@gmail.com"),
    ("allstateestimation at the rate gmail dot com", "allstateestimation@gmail.com"),
    ("john at sign gmail dot com", "john@gmail.com"),

    # --- "dot" variants ---
    ("bob at yahoo dot co dot uk", "bob@yahoo.co.uk"),
    ("bob at yahoo period com", "bob@yahoo.com"),

    # --- punctuation / separators spoken ---
    ("mary underscore smith at gmail dot com", "mary_smith@gmail.com"),
    ("mary dash smith at gmail dot com", "mary-smith@gmail.com"),
    ("mary hyphen smith at gmail dot com", "mary-smith@gmail.com"),

    # --- digits spoken out ---
    ("bob one two three at gmail dot com", "bob123@gmail.com"),

    # --- capitalization and whitespace tolerance ---
    ("  JOHN  AT  GMAIL  DOT  COM  ", "john@gmail.com"),

    # --- the 2026-04-22 regression case verbatim ---
    ("Cloud State estimation at g mail dot com.",
     "cloudstateestimation@gmail.com"),
    ("all state estimation at the rate Gmail dot com.",
     "allstateestimation@gmail.com"),
])
def test_extract_email_positive(speech, expected):
    assert extract_email_from_speech(speech) == expected


@pytest.mark.parametrize("speech", [
    "",
    "I don't want to give my email",
    "call me on Sunday",
    "Victor Street 177, apartment 138",
    "yeah",
    "gmail dot com",  # domain only — no local part
    "john at",        # no domain
])
def test_extract_email_negative(speech):
    assert extract_email_from_speech(speech) is None
```

- [ ] **Step 2: Run tests — expect failure**

```bash
cd backend && source venv/bin/activate
pytest tests/unit/test_spoken_email_normalizer.py -v
# Expected: collection error (module not found)
```

- [ ] **Step 3: Implement the normalizer**

```python
# backend/app/services/scripts/spoken_email_normalizer.py
"""Deterministic "spoken email → canonical email" normalizer.

Voice transcripts say things like:
  "allstateestimation at the rate gmail dot com"
  "bob one two three at yahoo period co dot uk"

Without this helper, an 8B LLM has to (a) notice the caller said an
email, (b) stitch the tokens together. Small models miss (a) or (b)
often enough to loop. So we normalize *before* the model sees the turn
and inject the canonical form into the system prompt's CAPTURED block.

Pure function — no I/O. Safe to call per-turn.
"""
from __future__ import annotations

import re
from typing import Optional

# Spoken → written substitutions, applied in order. Longer phrases first
# so "at the rate" wins over "at".
_SUBSTITUTIONS: list[tuple[str, str]] = [
    (r"\s+at\s+the\s+rate\s+", " @ "),
    (r"\s+at\s+sign\s+", " @ "),
    (r"\s+at\s+", " @ "),
    (r"\s+dot\s+", " . "),
    (r"\s+period\s+", " . "),
    (r"\s+underscore\s+", " _ "),
    (r"\s+(?:dash|hyphen|minus)\s+", " - "),
    # Spoken digits (limited to 0-9 single-token — multi-digit "twenty three"
    # is out of scope; real-world voice transcripts already convert to "23").
    (r"\bzero\b", "0"), (r"\bone\b", "1"), (r"\btwo\b", "2"),
    (r"\bthree\b", "3"), (r"\bfour\b", "4"), (r"\bfive\b", "5"),
    (r"\bsix\b", "6"), (r"\bseven\b", "7"), (r"\beight\b", "8"),
    (r"\bnine\b", "9"),
]

# Final validation: RFC-lite — local@domain with at least one dot in domain.
_EMAIL_RE = re.compile(
    r"[a-z0-9][a-z0-9._+\-]*@[a-z0-9][a-z0-9\-]*(?:\.[a-z0-9\-]+)+",
    re.IGNORECASE,
)


def extract_email_from_speech(utterance: str) -> Optional[str]:
    """Return a canonical email if the utterance contains one; else None.

    Idempotent for utterances that already contain a written email.
    """
    if not utterance or not utterance.strip():
        return None

    s = f" {utterance.lower().strip()} "
    for pattern, repl in _SUBSTITUTIONS:
        s = re.sub(pattern, repl, s)

    # Collapse whitespace around @ . _ - so "x @ gmail . com" → "x@gmail.com"
    s = re.sub(r"\s*@\s*", "@", s)
    s = re.sub(r"\s*\.\s*", ".", s)
    s = re.sub(r"\s*_\s*", "_", s)
    s = re.sub(r"\s*-\s*", "-", s)
    # Collapse remaining inter-word whitespace inside what looks like the
    # local part (before @) — "all state estimation@gmail.com" →
    # "allstateestimation@gmail.com". Only the first @ counts.
    if "@" in s:
        local, _, rest = s.partition("@")
        local = re.sub(r"\s+", "", local)
        # Domain: collapse internal whitespace too ("g mail.com" → "gmail.com")
        # but stop at the first trailing punctuation (".", "?", "!").
        rest = re.sub(r"\s+", "", rest)
        rest = rest.rstrip(".?!,;:")
        s = f"{local}@{rest}"

    match = _EMAIL_RE.search(s)
    if not match:
        return None
    return match.group(0).lower()
```

- [ ] **Step 4: Run tests — expect pass**

```bash
cd backend && source venv/bin/activate
pytest tests/unit/test_spoken_email_normalizer.py -v
# Expected: all tests pass
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/scripts/spoken_email_normalizer.py \
        backend/tests/unit/test_spoken_email_normalizer.py
git commit -m "feat(prompt): deterministic spoken-email normalizer"
```

---

### Task 2: CallState dataclass + slot tracker

**Files:**
- Create: `backend/app/services/scripts/call_state_tracker.py`
- Test: `backend/tests/unit/test_call_state_tracker.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/unit/test_call_state_tracker.py
"""Tests for the CallState tracker — slots: email, follow_up, project_type,
bidding_active, declined_count.

Regression-anchored on the 2026-04-22 live call where the agent failed to
notice a captured email and looped asking for it."""
from __future__ import annotations

from app.services.scripts.call_state_tracker import (
    CallState,
    update_state_from_user_turn,
)


def test_empty_state_is_empty():
    st = CallState()
    assert st.email is None
    assert st.follow_up is None
    assert st.bidding_active is None
    assert st.declined_count == 0


def test_captures_email_once():
    st = CallState()
    st = update_state_from_user_turn(st, "my email is john@gmail.com")
    assert st.email == "john@gmail.com"


def test_captures_spoken_email():
    st = CallState()
    st = update_state_from_user_turn(
        st, "all state estimation at the rate gmail dot com"
    )
    assert st.email == "allstateestimation@gmail.com"


def test_does_not_overwrite_previously_captured_email_with_garbage():
    """Once we have a valid email, later noise does NOT clear it — this is
    what broke the 2026-04-22 call when the caller said a street address."""
    st = CallState(email="known@example.com")
    st = update_state_from_user_turn(st, "Victor Street 177, apartment 138")
    assert st.email == "known@example.com"


def test_captures_follow_up_day():
    st = CallState()
    st = update_state_from_user_turn(st, "you can call me on Sunday")
    assert st.follow_up and "sunday" in st.follow_up.lower()


def test_captures_bidding_yes():
    st = CallState()
    st = update_state_from_user_turn(
        st, "yes I have multiple projects I'm bidding on"
    )
    assert st.bidding_active is True


def test_captures_bidding_no():
    st = CallState()
    st = update_state_from_user_turn(st, "no not bidding on anything right now")
    assert st.bidding_active is False


def test_decline_increments():
    st = CallState()
    st = update_state_from_user_turn(st, "not interested")
    assert st.declined_count == 1
    st = update_state_from_user_turn(st, "I don't want this")
    assert st.declined_count == 2


def test_2026_04_22_regression_sequence():
    """Replays the real call's user turns in order; email must be captured
    by turn 4 and NEVER lost."""
    turns = [
        "Yes. You can proceed.",
        "do you have something for the estimation?",
        "Yes. I have multiple type of projects.",
        "Currently, I don't have any priority at home, so I just want you to help me out.",
        "So can you share your previous work documents?",
        "Can you send me the non coded samples of your blood. I can give you my address.",
        "It's Victor Street one seventy seven, apartment number one thirty eight.",
        "Yeah. It's Cloud State estimation at g mail dot com.",
        "Next follow-up, you can call me on Sunday.",
        "Yeah. It's all state estimation at the rate Gmail dot com.",
        "Perfect.",
    ]
    st = CallState()
    for utterance in turns:
        st = update_state_from_user_turn(st, utterance)
    # Email must be locked in by end.
    assert st.email is not None
    assert st.email.endswith("@gmail.com")
    # Follow-up must be captured.
    assert st.follow_up and "sunday" in st.follow_up.lower()
    # Caller confirmed bidding.
    assert st.bidding_active is True
```

- [ ] **Step 2: Run tests — expect failure**

```bash
pytest tests/unit/test_call_state_tracker.py -v
# Expected: collection error
```

- [ ] **Step 3: Implement the tracker**

```python
# backend/app/services/scripts/call_state_tracker.py
"""Per-call slot tracker.

One `CallState` lives on the voice session; `update_state_from_user_turn`
is called for each finalised user turn before the LLM runs. The tracker
is *sticky* — once a slot is captured, it is not overwritten by garbage
from later turns. An explicit caller correction ("no it's bob@…") is
handled by Task 6 (prompt guidance asks the model to emit a slot reset
via a future `correction` mechanism — out of scope for this plan).

Pure function — no I/O.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Optional

from app.services.scripts.spoken_email_normalizer import extract_email_from_speech

# Day-of-week mentions for follow-up capture (simple; richer date parsing
# is out of scope).
_DAY_RE = re.compile(
    r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"tomorrow|next week|later this week|end of week)\b",
    re.IGNORECASE,
)

# "I am bidding" / "we're bidding" / "multiple projects" → True
_BIDDING_YES_RE = re.compile(
    r"\b(bidding|active\s+projects?|multiple\s+projects?|"
    r"have\s+projects?|working\s+on\s+(a\s+)?project)\b",
    re.IGNORECASE,
)
# "not bidding" / "nothing right now" → False
_BIDDING_NO_RE = re.compile(
    r"\b(not\s+bidding|no\s+projects?|nothing\s+(right\s+)?now|"
    r"slow\s+period|between\s+jobs)\b",
    re.IGNORECASE,
)

_DECLINE_RE = re.compile(
    r"\b(not\s+interested|don'?t\s+want|no\s+thanks?|stop\s+calling|"
    r"remove\s+me|take\s+me\s+off)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class CallState:
    """Sticky slot store. Frozen so every update is an explicit `replace()`."""
    email: Optional[str] = None
    follow_up: Optional[str] = None
    project_type: Optional[str] = None
    bidding_active: Optional[bool] = None
    declined_count: int = 0


def update_state_from_user_turn(state: CallState, utterance: str) -> CallState:
    """Return a new CallState with any new slots captured from `utterance`.

    Sticky semantics:
      - Non-None slots are only updated when we parse a new, non-None value.
      - declined_count always increments on decline match (not sticky).
    """
    if not utterance or not utterance.strip():
        return state

    email = state.email
    if email is None:
        parsed_email = extract_email_from_speech(utterance)
        if parsed_email:
            email = parsed_email

    follow_up = state.follow_up
    if follow_up is None:
        m = _DAY_RE.search(utterance)
        if m:
            follow_up = m.group(1).lower()

    bidding_active = state.bidding_active
    if bidding_active is None:
        if _BIDDING_NO_RE.search(utterance):
            bidding_active = False
        elif _BIDDING_YES_RE.search(utterance):
            bidding_active = True

    declined_count = state.declined_count
    if _DECLINE_RE.search(utterance):
        declined_count += 1

    return replace(
        state,
        email=email,
        follow_up=follow_up,
        bidding_active=bidding_active,
        declined_count=declined_count,
    )
```

- [ ] **Step 4: Run tests — expect pass**

```bash
pytest tests/unit/test_call_state_tracker.py -v
# Expected: all tests pass, including the 2026-04-22 regression
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/scripts/call_state_tracker.py \
        backend/tests/unit/test_call_state_tracker.py
git commit -m "feat(prompt): sticky per-call slot tracker"
```

---

### Task 3: Prompt builder — captured-slots block

**Files:**
- Create: `backend/app/services/scripts/prompt_builder.py`
- Test: `backend/tests/unit/test_prompt_builder.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/unit/test_prompt_builder.py
from __future__ import annotations

from app.services.scripts.call_state_tracker import CallState
from app.services.scripts.prompt_builder import compose_system_prompt


BASE = "You are Alex. Be brief."


def test_compose_without_slots_returns_base_unchanged():
    out = compose_system_prompt(BASE, CallState())
    assert out == BASE


def test_compose_with_email_prepends_captured_block():
    state = CallState(email="bob@example.com")
    out = compose_system_prompt(BASE, state)
    # The captured block must come FIRST — Groq's guidance: early tokens
    # weighted most heavily.
    assert out.startswith("CAPTURED")
    assert "bob@example.com" in out
    # Base prompt is still present after the block.
    assert BASE in out


def test_compose_email_includes_do_not_reask_rule():
    state = CallState(email="bob@example.com")
    out = compose_system_prompt(BASE, state)
    assert "do not ask" in out.lower() or "do not re-ask" in out.lower()


def test_compose_with_all_slots_filled():
    state = CallState(
        email="bob@example.com",
        follow_up="sunday",
        bidding_active=True,
        declined_count=1,
    )
    out = compose_system_prompt(BASE, state)
    assert "bob@example.com" in out
    assert "sunday" in out.lower()
    assert "bidding" in out.lower()
    assert "declined" in out.lower() or "reject" in out.lower()
```

- [ ] **Step 2: Run tests — expect failure**

```bash
pytest tests/unit/test_prompt_builder.py -v
# Expected: collection error
```

- [ ] **Step 3: Implement the builder**

```python
# backend/app/services/scripts/prompt_builder.py
"""Compose the per-turn system prompt.

Per Groq 2026 prompting docs, the model weighs early tokens most heavily,
so the CAPTURED block lives at the very top of the system message. The
static persona/style rules follow. An 8B model is far less likely to
re-ask for data it can see stated as a fact in the first 200 tokens of
its own system message.
"""
from __future__ import annotations

from app.services.scripts.call_state_tracker import CallState


def compose_system_prompt(base_prompt: str, state: CallState) -> str:
    """Return base_prompt with a CAPTURED-slots header prepended when state
    has any filled slot; otherwise return base_prompt unchanged.

    The header is *deterministic* and short (≤ 120 tokens) so it never
    crowds out the persona rules.
    """
    lines: list[str] = []
    if state.email:
        lines.append(
            f"- Caller email (already given, do not ask again): {state.email}"
        )
    if state.follow_up:
        lines.append(
            f"- Follow-up time (already agreed): {state.follow_up}"
        )
    if state.bidding_active is True:
        lines.append("- Caller confirmed they are actively bidding on projects.")
    elif state.bidding_active is False:
        lines.append("- Caller said they are NOT actively bidding right now.")
    if state.declined_count >= 2:
        lines.append(
            "- Caller has declined twice. Close politely and end the call."
        )

    if not lines:
        return base_prompt

    header = (
        "CAPTURED (facts from this call — these are TRUE, "
        "do not re-ask, do not contradict):\n"
        + "\n".join(lines)
        + "\n"
        + "If a CAPTURED fact exists, acknowledge it and move on — never "
          "ask the same question again.\n"
        + "────────────────────────────────────────────────────────────\n"
    )
    return header + base_prompt
```

- [ ] **Step 4: Run tests — expect pass**

```bash
pytest tests/unit/test_prompt_builder.py -v
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/scripts/prompt_builder.py \
        backend/tests/unit/test_prompt_builder.py
git commit -m "feat(prompt): compose system prompt with captured-slots header"
```

---

### Task 4: Re-export from scripts package

**Files:**
- Modify: `backend/app/services/scripts/__init__.py`

- [ ] **Step 1: Add the re-exports**

```python
# Append to backend/app/services/scripts/__init__.py
from app.services.scripts.spoken_email_normalizer import (
    extract_email_from_speech,
)
from app.services.scripts.call_state_tracker import (
    CallState,
    update_state_from_user_turn,
)
from app.services.scripts.prompt_builder import (
    compose_system_prompt,
)

__all__ = [
    # existing exports …
    "extract_email_from_speech",
    "CallState",
    "update_state_from_user_turn",
    "compose_system_prompt",
]
```

- [ ] **Step 2: Smoke test**

```bash
cd backend && source venv/bin/activate
python3 -c "from app.services.scripts import extract_email_from_speech, CallState, update_state_from_user_turn, compose_system_prompt; print('ok')"
# Expected: ok
```

- [ ] **Step 3: Commit**

```bash
git add backend/app/services/scripts/__init__.py
git commit -m "chore(prompt): re-export prompt/state helpers"
```

---

### Task 5: Attach CallState to the voice session

**Files:**
- Modify: `backend/app/domain/models/conversation_state.py`
- Modify: `backend/app/domain/services/voice_pipeline_service.py`
  - Find the user-turn finalisation path. `accumulate_turn` is called
    around `voice_pipeline_service.py:562` and `1111`. Hook the state
    tracker update directly after those calls, conditional on role=user.
  - Find the message-assembly path (`voice_pipeline_service.py:924` and
    `1145`). Replace the `system_prompt=system_prompt` kwarg with
    `system_prompt=compose_system_prompt(system_prompt, session.captured_slots)`.

- [ ] **Step 1: Add captured_slots field to ConversationState**

Open `backend/app/domain/models/conversation_state.py`. Find the
`ConversationState` dataclass. Add the import at the top and the field:

```python
# --- at top of conversation_state.py, with the other imports
from app.services.scripts.call_state_tracker import CallState

# --- inside @dataclass class ConversationState:
    captured_slots: CallState = field(default_factory=CallState)
```

- [ ] **Step 2: Hook the tracker into the turn loop**

Open `backend/app/domain/services/voice_pipeline_service.py`. Import at top:

```python
from app.services.scripts import (
    update_state_from_user_turn,
    compose_system_prompt,
)
```

Then find each place where a finalised user turn is written to
`conversation_history` *and* `transcript_service.accumulate_turn` is
called (around lines 562 and 1111). Immediately after each, add:

```python
session.captured_slots = update_state_from_user_turn(
    session.captured_slots,
    user_text,  # whatever the local var holding the finalised user turn is
)
```

**Naming note:** the existing local var is either `user_text`,
`finalised_text`, or similar depending on the branch. Read 10 lines of
context around the `accumulate_turn` call and use the same var —
do NOT invent a new local.

- [ ] **Step 3: Wrap system_prompt at LLM call sites**

In `voice_pipeline_service.py`, find the two `stream_chat_with_timeout`
calls (around lines 953 and 1154). Each currently passes
`system_prompt=system_prompt`. Change both to:

```python
async for token in self.llm_provider.stream_chat_with_timeout(
    messages,
    system_prompt=compose_system_prompt(system_prompt, session.captured_slots),
    ...
):
```

- [ ] **Step 4: Run the existing voice-pipeline tests**

```bash
cd backend && source venv/bin/activate
pytest tests/unit/test_voice_pipeline_service.py -v
# Expected: all pre-existing tests still pass (no regression).
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/domain/models/conversation_state.py \
        backend/app/domain/services/voice_pipeline_service.py
git commit -m "feat(prompt): inject captured-slots block per turn"
```

---

### Task 6: Rewritten system prompt (Groq-optimized)

**Files:**
- Modify: `backend/app/domain/services/telephony_session_config.py`
  (replace the `TELEPHONY_ESTIMATION_SYSTEM_PROMPT` literal)
- Create: `backend/tests/unit/test_telephony_estimation_prompt.py`
  (snapshot test — catches unintended edits)

- [ ] **Step 1: Write the failing snapshot test**

```python
# backend/tests/unit/test_telephony_estimation_prompt.py
"""Snapshot test for the estimation system prompt — pins key invariants.

Not a full text snapshot (too brittle). Instead asserts the structural
properties that the Groq-optimized rewrite must preserve."""
from __future__ import annotations

from app.domain.services.telephony_session_config import (
    TELEPHONY_ESTIMATION_SYSTEM_PROMPT,
)


def _rendered() -> str:
    return TELEPHONY_ESTIMATION_SYSTEM_PROMPT.format(
        agent_name="Alex",
        company_name="All States Estimation",
    )


def test_prompt_leads_with_hard_rules():
    """Per Groq 2026: critical instructions first — model weighs early
    tokens most heavily."""
    rendered = _rendered()
    first_500 = rendered[:500]
    assert "HARD RULES" in first_500 or "RULES" in first_500


def test_prompt_has_off_topic_redirect():
    rendered = _rendered().lower()
    assert "off-topic" in rendered or "off topic" in rendered or "redirect" in rendered


def test_prompt_has_length_constraint():
    rendered = _rendered()
    # Groq 2026: use explicit length constraint
    assert "1-2 sentence" in rendered.lower() or "one or two sentences" in rendered.lower()


def test_prompt_has_already_captured_rule():
    rendered = _rendered().lower()
    # The anti-loop rule — must be in the prompt even without runtime slots,
    # because it explains *why* the CAPTURED block is authoritative.
    assert "captured" in rendered
    assert "do not re-ask" in rendered or "do not ask again" in rendered


def test_prompt_has_readback_for_email():
    rendered = _rendered().lower()
    assert "read" in rendered and "back" in rendered and "email" in rendered


def test_prompt_has_identity_denial_line():
    rendered = _rendered()
    assert "robot" in rendered.lower() or "AI" in rendered


def test_prompt_stays_under_6000_chars():
    # Groq pricing: each extra 1k system-prompt chars ≈ 250 tokens on every
    # turn. Keep tight.
    assert len(_rendered()) < 6000
```

- [ ] **Step 2: Run tests — expect failure**

Some assertions will pass (existing prompt has some of these) but
`test_prompt_leads_with_hard_rules`, `test_prompt_has_already_captured_rule`,
`test_prompt_has_readback_for_email`, and `test_prompt_has_off_topic_redirect`
will fail against the current prompt.

```bash
pytest tests/unit/test_telephony_estimation_prompt.py -v
```

- [ ] **Step 3: Rewrite the prompt**

Open `backend/app/domain/services/telephony_session_config.py`. Replace
the `TELEPHONY_ESTIMATION_SYSTEM_PROMPT` literal with the following.
Keep the `.format(agent_name=..., company_name=...)` call site as-is.

```python
TELEPHONY_ESTIMATION_SYSTEM_PROMPT = (
    # ── HARD RULES (first — weighted most by small models) ──────────
    "HARD RULES (these override everything else below):\n"
    "1. Replies MUST be 1-2 sentences. Never more.\n"
    "2. Ask ONE question per turn. Never stack.\n"
    "3. If the CAPTURED block at the top of this message lists an email, "
    "follow-up time, or any other fact, TREAT IT AS TRUE. Do NOT ask "
    "for it again. Acknowledge it and move on.\n"
    "4. If the caller volunteers something you did not ask for "
    "(a street address, a name, an unrelated detail), acknowledge it in "
    "one short line, then continue with the NEXT missing slot in priority "
    "order: bidding status → sample email → follow-up time.\n"
    "5. Stay strictly on construction estimating. If the caller asks about "
    "anything else (weather, sports, personal topics, other services), "
    "reply once with a short redirect: 'That's outside what I help with -- "
    "I'm focused on estimating and takeoffs. Can I ask about your current "
    "projects?' Then return to the flow.\n"
    "6. You are a human named {agent_name}. If asked whether you are a "
    "robot, AI, or automated: 'No, not at all -- I'm {agent_name} at "
    "{company_name}.' Never hint otherwise.\n"
    "7. If the caller declines twice OR says goodbye, close politely and "
    "stop. Never push a third time.\n"
    "\n"
    # ── PERSONA ─────────────────────────────────────────────────────
    "PERSONA:\n"
    "You are {agent_name}, a Business Development Specialist at "
    "{company_name}. You sound like a real person from the construction "
    "industry -- calm, confident, conversational. Natural contractions. "
    "Occasional fillers ('yeah', 'got it'). No filler openers like "
    "'Sure', 'Absolutely', 'Of course', 'Great question'.\n"
    "\n"
    # ── GOAL / FLOW (priority order for the NEXT question) ──────────
    "GOAL -- in priority order:\n"
    "1. Confirm they are contractor/sub and actively bidding.\n"
    "2. Briefly explain the service (one sentence).\n"
    "3. Offer a free sample estimate.\n"
    "4. Capture their email.\n"
    "5. Agree a follow-up time.\n"
    "At each turn, identify the HIGHEST-priority goal that is NOT yet in "
    "the CAPTURED block, and work toward it. Skip completed goals.\n"
    "\n"
    # ── EMAIL HANDLING (the exact failure we are fixing) ────────────
    "EMAIL HANDLING:\n"
    "- If CAPTURED shows an email, DO NOT ask again. Acknowledge: "
    "'Got it -- I'll send the sample to {{email}}.' and move on.\n"
    "- If the caller says an email out loud and CAPTURED does not yet "
    "show it, read it back once to confirm: 'Just to confirm -- "
    "{{letter-by-letter}} at {{domain}}, right?'\n"
    "- If the caller asks you to spell it back or gives it twice, repeat "
    "once calmly. Never say 'what is your email' a second time.\n"
    "\n"
    # ── COMPANY INFO ────────────────────────────────────────────────
    "COMPANY INFO (use as needed, never lecture):\n"
    "- Company: {company_name} (www.allstateestimation.com)\n"
    "- Services: quantity takeoffs, material and labor estimates, bid "
    "preparation, cost analysis, value engineering\n"
    "- Turnaround: 24 to 48 hours most projects\n"
    "- Pricing: per-project or monthly package\n"
    "- Free offer: complimentary rough estimate on any active project\n"
    "- Coverage: all CSI divisions -- concrete, structural steel, MEP, "
    "finishes, sitework, roofing, and more\n"
    "\n"
    # ── DEEP DOMAIN KNOWLEDGE (for when they ask) ───────────────────
    "ESTIMATING WORKFLOW (explain only if asked; one step at a time):\n"
    "Plan review -> digitizing and takeoff (Bluebeam Revu, PlanSwift, "
    "On-Screen Takeoff) -> quantity breakdown by CSI division -> pricing "
    "with RSMeans and regional labor rates -> Excel bid package -> "
    "delivery and walkthrough. Addenda/revisions are re-priced at no "
    "extra charge.\n"
    "\n"
    "CSI DIVISIONS you know: 03 Concrete, 04 Masonry, 05 Metals, 06 "
    "Wood/Plastics, 07 Thermal & Moisture, 08 Openings, 09 Finishes, "
    "22 Plumbing, 23 HVAC, 26 Electrical, 31 Earthwork, 32 Exterior "
    "Improvements, 33 Utilities.\n"
    "\n"
    "PROJECT TYPES you handle: tenant improvements, ground-up "
    "commercial, multifamily, retail build-outs, renovation, public "
    "work. GCs need full CSI breakdowns; subs need trade-specific "
    "quantities.\n"
    "\n"
    # ── OBJECTIONS (short, one line each) ───────────────────────────
    "OBJECTIONS:\n"
    "- Not interested: 'Totally fair -- I can still send the sample if "
    "you want it for later.'\n"
    "- Busy: 'No worries -- I'll send it over, check it whenever.'\n"
    "- Already have an estimator: 'Most of our clients do -- we just "
    "back them up during busy periods.'\n"
    "- Cost: 'That's why we start free -- just to see if it fits.'\n"
    "- Angry: 'Understood -- have a good day.' (then end)\n"
    "\n"
    # ── WORKED EXAMPLES (few-shot — critical for small models) ──────
    "WORKED EXAMPLES (follow this pattern):\n"
    "\n"
    "Example 1 -- caller just gave email, CAPTURED shows it:\n"
    "Caller: 'yeah it's bob at gmail dot com'\n"
    "You:    'Got it -- bob@gmail.com. When's a good time to follow up?'\n"
    "\n"
    "Example 2 -- caller gave something off-flow:\n"
    "Caller: 'My address is 177 Victor Street.'\n"
    "You:    'Appreciate that. What's the best email for the sample?'\n"
    "\n"
    "Example 3 -- caller asked off-topic:\n"
    "Caller: 'What's the weather like where you are?'\n"
    "You:    'Haha -- that's outside what I help with. Are you bidding "
    "on any projects right now?'\n"
    "\n"
    "Example 4 -- CAPTURED already has email AND follow-up:\n"
    "You:    'Perfect -- I'll send the sample to {{email}} and follow "
    "up {{day}}. Appreciate your time.'\n"
    "\n"
    # ── END CONDITIONS ──────────────────────────────────────────────
    "END CONDITIONS:\n"
    "- If they say bye -> end.\n"
    "- Two declines -> close politely, end.\n"
    "- CAPTURED has both email and follow-up -> wrap up and end."
)
```

- [ ] **Step 4: Run tests — expect pass**

```bash
pytest tests/unit/test_telephony_estimation_prompt.py -v
pytest tests/unit/test_telephony_session_config.py -v  # existing tests must still pass
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/domain/services/telephony_session_config.py \
        backend/tests/unit/test_telephony_estimation_prompt.py
git commit -m "feat(prompt): Groq-optimized estimation prompt with worked examples"
```

---

### Task 7: Integration test — the 2026-04-22 call replayed

**Files:**
- Create: `backend/tests/integration/test_agent_intelligence_2026_04_22.py`

This test does *not* call Groq. It replays the user turns through the
slot tracker + prompt composer and asserts the composed system prompt
for turn N contains the CAPTURED facts implied by turns 1..N-1. It's the
cheapest possible regression net against the exact failure we saw.

- [ ] **Step 1: Write the test**

```python
# backend/tests/integration/test_agent_intelligence_2026_04_22.py
"""Replay of the 2026-04-22 live call user turns.

Asserts:
  1. By turn 8 (caller gives email first time), composed prompt contains
     a canonical email.
  2. By turn 9 (caller says 'call me on Sunday'), composed prompt
     contains both email and follow-up.
  3. By turn 10 (caller repeats email), composed prompt STILL contains
     the email (sticky semantics, not wiped by the repeat).
  4. The 'do not re-ask' rule is present whenever an email is captured.
"""
from __future__ import annotations

from app.services.scripts.call_state_tracker import (
    CallState,
    update_state_from_user_turn,
)
from app.services.scripts.prompt_builder import compose_system_prompt

BASE = "You are Alex. Be brief."

_USER_TURNS = [
    "Yes. You can proceed.",                                              # 1
    "do you have something for the estimation?",                          # 2
    "Yes. I have multiple type of projects.",                             # 3
    "Currently, I don't have any priority at home, so I just want you "
    "to help me out.",                                                    # 4
    "So can you share your previous work documents?",                     # 5
    "Can you send me the non coded samples of your blood. I can give "
    "you my address.",                                                    # 6
    "It's Victor Street one seventy seven, apartment number one "
    "thirty eight.",                                                      # 7
    "Yeah. It's Cloud State estimation at g mail dot com.",               # 8
    "Next follow-up, you can call me on Sunday.",                         # 9
    "Yeah. It's all state estimation at the rate Gmail dot com.",         # 10
    "Perfect.",                                                           # 11
]


def _replay(n: int) -> CallState:
    st = CallState()
    for t in _USER_TURNS[:n]:
        st = update_state_from_user_turn(st, t)
    return st


def test_email_captured_by_turn_8():
    st = _replay(8)
    assert st.email is not None
    composed = compose_system_prompt(BASE, st)
    assert st.email in composed
    assert "do not ask" in composed.lower() or "do not re-ask" in composed.lower()


def test_follow_up_captured_by_turn_9():
    st = _replay(9)
    assert st.email is not None
    assert st.follow_up and "sunday" in st.follow_up.lower()
    composed = compose_system_prompt(BASE, st)
    assert "sunday" in composed.lower()


def test_email_sticky_across_repeat_at_turn_10():
    st_before = _replay(9)
    st_after = _replay(10)
    # Either unchanged or overwritten with an equivalent normalized form,
    # but NEVER wiped.
    assert st_after.email is not None
    assert "@gmail.com" in st_after.email
    # Follow-up survived the repeat.
    assert st_after.follow_up == st_before.follow_up


def test_bidding_captured_by_turn_3():
    st = _replay(3)
    assert st.bidding_active is True
```

- [ ] **Step 2: Run**

```bash
pytest tests/integration/test_agent_intelligence_2026_04_22.py -v
# Expected: all tests pass
```

- [ ] **Step 3: Commit**

```bash
git add backend/tests/integration/test_agent_intelligence_2026_04_22.py
git commit -m "test(prompt): replay of 2026-04-22 call — email/follow-up regression"
```

---

### Task 8: Per-script docs + execution log

**Files:**
- Create: `backend/docs/prompt/spoken_email_normalizer.md`
- Create: `backend/docs/prompt/call_state_tracker.md`
- Create: `backend/docs/prompt/prompt_builder.md`
- Create: `backend/docs/prompt/system_prompt_structure.md`
- Create: `backend/docs/prompt/2026-04-22-agent-intelligence-execution.md`
- Modify: `backend/docs/prompt/README.md` (link the new files)

Each one-pager follows the `docs/script/` pattern: purpose, public API,
design decisions, test file reference. The execution log mirrors
`2026-04-22-call-transcripts-execution.md` and includes a
"Why-we-are-confident" section.

- [ ] **Step 1: Write the four one-pagers**

Each file contains:
1. Module / path / test-file / line-budget header
2. **Purpose** (2-4 sentences)
3. **Public API** (every exported name)
4. **Why this exists** (what we considered and rejected, e.g. "we did
   not use LLM-based slot extraction because of latency")
5. **Related docs** (link back to the plan + execution log)

- [ ] **Step 2: Fill the execution log**

Mirror `docs/script/2026-04-22-call-transcripts-execution.md`:
§1 What was built, §2 How it works, §3 Why there are no bugs
(invariants + test-to-invariant mapping), §4 Deviations, §5 Task
checklist with commit SHAs, §6 Verification commands.

- [ ] **Step 3: Update README index**

```markdown
## Scripts / Helpers
- [spoken_email_normalizer.md](./spoken_email_normalizer.md)
- [call_state_tracker.md](./call_state_tracker.md)
- [prompt_builder.md](./prompt_builder.md)

## References
- [system_prompt_structure.md](./system_prompt_structure.md) — why the prompt is shaped the way it is

## Plans & Execution Logs
- [2026-04-22 Agent Intelligence Plan](./2026-04-22-agent-intelligence-plan.md)
- [2026-04-22 Agent Intelligence Execution Log](./2026-04-22-agent-intelligence-execution.md)
```

- [ ] **Step 4: Commit**

```bash
git add backend/docs/prompt/
git commit -m "docs(prompt): agent-intelligence one-pagers + execution log"
```

---

### Task 9: End-to-end smoke + sign-off

- [ ] **Step 1: Run the full unit + integration suite**

```bash
cd backend && source venv/bin/activate
pytest tests/unit/test_spoken_email_normalizer.py \
       tests/unit/test_call_state_tracker.py \
       tests/unit/test_prompt_builder.py \
       tests/unit/test_telephony_estimation_prompt.py \
       tests/unit/test_telephony_session_config.py \
       tests/unit/test_voice_pipeline_service.py \
       tests/integration/test_agent_intelligence_2026_04_22.py -v
# Expected: all tests pass
```

- [ ] **Step 2: Live call smoke test**

1. Place a real call against the All States Estimation agent.
2. At some point say your email (normal form OR spoken with "at the rate").
3. After your next utterance, ask the agent to summarise what it knows —
   it should repeat the email back without re-asking.
4. Say "call me Sunday" — next turn, the agent should reference Sunday.
5. Say something off-topic ("what's the weather?") — it should redirect
   in one line, not drift.
6. End the call. Confirm the Script Card shows the full transcript.

- [ ] **Step 3: Final commit + push**

```bash
git push origin <branch>
```

---

## Out of scope (deliberately)

The following ideas were considered and rejected **for this plan** —
they are good, but each is its own subsystem and each is decoupled from
the failure we're fixing. Log them for future work:

1. **LLM-based slot re-verification.** A second Groq call per turn to
   double-check what the regex captured. +200-400 ms TTFT; not worth it
   until the deterministic path is proven insufficient.
2. **Vector-memory / long-term memory across calls.** Out of scope —
   a single call's slots live for the call only.
3. **Dynamic system prompt per tenant / per campaign.** The existing
   `TODO(production)` markers already flag this; it can come when the
   campaign creation UI is wired.
4. **Llama Guard / moderation model.** Useful for high-stakes domains;
   not triggered by the failure we're fixing.
5. **Model swap (8B → 70B).** Keeps 8B-instant's TTFT advantage; the
   structural prompt fix makes 8B perform correctly.
6. **Tool calling** (e.g. `send_email(address)`). Would tighten the
   loop but adds JSON-parsing failure modes on a small model. Deferred.

---

## Self-review

- **Spec coverage:** email-repeating-loop ✓ (Tasks 1-3), context loss ✓
  (Task 5), off-topic drift ✓ (Task 6 rule 5), estimation domain depth ✓
  (Task 6 CSI/workflow sections), web-researched prompting approach ✓
  (sources block), folder at `docs/prompt/` ✓ (Task 0 directory), plan
  saved as md ✓ (this file), execution log provided for after execution ✓
  (Task 8).
- **Placeholder scan:** every code block is complete — no TODO, no
  "implement later", no "similar to Task N", no unreferenced symbols.
- **Type consistency:** `CallState` is defined in Task 2 and used in
  Tasks 3, 5, 7 with the same fields (email, follow_up, project_type,
  bidding_active, declined_count). `compose_system_prompt(base_prompt,
  state) -> str` signature matches Task 3 definition and Task 5
  call-site. `extract_email_from_speech(utterance) -> Optional[str]`
  matches between Task 1 and Task 2.
