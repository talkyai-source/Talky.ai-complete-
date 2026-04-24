# Dental Workflow Test Guide
## From zero to 500ms latency in 4 steps

---

## Files in this package

| File | What it does |
|------|-------------|
| `backend/config/providers.yaml` | 9 latency fixes applied — drop this in, restart backend |
| `backend/app/domain/services/dental_workflow.py` | Agent config + all 6 test scenarios + campaign API payload builder |
| `backend/app/domain/services/streaming_pipeline.py` | LLM→TTS sentence streaming — eliminates the "wait for full LLM response" delay |
| `backend/tests/dental/test_dental_workflow.py` | Full test suite: unit + integration + latency benchmarks |
| `backend/tests/dental/conftest.py` | pytest markers for integration/latency tests |

---

## Step 1 — Apply the latency fixes

```bash
# Replace providers.yaml with the optimised version
cp dental/backend/config/providers.yaml backend/config/providers.yaml

# Copy the new service files
cp dental/backend/app/domain/services/dental_workflow.py \
   backend/app/domain/services/dental_workflow.py

cp dental/backend/app/domain/services/streaming_pipeline.py \
   backend/app/domain/services/streaming_pipeline.py

# Copy tests
mkdir -p backend/tests/dental
cp dental/backend/tests/dental/test_dental_workflow.py backend/tests/dental/
cp dental/backend/tests/dental/conftest.py             backend/tests/dental/
```

---

## Step 2 — Wire the streaming pipeline into voice_pipeline_service.py

In `backend/app/domain/services/voice_pipeline_service.py`, find the `handle_turn_end` method.

**Replace** the old sequential LLM → TTS block:
```python
# OLD: waits for full LLM response before TTS
response_text = await self.get_llm_response(session, full_transcript)
self.latency_tracker.mark_llm_end(call_id)
...
await self.synthesize_and_send_audio(session, response_text, websocket)
```

**With** the streaming version:
```python
# NEW: LLM tokens flow directly into TTS sentence-by-sentence
from app.domain.services.streaming_pipeline import stream_llm_to_tts

system_prompt = self.prompt_manager.get_system_prompt(
    getattr(session, "agent_config", None)
)

t0 = time.monotonic()
async for audio_chunk in stream_llm_to_tts(
    llm=self.llm_provider,
    tts=self.tts_provider,
    messages=session.conversation_history,
    system_prompt=system_prompt,
    call_id=call_id,
    tenant_id=getattr(session, "tenant_id", None),
    latency_tracker=self.latency_tracker,
    barge_in_event=self._barge_in_events.get(call_id),
    max_tokens=60,
    temperature=0.4,
):
    await self.media_gateway.send_audio(call_id, audio_chunk)

total_ms = (time.monotonic() - t0) * 1000
session.add_latency_measurement("total_turn", total_ms)
self.latency_tracker.mark_llm_end(call_id)
self.latency_tracker.log_metrics(call_id)
```

---

## Step 3 — Run the tests

### Unit tests (no API keys needed — runs in CI)
```bash
cd backend
pytest tests/dental/ -v --tb=short
```

Expected output:
```
tests/dental/test_dental_workflow.py::TestDentalAgentConfig::test_goal_is_appointment_confirmation PASSED
tests/dental/test_dental_workflow.py::TestDentalAgentConfig::test_context_contains_all_required_fields PASSED
tests/dental/test_dental_workflow.py::TestDentalAgentConfig::test_model_is_fast PASSED
tests/dental/test_dental_workflow.py::TestDentalAgentConfig::test_eot_timeout_is_800ms PASSED
tests/dental/test_dental_workflow.py::TestDentalAgentConfig::test_tts_provider_is_cartesia PASSED
tests/dental/test_dental_workflow.py::TestDentalScenarios::test_all_scenarios_defined PASSED
...
tests/dental/test_dental_workflow.py::TestStreamingPipeline::test_streaming_pipeline_latency_under_500ms PASSED
========================= 24 passed in 1.3s =========================
```

