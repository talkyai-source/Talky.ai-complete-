
---

## Bug 7 — Campaign Calls: Hardcoded Generic Greeting Ignores Campaign Context

### Symptom

Every outbound campaign call opened with the same hardcoded line:

> "Hello! This is an AI assistant calling on behalf of our team. How are you doing today?"

This greeting was identical regardless of which campaign was running, what company the agent represented, or what the call's purpose was (sales, survey, appointment reminder, etc.).

### Root Cause

`_send_outbound_greeting()` in `telephony_bridge.py` had a hardcoded string literal:

```python
greeting = (
    "Hello! This is an AI assistant calling on behalf of our team. "
    "How are you doing today?"
)
```

The `voice_session.call_session` already holds the campaign's `system_prompt`, `agent_config` (with `agent_name`, `company_name`, `goal`), but none of this was used — the string was always the same.

### Fix

Removed the hardcoded string. `_send_outbound_greeting()` now calls `get_llm_response(session, "")` — an empty user input triggers the LLM to generate an opening line using the campaign's system prompt:

```python
greeting = await voice_session.pipeline.get_llm_response(session, "")
```

The system prompt in `_build_telephony_session_config()` was also updated to include an explicit `OPENING THE CALL` section that instructs the LLM on the correct structure:

```
OPENING THE CALL:
- Greet the person, introduce yourself using your agent name and company name
- In 1 sentence explain the reason for the call based on your campaign goal
- End with a short open question
- Example: "Hi [name], this is [agent] from [company]. [reason]. [question]?"
```

When the dialer worker passes a campaign-specific system prompt (with real agent name, company, and goal), the LLM will use those details. For the default telephony path, it generates a contextually appropriate greeting from the default prompt.

### Files Changed

| File | Change |
|---|---|
| `backend/app/api/v1/endpoints/telephony_bridge.py` | Replaced hardcoded `greeting` string with `get_llm_response(session, "")` in `_send_outbound_greeting()`; updated system_prompt in `_build_telephony_session_config()` with campaign-aware opening instructions |
