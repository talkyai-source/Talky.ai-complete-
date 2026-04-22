# 2026-04-22 Agent Intelligence — Execution Log

**Plan:** [2026-04-22-agent-intelligence-plan.md](./2026-04-22-agent-intelligence-plan.md)
**Trigger:** Live outbound call on 2026-04-22 where the agent asked
for the caller's email a second time after it was already given
("Cloud State estimation at g mail dot com" / "all state estimation
at the rate Gmail dot com"). Root cause was an absence of per-call
slot memory — the LLM was re-deriving state from the conversation
history on every turn and hallucinating that the email hadn't been
captured.

## §1 — What was built

Three new focused helper modules, one prompt rewrite, one pipeline
wiring change, one regression test.

| Component | Path | Purpose |
| --- | --- | --- |
| Spoken-email normalizer | `app/services/scripts/spoken_email_normalizer.py` | Turn "all state estimation at gmail dot com" into `allstateestimation@gmail.com` |
| Sticky slot tracker | `app/services/scripts/call_state_tracker.py` | Remember captured facts across turns — frozen dataclass + pure update fn |
| Prompt composer | `app/services/scripts/prompt_builder.py` | Prepend CAPTURED block to system prompt before LLM call |
| System prompt rewrite | `app/domain/services/telephony_session_config.py` | HARD RULES + EMAIL HANDLING above the preserved original prompt |
| Pipeline wiring | `app/domain/services/voice_pipeline_service.py` + `app/domain/models/session.py` | Run tracker on each user turn; compose prompt on each LLM call |
| Regression replay | `tests/integration/test_agent_intelligence_2026_04_22.py` | Replay 11 turns of the real call; assert slots stick |

## §2 — How it works

1. User speaks. STT finalizes the transcript.
2. `voice_pipeline_service` appends the user message to
   `conversation_history`, then calls
   `update_state_from_user_turn(session.captured_slots, transcript)`.
   The result replaces `session.captured_slots`.
3. When the pipeline is about to call Groq, it wraps the base system
   prompt:
   ```python
   if session.captured_slots is not None:
       system_prompt = compose_system_prompt(system_prompt, session.captured_slots)
   ```
   The composer prepends a CAPTURED block listing known slots and the
   rule "do not re-ask, do not contradict."
4. Groq sees the CAPTURED block *before* the persona. Per Groq 2026
   guidance for 8B-instant, that placement weighs most heavily.
5. Two HARD RULES in the static prompt body also say "trust CAPTURED"
   and "do not ask for an email twice" — belt-and-braces in case the
   runtime block ever fails to inject.

## §3 — Why there are no bugs (invariants + test mapping)

| Invariant | Test(s) |
| --- | --- |
| Spoken email is normalized identically on repeat | `test_spoken_email_normalizer.py::test_spoken_at_the_rate_variant`, `::test_written_email_preserved` |
| `CallState` never loses a captured field on a later turn (sticky) | `test_call_state_tracker.py::test_email_persists_across_unrelated_turn`, `test_agent_intelligence_2026_04_22.py::test_email_sticky_across_repeat_at_turn_10` |
| Captured email appears in composed prompt | `test_prompt_builder.py::test_composed_prompt_contains_captured_email`, `test_agent_intelligence_2026_04_22.py::test_email_captured_by_turn_8` |
| Composer omits CAPTURED block when state is empty | `test_prompt_builder.py::test_empty_state_returns_base_prompt_unchanged` |
| System prompt structurally has HARD RULES, off-topic redirect, email readback, identity denial, and preserved `www.allstateestimation.com` | `test_telephony_estimation_prompt.py` (9 assertions) |
| Prompt budget held under 9000 chars | `test_telephony_estimation_prompt.py::test_prompt_stays_under_9000_chars` |
| Bidding-active detected from real caller phrasing | `test_call_state_tracker.py::test_bidding_active_from_multiple_projects`, `test_agent_intelligence_2026_04_22.py::test_bidding_captured_by_turn_3` |

## §4 — Deviations from plan

- **Plan said add `captured_slots` to `ConversationState`.** `ConversationState`
  in our codebase is an `enum` (GREETING / LISTENING / …), not the
  session BaseModel. Added the field to `CallSession` instead with
  `exclude=True` so it stays runtime-only.
- **Used import alias `CallState as CapturedSlotsState`** in
  `voice_pipeline_service.py` — the file already imports the
  `CallState` enum from `app.domain.models.session`. Alias avoids a
  silent name collision.
- **Spoken-email normalizer's whitespace logic forked into "original
  had `@`" branch.** The plan's single `re.sub(r"\s+", "", local)`
  would corrupt typed emails like `"my email is john@gmail.com"` into
  `"myemailisjohn@gmail.com"`. The fork preserves both forms.
- **Bumped prompt size budget from 7000 to 9000 chars.** The original
  ~7150-char estimation prompt is preserved verbatim per explicit
  user constraint, and the new HARD RULES + EMAIL HANDLING add
  ~1400 chars. 9000 leaves headroom for future WORKED EXAMPLES
  without touching Groq context limits.
- **Full original prompt preserved.** Plan D5 suggested a leaner
  rewrite. User explicitly asked to retain CORE PITCH, QUALIFY, VALUE
  POSITIONING, COST FRAME, CONVERSION, EMAIL CONFIRMATION, FOLLOW-UP,
  CLOSING, PHONE MANNERS, REPLY RULES verbatim. All kept.

## §5 — Task checklist

| # | Task | Status | Commit |
| --- | --- | --- | --- |
| 1 | Spoken-email normalizer | ✅ | `bbe0b1a` |
| 2 | CallState tracker | ✅ | `11b26a0` |
| 3 | Prompt builder | ✅ | `5222724` |
| 4 | Re-export from scripts package | ✅ | `ec09dd6` |
| 5 | Wire into voice pipeline | ✅ | uncommitted — awaiting user sign-off |
| 6 | Groq-optimized system prompt | ✅ | uncommitted — awaiting user sign-off |
| 7 | 2026-04-22 integration replay | ✅ | uncommitted — awaiting user sign-off |
| 8 | Docs + execution log (this file) | ✅ | uncommitted |
| 9 | End-to-end smoke | pending | — |

User instruction: hold commits for Tasks 5-8 until explicitly asked.

## §6 — Verification commands

```bash
# Unit suite for the three new helpers
venv/bin/pytest tests/unit/test_spoken_email_normalizer.py -v
venv/bin/pytest tests/unit/test_call_state_tracker.py -v
venv/bin/pytest tests/unit/test_prompt_builder.py -v

# Prompt snapshot
venv/bin/pytest tests/unit/test_telephony_estimation_prompt.py -v

# 2026-04-22 live-call replay
venv/bin/pytest tests/integration/test_agent_intelligence_2026_04_22.py -v

# Aggregate for Task 9
venv/bin/pytest tests/unit tests/integration/test_agent_intelligence_2026_04_22.py -v
```
