# Provider Key Provisioning — 50-concurrent target

**Date:** 2026-05-05
**Phase:** 1 (Single-pod foundations)
**Purpose:** exact key counts, plans, and env-var mapping needed to run the Phase 1 verification load test (50 sustained concurrent calls) and to leave headroom on each provider's concurrency contract.

---

## Per-provider requirements

| Provider | Role | Keys | Plan / tier required | Why exactly that count |
|---|---|---|---|---|
| **Groq** | LLM (primary) | **2** | Production tier (paid) | ~500 LLM RPM at 50 calls × 10 turns/min. Standard production tier covers it ~12× over with one key; the 2nd is a hot spare so a single key getting a 429 storm or temporary outage doesn't drop the pod. |
| **ElevenLabs** | TTS (primary) | **3** | Scale plan (~250 concurrent streams) | The binding constraint is concurrent streams, not RPM. 50 streams against one Scale-plan key leaves 20 % margin — that's gone the moment a sentence holds the stream open mid-synthesis. 3 keys spreads to ~17 streams each, and any one key can fail without dropping calls. **Bump to 5 keys if traffic is bursty (campaign-style 9:00 AM spikes).** |
| **Cartesia** | TTS (fallback) | **2** | Pro plan (~100 concurrent per key) | Only engaged when ElevenLabs trips the circuit breaker. One key suffices for capacity; 2 lets the pool route around a single-key outage without re-tripping the breaker back to the already-broken primary. |
| **Deepgram** | STT (primary) | **1** + concurrency uplift | Pay-as-you-go with concurrency limit raised to ≥100 | One streaming WebSocket per call → 50 WS at peak. Default account cap is 100 concurrent, but new accounts often start at 25–50. **Action: email Deepgram support and ask for the per-account streaming-WS concurrency limit to be raised to 100.** A 2nd key is wasted spend — Deepgram doesn't volume-discount across keys, and per-tenant cost tracking gets messier. |
| **Google Cloud TTS** | TTS (secondary fallback) | **2 service accounts in 2 GCP projects** | Default quota (1000 RPM/project) is enough | Engaged only when both ElevenLabs and Cartesia trip. Even at full failover, 50 × 4 RPM = 200 RPM fits one project — but two projects buys HA at the GCP-project blast radius (one project gets quota-paused, the other still serves). |

---

## Env-var mapping the codebase reads

Multi-key pools are activated by setting the CSV variants. If only the legacy single-key var is set, the provider falls back to single-key behaviour exactly as before — no breakage.

```
# Groq LLM
GROQ_API_KEYS=gsk_<primary>,gsk_<spare>

# ElevenLabs TTS (primary)
ELEVENLABS_API_KEYS=sk_<key1>,sk_<key2>,sk_<key3>

# Cartesia TTS (fallback)
CARTESIA_API_KEYS=<key1>,<key2>

# Deepgram STT
DEEPGRAM_API_KEYS=<key1>
# OR equivalently the legacy single-key form:
DEEPGRAM_API_KEY=<key1>

# Google Cloud TTS (secondary fallback)
# Note: GCP TTS uses Application Default Credentials, not API keys.
# Set GOOGLE_APPLICATION_CREDENTIALS to a service-account JSON path,
# and switch projects via GOOGLE_CLOUD_PROJECT for the fallback path.
# Multi-project rotation will be wired in Phase 2.
GOOGLE_APPLICATION_CREDENTIALS=/run/secrets/gcp-tts-sa1.json
GOOGLE_CLOUD_PROJECT=talky-tts-primary
```

### Sizing knobs (already wired, defaults shown)

These cap the in-process semaphore at ≤ 85 % of the plan limit so the pod never overshoots its contracted ceiling regardless of caller burst.

```
GROQ_MAX_CONCURRENT=80
ELEVENLABS_MAX_CONCURRENT=200
DEEPGRAM_MAX_CONCURRENT=80
CARTESIA_MAX_CONCURRENT=80
```

---

## Bursty-traffic guidance

If your campaign launches concentrate originations (e.g., everyone clicks "Start" at 9:00 AM Monday), the first thing to saturate is **ElevenLabs concurrent streams**. Increase that pool first:

- Steady 50 concurrent → **3 ElevenLabs keys**
- Bursty 50 concurrent (peak ~75 within 30 s) → **5 ElevenLabs keys**

Other providers hold even under bursts because the bottleneck on Groq is RPM (averaged) and on Deepgram is concurrent connections (capped by the pod's `MAX_TELEPHONY_SESSIONS`).

---

## What changes when we scale to 1000 concurrent (Phase 4)

Same env-var shape, just bigger numbers and bigger plans. Architecture is unchanged.

| Provider | 50-concurrent | 1000-concurrent |
|---|---|---|
| Groq | 2 keys, Production tier | 4–6 keys, Enterprise dedicated capacity |
| ElevenLabs | 3 keys, Scale plan | 6–8 keys, Enterprise contract |
| Cartesia | 2 keys, Pro plan | 4 keys, Enterprise tier |
| Deepgram | 1 key + uplift to 100 | 1 key + uplift to 1500 (Enterprise) |
| Google TTS | 2 GCP projects | 5 GCP projects |

---

## Operator handoff

Once the keys are provisioned, paste them into `.env` (or your secret manager) using the env-var names above and restart the backend pod. Verify the pools loaded:

```bash
curl http://localhost:8000/api/v1/healthz/ready
# → 200 OK when ready
```

Then run the Phase 1 verification load test:

```bash
cd backend
./venv/bin/python scripts/loadtest_calls.py \
    --concurrent 50 --duration 600 --base-url http://localhost:8000
```

Pass criteria are listed in `phase1_complete.md` §How to verify.
