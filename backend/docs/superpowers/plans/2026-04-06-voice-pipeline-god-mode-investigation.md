# Voice Pipeline God Mode Investigation & Fix Plan
**Date:** 2026-04-06
**Status:** Ready for implementation
**Investigator:** Claude Sonnet 4.6 — systematic root-cause analysis

---

## Investigation Summary

User reported that previously-claimed fixes are still producing broken behavior:
- "Sure thing!" is still spoken aloud on every turn
- Latency tracking shows N/A or all-zeros for interrupted turns

All three files were read in full. Root causes confirmed from code, not from logs or summaries.

---

## Root Cause 1 — "Sure thing!" Still Spoken

### File: `backend/app/domain/services/llm_guardrails.py`

**Current pattern (line 269):**
```python
r'^Sure[!,]\s+',
```

**Why it fails:**
- `[!,]` is a character class matching ONLY `!` or `,`
- "Sure thing!" has a **space** after "Sure"
- `^Sure[!,]\s+` never matches "Sure thing!" — it passes through untouched

**Second root cause — System prompt actively encourages fillers:**
File: `backend/app/domain/services/ask_ai_session_config.py` line 59:
> "Sound natural and conversational, like talking to a friend"

GPT-OSS-120b uses "Sure thing!", "Absolutely!", "Great question!" as natural openers because the prompt encourages this style. There is NO instruction to avoid filler openers.

---

## Root Cause 2 — Latency Tracking Wipes Mid-Turn (Race Condition)

### File: `backend/app/domain/services/voice_pipeline_service.py` lines 381–384

```python
current_metrics = self.latency_tracker.get_metrics(call_id)
if not current_metrics or current_metrics.turn_id != session.turn_id:
    self.latency_tracker.start_turn(call_id, session.turn_id)   # ← WIPES ALL DATA
    self.latency_tracker.mark_listening_start(call_id)
```

**Why it fires mid-turn:**
In `handle_turn_end` (line 585–586):
```python
session.turn_id += 1     # incremented BEFORE _run_turn is created
turn_task = asyncio.create_task(self._run_turn(...))
```

While `_run_turn` for Turn N is executing, `session.turn_id = N+1`.
If a barge-in occurs and any STT transcript arrives:
- tracker has `turn_id = N`
- `session.turn_id = N+1`
- Mismatch → `start_turn(call_id, N+1)` → **wipes Turn N metrics**
- `_run_turn` then calls `log_metrics` → logs empty metrics → N/A

---

## Root Cause 3 — `start_turn` Pre-populates `speech_end_time` Incorrectly

### File: `backend/app/domain/services/latency_tracker.py` lines 174–181

```python
metrics = LatencyMetrics(
    call_id=call_id,
    turn_id=turn_id,
    listening_start_time=datetime.utcnow(),
    speech_end_time=datetime.utcnow()    # ← WRONG: set to "now" at listening start
)
```

`total_latency_ms` = `audio_start_time - speech_end_time`.
Since `speech_end_time` is set to the start of the listening phase, `total_latency_ms` includes
all the time the user was actually talking — making it meaningless.

---

## Fix Plan

### Task 1 — Fix filler stripping + system prompt (llm_guardrails.py + ask_ai_session_config.py)

**TDD approach: write failing tests first, then fix.**

#### 1a. Add failing tests to `backend/tests/unit/test_llm_guardrails.py`

Add a new `TestFillerStripExtended` class:
```python
class TestFillerStripExtended:
    """Verify multi-word filler openers are stripped."""

    @pytest.fixture
    def guardrails(self):
        return LLMGuardrails()

    def test_sure_thing_exclamation(self, guardrails):
        result = guardrails.clean_response("Sure thing! Here are our plans.")
        assert not result.startswith("Sure thing")
        assert "Here are our plans." in result

    def test_sure_thing_comma(self, guardrails):
        result = guardrails.clean_response("Sure thing, here are our plans.")
        assert not result.startswith("Sure thing")

    def test_sure_thing_no_punctuation(self, guardrails):
        result = guardrails.clean_response("Sure thing here are our plans.")
        assert not result.startswith("Sure thing")

    def test_absolutely(self, guardrails):
        result = guardrails.clean_response("Absolutely! Talky lets you automate calls.")
        assert not result.startswith("Absolutely")

    def test_certainly(self, guardrails):
        result = guardrails.clean_response("Certainly! Here is the pricing.")
        assert not result.startswith("Certainly")

    def test_definitely(self, guardrails):
        result = guardrails.clean_response("Definitely! We offer three plans.")
        assert not result.startswith("Definitely")

    def test_great_comma(self, guardrails):
        result = guardrails.clean_response("Great, I can help with that!")
        assert not result.startswith("Great,")

    def test_great_exclamation_opener(self, guardrails):
        result = guardrails.clean_response("Great! We have three plans.")
        assert not result.startswith("Great!")

    def test_no_problem(self, guardrails):
        result = guardrails.clean_response("No problem! Here is the info.")
        assert not result.startswith("No problem")

    def test_happy_to_help(self, guardrails):
        result = guardrails.clean_response("Happy to help! Talky is a voice AI platform.")
        assert not result.startswith("Happy to help")

    def test_sure_preserves_content(self, guardrails):
        """The substantive answer must survive stripping."""
        result = guardrails.clean_response("Sure thing! Talky lets you automate calls.")
        assert "Talky lets you automate calls" in result

    def test_already_direct_not_affected(self, guardrails):
        """Direct answers without filler must not be mutilated."""
        original = "Talky offers three plans: Basic, Professional, and Enterprise."
        result = guardrails.clean_response(original)
        assert result == original
```

