# Google TTS Connection Hardening

**Files:** `app/infrastructure/tts/google_tts_streaming.py`
**Tests:** `tests/unit/test_google_tts_streaming_hardening.py`
**Trigger:** 2026-04-22 live call at 17:23:32 — Turn 6 went silent for
~23 s. The server log recorded
`Google TTS retry 1/2 after 0.35s — 409 Stream aborted due to long
duration elapsed without input sent`, followed by
`High TTFT: 6541ms — likely Groq rate limiting or cold cache`.
Groq's slow first token caused a gap inside Google's 5-second
"continuous input required" window. Google aborted the stream. Every
retry tried to re-open streaming, which is the same path that just
failed — so Turn 6 emitted zero audio. The caller hung up.

## Goal

The Google TTS connection must stay alive for the entire call life.
It must not break until the call is hung up. No feature change. No
happy-path latency regression.

## Approach — two-layer protection

### Layer 1 — Bounded streaming attempt with read-timeout

The gRPC bidi stream (`streaming_synthesize`) is still used on the
fast path — this keeps the ~100-300 ms first-audio latency that
defines the whole product.

What changes:

- Each individual response-chunk read from the stream is wrapped in
  an explicit `asyncio.wait_for` timeout (default 8 s). If Google's
  server side stalls or gets aborted, the read returns promptly with
  a `TimeoutError` instead of hanging the caller.
- A first-chunk sentinel tracks whether any audio has been yielded
  to the caller. A failure **before** the first chunk is recoverable;
  a failure **after** the first chunk is not (retrying would replay
  audio and stutter the caller's speech). This is the opposite of the
  pre-fix behavior, which blindly retried and could replay partial
  audio on top of the next attempt.

### Layer 2 — REST fallback (unary synthesize_speech)

When the bidi stream fails **before emitting a single audio chunk**,
fall back to Google's unary `SynthesizeSpeech` call on the same
gRPC client. The unary path:

- Does not have Google's streaming 5-second idle rule. It's a normal
  request/response RPC.
- Returns the entire LINEAR16 audio buffer in one response. We slice
  it into the same ≤16 KB `AudioChunk`s that the streaming path
  emits, so the media gateway and the caller receive identical
  framing.
- Adds ~400-800 ms of first-audio latency compared with streaming.
  This happens only on the sad path (streaming has already failed);
  it is never in the happy-path budget.

The fallback runs for the **current sentence only**. The next
sentence starts a fresh streaming attempt. The provider never flips
into a permanent REST mode — streaming is restored automatically as
soon as Google's service recovers.

## Why this does not change any feature

| Concern | Outcome |
| --- | --- |
| Voice & prosody | Same voice (Chirp 3: HD), same speaking rate, same language code on both paths |
| Audio format to media gateway | Float32 PCM chunks, same sample rate, same chunk size |
| Barge-in | Unchanged — `stream_synthesize` is still an async generator; the caller's barge-in check inside `async for audio_chunk` still fires between chunks |
| Per-turn timing | Happy-path latency unchanged; fallback adds ≤800 ms on failing turns only |
| Circuit breaker | Still wrapping streaming attempts; failures still count toward the 5-failure trip |

## Why this does not regress latency

The read-timeout (`asyncio.wait_for` at 8 s) fires only on stalls
that would otherwise fail the turn outright. On a healthy stream
every chunk arrives in milliseconds — the timeout is never reached.

The REST fallback is only entered when streaming has already raised.
On a healthy call it is never entered.

## Design decisions

- **No mid-stream reconnect.** Once any audio has been yielded to the
  caller, a reconnect would replay audio. Audible. Worse than just
  failing the turn. Kept simple: raise, let the turn-level fallback
  fire ("sorry, could you repeat that?").
- **No client-side heartbeat.** The Google bidi stream's 5-second
  rule applies to the input direction. Our `request_generator` sends
  all sentences back-to-back from a fully-materialized `text` string
  — there is no idle gap on the send side. The observed 409 comes
  from gRPC flow-control pauses during slow upstream Google
  processing, which a client heartbeat cannot fix. The correct
  action is to abandon that stream promptly (read-timeout) and fall
  back.
- **REST fallback uses the same gRPC client, not the REST provider
  in `google_tts.py`.** `TextToSpeechAsyncClient.synthesize_speech`
  is a unary call on an already-warmed gRPC channel. Using it avoids
  cold DNS/TLS cost and keeps the fallback path inside a single
  provider class.
- **Fallback scoped to one sentence.** No session-level mode flag.
  Every new sentence starts with streaming. This keeps streaming
  recovery automatic when Google's service comes back.

## Observability

New log lines (all at WARNING):

- `google_tts_streaming: chunk read stall >8s — aborting stream for REST fallback`
- `google_tts_streaming: streaming failed pre-first-chunk — falling back to REST for sentence ({chars} chars)`
- `google_tts_streaming: streaming failed post-first-chunk — raising (no replay) after {n} chunks`

On success the fast path stays silent.

## Related docs

- [2026-04-22 Google TTS Connection Hardening Plan](../superpowers/plans/2026-04-22-google-tts-connection-hardening.md)
- [2026-04-22 Execution Log](./2026-04-22-google-tts-connection-hardening-execution.md)
