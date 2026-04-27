# T1.3 — Resilient STT + TTS wrappers

_Date: 2026-04-25_
_Plan reference: `~/.claude/plans/zazzy-wibbling-stardust.md` → Part 2, Tier 1, T1.3_
_Tests: 13 new (total 157 across the new-code suite)_
_Routes: unchanged (241) — new modules; not yet wired into live pipeline_

---

## What was broken

A Deepgram or Cartesia WebSocket drop mid-call ended the session.
There was no reconnect, no failover, and no second provider
configured anywhere in the stack. The only fallback was the
existing session watchdog, which tore down silent calls after 30s
— too late; the caller heard silence and hung up first.

Grep pattern `raise` inside `backend/app/infrastructure/stt/` and
`backend/app/infrastructure/tts/` confirmed every provider surfaces
connection errors up to the voice pipeline where they terminated
the call.

---

## What shipped

Two provider-wrapper classes that satisfy the existing `STTProvider`
and `TTSProvider` interfaces. They compose a primary and an optional
secondary provider with circuit-breaker-gated failover.

**Important:** the wrappers are built and tested, **but not yet
wired into the live voice pipeline**. Landing the mechanism here
de-risks the follow-up integration — rewiring the providers needs
a dry-run on staging before it can touch production.

### New modules

`backend/app/domain/services/resilient_stt.py` — `ResilientSTTProvider`

Key behaviours (the full rationale lives in the module docstring):

- **Single reconnect attempt, 500 ms budget.** Flapping between
  providers mid-call produces duplicate partials and confuses
  turn-detection. If the first reconnect fails we swap to secondary
  for the rest of the call.
- **Ring-buffer audio replay (~500 ms by default).** The buffer
  captures the tail of the utterance that was in-flight when the
  drop happened. On failover we replay that audio into the secondary
  so transcription continues instead of losing the sentence. Worst-
  case double-transcription is bounded at the buffer size.
- **No mid-stream partial merging.** When we swap, we drop any
  pending primary partials and restart. Different models segment
  words differently; merging produces word salad.
- **Circuit breaker on the primary only.** After `failure_threshold`
  consecutive failures the breaker opens and new streams go
  directly to secondary until the recovery window elapses. Reuses
  the existing `app.utils.resilience.CircuitBreaker`.
- **Fail-through on double failure.** If both providers fail, the
  wrapper yields no transcripts. The existing session watchdog
  tears down silent calls after 30s — no new hangup path.

`backend/app/domain/services/resilient_tts.py` — `ResilientTTSProvider`

Key behaviours:

- **Startup-only failover.** If the primary fails during handshake
  (auth error, connection refused, rate limit) we catch, flip to
  secondary, and replay the SAME text. Caller hears one continuous
  utterance with no voice change.
- **Mid-stream drops re-raise.** If the primary fails AFTER we've
  already yielded audio, we abort and re-raise. The caller has
  already heard part of the sentence on the primary voice;
  stitching the remainder from a different voice is a worse
  experience than truncating. The voice pipeline decides recovery
  (speak a "one moment" recovery phrase via secondary, or let the
  LLM re-prompt next turn).
- **Breaker gates the primary only.** If the circuit is already
  open on entry, we go straight to secondary — no probe mid-call.
- **Optional voice-id mapping.** `TTSFailoverPolicy.voice_id_map`
  lets tenants pair "Cartesia Tessa → ElevenLabs Bella" so the
  two voices sound similar when failover fires.

Both wrappers inherit from their respective interfaces
(`STTProvider` / `TTSProvider`), so call-site code needs no changes
beyond swapping which class gets constructed in the factory.

### Breaker integration

Uses the existing `app.utils.resilience.CircuitBreaker` (already in
tree, already in use by the Groq LLM provider). One breaker per
primary-provider instance:

```python
self._breaker = CircuitBreaker(
    name=f"stt-{primary.name}",
    failure_threshold=policy.failure_threshold,
    recovery_timeout=policy.recovery_timeout_seconds,
)
```

`CircuitState.OPEN` on entry → skip primary, go to secondary.
Primary exception via `async with breaker:` → the breaker sees the
failure and decides whether to open next time.

### Config

Both wrappers take an optional policy dataclass. Defaults are
voice-appropriate (sub-second budgets):

```python
@dataclass
class ReconnectPolicy:
    reconnect_timeout_seconds: float = 0.5
    max_reconnect_attempts: int = 1
    audio_buffer_ms: int = 500
    failure_threshold: int = 3
    recovery_timeout_seconds: float = 30.0

@dataclass
class TTSFailoverPolicy:
    failure_threshold: int = 3
    recovery_timeout_seconds: float = 30.0
    voice_id_map: Dict[str, str] | None = None
```