#### 1b. Fix `llm_guardrails.py` filler_starts list

Replace the `filler_starts` list (lines 263–271) with:
```python
filler_starts = [
    r'^(Well,?\s+)',
    r'^(So,?\s+)',
    r'^(Actually,?\s+)',
    r'^(Okay,?\s+)',
    r'^(Alright,?\s+)',
    r'^Sure[!,]?\s+',          # "Sure!" / "Sure," / "Sure " (bare)
    r'^Sure thing[!,]?\s*',    # "Sure thing!" / "Sure thing," / "Sure thing"
    r'^(Of course[!,]?\s+)',
    r'^(Absolutely[!,]?\s+)',
    r'^(Certainly[!,]?\s+)',
    r'^(Definitely[!,]?\s+)',
    r'^(Great[!,]\s+)',        # "Great!" or "Great," as opener
    r'^(No problem[!,]?\s+)',
    r'^(Happy to help[!,]?\s+)',
]
```

**Note:** Apply in a loop with `re.IGNORECASE` (already done — the existing loop uses that flag).

#### 1c. Fix `ask_ai_session_config.py` system prompt

Add one line to `ASK_AI_SYSTEM_PROMPT` under `## Important Guidelines`:
```
- Never start your response with filler openers like 'Sure thing', 'Absolutely', 'Certainly', 'Great', 'Of course' — answer directly
```

---

### Task 2 — Fix latency tracker race condition (voice_pipeline_service.py)

**TDD approach: write failing test first.**

#### 2a. Add failing test to `backend/tests/unit/test_latency_tracker.py` (create if not exists)

```python
def test_start_turn_does_not_prepopulate_speech_end():
    tracker = LatencyTracker()
    tracker.start_turn("call1", 1)
    metrics = tracker.get_metrics("call1")
    assert metrics.speech_end_time is None, (
        "start_turn must not pre-set speech_end_time — "
        "it should only be set via mark_speech_end"
    )
```

#### 2b. Fix `latency_tracker.py` — remove `speech_end_time` from `start_turn`

Current (lines 174–181):
```python
metrics = LatencyMetrics(
    call_id=call_id,
    turn_id=turn_id,
    listening_start_time=datetime.utcnow(),
    speech_end_time=datetime.utcnow()    # ← REMOVE THIS LINE
)
```

After fix:
```python
metrics = LatencyMetrics(
    call_id=call_id,
    turn_id=turn_id,
    listening_start_time=datetime.utcnow(),
)
```

#### 2c. Fix the race condition in `voice_pipeline_service.py` — guard `handle_transcript`

The root cause is that `handle_transcript` resets the tracker when `session.turn_id` has already been incremented past the current processing turn.

Fix: only reset the tracker in `handle_transcript` if the LLM is NOT actively processing.

Current (lines 381–384):
```python
current_metrics = self.latency_tracker.get_metrics(call_id)
if not current_metrics or current_metrics.turn_id != session.turn_id:
    self.latency_tracker.start_turn(call_id, session.turn_id)
    self.latency_tracker.mark_listening_start(call_id)
```

After fix:
```python
current_metrics = self.latency_tracker.get_metrics(call_id)
# Only reset tracker if LLM is not actively processing this turn.
# session.turn_id is pre-incremented before _run_turn is created, so
# a mismatch during active LLM/TTS processing is expected — not a bug.
if (not current_metrics or current_metrics.turn_id != session.turn_id) and not session.llm_active:
    self.latency_tracker.start_turn(call_id, session.turn_id)
    self.latency_tracker.mark_listening_start(call_id)
```

#### 2d. Ensure `log_metrics` is called even on cancelled turns

Currently `log_metrics` (line 724) is only called in the try block. If `CancelledError` fires during TTS, it's skipped.

Add a call to `log_metrics` in the `except CancelledError` block before re-raising, to capture partial metrics:

```python
except asyncio.CancelledError:
    # Log whatever partial latency data we have before wiping
    self.latency_tracker.log_metrics(call_id)   # ← ADD THIS
    if (
        _user_msg_appended
        and session.conversation_history
        and session.conversation_history[-1] is user_message
    ):
        session.conversation_history.pop()
        logger.info("user_message_rolled_back_on_cancel", ...)
    ...
    raise
```

---

## Implementation Order

1. **Task 1** (filler stripping) — 2 files, clear scope, tests first
2. **Task 2** (latency tracking) — 2 files, tests first

Each task is independent. Both can be validated with unit tests before commit.

---

## Test Verification Commands

```bash
cd /home/ai-lab/Desktop/Talky.ai-complete-/backend

# Task 1 — filler tests
python -m pytest tests/unit/test_llm_guardrails.py -v -k "TestFillerStrip" 2>&1 | tail -30

# Task 2 — latency tracker tests
python -m pytest tests/unit/test_latency_tracker.py -v 2>&1 | tail -30

# Full suite
python -m pytest tests/unit/ -v 2>&1 | tail -40
```

---

## Success Criteria

| Issue | Success Signal |
|-------|---------------|
| "Sure thing!" stripped | `clean_response("Sure thing! Talky…")` returns `"Talky…"` |
| LLM doesn't generate fillers | GPT-OSS returns direct answers, no filler openers |
| Latency not wiped mid-turn | Turn N latency shows real values even after barge-in |
| `total_latency_ms` accurate | Measures from actual speech-end, not listening-start |
| Cancelled turns still logged | Barge-in turns show partial latency in logs, not N/A |