### Integration tests (requires running backend + JWT token)
```bash
export API_BASE_URL=http://localhost:8000
export TEST_TOKEN=$(curl -s -X POST $API_BASE_URL/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@talky.ai","password":"yourpassword"}' | jq -r .access_token)

pytest tests/dental/ -v -m integration --tb=short
```

### Latency benchmark (requires real API keys)
```bash
export RUN_LATENCY=true
export DEEPGRAM_API_KEY=your_key
export GROQ_API_KEY=your_key
export CARTESIA_API_KEY=your_key

pytest tests/dental/ -v -m latency --tb=short -s
```

Expected output when passing:
```
PASSED [100%]
Groq first token: 87ms
Cartesia first chunk: 94ms
Full pipeline (LLM+TTS first audio): 181ms — TARGET: 500ms
```

---

## Step 4 — Create a test campaign via the API

```python
# quick_test.py — run this to create the dental campaign and check the dashboard
import httpx
import os
from app.domain.services.dental_workflow import build_dental_campaign_payload

BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")
TOKEN    = os.getenv("TEST_TOKEN")

payload = build_dental_campaign_payload(
    practice_name="Bright Smile Dental",
    agent_name="Sarah",
    doctor_name="Dr. Patel",
    contacts=[
        {
            "patient_name": "Test Patient A",
            "phone": "+15550000001",  # Use your test number
            "appointment_date": "Friday, April 11th",
            "appointment_time": "10:00 AM",
        }
    ],
)

resp = httpx.post(
    f"{BASE_URL}/api/v1/campaigns/",
    json=payload,
    headers={"Authorization": f"Bearer {TOKEN}"},
    timeout=10,
)
print(f"Status: {resp.status_code}")
print(f"Campaign: {resp.json()}")
```

Run it:
```bash
python quick_test.py
```

Then open the admin panel at `https://api.your-domain.com:5050` (via SSH tunnel) and you should see the campaign with one contact.

---

## Reading the latency numbers

After a real call, check the backend logs:
```bash
docker compose logs backend | grep "LATENCY\|turn_complete\|LLM_TIMEOUT"
```

Look for lines like:
```
[OK]  Turn 0 latency: 312ms (STT-first: 95ms, LLM-first-token: 87ms, TTS-first-chunk: 94ms, LLM-total: 180ms, TTS-total: 0ms)
[OK]  Turn 1 latency: 289ms (STT-first: 82ms, LLM-first-token: 91ms, TTS-first-chunk: 88ms ...)
[SLOW] Turn 2 latency: 620ms — this turn exceeded the 500ms target
```

If you see `SLOW` turns:
- LLM-first-token > 200ms → Groq network issue (retry or switch to faster model)
- TTS-first-chunk > 150ms → Cartesia latency spike (check speaking_rate and model)
- STT-first > 200ms → Deepgram RTT or EOT threshold too high
- Total consistently 500-700ms → lower `eot_timeout_ms` to 600ms

---

## The 9 latency fixes applied (summary)

| Fix | Config key | Old value | New value | Saving |
|-----|-----------|-----------|-----------|--------|
| 1 | `eot_timeout_ms` | 5000ms | 800ms | ~250ms |
| 2 | `eot_threshold` | 0.7 | 0.6 | ~40ms |
| 3 | `eager_eot_threshold` | disabled | 0.45 | ~150ms |
| 4 | `tts.active` | google | cartesia | ~200ms |
| 5 | `speaking_rate` | 1.0 | 1.1 | ~8s/call |
| 6 | `llm.model` | 70b | 8b-instant | ~120ms |
| 7 | `temperature` | 0.7 | 0.4 | ~20ms |
| 8 | `max_tokens` | 150 | 60 | ~30ms |
| 9 | Streaming LLM→TTS | sequential | sentence-by-sentence | ~120ms |

Combined theoretical max saving: **~930ms** (from ~1100ms worst case to ~170ms best case)
Real-world target: consistently under **500ms** on p95 turns.