---

## Integration — NOT done in this sprint

The voice pipeline currently builds STT / TTS instances directly in
the orchestrator / factory. Rewiring is straightforward:

```python
# backend/app/infrastructure/stt/factory.py
primary = DeepgramFluxSTT()
secondary = DeepgramSTT()  # nova-2 model, different WS
resilient = ResilientSTTProvider(primary, secondary)
await resilient.initialize(config)
return resilient
```

Deferred because:

1. Provider pairing choices matter per-tenant (cost / quality
   tradeoffs) — should probably go through the per-tenant AI-config
   UI (T1.1 territory).
2. A dry-run on staging is mandatory before any change to the voice
   pipeline's resilience behaviour. Rewiring without that is a
   "will break production to find out" class of change.
3. The TTS wrapper's mid-stream-drop re-raise exposes a contract
   change for the voice pipeline — it may need to swallow that
   exception and speak a recovery utterance. That's a
   voice_pipeline_service edit that wants its own review.

The follow-up task is small and mechanical once those decisions
land.

---

## Design decisions — explicit

Documented inline in each module header. Summarised here so they're
easy to revisit when wiring the integration:

**STT**
- Reconnect before failover — not both in parallel.
- Replay the buffered tail on failover (500 ms cap).
- Drop pending partials on provider switch.
- One breaker per primary; secondary is the hot backup.

**TTS**
- Fail fast on handshake — safe to re-render text on secondary.
- Mid-stream drop = utterance truncated (don't stitch voices).
- Breaker open on entry → secondary only, no primary probe.
- Voice-id mapping optional, disabled by default.

If any of these prove wrong under load they're all knobs in the
policy dataclasses — changeable without touching the wiring.

---

## Verification

```bash
./venv/bin/python3 -m pytest tests/unit/test_resilient_providers.py -q
# 13 passed
```

Full new-code suite:

```bash
./venv/bin/python3 -m pytest \
  tests/unit/test_resilient_providers.py \
  tests/unit/test_credential_resolver.py \
  tests/unit/test_phone_timezone.py \
  tests/unit/test_global_concurrency.py \
  tests/unit/test_recording_policy.py \
  tests/unit/test_caller_id_verification.py \
  tests/unit/test_prod_fail_closed.py \
  tests/unit/test_prompt_composer.py \
  tests/unit/test_interruption_filter.py \
  tests/unit/test_agent_name_rotator.py \
  tests/unit/test_telephony_bridge_first_speaker.py \
  -q
# 157 passed
```

## Test coverage (13 tests)

STT wrapper:
- Happy path — primary-only, secondary untouched.
- Primary raise at start → failover to secondary.
- No secondary → empty output on failure (no crash).
- Ring-buffer cap: 100 chunks at 10 ms → ~50 retained (500 ms).
- Circuit open on entry → skip primary entirely.

TTS wrapper:
- Happy path — primary-only.
- Startup failure → secondary gets the same text and voice.
- Mid-stream drop → 2 chunks yielded, then RuntimeError raised,
  secondary NOT invoked (no voice stitching).
- Voice-id mapping applied on failover.
- Circuit open on entry → skip primary entirely.
- No secondary → startup failure re-raises.
- `get_available_voices()` prefers primary, falls back to secondary.
- `cleanup()` runs on both providers.

All tests use in-process fakes — no Deepgram / Cartesia credentials
needed for CI.

---

## File manifest

**New**
- `backend/app/domain/services/resilient_stt.py`
- `backend/app/domain/services/resilient_tts.py`
- `backend/tests/unit/test_resilient_providers.py`
- `backend/docs/scrutiny/2026-04-25-t1-3-resilient-providers.md` (this file)

**Modified**
- (none — wrappers are additive; no existing code touched)

---

## What's next

1. **Wire into the live voice pipeline.** Mechanical: swap the
   direct provider instantiation in the STT / TTS factory for the
   resilient wrapper when a secondary is configured. Needs staging
   dry-run.
2. **Secondary provider configuration**: per-tenant or global? The
   per-tenant knob falls naturally out of T1.1 (`tenant_ai_credentials`
   now stores keys per provider, so a tenant can declare both a
   primary Cartesia and a fallback ElevenLabs).
3. **T1.4** — Twilio + Telnyx adapters.
4. **T2.x** — DNC list integration, horizontal dialer scaling,
   Sentry alerting, legacy hardcode removal.
