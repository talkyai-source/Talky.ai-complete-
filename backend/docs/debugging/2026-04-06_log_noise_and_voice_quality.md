# Debugging Log — 2026-04-06
## Log Noise Reduction & Voice Quality Investigation

---

## Bug 17 — `[LLM DEBUG]` Lines Logged at INFO Level (5 Lines Per LLM Call)

### Symptom

Every LLM call emitted 5 INFO-level lines before streaming started:

```
[LLM DEBUG] Preparing LLM call for 3781f27c-...
[LLM DEBUG] System prompt length: 1243 chars, custom=True
[LLM DEBUG] Messages with prefill count: 7
[LLM DEBUG] Effective config - model: openai/gpt-oss-120b, temp: 0.6, max_tokens: 100
[LLM DEBUG] Last message role: user, content: 'Hi. I am calling...
```

And 3 more INFO lines after the LLM response:

```
LLM raw response (87 chars): '...'
After clean_response (79 chars): '...'
After truncate_response (79 chars): '...'
```

8 INFO lines per turn × 5 turns = 40 unnecessary INFO log lines per call.

### Root Cause

`_prepare_and_execute_llm_call()` in `voice_pipeline_service.py` added verbose
`logger.info()` calls as development debug instrumentation. These were never
demoted to DEBUG before production deployment.

### Fix

- Collapsed 5-line `[LLM DEBUG]` block into a single `logger.debug()` one-liner
- Demoted "LLM raw response", "After clean_response", "After truncate_response"
  from `logger.info` → `logger.debug`

Error paths (`[LLM DEBUG] Zero tokens received`) retain WARNING level — these
are exceptional and must remain visible.

### Files Changed

| File | Change |
|---|---|
| `backend/app/domain/services/voice_pipeline_service.py` | 8 `logger.info` → `logger.debug` in `_prepare_and_execute_llm_call()` |

---

## Bug 18 — `transcript_received` Logged at INFO Level (10–15 Times Per Utterance)

### Symptom

Deepgram Flux streams incremental transcript updates as the caller speaks.
Each partial update triggered an INFO log:

```
transcript_received  call_id=3781f27c  text="Hi"  is_final=False
transcript_received  call_id=3781f27c  text="Hi."  is_final=False
transcript_received  call_id=3781f27c  text="Hi. This"  is_final=False
transcript_received  call_id=3781f27c  text="Hi. This is"  is_final=False
...  (10–15 times per utterance)
transcript_received  call_id=3781f27c  text="Hi. This is John."  is_final=True
```

For a 5-turn conversation: ~60 INFO log lines from transcript partials alone.

### Root Cause

`handle_transcript()` in `voice_pipeline_service.py` fired `logger.info("transcript_received", ...)`
for every Deepgram partial update — including non-final incremental tokens. The
info level was appropriate when first added (as an audit trail), but incremental
partials have no diagnostic value at INFO; only the final transcript matters.

### Fix

Demoted `transcript_received` log from `logger.info` → `logger.debug`.

The final transcript is still clearly visible in the `EndOfTurn` event log which
remains at INFO level.

### Files Changed

| File | Change |
|---|---|
| `backend/app/domain/services/voice_pipeline_service.py` | `logger.info("transcript_received", ...)` → `logger.debug` |

---

## Bug 19 — `Audio validation passed` Hot-Path Debug Log (10 Lines/Second)

### Symptom

```
Audio validation passed: 1600 bytes, 100.0ms @ 8000Hz
Audio validation passed: 1600 bytes, 100.0ms @ 8000Hz
...  (10 times per second during every call)
```

At 10Hz × 60s call = 600 debug lines of zero-diagnostic value per call.

### Root Cause

`validate_pcm_format()` in `audio_utils.py` logged at `logger.debug` on every
successful validation. This function is called for every inbound audio chunk
from the STT pipeline — 10 times per second at 8kHz PCMU. The log only fires
on success (failures already bubble up as return values); success means "audio
is normal-sized" — there is nothing to log.

### Fix

Removed the `logger.debug` call from the success path of `validate_pcm_format()`.
Error return paths (chunk too small, chunk too large, not divisible by frame size)
are conveyed via return values, not logs — no change needed there.

### Files Changed

| File | Change |
|---|---|
| `backend/app/utils/audio_utils.py` | Removed success-path `logger.debug` from `validate_pcm_format()` |

---

## Bug 20 — Groq SDK `groq._base_client` Dumps Full HTTP Headers at DEBUG Level

### Symptom

Every LLM request and response produced a block of HTTP header debug output:

