# Monitoring LLM Issues - Quick Reference

## Key Log Patterns to Watch

### 1. Zero Tokens Warning
```
[GROQ DEBUG] WARNING: Zero tokens received from Groq!
```
**Action**: Check if empty messages are in conversation history (should be filtered now)

### 2. Empty Message Detection
```
[GROQ DEBUG] Skipping empty assistant message in conversation history
```
**Status**: Normal - this is the fix working correctly

### 3. LLM Timeout
```
LLM timeout after X.XXs (limit: 10.0s), tokens received: 0
```
**Action**: Check network connectivity to Groq API

### 4. Fallback Usage
```
Response validation failed: <reason>, using fallback
```
**Status**: Normal for occasional use, investigate if frequent

## Diagnostic Commands

### Check Groq API Status
```bash
cd backend
source venv/bin/activate
python check_groq_api.py
```

Expected output:
- ✓ API Key found
- ✓ Response received
- ✓ Streaming completed
- Token counts > 0

### Test Empty Message Handling
```bash
python test_empty_message_fix.py
```

Expected output:
- ✅ FIX VERIFIED: Filtering empty messages resolves the issue!
- ✅ Conversation completed successfully!

### Check Rate Limits

Visit: https://console.groq.com/settings/limits

Monitor:
- Requests per minute (RPM)
- Tokens per minute (TPM)
- Daily quota usage

## Common Issues & Solutions

### Issue: Repeated Zero Token Responses

**Symptoms**:
- Multiple consecutive zero-token warnings
- Fallback responses being used repeatedly
- Empty assistant messages in logs

**Solution** (Already Implemented):
- Empty messages are now filtered before sending to Groq
- Empty responses are not added to conversation history

**Verify Fix**:
```bash
# Check logs for this pattern:
grep "Skipping empty" backend.log
```

### Issue: Rate Limit Exceeded

**Symptoms**:
- Error messages mentioning "rate_limit"
- 429 HTTP status codes
- Sudden increase in zero-token responses

**Solution**:
1. Check Groq dashboard for current usage
2. Implement request throttling if needed
3. Consider upgrading Groq plan

### Issue: High Latency

**Symptoms**:
- LLM latency > 2000ms
- Timeout warnings
- Slow conversation flow

**Solution**:
1. Check network connectivity
2. Verify Groq API status
3. Consider using faster model (llama-3.1-8b-instant)

## Configuration Tuning

### Model Selection (config/providers.yaml or AI Options)

```yaml
llm:
  model: "llama-3.3-70b-versatile"  # Default: balanced
  # Alternatives:
  # - "llama-3.1-8b-instant"        # Fastest (560 t/s)
  # - "llama-4-scout-17b-16e-instruct"  # Very fast (750 t/s)
```

### Temperature Settings

```python
# In AI Options or global config
llm_temperature: 0.6  # Default for voice (0.2-0.8 recommended)
```

- Lower (0.2-0.4): More factual, consistent
- Higher (0.6-0.8): More conversational, creative

### Max Tokens

```python
llm_max_tokens: 100  # Default for voice responses
```

- Voice AI: 50-150 tokens (concise responses)
- Chat: 200-500 tokens (detailed responses)

## Health Check Endpoints

### Backend Health
```bash
curl http://localhost:8000/health
```

### Groq API Health
```bash
curl https://api.groq.com/openai/v1/models \
  -H "Authorization: Bearer $GROQ_API_KEY"
```

## Alerting Thresholds

Set up alerts for:
- Zero-token rate > 5% of requests
- LLM timeout rate > 2% of requests
- Fallback usage > 10% of responses
- Average LLM latency > 1500ms

## Support Resources

- Groq Documentation: https://console.groq.com/docs
- Groq Status Page: https://status.groq.com/
- Rate Limits: https://console.groq.com/settings/limits
- Model Benchmarks: https://console.groq.com/docs/models
