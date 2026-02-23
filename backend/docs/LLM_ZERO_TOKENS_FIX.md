# LLM Zero Tokens Issue - Root Cause & Fix

## Problem Summary

The backend was experiencing intermittent issues where Groq LLM returned zero tokens, causing the voice pipeline to use fallback responses repeatedly.

## Root Cause

The issue was caused by **empty assistant messages being added to the conversation history**:

1. When LLM returned zero tokens (for any reason), `response_text` was empty
2. The code still added an empty `Message(role=ASSISTANT, content="")` to `conversation_history`
3. On the next turn, these empty messages were sent to Groq
4. Groq's API doesn't handle empty messages well, leading to more zero-token responses
5. This created a cascading failure where subsequent LLM calls also failed

### Evidence from Logs

```
[LLM DEBUG] Messages: [
  "role=<MessageRole.USER: 'user'> content='Hello. Ca",
  "role=<MessageRole.ASSISTANT: 'assistant'> content=",  # <-- EMPTY!
  'role=<MessageRole.USER: \'user\'> content="Okay. Wha'
]
```

## Solution

### 1. Filter Empty Messages in Groq Provider (`backend/app/infrastructure/llm/groq.py`)

Added validation to skip empty messages before sending to Groq:

```python
# User/Assistant channels: Conversation history
for msg in messages:
    # Skip empty messages - they can cause issues with the LLM
    if not msg.content or not msg.content.strip():
        logger.warning(f"[GROQ DEBUG] Skipping empty {msg.role.value} message in conversation history")
        continue
        
    groq_messages.append({
        "role": msg.role.value,
        "content": msg.content
    })
```

### 2. Prevent Empty Messages from Being Added (`backend/app/domain/services/voice_pipeline_service.py`)

Only add assistant messages to conversation history if they contain actual content:

```python
# Add assistant message to conversation (only if non-empty)
# Empty responses can confuse the LLM on subsequent turns
if response_text and response_text.strip():
    assistant_message = Message(
        role=MessageRole.ASSISTANT,
        content=response_text
    )
    session.conversation_history.append(assistant_message)
else:
    logger.warning(
        f"Skipping empty assistant message for call {call_id} - "
        "this prevents conversation history corruption"
    )
```

### 3. Enhanced Logging

Added detailed message logging in Groq provider to help diagnose future issues:

```python
# Log each message for debugging (truncated)
for i, msg in enumerate(groq_messages):
    content_preview = msg['content'][:100] if msg['content'] else '<EMPTY>'
    logger.info(f"[GROQ DEBUG] Message {i}: role={msg['role']}, content='{content_preview}...'")
```

## Verification

The fix was verified with test scripts:
- `check_groq_api.py` - Confirmed API key is valid and not rate-limited
- `diagnose_llm_issue.py` - Identified that empty messages cause issues
- `test_empty_message_fix.py` - Verified the fix resolves the problem

### Test Results

✅ Groq API is working correctly (not rate-limited)
✅ Empty messages in conversation history cause zero-token responses
✅ Filtering empty messages resolves the issue
✅ Multi-turn conversations work correctly with the fix

## Additional Notes

### Why This Happened

The original code assumed LLM would always return some content. However, there are edge cases where zero tokens can be returned:
- Stop sequences triggering immediately
- Network issues causing incomplete responses
- API rate limiting (though not the case here)
- Malformed conversation history (the actual cause)

### Prevention

The two-layer fix ensures:
1. **Prevention**: Don't add empty messages to history in the first place
2. **Defense**: Filter out any empty messages before sending to LLM

This defense-in-depth approach prevents the cascading failure pattern.

## Files Modified

1. `backend/app/infrastructure/llm/groq.py` - Added empty message filtering and enhanced logging
2. `backend/app/domain/services/voice_pipeline_service.py` - Only add non-empty assistant messages to history

## Testing

To test the fix:

```bash
cd backend
source venv/bin/activate
python test_empty_message_fix.py
```

Expected output: All tests pass with ✅ indicators.