```
DEBUG [groq._base_client] Request options: {...headers: {Authorization: Bearer gsk_..., ...}}
DEBUG [groq._base_client] HTTP Response: POST https://api.groq.com/... "200 OK" ...
```

This was visible even at DEBUG level when `LOG_LEVEL=DEBUG`, leaking the
partial API key into logs and filling log buffers.

### Root Cause

The Groq Python SDK's internal `_base_client` module uses standard Python
`logging` at `DEBUG` level for all HTTP activity. With the app default
`LOG_LEVEL=DEBUG`, this was unfiltered.

Only `httpcore`, `httpx`, `hpack`, `urllib3`, and `websockets` were in the
`main.py` noise-suppression list — `groq` and `groq._base_client` were not.

### Fix

Added `groq._base_client` and `groq` to the noisy third-party logger suppression
list in `main.py`, setting them to `WARNING` level.

### Files Changed

| File | Change |
|---|---|
| `backend/app/main.py` | Added `"groq._base_client"` and `"groq"` to the noisy logger `setLevel(WARNING)` loop |

---

## Bug 21 — LLM Response Starting With `—` Em-Dash After Filler Removal

### Symptom

Some AI turns began with an em-dash artifact audible via TTS:

```
LLM raw:   "Sure thing! —I'm offering a complimentary assessment..."
After clean: "—I'm offering a complimentary assessment..."
TTS speaks:  "<dash sound> I'm offering a complimentary assessment..."
```

### Root Cause

`clean_response()` in `llm_guardrails.py` stripped filler prefixes like
`"Sure thing! "` but left any trailing punctuation the filler was attached to.
When an LLM uses `"Sure thing! —"` as a connector to the actual content, the
em-dash was left as the first character of the cleaned response.

The TTS engine (Deepgram/Google) vocalises an em-dash as a brief pause or
audible artifact, creating an unnatural start to the AI utterance.

### Fix

Added a regex step in `clean_response()` after filler removal that strips
leading em-dashes, en-dashes, and hyphens:

```python
# Strip leading em-dashes / en-dashes left behind after filler removal
cleaned = re.sub(r'^[—–\-]+\s*', '', cleaned)
```

### Files Changed

| File | Change |
|---|---|
| `backend/app/domain/services/llm_guardrails.py` | Added em-dash strip step in `clean_response()` after filler patterns |

---

## Investigation — Groq Region Routing Causes 3–4× TTFT Variance ("Jittery Voice")

### Symptom

Production call logs for `talky-out-66` showed highly inconsistent LLM response
times across turns in the same call:

| Turn | Groq Region | LLM First Token | Total Turn Latency |
|------|-------------|-----------------|-------------------|
| 1    | dmm (Dallas)| 199ms           | 580ms ✓           |
| 2    | SIN (Singapore) | 768ms       | 1082ms ✗          |
| 3    | SIN (Singapore) | 701ms       | 1194ms ✗          |
| 4    | SIN (Singapore) | 744ms       | 1140ms ✗          |
| 5    | dmm (Dallas)| 259ms           | 589ms ✓           |

The user perceived this as the AI being "sometimes quick, sometimes slow" — a
jittery, inconsistent conversational experience. 4 of 5 turns routed to SIN,
with 3–4× higher latency.

### Root Cause

The Groq Python SDK connects to `api.groq.com` which is proxied by Cloudflare.
Cloudflare routes each TCP connection to the nearest Groq PoP based on BGP
routing from the server's location. The server is in a region that sometimes
resolves to Dallas (200ms TTFT) and sometimes to Mumbai→Singapore (750ms TTFT).

Groq does **not** provide a user-selectable region API. The `AsyncGroq` client
only accepts `api_key` and `base_url` — there is no `region=` parameter.

### Status: External — Not Fixable in Code

This is a Groq infrastructure routing issue. Mitigation options:

1. **Monitor and alert** — log `x-groq-region` from response headers per turn
   (requires accessing HTTP response headers from the streaming SDK, which is
   non-trivial with the current `AsyncGroq` client abstraction).
2. **Deploy backend closer to Dallas** — if the server is hosted in a region
   that consistently routes to `bom→SIN`, migrating to US-central/US-east
   infrastructure would reduce this variance.
3. **Switch to Groq's direct API with HTTP-level keep-alive** — persistent
   connections may reduce Cloudflare re-routing variance (unverified).

For now, this is documented as a known production latency variance. The
application-side fixes (fixes 8–16 in prior debug logs) have reduced all
controllable latency sources. The remaining jitter is from Groq's routing.
